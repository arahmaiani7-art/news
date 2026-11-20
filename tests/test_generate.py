"""生成流程的失败回退与内容规则测试。

直接用 stdlib unittest，不引入 pytest —— 项目只有 4 个模块，依赖越少越好。

    python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import content  # noqa: E402
import generate  # noqa: E402


class TestComposeItem(unittest.TestCase):
    """文案只从原文取，不改写标点、不留半句。"""

    def test_short_sentence_used_verbatim(self):
        item = {"word": "标题", "desc": "近日，某集团向厦门大学捐赠1亿元，用于建设大楼。"}
        self.assertEqual(content.compose_item(item), "近日，某集团向厦门大学捐赠1亿元，用于建设大楼")

    def test_overlong_sentence_falls_back_to_title(self):
        desc = "这是一段很长的描述，" * 10
        item = {"word": "完整标题", "desc": desc}
        self.assertEqual(content.compose_item(item), "完整标题")

    def test_dangling_sentence_falls_back_to_title(self):
        item = {"word": "完整标题", "desc": "国家主席在会议上表示"}
        self.assertEqual(content.compose_item(item), "完整标题")

    def test_no_desc_uses_title(self):
        self.assertEqual(content.compose_item({"word": "只有标题", "desc": ""}), "只有标题")

    def test_result_never_exceeds_limit(self):
        for desc in ["把" * 200, "标题超长" * 30, ""]:
            text = content.compose_item({"word": "词" * 100, "desc": desc})
            self.assertLessEqual(len(text), content.MAX_CHARS, text)


class TestSelect(unittest.TestCase):
    def _candidates(self, n=50):
        """每个候选用唯一标题与唯一描述，模拟真实榜单的不重复性。"""
        base = [
            ("习近平会见外宾", "11月{n}日，国家主席习近平在人民大会堂会见外宾"),
            ("央行发布报告", "央行发布第{n}期季度报告，提到适度宽松的货币政策安排"),
            ("纪委通报", "经省委批准，省纪委监委第{n}次通报干部违纪问题立案审查"),
            ("疫苗接种提醒", "疾控中心第{n}次提醒市民及时接种流感疫苗，注意防护"),
            ("国产芯片突破", "某公司宣布第{n}代国产芯片量产，性能提升三成以上"),
        ]
        out = []
        for i in range(n):
            word, desc = base[i % len(base)]
            out.append(
                {
                    "word": f"{word}{i}",
                    "desc": desc.format(n=i + 1),
                    "rank": i + 1,
                }
            )
        return out


    def test_returns_exact_count(self):
        items = content.select(self._candidates(), count=15)
        self.assertEqual(len(items), 15)

    def test_no_duplicate_text(self):
        items = content.select(self._candidates(), count=15)
        texts = [x["text"] for x in items]
        self.assertEqual(len(texts), len(set(texts)))

    def test_respects_topic_mix(self):
        items = content.select(self._candidates(60), count=15)
        from collections import Counter

        got = Counter(x["topic"] for x in items)
        # 每类都不超过配额上限
        for topic, quota in content.TARGET_MIX.items():
            self.assertLessEqual(got.get(topic, 0), quota, f"{topic} 超额: {got}")

    def test_insufficient_candidates_raises(self):
        with self.assertRaises(ValueError):
            content.select(self._candidates(5), count=15)

    def test_short_text_excluded(self):
        cands = [{"word": "11月19日", "desc": "11月19日"}]
        cands += self._candidates(50)
        items = content.select(cands, count=15)
        for it in items:
            self.assertGreaterEqual(len(it["text"]), content.MIN_CHARS, it["text"])


class TestValidate(unittest.TestCase):
    def test_rejects_wrong_count(self):
        with self.assertRaises(ValueError):
            content.validate([{"text": "x" * 20}], count=15)

    def test_rejects_duplicates(self):
        items = [{"text": f"第{i}条内容长度足够通过校验"} for i in range(14)]
        items.append({"text": items[0]["text"]})
        with self.assertRaises(ValueError):
            content.validate(items, count=15)

    def test_accepts_valid(self):
        items = [{"text": f"第{i}条内容长度足够通过校验"} for i in range(15)]
        content.validate(items, count=15)


class TestLatestPngPreservedOnFailure(unittest.TestCase):
    """上游失败时不能破坏已发布的 latest.png。"""

    def test_news_failure_keeps_previous_png(self):
        latest = ROOT / "output" / "latest.png"
        if not latest.exists():
            self.skipTest("尚无 latest.png，先跑一次 generate.py")

        before = latest.read_bytes()

        with mock.patch.object(generate, "fetch_candidates", side_effect=RuntimeError("上游挂了")):
            code = generate.main(["--date", "2026-11-20"])

        self.assertNotEqual(code, 0, "抓取失败应返回非 0")
        self.assertEqual(before, latest.read_bytes(), "latest.png 不应被改动")

    def test_lunar_failure_keeps_previous_png(self):
        latest = ROOT / "output" / "latest.png"
        if not latest.exists():
            self.skipTest("尚无 latest.png，先跑一次 generate.py")

        before = latest.read_bytes()

        with mock.patch.object(generate, "resolve_date", side_effect=RuntimeError("年历挂了")):
            code = generate.main(["--date", "2026-11-20"])

        self.assertNotEqual(code, 0, "农历失败应返回非 0")
        self.assertEqual(before, latest.read_bytes(), "latest.png 不应被改动")


if __name__ == "__main__":
    unittest.main()
