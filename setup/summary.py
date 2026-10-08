"""Completion checklist and deep links."""

from typing import Any, Dict, List, Optional

from .kibana import deep_links
from .logutil import say

EXPLORER_JOBS = ["mlws-apm-multimetric", "mlws-logs-errors-correct", "mlws-logs-errors-level-filtered",
                 "mlws-logs-categories", "mlws-logs-store-volume", "mlws-logs-inventory-health"]
VIEWER_JOBS = ["mlws-apm-multimetric", "mlws-logs-store-volume"]


def _fmt_tail(tail_end: str) -> str:
    """Render the tail_end meta value as 'YYYY-MM-DD HH:MM' (UTC)."""
    t = str(tail_end).replace("T", " ").rstrip("Z")
    return t[:16]


def print_links(kibana_url: str, job_ids: Optional[List[str]] = None) -> None:
    """Print the deep links (Anomaly Explorer for the jobs that exist)."""
    explorer = [j for j in EXPLORER_JOBS if job_ids is None or j in job_ids]
    viewer = [j for j in VIEWER_JOBS if job_ids is None or j in job_ids]
    say("Links")
    for label, url in deep_links(kibana_url, explorer, viewer):
        say("  %-46s %s" % (label, url))


def _utc(value: Any) -> str:
    """'2026-10-02T17:00:00Z' -> '2026-10-02 17:00'."""
    return str(value).replace("T", " ").rstrip("Z")[:16]


def _planted(anomalies: Optional[List[Dict[str, Any]]]) -> List[str]:
    lines = []
    for a in anomalies or []:
        if not a.get("start") or not a.get("id", "").startswith("A"):
            continue
        lines.append("  %s  %s  (%s to %s UTC)" % (a["id"], a.get("title", ""), _utc(a["start"]),
                                                  _utc(a["end"])[11:] if a.get("end") else "?"))
    return lines


def _live_lines(live: Optional[Dict[str, str]]) -> List[str]:
    live = live or {}
    out: List[str] = []

    def state(name: str) -> str:
        o = live.get(name, "")
        if o == "started" or not o:
            return ""
        return "  [%s]" % o

    if "cascade" in live:
        out.append("  payments-gateway starts failing in about 2 minutes; its alert appears about 25-35 minutes "
                   "after setup finishes.%s" % state("cascade"))
        out.append("  orders-api latency starts about 15 minutes after that; its alert about 15 minutes later.")
    if "info-flood" in live:
        out.append("  The ABC application starts logging a flood of INFO messages in about 2 minutes; "
                   "its results appear about 20-35 minutes after setup finishes.%s" % state("info-flood"))
    if "delayed" in live:
        out.append("  Some XYZ logs were added late, into a period the jobs had already analysed "
                   "(look for a delayed-data warning on the XYZ job).%s" % state("delayed"))
    return out


def print_summary(kibana_url: str, steps: Dict[str, str], job_ids: List[str], prebuilt_module_job: bool,
                  tail_end: Optional[str] = None, anomalies: Optional[List[Dict[str, Any]]] = None,
                  live: Optional[Dict[str, str]] = None, live_meta: Optional[Dict[str, Any]] = None) -> None:
    """Checklist of what setup did, links, what was started for the learner, and next steps."""
    say("")
    say("Setup summary")
    for name, status in steps.items():
        mark = "[x]" if status.startswith("ok") or status.startswith("skipped") else "[ ]"
        say("  %s %-16s %s" % (mark, name, status))
    say("")
    print_links(kibana_url, job_ids)
    say("")
    say("What was started for you")
    say("Problems hidden in the past data (times are UTC):")
    for line in _planted(anomalies) or ["  (not available)"]:
        say(line)
    say("Problems developing now, in real time:")
    lines = _live_lines(live)
    if lines:
        for line in lines:
            say(line)
    else:
        say("  none were started this time. Start them any time with: python setup.py inject cascade "
            "(or info-flood, delayed).")
    say("")
    if not prebuilt_module_job:
        say("Exercise 2 asks you to create the APM transaction-metrics job yourself with the wizard, job prefix 'mlws-'.")
    say("Next: open docs/guide.md and start with Exercise 1.")
    if tail_end:
        say("The sample data ends %s UTC; after that the low-count detectors flag empty time buckets, "
            "so finish before then and run `python setup.py teardown` when you are done." % _fmt_tail(tail_end))
    else:
        say("When you are finished, run `python setup.py teardown`.")
    say("Check everything any time with: python setup.py verify")
