"""Log message templates (by app_code and level class) and APM error messages. All fictional."""
import hashlib
import string
from collections import namedtuple

# text, fields (generated variables), error_code, error_name, path template, method, status, typical ms
Tpl = namedtuple("Tpl", "text fields code name path method status rt")

_CTX = {"site", "terminal", "company"}
_fmt = string.Formatter()


def _t(text, code=None, name=None, path=None, method=None, status=None, rt=None):
    fields = tuple(sorted({f for _, f, _, _ in _fmt.parse(text) if f and f not in _CTX}))
    return Tpl(text, fields, code, name, path, method, status, rt)


# variable generators: rng -> value
VARS = {
    "order": lambda r: "ORD-%07d" % r.randrange(10 ** 7),
    "amount": lambda r: "%d.%02d" % (r.randrange(3, 140), r.randrange(100)),
    "ms": lambda r: int(r.lognormvariate(5.3, 0.6)),
    "n": lambda r: r.randint(1, 5),
    "items": lambda r: r.randint(1, 9),
    "last4": lambda r: "%04d" % r.randrange(10000),
    "rc": lambda r: "%02d" % r.choice((5, 14, 51, 54, 57, 61)),
    "status": lambda r: r.choice(("accepted", "preparing", "ready", "picked_up")),
    "sess": lambda r: "%08x" % r.getrandbits(32),
    "sku": lambda r: "SKU-%05d" % r.randrange(100000),
    "user": lambda r: "user%03d" % r.randrange(400),
    "count": lambda r: r.randint(3, 400),
    "ver": lambda r: "v%d" % r.randint(1000, 1400),
    "batch": lambda r: "B%06d" % r.randrange(10 ** 6),
    "garbage": lambda r: "".join(r.choice("{}[]\\x00\\ufffd0123456789abcdef~^") for _ in range(r.randint(6, 12))),
}

LEVEL_CLASSES = ("INFO", "WARN", "ERROR")

