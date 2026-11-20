"""模板渲染为 PNG。

用 Playwright + Chromium 打开本地 HTML，按内容实际高度截图。
不用固定高度：条目字数不同会导致总高变化，固定高度会裁切或留白。

字体：容器内需预装 Noto Sans CJK SC。缺字体时 Chromium 会回退到其他字体，
中文可能渲染成方框，所以启动后先做一次字体可用性校验。
"""
from __future__ import annotations

import html
import pathlib
from typing import Iterable

from playwright.sync_api import sync_playwright

NEWS_COUNT = 15
# 每条上限：模板正文宽约 317px、14px 字号，一行约 22 字。
# 条目没有固定高度、海报按内容自动增高，100 字约 5 行仍不会被裁切。
MAX_CHARS = 100


class RenderError(RuntimeError):
    """渲染失败，或内容超出模板容量。"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def build_html(
    template_path: pathlib.Path,
    *,
    weekday_en: str,
    weekday_cn: str,
    header_motto: str,
    lunar_date: str,
    solar_year: str,
    solar_date: str,
    news: Iterable[str],
    footer_motto: str,
) -> str:
    news = list(news)
    if len(news) != NEWS_COUNT:
        raise RenderError(f"需要 {NEWS_COUNT} 条新闻，实际 {len(news)} 条")

    too_long = [(i, t) for i, t in enumerate(news, 1) if len(t) > MAX_CHARS]
    if too_long:
        detail = "; ".join(f"第{i}条 {len(t)}字" for i, t in too_long)
        raise RenderError(f"以下条超过 {MAX_CHARS} 字上限：{detail}")

    items_html = "\n".join(
        f'    <div class="news-item" data-name="news-item-{i:02d}">'
        f'<span class="news-num" data-name="news-num-{i:02d}">{i}、</span>'
        f'<p class="news-text" data-name="news-text-{i:02d}">{_esc(text)}</p>'
        f"</div>"
        for i, text in enumerate(news, 1)
    )

    template = template_path.read_text(encoding="utf-8")
    replacements = {
        "{{WEEKDAY_EN}}": _esc(weekday_en),
        "{{WEEKDAY_CN}}": _esc(weekday_cn),
        "{{HEADER_MOTTO}}": _esc(header_motto),
        "{{LUNAR_DATE}}": _esc(lunar_date),
        "{{SOLAR_YEAR}}": _esc(solar_year),
        "{{SOLAR_DATE}}": _esc(solar_date),
        "{{NEWS_LIST}}": items_html,
        "{{FOOTER_MOTTO}}": _esc(footer_motto),
    }
    out = template
    for key, value in replacements.items():
        out = out.replace(key, value)

    leftover = out.count("{{")
    if leftover:
        raise RenderError(f"模板仍有 {leftover} 个未替换占位符")
    return out


def render_png(html_path: pathlib.Path, png_path: pathlib.Path, width: int = 375) -> dict:
    """按内容高度截图，返回 {width, height}。"""
    png_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--font-render-hinting=none"])
        try:
            page = browser.new_page(
                viewport={"width": width, "height": 1200},
                device_scale_factor=2,  # Retina 清晰度
            )
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(300)  # 等字体与布局稳定

            # 字体校验：CJK 缺字时宽度会异常，用已知中文串做兜底检查
            font_ok = page.evaluate(
                """() => {
                    const el = document.querySelector('.news-text');
                    if (!el) return false;
                    const cs = getComputedStyle(el);
                    return /Noto Sans|PingFang|Source Han|Microsoft YaHei/i.test(cs.fontFamily);
                }"""
            )
            if not font_ok:
                raise RenderError("模板字体声明异常，无法确认中文字体")

            metrics = page.evaluate(
                """() => {
                    const el = document.querySelector('.poster');
                    const r = el.getBoundingClientRect();
                    return { width: Math.ceil(r.width), height: Math.ceil(r.height) };
                }"""
            )

            page.set_viewport_size(
                {"width": metrics["width"], "height": metrics["height"]}
            )
            page.wait_for_timeout(150)
            page.screenshot(path=str(png_path), full_page=True)
        finally:
            browser.close()

    return metrics
