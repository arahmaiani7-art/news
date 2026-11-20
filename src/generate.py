"""生成当日新闻海报 PNG。

用法：
    python3 src/generate.py                 # 生成今天
    python3 src/generate.py --date 2026-11-20
    python3 src/generate.py --dry-run       # 只取数，不截图

产物：
    output/latest.png      固定名，供外部插件读取
    output/YYYY-MM-DD.png  按日期归档
    output/latest.json     当天 15 条与元信息，便于排查

数据源：60 秒读懂世界 API（主站 + 两个镜像），15 条原文直接上图。

失败策略：任一步失败都以非 0 退出，且 **不覆盖已有的 latest.png**，
保证外部插件在上游数据源抖动时仍拿到上一张可用图。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import shutil
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from content import NEWS_COUNT, validate  # noqa: E402
from lunar import resolve_date  # noqa: E402
from news import fetch_daily  # noqa: E402
from render import RenderError, build_html, render_png  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "poster.html"
OUTPUT_DIR = ROOT / "output"

# 页头文案固定，界面识别度靠它；页脚用接口自带微语
HEADER_MOTTO = "把握今天的小事，时间会替努力积攒答案。"
FOOTER_FALLBACK = HEADER_MOTTO


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="生成每日新闻海报")
    p.add_argument("--date", help="目标日期 YYYY-MM-DD，默认今天（北京时间）")
    p.add_argument("--dry-run", action="store_true", help="只取数不渲染")
    p.add_argument("--keep-html", action="store_true", help="保留中间 HTML 便于调试")
    return p.parse_args(argv)


def beijing_today() -> dt.date:
    """GitHub Actions 跑在 UTC，统一按北京时间取当天。"""
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.date:
        try:
            target = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"[ERROR] 日期格式不对：{args.date}，应为 YYYY-MM-DD", file=sys.stderr)
            return 2
    else:
        target = beijing_today()

    print(f"[1/4] 目标日期 {target.isoformat()}")

    try:
        date_fields = resolve_date(target)
        print(
            f"      农历 {date_fields['lunar_date']}  "
            f"{date_fields['weekday_cn']} {date_fields['weekday_en']}"
        )
    except Exception as exc:
        print(f"[FAIL] 农历查询失败：{exc}", file=sys.stderr)
        return 3

    try:
        daily = fetch_daily(target)
        print(f"[2/4] 新闻来源 {daily['source']}")
    except Exception as exc:
        print(f"[FAIL] 新闻抓取失败：{exc}", file=sys.stderr)
        hint = (
            f"      接口只提供最新一期（当前 {beijing_today().isoformat()}），"
            f"不支持回放 {target.isoformat()} 的历史新闻。"
        )
        print(hint, file=sys.stderr)
        return 4
    texts = daily["news"]
    items = [{"index": i, "text": t} for i, t in enumerate(texts, 1)]

    try:
        validate(items, count=NEWS_COUNT)
    except Exception as exc:
        print(f"[FAIL] 内容校验失败：{exc}", file=sys.stderr)
        return 5

    print(f"[3/4] 取到 {len(items)} 条（接口原文，未截断）")
    for it in items:
        print(f"      {it['index']:2d}. {it['text']}")

    if args.dry_run:
        print("[dry-run] 不渲染，退出")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / "poster.html"
    tmp_png = OUTPUT_DIR / "latest.tmp.png"
    dated_png = OUTPUT_DIR / f"{target.isoformat()}.png"
    latest_png = OUTPUT_DIR / "latest.png"

    footer = (daily.get("tip") or "").strip() or FOOTER_FALLBACK

    try:
        html_doc = build_html(
            TEMPLATE,
            weekday_en=date_fields["weekday_en"],
            weekday_cn=date_fields["weekday_cn"],
            header_motto=HEADER_MOTTO,
            lunar_date=date_fields["lunar_date"],
            solar_year=date_fields["solar_year"],
            solar_date=date_fields["solar_date"],
            news=texts,
            footer_motto=footer,
        )
        html_path.write_text(html_doc, encoding="utf-8")
    except (RenderError, OSError) as exc:
        print(f"[FAIL] HTML 生成失败：{exc}", file=sys.stderr)
        return 6

    try:
        metrics = render_png(html_path, tmp_png)
        print(f"[4/4] 渲染完成 {metrics['width']}x{metrics['height']}")
    except Exception as exc:
        print(f"[FAIL] 截图失败：{exc}", file=sys.stderr)
        traceback.print_exc()
        tmp_png.unlink(missing_ok=True)
        return 7

    # 全部成功后才落盘，失败时保留上一张 latest.png
    shutil.copy2(tmp_png, dated_png)
    shutil.move(str(tmp_png), str(latest_png))

    if not args.keep_html:
        html_path.unlink(missing_ok=True)

    meta = {
        "date": target.isoformat(),
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "width": metrics["width"],
        "height": metrics["height"],
        **date_fields,
        "source": daily.get("source"),
        "source_link": daily.get("link"),
        "tip": footer,
        "news": [{"index": it["index"], "text": it["text"]} for it in items],
    }
    (OUTPUT_DIR / "latest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[OK] {latest_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())