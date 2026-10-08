"""Seeded (A1-A7) and live anomalies, their resolution against T0, and their effect on generation."""
import dataclasses
import datetime as _dt
from dataclasses import dataclass, field

_UTC = _dt.timezone.utc

J_MM = "mlws-apm-multimetric"
J_ERR = "mlws-logs-errors-correct"
J_LVL = "mlws-logs-errors-level-filtered"
J_CAT = "mlws-logs-categories"
J_SITE = "mlws-logs-store-volume"
J_INV = "mlws-logs-inventory-health"
J_POP = "mlws-terminal-population"


@dataclass
class Anomaly:
    id: str
    title: str
    kind: str
    day_offset: object  # int | None
    start_hhmm: object  # str | None
    duration_min: int
    target: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    catches: list = field(default_factory=list)
    must_not_catch: list = field(default_factory=list)
    start: object = None  # datetime | None (resolved)
    end: object = None
    requires: object = None  # None | "with_population"
    entity: str = ""
    what: str = ""

    def to_dict(self):
        d = dataclasses.asdict(self)
        d["start"] = self.start.isoformat().replace("+00:00", "Z") if self.start else None
        d["end"] = self.end.isoformat().replace("+00:00", "Z") if self.end else None
        return d

    @staticmethod
    def from_dict(d):
        d = dict(d)
        for k in ("start", "end"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = _parse(v)
        names = {f.name for f in dataclasses.fields(Anomaly)}
        return Anomaly(**{k: v for k, v in d.items() if k in names})


def _parse(s):
    s = s.replace("Z", "+00:00")
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_UTC)
    return d.astimezone(_UTC)