LOG_TEMPLATES = {
    "XYZ": {
        "INFO": [
            _t("Order {order} completed for terminal {terminal}, total ${amount}", path="/api/v1/checkout",
               method="POST", status=200, rt=140),
            _t("Tender ending {last4} approved for terminal {terminal} in {ms} ms",
               path="/api/v1/checkout/tender", method="POST", status=200, rt=110),
            _t("Cart {order} loaded for terminal {terminal} ({items} items)", path="/api/v1/cart/{id}",
               method="GET", status=200, rt=45),
            _t("Payment captured for order {order}, amount ${amount}", path="/internal/payments/capture",
               method="POST", status=200, rt=180),
            _t("Receipt printed for order {order} at site {site}"),
        ],
        "WARN": [
            _t("Card reader on terminal {terminal} responded slowly ({ms} ms)", "CK-RDR-02", "CardReaderSlow"),
            _t("Tender retry {n} for order {order} on terminal {terminal}", "CK-TND-05", "TenderRetry",
               path="/api/v1/checkout/tender", method="POST", status=409, rt=400),
        ],
        "ERROR": [
            _t("Payment authorization declined by issuer for order {order} (code {rc})", "PG-051", "IssuerDeclined",
               path="/internal/payments/authorize", method="POST", status=402, rt=600),
            _t("Failed to persist order {order}: database connection timeout after {ms} ms", "CK-DB-504",
               "DatabaseTimeout", path="/api/v1/checkout", method="POST", status=500, rt=3000),
            _t("Terminal {terminal} lost connection to payments-gateway", "CK-NET-01", "GatewayUnreachable",
               path="/api/v1/checkout/tender", method="POST", status=502, rt=5000),
        ],
    },
    "PQR": {
        "INFO": [
            _t("Online order {order} accepted for site {site} ({items} items)", path="/api/v1/orders",
               method="POST", status=201, rt=200),
            _t("Catalog served for site {site} in {ms} ms", path="/api/v1/catalog/{storeId}", method="GET",
               status=200, rt=90),
            _t("Order {order} status changed to {status}", path="/api/v1/orders/{id}", method="GET",
               status=200, rt=60),
            _t("Pickup window confirmed for order {order}: {n} min", path="/api/v1/orders", method="POST",
               status=200, rt=150),
        ],
        "WARN": [
            _t("Order {order} throttled for shopper session {sess}", "OR-429", "RateLimited",
               path="/api/v1/orders", method="POST", status=429, rt=30),
            _t("Catalog cache miss for site {site}, rebuilding", "OR-CACHE-01", "CatalogCacheMiss",
               path="/api/v1/catalog/{storeId}", method="GET", status=200, rt=700),
        ],
        "ERROR": [
            _t("Failed to submit order {order} to site {site}: store offline", "OR-503", "StoreOffline",
               path="/api/v1/orders", method="POST", status=503, rt=2500),
            _t("Payment authorization timed out for online order {order}", "OR-PAY-408", "PaymentTimeout",
               path="/api/v1/orders", method="POST", status=504, rt=8000),
            _t("Order {order} rejected: invalid modifier {sku}", "OR-400", "InvalidModifier",
               path="/api/v1/orders", method="POST", status=400, rt=50),
        ],
    },
    "LMN": {
        "INFO": [
            _t("Portal config for site {site} served in {ms} ms", path="/portal/api/sites/{id}/config",
               method="GET", status=200, rt=120),
            _t("Price list draft saved by {user} for company {company}", path="/portal/api/pricelist", method="PUT",
               status=200, rt=260),
            _t("Sales report generated for company {company} ({count} rows)",
               path="/portal/api/reports/sales", method="GET", status=200, rt=900),
            _t("Terminal {terminal} configuration synced to portal"),
        ],
        "WARN": [
            _t("Report for company {company} exceeded {ms} ms", "AP-RPT-01", "SlowReport",
               path="/portal/api/reports/sales", method="GET", status=200, rt=4500),
            _t("Config version conflict for site {site}, retrying", "AP-409", "ConfigConflict",
               path="/portal/api/sites/{id}/config", method="GET", status=409, rt=80),
        ],
        "ERROR": [
            _t("Failed to load config for site {site}: upstream timeout after {ms} ms", "AP-504",
               "UpstreamTimeout", path="/portal/api/sites/{id}/config", method="GET", status=504, rt=6000),
            _t("Price list save failed for company {company}: validation error on item {sku}", "AP-422",
               "PriceListValidationFailed", path="/portal/api/pricelist", method="PUT", status=422, rt=90),
            _t("Report export failed for company {company}: out of memory", "AP-500", "ReportExportFailed",
               path="/portal/api/reports/sales", method="GET", status=500, rt=7000),
        ],
    },
    "ABC": {
        "INFO": [
            _t("Catalog sync to site {site} completed in {ms} ms (version {ver})"),
            _t("Catalog acknowledgement received from terminal {terminal} for version {ver}"),
            _t("Catalog sync batch {batch} queued for {count} sites"),
        ],
        "WARN": [
            _t("Catalog ack from terminal {terminal} delayed {ms} ms", "CS-ACK-01", "AckDelayed"),
            _t("Catalog sync to site {site} retrying after connection reset (attempt {n})", "CS-CONN-02",
               "ConnectionReset"),
        ],
        "ERROR": [
            _t("Catalog sync to site {site} failed: terminal {terminal} unreachable", "CS-503",
               "TerminalUnreachable"),
            _t("Catalog sync batch {batch} aborted: broker nack", "CS-BRK-01", "BrokerNack"),
            _t("Catalog package checksum mismatch for version {ver}", "CS-CHK-01", "ChecksumMismatch"),
        ],
    },
    "DEF": {
        "INFO": [
            _t("Inventory sync batch {batch} completed: {count} items updated for site {site}"),
            _t("Inventory delta applied for company {company}: {count} SKUs"),
            _t("Nightly inventory reconciliation started for company {company}"),
        ],
        "WARN": [
            _t("Inventory sync for site {site} slower than expected ({ms} ms)", "INV-SLOW-01", "SyncSlow"),
            _t("SKU {sku} not found for site {site}, skipping", "INV-404", "SkuNotFound"),
        ],
        "ERROR": [
            _t("Inventory sync batch {batch} failed: database deadlock detected", "INV-DB-40P01",
               "DatabaseDeadlock"),
            _t("Failed to apply delta for site {site}: stale version {ver}", "INV-409", "StaleVersion"),
            _t("Supplier feed unavailable for company {company}", "INV-503", "SupplierFeedUnavailable"),
        ],
    },
}

