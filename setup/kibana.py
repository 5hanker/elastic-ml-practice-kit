"""Kibana assets: data views, saved objects import (multipart) and deep links."""

import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from . import names
from .client import ApiError, KbClient
from .logutil import say


def encode_multipart(
    files: List[Tuple[str, str, bytes, str]],
    fields: Optional[Dict[str, str]] = None,
    boundary: Optional[str] = None,
) -> Tuple[bytes, str]:
    """Encode multipart/form-data (stdlib only).

    ``files`` items are ``(field_name, filename, content, content_type)``.
    Returns ``(body, content_type_header)``.
    """
    boundary = boundary or "----mlws" + uuid.uuid4().hex
    crlf = b"\r\n"
    out = b""
    for name, value in (fields or {}).items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n" % (boundary, name)).encode()
        out += value.encode("utf-8") + crlf
    for name, filename, content, ctype in files:
        out += (
            "--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
            "Content-Type: %s\r\n\r\n" % (boundary, name, filename, ctype)
        ).encode()
        out += content + crlf
    out += ("--%s--\r\n" % boundary).encode()
    return out, "multipart/form-data; boundary=%s" % boundary


def ensure_data_views(kb: KbClient, root: Path) -> None:
    """Create every data view in kibana/data-views.json (override: true)."""
    views = json.loads((root / "kibana" / "data-views.json").read_text(encoding="utf-8"))
    for dv in views:
        names.assert_allowed(dv["id"])
        kb.post("/api/data_views/data_view", {"data_view": dv, "override": True})
        say("  data view %s: ok" % dv["id"])


def import_saved_objects(kb: KbClient, root: Path) -> None:
    """Import kibana/saved-objects.ndjson (overwrite) via multipart."""
    content = (root / "kibana" / "saved-objects.ndjson").read_bytes()
    body, ctype = encode_multipart([("file", "saved-objects.ndjson", content, "application/ndjson")])
    _, resp = kb.post_multipart("/api/saved_objects/_import", body, ctype, params={"overwrite": "true"})
    if isinstance(resp, dict) and not resp.get("success", True):
        errs = resp.get("errors", [])[:5]
        raise ApiError(200, "saved objects import reported errors: %s" % json.dumps(errs)[:600],
                       "POST", "/api/saved_objects/_import")
    say("  saved objects imported: %s" % (resp.get("successCount", "?") if isinstance(resp, dict) else "?"))


def ensure_kibana(kb: KbClient, root: Path) -> None:
    """Data views then saved objects."""
    say("Kibana assets")
    ensure_data_views(kb, root)
    import_saved_objects(kb, root)


def deep_links(kibana_url: str, explorer_jobs: List[str], viewer_jobs: List[str]) -> List[Tuple[str, str]]:
    """Labelled deep links for the completion checklist."""
    base = kibana_url.rstrip("/")
    g = "_g=(ml:(jobIds:!(%s)),time:(from:now-30d,to:now))"
    links = [("ML jobs list", base + "/app/ml/jobs")]
    for j in explorer_jobs:
        links.append(("Anomaly Explorer: " + j, base + "/app/ml/explorer?" + g % j))
    for j in viewer_jobs:
        links.append(("Single Metric Viewer: " + j, base + "/app/ml/timeseriesexplorer?" + g % j))
    links += [
        ("APM services (production)", base + "/app/apm/services?environment=production&rangeFrom=now-7d&rangeTo=now"),
        ("Dashboard mlws-home", base + "/app/dashboards#/view/" + quote(names.DASHBOARD_ID)),
        ("Rules", base + "/app/observability/alerts/rules"),
        ("Discover", base + "/app/discover"),
    ]
    return links
