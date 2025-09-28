import pytz

from datetime import datetime, timedelta


def parse_iso_to_pytz_utc(iso_str: str) -> datetime:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(pytz.UTC)


def get_sleep_duration(sleep_until: datetime, to_seconds: bool = False, to_minutes: bool = True) -> float:
    current_time = datetime.now(pytz.UTC)
    sleep_until = sleep_until.replace(tzinfo=pytz.UTC)

    if sleep_until > current_time:
        if to_seconds:
            return (sleep_until - current_time).total_seconds()
        elif to_minutes:
            return (sleep_until - current_time).total_seconds() / 60

    return 0


def minutes_to_pytz_utc(minutes: int) -> datetime:
    return datetime.now(pytz.UTC) + timedelta(minutes=minutes)


async def verify_sleep(sleep_until: datetime) -> bool:
    current_time = datetime.now(pytz.UTC)
    sleep_until = sleep_until.replace(tzinfo=pytz.UTC)

    if sleep_until > current_time:
        return True

    return False


def get_sleep_until(minutes: int = None, seconds: int = None) -> datetime:
    duration = timedelta()

    if minutes is not None:
        duration += timedelta(minutes=minutes)

    if seconds is not None:
        duration += timedelta(seconds=seconds)

    return datetime.now(pytz.UTC) + duration
