"""Plan and chunked generation."""
import argparse
import datetime as _dt
import json
import random
from dataclasses import dataclass, field

from . import anomalies as _an
from . import apm as _apm
from . import logs as _logs
from . import streams as S

_UTC = _dt.timezone.utc
DEFAULT_SEED = 20261006


def _iso(d):
    return d.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    d = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=_UTC)
    return d.astimezone(_UTC)


@dataclass
class Plan:
    t0: _dt.datetime
    start: _dt.datetime
    tail_end: _dt.datetime
    seed: int = DEFAULT_SEED
    scale: float = 1.0
    with_population: bool = False
    anomalies: list = field(default_factory=list)  # resolved Anomaly windows (seeded + optional live)
    only: object = None  # {"services": [...], "app_codes": [...]} or None

    def to_dict(self):
        return {"t0": _iso(self.t0), "start": _iso(self.start), "tail_end": _iso(self.tail_end),
                "seed": self.seed, "scale": self.scale, "with_population": self.with_population,
                "anomalies": [a.to_dict() for a in self.anomalies], "only": self.only}

    @staticmethod
    def from_dict(d):
        return Plan(t0=_parse(d["t0"]), start=_parse(d["start"]), tail_end=_parse(d["tail_end"]),
                    seed=d.get("seed", DEFAULT_SEED), scale=d.get("scale", 1.0),
                    with_population=d.get("with_population", False),
                    anomalies=[a if isinstance(a, _an.Anomaly) else _an.Anomaly.from_dict(a)
                               for a in d.get("anomalies", [])],
                    only=d.get("only"))


def make_plan(t0=None, days=28, scale=1.0, seed=DEFAULT_SEED, with_population=False, tail_end=None):
    if t0 is None:
        t0 = _dt.datetime.now(_UTC)
    t0 = t0.astimezone(_UTC).replace(second=0, microsecond=0)
    start = (t0 - _dt.timedelta(days=days)).replace(hour=0, minute=0)
    if tail_end is None:
        tail_end = min(t0 + _dt.timedelta(hours=24), t0 + _dt.timedelta(days=7))
    anoms = _an.resolve(_an.seeded_for(with_population), t0)
    return Plan(t0=t0, start=start, tail_end=tail_end, seed=seed, scale=scale,
                with_population=with_population, anomalies=anoms)


def chunks(plan):
    out = []
    s = plan.start
    while s < plan.tail_end:
        e = min(s + _dt.timedelta(hours=1), plan.tail_end)
        out.append((s, e))
        s = e
    return out


def generate_chunk(plan_dict, start_iso, end_iso):
    plan = Plan.from_dict(plan_dict)
    s = _parse(start_iso)
    e = _parse(end_iso)
    sep = int(s.timestamp())
    eep = int(e.timestamp())
    rng = random.Random(hash((plan.seed, sep)))
    only = plan.only
    svcs = codes = None
    if only is not None:
        svcs = only.get("services")
        codes = only.get("app_codes")
    out = {st: [] for st in S.ALL_STREAMS}
    if only is None or svcs:
        out.update(_apm.generate(rng, sep, eep, int(plan.t0.timestamp()), plan.scale, plan.anomalies, svcs))
    if only is None or codes:
        out[S.LOGS] = _logs.generate(rng, sep, eep, plan.scale, plan.anomalies, codes)
    return out


def estimate(plan):
    """Approximate docs per stream for the whole plan (baseline rates; anomalies add <1%)."""
    sep = int(plan.start.timestamp())
    eep = int(plan.tail_end.timestamp())
    only = plan.only or {}
    out = {st: 0 for st in S.ALL_STREAMS}
    if plan.only is None or only.get("services"):
        r = _apm.expected(sep, eep, int(plan.t0.timestamp()), plan.scale, only.get("services"))
        for k, v in r.items():
            out[k] = int(v)
    if plan.only is None or only.get("app_codes"):
        out[S.LOGS] = int(_logs.expected(sep, eep, plan.scale, only.get("app_codes")))
    _add_anomaly_extras(plan, out)
    return out


def _add_anomaly_extras(plan, out):
    """Rough contribution of anomaly windows (extra logs and forced APM errors)."""
    from . import messages as _m
    only = plan.only
    for a in plan.anomalies:
        if a.start is None or a.kind == "delayed_logs":
            continue
        mep = a.start.timestamp() + (a.end - a.start).total_seconds() / 2
        mins = a.duration_min * 0.925  # trapezoid ramp
        code = a.target.get("app_code")
        if only is not None and only.get("app_codes") is not None and code not in only["app_codes"]:
            continue
        lam = _logs.code_rate(code, mep) * plan.scale if code else 0.0
        p = a.params
        if a.kind == "log_spike":
            out[S.LOGS] += int(p["extra_x"] * lam * mins)
        elif a.kind == "failure":
            out[S.LOGS] += int((p["log_total_x"] - 1.0) * lam * mins)
            svc = a.target["service"]
            from .entities import SERVICE_BY_NAME
            out[S.ERRORS] += int(_apm.svc_lambda(SERVICE_BY_NAME[svc], mep) * plan.scale * mins * p["fail_rate"])
        elif a.kind == "terminal_errors":
            out[S.LOGS] += int(p["err_per_min"] * mins)
        elif a.kind == "cascade":
            out[S.LOGS] += int(p["err_per_min"] * mins)
            dmins = (a.duration_min - p["downstream_delay_min"]) * 0.925
            lx = _logs.code_rate(a.target["downstream_app_code"], mep) * plan.scale
            out[S.LOGS] += int(max(lx * _logs.ERROR_SHARE * (p["lmn_err_x"] - 1.0), 3.0) * dmins)


def _cli(argv=None):
    ap = argparse.ArgumentParser(prog="python -m data.generate",
                                 description="Inspect the generator: counts and one sample doc per stream.")
    ap.add_argument("--sample", required=True, help="chunk start, e.g. 2026-09-20T17:00Z (1 hour)")
    ap.add_argument("--hours", type=float, default=1.0)
    ap.add_argument("--t0", default="2026-10-01T12:00Z")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--population", action="store_true")
    ap.add_argument("--estimate", action="store_true", help="also print the plan-wide estimate")
    a = ap.parse_args(argv)
    plan = make_plan(t0=_parse(a.t0), scale=a.scale, with_population=a.population)
    s = _parse(a.sample)
    e = s + _dt.timedelta(hours=a.hours)
    res = generate_chunk(plan.to_dict(), _iso(s), _iso(e))
    print("plan: start=%s t0=%s tail_end=%s" % (_iso(plan.start), _iso(plan.t0), _iso(plan.tail_end)))
    for an in plan.anomalies:
        print("  %s %s -> %s %s" % (an.id, _iso(an.start), _iso(an.end), an.title))
    print("chunk %s .. %s" % (_iso(s), _iso(e)))
    for st in S.ALL_STREAMS:
        print("%-44s %7d" % (st, len(res[st])))
    for st in S.ALL_STREAMS:
        if res[st]:
            print("\n== %s" % st)
            print(json.dumps(res[st][0], indent=1))
    if a.estimate:
        print("\nestimate:", json.dumps(estimate(plan), indent=1))


if __name__ == "__main__":
    _cli()
