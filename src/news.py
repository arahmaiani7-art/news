"""获取「60 秒读懂世界」当日 15 条新闻，主站失败时依次尝试镜像。

数据源：https://60s-api.viki.moe/v2/60s （开源项目 vikiboss/60s）
镜像：60s.viki.moe、60s.coolsong.com

接口每日约北京时间 08:00 前更新，当天内容以 `data.date` 为准。
本模块只做取数与完整性校验，不改写、不截断、不重排文案——
"新闻归纳呆板"的根因是旧的摘要截断规则，保留接口原文才有完整语义。
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request

NEWS_COUNT = 15

URLS = (
    "https://60s-api.viki.moe/v2/60s",
    "https://60s.viki.moe/v2/60s",
    "https://60s.coolsong.com/v2/60s",
)

# 单条超过这个长度说明接口数据异常，宁可失败也不生成畸形长条目
MAX_RAW_CHARS = 300

BJ = dt.timezone(dt.timedelta(hours=8))


class NewsError(RuntimeError):
    """新闻接口不可用或数据无效。不返回空列表冒充当日数据。"""


def _check(payload: object, target: dt.date) -> dict:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise NewsError("接口返回失败")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise NewsError("接口 data 字段无效")
    if data.get("date") != target.isoformat():
        raise NewsError(f"新闻日期 {data.get('date')} 与目标日期 {target.isoformat()} 不符")

    news = data.get("news")
    if (
        not isinstance(news, list)
        or len(news) != NEWS_COUNT
        or any(not isinstance(item, str) or not item.strip() for item in news)
    ):
        size = len(news) if isinstance(news, list) else "非列表"
        raise NewsError(f"新闻必须是 {NEWS_COUNT} 条非空文字，实际 {size}")

    texts = [item.strip() for item in news]
    if len(set(texts)) != NEWS_COUNT:
        raise NewsError("新闻包含重复条目")
    too_long = [len(t) for t in texts if len(t) > MAX_RAW_CHARS]
    if too_long:
        raise NewsError(f"新闻条目长度异常，最长 {max(too_long)} 字")
    return {**data, "news": texts}


def fetch_daily(target: dt.date, timeout: int = 20) -> dict:
    """返回接口 data（含 news / tip / date / link 等），主站失败自动换镜像。"""
    errors: list[str] = []
    for url in URLS:
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            return {**_check(payload, target), "source": url}
        except Exception as exc:  # 网络、JSON、校验失败都换下一个镜像
            errors.append(f"{url}: {type(exc).__name__}: {exc}")

    raise NewsError("所有新闻接口均失败：" + "; ".join(errors))


def fetch_news(target: dt.date, timeout: int = 20) -> list[str]:
    """只要 15 条文案，顺序与接口一致。"""
    return fetch_daily(target, timeout=timeout)["news"]


def wait_for_today(
    target: dt.date,
    *,
    deadline: dt.datetime,
    interval: int = 180,
    timeout: int = 20,
    sleep=time.sleep,
    clock=lambda: dt.datetime.now(BJ),
) -> dict:
    """等到接口真的发布 target 当天的那一期，或到 deadline 放弃。

    为什么需要等待：GitHub Actions 的 schedule 存在明显排队延迟
    （实测 cron 名义 08:15，实际 09:12–09:19 才启动，延迟近一小时），
    而 60 秒接口通常 07:48 前后才更新。若只在固定时刻取一次，
    要么取到昨天的，要么因为排队错过发送时间。
    所以策略是「提前触发 + 任务内等到当天数据就绪」。

    sleep / clock 可注入，便于测试不真的睡觉。
    """
    attempt = 0
    last_error = ""
    while True:
        attempt += 1
        try:
            daily = fetch_daily(target, timeout=timeout)
            print(f"      第 {attempt} 次尝试取得 {daily['date']} 数据，等待结束")
            return daily
        except Exception as exc:
            last_error = str(exc)

        now = clock()
        if now >= deadline:
            raise NewsError(
                f"等到 {deadline.isoformat()} 截止仍未取得 {target.isoformat()} 当天数据"
                f"（{last_error}）"
            )

        remaining = (deadline - now).total_seconds()
        wait = min(interval, max(int(remaining), 1))
        print(
            f"      [{now:%H:%M:%S} 北京] 第 {attempt} 次未就绪：{last_error[:120]}；"
            f"{wait}s 后重试（截止 {deadline:%H:%M}）"
        )
        sleep(wait)
