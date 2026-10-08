"""Application logs (logs-mlws.app-default)."""
import math

from . import anomalies as _an
from . import messages as _m
from ._util import poisson, minute_prefix, site_cum, pick_site
from .entities import (APP_CODES, APP_CODE_ORDER, LOG_BUILDS, SITES, TERMINALS, SITE_BY_ID, SITE_IDX)
from .seasonality import global_factor, local_hour

LOG_BASE_PER_MIN = 66.0  # average logs per minute at scale 1.0 
ERROR_SHARE = 0.04       # baseline ERROR+CRITICAL+FATAL share
DATA_STREAM = {"type": "logs", "dataset": "mlws.app", "namespace": "default"}
_SVC = {}
for _c, _d in APP_CODES.items():
    for _s, _w in _d["services"]:
        _SVC[_s] = {"name": _s}
_ENV = "production"
INFO_ERRORISH_RATE = 0.003  # baseline INFO docs that carry an error_code (recovered/transient problems)
_INFO_CODES = {
    "XYZ": ("CK-RCV-01", "RecoveredAfterRetry"),
    "PQR": ("OR-RCV-01", "RecoveredAfterRetry"),
    "LMN": ("AP-RCV-01", "RecoveredAfterRetry"),
    "ABC": ("CS-RCV-01", "RecoveredAfterRetry"),
    "DEF": ("INV-RCV-01", "RecoveredAfterRetry"),
}
_PAY_ERRORS = (("PE-1029", "PAYMENT_DECLINED"), ("PE-1031", "INVALID_CARD"), ("PE-1042", "INSTRUMENT_DECLINED"),
               ("PE-1017", "PAYER_ACTION_REQUIRED"))
_ORDER_SOURCES = ("kiosk", "web", "mobile", "delivery-partner")
_ORDER_SOURCE_W = (0.25, 0.35, 0.25, 0.15)
_PARTNERS = ("DashFleet", "QuickCourier", "EatNow")
_log = math.log


def code_rate(code, mep):
    """Expected baseline docs per minute for an app code (no noise, scale 1)."""
    g = global_factor(mep + 30)
    r = LOG_BASE_PER_MIN * APP_CODES[code]["share"] * g
    if code == "DEF":
        r *= 0.55 + 0.45 * (g / 1.1)  # batch-driven: flatter than the front-of-store curves
    return r


def _level(rng):
    x = rng.random()
    if x < 0.90:
        lvl, cls = "INFO", "INFO"
    elif x < 0.96:
        lvl, cls = "WARN", "WARN"
    elif x < 0.995:
        lvl, cls = "ERROR", "ERROR"
    elif x < 0.998:
        lvl, cls = "CRITICAL", "ERROR"
    else:
        lvl, cls = "FATAL", "ERROR"
    return lvl, cls


def _case(rng, lvl):
    # only lowercase or UPPERCASE : ~10% lowercase, never Title-case
    if rng.random() < 0.10:
        return lvl.lower()
    return lvl


def _mk(rng, pre, mep, code, appname, svc, lvl, tpl, sidx, queue, sync_x, link_trace, med_sync):
    site = SITES[sidx]
    terms = TERMINALS[sidx]
    build = _build(rng)
    ms = int(rng.random() * 60000)
    s, r = divmod(ms, 1000)
    term = terms[int(rng.random() * len(terms))]
    doc = {
        "@timestamp": "%s%02d.%03dZ" % (pre, s, r),
        "level": lvl,
        "app_code": code,
        "app_name": appname,
        "app_env": _ENV,
        "app_region": "us-east-1" if rng.random() < 0.8 else "us-west-2",
        "app_build": build,
        "app_version": build,
        "device_id": "D-" + term[2:],
        "tenant_id": site["tenant_id"],
        "tenant_name": site["tenant_name"],
        "store_id": site["store_id"],
        "store_name": site["store_name"],
        "terminal_id": term,
        "store_code": site["store_code"],
        "service": _SVC[svc],
        "message": _m.render(tpl, rng, site["store_id"], term, site["tenant_name"]),
        "data_stream": DATA_STREAM,
    }
    if tpl.code:
        doc["error_code"] = tpl.code
        doc["error_name"] = tpl.name
    elif lvl.upper() == "INFO" and rng.random() < INFO_ERRORISH_RATE:
        doc["error_code"], doc["error_name"] = _INFO_CODES[code]
    if tpl.path:
        doc["request_path"] = tpl.path
        doc["http_method"] = tpl.method
        if tpl.rt:
            doc["response_time_ms"] = int(tpl.rt * rng.lognormvariate(0.0, 0.4)) + 1
        doc["args"] = {"status_code": tpl.status}
        if tpl.status and tpl.status >= 400:
            doc["http_error_status"] = tpl.status
            if code in ("XYZ", "PQR") and rng.random() < 0.4:
                pc, pn = _PAY_ERRORS[int(rng.random() * len(_PAY_ERRORS))]
                doc["payment_error_code"] = pc
                doc["payment_error_name"] = pn
    doc["processing_time_ms"] = (int(doc["response_time_ms"] * rng.uniform(0.6, 0.95)) + 1
                                 if "response_time_ms" in doc else int(rng.lognormvariate(3.2, 0.7)) + 1)
    if code == "PQR":
        x = rng.random()
        acc = 0.0
        src = _ORDER_SOURCES[-1]
        for name, w in zip(_ORDER_SOURCES, _ORDER_SOURCE_W):
            acc += w
            if x < acc:
                src = name
                break
        doc["order_source"] = src
        if src == "delivery-partner":
            doc["delivery_partner"] = _PARTNERS[int(rng.random() * len(_PARTNERS))]
    if link_trace:
        doc["trace"] = {"id": "%032x" % rng.getrandbits(128)}
    if med_sync:
        doc["sync_duration_ms"] = round(rng.lognormvariate(_log(med_sync), 0.35) * sync_x, 1)
    if code == "DEF":
        if queue is None:
            doc["db_queue_size"] = rng.randint(20, 80)
        else:
            doc["db_queue_size"] = int(queue * rng.uniform(0.96, 1.04))
        doc["orders_amount"] = round(rng.lognormvariate(_log(1200.0), 0.3), 2)
    if code in ("DEF", "ABC"):
        doc["pipeline_queue_size"] = (rng.randint(0, 40) if queue is None
                                                    else int(queue * rng.uniform(0.05, 0.15)))
    return doc


