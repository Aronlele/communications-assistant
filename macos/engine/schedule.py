from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate(weekly, zone):
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, TypeError):
        raise ValueError("无法识别时区，请使用 Asia/Shanghai 等 IANA 时区名称") from None
    if not isinstance(weekly, list) or len(weekly) != 7:
        raise ValueError("每周必须包含七天")
    for day in weekly:
        if not isinstance(day, list) or len(day) > 8:
            raise ValueError("每天最多八个时间段")
        for pair in day:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("时间段格式应为 09:00-18:00")
            for position, value in enumerate(pair):
                if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
                    raise ValueError("时间格式应为 HH:mm")
                hour, minute = map(int, value.split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59) and not (position == 1 and hour == 24 and minute == 0):
                    raise ValueError("时间超出范围")
            if pair[0] == pair[1]:
                raise ValueError("起止时间相同，请用 00:00-24:00 表示全天")


def boundary(day, value, zone, ending=False):
    hour, minute = map(int, value.split(":"))
    if hour == 24:
        day += timedelta(days=1); hour = 0
    naive = datetime(day.year, day.month, day.day, hour, minute)
    # DST 空洞向前推进，重复时段起点取早、终点取晚。
    for _ in range(181):
        candidates = []
        for fold in (0, 1):
            local = naive.replace(tzinfo=zone, fold=fold)
            utc = local.astimezone(timezone.utc)
            if utc.astimezone(zone).replace(tzinfo=None) == naive:
                candidates.append(utc)
        if candidates:
            return max(candidates) if ending else min(candidates)
        naive += timedelta(minutes=1)
    raise ValueError("无法解析时区时间")


def intervals(weekly, zone_name, now=None, days=7):
    validate(weekly, zone_name)
    zone = ZoneInfo(zone_name)
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(zone).date()
    result = []
    for delta in range(-1, days):
        day = today + timedelta(days=delta)
        for start, end in weekly[day.weekday()]:
            first = boundary(day, start, zone)
            last = boundary(day + timedelta(days=int(end < start)), end, zone, True)
            if last > now:
                result.append((first, last))
    return sorted(result)


def active(weekly, zone, now=None):
    now = now or datetime.now(timezone.utc)
    return any(start <= now < end for start, end in intervals(weekly, zone, now, 1))
