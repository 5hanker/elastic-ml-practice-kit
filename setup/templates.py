"""Ingest pipeline, index template and data stream lifecycle."""

import json
from pathlib import Path
from typing import Any, Dict

from . import names
from .client import ApiError, EsClient
from .logutil import say, warn


def _load(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError("missing asset file: %s" % path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_templates(es: EsClient, root: Path) -> None:
    """PUT the ingest pipeline first, then the index template (both idempotent)."""
    tdir = root / "templates"
    pipeline = _load(tdir / "mlws-app-logs.pipeline.json")
    template = _load(tdir / "mlws-app-logs.template.json")
    es.put("/_ingest/pipeline/" + names.assert_allowed(names.PIPELINE), pipeline)
    say("  pipeline %s: ok" % names.PIPELINE)
    es.put("/_index_template/" + names.assert_allowed(names.TEMPLATE), template)
    say("  index template %s: ok" % names.TEMPLATE)


def set_lifecycle(es: EsClient) -> None:
    """Set 45d retention on the APM trace and error data streams (after load)."""
    for stream in names.LIFECYCLE_STREAMS:
        try:
            es.put("/_data_stream/%s/_lifecycle" % names.assert_allowed(stream), {"data_retention": "45d"})
            say("  lifecycle %s: data_retention 45d" % stream)
        except ApiError as exc:
            warn("could not set lifecycle on %s: %s" % (stream, exc))
