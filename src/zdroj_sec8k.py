"""SEC 8-K: hlášení, ve kterých firmy samy oznamují získané vládní kontrakty.

Používá fulltextové vyhledávání EDGAR (zdarma, bez klíče). Výhoda: ticker je jistý,
protože hlášení podává sama firma. Částka a popis se z textu tiskové zprávy vyčítají
automaticky, proto jde o orientační údaj (odkaz vede na původní dokument).
"""
import datetime as dt
import re
import time

from bs4 import BeautifulSoup

from .common import http_session, log, norm_piid
from .zdroj_dod import CISLO

EFTS = "https://efts.sec.gov/LATEST/search-index"
DOTAZY = [
    '"awarded a contract"', '"has been awarded"', '"was awarded"', '"contract award"', '"task order"',
    '"Department of War"', '"Department of Defense" contract', '"U.S. Army" contract', '"U.S. Navy" contract',
    '"U.S. Air Force" contract', '"NASA" contract', '"Missile Defense Agency"',
]
URAD = re.compile(
    r"Department of (?:Defense|War|the (?:Army|Navy|Air Force)|Energy|Homeland Security|Veterans Affairs"
    r"|State|Health and Human Services|Transportation|Justice|Commerce)|U\.S\. (?:Army|Navy|Air Force|Space Force"
    r"|Marine Corps|Coast Guard|Government|Special Operations Command|Department)|\bU\.S\. government\b"
    r"|federal government|Missile Defense Agency|DARPA|NASA|Defense Logistics Agency|General Services Administration"
    r"|Space Development Agency|Space Systems Command|Naval (?:Sea|Air) Systems Command|Army Contracting Command"
    r"|Air Force Life Cycle Management Center|Pentagon|National Institutes of Health|\bFAA\b|\bCDC\b|\bDHS\b|\bDoD\b|\bDoW\b"
)
KONTRAKT = re.compile(r"\b(?:award(?:ed)?|contract|task order|delivery order|IDIQ|option)\b", re.I)
CASTKA = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million|thousand|bn|mn|[BMK]\b)?", re.I)
NASOBEK = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6, "thousand": 1e3, "k": 1e3}
TICKER = re.compile(r"\(([A-Z0-9.\-]{1,10}(?:,\s*[A-Z0-9.\-]{1,10})*)\)\s*\(CIK")


def _castky(veta: str) -> list[float]:
    vystup = []
    for m in CASTKA.finditer(veta):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        vystup.append(v * NASOBEK.get((m.group(2) or "").lower(), 1))
    return vystup


ZKRATKA = re.compile(
    r"(?:\b[A-Z]\.|\b(?:Inc|Corp|Co|Ltd|No|Nos|Mr|Ms|Dr|St|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
    r"|Va|Md|Calif|Fla|Ala|Mass|Conn|Ariz|Colo|Wash|Penn|Tex|Gen|Lt|Col|Adm|approx|vs)\.)$")


def _vety(text: str) -> list[str]:
    kusy, vety = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", text), []
    for k in kusy:
        if vety and ZKRATKA.search(vety[-1]):
            vety[-1] += " " + k
        else:
            vety.append(k)
    return vety


def vytez_text(text: str, min_castka: float):
    """Najde nejvýznamnější větu o vládním kontraktu. Vrací (castka, popis, cisla_kontraktu, zadavatel) nebo None."""
    text = re.sub(r"\s+", " ", text)
    nejlepsi = None
    for veta in _vety(text):
        if len(veta) > 700 or not KONTRAKT.search(veta) or not URAD.search(veta):
            continue
        castky = [c for c in _castky(veta) if c >= min_castka]
        if not castky:
            continue
        if not nejlepsi or max(castky) > nejlepsi[0]:
            nejlepsi = (max(castky), veta.strip(), URAD.search(veta).group(0))
    if not nejlepsi:
        return None
    popis = nejlepsi[1]
    m = re.match(r"^.{0,140}?\s(?:--|\u2013|\u2014)\s*", popis)  # odstraň úvodní "MĚSTO, datum /agentura/ --"
    if m:
        popis = popis[m.end():]
    if len(popis) > 300:
        popis = popis[:297] + "..."
    return nejlepsi[0], popis, sorted({norm_piid(c) for c in CISLO.findall(text)}), nejlepsi[2]