# Templates injected by anomalies: key -> (app_code, service, level, Tpl)
ANOMALY_TPLS = {
    "abc_reject": ("ABC", "catalog-sync", "INFO",
                   _t("Catalog sync to site {site} rejected: HTTP 422 payload validation failed, "
                      "will retry (attempt {n})", "CS-422", "CatalogPayloadRejected")),
    "issuer_timeout": ("XYZ", "payments-gateway", "ERROR",
                       _t("Authorization failed for terminal {terminal}: issuer did not respond (code 91)",
                          "PG-091", "IssuerTimeoutException", path="/internal/payments/authorize",
                          method="POST", status=504, rt=8000)),
    "inv_reject": ("DEF", "inventory-sync", "ERROR",
                   _t("Inventory delta rejected: cannot parse SKU payload '{garbage}' for company {company}",
                      "INV-422", "InventoryPayloadRejected")),
    "lmn_err": ("LMN", "admin-portal-api", "ERROR",
                _t("Portal request timed out waiting for inventory sync ({ms} ms)", "AP-504", "UpstreamTimeout",
                   path="/portal/api/sites/{id}/config", method="GET", status=504, rt=9000)),
    "printer_offline": ("XYZ", "cart-api", "ERROR",
                        _t("Printer offline on terminal {terminal}, retry {n}", "CK-PRN-01", "PrinterOffline")),
}


def render(tpl, rng, site="", terminal="", company=""):
    vals = {k: VARS[k](rng) for k in tpl.fields}
    return tpl.text.format(site=site, terminal=terminal, company=company, **vals)


# ---------------------------------------------------------------- APM errors
# service -> [(exception type, message, culprit, handled)]
APM_ERRORS = {
    "cart-api": [
        ("Error", "connect ECONNREFUSED 10.20.4.17:8080", "from (node_modules/axios/dist/node/axios.cjs)", True),
        ("ValidationError", "cart total does not match line items", "validateCart (src/cart/validate.js)", True),
        ("TimeoutError", "tender request timed out after 5000 ms", "submitTender (src/tender/client.js)", False),
    ],
    "payments-gateway": [
        ("IssuerTimeoutException", "issuer did not respond (code 91)",
         "com.mlws.payments.IssuerClient.authorize(IssuerClient.java:142)", False),
        ("CardDeclinedException", "card declined by issuer (code 51)",
         "com.mlws.payments.PaymentController.authorize(PaymentController.java:88)", True),
        ("SQLTransientConnectionException", "connection pool exhausted",
         "com.mlws.payments.LedgerRepository.save(LedgerRepository.java:57)", False),
    ],
    "orders-api": [
        ("Error", "store S-offline: submit rejected with 503", "submitOrder (src/orders/submit.js)", True),
        ("ValidationError", "invalid modifier id", "validateOrder (src/orders/validate.js)", True),
        ("TimeoutError", "payment authorization timed out", "authorize (src/payments/client.js)", False),
    ],
    "admin-portal-api": [
        ("java.net.SocketTimeoutException", "Read timed out",
         "com.mlws.portal.SiteConfigClient.fetch(SiteConfigClient.java:73)", False),
        ("PriceListValidationException", "item SKU failed schema validation",
         "com.mlws.portal.PriceListController.save(PriceListController.java:121)", True),
        ("java.lang.OutOfMemoryError", "Java heap space",
         "com.mlws.portal.ReportExporter.export(ReportExporter.java:210)", False),
    ],
    "inventory-sync": [
        ("DeadlockLoserDataAccessException", "deadlock detected",
         "com.mlws.inventory.InventorySyncJob.applyDelta(InventorySyncJob.java:164)", False),
        ("StaleVersionException", "stale inventory version", "com.mlws.inventory.DeltaApplier.apply(DeltaApplier.java:92)", True),
    ],
    "catalog-sync": [
        ("ConnectionResetError", "[Errno 104] Connection reset by peer", "sync_catalog (catalog_sync/publisher.py)", True),
        ("BrokerNackError", "broker nack for catalog package", "consume_catalog_ack (catalog_sync/consumer.py)", False),
    ],
}

ISSUER_TIMEOUT_ERROR = ("IssuerTimeoutException", "issuer did not respond (code 91)",
                        "com.mlws.payments.IssuerClient.authorize(IssuerClient.java:142)", False)


def grouping_key(etype, culprit):
    return hashlib.md5((etype + "|" + culprit).encode()).hexdigest()[:16]
