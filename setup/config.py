"""Configuration resolution.

Order: CLI flags, environment variables, ``.env`` in the repo root, then an
interactive prompt (``getpass`` for the key) when stdin is a TTY.
"""

import getpass
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """Invalid or missing configuration."""


@dataclass
class Config:
    """Resolved settings. ``api_key`` must never be logged."""

    kibana_url: str
    api_key: str
    es_url: str
    alert_email: Optional[str] = None
    days: int = 28
    scale: float = 1.0
    root: Path = REPO_ROOT

    def client_dict(self) -> Dict[str, Any]:
        """Picklable dict for worker processes (contains the key; never log it)."""
        return {"es_url": self.es_url, "api_key": self.api_key}

    def __repr__(self) -> str:  # keep the key out of accidental logging
        return "Config(kibana_url=%r, es_url=%r, api_key='****')" % (self.kibana_url, self.es_url)


def parse_dotenv(path: Path) -> Dict[str, str]:
    """Parse simple ``KEY=VALUE`` lines (comments, ``export``, quotes supported)."""
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key.strip()] = value
    return out


def normalize_url(url: str) -> str:
    """Add https:// if missing and strip trailing slashes."""
    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        url = "https://" + url
    return url.rstrip("/")


def derive_es_url(kibana_url: str) -> str:
    """Kibana URL with ``.kb.`` replaced by ``.es.``."""
    if ".kb." not in kibana_url:
        raise ConfigError(
            "cannot derive the Elasticsearch URL from %s (no '.kb.' in the host); "
            "set ELASTIC_ES_URL or pass --es-url" % kibana_url
        )
    return kibana_url.replace(".kb.", ".es.", 1)


def clean_api_key(key: str) -> str:
    """Strip whitespace and a leading ``ApiKey `` and reject the unencoded form."""
    key = key.strip()
    if key.lower().startswith("apikey "):
        key = key[7:].strip()
    if ":" in key:
        raise ConfigError(
            "ELASTIC_API_KEY looks like the 'id:secret' form; use the 'encoded' value shown when the key was created"
        )
    return key


def load_config(
    args: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
    root: Path = REPO_ROOT,
    need_key: bool = True,
    interactive: Optional[bool] = None,
) -> Config:
    """Resolve a :class:`Config` from flags, env, .env and prompts."""
    env = os.environ if env is None else env
    dotenv = parse_dotenv(root / ".env")
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()

    def pick(flag: str, var: str) -> Optional[str]:
        val = getattr(args, flag, None) if args is not None else None
        if val not in (None, ""):
            return str(val)
        for src in (env, dotenv):
            if src.get(var):
                return src[var]
        return None

    kibana = pick("kibana_url", "ELASTIC_KIBANA_URL")
    if not kibana and interactive:
        try:
            kibana = input("Paste your project's Kibana URL: ").strip()
        except EOFError:
            kibana = ""
    if not kibana:
        raise ConfigError("ELASTIC_KIBANA_URL is required: set it as an environment variable, put it in a .env file, pass --kibana-url, or run in a terminal to be asked for it. It looks like https://NAME.kb.REGION.PROVIDER.elastic.cloud")
    kibana = normalize_url(kibana)
    if ".es." in kibana and ".kb." not in kibana:
        # A common slip: the Elasticsearch endpoint pasted where the Kibana URL belongs.
        print("note: ELASTIC_KIBANA_URL looks like an Elasticsearch URL (.es.); using the matching Kibana URL (.kb.)",
              file=sys.stderr)
        kibana = kibana.replace(".es.", ".kb.", 1)

    key = pick("api_key", "ELASTIC_API_KEY")
    if not key and need_key and interactive:
        try:
            key = getpass.getpass("Paste your API key (input is hidden): ")
        except EOFError:
            key = ""
    if need_key and not key:
        raise ConfigError("ELASTIC_API_KEY is required: set it as an environment variable, put it in a .env file, or run in a terminal to be asked for it (create one in Kibana under Management > API keys)")
    key = clean_api_key(key) if key else ""

    es = pick("es_url", "ELASTIC_ES_URL")
    es = normalize_url(es) if es else derive_es_url(kibana)

    def num(flag: str, var: str, default: Any, cast: Any) -> Any:
        raw = pick(flag, var)
        if raw is None:
            return default
        try:
            return cast(raw)
        except ValueError:
            raise ConfigError("%s must be a number, got %r" % (var, raw))

    days = num("days", "MLWS_DAYS", 28, int)
    scale = num("scale", "MLWS_SCALE", 1.0, float)
    if days < 2 or days > 90:
        raise ConfigError("MLWS_DAYS must be between 2 and 90")
    if scale <= 0 or scale > 10:
        raise ConfigError("MLWS_SCALE must be > 0 and <= 10")
    email = pick("alert_email", "ALERT_EMAIL")
    return Config(kibana, key, es, email or None, days, scale, root)
