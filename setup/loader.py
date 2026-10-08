"""Parallel data loader.

The main process lists 1-hour chunks (``data.generate.chunks``); worker
processes each call ``generate_chunk`` and post ``_bulk`` themselves, so the
only thing sent to a worker is a small picklable dict (endpoint + key for its
own client). Batches are ~5 MB / 5,000 docs, gzip-compressed.
"""

import hashlib
import json
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import names
from .client import ApiError, EsClient
from .logutil import say, warn
from .timeutil import humanize, iso

MAX_BATCH_DOCS = 5000
MAX_BATCH_BYTES = 5 * 1024 * 1024
MAX_429_ROUNDS = 6
PROGRESS_SECONDS = 5.0


@dataclass
class BulkStats:
    """Counters for bulk indexing."""

    docs: int = 0
    errors: int = 0
    retried: int = 0
    duplicates: int = 0
    samples: List[str] = field(default_factory=list)

    def note(self, reason: str) -> None:
        if len(self.samples) < 5 and reason not in self.samples:
            self.samples.append(reason)

    def merge(self, other: Dict[str, Any]) -> None:
        self.docs += other.get("docs", 0)
        self.errors += other.get("errors", 0)
        self.retried += other.get("retried", 0)
        self.duplicates += other.get("duplicates", 0)
        for s in other.get("samples", []):
            self.note(s)

    def to_dict(self) -> Dict[str, Any]:
        return {"docs": self.docs, "errors": self.errors, "retried": self.retried, "duplicates": self.duplicates, "samples": self.samples}


def _default(obj: Any) -> str:
    if isinstance(obj, datetime):
        return iso(obj)
    raise TypeError("not JSON serializable: %r" % type(obj))


def _doc_key(doc: Dict[str, Any]) -> Optional[str]:
    """A doc-unique key the generator already emits (APM ids), if any."""
    event = (doc.get("processor") or {}).get("event")
    if event == "span":
        return "span:" + str((doc.get("span") or {}).get("id", "")) or None
    if event == "error":
        return "error:" + str((doc.get("error") or {}).get("id", "")) or None
    if event == "transaction":
        return "tx:" + str((doc.get("transaction") or {}).get("id", "")) or None
    return None


