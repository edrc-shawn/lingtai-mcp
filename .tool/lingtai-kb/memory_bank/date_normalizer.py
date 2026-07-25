# -*- coding: utf-8 -*-
"""
日期归一化器 (Date Normalizer)
==============================
解析记忆内容中的相对日期/时间表达，基于记忆的上下文时间戳
将其归一化为绝对日期字符串，辅助时间类问题检索匹配。

支持的相对日期模式：
- today, yesterday, the day before yesterday
- last <weekday>, this <weekday>, next <weekday>
- <N> days ago, <N> weeks ago
- last week, this week, next week
- last month, this month, next month
- the <weekday> before <date>
- <N> weekends ago
"""
import re
from datetime import datetime, timedelta

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 12, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_PATTERNS = [
    # the <weekday> before <abs_date> — e.g. "the Sunday before 25 May 2023"
    (re.compile(r"the\s+(\w+)\s+before\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.I),
     lambda m: _weekday_before_date(m.group(1), int(m.group(2)), m.group(3), int(m.group(4)))),
    # the <weekday> before <month> <year> — e.g. "the Friday before July 2023"
    (re.compile(r"the\s+(\w+)\s+before\s+(\w+)\s+(\d{4})", re.I),
     lambda m: _weekday_before_month(m.group(1), m.group(2), int(m.group(3)))),
    # last <weekday> — e.g. "last Friday", "last Saturday"
    (re.compile(r"last\s+(\w+)", re.I),
     lambda m, ref: _last_weekday(m.group(1), ref)),
    # this <weekday> — e.g. "this Monday"
    (re.compile(r"this\s+(\w+)", re.I),
     lambda m, ref: _this_weekday(m.group(1), ref)),
    # next <weekday>
    (re.compile(r"next\s+(\w+)", re.I),
     lambda m, ref: _this_weekday(m.group(1), ref + timedelta(days=7))),
    # yesterday
    (re.compile(r"yesterday", re.I), lambda m, ref: ref - timedelta(days=1)),
    # the day before yesterday
    (re.compile(r"the\s+day\s+before\s+yesterday", re.I), lambda m, ref: ref - timedelta(days=2)),
    # <N> days ago
    (re.compile(r"(\d+)\s+days?\s+ago", re.I), lambda m, ref: ref - timedelta(days=int(m.group(1)))),
    # <N> weeks ago
    (re.compile(r"(\d+)\s+weeks?\s+ago", re.I), lambda m, ref: ref - timedelta(weeks=int(m.group(1)))),
    # <N> weekends ago
    (re.compile(r"(\d+)\s+weekends?\s+ago", re.I), lambda m, ref: ref - timedelta(weeks=int(m.group(1)))),
    # last week / last weekend
    (re.compile(r"last\s+week(end)?", re.I), lambda m, ref: ref - timedelta(days=7)),
    # this week
    (re.compile(r"this\s+week", re.I), lambda m, ref: ref),
    # the weekend before <abs_date>
    (re.compile(r"the\s+weekend\s+before\s+(\d{1,2})\s+(\w+)\s+(\d{4})", re.I),
     lambda m: _weekday_before_date("saturday", int(m.group(1)), m.group(2), int(m.group(3)))),
    # last month
    (re.compile(r"last\s+month", re.I), lambda m, ref: _first_of_month(ref) - timedelta(days=1)),
    # this month
    (re.compile(r"this\s+month", re.I), lambda m, ref: ref.replace(day=1)),
    # next month
    (re.compile(r"next\s+month", re.I), lambda m, ref: _first_of_next_month(ref)),
    # <month> <year> — e.g. "June 2023"
    (re.compile(r"(\w+)\s+(\d{4})$", re.I), lambda m: _month_year(m.group(1), int(m.group(2)))),
]


def _last_weekday(name: str, ref: datetime) -> datetime:
    """找到 ref 之前的最近一个指定 weekday"""
    target = _WEEKDAYS.get(name.lower())
    if target is None:
        return ref
    if ref.weekday() >= target:
        delta = ref.weekday() - target
    else:
        delta = ref.weekday() + (7 - target)
    return ref - timedelta(days=delta) if delta else ref - timedelta(days=7)


def _this_weekday(name: str, ref: datetime) -> datetime:
    """找到 ref 当天或之后的第一个指定 weekday"""
    target = _WEEKDAYS.get(name.lower())
    if target is None:
        return ref
    delta = (target - ref.weekday()) % 7
    if delta == 0:
        return ref  # today
    return ref + timedelta(days=delta)


def _first_of_month(dt: datetime) -> datetime:
    return dt.replace(day=1)


def _first_of_next_month(dt: datetime) -> datetime:
    m = dt.month + 1
    y = dt.year
    if m > 12:
        m = 1
        y += 1
    return dt.replace(year=y, month=m, day=1)


def _parse_abs_date(day: int, month_name: str, year: int) -> datetime:
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _weekday_before_date(weekday_name: str, day: int, month_name: str, year: int) -> datetime:
    abs_date = _parse_abs_date(day, month_name, year)
    if abs_date is None:
        return None
    target = _WEEKDAYS.get(weekday_name.lower())
    if target is None:
        return abs_date
    # 目标 weekday 在 abs_date 之前的最近一天
    if abs_date.weekday() > target:
        delta = abs_date.weekday() - target
    elif abs_date.weekday() == target:
        delta = 7  # 同一天就往前退7天
    else:
        delta = abs_date.weekday() + (7 - target)
    return abs_date - timedelta(days=delta)


def _weekday_before_month(weekday_name: str, month_name: str, year: int) -> datetime:
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        abs_date = datetime(year, month, 1)
    except ValueError:
        return None
    target = _WEEKDAYS.get(weekday_name.lower())
    if target is None:
        return abs_date
    if abs_date.weekday() > target:
        delta = abs_date.weekday() - target
    elif abs_date.weekday() == target:
        delta = 7
    else:
        delta = abs_date.weekday() + (7 - target)
    return abs_date - timedelta(days=delta)


def _month_year(month_name: str, year: int) -> datetime:
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return datetime(year, month, 1)
    except ValueError:
        return None


def normalize_text(text: str, ref_dt: datetime = None) -> str:
    """归一化文本中的相对日期表达为绝对日期，返回原始文本 + 归一化注释。

    Args:
        text: 原始文本
        ref_dt: 参考时间（默认为当前时间）

    Returns:
        str: 原始文本 + 归一化日期注释（若有）
    """
    if ref_dt is None:
        ref_dt = datetime.now()

    normalized_dates = []
    used = set()

    def _iso(dt):
        if dt is None:
            return None
        return dt.strftime("%Y-%m-%d")

    # 先匹配带绝对引用的模式（无需 ref 参数）
    for pat, handler in _PATTERNS:
        for m in pat.finditer(text):
            sig = (pat.pattern, m.start(), m.end())
            if sig in used:
                continue
            used.add(sig)
            # handler 可能需要 ref
            try:
                result = handler(m)
                if result is None:
                    continue
            except TypeError:
                # handler 需要 ref
                try:
                    result = handler(m, ref_dt)
                except Exception:
                    continue
            dt = _iso(result)
            if dt:
                normalized_dates.append(f"[{dt}]")

    if normalized_dates:
        return text + " " + " ".join(normalized_dates)
    return text


def normalize_gt_answer(gt: str, ref_dt: datetime = None) -> str:
    """对标准答案字符串做与 normalize_text 同规则的归一化，以便比对。

    例如 GT "the Sunday before 25 May 2023" → "the Sunday before 25 May 2023 [2023-05-21]"
    """
    return normalize_text(gt, ref_dt)


def match_normalized(recalled_text: str, gt_normalized: str) -> bool:
    """判断召回文本归一化后是否包含 GT 答案中的日期。

    检查归一化注释 [YYYY-MM-DD] 是否有交集。
    """
    dates_recalled = set(re.findall(r"\[(\d{4}-\d{2}-\d{2})\]", recalled_text))
    dates_gt = set(re.findall(r"\[(\d{4}-\d{2}-\d{2})\]", gt_normalized))
    return bool(dates_recalled & dates_gt)
