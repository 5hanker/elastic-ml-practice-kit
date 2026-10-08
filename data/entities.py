"""Fictional, deterministic entities: brands, sites, terminals, services, hosts, builds."""
import hashlib
import random

# ---------------------------------------------------------------- brands
BRANDS = [
    dict(key="burger_barn", name="Skillet & Co", tenant_id="C-01", tz="US/Central",
         curve="qsr", weekend=1.3, segment="QSR", sites=50),
    dict(key="bean_scene", name="Daybreak Coffee", tenant_id="C-02", tz="US/Eastern",
         curve="coffee", weekend=0.8, segment="Coffee", sites=35),
    dict(key="taco_verde", name="Casa Fuego", tenant_id="C-03", tz="US/Central",
         curve="qsr", weekend=1.3, segment="QSR", sites=35),
    dict(key="harbor_grill", name="Harbor Grill", tenant_id="C-04", tz="US/Eastern",
         curve="dinner", weekend=1.0, segment="Casual dining", sites=25),
    dict(key="noodle_nook", name="Noodle Nook", tenant_id="C-05", tz="US/Pacific",
         curve="lunch", weekend=1.0, segment="Fast casual", sites=25),
    dict(key="crust_co", name="Crust & Co Pizza", tenant_id="C-06", tz="US/Eastern",
         curve="pizza", weekend=1.3, segment="Pizza", sites=30),
]

FLAGSHIP_SITE = "S-0117"
FLAGSHIP_TERMINAL = "T-0117-04"
_STREETS = ["Elm St", "Oak Ave", "Maple Rd", "Cedar Ln", "Pine Blvd", "Birch Way", "Lake Dr",
            "Hill St", "Main St", "Park Ave", "River Rd", "Mill St", "Union Sq", "Harbor Rd",
            "Sunset Blvd", "Market St", "Bridge St", "Orchard Ln", "Ridge Rd", "Station Rd"]


def _build_sites():
    rng = random.Random(1701)
    order = []
    for bi, b in enumerate(BRANDS):
        order += [bi] * b["sites"]
    rng.shuffle(order)
    # the flagship (S-0117, index 116) belongs to the first brand
    if order[116] != 0:
        j = order.index(0)
        order[116], order[j] = order[j], order[116]
    sites = []
    for i in range(200):
        bi = order[i]
        b = BRANDS[bi]
        sid = "S-%04d" % (i + 1)
        store = "%04d" % (1000 + rng.randrange(8999))
        sites.append(dict(
            idx=i, store_id=sid, brand=bi, tenant_id=b["tenant_id"], tenant_name=b["name"],
            store_name="%s %s" % (b["name"], _STREETS[rng.randrange(len(_STREETS))]),
            store_code=store, n_terminals=rng.randint(8, 16),
            weight=rng.lognormvariate(0, 0.35)))
    others = [s for s in sites if s["store_id"] != FLAGSHIP_SITE]
    mean = sum(s["weight"] for s in others) / len(others)
    for s in others:
        s["weight"] = s["weight"] / mean
    for s in sites:
        if s["store_id"] == FLAGSHIP_SITE:
            s["weight"] = 4.0
    return sites


SITES = _build_sites()
SITE_BY_ID = {s["store_id"]: s for s in SITES}
SITE_IDX = {s["store_id"]: s["idx"] for s in SITES}


def terminals_of(site):
    num = site["store_id"][2:]
    return ["T-%s-%02d" % (num, n + 1) for n in range(site["n_terminals"])]


TERMINALS = [terminals_of(s) for s in SITES]  # indexed by site idx


def _brand_shares():
    tot = [0.0] * len(BRANDS)
    for s in SITES:
        tot[s["brand"]] += s["weight"]
    t = sum(tot)
    return [x / t for x in tot]


BRAND_SHARE = _brand_shares()

# ---------------------------------------------------------------- app codes
APP_CODES = {
    "XYZ": dict(app_name="checkout-app", share=0.35,
                 services=[("cart-api", 0.6), ("payments-gateway", 0.4)]),
    "PQR": dict(app_name="online-ordering", share=0.25, services=[("orders-api", 1.0)]),
    "LMN": dict(app_name="data-management", share=0.15, services=[("admin-portal-api", 1.0)]),
    "ABC": dict(app_name="catalog-sync", share=0.15, services=[("catalog-sync", 1.0)]),
    "DEF": dict(app_name="inventory-sync", share=0.10, services=[("inventory-sync", 1.0)]),
}
APP_CODE_ORDER = ["XYZ", "PQR", "LMN", "ABC", "DEF"]
SERVICE_APP_CODE = {}
for _c, _d in APP_CODES.items():
    for _s, _w in _d["services"]:
        SERVICE_APP_CODE[_s] = _c
LOG_BUILDS = [("2026.09.3", 0.50), ("2026.09.4", 0.40), ("2026.10.0-canary", 0.10)]

