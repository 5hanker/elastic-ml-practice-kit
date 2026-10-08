"""APM data: transactions, spans, errors and per-minute metric aggregation (3 metric streams)."""
import bisect
import math

from . import anomalies as _an
from . import messages as _m
from . import streams as S
from ._util import poisson, minute_prefix, minute_iso, site_cum, pick_site
from .entities import SERVICES, SERVICE_BY_NAME, EDGES, SITES
from .seasonality import global_factor, tz_offset

TPM_K = 0.88           # converts the nominal "peak tpm" column to the modelled mean (calibrated to ~2.0M tx)
SIGMA = 0.35
SPAN_SAMPLE = 0.10
ERROR_SAMPLE = 0.30
HIST_K = 20.0          # log buckets, ~5% wide
SPANS_DAYS = 3

_log = math.log
_exp = math.exp

DS_TRACES = {"type": "traces", "dataset": "apm", "namespace": "mlws"}
DS_ERRORS = {"type": "logs", "dataset": "apm.error", "namespace": "mlws"}
DS_MT = {"type": "metrics", "dataset": "apm.transaction.1m", "namespace": "mlws"}
DS_MST = {"type": "metrics", "dataset": "apm.service_transaction.1m", "namespace": "mlws"}
DS_MSUM = {"type": "metrics", "dataset": "apm.service_summary.1m", "namespace": "mlws"}
P_TX = {"event": "transaction"}
P_SPAN = {"event": "span"}
P_ERR = {"event": "error"}
P_MET = {"event": "metric"}
HINTS = ["_doc_count"]
ES_HINTS = {"mapping": {"hints": HINTS}}
MS_TX = {"name": "transaction", "interval": "1m"}
MS_ST = {"name": "service_transaction", "interval": "1m"}
MS_SUM = {"name": "service_summary", "interval": "1m"}
FAIL_CODES = (500, 502, 503, 504)
DB_SPANS = {
    "cart-api": ["SELECT shop.carts", "INSERT shop.orders", "SELECT shop.catalog_items"],
    "payments-gateway": ["INSERT ledger.entries", "SELECT ledger.tenders", "UPDATE ledger.batches"],
    "orders-api": ["SELECT orders.orders", "INSERT orders.orders", "SELECT orders.catalog_cache"],
    "admin-portal-api": ["SELECT portal.sites", "UPDATE portal.price_lists", "SELECT portal.sales_rollup"],
    "inventory-sync": ["UPDATE inv.stock", "SELECT inv.skus", "INSERT inv.deltas"],
    "catalog-sync": ["SELECT catalog.versions", "UPDATE catalog.sync_state"],
}
EXTERNAL = {"payments-gateway": ("issuer-network.example:443", "POST issuer-network.example")}

# precompute per-service, per-transaction log-normal mu (microseconds)
_MU = []
for _s in SERVICES:
    _MU.append([_log(_s.p50_ms * 1000.0 * t[3]) for t in _s.txs])


def svc_lambda(svc, mep):
    """Expected transactions per minute (no noise, scale 1, no anomalies)."""
    if svc.name == "inventory-sync":
        return _inv_lambda(mep)[0]
    return svc.peak_tpm * TPM_K * global_factor(mep + 30)


