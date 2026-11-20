"""取数与生成流程的测试。

直接用 stdlib unittest，不引入 pytest —— 项目依赖越少越好。

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import datetime as dt
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import content  # noqa: E402
import generate  # noqa: E402
import news  # noqa: E402


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
