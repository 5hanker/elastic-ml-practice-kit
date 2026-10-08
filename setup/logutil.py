"""Logging with secret redaction.

All user-visible output goes through the ``mlws`` logger (console handler:
plain messages; optional file handler: timestamped). Both handlers redact the
API key and any ``ApiKey <token>`` pattern, including inside tracebacks.
"""

import base64
import logging
import re
import sys
from typing import Iterable, Optional, Set, TextIO

LOGGER_NAME = "mlws"
LOG_FILE_NAME = "mlws-setup.log"
_APIKEY_RE = re.compile(r"ApiKey\s+[A-Za-z0-9+/=_\-\.]+", re.IGNORECASE)
_SECRETS: Set[str] = set()


def register_secret(value: Optional[str]) -> None:
    """Register a secret (and its base64-decoded parts) for redaction."""
    if not value or len(value) < 6:
        return
    _SECRETS.add(value)
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except Exception:
        return
    if ":" in decoded:
        _SECRETS.add(decoded)
        for part in decoded.split(":", 1):
            if len(part) >= 8:
                _SECRETS.add(part)


def redact(text: str) -> str:
    """Replace registered secrets and ``ApiKey <token>`` with ``ApiKey ****``."""
    text = _APIKEY_RE.sub("ApiKey ****", text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        if secret in text:
            text = text.replace(secret, "****")
    return text


class RedactingFilter(logging.Filter):
    """Rewrites each record's message so secrets never reach a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
        except Exception:
            record.msg = redact(str(record.msg))
        record.args = None
        return True


class RedactingFormatter(logging.Formatter):
    """Redacts the fully formatted output (covers exception tracebacks)."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(
    verbose: bool = False,
    log_file: Optional[str] = None,
    stream: Optional[TextIO] = None,
    secrets: Iterable[str] = (),
) -> logging.Logger:
    """(Re)configure the ``mlws`` logger; returns it."""
    for s in secrets:
        register_secret(s)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    console = logging.StreamHandler(stream or sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(RedactingFormatter("%(message)s"))
    console.addFilter(RedactingFilter())
    logger.addHandler(console)
    if log_file:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(message)s"))
            fh.addFilter(RedactingFilter())
            logger.addHandler(fh)
        except OSError:
            logger.warning("WARN: cannot open log file %s", log_file)
    return logger


def get_logger() -> logging.Logger:
    """The shared ``mlws`` logger."""
    return logging.getLogger(LOGGER_NAME)


def say(message: str = "") -> None:
    """User-facing output line."""
    get_logger().info(message)


def warn(message: str) -> None:
    """User-facing warning line."""
    get_logger().warning("WARN: %s", message)
