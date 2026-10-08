"""Fixed names and the allowlist predicate.

Every destructive call in the CLI asserts its target with :func:`assert_allowed`.
This module is deliberately independent of ``data/`` so teardown works even
when the generator is unavailable.
"""

from typing import Tuple

DATA_STREAMS: Tuple[str, ...] = (
    "traces-apm-mlws",
    "logs-apm.error-mlws",
    "metrics-apm.transaction.1m-mlws",
    "metrics-apm.service_transaction.1m-mlws",
    "metrics-apm.service_summary.1m-mlws",
    "logs-mlws.app-default",
)
META_INDEX = "mlws-meta"
ALERTS_INDEX = "mlws-alerts-history"
INDICES: Tuple[str, ...] = (META_INDEX, ALERTS_INDEX)
TEMPLATE = "mlws-app-logs"
PIPELINE = "mlws-app-logs"
JOB_IDS: Tuple[str, ...] = (
    "mlws-apm-multimetric",
    "mlws-logs-errors-correct",
    "mlws-logs-errors-level-filtered",
    "mlws-logs-categories",
    "mlws-logs-store-volume",
    "mlws-logs-inventory-health",
    "mlws-terminal-population",
    "mlws-apm_tx_metrics",
)
CONNECTOR_ID = "mlws-alerts"
RULE_IDS: Tuple[str, ...] = (
    "mlws-ml-native-record",
    "mlws-ml-per-partition",
    "mlws-ml-per-partition-15m",
    "mlws-ml-jobs-health",
)
DATA_VIEW_IDS: Tuple[str, ...] = (
    "mlws-dv-app-logs",
    "mlws-dv-apm",
    "mlws-dv-ml-anomalies",
    "mlws-dv-alerts",
)
DASHBOARD_ID = "mlws-home"
LIFECYCLE_STREAMS: Tuple[str, ...] = ("traces-apm-mlws", "logs-apm.error-mlws")

_FORBIDDEN_CHARS = set("*,/\\? \t\n#%")


class AllowlistError(RuntimeError):
    """Raised when a name outside the mlws allowlist would be touched."""


def datafeed_id(job_id: str) -> str:
    """Datafeed id for a job id (``datafeed-<job_id>``)."""
    return "datafeed-" + job_id


def is_allowed(name: str) -> bool:
    """True if ``name`` may be created/modified/deleted by this tool.

    Rule: starts with ``mlws-`` or ``datafeed-mlws-``, or equals a data
    stream name. Additionally: wildcards, commas and path
    characters are rejected so a single name can never expand to many targets.
    """
    if not isinstance(name, str) or not name:
        return False
    if any(c in _FORBIDDEN_CHARS for c in name) or ".." in name:
        return False
    return (
        name.startswith("mlws-")
        or name.startswith("datafeed-mlws-")
        or name in DATA_STREAMS
    )


def assert_allowed(name: str) -> str:
    """Return ``name`` if allowed, else raise :class:`AllowlistError`."""
    if not is_allowed(name):
        raise AllowlistError("refusing to touch %r: not in the mlws allowlist" % (name,))
    return name
