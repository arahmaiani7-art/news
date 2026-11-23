#!/usr/bin/env python3
"""判断当天海报是否已经生成过，用于第二条 cron 的幂等跳过。

为什么需要：schedule 配了 07:00 和 08:00 两条（防止单条被 GitHub 丢弃）。
正常情况下 07:00 那条已经出图，08:00 那条再跑一次就是纯冗余——
多一次完整构建、多一次 Pages 部署、多一个 commit
（generated_at_utc 每次都变，靠 git diff 判断不出「内容其实没变」）。

所以：若 latest.json 的 date 已是今天，且 generated_at 晚于当天的
cutoff（默认北京 07:00，即主任务开始时间），就说明主任务已完成，
第二条可以整个 job 跳过。

刻意保守：只在「明确已生成」时跳过。任何解析失败、字段缺失、
时间早于 cutoff，都返回「需要生成」，宁可多跑一次也不冒空发的风险。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys

BJ = dt.timezone(dt.timedelta(hours=8))


def should_skip(
    meta_path: pathlib.Path,
    *,
    now: dt.datetime,
    cutoff: str = "07:00",
) -> tuple[bool, str]:
    """返回 (是否跳过, 原因)。任何不确定都判为「不跳过」。"""
    if not meta_path.exists():
        return False, f"{meta_path.name} 不存在，需要生成"

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"读取 {meta_path.name} 失败（{exc}），需要生成"

    today = now.astimezone(BJ).date().isoformat()
    date = meta.get("date")
    if date != today:
        return False, f"latest.json 的 date 是 {date!r}，不是今天 {today}，需要生成"

    raw = meta.get("generated_at_utc")
    if not raw:
        return False, "latest.json 缺少 generated_at_utc，无法判断新鲜度，需要生成"

    try:
        generated = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False, f"generated_at_utc 无法解析（{raw!r}），需要生成"
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=dt.timezone.utc)

    hour, minute = (int(x) for x in cutoff.split(":"))
    # cutoff 用北京时间表达，但以「今天」为准，避免跨 UTC 日界
    threshold = dt.datetime.combine(
        now.astimezone(BJ).date(), dt.time(hour, minute), tzinfo=BJ
    )

    if generated < threshold:
        return (
            False,
            f"今天已生成于 {generated.astimezone(BJ):%H:%M}，早于主任务 {cutoff}，"
            f"说明是手动补跑产物，需要重新生成",
        )

    return (
        True,
        f"今天已生成于 {generated.astimezone(BJ):%H:%M}（不早于 {cutoff}），跳过重复构建",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="判断今日海报是否已生成，用于跳过冗余构建")
    p.add_argument("--meta", default="output/latest.json", help="latest.json 路径")
    p.add_argument("--cutoff", default="07:00", help="主任务开始时间（北京时间 HH:MM）")
    p.add_argument("--now", help="测试用：指定当前时间（ISO 格式），默认取系统时间")
    args = p.parse_args(argv)

    now = (
        dt.datetime.fromisoformat(args.now)
        if args.now
        else dt.datetime.now(dt.timezone.utc)
    )
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)

    skip, reason = should_skip(
        pathlib.Path(args.meta), now=now, cutoff=args.cutoff
    )
    print(reason)

    # 在 GitHub Actions 里写入 step output；本地运行只打印，方便手动核对
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"SKIP={1 if skip else 0}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())