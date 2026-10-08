"""Live scenarios: started by ``setup`` and replayable with ``setup.py inject <scenario>``."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import names
from .client import ApiError, EsClient
from .loader import BulkStats, bulk_docs
from .logutil import say, warn
from .meta import jsonable, read_meta, write_meta
from .timeutil import ceil_minutes, humanize, iso, parse_iso, utcnow

BUCKET_MINUTES = 15
QUERY_DELAY = timedelta(seconds=90)
SCENARIOS = ("cascade", "info-flood", "delayed")


def delete_query(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build the _delete_by_query body for one ``delete`` spec."""
    start, end = spec["range"]
    filters: List[Dict[str, Any]] = [
        {"range": {"@timestamp": {"gte": iso(parse_iso(start)), "lt": iso(parse_iso(end))}}}
    ]
    for field_name, value in (spec.get("terms") or {}).items():
        values = value if isinstance(value, (list, tuple)) else [value]
        filters.append({"terms": {field_name: list(values)}})
    return {"query": {"bool": {"filter": filters}}}


def delete_windows(es: EsClient, specs: List[Dict[str, Any]]) -> int:
    """Delete normal tail docs for affected entities (allowlisted streams only)."""
    total = 0
    for spec in specs:
        stream = names.assert_allowed(spec["stream"])
        _, resp = es.post(
            "/%s/_delete_by_query" % stream, delete_query(spec),
            params={"conflicts": "proceed", "refresh": "true", "wait_for_completion": "true"}, timeout=120,
        )
        n = resp.get("deleted", 0) if isinstance(resp, dict) else 0
        total += n
        say("  deleted %d docs from %s %s" % (n, stream, spec.get("terms", {})))
    return total


def split_hours(windows: List[Tuple[Any, Any]]) -> List[Tuple[str, str]]:
    """Split windows at clock-hour boundaries (generator works in 1h chunks)."""
    out: List[Tuple[str, str]] = []
    for s, e in windows:
        cur, end = parse_iso(s), parse_iso(e)
        while cur < end:
            nxt = min(end, cur.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            out.append((iso(cur), iso(nxt)))
            cur = nxt
    return out


def appearance(anomaly: Dict[str, Any], now: datetime) -> str:
    """Describe when an injected anomaly should show up in ML results."""
    start, end = parse_iso(anomaly["start"]), parse_iso(anomaly["end"])
    first_bucket_end = ceil_minutes(start + timedelta(seconds=1), BUCKET_MINUTES)
    last_bucket_end = ceil_minutes(end, BUCKET_MINUTES)
    first_visible = first_bucket_end + QUERY_DELAY
    last_visible = last_bucket_end + QUERY_DELAY

    def rel(t: datetime) -> str:
        d = t - now
        return ("in %s" % humanize(d)) if d.total_seconds() > 0 else "already (%s ago)" % humanize(d)

    return "anomalous from %s to %s UTC; first bucket result ~%s UTC (%s), last ~%s UTC (%s)" % (
        start.strftime("%H:%M"), end.strftime("%H:%M"), first_visible.strftime("%H:%M:%S"), rel(first_visible),
        last_visible.strftime("%H:%M:%S"), rel(last_visible))


def scenario_end(anomalies: List[Dict[str, Any]], now: datetime, scenario: str) -> datetime:
    """When a scenario counts as finished (used to avoid starting it twice).

    The delayed scenario writes into the past, so it is held "active" for 30 minutes after it started.
    """
    end = max(parse_iso(a["end"]) for a in anomalies) if anomalies else now
    if scenario == "delayed":
        end = max(end, now + timedelta(minutes=30))
    return end


def active_scenarios(meta: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, str]:
    """Scenarios recorded in the meta document that have not finished yet: ``{name: end time (ISO)}``."""
    now = now or utcnow()
    out: Dict[str, str] = {}
    for name, rec in ((meta or {}).get("live_scenarios") or {}).items():
        try:
            if parse_iso(rec["ends_at"]) > now:
                out[name] = rec["ends_at"]
        except (KeyError, ValueError, TypeError):
            continue
    return out


def record_scenario(es: EsClient, meta: Dict[str, Any], scenario: str, now: datetime,
                    anomalies: List[Dict[str, Any]]) -> None:
    """Remember a started scenario (start and end time) in the meta document."""
    live = dict(meta.get("live_scenarios") or {})
    live[scenario] = {"started_at": iso(now), "ends_at": iso(scenario_end(anomalies, now, scenario))}
    meta["live_scenarios"] = live
    write_meta(es, meta)


def run_inject(es: EsClient, scenario: str, now: Optional[datetime] = None) -> int:
    """Plan, delete, regenerate and bulk-load a live scenario. Returns exit code."""
    from data import generate, inject as data_inject  # noqa: F401  (generator owned by data/)

    if scenario not in SCENARIOS:
        warn("unknown scenario %r (choose from %s)" % (scenario, ", ".join(SCENARIOS)))
        return 1
    now = now or utcnow()
    meta = read_meta(es)
    if not meta or not meta.get("loaded"):
        warn("no completed setup found (mlws-meta); run `python setup.py setup` first")
        return 1
    say("Injecting scenario '%s' at %s UTC" % (scenario, iso(now)))
    spec = data_inject.plan_inject(meta, scenario, now)
    deleted = delete_windows(es, spec.get("delete") or [])
    windows = split_hours(list(spec.get("windows") or []))
    plan_dict = jsonable(spec["plan"]) if not isinstance(spec["plan"], dict) else spec["plan"]
    stats = BulkStats()
    for s, e in windows:
        docs = generate.generate_chunk(plan_dict, s, e)
        stats.merge(bulk_docs(es, docs).to_dict())
    say("  indexed %s docs (%d errors), deleted %d normal docs" % (format(stats.docs, ","), stats.errors, deleted))
    for reason in stats.samples:
        say("    - " + reason)
    say("")
    say("What was injected")
    anomalies = jsonable(spec.get("anomalies") or [])
    for an in anomalies:
        say("  %s %s: %s" % (an.get("id", "?"), an.get("title", ""), appearance(an, now)))
    if spec.get("delayed"):
        say("  Delayed data: the real-time datafeed already passed this window. Expect a delayed-data "
            "annotation/message on the XYZ job within a few datafeed cycles.")
    say("Timing: the first results appear about 20-35 minutes after the problem starts.")
    say("Watch: Anomaly Explorer and the Rules page (links: python setup.py links).")
    if stats.errors:
        return 1
    record_scenario(es, meta, scenario, now, anomalies)
    return 0


__all__ = ["run_inject", "active_scenarios", "record_scenario", "scenario_end", "delete_query", "split_hours", "appearance", "ApiError"]