def _build(rng):
    x = rng.random()
    return LOG_BUILDS[0][0] if x < 0.5 else (LOG_BUILDS[1][0] if x < 0.9 else LOG_BUILDS[2][0])


def generate(rng, start_ep, end_ep, scale=1.0, anoms=None, only_codes=None):
    """Generate app logs for minutes in [start_ep, end_ep) (epoch seconds, minute aligned)."""
    docs = []
    act = _an.active(anoms, start_ep, end_ep) if anoms else []
    cum, total = site_cum((start_ep + end_ep) / 2.0)
    silent_sites = sorted({a.target["site"] for a in act if a.kind == "store_silent"})
    codes = [c for c in APP_CODE_ORDER if only_codes is None or c in only_codes]
    rnd = rng.random
    tpls = _m.LOG_TEMPLATES
    append = docs.append
    for mep in range(int(start_ep), int(end_ep), 60):
        mid = mep + 30
        pre = minute_prefix(mep)
        silent = set()
        for sid in silent_sites:
            if _an.effect(act, mid, site=sid).get("silent"):
                silent.add(sid)
        for code in codes:
            cd = APP_CODES[code]
            appname = cd["app_name"]
            lam_code = code_rate(code, mep) * scale
            eff = _an.effect(act, mid, app_code=code) if act else {}
            queue = eff.get("queue")
            sync_x = eff.get("sync_x", 1.0)
            med_sync = 3500.0 if code == "DEF" else (900.0 if code == "ABC" else 0.0)
            tp = tpls[code]
            for svc, w in cd["services"]:
                cx = _an.effect(act, mid, service=svc).get("count_x", 1.0) if act else 1.0
                n = poisson(rng, lam_code * w * cx * rng.uniform(0.85, 1.15))
                for _ in range(n):
                    sidx = pick_site(rng, cum, total)
                    if silent and SITES[sidx]["store_id"] in silent:
                        continue
                    lvl, cls = _level(rng)
                    lst = tp[cls]
                    tpl = lst[int(rnd() * len(lst))]
                    append(_mk(rng, pre, mep, code, appname, svc, _case(rng, lvl), tpl, sidx, queue, sync_x,
                               rnd() < 0.2, med_sync))
            for ex in eff.get("extras", ()):
                _, esvc, elvl, etpl = _m.ANOMALY_TPLS[ex["tpl"]]
                if "abs" in ex:
                    lam = ex["abs"]
                else:
                    lam = ex["rel"] * lam_code * (ERROR_SHARE if ex.get("of") == "error" else 1.0)
                    lam = max(lam, ex.get("floor", 0.0) * min(1.0, ex["rel"] / 2.0))
                n = poisson(rng, lam * scale if "abs" not in ex else lam)
                for _ in range(n):
                    if "site" in ex:
                        sidx = SITE_IDX[ex["site"]]
                    else:
                        sidx = pick_site(rng, cum, total)
                    if silent and SITES[sidx]["store_id"] in silent:
                        continue
                    d = _mk(rng, pre, mep, code, appname, esvc, _case(rng, elvl), etpl, sidx, queue, sync_x,
                            rnd() < 0.2, med_sync)
                    if "terminal" in ex:
                        d["terminal_id"] = ex["terminal"]
                        d["message"] = _m.render(etpl, rng, ex["site"], ex["terminal"],
                                                 SITE_BY_ID[ex["site"]]["tenant_name"])
                    append(d)
    return docs


def expected(start_ep, end_ep, scale=1.0, only_codes=None):
    """Expected baseline doc count (no anomalies), sampled every 5 minutes."""
    tot = 0.0
    codes = [c for c in APP_CODE_ORDER if only_codes is None or c in only_codes]
    step = 300
    for mep in range(int(start_ep), int(end_ep), step):
        span = min(step, int(end_ep) - mep) / 60.0
        for c in codes:
            tot += code_rate(c, mep + 120) * scale * span
    return tot
