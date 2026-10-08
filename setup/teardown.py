"""Remove every mlws artefact (and nothing else).

Every delete goes through :func:`_es_delete` / :func:`_kb_delete`, which assert
the target name against the allowlist first.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import jobs as jobmod
from . import names
from .client import ApiError, EsClient, KbClient
from .logutil import say, warn
from .meta import delete_meta


def _es_delete(es: EsClient, name: str, path: str, params: Optional[Dict[str, Any]] = None) -> None:
    names.assert_allowed(name)
    es.delete(path, params=params, allow=(404,), timeout=120)


def _kb_delete(kb: KbClient, name: str, path: str, internal: bool = False) -> None:
    names.assert_allowed(name)
    kb.delete(path, allow=(404,), internal=internal)


def all_job_ids(root: Path) -> List[str]:
    """Hardcoded ids plus any allowlisted ids found in jobs/*.json."""
    ids = list(names.JOB_IDS)
    try:
        for spec in jobmod.load_specs(root, include_all=True):
            if spec.job_id not in ids:
                ids.append(spec.job_id)
    except Exception as exc:  # assets missing or invalid: fall back to the fixed list
        warn("could not read jobs/*.json (%s); using the built-in job list" % exc)
    return ids


def saved_object_targets(root: Path) -> List[Tuple[str, str]]:
    """(type, id) pairs from kibana/saved-objects.ndjson, allowlisted ids only."""
    out: List[Tuple[str, str]] = [("dashboard", names.DASHBOARD_ID)]
    path = root / "kibana" / "saved-objects.ndjson"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            typ, oid = obj.get("type"), obj.get("id")
            if typ and oid and typ != "index-pattern" and names.is_allowed(oid) and (typ, oid) not in out:
                out.append((typ, oid))
    return out


def find_search_ids(kb: KbClient) -> List[str]:
    """Saved searches whose id starts with mlws- (via _find, paginated; internal header)."""
    found: List[str] = []
    page = 1
    while page <= 100:
        try:
            _, resp = kb.get("/api/saved_objects/_find",
                             params={"type": "search", "per_page": 100, "page": page})
        except ApiError:
            break
        objs = resp.get("saved_objects", [])
        found.extend(o["id"] for o in objs if names.is_allowed(o.get("id", "")))
        if not objs or page * 100 >= int(resp.get("total", 0) or 0):
            break
        page += 1
    return found


def plan_lines(root: Path) -> List[str]:
    """Human-readable list of what teardown removes."""
    return [
        "ML datafeeds and jobs: " + ", ".join(all_job_ids(root)),
        "Rules: " + ", ".join(names.RULE_IDS) + "; connector: " + names.CONNECTOR_ID,
        "Kibana: data views " + ", ".join(names.DATA_VIEW_IDS) + "; dashboard " + names.DASHBOARD_ID + "; mlws-search-* searches",
        "Data streams: " + ", ".join(names.DATA_STREAMS),
        "Indices: " + ", ".join(names.INDICES) + "; index template and ingest pipeline: " + names.TEMPLATE,
    ]


def remove_ml(es: EsClient, root: Path) -> None:
    """Stop datafeeds, close jobs, delete datafeeds then jobs (404 ignored)."""
    ids = all_job_ids(root)
    for jid in ids:
        did = names.datafeed_id(jid)
        _es_delete_safe_post(es, did, "/_ml/datafeeds/%s/_stop" % did, {"force": True})
    for jid in ids:
        _es_delete_safe_post(es, jid, "/_ml/anomaly_detectors/%s/_close" % jid, None, {"force": "true"})
    for jid in ids:
        did = names.datafeed_id(jid)
        _es_delete(es, did, "/_ml/datafeeds/" + did, {"force": "true"})
    for jid in ids:
        _es_delete(es, jid, "/_ml/anomaly_detectors/" + jid,
                   {"force": "true", "delete_user_annotations": "true", "wait_for_completion": "true"})
    say("  ML datafeeds and jobs removed")


def _es_delete_safe_post(es: EsClient, name: str, path: str, body: Any, params: Optional[Dict[str, Any]] = None) -> None:
    names.assert_allowed(name)
    try:
        es.post(path, body, params=params, allow=(404,), timeout=120)
    except ApiError as exc:
        if exc.status != 409:
            raise


def remove_alerts(kb: KbClient) -> None:
    """Delete rules then the connector (404 ignored)."""
    for rid in names.RULE_IDS:
        _kb_delete(kb, rid, "/api/alerting/rule/" + rid)
    _kb_delete(kb, names.CONNECTOR_ID, "/api/actions/connector/" + names.CONNECTOR_ID)
    say("  rules and connector removed")


def remove_kibana_objects(kb: KbClient, root: Path) -> None:
    """Delete data views, dashboard and searches."""
    for dv in names.DATA_VIEW_IDS:
        _kb_delete(kb, dv, "/api/data_views/data_view/" + dv)
    targets = saved_object_targets(root)
    for sid in find_search_ids(kb):
        if ("search", sid) not in targets:
            targets.append(("search", sid))
    for typ, oid in targets:
        path = "/api/saved_objects/%s/%s" % (typ, oid)
        try:
            _kb_delete(kb, oid, path)
        except ApiError as exc:
            if exc.status == 400 and "not available" in exc.message.lower():
                # INTERNAL API: no public equivalent on Serverless 9.6 (saved object delete is hidden on public route)
                say("  note: using internal-origin header to delete %s %s (INTERNAL)" % (typ, oid))
                _kb_delete(kb, oid, path, internal=True)
            else:
                raise
    say("  Kibana saved objects removed")


def remove_data(es: EsClient) -> None:
    """Delete meta marker, data streams, indices, template and pipeline (in a safe order)."""
    delete_meta(es)
    remove_streams(es)
    for idx in names.INDICES:
        _es_delete(es, idx, "/" + idx)
    _es_delete(es, names.TEMPLATE, "/_index_template/" + names.TEMPLATE)
    _es_delete(es, names.PIPELINE, "/_ingest/pipeline/" + names.PIPELINE)
    say("  data streams, indices, template and pipeline removed")


def remove_streams(es: EsClient) -> None:
    """Delete the six allowlisted data streams by exact name."""
    for stream in names.DATA_STREAMS:
        _es_delete(es, stream, "/_data_stream/" + stream)


def confirm(root: Path, assume_yes: bool, stdin_isatty: bool) -> bool:
    """Show what will be deleted and ask for confirmation unless --yes."""
    say("Teardown will remove ONLY these mlws artefacts:")
    for line in plan_lines(root):
        say("  - " + line)
    if assume_yes:
        return True
    if not stdin_isatty:
        warn("not an interactive terminal; pass --yes to confirm")
        return False
    return input("Type 'yes' to delete them: ").strip().lower() == "yes"


def run_teardown(es: EsClient, kb: KbClient, root: Path, assume_yes: bool = False,
                 stdin_isatty: bool = False) -> int:
    """Full teardown; returns an exit code."""
    if not confirm(root, assume_yes, stdin_isatty):
        say("Aborted; nothing was deleted.")
        return 1
    say("Tearing down")
    remove_alerts(kb)
    remove_ml(es, root)
    remove_kibana_objects(kb, root)
    remove_data(es)
    say("Teardown complete.")
    return 0
