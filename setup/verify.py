"""Verification: counts, job/datafeed states, APM services, anomaly assertions."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import jobs as jobmod
from . import names
from .client import ApiError, EsClient, KbClient
from .logutil import say
from .meta import read_meta
from .timeutil import iso, parse_iso, utcnow

REPORT_FILE = "verify-report.json"
EXPECTED_SERVICES = ["cart-api", "payments-gateway", "orders-api",
                     "admin-portal-api", "inventory-sync", "catalog-sync"]
PRE_MARGIN = timedelta(minutes=15)
POST_MARGIN = timedelta(minutes=30)


def full_job_id(job: str) -> str:
    """Accept short ids such as ``apm-multimetric`` and return ``mlws-...``."""
    return job if job.startswith("mlws-") else "mlws-" + job


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    return [value] if isinstance(value, str) else list(value)


def anomaly_query(job_id: str, partition: str, start: datetime, end: datetime,
                  function: Any = None, field_name: Any = None) -> Dict[str, Any]:
    """Search body: record results for ``partition`` in the padded window, max record_score.

    Optional ``function`` / ``field_name`` (a string or a list meaning "any of")
    restrict the records to the detector the anomaly is expected to hit.
    """
    extra: List[Dict[str, Any]] = []
    for key, val in (("function", function), ("field_name", field_name)):
        vals = _as_list(val)
        if vals:
            extra.append({"terms": {key: vals}})
    return {
        "size": 0,
        "query": {"bool": {"filter": [
            {"term": {"result_type": "record"}},
            {"term": {"job_id": job_id}},
            {"range": {"timestamp": {"gte": iso(start - PRE_MARGIN), "lte": iso(end + POST_MARGIN)}}},
            {"bool": {"should": [
                {"term": {"partition_field_value": partition}},
                {"term": {"by_field_value": partition}},
                {"term": {"over_field_value": partition}},
            ], "minimum_should_match": 1}},
        ] + extra}},
        "aggs": {"max_score": {"max": {"field": "record_score"}}},
    }


def max_record_score(es: EsClient, job_id: str, partition: str, start: datetime, end: datetime,
                     function: Any = None, field_name: Any = None) -> Optional[float]:
    """Highest record_score in the window, or None if there are no records."""
    _, resp = es.post("/.ml-anomalies-*/_search", anomaly_query(job_id, partition, start, end, function, field_name),
                      params={"ignore_unavailable": "true"})
    if not (resp.get("hits", {}).get("total", {}).get("value", 0)):
        return None
    return (resp.get("aggregations", {}).get("max_score", {}) or {}).get("value")


def evaluate(kind: str, score: Optional[float], threshold: float, caught_up: bool) -> str:
    """PASS / FAIL / PENDING for one assertion."""
    if kind == "catch":
        if score is not None and score >= threshold:
            return "PASS"
        return "FAIL" if caught_up else "PENDING"
    if score is not None and score >= threshold:
        return "FAIL"
    return "PASS" if caught_up else "PENDING"


def _add(rows: List[Dict[str, str]], category: str, name: str, status: str, detail: str) -> None:
    rows.append({"category": category, "name": name, "status": status, "detail": detail})


def check_counts(es: EsClient, rows: List[Dict[str, str]]) -> None:
    """Document count per data stream (must be > 0)."""
    for stream in names.DATA_STREAMS:
        try:
            _, resp = es.post("/%s/_count" % stream, None, allow=(404,))
            n = resp.get("count", 0) if isinstance(resp, dict) else 0
        except ApiError as exc:
            _add(rows, "data", stream, "FAIL", str(exc))
            continue
        _add(rows, "data", stream, "PASS" if n > 0 else "FAIL", "%s docs" % format(n, ","))


def check_jobs(es: EsClient, specs: List[jobmod.JobSpec], rows: List[Dict[str, str]]) -> Dict[str, Optional[datetime]]:
    """Job and datafeed states; returns latest_record_timestamp per job."""
    latest: Dict[str, Optional[datetime]] = {}
    for spec in specs:
        st = jobmod.job_stats(es, spec.job_id)
        if st is None:
            _add(rows, "jobs", spec.job_id, "FAIL", "job not found")
            latest[spec.job_id] = None
            continue
        ts = (st.get("data_counts") or {}).get("latest_record_timestamp")
        latest[spec.job_id] = parse_iso(jobmod.time_from_millis(ts)) if ts else None
        jstate = st.get("state")
        fstate = jobmod.datafeed_state(es, spec.datafeed_id)
        status = "PASS" if jstate == "opened" and fstate == "started" else (
            "PENDING" if jstate == "opening" or (jstate == "opened" and fstate == "starting") else "FAIL")
        _add(rows, "jobs", spec.job_id, status, "job %s, datafeed %s, latest record %s" % (
            jstate, fstate, iso(latest[spec.job_id]) if latest[spec.job_id] else "none"))
    return latest


def mlws_trace_services(es: EsClient) -> Dict[str, int]:
    """Doc counts per production service in traces-apm-mlws (empty if unreadable)."""
    body = {"size": 0, "query": {"term": {"service.environment": "production"}},
            "aggs": {"svc": {"terms": {"field": "service.name", "size": 50}}}}
    _, resp = es.post("/traces-apm-mlws/_search", body, allow=(404,))
    buckets = ((resp.get("aggregations") or {}).get("svc") or {}).get("buckets", []) if isinstance(resp, dict) else []
    return {b["key"]: b.get("doc_count", 0) for b in buckets}


def check_apm(kb: KbClient, rows: List[Dict[str, str]], es: Optional[EsClient] = None) -> None:
    """APM services visible in production AND backed by docs in traces-apm-mlws.

    The second condition avoids false positives on projects that already show
    same-named services from other data.
    """
    end = utcnow()
    params = {
        "start": iso(end - timedelta(days=3)), "end": iso(end), "environment": "production", "kuery": "",
        "probability": "1", "documentType": "serviceTransactionMetric", "rollupInterval": "1m",
        "useDurationSummary": "true",
    }
    try:
        # INTERNAL API: no public equivalent on Serverless 9.6 (APM services list as shown in the UI)
        _, resp = kb.get("/internal/apm/services", params=params)
    except ApiError as exc:
        _add(rows, "apm", "services", "WARN", "could not query APM services (%s)" % exc)
        return
    found = {i.get("serviceName") for i in resp.get("items", [])
             if i.get("environments") is None or "production" in (i.get("environments") or [])}         if isinstance(resp, dict) else set()
    if es is not None:
        try:
            have_docs = mlws_trace_services(es)
        except ApiError as exc:
            _add(rows, "apm", "services", "WARN", "could not query traces-apm-mlws (%s)" % exc)
            return
        found = {s for s in found if have_docs.get(s, 0) > 0}
    missing = [s for s in EXPECTED_SERVICES if s not in found]
    _add(rows, "apm", "services", "PASS" if not missing else "FAIL",
         "%d/%d visible%s" % (len(EXPECTED_SERVICES) - len(missing), len(EXPECTED_SERVICES),
                              (", missing: " + ", ".join(missing)) if missing else ""))


def check_anomalies(es: EsClient, meta: Dict[str, Any], latest: Dict[str, Optional[datetime]],
                    rows: List[Dict[str, str]]) -> None:
    """Assertions from each resolved anomaly's ``catches`` / ``must_not_catch``."""
    for an in meta.get("anomalies", []):
        if not an.get("start") or not an.get("end"):
            continue
        start, end = parse_iso(an["start"]), parse_iso(an["end"])
        for kind, key, field_name in (("catch", "catches", "min_score"), ("not", "must_not_catch", "max_score")):
            for entry in an.get(key) or []:
                job = full_job_id(entry["job"])
                part = entry["partition"]
                threshold = float(entry.get(field_name, 75 if kind == "catch" else 25))
                label = "%s %s %s/%s" % (an.get("id"), "catches" if kind == "catch" else "must-not-catch", job.replace("mlws-", ""), part)
                if job not in latest:
                    _add(rows, "anomaly", label, "SKIP", "job not part of this setup")
                    continue
                try:
                    score = max_record_score(es, job, part, start, end, entry.get("function"), entry.get("field_name"))
                except ApiError as exc:
                    _add(rows, "anomaly", label, "FAIL", str(exc))
                    continue
                lts = latest.get(job)
                caught_up = lts is not None and lts >= end + POST_MARGIN
                status = evaluate(kind, score, threshold, caught_up)
                _add(rows, "anomaly", label, status, "max record_score %s (%s %s)" % (
                    "none" if score is None else "%.0f" % score, ">=" if kind == "catch" else "<", "%.0f" % threshold))


