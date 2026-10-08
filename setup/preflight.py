"""Preflight checks (exit code 2 on failure, with actionable messages)."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .client import ApiError, EsClient, KbClient
from .logutil import say
from .names import ALERTS_INDEX, META_INDEX

REQUIRED_CLUSTER = ["monitor", "manage_ml", "manage_ingest_pipelines", "manage_index_templates"]
STREAM_PRIVS = ["auto_configure", "create_doc", "create_index", "delete", "manage", "read", "view_index_metadata"]
INDEX_PATTERNS = [
    "traces-apm-mlws", "logs-apm.error-mlws", "metrics-apm.*-mlws", "logs-mlws.app-default",
    META_INDEX, ALERTS_INDEX,
]
FIX_KEY = (
    "Fix: in Kibana open Management > API keys, create a new key with no restrictions (or one that "
    "allows cluster permissions %s and index permissions %s on the mlws-* and *-mlws data), "
    "paste the new key and run the command again." % (", ".join(REQUIRED_CLUSTER), ", ".join(STREAM_PRIVS))
)


@dataclass
class Report:
    """Collected preflight results."""

    items: List[Dict[str, str]] = field(default_factory=list)
    email_connector_id: Optional[str] = None
    ml_nodes: Optional[int] = None

    def add(self, level: str, name: str, msg: str) -> None:
        self.items.append({"level": level, "name": name, "msg": msg})
        say("[%-4s] %s: %s" % (level, name, msg))

    @property
    def failed(self) -> bool:
        return any(i["level"] == "FAIL" for i in self.items)


def privilege_request() -> Dict[str, Any]:
    """Body for ``_security/user/_has_privileges`` derived from the allowlist."""
    return {
        "cluster": REQUIRED_CLUSTER,
        "index": [
            {"names": INDEX_PATTERNS, "privileges": STREAM_PRIVS},
            {"names": [".ml-anomalies-*"], "privileges": ["read", "view_index_metadata"]},
        ],
    }


def missing_privileges(resp: Dict[str, Any]) -> List[str]:
    """Flatten a has_privileges response into a list of missing privilege strings."""
    missing = [("cluster:" + p) for p, ok in (resp.get("cluster") or {}).items() if not ok]
    for idx, privs in (resp.get("index") or {}).items():
        missing += ["%s on %s" % (p, idx) for p, ok in privs.items() if not ok]
    return missing


def check_es(es: EsClient, rep: Report, allow_non_serverless: bool) -> None:
    """Reach Elasticsearch and require the serverless build flavor."""
    try:
        _, info = es.get("/")
    except ApiError as exc:
        rep.add("FAIL", "elasticsearch", "%s. Fix: check that the Kibana URL you pasted is the one for your project, and that the API key is the full encoded key, then run the command again." % exc)
        return
    ver = info.get("version", {}) if isinstance(info, dict) else {}
    if ver.get("build_flavor") == "serverless":
        rep.add("OK", "elasticsearch", "reachable, Serverless project")
    elif allow_non_serverless:
        rep.add("WARN", "elasticsearch", "not serverless: supported but untested (continuing: --allow-non-serverless)")
    else:
        rep.add("FAIL", "elasticsearch", "this is not an Elastic Cloud Serverless project. Fix: create a "
                "free Serverless project of type Observability and use its URL, or add --allow-non-serverless to try anyway.")


def check_kibana(kb: KbClient, rep: Report) -> None:
    """Reach Kibana and check it is an Observability project."""
    try:
        _, st = kb.get("/api/status")
    except ApiError as exc:
        rep.add("FAIL", "kibana", "%s. Fix: check that the Kibana URL you pasted is the one for your project (it contains .kb.), and that the API key is correct." % exc)
        return
    plugins = {}
    if isinstance(st, dict):
        plugins = (st.get("status") or {}).get("plugins") or st.get("plugins") or {}
    if not plugins:
        rep.add("WARN", "kibana", "reachable, but /api/status lists no plugins; cannot confirm project type")
        return
    names = set(plugins)
    if "apm" in names and ("serverlessObservability" in names or "observability" in names):
        rep.add("OK", "kibana", "reachable, Observability project")
    else:
        rep.add("FAIL", "kibana", "this does not look like an Observability project. "
                "Fix: create a new Serverless project of type Observability and use its Kibana URL.")


def check_ml(es: EsClient, rep: Report) -> None:
    """Check ML is available."""
    try:
        es.get("/_ml/anomaly_detectors/_stats")
        rep.add("OK", "ml", "anomaly detection available")
    except ApiError as exc:
        rep.add("FAIL", "ml", "%s. Fix: machine learning is not available to this API key or project; create a new key with no restrictions, or use an Observability Serverless project." % exc)


def check_privileges(es: EsClient, rep: Report) -> None:
    """Verify the key holds the privileges the tool needs."""
    try:
        _, resp = es.post("/_security/user/_has_privileges", privilege_request())
    except ApiError as exc:
        rep.add("WARN", "privileges", "could not verify (%s); continuing" % exc)
        return
    miss = missing_privileges(resp)
    if miss:
        rep.add("FAIL", "privileges", "missing: %s. %s" % ("; ".join(miss[:12]), FIX_KEY))
    else:
        rep.add("OK", "privileges", "required cluster/index privileges present")


def check_connector_types(kb: KbClient, rep: Report) -> None:
    """Check the index connector type is enabled."""
    try:
        _, types = kb.get("/api/actions/connector_types")
    except ApiError as exc:
        rep.add("WARN", "connectors", "could not list connector types (%s)" % exc)
        return
    enabled = {t.get("id") for t in types if isinstance(t, dict) and t.get("enabled", True)}
    if ".index" in enabled:
        rep.add("OK", "connectors", "index connector type enabled")
    else:
        rep.add("FAIL", "connectors", "the connector type needed to record alerts is switched off in this project, so alert rules cannot be created. Fix: use a standard Observability Serverless project.")


def check_ml_nodes(kb: KbClient, rep: Report) -> None:
    """Warn about ML cold start when no ML node is running."""
    try:
        # INTERNAL API: no public equivalent on Serverless 9.6 (ML node count for cold-start warning)
        _, resp = kb.get("/internal/ml/ml_node_count")
    except ApiError as exc:
        rep.add("INFO", "ml-nodes", "node count unavailable (%s); skipping cold-start check" % exc.status)
        return
    count = resp.get("count") if isinstance(resp, dict) else None
    rep.ml_nodes = count
    if count == 0:
        rep.add("WARN", "ml-nodes", "no ML node running yet: Serverless starts one on demand, so the "
                "first job open can take a few minutes (cold start)")
    else:
        rep.add("OK", "ml-nodes", "%s ML node(s) available" % count)


def find_email_connector(kb: KbClient, rep: Report, email: str) -> None:
    """When ALERT_EMAIL is set, locate a preconfigured .email connector."""
    try:
        _, conns = kb.get("/api/actions/connectors")
    except ApiError as exc:
        rep.add("WARN", "email", "could not list connectors (%s); email actions will be skipped" % exc)
        return
    pre = [c for c in conns if c.get("connector_type_id") == ".email" and c.get("is_preconfigured")]
    if pre:
        rep.email_connector_id = pre[0].get("id")
        rep.add("OK", "email", "using preconfigured email connector '%s'" % pre[0].get("name"))
    else:
        rep.add("WARN", "email", "no preconfigured .email connector found; rules will be created "
                "without an email action (index connector only)")


def run_preflight(es: EsClient, kb: KbClient, alert_email: Optional[str] = None,
                  allow_non_serverless: bool = False) -> Report:
    """Run every check; the caller exits with code 2 if ``report.failed``."""
    say("Preflight checks")
    rep = Report()
    check_es(es, rep, allow_non_serverless)
    check_kibana(kb, rep)
    if rep.failed:
        return rep
    check_ml(es, rep)
    check_privileges(es, rep)
    check_connector_types(kb, rep)
    check_ml_nodes(kb, rep)
    if alert_email:
        find_email_connector(kb, rep, alert_email)
    return rep
