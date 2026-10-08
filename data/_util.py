"""Small shared helpers (stdlib only)."""
import bisect
import math
import time

from .entities import BRANDS, SITES
from .seasonality import brand_factors

_exp = math.exp
_sqrt = math.sqrt


def poisson(rng, lam):
    if lam <= 0.0:
        return 0
    if lam < 25.0:
        limit = _exp(-lam)
        k = 0
        p = rng.random()
        while p > limit:
            k += 1
            p *= rng.random()
        return k
    n = int(rng.gauss(lam, _sqrt(lam)) + 0.5)
    return n if n > 0 else 0


def minute_prefix(mep):
    """'2026-09-30T20:46:' for an epoch second at a minute boundary."""
    return time.strftime("%Y-%m-%dT%H:%M:", time.gmtime(mep))


def minute_iso(mep):
    return time.strftime("%Y-%m-%dT%H:%M:00.000Z", time.gmtime(mep))


def site_cum(epoch_s):
    """Cumulative site weights (site weight x brand seasonal factor) for picking a site."""
    bf = brand_factors(epoch_s)
    cum = []
    acc = 0.0
    for s in SITES:
        acc += s["weight"] * bf[s["brand"]]
        cum.append(acc)
    return cum, acc


def pick_site(rng, cum, total):
    i = bisect.bisect(cum, rng.random() * total)
    return i if i < len(cum) else len(cum) - 1