def print_table(rows: List[Dict[str, str]]) -> None:
    """Fixed-width table of all rows."""
    w = max([len(r["name"]) for r in rows] + [10])
    say("%-8s %-*s %-8s %s" % ("area", w, "check", "status", "detail"))
    for r in rows:
        say("%-8s %-*s %-8s %s" % (r["category"], w, r["name"], r["status"], r["detail"]))


def run_verify(es: EsClient, kb: KbClient, root: Path, allow_pending: bool = False,
               report_path: Optional[Path] = None) -> int:
    """Run all checks, print a table, write the JSON report; exit code 0/1."""
    say("Verify")
    rows: List[Dict[str, str]] = []
    meta = read_meta(es)
    if not meta:
        _add(rows, "meta", "mlws-meta", "FAIL", "no setup marker found; run `python setup.py setup` first")
        specs: List[jobmod.JobSpec] = []
    else:
        specs = jobmod.load_specs(root, bool(meta.get("with_population")), bool(meta.get("prebuild_module_job")))
        _add(rows, "meta", "mlws-meta", "PASS", "t0 %s, %s days, scale %s" % (meta.get("t0"), meta.get("days"), meta.get("scale")))
    check_counts(es, rows)
    latest = check_jobs(es, specs, rows)
    check_apm(kb, rows, es)
    if meta:
        check_anomalies(es, meta, latest, rows)
    print_table(rows)
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    failed = counts.get("FAIL", 0) > 0 or (counts.get("PENDING", 0) > 0 and not allow_pending)
    report = {"generated_at": iso(utcnow()), "allow_pending": allow_pending, "summary": counts,
              "passed": not failed, "results": rows}
    path = report_path or (root / REPORT_FILE)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    say("Summary: %s. Report written to %s" % (", ".join("%s=%d" % kv for kv in sorted(counts.items())), path.name))
    if counts.get("PENDING") and allow_pending:
        say("Pending items are jobs still catching up; re-run verify in a few minutes.")
    return 1 if failed else 0