# ---------------------------------------------------------------- APM services
# tx tuple: (name, type, weight, p50 factor)
_SVC_DEFS = [
    dict(name="cart-api", agent="nodejs", app_code="XYZ", peak_tpm=14, p50_ms=120, base_fail=0.006,
         txs=[("POST /api/v1/checkout", "request", 0.45, 1.3),
              ("GET /api/v1/cart/{id}", "request", 0.35, 0.5),
              ("POST /api/v1/checkout/tender", "request", 0.20, 1.0)]),
    dict(name="payments-gateway", agent="java", app_code="XYZ", peak_tpm=12, p50_ms=220, base_fail=0.008,
         txs=[("PaymentController#authorize", "request", 0.55, 1.2),
              ("PaymentController#capture", "request", 0.35, 0.8),
              ("PaymentController#refund", "request", 0.10, 1.0)]),
    dict(name="orders-api", agent="nodejs", app_code="PQR", peak_tpm=12, p50_ms=180, base_fail=0.007,
         txs=[("POST /api/v1/orders", "request", 0.35, 1.6),
              ("GET /api/v1/catalog/{storeId}", "request", 0.40, 0.7),
              ("GET /api/v1/orders/{id}", "request", 0.25, 0.6)]),
    dict(name="admin-portal-api", agent="java", app_code="LMN", peak_tpm=8, p50_ms=250, base_fail=0.005,
         txs=[("GET /portal/api/sites/{id}/config", "request", 0.50, 0.8),
              ("PUT /portal/api/catalog", "request", 0.20, 1.4),
              ("GET /portal/api/reports/sales", "request", 0.30, 1.2)]),
    dict(name="inventory-sync", agent="java", app_code="DEF", peak_tpm=2, p50_ms=4000, base_fail=0.004,
         txs=[("InventorySyncJob#run", "job", 0.4, 1.0),
              ("InventorySyncJob#applyDelta", "job", 0.6, 1.0)]),
    dict(name="catalog-sync", agent="python", app_code="ABC", peak_tpm=3, p50_ms=90, base_fail=0.009,
         txs=[("sync_catalog", "messaging", 0.55, 1.2),
              ("consume_catalog_ack", "messaging", 0.45, 0.8)]),
]
# (service, transaction name) -> (downstream service, downstream transaction index)
EDGES = {
    ("cart-api", "POST /api/v1/checkout"): ("payments-gateway", 0),
    ("cart-api", "POST /api/v1/checkout/tender"): ("payments-gateway", 1),
    ("orders-api", "POST /api/v1/orders"): ("payments-gateway", 0),
    ("orders-api", "GET /api/v1/catalog/{storeId}"): ("admin-portal-api", 0),
    ("admin-portal-api", "PUT /portal/api/catalog"): ("catalog-sync", 0),
    ("inventory-sync", "InventorySyncJob#applyDelta"): ("admin-portal-api", 0),
}
_AGENTS = {
    "nodejs": dict(agent={"name": "nodejs", "version": "4.15.0", "activation_method": "require"},
                   language={"name": "javascript"},
                   extra={"framework": {"name": "express", "version": "4.22.2"},
                          "runtime": {"name": "node", "version": "20.20.2"}}),
    "java": dict(agent={"name": "java", "version": "1.55.0"},
                 language={"name": "Java", "version": "17.0.19"},
                 extra={"runtime": {"name": "Java", "version": "17.0.19"}}),
    "python": dict(agent={"name": "python", "version": "6.23.0"},
                   language={"name": "python", "version": "3.12.4"},
                   extra={"framework": {"name": "fastapi", "version": "0.115.0"},
                          "runtime": {"name": "CPython", "version": "3.12.4"}}),
}
_BUILD_NAMES = ["3.4.1", "3.4.2", "3.5.0-canary"]


class Service(object):
    pass


def _build_services():
    out = []
    for si, d in enumerate(_SVC_DEFS):
        s = Service()
        s.idx = si
        s.name = d["name"]
        s.agent_name = d["agent"]
        s.app_code = d["app_code"]
        s.peak_tpm = d["peak_tpm"]
        s.p50_ms = d["p50_ms"]
        s.base_fail = d["base_fail"]
        s.txs = d["txs"]
        tot = sum(t[2] for t in s.txs)
        acc = 0.0
        s.tx_cum = []
        for t in s.txs:
            acc += t[2] / tot
            s.tx_cum.append(acc)
        s.tx_cum[-1] = 1.0
        ag = _AGENTS[d["agent"]]
        s.agent = ag["agent"]
        s.language = ag["language"]["name"]
        pod = hashlib.md5(s.name.encode()).hexdigest()[:4]
        nh = 2 + (si % 3)  # 2-4 hosts
        s.hosts = ["%s-%s-%d" % (s.name, pod, n + 1) for n in range(nh)]
        s.builds = list(_BUILD_NAMES)
        s.build_cum = [0.45, 0.90, 1.0]  # last build is the ~10% canary
        s.instances = []  # [build idx][host idx] -> dict(service=..., host=...)
        for b in s.builds:
            row = []
            for h in s.hosts:
                svc = {"name": s.name, "environment": "production", "version": b,
                       "language": dict(ag["language"]), "node": {"name": h}}
                svc.update(ag["extra"])
                host = {"name": h, "hostname": h, "os": {"platform": "linux"}, "architecture": "amd64"}
                row.append(dict(service=svc, host=host))
            s.instances.append(row)
        out.append(s)
    return out


SERVICES = _build_services()
SERVICE_BY_NAME = {s.name: s for s in SERVICES}
BUILDS = {s.name: list(s.builds) for s in SERVICES}
HOSTS = {s.name: list(s.hosts) for s in SERVICES}
