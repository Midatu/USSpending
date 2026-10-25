"""Denní aktualizace seznamu burzovně obchodovaných společností (directory tickerů).

Zdroje:
  1) SEC EDGAR company_tickers_exchange.json: všechny firmy registrované u SEC
     (NYSE, Nasdaq, OTC, CBOE), včetně zahraničních firem s ADR v USA.
  2) zahranicni_tickery.csv: ručně udržovaný doplněk primárních zahraničních kotací
     (Londýn, Paříž, Frankfurt, Milán, Stockholm, Oslo, Tokio, Soul, Toronto, ...).
"""
import csv

from .common import DATA, ROOT, http_session, iso_ted, log, nacti_json, uloz_json

SEC_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SOUBOR = DATA / "tickery.json"


def stahni_sec(kontakt: str) -> list[dict]:
    s = http_session(user_agent=kontakt)
    s.headers["Accept"] = "application/json"
    r = s.get(SEC_URL, timeout=60)
    r.raise_for_status()
    j = r.json()
    pole = j["fields"]  # ["cik","name","ticker","exchange"]
    vystup = []
    for radek in j["data"]:
        z = dict(zip(pole, radek))
        if not z.get("ticker"):
            continue
        vystup.append({
            "ticker": str(z["ticker"]).upper(),
            "nazev": z.get("name") or "",
            "burza": z.get("exchange") or "",
            "cik": z.get("cik"),
            "zdroj": "SEC",
        })
    return vystup


def nacti_zahranicni() -> list[dict]:
    vystup = []
    cesta = ROOT / "zahranicni_tickery.csv"
    if not cesta.exists():
        return vystup
    with open(cesta, encoding="utf-8") as f:
        for z in csv.DictReader((r for r in f if not r.startswith("#")), delimiter=";"):
            t = (z.get("ticker") or "").strip()
            if t:
                vystup.append({"ticker": t.upper(), "nazev": (z.get("nazev") or "").strip(),
                               "burza": (z.get("burza") or "").strip(), "cik": None,
                               "zdroj": "ruční seznam"})
    return vystup


def aktualizuj(cfg: dict) -> dict:
    stary = nacti_json(SOUBOR, {"seznam": []})
    try:
        sec = stahni_sec(cfg.get("sec_kontakt", "GovContractTracker admin@example.com"))
        log.info("SEC: staženo %d tickerů", len(sec))
    except Exception as e:  # noqa: BLE001
        log.error("SEC seznam se nepodařilo stáhnout (%s), ponechávám předchozí.", e)
        sec = [t for t in stary.get("seznam", []) if t.get("zdroj") == "SEC"]
    vse, videno = [], set()
    for t in sec + nacti_zahranicni():
        if t["ticker"] in videno:
            continue
        videno.add(t["ticker"])
        vse.append(t)
    vysledek = {"aktualizace": iso_ted(), "pocet": len(vse), "seznam": vse}
    uloz_json(SOUBOR, vysledek, kompaktni=True)
    log.info("Directory tickerů uložen: %d položek", len(vse))
    return vysledek


def nacti() -> dict:
    return nacti_json(SOUBOR, {"seznam": []})