def _inv_lambda(mep):
    """inventory-sync: batch every 5 minutes plus a heavier batch at 04:00 local (US/Eastern)."""
    lam = 0.0
    heavy = 1.0
    if (mep // 60) % 5 == 0:
        lam = 10.0
    lh = ((mep + tz_offset("US/Eastern", mep)) % 86400) / 3600.0
    if 4.0 <= lh < 4.17:
        lam += 40.0
        heavy = 2.0
    return lam, heavy


class _Gen(object):
    def __init__(self, rng, scale, act, only, spans_on, site_cum_, silent_sites):
        self.rng = rng
        self.scale = scale
        self.act = act
        self.only = set(only) if only is not None else None
        self.spans_on = spans_on
        self.cum, self.total = site_cum_
        self.silent_sites = silent_sites
        self.silent = set()
        self.tr = []
        self.er = []
        self.acc = {}
        self.minfo = {}
        self.mi = 0
        self.pre = ""
        self.mep = 0

    # ---- per-minute info for each service: (latency multipliers per tx, fail p, force_errors)
    def prep_minute(self, mid):
        act = self.act
        rng = self.rng
        self.minfo = {}
        for s in SERVICES:
            if not act:
                self.minfo[s.idx] = ([1.0] * len(s.txs), s.base_fail, False, 1.0)
                continue
            e = _an.effect(act, mid, service=s.name)
            lat = [_an.effect(act, mid, service=s.name, transaction=t[0]).get("latency_x", 1.0) for t in s.txs]
            fp = max(s.base_fail, e.get("fail_rate", 0.0))
            self.minfo[s.idx] = (lat, fp, e.get("force_errors", False), e.get("count_x", 1.0))
        self.silent = set()
        for sid in self.silent_sites:
            if _an.effect(act, mid, site=sid).get("silent"):
                self.silent.add(sid)

    def tx(self, svc, ti, sec, sidx, trace_id, parent_id, heavy, depth):
        rng = self.rng
        rnd = rng.random
        td = svc.txs[ti]
        lat, fail_p, force, _ = self.minfo[svc.idx]
        bi = bisect.bisect(svc.build_cum, rnd())
        if bi > 2:
            bi = 2
        inst = svc.instances[bi][int(rnd() * len(svc.hosts))]
        dur = rng.lognormvariate(_MU[svc.idx][ti], SIGMA) * lat[ti] * heavy
        if bi == 2:
            dur *= 1.05
        failed = rnd() < fail_p
        site = SITES[sidx]
        tid = "%016x" % rng.getrandbits(64)
        if trace_id is None:
            trace_id = "%032x" % rng.getrandbits(128)
        ttype = td[1]
        mep = self.mep
        spans = None
        span_started = 0
        if self.spans_on and depth < 3 and rnd() < SPAN_SAMPLE:
            spans, dur = self._spans(svc, ti, td, inst, tid, trace_id, sec, sidx, dur, heavy, depth)
            span_started = len(spans)
        dur_us = int(dur) + 1
        if spans:
            for sp in spans:
                sp["trace"] = {"id": trace_id}
            self.tr.extend(spans)
        ts_us = mep * 1000000 + int(sec * 1000000)
        if ttype == "request":
            if failed:
                status = FAIL_CODES[int(rnd() * 4)]
                result = "HTTP 5xx"
            else:
                status = 201 if td[0].startswith("POST") else (204 if td[0].startswith("PUT") else 200)
                result = "HTTP 2xx"
        else:
            status = None
            result = "failure" if failed else "success"
        doc = {
            "@timestamp": "%s%09.6f000Z" % (self.pre, sec),
            "timestamp": {"us": ts_us},
            "processor": P_TX,
            "data_stream": DS_TRACES,
            "agent": svc.agent,
            "service": inst["service"],
            "host": inst["host"],
            "trace": {"id": trace_id},
            "transaction": {"id": tid, "name": td[0], "type": ttype, "duration": {"us": dur_us},
                            "result": result, "sampled": True, "representative_count": 1.0,
                            "span_count": {"started": span_started}},
            "span": {"id": tid},
            "event": {"outcome": "failure" if failed else "success",
                      "success_count": 0.0 if failed else 1.0},
            "labels": {"store_id": site["store_id"], "tenant_id": site["tenant_id"]},
        }
        if status is not None:
            doc["http"] = {"response": {"status_code": status}}
        if parent_id is not None:
            doc["parent"] = {"id": parent_id}
        self.tr.append(doc)
        # metric accumulation
        k = (self.mi, svc.idx, ti, failed)
        a = self.acc.get(k)
        if a is None:
            a = self.acc[k] = [0, 0, {}]
        a[0] += 1
        a[1] += dur_us
        b = int(_log(dur_us) * HIST_K)
        h = a[2]
        h[b] = h.get(b, 0) + 1
        if failed and (force or rnd() < ERROR_SAMPLE):
            self._error(svc, inst, td, tid, trace_id, parent_id, sec, status, force)
        return dur_us, failed

    def _spans(self, svc, ti, td, inst, tid, trace_id, sec, sidx, dur, heavy, depth):
        rng = self.rng
        rnd = rng.random
        mep = self.mep
        out = []
        base_us = mep * 1000000 + int(sec * 1000000)
        off = 0.0
        ndb = rng.randint(1, 4)
        for _ in range(ndb):
            nm = DB_SPANS[svc.name][int(rnd() * len(DB_SPANS[svc.name]))]
            d = int(rng.lognormvariate(_log(6000.0), 0.6)) + 200
            out.append(self._span_doc(inst, tid, sec + off / 1e6, base_us + int(off), d, nm, "db", "postgresql",
                                      "query", "postgresql", "postgresql", "mlws_app", "%016x" % rng.getrandbits(64), False))
            off += d + 50
        ext = EXTERNAL.get(svc.name)
        edge = EDGES.get((svc.name, td[0]))
        if edge is not None and (self.only is None or edge[0] in self.only):
            dsvc = SERVICE_BY_NAME[edge[0]]
            sid = "%016x" % rng.getrandbits(64)
            dsec = min(59.5, sec + off / 1e6 + 0.001)
            dd, dfail = self.tx(dsvc, edge[1], dsec, sidx, trace_id, sid, 1.0, depth + 1)
            sdur = dd + 2500
            method = dsvc.txs[edge[1]][0].split(" ")[0] if dsvc.txs[edge[1]][1] == "request" else "CALL"
            if method not in ("GET", "POST", "PUT"):
                method = "POST"
            res = "%s:8080" % dsvc.name
            out.append(self._span_doc(inst, tid, dsec - 0.001, base_us + int(off), sdur, "%s %s" % (method, dsvc.name),
                                      "external", "http", method, res, "http", res, sid, dfail))
            off += sdur
        elif ext is not None:
            d = int(rng.lognormvariate(_log(90000.0), 0.4)) + 1000
            sid = "%016x" % rng.getrandbits(64)
            out.append(self._span_doc(inst, tid, sec + off / 1e6, base_us + int(off), d, ext[1], "external", "http",
                                      "POST", ext[0], "http", ext[0], sid, False))
            off += d
        total = off + 1500
        if total > dur:
            dur = total
        return out, dur

    def _span_doc(self, inst, tid, sec, ts_us, dur_us, name, stype, subtype, action, resource, tgt_type, tgt_name,
                  sid, failed):
        sv = dict(inst["service"])
        sv["target"] = {"type": tgt_type, "name": tgt_name}
        return {
            "@timestamp": "%s%09.6f000Z" % (self.pre, min(sec, 59.999)),
            "timestamp": {"us": ts_us},
            "processor": P_SPAN,
            "data_stream": DS_TRACES,
            "agent": SERVICE_BY_NAME[sv["name"]].agent,
            "service": sv,
            "host": inst["host"],
            "parent": {"id": tid},
            "transaction": {"id": tid},
            "event": {"outcome": "failure" if failed else "success"},
            "span": {"id": sid, "name": name, "type": stype,
                     "subtype": subtype, "action": action, "duration": {"us": int(dur_us)},
                     "representative_count": 1.0,
                     "destination": {"service": {"resource": resource}}},
        }

    def _error(self, svc, inst, td, tid, trace_id, parent_id, sec, status, force):
        rng = self.rng
        cat = _m.APM_ERRORS[svc.name]
        if force and svc.name == "payments-gateway":
            etype, emsg, culprit, handled = _m.ISSUER_TIMEOUT_ERROR
            top = "%s: %s" % (etype, emsg)
        else:
            etype, emsg, culprit, handled = cat[int(rng.random() * len(cat))]
            top = emsg
        esec = sec + 0.0005
        if esec > 59.999:
            esec = 59.999
        doc = {
            "@timestamp": "%s%09.6f000Z" % (self.pre, esec),
            "timestamp": {"us": self.mep * 1000000 + int(esec * 1000000)},
            "processor": P_ERR,
            "data_stream": DS_ERRORS,
            "agent": svc.agent,
            "service": inst["service"],
            "host": inst["host"],
            "trace": {"id": trace_id},
            "parent": {"id": tid},
            "transaction": {"id": tid, "name": td[0], "type": td[1], "sampled": True},
            "error": {"id": "%032x" % rng.getrandbits(128),
                      "grouping_key": _m.grouping_key(etype, culprit),
                      "culprit": culprit,
                      "exception": [{"type": etype, "message": emsg, "handled": handled}]},
            "message": top,
            "event": {"dataset": "apm.error"},
        }
        if status is not None:
            doc["http"] = {"response": {"status_code": status}}
        self.er.append(doc)

    # ---- metrics ----
    def metrics(self, start_ep):
        mt, mst, msum = [], [], []
        by_st = {}
        by_sum = {}
        for (mi, si, ti, failed), (n, tot, h) in self.acc.items():
            svc = SERVICES[si]
            td = svc.txs[ti]
            ts = minute_iso(start_ep + 60 * mi)
            inst = svc.instances[0][0]
            vals, cnts = _hist_lists(h)
            mt.append({
                "@timestamp": ts,
                "_doc_count": n,
                "elasticsearch": ES_HINTS,
                "processor": P_MET,
                "metricset": MS_TX,
                "data_stream": DS_MT,
                "agent": {"name": svc.agent_name},
                "service": inst["service"],
                "host": inst["host"],
                "transaction": {"name": td[0], "type": td[1], "root": True,
                                "result": ("HTTP 5xx" if failed else "HTTP 2xx") if td[1] == "request"
                                else ("failure" if failed else "success"),
                                "duration": {"histogram": {"values": vals, "counts": cnts},
                                             "summary": {"sum": float(tot), "value_count": n}}},
                "event": {"outcome": "failure" if failed else "success",
                          "success_count": {"sum": 0.0 if failed else float(n), "value_count": n}},
            })
            k = (mi, si, td[1])
            b = by_st.get(k)
            if b is None:
                b = by_st[k] = [0, 0, 0, {}]
            b[0] += n
            b[1] += tot
            if not failed:
                b[2] += n
            bh = b[3]
            for bk, c in h.items():
                bh[bk] = bh.get(bk, 0) + c
            k2 = (mi, si)
            by_sum[k2] = by_sum.get(k2, 0) + n
        for (mi, si, ttype), (n, tot, succ, h) in by_st.items():
            svc = SERVICES[si]
            ts = minute_iso(start_ep + 60 * mi)
            vals, cnts = _hist_lists(h)
            svc_min = {"name": svc.name, "environment": "production", "language": {"name": svc.language}}
            mst.append({
                "@timestamp": ts,
                "_doc_count": n,
                "elasticsearch": ES_HINTS,
                "processor": P_MET,
                "metricset": MS_ST,
                "data_stream": DS_MST,
                "agent": {"name": svc.agent_name},
                "service": svc_min,
                "transaction": {"root": True, "type": ttype,
                                "duration": {"histogram": {"values": vals, "counts": cnts},
                                             "summary": {"sum": float(tot), "value_count": n}}},
                "event": {"success_count": {"sum": float(succ), "value_count": n}},
            })
        for (mi, si), n in by_sum.items():
            svc = SERVICES[si]
            msum.append({
                "@timestamp": minute_iso(start_ep + 60 * mi),
                "processor": P_MET,
                "metricset": MS_SUM,
                "data_stream": DS_MSUM,
                "agent": {"name": svc.agent_name},
                "service": {"name": svc.name, "environment": "production", "language": {"name": svc.language}},
                "service_summary": float(n),
            })
        return mt, mst, msum


def _hist_lists(h):
    keys = sorted(h)
    return [round(_exp((b + 0.5) / HIST_K), 3) for b in keys], [h[b] for b in keys]


def generate(rng, start_ep, end_ep, t0_ep, scale=1.0, anoms=None, only_services=None):
    """Returns {stream: [docs]} for the 5 APM streams over minutes in [start_ep, end_ep)."""
    act = _an.active(anoms, start_ep, end_ep) if anoms else []
    spans_on = start_ep >= t0_ep - SPANS_DAYS * 86400
    silent_sites = sorted({a.target["site"] for a in act if a.kind == "store_silent"})
    g = _Gen(rng, scale, act, only_services, spans_on, site_cum((start_ep + end_ep) / 2.0), silent_sites)
    rnd = rng.random
    svcs = [s for s in SERVICES if only_services is None or s.name in only_services]
    for mi, mep in enumerate(range(int(start_ep), int(end_ep), 60)):
        g.mi = mi
        g.mep = mep
        g.pre = minute_prefix(mep)
        mid = mep + 30
        g.prep_minute(mid)
        for svc in svcs:
            if svc.name == "inventory-sync":
                lam, heavy = _inv_lambda(mep)
                lam *= scale
            else:
                lam = svc.peak_tpm * TPM_K * global_factor(mid) * scale
                heavy = 1.0
            lam *= g.minfo[svc.idx][3]
            n = poisson(rng, lam * rng.uniform(0.85, 1.15)) if lam > 0 else 0
            cum = svc.tx_cum
            for _ in range(n):
                ti = bisect.bisect(cum, rnd())
                if ti >= len(cum):
                    ti = len(cum) - 1
                sidx = pick_site(rng, g.cum, g.total)
                if g.silent and SITES[sidx]["store_id"] in g.silent:
                    continue
                g.tx(svc, ti, rnd() * 59.0, sidx, None, None, heavy, 0)
    mt, mst, msum = g.metrics(start_ep)
    return {S.TRACES: g.tr, S.ERRORS: g.er, S.MT: mt, S.MST: mst, S.MSUM: msum}


def expected(start_ep, end_ep, t0_ep, scale=1.0, only_services=None):
    """Expected doc counts per APM stream, sampled every 5 minutes (no anomalies)."""
    tx = mt = mst = spans = err = 0.0
    svcs = [s for s in SERVICES if only_services is None or s.name in only_services]
    step = 300
    for mep in range(int(start_ep), int(end_ep), step):
        span = min(step, int(end_ep) - mep) / 60.0
        on = mep >= t0_ep - SPANS_DAYS * 86400
        for s in svcs:
            if s.name == "inventory-sync":
                # batches every 5 min: one active minute per step
                lam_b = 10.0 * scale
                lam_h, _ = 0.0, 1.0
                lam_min_avg = lam_b / 5.0
                ntx = lam_min_avg * span
                for ti, t in enumerate(s.txs):
                    w = t[2]
                    p = 1.0 - _exp(-lam_b * w)
                    mt += p * (span / 5.0)
                mst += 1.0 * (1.0 - _exp(-lam_b)) * (span / 5.0)
            else:
                lam = svc_lambda(s, mep + 120) * scale
                ntx = lam * span
                prev = 0.0
                for ti, t in enumerate(s.txs):
                    w = s.tx_cum[ti] - prev
                    prev = s.tx_cum[ti]
                    lf = lam * w * s.base_fail
                    ls = lam * w - lf
                    mt += ((1.0 - _exp(-ls)) + (1.0 - _exp(-lf))) * span
                mst += (1.0 - _exp(-lam)) * span
            tx += ntx
            err += ntx * s.base_fail * ERROR_SAMPLE
            if on:
                spans += ntx * SPAN_SAMPLE * SPANS_PER_SAMPLED
                tx += ntx * SPAN_SAMPLE * DOWNSTREAM_PER_SAMPLED
    return {S.TRACES: tx + spans, S.ERRORS: err, S.MT: mt, S.MST: mst, S.MSUM: mst}


SPANS_PER_SAMPLED = 3.3      # spans per sampled trace (db spans + exit span), calibrated
DOWNSTREAM_PER_SAMPLED = 0.45  # extra downstream transactions per sampled trace, calibrated
