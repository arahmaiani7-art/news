"""取数与生成流程的测试。

直接用 stdlib unittest，不引入 pytest —— 项目依赖越少越好。

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import collections
import datetime as dt
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import content  # noqa: E402
import generate  # noqa: E402
import motto  # noqa: E402
import news  # noqa: E402
import should_skip  # noqa: E402


def _payload(date: str = "2026-11-20", texts: list[str] | None = None) -> dict:
    if texts is None:
        texts = [f"第{i}条新闻内容，包含具体数字与主体，长度足以通过校验" for i in range(15)]
    return {
        "code": 200,
        "data": {"date": date, "news": texts, "tip": "微语", "link": "https://example.com"},
    }


class _FakeResponse:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload, ensure_ascii=False).encode())

    def read(self, *args):
        return self._buf.read(*args)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestNewsCheck(unittest.TestCase):
    """接口数据完整性校验：宁可失败，不生成残缺海报。"""

    target = dt.date(2026, 11, 20)

    def test_accepts_valid_payload(self):
        data = news._check(_payload(), self.target)
        self.assertEqual(len(data["news"]), 15)

    def test_rejects_wrong_date(self):
        with self.assertRaises(news.NewsError):
            news._check(_payload(date="2026-11-19"), self.target)

    def test_rejects_wrong_count(self):
        with self.assertRaises(news.NewsError):
            news._check(_payload(texts=["只有一条"]), self.target)

    def test_rejects_duplicates(self):
        texts = ["完全相同的一条新闻内容，长度足够"] * 15
        with self.assertRaises(news.NewsError):
            news._check(_payload(texts=texts), self.target)

    def test_rejects_blank_item(self):
        texts = [f"第{i}条新闻内容，长度足够通过校验的占位文本" for i in range(15)]
        texts[3] = "   "
        with self.assertRaises(news.NewsError):
            news._check(_payload(texts=texts), self.target)

    def test_rejects_error_code(self):
        with self.assertRaises(news.NewsError):
            news._check({"code": 500, "data": {}}, self.target)

    def test_rejects_absurdly_long_item(self):
        texts = [f"第{i}条新闻内容" for i in range(15)]
        texts[0] = "长" * (news.MAX_RAW_CHARS + 1)
        with self.assertRaises(news.NewsError):
            news._check(_payload(texts=texts), self.target)


class TestFetchFallback(unittest.TestCase):
    """主站失败要自动落到镜像；全失败要抛错而不是返回空列表。"""

    target = dt.date(2026, 11, 20)

    def test_falls_back_to_mirror(self):
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise OSError("主站挂了")
            return _FakeResponse(_payload())

        with mock.patch("urllib.request.urlopen", fake_urlopen):
            data = news.fetch_daily(self.target)

        self.assertEqual(data["source"], news.URLS[1])
        self.assertEqual(len(data["news"]), 15)

    def test_all_sources_failing_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("全挂")):
            with self.assertRaises(news.NewsError):
                news.fetch_daily(self.target)


class TestShouldSkip(unittest.TestCase):
    """第二条 cron 的幂等跳过。核心安全要求：任何不确定都不能跳过。"""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.meta = self.tmp / "latest.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, **fields):
        base = {"date": "2026-11-23", "generated_at_utc": "2026-11-23T03:12:37+00:00"}
        base.update(fields)
        self.meta.write_text(json.dumps(base), encoding="utf-8")

    def _now(self, hhmm="08:00"):
        hour, minute = (int(x) for x in hhmm.split(":"))
        return dt.datetime(2026, 11, 23, hour, minute, tzinfo=should_skip.BJ)

    def test_skips_when_already_generated_after_cutoff(self):
        self._write()
        skip, reason = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertTrue(skip, reason)
        self.assertIn("跳过", reason)

    def test_generates_when_meta_missing(self):
        skip, reason = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertFalse(skip)
        self.assertIn("不存在", reason)

    def test_generates_when_date_is_yesterday(self):
        """第二条 cron 跨到新的一天时必须重新生成，不能被昨天的产物骗过去。"""
        self._write(date="2026-11-22")
        skip, reason = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertFalse(skip)
        self.assertIn("不是今天", reason)

    def test_generates_when_generated_before_cutoff(self):
        """07:00 前生成的是手动补跑产物，说明主任务还没跑，08:00 必须照常生成。"""
        self._write(generated_at_utc="2026-11-22T20:30:00+00:00")  # 北京 04:30
        skip, reason = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertFalse(skip)
        self.assertIn("早于主任务", reason)

    def test_generates_when_field_missing(self):
        self._write(generated_at_utc=None)
        skip, _ = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertFalse(skip)

    def test_generates_when_json_is_corrupt(self):
        self.meta.write_text("{not json", encoding="utf-8")
        skip, reason = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertFalse(skip)
        self.assertIn("读取", reason)

    def test_generates_when_timestamp_unparsable(self):
        self._write(generated_at_utc="昨天下午")
        skip, _ = should_skip.should_skip(self.meta, now=self._now("08:00"))
        self.assertFalse(skip)

    def test_writes_github_output(self):
        """脚本要真的把 SKIP 写进 GITHUB_OUTPUT，否则 workflow 判断不到。"""
        self._write()
        out = self.tmp / "out.txt"
        env = {"GITHUB_OUTPUT": str(out), "PATH": os.environ["PATH"]}
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "src" / "should_skip.py"),
                "--meta",
                str(self.meta),
                "--now",
                "2026-11-23T00:00:00+00:00",
            ],
            check=True,
            env=env,
            capture_output=True,
        )
        self.assertIn("SKIP=1", out.read_text(encoding="utf-8"))
    """定时任务的核心：数据晚到要等，等到截止就失败，不能空转或早退。"""

    target = dt.date(2026, 11, 20)

    def _deadline(self, hhmm="08:45"):
        hour, minute = (int(x) for x in hhmm.split(":"))
        return dt.datetime.combine(self.target, dt.time(hour, minute), tzinfo=news.BJ)

    def _clock(self, start):
        """可推进的假时钟：每次 sleep 都把时间往前推，模拟真实流逝。"""
        state = {"now": start}

        def clock():
            return state["now"]

        def sleep(seconds):
            state["now"] += dt.timedelta(seconds=seconds)

        return clock, sleep, state

    def test_returns_immediately_when_ready(self):
        clock, sleep, _ = self._clock(self._deadline("07:00"))
        with mock.patch.object(news, "fetch_daily", return_value={"date": "2026-11-20", "news": []}):
            data = news.wait_for_today(
                self.target, deadline=self._deadline(), sleep=sleep, clock=clock
            )
        self.assertEqual(data["date"], "2026-11-20")
        self.assertEqual(clock(), self._deadline("07:00"), "数据已就绪就不该推进时间")

    def test_retries_until_data_arrives(self):
        """前两次还是昨天的数据，第三次才发布——必须等到第三次。"""
        results = [
            news.NewsError("新闻日期 2026-11-19 与目标日期 2026-11-20 不符"),
            news.NewsError("网络抖动"),
            {"date": "2026-11-20", "news": ["ok"]},
        ]

        def fake_fetch(target, timeout=20):
            item = results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        clock, sleep, _ = self._clock(self._deadline("07:00"))
        with mock.patch.object(news, "fetch_daily", fake_fetch):
            data = news.wait_for_today(
                self.target,
                deadline=self._deadline(),
                interval=60,
                sleep=sleep,
                clock=clock,
            )

        self.assertEqual(data["news"], ["ok"])
        self.assertEqual(results, [], "第三次应该成功并停止重试")
        self.assertEqual(clock(), self._deadline("07:02"), "两次重试各等 60 秒")

    def test_raises_when_deadline_passed(self):
        """截止时间已过且数据仍未就绪，必须抛错（上游失败不能静默）。"""
        past = self._deadline("08:45")
        clock, sleep, _ = self._clock(past + dt.timedelta(minutes=1))
        with mock.patch.object(news, "fetch_daily", side_effect=news.NewsError("还是昨天")):
            with self.assertRaises(news.NewsError) as ctx:
                news.wait_for_today(
                    self.target, deadline=past, sleep=sleep, clock=clock
                )
        self.assertIn("仍未取得", str(ctx.exception))
        self.assertEqual(clock(), past + dt.timedelta(minutes=1), "超时后不该再等")

    def test_sleep_never_exceeds_remaining_time(self):
        """接近截止时最后一次 sleep 不能超过剩余时间，避免睡过头。"""
        deadline = self._deadline("08:45")
        clock, sleep, _ = self._clock(deadline - dt.timedelta(seconds=10))
        slept = []

        def recording_sleep(seconds):
            slept.append(seconds)
            sleep(seconds)

        with mock.patch.object(news, "fetch_daily", side_effect=news.NewsError("还没更新")):
            with self.assertRaises(news.NewsError):
                news.wait_for_today(
                    self.target,
                    deadline=deadline,
                    interval=3600,
                    sleep=recording_sleep,
                    clock=clock,
                )

        self.assertEqual(slept, [10], f"应只睡剩余的 10 秒，实际 {slept}")


class TestValidate(unittest.TestCase):
    def _items(self, n=15):
        return [{"text": f"第{i}条内容长度足够通过校验"} for i in range(n)]

    def test_rejects_wrong_count(self):
        with self.assertRaises(ValueError):
            content.validate(self._items(1), count=15)

    def test_rejects_duplicates(self):
        items = self._items(15)
        items[4]["text"] = items[0]["text"]
        with self.assertRaises(ValueError):
            content.validate(items, count=15)

    def test_rejects_empty_and_newline(self):
        items = self._items(15)
        items[0]["text"] = "  "
        with self.assertRaises(ValueError):
            content.validate(items, count=15)

        items[0]["text"] = "第一行\n第二行"
        with self.assertRaises(ValueError):
            content.validate(items, count=15)

    def test_rejects_over_limit(self):
        items = self._items(15)
        items[2]["text"] = "长" * (content.MAX_CHARS + 1)
        with self.assertRaises(ValueError):
            content.validate(items, count=15)

    def test_accepts_valid(self):
        content.validate(self._items(15), count=15)

    def test_limit_covers_real_feed(self):
        """接口最长条目实测约 55 字，上限必须留出余量。"""
        self.assertGreaterEqual(content.MAX_CHARS, 60)


class TestMotto(unittest.TestCase):
    """页头格言的确定性轮换。这些性质比具体文案更重要，加条目时不能破坏。"""

    def test_pool_size_is_rotation_period(self):
        self.assertEqual(len(motto.MOTTOS), 120)

    def test_no_duplicates(self):
        """有重复就可能让相邻两天撞成同一句，破坏「跨天必换」。"""
        dups = [m for m, n in collections.Counter(motto.MOTTOS).items() if n > 1]
        self.assertEqual(dups, [], f"格言池有重复条目：{dups}")

    def test_every_motto_within_length_limit(self):
        """页头是固定宽度红条，超长会换行挤压 FRIDAY 与农历。"""
        over = [(i, m) for i, m in enumerate(motto.MOTTOS, 1) if len(m) > motto.MAX_LEN]
        self.assertEqual(over, [], f"以下格言超过 {motto.MAX_LEN} 字：{over}")

    def test_no_blank_entries(self):
        for i, m in enumerate(motto.MOTTOS, 1):
            self.assertTrue(m.strip(), f"第 {i} 条为空")

    def test_same_day_is_stable(self):
        day = dt.date(2026, 11, 24)
        self.assertEqual(motto.pick_motto(day), motto.pick_motto(day))

    def test_consecutive_days_differ(self):
        """连续 400 天任意相邻两天都不能相同，跨池边界也要成立。"""
        start = dt.date(2026, 1, 1)
        for n in range(400):
            a = motto.pick_motto(start + dt.timedelta(days=n))
            b = motto.pick_motto(start + dt.timedelta(days=n + 1))
            self.assertNotEqual(a, b, f"{start + dt.timedelta(days=n)} 与次日撞句")

    def test_covers_whole_pool(self):
        """周期内必须把每条都用到，否则等于池子变小了。"""
        start = dt.date(2026, 1, 1)
        seen = {motto.pick_motto(start + dt.timedelta(days=n)) for n in range(120)}
        self.assertEqual(len(seen), 120)

    def test_full_cycle_returns_to_start(self):
        start = dt.date(2026, 1, 1)
        self.assertEqual(
            motto.pick_motto(start),
            motto.pick_motto(start + dt.timedelta(days=len(motto.MOTTOS))),
        )

    def test_footer_offset_differs_from_header(self):
        """页脚兜底用 offset=1，避免同一张图上下出现同一句话。"""
        for n in range(120):
            day = dt.date(2026, 1, 1) + dt.timedelta(days=n)
            self.assertNotEqual(motto.pick_motto(day), motto.pick_motto(day, offset=1))


class TestLatestPngPreservedOnFailure(unittest.TestCase):
    """上游失败时不能破坏已发布的 latest.png。"""

    def _require_latest(self):
        latest = ROOT / "output" / "latest.png"
        if not latest.exists():
            self.skipTest("尚无 latest.png，先跑一次 generate.py")
        return latest

    def test_news_failure_keeps_previous_png(self):
        latest = self._require_latest()
        before = latest.read_bytes()

        with mock.patch.object(generate, "fetch_daily", side_effect=RuntimeError("上游挂了")):
            code = generate.main(["--date", "2026-11-20"])

        self.assertNotEqual(code, 0, "抓取失败应返回非 0")
        self.assertEqual(before, latest.read_bytes(), "latest.png 不应被改动")

    def test_lunar_failure_keeps_previous_png(self):
        latest = self._require_latest()
        before = latest.read_bytes()

        with mock.patch.object(generate, "resolve_date", side_effect=RuntimeError("年历挂了")):
            code = generate.main(["--date", "2026-11-20"])

        self.assertNotEqual(code, 0, "农历失败应返回非 0")
        self.assertEqual(before, latest.read_bytes(), "latest.png 不应被改动")


if __name__ == "__main__":
    unittest.main()
