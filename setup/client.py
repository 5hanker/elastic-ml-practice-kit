"""HTTP clients for Elasticsearch and Kibana (urllib only).

Rules:
- headers are never logged, the key is never logged;
- Kibana calls send ``kbn-xsrf: true``; ONLY ``/internal/*`` and
  ``/api/saved_objects/_find`` also get the internal-origin header;
- 429/502/503/504 and connection errors retry with exponential backoff and
  jitter (max 6 attempts);
- HTTP errors become :class:`ApiError` with status plus ES error type/reason.
"""

import gzip
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

from .logutil import get_logger

RETRY_STATUSES = (429, 502, 503, 504)
MAX_ATTEMPTS = 6
DEFAULT_TIMEOUT = 60.0
USER_AGENT = "mlws-setup/1.0"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects (they would leak the API key to another host)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


class ApiError(Exception):
    """An HTTP (or connection) failure with a readable message."""

    def __init__(self, status: int, message: str, method: str = "", path: str = "") -> None:
        self.status = status
        self.message = message
        self.method = method
        self.path = path
        super().__init__("%s %s -> HTTP %s: %s" % (method, path, status or "n/a", message))


def describe_error(body: Any, raw: str = "") -> str:
    """Extract a readable reason from an ES or Kibana error body."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            typ = err.get("type", "error")
            reason = err.get("reason", "")
            cause = err.get("root_cause")
            if isinstance(cause, list) and cause and isinstance(cause[0], dict):
                reason = reason or cause[0].get("reason", "")
            return "%s: %s" % (typ, reason)
        if isinstance(err, str):
            msg = body.get("message", "")
            return "%s: %s" % (err, msg) if msg else err
        if body.get("message"):
            return str(body["message"])
    return (raw or "").strip()[:300] or "no details"


def kibana_extra_headers(path: str, force_internal: bool = False) -> Dict[str, str]:
    """Headers a Kibana request needs beyond auth, based on its path.

    Internal headers are sent ONLY to ``/internal/*`` and
    ``/api/saved_objects/_find`` (or when ``force_internal`` is set for the
    documented delete fallback); public routes reject them with 400.
    """
    bare = path.split("?", 1)[0]
    headers = {"kbn-xsrf": "true"}
    if bare.startswith("/internal/"):
        headers["x-elastic-internal-origin"] = "Kibana"
        headers["elastic-api-version"] = "1"
    elif bare == "/api/saved_objects/_find" or force_internal:
        headers["x-elastic-internal-origin"] = "Kibana"
    return headers


class _Http:
    """Shared request loop. Subclasses supply ``_extra_headers``."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self.backoff_base = backoff_base
        self._sleep = sleep
        self.log = get_logger()

    def __repr__(self) -> str:
        return "%s(%r)" % (type(self).__name__, self.base_url)

    def _extra_headers(self, path: str, internal: bool) -> Dict[str, str]:
        return {}

    def _delay(self, attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 30.0)
            except ValueError:
                pass
        return min(self.backoff_base * (2 ** attempt), 30.0) * (0.5 + random.random() / 2)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        params: Optional[Dict[str, Any]] = None,
        allow: Iterable[int] = (),
        internal: bool = False,
        raw: Optional[bytes] = None,
        content_type: Optional[str] = None,
        gzip_body: bool = False,
        timeout: Optional[float] = None,
    ) -> Tuple[int, Any]:
        """Send a request; return ``(status, parsed_json_or_text)``.

        2xx and statuses in ``allow`` are returned; others raise ApiError.
        """
        url = self.base_url + path
        if params:
            url += ("&" if "?" in path else "?") + urllib.parse.urlencode(params)
        data: Optional[bytes] = raw
        ctype = content_type
        if data is None and body is not None:
            data = json.dumps(body).encode("utf-8")
            ctype = ctype or "application/json"
        headers = {
            "Authorization": "ApiKey " + self._api_key,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        headers.update(self._extra_headers(path, internal))
        if data is not None:
            headers["Content-Type"] = ctype or "application/json"
            if gzip_body:
                data = gzip.compress(data, 3)
                headers["Content-Encoding"] = "gzip"
        allow = tuple(allow)
        last: Optional[ApiError] = None
        for attempt in range(MAX_ATTEMPTS):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            retry_after = None
            try:
                with _OPENER.open(req, timeout=timeout or self.timeout) as resp:
                    status = resp.status
                    text = resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                status = exc.code
                if status in (301, 302, 303, 307, 308):
                    loc = exc.headers.get("Location", "") if exc.headers else ""
                    host = urllib.parse.urlparse(loc).netloc or loc or "another host"
                    exc.close()
                    raise ApiError(
                        status,
                        "unexpected redirect to %s - check ELASTIC_KIBANA_URL" % host,
                        method, path,
                    )
                text = exc.read().decode("utf-8", "replace") if exc.fp else ""
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
            except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last = ApiError(0, "connection failed: %s" % (reason,), method, path)
                self.log.debug("%s %s -> connection error (attempt %d)", method, path, attempt + 1)
                if attempt + 1 < MAX_ATTEMPTS:
                    self._sleep(self._delay(attempt, None))
                    continue
                raise last
            parsed: Any = text
            if text:
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = text
            else:
                parsed = {}
            self.log.debug("%s %s -> %s", method, path.split("?", 1)[0], status)
            if 200 <= status < 300 or status in allow:
                return status, parsed
            last = ApiError(status, describe_error(parsed, text), method, path)
            if status in RETRY_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                self._sleep(self._delay(attempt, retry_after))
                continue
            raise last
        raise last or ApiError(0, "request failed", method, path)

    def get(self, path: str, **kw: Any) -> Tuple[int, Any]:
        """GET helper."""
        return self.request("GET", path, **kw)

    def put(self, path: str, body: Any = None, **kw: Any) -> Tuple[int, Any]:
        """PUT helper."""
        return self.request("PUT", path, body, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Tuple[int, Any]:
        """POST helper."""
        return self.request("POST", path, body, **kw)

    def delete(self, path: str, **kw: Any) -> Tuple[int, Any]:
        """DELETE helper."""
        return self.request("DELETE", path, **kw)


class EsClient(_Http):
    """Elasticsearch client."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._gzip_ok = True

    def bulk(self, ndjson: bytes) -> Dict[str, Any]:
        """POST ``_bulk``; gzip the body, falling back to plain if rejected."""
        if self._gzip_ok:
            try:
                return self.request(
                    "POST", "/_bulk", raw=ndjson, content_type="application/x-ndjson",
                    gzip_body=True, timeout=120,
                )[1]
            except ApiError as exc:
                if exc.status not in (400, 411, 415):
                    raise
                self.log.debug("bulk with gzip rejected (%s); retrying uncompressed", exc.status)
                result = self.request(
                    "POST", "/_bulk", raw=ndjson, content_type="application/x-ndjson", timeout=120
                )[1]
                self._gzip_ok = False
                return result
        return self.request(
            "POST", "/_bulk", raw=ndjson, content_type="application/x-ndjson", timeout=120
        )[1]


class KbClient(_Http):
    """Kibana client (adds kbn-xsrf; internal headers only where allowed)."""

    def _extra_headers(self, path: str, internal: bool) -> Dict[str, str]:
        return kibana_extra_headers(path, internal)

    def post_multipart(self, path: str, body: bytes, content_type: str, **kw: Any) -> Tuple[int, Any]:
        """POST a pre-encoded multipart body."""
        return self.request("POST", path, raw=body, content_type=content_type, **kw)


def make_clients(cfg: Any) -> Tuple[EsClient, KbClient]:
    """Build both clients from a :class:`setup.config.Config`."""
    return EsClient(cfg.es_url, cfg.api_key), KbClient(cfg.kibana_url, cfg.api_key)
