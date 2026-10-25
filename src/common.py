"""Společné pomocné funkce: cesty, konfigurace, HTTP, ukládání dat."""
import datetime as dt
import json
import logging
import os
import re
import tempfile
from pathlib import Path

import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"
WEB = ROOT / "web"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tracker")

PROHLIZEC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)


def nacti_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def nacti_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        log.warning("Soubor %s je poškozený, začínám znovu.", path)
        return default


def uloz_json(path: Path, obj, kompaktni=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        if kompaktni:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def http_session(user_agent: str = PROHLIZEC_UA) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "POST"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"})
    return s


def ted() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def dnes() -> dt.date:
    return ted().date()


def iso_ted() -> str:
    return ted().strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_piid(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def norm_mod(s) -> str:
    s = re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    if s in ("", "0", "00", "000", "0000", "NONE"):
        return "0"
    return s