def stahni(cfg: dict, stav: dict) -> list[dict]:
    s = http_session(user_agent=cfg.get("sec_kontakt", "GovContractTracker admin@example.com"))
    s.headers["Accept"] = "application/json"
    min_castka = float(cfg.get("min_castka", 1000000))
    dnes = dt.date.today()
    od = dnes - dt.timedelta(days=int(cfg.get("sec_dny_zpet", 3)))
    zpracovane = stav.setdefault("sec_dokumenty", {})
    limit = int(cfg.get("sec_max_dokumentu_za_beh", 120))

    zasahy, videne = [], set()
    for dotaz in DOTAZY:
        nacteno = 0
        for _ in range(5):  # max 5 stránek na jeden dotaz
            r = s.get(EFTS, params={"q": dotaz, "forms": "8-K", "dateRange": "custom",
                                    "startdt": od.isoformat(), "enddt": dnes.isoformat(), "from": nacteno},
                      timeout=60)
            r.raise_for_status()
            j = (r.json().get("hits") or {})
            hity = j.get("hits") or []
            celkem = (j.get("total") or {}).get("value", 0)
            for h in hity:
                if h.get("_id") not in videne:
                    videne.add(h.get("_id"))
                    zasahy.append(h)
            nacteno += len(hity)
            time.sleep(0.15)
            if not hity or nacteno >= celkem:
                break
    # tiskové zprávy (příloha EX-99) a hlavní dokument 8-K mají přednost před ostatními přílohami
    zasahy.sort(key=lambda h: 0 if str((h.get("_source") or {}).get("file_type", "")).upper().startswith(("EX-99", "8-K")) else 1)

    vystup, stazeno = [], 0
    for h in zasahy:
        zdroj = h.get("_source") or {}
        dok_id = h.get("_id") or ""
        adsh = zdroj.get("adsh") or dok_id.split(":")[0]
        soubor = dok_id.split(":", 1)[1] if ":" in dok_id else ""
        ciks = zdroj.get("ciks") or []
        if not (adsh and soubor and ciks) or dok_id in zpracovane:
            continue
        if stazeno >= limit:
            break
        url = f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{adsh.replace('-', '')}/{soubor}"
        try:
            time.sleep(0.15)  # SEC povoluje max. 10 dotazů za sekundu
            rr = s.get(url, timeout=60)
            rr.raise_for_status()
            stazeno += 1
            zpracovane[dok_id] = dnes.isoformat()
            vysledek = vytez_text(BeautifulSoup(rr.content, "lxml").get_text(" "), min_castka)
        except Exception as e:  # noqa: BLE001
            log.warning("8-K %s selhal: %s", url, e)
            continue
        if not vysledek:
            continue
        castka, popis, cisla, urad = vysledek
        jmeno = (zdroj.get("display_names") or [""])[0]
        m = TICKER.search(jmeno)
        ticker = m.group(1).split(",")[0].strip() if m else ""
        vystup.append({
            "piid": "8K" + adsh.replace("-", ""), "mod": "0", "typ": "Hlášení 8-K",
            "datum": (zdroj.get("file_date") or dnes.isoformat())[:10],
            "spolecnost": re.sub(r"\s*\(.*$", "", jmeno).strip().title(),
            "ticker_pevny": ticker, "castka_8k": castka, "popis_8k": popis,
            "odkaz_8k": url, "cisla_kontraktu": cisla, "agentura": urad,
        })
    # stav držíme malý
    if len(zpracovane) > 3000:
        for k in sorted(zpracovane, key=zpracovane.get)[:-3000]:
            zpracovane.pop(k)
    log.info("SEC 8-K: %d dokumentů z %d dotazů, staženo %d, %d hlášení o kontraktech",
             len(zasahy), len(DOTAZY), stazeno, len(vystup))
    return vystup
