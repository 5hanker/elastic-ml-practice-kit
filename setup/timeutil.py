"""Small UTC time helpers (Python 3.9 safe)."""

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc


def utcnow() -> datetime:
    """Current time, timezone-aware UTC."""
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    """Format as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: Any) -> datetime:
    """Parse an ISO-8601 string (``Z`` allowed) or pass a datetime through."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        head, _, frac = text.partition(".")
        tz = ""
        for sep in ("+", "-"):
            if sep in frac:
                frac, tz = frac.split(sep, 1)
                tz = sep + tz
                break
        dt = datetime.fromisoformat("%s.%s%s" % (head, frac[:6].ljust(6, "0"), tz))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def ceil_minutes(dt: datetime, n: int) -> datetime:
    """Round ``dt`` up to the next multiple of ``n`` minutes (epoch aligned)."""
    step = n * 60
    ts = dt.timestamp()
    return datetime.fromtimestamp(-(-ts // step) * step, UTC)


def humanize(delta: timedelta) -> str:
    """Compact duration such as ``1h05m`` or ``42s``."""
    s = int(abs(delta.total_seconds()))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm%02ds" % (s // 60, s % 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)
