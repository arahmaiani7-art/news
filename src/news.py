"""热点抓取。

数据源：百度热搜实时榜。
页面把数据内联在 HTML 注释 `<!--s-data:{...}-->` 里，直接正则取出解析，
不必启动无头浏览器。

注意：本模块只负责取回候选条目，不判断哪条"重要"。选题与压缩由调用方决定。
"""
from __future__ import annotations

import gzip
import json
import re
import urllib.request
import zlib

BAIDU_URL = "https://top.baidu.com/board?tab=realtime"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/159.0 Safari/537.36"
)

_DATA_RE = re.compile(r"<!--s-data:([\s\S]*?)-->")


class HotListError(RuntimeError):
    """热榜获取或解析失败。不返回空列表冒充当日数据。"""


def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        encoding = (resp.headers.get("Content-Encoding") or "").lower()

    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw, -zlib.MAX_WBITS)

    return raw.decode("utf-8", errors="replace")


def fetch_baidu_hot(limit: int = 50, timeout: int = 30) -> list[dict]:
    """返回 [{rank, word, desc, score}]，按榜单原始顺序。"""
    html = _fetch(BAIDU_URL, timeout=timeout)
    m = _DATA_RE.search(html)
    if not m:
        raise HotListError("百度热榜页面中未找到 s-data 数据块，页面结构可能已变更")

    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        raise HotListError(f"百度热榜数据块 JSON 解析失败：{exc}") from exc

    cards = (payload.get("data") or {}).get("cards") or []
    if not cards:
        raise HotListError("百度热榜数据块中没有 cards 字段")

    items: list[dict] = []
    for card in cards:
        for entry in card.get("content") or []:
            word = (entry.get("word") or "").strip()
            if not word:
                continue
            items.append(
                {
                    "rank": len(items) + 1,
                    "word": word,
                    "desc": (entry.get("desc") or "").strip(),
                    "score": entry.get("hotScore"),
                }
            )

    if not items:
        raise HotListError("百度热榜解析结果为空")

    return items[:limit]


def fetch_candidates(limit: int = 50, timeout: int = 30) -> list[dict]:
    """当前只接百度。保留此函数作为多源扩展点。

    若要加抖音等需要渲染的数据源，在这里合并，并在返回值里保留来源标记。
    """
    items = fetch_baidu_hot(limit=limit, timeout=timeout)
    for it in items:
        it["source"] = "baidu"
    return items
