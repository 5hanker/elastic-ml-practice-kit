"""ML anomaly detection jobs, datafeeds, sync, wait and forecasts."""

import copy
import json
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import names
from .client import ApiError, EsClient, KbClient
from .logutil import say, warn
from .timeutil import humanize, iso, parse_iso, utcnow

WAIT_TIMEOUT_S = 12 * 60
WAIT_INTERVAL_S = 15
CAUGHT_UP_LAG = timedelta(minutes=30)


@dataclass
class JobSpec:
    """One ``jobs/NN-<job_id>.json`` file."""

    job_id: str
    requires: Optional[str]
    job: Dict[str, Any]
    datafeed: Dict[str, Any]
    forecast: Optional[Dict[str, Any]]
    path: str = ""

    @property
    def datafeed_id(self) -> str:
        return names.datafeed_id(self.job_id)


def load_specs(root: Path, with_population: bool = False, prebuild_module_job: bool = False,
               include_all: bool = False) -> List[JobSpec]:
    """Load ``jobs/*.json`` sorted by file name, honoring ``requires``."""
    specs: List[JobSpec] = []
    for path in sorted((root / "jobs").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        req = d.get("requires")
        if not include_all:
            if req == "with_population" and not with_population:
                continue
            if req == "prebuild_module_job" and not prebuild_module_job:
                continue
        job_id = names.assert_allowed(d["job_id"])
        specs.append(JobSpec(job_id, req, d["job"], d["datafeed"], d.get("forecast"), str(path)))
    return specs


def _exists(es: EsClient, path: str) -> bool:
    status, _ = es.get(path, allow=(404,))
    return status != 404


def ensure_job(es: EsClient, spec: JobSpec) -> str:
    """Create-or-skip the job and its datafeed. Returns 'created' or 'exists'."""
    jid = names.assert_allowed(spec.job_id)
    did = names.assert_allowed(spec.datafeed_id)
    outcome = "exists"
    if not _exists(es, "/_ml/anomaly_detectors/" + jid):
        es.put("/_ml/anomaly_detectors/" + jid, spec.job)
        outcome = "created"
    if not _exists(es, "/_ml/datafeeds/" + did):
        body = copy.deepcopy(spec.datafeed)
        body["job_id"] = jid
        es.put("/_ml/datafeeds/" + did, body)
        outcome = "created"
    return outcome


def job_stats(es: EsClient, job_id: str) -> Optional[Dict[str, Any]]:
    """Stats for one job, or None if missing."""
    status, resp = es.get("/_ml/anomaly_detectors/%s/_stats" % job_id, allow=(404,))
    if status == 404:
        return None
    jobs = resp.get("jobs", [])
    return jobs[0] if jobs else None


def datafeed_state(es: EsClient, datafeed_id: str) -> Optional[str]:
    """State string of a datafeed, or None if missing."""
    status, resp = es.get("/_ml/datafeeds/%s/_stats" % datafeed_id, allow=(404,))
    if status == 404:
        return None
    feeds = resp.get("datafeeds", [])
    return feeds[0].get("state") if feeds else None


def _open_job(es: EsClient, jid: str) -> str:
    """Open a job; 'opened', 'opening' (408: still opening) or 'already'."""
    try:
        es.post("/_ml/anomaly_detectors/%s/_open" % jid, {"timeout": "30s"}, allow=(409,), timeout=90)
    except ApiError as exc:
        if exc.status != 408:
            raise
        say("  %s: still opening (ML node cold start); continuing" % jid)
        return "opening"
    return "opened"


def start_job(es: EsClient, spec: JobSpec, start: str) -> str:
    """Open the job and start its real-time datafeed. Returns a status word.

    408 from open/start means "still opening" (lazy open) -> 'starting';
    409 means already started/starting -> ok. A 'failed' job is force-closed
    and reopened once; if it fails again it is reported, not retried.
    The job is never reset here (a new job has nothing to reset).
    """
    jid, did = spec.job_id, spec.datafeed_id
    st = job_stats(es, jid) or {}
    if st.get("state") == "failed":
        say("  %s: job in 'failed' state; force-closing and reopening once" % jid)
        es.post("/_ml/anomaly_detectors/%s/_close" % jid, {"force": True}, allow=(409,), timeout=90)
    opened = _open_job(es, jid)
    if opened != "opening" and (job_stats(es, jid) or {}).get("state") == "failed":
        return "failed (job state 'failed' after reopen; see job messages)"
    state = datafeed_state(es, did)
    if state in ("started", "starting"):
        return "running"
    stats = job_stats(es, jid) or st
    processed = (stats.get("data_counts") or {}).get("processed_record_count", 0)
    body: Dict[str, Any] = {"timeout": "60s"}
    if not processed:  # first start: backfill from the plan start, then run in real time (no end)
        body["start"] = start
    try:
        es.post("/_ml/datafeeds/%s/_start" % did, body, allow=(409,), timeout=120)
    except ApiError as exc:
        if exc.status != 408:
            raise
        say("  %s: datafeed still starting (job opening); it will start on its own" % jid)
        return "starting"
    return "started"


def sync_saved_objects(kb: KbClient) -> None:
    """Make ML jobs visible in the Kibana ML UI (public route, GET)."""
    try:
        kb.get("/api/ml/saved_objects/sync")
        say("  ML saved objects synced")
    except ApiError as exc:
        warn("ML saved objects sync failed (%s); jobs may not appear in the UI until synced" % exc)


def ensure_all(es: EsClient, kb: KbClient, specs: List[JobSpec], start: str) -> Dict[str, str]:
    """Create, open and start every job, then sync saved objects."""
    say("ML jobs")
    results: Dict[str, str] = {}
    for spec in specs:
        outcome = ensure_job(es, spec)
        try:
            state = start_job(es, spec, start)
        except ApiError as exc:
            warn("%s: could not start (%s)" % (spec.job_id, exc))
            state = "start-failed"
        results[spec.job_id] = "%s/%s" % (outcome, state)
        say("  %-36s %s, datafeed %s" % (spec.job_id, outcome, state))
    sync_saved_objects(kb)
    return results


def lag_of(stats: Dict[str, Any], now: Any) -> Optional[timedelta]:
    """How far the job's latest record is behind ``now`` (None if no data yet)."""
    ts = (stats.get("data_counts") or {}).get("latest_record_timestamp")
    if ts is None:
        return None
    return now - parse_iso(time_from_millis(ts))


def time_from_millis(ms: Any) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(ms) / 1000.0, timezone.utc).isoformat()


