"""Connector and rules: create, else update, by fixed id."""

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import names
from .client import ApiError, KbClient
from .logutil import say, warn

UPDATABLE_RULE_FIELDS = ("name", "tags", "schedule", "params", "actions", "notify_when", "throttle")


def _read(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_connector(kb: KbClient, root: Path) -> None:
    """POST the index connector; if it already exists, PUT name+config."""
    spec = _read(root / "alerts" / "connector-mlws-alerts.json")
    cid = names.assert_allowed(spec["id"])
    body = {k: spec[k] for k in ("name", "connector_type_id", "config") if k in spec}
    path = "/api/actions/connector/" + cid
    try:
        kb.post(path, body)
        say("  connector %s: created" % cid)
    except ApiError as exc:
        if exc.status == 409 or (exc.status == 400 and "exist" in exc.message.lower()):
            kb.put(path, {"name": body["name"], "config": body["config"]})
            say("  connector %s: updated" % cid)
        else:
            raise


def render_email_action(template: Dict[str, Any], email: str, connector_id: str) -> Dict[str, Any]:
    """Substitute ``{{ALERT_EMAIL}}`` (and ``{{EMAIL_CONNECTOR_ID}}``) in the template.

    Other mustache tokens (``{{context.*}}``, ``{{rule.*}}``) are left alone.
    """
    text = json.dumps(template)
    text = text.replace("{{ALERT_EMAIL}}", json.dumps(email)[1:-1])
    text = text.replace("{{EMAIL_CONNECTOR_ID}}", json.dumps(connector_id)[1:-1])
    action = json.loads(text)
    action.setdefault("id", connector_id)
    return action


def build_rule_body(rule: Dict[str, Any], email: Optional[str], email_connector_id: Optional[str]) -> Dict[str, Any]:
    """Rule body from a rule file, adding the optional email action."""
    body = copy.deepcopy(rule["body"])
    tpl = rule.get("email_action")
    if tpl and email and email_connector_id:
        body.setdefault("actions", []).append(render_email_action(tpl, email, email_connector_id))
    return body


def ensure_rules(kb: KbClient, root: Path, email: Optional[str], email_connector_id: Optional[str]) -> None:
    """POST each rule; on 409 PUT the updatable fields."""
    if email and not email_connector_id:
        say("  note: ALERT_EMAIL set but no preconfigured .email connector; email actions skipped")
    for path in sorted((root / "alerts").glob("rule-*.json")):
        rule = _read(path)
        rid = names.assert_allowed(rule["id"])
        body = build_rule_body(rule, email, email_connector_id)
        url = "/api/alerting/rule/" + rid
        try:
            kb.post(url, body)
            say("  rule %s: created" % rid)
        except ApiError as exc:
            if exc.status != 409:
                raise
            upd = {k: body[k] for k in UPDATABLE_RULE_FIELDS if k in body}
            kb.put(url, upd)
            try:  # a PUT update can leave a rule disabled; re-enable (ignore "already enabled")
                kb.post(url + "/_enable", allow=(400, 404, 409))
            except ApiError:
                pass
            say("  rule %s: updated" % rid)


def ensure_alerts(kb: KbClient, root: Path, email: Optional[str], email_connector_id: Optional[str]) -> None:
    """Connector first (rules reference it), then rules."""
    say("Alerting")
    ensure_connector(kb, root)
    ensure_rules(kb, root, email, email_connector_id)
    if not email:
        say("  (no ALERT_EMAIL: rules write to the mlws-alerts-history index only)")


__all__ = ["ensure_alerts", "ensure_connector", "ensure_rules", "render_email_action", "build_rule_body", "warn"]
