"""Per-brand hourly curves in the brand's own timezone.

Uses zoneinfo when the tz database is available (Linux/macOS/Codespaces); otherwise falls
back to US DST rules implemented here (Windows may lack tzdata). Both give the same offsets.
"""
import datetime as _dt
import functools
import math

from .entities import BRANDS, BRAND_SHARE

try:  # pragma: no cover - depends on platform
    from zoneinfo import ZoneInfo
    _IANA = {"US/Central": "America/Chicago", "US/Eastern": "America/New_York",
             "US/Pacific": "America/Los_Angeles"}
    _ZI = {k: ZoneInfo(v) for k, v in _IANA.items()}
    HAVE_ZONEINFO = True
except Exception:  # noqa
    _ZI = {}
    HAVE_ZONEINFO = False

_STD = {"US/Central": -6 * 3600, "US/Eastern": -5 * 3600, "US/Pacific": -8 * 3600}
_UTC = _dt.timezone.utc


def _nth_sunday(year, month, n):
    d = _dt.date(year, month, 1)
    first = (6 - d.weekday()) % 7  # days to first Sunday
    return _dt.date(year, month, 1 + first + 7 * (n - 1))


def fallback_offset(tzname, epoch_s):
    """US DST rules: 2nd Sunday of March 02:00 local standard to 1st Sunday of Nov 02:00 local DST."""
    std = _STD[tzname]
    year = _dt.datetime.fromtimestamp(epoch_s, _UTC).year
    ds = _nth_sunday(year, 3, 2)
    de = _nth_sunday(year, 11, 1)
    start = _dt.datetime(ds.year, ds.month, ds.day, 2, tzinfo=_UTC).timestamp() - std
    end = _dt.datetime(de.year, de.month, de.day, 1, tzinfo=_UTC).timestamp() - std
    return std + 3600 if start <= epoch_s < end else std


def zoneinfo_offset(tzname, epoch_s):
    return int(_dt.datetime.fromtimestamp(epoch_s, _ZI[tzname]).utcoffset().total_seconds())


@functools.lru_cache(maxsize=None)
def _off_hour(tzname, epoch_hour):
    e = epoch_hour * 3600
    if HAVE_ZONEINFO:
        return zoneinfo_offset(tzname, e)
    return fallback_offset(tzname, e)


def tz_offset(tzname, epoch_s):
    return _off_hour(tzname, int(epoch_s) // 3600)


def _curve(peaks, floor):
    vals = []
    for h in range(24):
        v = floor
        for c, w, a in peaks:
            d = min(abs(h - c), 24 - abs(h - c))
            v += a * math.exp(-0.5 * (d / w) ** 2)
        vals.append(v)
    mean = sum(vals) / 24.0
    vals = [v / mean for v in vals]
    return vals + [vals[0]]


# (hour centre, width, amplitude)
CURVES = {
    "qsr": _curve([(8, 1.2, 0.35), (12.3, 1.5, 1.0), (18.3, 1.8, 1.1), (21.5, 1.5, 0.3)], 0.06),
    "coffee": _curve([(7.5, 1.6, 1.0), (10.0, 1.5, 0.45), (14.5, 1.8, 0.35)], 0.04),
    "dinner": _curve([(12.2, 1.3, 0.45), (18.8, 1.9, 1.0)], 0.04),
    "lunch": _curve([(12.3, 1.5, 1.0), (18.5, 1.6, 0.4)], 0.04),
    "pizza": _curve([(12.5, 1.5, 0.35), (18.8, 1.8, 1.0), (22.5, 2.0, 0.65)], 0.05),
}
GROWTH_EPOCH = _dt.datetime(2026, 9, 1, tzinfo=_UTC).timestamp()
GROWTH_PER_WEEK = 0.02


def growth(epoch_s):
    return 1.0 + GROWTH_PER_WEEK * (epoch_s - GROWTH_EPOCH) / (7 * 86400.0)


def curve_value(curve, hour_frac):
    c = CURVES[curve]
    i = int(hour_frac)
    f = hour_frac - i
    return c[i] * (1.0 - f) + c[i + 1] * f


def brand_factor(brand, epoch_s):
    """Relative volume (daily mean ~1 on weekdays) for a brand at a UTC epoch second."""
    local = epoch_s + tz_offset(brand["tz"], epoch_s)
    day = int(local // 86400)
    wd = (day + 3) % 7  # 1970-01-01 was a Thursday; Monday=0
    hf = (local % 86400) / 3600.0
    wk = brand["weekend"] if wd >= 5 else 1.0
    return curve_value(brand["curve"], hf) * wk * growth(epoch_s)


def brand_factors(epoch_s):
    return [brand_factor(b, epoch_s) for b in BRANDS]


def global_factor(epoch_s):
    """Site-weighted average over all brands."""
    tot = 0.0
    for b, sh in zip(BRANDS, BRAND_SHARE):
        tot += sh * brand_factor(b, epoch_s)
    return tot


def local_hour(brand, epoch_s):
    return ((epoch_s + tz_offset(brand["tz"], epoch_s)) % 86400) / 3600.0
