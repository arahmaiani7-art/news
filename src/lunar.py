"""农历与日期计算。

农历一律查香港天文台年历，不做推算。
数据源：https://www.hko.gov.hk/tc/gts/time/calendar/text/files/T{YYYY}c.txt
格式为 UTF-8 BOM，每行形如：
    2026年11月20日 星期五 十月十二
    2026年11月01日 星期日 廿三        <- 公历月初常对上农历月末
    ...
    2026年11月09日 星期一 十月          <- 初一那行第 2 列是纯月名，不带"初一"
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.request

HKO_URL = "https://www.hko.gov.hk/tc/gts/time/calendar/text/files/T{year}c.txt"

WEEKDAY_EN = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"]
WEEKDAY_CN = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 农历月名的匹配：正二三四五六七八九十冬腊 + 月，允许"闰"前缀
_MONTH_RE = re.compile(r"^(闰?[正二三四五六七八九十冬腊]月)$")
_DATE_RE = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$")


class LunarLookupError(RuntimeError):
    """农历查询失败。不做兜底推算，直接抛出。"""


def _fetch_year_text(year: int, timeout: int = 30) -> str:
    url = HKO_URL.format(year=year)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # UTF-8 with BOM
    return raw.decode("utf-8-sig", errors="replace")


def lookup_lunar(target: dt.date, timeout: int = 30) -> dict:
    """返回 {lunar_date, lunar_month, weekday_cn, weekday_en}。

    农历月与农历日是分开取的：每行第 2 列在初一那天只有月名（如"十月"），
    其余日期是"月名+日名"（如"十月十二"）。所以直接按目标行取整串即可，
    并额外用最近一次出现的纯月名做校验。
    """
    text = _fetch_year_text(target.year, timeout=timeout)

    current_month = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue

        m = _MONTH_RE.match(parts[1])
        if m:
            current_month = m.group(1)

        d = _DATE_RE.match(parts[0])
        if not d:
            continue
        y, mo, da = int(d.group(1)), int(d.group(2)), int(d.group(3))
        if (y, mo, da) != (target.year, target.month, target.day):
            continue

        # parts[1] 是"农历日"列；若它本身就是月名（初一），用 current_month 补全
        lunar_day_col = parts[1]
        weekday_cn = parts[2]

        if _MONTH_RE.match(lunar_day_col):
            # 初一那行第 2 列是纯月名（如"十月"），需要自己补"初一"
            lunar_month = lunar_day_col
            lunar_date = f"{lunar_month}初一"
        else:
            # 其余日期第 2 列只有日名（如"十二"），月名要取最近出现的纯月名
            lunar_month = current_month
            lunar_date = f"{lunar_month}{lunar_day_col}"
            if not lunar_month:
                raise LunarLookupError(
                    f"{target.isoformat()} 取到农历日 {lunar_day_col}，但未能确定农历月"
                )

        if weekday_cn not in WEEKDAY_CN:
            raise LunarLookupError(
                f"{target.isoformat()} 星期列异常：{weekday_cn!r}"
            )

        return {
            "lunar_date": lunar_date,
            "lunar_month": lunar_month,
            "weekday_cn": weekday_cn,
            "weekday_en": WEEKDAY_EN[WEEKDAY_CN.index(weekday_cn)],
        }

    raise LunarLookupError(f"香港天文台年历中未找到 {target.isoformat()}")


def solar_parts(target: dt.date) -> dict:
    """公历显示用字段。星期以天文台年历为准，此处仅算公历年月日。"""
    return {
        "solar_year": f"{target.year}年",
        "solar_date": f"{target.month}月{target.day}日",
    }


def resolve_date(target: dt.date, timeout: int = 30) -> dict:
    """合并农历与公历字段，供模板渲染。"""
    lunar = lookup_lunar(target, timeout=timeout)
    return {**lunar, **solar_parts(target)}