SEEDED = [
    Anomaly("A1", "Checkout traffic drops to 30%", "volume_drop", -9, "17:00", 90,
            target={"service": "cart-api"}, params={"count_x": 0.3},
            catches=[{"job": J_MM, "partition": "cart-api", "min_score": 75, "detector": "low_count", "function": "low_count"}],
            entity="cart-api",
            what="Transactions per minute x0.3 (APM and XYZ logs from cart-api)."),
    Anomaly("A2", "Order submission latency x4", "latency", -7, "23:00", 90,
            target={"service": "orders-api", "transaction": "POST /api/v1/orders"},
            params={"latency_x": 4.0},
            catches=[{"job": J_MM, "partition": "orders-api", "min_score": 75,
                      "detector": "high_mean latency", "function": "high_mean",
                      "field_name": "transaction.duration.us"}],
            entity="orders-api POST /api/v1/orders", what="Transaction latency x4."),
    Anomaly("A3", "Flagship site goes dark", "store_silent", -5, "16:30", 120,
            target={"site": "S-0117"}, params={},
            catches=[{"job": J_SITE, "partition": "S-0117", "min_score": 75, "detector": "low_count by site", "function": "low_count"}],
            entity="site S-0117", what="Zero logs and zero transactions for the site."),
    Anomaly("A4", "Issuer timeouts at the payments gateway", "failure", -4, "17:00", 60,
            target={"service": "payments-gateway", "app_code": "XYZ"},
            params={"fail_rate": 0.25, "log_total_x": 3.0},
            catches=[{"job": J_MM, "partition": "payments-gateway", "min_score": 75, "detector": "high_sum mlws_failed", "function": "high_sum",
                      "field_name": "mlws_failed"},
                     {"job": J_LVL, "partition": "XYZ", "min_score": 75, "detector": "high_count", "function": "high_count"}],
            entity="payments-gateway / XYZ",
            what=('Failure rate 25%; APM errors "IssuerTimeoutException: issuer did not respond (code 91)"; '
                  "XYZ ERROR logs lift total XYZ volume x3.")),
    Anomaly("A5", "Catalog sync rejected at scale (INFO-level flood)", "log_spike", -2, "16:00", 120,
            target={"app_code": "ABC"}, params={"extra_x": 7.0},
            catches=[{"job": J_ERR, "partition": "ABC", "min_score": 75, "detector": "high_count", "function": "high_count"},
                     {"job": J_CAT, "partition": "ABC", "min_score": 75, "detector": "count / rare category",
                      "function": ["count", "rare"]}],
            must_not_catch=[{"job": J_LVL, "partition": "ABC", "max_score": 25}],
            entity="app_code ABC",
            what=('Extra INFO logs "Catalog sync to site {site} rejected: HTTP 422 payload validation failed, '
                  'will retry (attempt {n})" with error_code CS-422 / CatalogPayloadRejected; ABC volume x8.')),
    Anomaly("A6", "Inventory queue backs up, portal slows down", "cascade", -1, "22:30", 150,
            target={"service": "inventory-sync", "downstream": "admin-portal-api",
                    "app_code": "DEF", "downstream_app_code": "LMN"},
            params={"queue_from": 50, "queue_to": 6000, "sync_x": 10.0, "err_per_min": 25,
                    "inv_latency_x": 4.0, "downstream_delay_min": 45, "downstream_latency_x": 3.0,
                    "lmn_err_x": 6.0},
            catches=[{"job": J_INV, "partition": "DEF", "min_score": 75, "detector": "queue size / syncDuration",
                      "function": ["max", "high_mean"],
                      "field_name": ["db_queue_size", "sync_duration_ms"]},
                     {"job": J_LVL, "partition": "DEF", "min_score": 75, "detector": "high_count", "function": "high_count"},
                     {"job": J_MM, "partition": "admin-portal-api", "min_score": 75, "detector": "high_mean latency",
                      "function": "high_mean", "field_name": "transaction.duration.us"},
                     {"job": J_CAT, "partition": "DEF", "min_score": 75, "detector": "count / rare category",
                      "function": ["count", "rare"]}],
            entity="inventory-sync -> admin-portal-api",
            what=("DEF queue ramps 50 -> 6000, syncDuration x10, ERROR \"Inventory delta rejected: cannot parse SKU "
                  "payload '{garbage}' for company {company}\" (>=20/min); from +45m admin-portal-api latency x3 "
                  "and LMN ERROR logs x6.")),
    Anomaly("A7", "Printer offline on one terminal", "terminal_errors", -3, "12:00", 60,
            target={"terminal": "T-0117-04", "site": "S-0117", "app_code": "XYZ"},
            params={"err_per_min": 30},
            catches=[{"job": J_POP, "partition": "T-0117-04", "min_score": 75, "detector": "high_count over terminal_id", "function": "high_count"}],
            requires="with_population", entity="terminal T-0117-04",
            what='ERROR "Printer offline on terminal {t}, retry {n}" at 30/min.'),
]


def resolve(anomalies, t0):
    """Compute start/end for seeded anomalies relative to t0 (date(T0) + day_offset at HH:MM UTC).
    If end > t0 - 60m, shift by -1 day (repeated until satisfied). Live anomalies pass through."""
    out = []
    t0 = t0.astimezone(_UTC)
    base = _dt.datetime(t0.year, t0.month, t0.day, tzinfo=_UTC)
    for a in anomalies:
        if a.day_offset is None and a.start is not None:
            out.append(dataclasses.replace(a))
            continue
        hh, mm = [int(x) for x in a.start_hhmm.split(":")]
        start = base + _dt.timedelta(days=a.day_offset, hours=hh, minutes=mm)
        end = start + _dt.timedelta(minutes=a.duration_min)
        while end > t0 - _dt.timedelta(minutes=60):
            start -= _dt.timedelta(days=1)
            end -= _dt.timedelta(days=1)
        out.append(dataclasses.replace(a, start=start, end=end))
    return out


def seeded_for(with_population):
    return [a for a in SEEDED if with_population or a.requires != "with_population"]


# ---------------------------------------------------------------- live scenarios
def _floor_min(now):
    now = now.astimezone(_UTC)
    return now.replace(second=0, microsecond=0)


def _ceil_min(now):
    f = _floor_min(now)
    return f if f == now.astimezone(_UTC) else f + _dt.timedelta(minutes=1)


