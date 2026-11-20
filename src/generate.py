"""生成当日新闻海报 PNG。

用法：
    python3 src/generate.py                 # 生成今天
    python3 src/generate.py --date 2026-11-20
    python3 src/generate.py --dry-run       # 只取数选题，不截图

产物：
    output/latest.png      固定名，供外部插件读取
    output/YYYY-MM-DD.png  按日期归档
    output/latest.json     当天选中的 15 条与元信息，便于排查

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

from content import NEWS_COUNT, select, validate  # noqa: E402
from lunar import resolve_date  # noqa: E402
from news import fetch_candidates  # noqa: E402
from render import RenderError, build_html, render_png  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "poster.html"
OUTPUT_DIR = ROOT / "output"

# 页头与页脚文案。改动会影响每天所有海报，保持稳定。
HEADER_MOTTO = "把握今天的小事，时间会替努力积攒答案。"
FOOTER_MOTTO = HEADER_MOTTO


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="生成每日新闻海报")
    p.add_argument("--date", help="目标日期 YYYY-MM-DD，默认今天")
    p.add_argument("--dry-run", action="store_true", help="只选题不渲染")
    p.add_argument("--keep-html", action="store_true", help="保留中间 HTML 便于调试")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.date:
        try:
            target = dt.date.fromisoformat(args.date)
        except ValueError:
            print(f"[ERROR] 日期格式不对：{args.date}，应为 YYYY-MM-DD", file=sys.stderr)
            return 2
    else:
        # GitHub Actions 跑在 UTC，按北京时间取当天
        target = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=8)).date()

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
        candidates = fetch_candidates(limit=50)
        print(f"[2/4] 热点候选 {len(candidates)} 条")
    except Exception as exc:
        print(f"[FAIL] 热点抓取失败：{exc}", file=sys.stderr)
        return 4

    try:
        items = select(candidates, count=NEWS_COUNT)
        validate(items, count=NEWS_COUNT)
    except Exception as exc:
        print(f"[FAIL] 选题或校验失败：{exc}", file=sys.stderr)
        return 5

    print(f"[3/4] 选出 {len(items)} 条")
    for i, it in enumerate(items, 1):
        print(f"      {i:2d}. [{it['topic']}] {it['text']}")

    if args.dry_run:
        print("[dry-run] 不渲染，退出")
        return 0

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT_DIR / "poster.html"
    tmp_png = OUTPUT_DIR / "latest.tmp.png"
    dated_png = OUTPUT_DIR / f"{target.isoformat()}.png"
    latest_png = OUTPUT_DIR / "latest.png"

    try:
        html_doc = build_html(
            TEMPLATE,
            weekday_en=date_fields["weekday_en"],
            weekday_cn=date_fields["weekday_cn"],
            header_motto=HEADER_MOTTO,
            lunar_date=date_fields["lunar_date"],
            solar_year=date_fields["solar_year"],
            solar_date=date_fields["solar_date"],
            news=[it["text"] for it in items],
            footer_motto=FOOTER_MOTTO,
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
        "news": [{"index": i, "topic": it["topic"], "text": it["text"]}
                 for i, it in enumerate(items, 1)],
    }
    (OUTPUT_DIR / "latest.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[OK] {latest_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
