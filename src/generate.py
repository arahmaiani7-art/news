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
from motto import pick_motto  # noqa: E402
from news import BJ, fetch_daily, wait_for_today  # noqa: E402
from render import RenderError, build_html, render_png  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "poster.html"
OUTPUT_DIR = ROOT / "output"

# GitHub Actions schedule 实测排队延迟 57–64 分钟（cron 名义 08:15 → 实际 09:12~09:19），
# 所以触发时间提前到北京时间 07:00，并在任务内等到接口发布当天数据。
# 截止 08:45 是因为插件 09:00 发送：留出渲染与 Pages 部署时间，
# 同时给「触发准点」和「接口 07:48 更新」这两种情况都留足余量。
DEFAULT_DEADLINE = "08:45"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="生成每日新闻海报")
    p.add_argument("--date", help="目标日期 YYYY-MM-DD，默认今天（北京时间）")
    p.add_argument("--dry-run", action="store_true", help="只取数不渲染")
    p.add_argument("--keep-html", action="store_true", help="保留中间 HTML 便于调试")
    p.add_argument(
        "--wait",
        metavar="HH:MM",
        default=DEFAULT_DEADLINE,
        help=f"接口未发布当天数据时轮询等待，至迟北京时间 HH:MM（默认 {DEFAULT_DEADLINE}）",
    )
    p.add_argument(
        "--no-wait",
        action="store_true",
        help="不做轮询等待，取到就渲染；接口未更新即失败（手动查看时用）",
    )
    return p.parse_args(argv)


def beijing_today() -> dt.date:
    """GitHub Actions 跑在 UTC，统一按北京时间取当天。"""
    return dt.datetime.now(BJ).date()


def _deadline_for(target: dt.date, hhmm: str) -> dt.datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(target, dt.time(hour, minute), tzinfo=BJ)


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
        if args.no_wait or args.date:
            # 手动补跑/调试：只取一次，不占用等待时间
            daily = fetch_daily(target)
            print(f"[2/4] 新闻来源 {daily['source']}")
        else:
            deadline = _deadline_for(target, args.wait)
            print(f"[2/4] 等待 {target.isoformat()} 当天数据就绪（至迟 {args.wait} 北京）")
            daily = wait_for_today(target, deadline=deadline)
            print(f"      新闻来源 {daily['source']}")
    except Exception as exc:
        print(f"[FAIL] 新闻抓取失败：{exc}", file=sys.stderr)
        hint = (
            f"      接口只提供最新一期（当前北京日期 {beijing_today().isoformat()}），"
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

    # 页头按日期轮换（同一天恒定，跨天必换）；页脚优先用接口自带微语
    header_motto = pick_motto(target)
    footer = (daily.get("tip") or "").strip() or pick_motto(target, offset=1)

    try:
        html_doc = build_html(
            TEMPLATE,
            weekday_en=date_fields["weekday_en"],
            weekday_cn=date_fields["weekday_cn"],
            header_motto=header_motto,
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
        size_kb = tmp_png.stat().st_size / 1024
        print(
            f"[4/4] 渲染完成 {metrics['pixel_width']}x{metrics['pixel_height']}"
            f"（CSS {metrics['width']}x{metrics['height']} @{metrics['scale']}x，"
            f"{size_kb:.0f} KB）"
        )
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
        # CSS 尺寸用于还原版式；pixel_* 是图片真实像素，别只写 CSS 尺寸造成假账
        "width": metrics["width"],
        "height": metrics["height"],
        "scale": metrics["scale"],
        "pixel_width": metrics["pixel_width"],
        "pixel_height": metrics["pixel_height"],
        **date_fields,
        "source": daily.get("source"),
        "source_link": daily.get("link"),
        "header_motto": header_motto,
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