def _live(id_, title, kind, start, end, target, params, entity, what):
    return Anomaly(id_, title, kind, None, None, int((end - start).total_seconds() // 60),
                   target=target, params=params, start=start, end=end, entity=entity, what=what)


def _cascade(now):
    n = _ceil_min(now)
    m = _dt.timedelta(minutes=1)
    return [
        _live("L1", "Live: payments-gateway failures", "failure", n + 2 * m, n + 47 * m,
              {"service": "payments-gateway", "app_code": "XYZ"},
              {"fail_rate": 0.25, "log_total_x": 3.0}, "payments-gateway / XYZ",
              "Failure rate 25%; XYZ ERROR logs x3."),
        _live("L2", "Live: order latency x4", "latency", n + 17 * m, n + 62 * m,
              {"service": "orders-api"}, {"latency_x": 4.0}, "orders-api",
              "Latency x4 for all transactions."),
    ]


def _info_flood(now):
    n = _ceil_min(now)
    m = _dt.timedelta(minutes=1)
    return [_live("L1", "Live: ABC INFO spike", "log_spike", n + 2 * m, n + 62 * m,
                  {"app_code": "ABC"}, {"extra_x": 7.0}, "app_code ABC",
                  "A5-style INFO flood with error_code CS-422.")]


def _delayed(now):
    n = _floor_min(now)
    m = _dt.timedelta(minutes=1)
    return [_live("L1", "Live: delayed XYZ logs", "delayed_logs", n - 45 * m, n - 30 * m,
                  {"app_code": "XYZ"}, {}, "app_code XYZ",
                  "XYZ logs written late into a window the real-time datafeed already passed.")]


LIVE_SCENARIOS = {"cascade": _cascade, "info-flood": _info_flood, "delayed": _delayed}


# ---------------------------------------------------------------- effects
def ramp(a, t):
    """0..1 trapezoid with 15% ramp in/out. t is epoch seconds."""
    s = a.start.timestamp()
    e = a.end.timestamp()
    if t < s or t >= e:
        return 0.0
    dur = e - s
    edge = 0.15 * dur
    x = t - s
    if x < edge:
        return x / edge
    if dur - x < edge:
        return (dur - x) / edge
    return 1.0


def _ramp_sub(s, e, t):
    if t < s or t >= e:
        return 0.0
    edge = 0.15 * (e - s)
    x = t - s
    if x < edge:
        return x / edge
    if (e - s) - x < edge:
        return ((e - s) - x) / edge
    return 1.0


def active(anomalies, start_epoch, end_epoch):
    """Anomalies overlapping [start, end) in epoch seconds."""
    return [a for a in anomalies if a.start is not None
            and a.start.timestamp() < end_epoch and a.end.timestamp() > start_epoch]


def effect(anomalies, ts, **entity):
    """Multipliers/overrides active for `entity` at ts (datetime or epoch seconds).

    entity keys: service, transaction, site, app_code, terminal. Returned keys (only when set):
      count_x, latency_x (multipliers), fail_rate (absolute), force_errors, silent, queue, sync_x,
      extras: [{"tpl", "rel"|"abs", "of": "total"|"error", "site", "terminal"}], active: [ids]
    """
    t = ts if isinstance(ts, (int, float)) else ts.timestamp()
    out = {}
    svc = entity.get("service")
    tx = entity.get("transaction")
    site = entity.get("site")
    code = entity.get("app_code")
    for a in anomalies:
        if a.start is None or a.kind == "delayed_logs":
            continue
        r = ramp(a, t)
        if r <= 0.0:
            continue
        tg, p, k = a.target, a.params, a.kind
        hit = False
        if k == "volume_drop":
            if svc is not None and svc == tg["service"]:
                out["count_x"] = out.get("count_x", 1.0) * (1.0 + (p["count_x"] - 1.0) * r)
                hit = True
        elif k == "latency":
            if svc is not None and svc == tg["service"] and (
                    "transaction" not in tg or tx is None or tx == tg["transaction"]):
                out["latency_x"] = out.get("latency_x", 1.0) * (1.0 + (p["latency_x"] - 1.0) * r)
                hit = True
        elif k == "store_silent":
            if site is not None and site == tg["site"]:
                out["silent"] = True
                hit = True
        elif k == "failure":
            if svc is not None and svc == tg["service"]:
                out["fail_rate"] = max(out.get("fail_rate", 0.0), p["fail_rate"] * r)
                out["force_errors"] = True
                hit = True
            if code is not None and code == tg.get("app_code"):
                out.setdefault("extras", []).append(
                    {"tpl": "issuer_timeout", "rel": (p["log_total_x"] - 1.0) * r, "of": "total"})
                hit = True
        elif k == "log_spike":
            if code is not None and code == tg["app_code"]:
                out.setdefault("extras", []).append(
                    {"tpl": "abc_reject", "rel": p["extra_x"] * r, "of": "total"})
                hit = True
        elif k == "terminal_errors":
            if code is not None and code == tg["app_code"]:
                out.setdefault("extras", []).append(
                    {"tpl": "printer_offline", "abs": p["err_per_min"] * r, "of": "total",
                     "site": tg["site"], "terminal": tg["terminal"]})
                hit = True
        elif k == "cascade":
            s = a.start.timestamp()
            e = a.end.timestamp()
            ds = s + p["downstream_delay_min"] * 60.0
            if code is not None and code == tg["app_code"]:
                out["queue"] = p["queue_from"] + (p["queue_to"] - p["queue_from"]) * ((t - s) / (e - s))
                out["sync_x"] = out.get("sync_x", 1.0) * (1.0 + (p["sync_x"] - 1.0) * r)
                out.setdefault("extras", []).append(
                    {"tpl": "inv_reject", "abs": p["err_per_min"] * r, "of": "total"})
                hit = True
            if svc is not None and svc == tg["service"]:
                out["latency_x"] = out.get("latency_x", 1.0) * (1.0 + (p["inv_latency_x"] - 1.0) * r)
                hit = True
            r2 = _ramp_sub(ds, e, t)
            if r2 > 0:
                if svc is not None and svc == tg["downstream"]:
                    out["latency_x"] = out.get("latency_x", 1.0) * (
                        1.0 + (p["downstream_latency_x"] - 1.0) * r2)
                    hit = True
                if code is not None and code == tg["downstream_app_code"]:
                    out.setdefault("extras", []).append(
                        {"tpl": "lmn_err", "rel": (p["lmn_err_x"] - 1.0) * r2, "of": "error", "floor": 3.0})
                    hit = True
        if hit:
            out.setdefault("active", []).append(a.id)
    return out


# ---------------------------------------------------------------- docs
def anomalies_markdown(resolved):
    """Markdown table of anomalies (id, UTC window, entity, what changed, caught by, must NOT be caught by)."""
    def fmt(s):
        return s.strftime("%Y-%m-%d %H:%M") if s else "?"

    def jobs(lst, key):
        if not lst:
            return "-"
        return "; ".join("`%s` / %s (%s %d)" % (c["job"], c["partition"],
                                                "score >=" if key == "min_score" else "score <=", c[key])
                         for c in lst)

    lines = ["| ID | Window (UTC) | Entity | What changed | Caught by | Must NOT be caught by |",
             "|---|---|---|---|---|---|"]
    for a in resolved:
        win = "%s to %s" % (fmt(a.start), a.end.strftime("%H:%M") if a.end else "?")
        if a.start and a.end and a.end.date() != a.start.date():
            win = "%s to %s" % (fmt(a.start), fmt(a.end))
        title = a.id + (" (population only)" if a.requires == "with_population" else "")
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            title, win, a.entity or "-", (a.what or a.title).replace("|", "/"),
            jobs(a.catches, "min_score"), jobs(a.must_not_catch, "max_score")))
    return "\n".join(lines) + "\n"
