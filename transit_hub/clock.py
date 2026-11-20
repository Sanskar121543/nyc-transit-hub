import calendar
import datetime as dt

STANDARD = -5 * 3600
DAYLIGHT = -4 * 3600
_UTC = dt.timezone.utc


def _nth_sunday(year, month, n):
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))


def _dst_days(year):
    return _nth_sunday(year, 3, 2), _nth_sunday(year, 11, 1)


def utc_offset(epoch):
    year = dt.datetime.fromtimestamp(epoch, _UTC).year
    start, end = _dst_days(year)
    begins = calendar.timegm((start.year, start.month, start.day, 7, 0, 0))
    ends = calendar.timegm((end.year, end.month, end.day, 6, 0, 0))
    return DAYLIGHT if begins <= epoch < ends else STANDARD


def local_datetime(epoch):
    return dt.datetime.fromtimestamp(epoch + utc_offset(epoch), _UTC).replace(tzinfo=None)


def local_date(epoch):
    return local_datetime(epoch).date()


def service_day_start(day):
    """Epoch of 'noon minus 12h' for a service day, the origin GTFS times count from."""
    start, end = _dst_days(day.year)
    offset = DAYLIGHT if start <= day < end else STANDARD
    noon = calendar.timegm((day.year, day.month, day.day, 12, 0, 0)) - offset
    return noon - 12 * 3600


def format_clock(epoch):
    return local_datetime(epoch).strftime("%I:%M %p").lstrip("0")


def parse_clock(text, now):
    """Turn 'HH:MM' into an epoch on the local day of `now`, rolling to tomorrow if already past."""
    hours, minutes = text.split(":")
    if not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
        raise ValueError("time must be HH:MM between 00:00 and 23:59")
    day = local_date(now)
    base = service_day_start(day)
    epoch = base + int(hours) * 3600 + int(minutes) * 60
    if epoch < now - 60:
        epoch += 86400
    return epoch