def doc_id(stream: str, doc: Dict[str, Any], seen: Optional[Dict[str, int]] = None) -> str:
    """Deterministic ``_id``: sha1(stream, @timestamp, unique key).

    Key order: explicit ``__id`` (popped by the caller), APM transaction/span/
    error id, else a hash of the canonical doc body plus its occurrence count
    within ``seen`` (so byte-identical docs stay distinct). Generation is
    deterministic, so a retried batch yields the same ids (409 on replay).
    """
    key = doc.get("__id")
    if key is None:
        key = _doc_key(doc)
    if key is None or key.endswith(":"):
        body = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=_default)
        key = "h:" + hashlib.sha1(body.encode("utf-8")).hexdigest()
        if seen is not None:
            n = seen.get(key, 0)
            seen[key] = n + 1
            key += ":%d" % n
    raw = "|".join((stream, str(doc.get("@timestamp", "")), str(key)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def encode_doc(stream: str, doc: Dict[str, Any], seen: Optional[Dict[str, int]] = None) -> bytes:
    """One ``create`` action (with deterministic ``_id``) + source as NDJSON bytes."""
    doc_ident = doc_id(stream, doc, seen)
    if "__id" in doc:
        doc = {k: v for k, v in doc.items() if k != "__id"}
    action = {"create": {"_index": stream, "_id": doc_ident}}
    return (
        json.dumps(action, separators=(",", ":")).encode("utf-8") + b"\n"
        + json.dumps(doc, separators=(",", ":"), default=_default).encode("utf-8")
        + b"\n"
    )


def send_batch(es: EsClient, pairs: List[bytes], stats: BulkStats,
               sleep: Callable[[float], None] = time.sleep) -> None:
    """POST one batch; retry item-level 429s, count and sample other errors."""
    for rnd in range(MAX_429_ROUNDS):
        resp = es.bulk(b"".join(pairs))
        if not resp.get("errors"):
            stats.docs += len(pairs)
            return
        retry: List[bytes] = []
        for pair, item in zip(pairs, resp.get("items", [])):
            res = next(iter(item.values()))
            status = res.get("status", 200)
            if status < 300:
                stats.docs += 1
            elif status == 409:
                # deterministic _id already written (earlier attempt): success
                stats.duplicates += 1
            elif status == 429:
                retry.append(pair)
            else:
                stats.errors += 1
                err = res.get("error") or {}
                stats.note("%s: %s" % (err.get("type", status), str(err.get("reason", ""))[:160]))
        if not retry:
            return
        stats.retried += len(retry)
        pairs = retry
        sleep(min(1.0 * (2 ** rnd), 20.0))
    stats.errors += len(pairs)
    stats.note("429 too_many_requests persisted after %d retries" % MAX_429_ROUNDS)


def bulk_docs(es: EsClient, docs_by_stream: Dict[str, List[Dict[str, Any]]],
              sleep: Callable[[float], None] = time.sleep) -> BulkStats:
    """Bulk-index generated docs into their data streams (``create`` ops)."""
    stats = BulkStats()
    batch: List[bytes] = []
    size = 0
    for stream, docs in docs_by_stream.items():
        names.assert_allowed(stream)
        seen: Dict[str, int] = {}
        for doc in docs:
            line = encode_doc(stream, doc, seen)
            batch.append(line)
            size += len(line)
            if len(batch) >= MAX_BATCH_DOCS or size >= MAX_BATCH_BYTES:
                send_batch(es, batch, stats, sleep)
                batch, size = [], 0
    if batch:
        send_batch(es, batch, stats, sleep)
    return stats


def load_chunk(client_cfg: Dict[str, Any], plan_dict: Dict[str, Any], start_iso: str, end_iso: str) -> Dict[str, Any]:
    """Worker entry point (module level so it pickles under Windows spawn).

    Builds its own client from the picklable ``client_cfg``; the key is used
    only to authenticate and is never logged.
    """
    from data import generate  # imported here: workers import lazily

    es = EsClient(client_cfg["es_url"], client_cfg["api_key"])
    docs = generate.generate_chunk(plan_dict, start_iso, end_iso)
    return bulk_docs(es, docs).to_dict()


@dataclass
class LoadResult:
    """Outcome of a load run."""

    stats: BulkStats
    chunks_total: int
    chunks_failed: int
    seconds: float
    failures: List[str] = field(default_factory=list)


def _progress(done: int, total: int, docs: int, est_total: int, t_start: float) -> str:
    elapsed = max(time.time() - t_start, 0.001)
    rate = docs / elapsed
    if est_total > docs and rate > 0:
        eta = (est_total - docs) / rate
    elif done:
        eta = elapsed / done * (total - done)
    else:
        eta = 0
    pct = 100.0 * done / total if total else 100.0
    return "  loading: %d/%d chunks (%.0f%%), %s docs, %s docs/s, elapsed %s, ETA ~%s" % (
        done, total, pct, format(docs, ","), format(int(rate), ","),
        humanize_s(elapsed), humanize_s(eta),
    )


def humanize_s(seconds: float) -> str:
    from datetime import timedelta
    return humanize(timedelta(seconds=seconds))


def default_workers() -> int:
    """min(6, cpu_count)."""
    return max(1, min(6, os.cpu_count() or 2))


def load(
    client_cfg: Dict[str, Any],
    plan: Any,
    workers: Optional[int] = None,
    budget_minutes: float = 20.0,
    progress_seconds: float = PROGRESS_SECONDS,
) -> LoadResult:
    """Generate and bulk-load every chunk of ``plan``; returns counters."""
    from data import generate

    workers = workers or default_workers()
    chunk_list: Sequence[Tuple[datetime, datetime]] = generate.chunks(plan)
    total = len(chunk_list)
    try:
        est_total = sum(generate.estimate(plan).values())
    except Exception:
        est_total = 0
    plan_dict = plan.to_dict()
    say("Loading %d hourly chunks (~%s docs estimated) with %d worker(s)" % (total, format(est_total, ","), workers))
    stats = BulkStats()
    failures: List[str] = []
    done = 0
    t_start = time.time()
    last_print = t_start
    warned_budget = False

    def consume(result: Dict[str, Any]) -> None:
        stats.merge(result)

    def tick() -> None:
        nonlocal last_print, warned_budget
        now = time.time()
        if now - last_print >= progress_seconds:
            say(_progress(done, total, stats.docs, est_total, t_start))
            last_print = now
            elapsed = now - t_start
            if not warned_budget and done and elapsed > 30 and elapsed / done * total > budget_minutes * 60:
                warn("projected load time %s exceeds the %d-minute budget; "
                     "consider --scale or --days lower values" % (humanize_s(elapsed / done * total), budget_minutes))
                warned_budget = True

    jobs = [(iso(s), iso(e)) for s, e in chunk_list]
    if workers == 1:
        for s, e in jobs:
            try:
                consume(load_chunk(client_cfg, plan_dict, s, e))
            except Exception as exc:  # keep going; report at the end
                failures.append("%s: %s" % (s, exc))
            done += 1
            tick()
    else:
        ex = ProcessPoolExecutor(max_workers=workers)
        try:
            pending = {ex.submit(load_chunk, client_cfg, plan_dict, s, e): s for s, e in jobs}
            while pending:
                finished, _ = wait(list(pending), timeout=1.0, return_when=FIRST_COMPLETED)
                for fut in finished:
                    start = pending.pop(fut)
                    try:
                        consume(fut.result())
                    except Exception as exc:
                        failures.append("%s: %s" % (start, exc))
                    done += 1
                tick()
        except KeyboardInterrupt:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            ex.shutdown(wait=True)
    seconds = time.time() - t_start
    say(_progress(done, total, stats.docs, est_total, t_start).replace("loading:", "loaded:"))
    return LoadResult(stats, total, len(failures), seconds, failures)


def check_result(res: LoadResult, max_error_fraction: float = 0.005) -> bool:
    """Report errors; True if the load is acceptable."""
    ok = True
    if res.chunks_failed:
        ok = False
        warn("%d chunk(s) failed, e.g. %s" % (res.chunks_failed, "; ".join(res.failures[:3])))
    if res.stats.errors:
        frac = res.stats.errors / max(res.stats.docs + res.stats.errors, 1)
        say("  %d document error(s) (%.2f%%). First reasons:" % (res.stats.errors, frac * 100))
        for s in res.stats.samples[:5]:
            say("    - " + s)
        if frac > max_error_fraction:
            ok = False
    if res.stats.duplicates:
        say("  %d document(s) already present (duplicate-skipped)" % res.stats.duplicates)
    if res.stats.retried:
        say("  %d document(s) retried after 429" % res.stats.retried)
    return ok


__all__ = ["load", "load_chunk", "bulk_docs", "send_batch", "check_result", "BulkStats", "LoadResult",
           "default_workers", "ApiError"]