def wait_for_catch_up(
    es: EsClient,
    specs: List[JobSpec],
    timeout_s: float = WAIT_TIMEOUT_S,
    interval_s: float = WAIT_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> Dict[str, bool]:
    """Poll until every job's latest record is within 30 minutes of now.

    Returns ``{job_id: caught_up}``. Never raises on timeout.
    """
    say("Waiting for datafeeds to catch up (timeout %s)" % humanize(timedelta(seconds=timeout_s)))
    t0 = clock()
    caught = {s.job_id: False for s in specs}
    failed: Dict[str, str] = {}
    while True:
        parts = []
        for spec in specs:
            jid = spec.job_id
            if caught[jid] or jid in failed:
                continue
            st = job_stats(es, jid)
            if st is None:
                failed[jid] = "missing"
                continue
            if st.get("state") == "failed":
                failed[jid] = "failed"
                warn("%s is in state 'failed'" % jid)
                continue
            lag = lag_of(st, utcnow())
            if lag is not None and lag <= CAUGHT_UP_LAG:
                caught[jid] = True
            else:
                parts.append("%s %s" % (jid.replace("mlws-", ""), "no data yet" if lag is None else "lag " + humanize(lag)))
        pending = [j for j, ok in caught.items() if not ok and j not in failed]
        say("  caught up %d/%d%s" % (sum(caught.values()), len(caught), ("  waiting: " + ", ".join(parts)) if parts else ""))
        if not pending:
            return caught
        if clock() - t0 >= timeout_s:
            warn("timed out waiting for: %s (they keep running; re-run `python setup.py verify` later)" % ", ".join(pending))
            return caught
        sleep(interval_s)


def run_forecasts(es: EsClient, specs: List[JobSpec]) -> None:
    """Run a forecast for jobs that define one, only if none exists yet."""
    for spec in specs:
        if not spec.forecast:
            continue
        jid = names.assert_allowed(spec.job_id)
        st = job_stats(es, jid) or {}
        if (st.get("forecasts_stats") or {}).get("total", 0) > 0:
            say("  forecast %s: already exists" % jid)
            continue
        try:
            es.post("/_ml/anomaly_detectors/%s/_forecast" % jid, {"duration": spec.forecast.get("duration", "3d")})
            say("  forecast %s: started (%s)" % (jid, spec.forecast.get("duration", "3d")))
        except ApiError as exc:
            warn("forecast for %s failed (%s); re-run setup later" % (jid, exc))
