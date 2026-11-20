"""渲染内容校验。

新闻文案直接来自 60 秒接口，这里只做"能不能安全画上去"的硬校验，
不改写、不截断——旧版按 40 字截断会把完整句子砍成半句，读起来呆板。
"""
from __future__ import annotations

from typing import Sequence

NEWS_COUNT = 15

# 单条上限。模板正文宽约 317px、14px 字号，一行约 22 字；
# 条目无固定高度、海报按内容自动增高，所以 100 字约 5 行仍不会被裁切。
MAX_CHARS = 100


class ContentError(ValueError):
    """文案不合格，不能生成完整海报。"""


def validate(items: Sequence[dict], count: int = NEWS_COUNT) -> None:
    if len(items) != count:
        raise ContentError(f"条数不对：{len(items)}/{count}")

    seen: dict[str, int] = {}
    for i, item in enumerate(items, 1):
        text = (item.get("text") or "").strip()
        if not text:
            raise ContentError(f"第 {i} 条文案为空")
        if len(text) > MAX_CHARS:
            raise ContentError(f"第 {i} 条 {len(text)} 字，超过 {MAX_CHARS} 字上限：{text}")
        if "\n" in text or "\r" in text:
            raise ContentError(f"第 {i} 条含换行，渲染会错位：{text!r}")
        if text in seen:
            raise ContentError(f"第 {i} 条与第 {seen[text]} 条文案重复：{text}")
        seen[text] = i
