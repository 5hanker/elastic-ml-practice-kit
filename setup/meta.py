"""The ``mlws-meta`` setup marker (doc id ``setup``)."""

import dataclasses
from datetime import datetime
from typing import Any, Dict, Optional

from . import names
from .client import ApiError, EsClient
from .timeutil import iso

DOC_PATH = "/%s/_doc/setup" % names.META_INDEX


def jsonable(obj: Any) -> Any:
    """Recursively convert dataclasses/datetimes into JSON-safe values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        obj = dataclasses.asdict(obj)
    if isinstance(obj, datetime):
        return iso(obj)
    if isinstance(obj, dict):
        return {k: jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj


def read_meta(es: EsClient) -> Optional[Dict[str, Any]]:
    """Return the meta document, or None if the index/doc does not exist."""
    status, resp = es.get(DOC_PATH, allow=(404,))
    if status == 404 or not isinstance(resp, dict) or not resp.get("found", True):
        return None
    return resp.get("_source")


def write_meta(es: EsClient, meta: Dict[str, Any]) -> None:
    """Create/overwrite the meta document (visible immediately)."""
    names.assert_allowed(names.META_INDEX)
    es.put(DOC_PATH, jsonable(meta), params={"refresh": "true"})


def delete_meta(es: EsClient) -> None:
    """Delete the meta index (404 ignored)."""
    es.delete("/" + names.assert_allowed(names.META_INDEX), allow=(404,))


def is_loaded(meta: Optional[Dict[str, Any]]) -> bool:
    """True when a previous setup completed the data load."""
    return bool(meta and meta.get("loaded"))


__all__ = ["read_meta", "write_meta", "delete_meta", "is_loaded", "jsonable", "ApiError"]
