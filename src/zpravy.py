"""Zprávy o firmách s tickerem z listu Kontrakty: sběr, sentiment, denní souhrny a upozornění.

Zdroje (oba jsou rozhraní určená pro programy, žádné stahování webových stránek):
  - GDELT DOC 2.0 API (https://www.gdeltproject.org): otevřená databáze světových zpráv, podle podmínek
    použitelná bez omezení s uvedením zdroje a odkazem (na webu je uvedeno), hledá se podle názvu firmy
  - Finnhub company-news API (https://finnhub.io): zprávy podle tickeru, bezplatný osobní klíč
    v GitHub Secrets (FINNHUB_API_KEY). Klíč se posílá v hlavičce, nikdy v adrese, takže se nedostane do logu.
    Dokud v config.yaml není zpravy_finnhub_verejne: true, titulky a odkazy z Finnhub se nezveřejňují
    (ani ve veřejné větvi data); zprávy se jen započítají do sentimentu a trendů.

Autorská práva a slušné chování:
  - ukládá a zveřejňuje se jen titulek, odkaz na původní článek, název vydavatele, datum
    a naše vlastní vyhodnocení (sentiment, kategorie); texty článků ani úryvky z nich se neukládají,
  - krátký popisek (Finnhub) se použije jen v paměti pro výpočet sentimentu a hned se zahodí,
  - u GDELT se před dotazy kontroluje robots.txt a dodržuje se pauza mezi dotazy, kterou GDELT žádá,
  - program se identifikuje vlastním jménem a odkazem na repozitář (žádné maskování).

Data ve větvi "data":
  zpravy/clanky/RRRR-MM.json  jednotlivé zprávy (drží se zpravy_uchovavat_dni, výchozí 180 dní);
                              záznamy jen pro statistiku (bez titulku) se drží 40 dní
  zpravy/denni.json           denní souhrn za ticker [počet, součet sentimentu, negativní, pozitivní]
                              (pár bajtů na den, drží se trvale, z něj se počítají trendy)
  zpravy/upozorneni.json      upozornění na trendy (aktivní i nedávno skončená)
  zpravy/kontrola.json        kdy byl který ticker naposledy zkontrolován v kterém zdroji
  zpravy/kapitalizace.json    tržní kapitalizace (pro porovnání s výší kontraktu), 1x denně
"""
import datetime as dt
import hashlib
import os
import re
import time
from collections import defaultdict
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .ceny import mena
from .common import DATA, ROOT, castka_zaznamu, iso_ted, log, nacti_json, uloz_json

SLOZKA = DATA / "zpravy"
SLOZKA_CLANKY = SLOZKA / "clanky"
SOUBOR_DENNI = SLOZKA / "denni.json"
SOUBOR_UPOZ = SLOZKA / "upozorneni.json"
SOUBOR_KONTROLA = SLOZKA / "kontrola.json"
SOUBOR_KAP = SLOZKA / "kapitalizace.json"

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
VERZE_DAT = 2  # zvýšení smaže uložené zprávy a stáhne je znovu (např. po zpřísnění filtrů)
STATISTIKA_DNI = 40  # jak dlouho se drží záznamy jen pro statistiku a za kolik dní se přepočítávají souhrny

POZITIVNI, NEGATIVNI = 0.25, -0.25  # hranice sentimentu (-1 až +1) pro pozitivní / negativní zprávu


# ---------- sentiment ----------

# Úpravy obecného slovníku VADER pro finanční zprávy (vlastní seznam, stupnice -4 až +4).
# Vojenské a obranné výrazy jsou u zbrojních firem běžný popis produktu, ne špatná zpráva,
# proto je neutralizujeme (jinak by "missile", "destroyer" nebo "war" táhly sentiment dolů).
FINANCNI_SLOVNIK = {
    # pozitivní
    "awarded": 1.8, "award": 1.8, "awards": 1.6, "wins": 2.0, "win": 1.8, "won": 1.8, "secures": 1.6, "secured": 1.6,
    "beats": 1.6, "beat": 1.3, "tops": 1.2, "exceeds": 1.5, "outperform": 2.0, "outperforms": 2.0,
    "upgrade": 2.0, "upgrades": 2.0, "upgraded": 2.0, "raises": 1.0, "raised": 0.8, "boosts": 1.4, "boost": 1.2,
    "surge": 2.0, "surges": 2.0, "surged": 2.0, "soar": 2.2, "soars": 2.2, "soared": 2.2, "jumps": 1.5,
    "jumped": 1.5, "rally": 1.5, "rallies": 1.5, "climbs": 1.2, "gains": 1.3, "rises": 1.0, "rose": 1.0,
    "record": 1.0, "profit": 1.3, "profitable": 1.6, "growth": 1.2, "expands": 1.1, "expansion": 1.0,
    "bullish": 2.2, "buyback": 1.2, "repurchase": 0.8, "dividend": 0.6, "approval": 1.4, "approved": 1.4,
    "milestone": 1.2, "backlog": 0.6, "momentum": 1.0, "strong": 1.5, "upbeat": 1.8,
    # negativní
    "downgrade": -2.2, "downgrades": -2.2, "downgraded": -2.2, "underperform": -1.8, "bearish": -2.2,
    "miss": -1.5, "misses": -1.6, "missed": -1.6, "plunge": -2.6, "plunges": -2.6, "plunged": -2.6,
    "tumble": -2.2, "tumbles": -2.2, "tumbled": -2.2, "slump": -2.0, "slumps": -2.0, "sinks": -1.8, "sank": -1.8,
    "falls": -1.2, "fell": -1.2, "drops": -1.2, "dropped": -1.2, "slides": -1.3, "declines": -1.2, "decline": -1.2,
    "lawsuit": -2.0, "lawsuits": -2.0, "sued": -2.0, "sues": -1.8, "probe": -1.8, "investigation": -1.8,
    "investigating": -1.6, "subpoena": -2.0, "indicted": -2.8, "charged": -1.8, "settlement": -0.8,
    "recall": -1.8, "recalls": -1.8, "layoffs": -2.0, "layoff": -2.0, "cuts": -1.3, "slashes": -1.8,
    "loss": -1.6, "losses": -1.6, "bankruptcy": -3.2, "default": -1.8, "delay": -1.3, "delays": -1.3,
    "delayed": -1.3, "protest": -1.0, "terminated": -2.0, "termination": -2.0, "cancels": -1.8,
    "canceled": -1.8, "cancelled": -1.8, "overrun": -1.8, "overruns": -1.8, "penalty": -1.8, "fined": -2.0,
    "halted": -1.8, "halts": -1.8, "scrutiny": -1.3, "breach": -2.0, "hack": -2.0, "hacked": -2.2,
    "outage": -1.5, "weak": -1.5, "weaker": -1.5, "lowers": -1.2, "lowered": -1.2, "warns": -1.6,
    "shortfall": -1.8, "writedown": -2.0, "impairment": -1.8, "downturn": -1.8, "selloff": -2.0,
    # neutralizace (popis oboru, názvy, ne nálada)
    **{w: 0.0 for w in (
        "war", "wars", "warfare", "attack", "attacks", "missile", "missiles", "weapon", "weapons", "defense",
        "defence", "defend", "defended", "combat", "kill", "killed", "kills", "killing", "lethal", "deadly",
        "destroy", "destroyer", "destroyers", "threat", "threats", "fight", "fighter", "fighters", "battle",
        "battlefield", "bomb", "bombs", "bomber", "bombers", "enemy", "conflict", "military", "strike", "strikes",
        "assault", "hostile", "gun", "guns", "shoot", "shot", "security", "secure", "protect", "protection",
        "support", "supports", "supported", "care", "trust", "united", "dynamics", "save", "rescue", "army",
        "navy", "force", "forces", "danger", "dangerous", "dead", "death", "intelligence", "health",
    )},
}

_analyzer = None


def _analyzator():
    global _analyzer
    if _analyzer is None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        _analyzer = SentimentIntensityAnalyzer()
        _analyzer.lexicon.update(FINANCNI_SLOVNIK)
    return _analyzer


def sentiment(text: str, nazvy: list[str]) -> float:
    """Sentiment -1 až +1. Názvy firem se před hodnocením nahradí neutrálním slovem
    (např. "Northern Trust" nebo "UnitedHealth Care" by jinak zkreslily výsledek)."""
    for n in sorted(nazvy, key=len, reverse=True):
        if n:
            text = re.sub(re.escape(n), "Company", text, flags=re.I)
    return round(_analyzator().polarity_scores(text)["compound"], 3)


KATEGORIE = [
    ("Soud a vyšetřování", r"lawsuit|\bsue[sd]?\b|probe|investigat|subpoena|indict|class action|fraud|\bdoj\b|"
                           r"justice department|settle|whistleblow|charged"),
    ("Analytici", r"upgrade|downgrade|price target|\brating\b|analyst|overweight|underweight|outperform|"
                  r"underperform|\bbuy\b.*\brating|initiat\w* coverage"),
    ("Výsledky", r"earnings|results|quarter|\bq[1-4]\b|revenue|\beps\b|guidance|outlook|profit|forecast"),
    ("Kontrakt", r"contract|award|task order|\bidiq\b|order from|selected by|wins .*(deal|order|pact)"),
    ("Fúze a akvizice", r"acqui|merger|\bmerge|buyout|takeover|to buy|divest|spin.?off|stake in"),
    ("Propouštění", r"layoff|job cuts|cut\w* jobs|restructur|workforce reduction"),
    ("Vedení firmy", r"\bceo\b|\bcfo\b|chief executive|resign|appoint|steps down|board of directors|names new"),
    ("Dividenda a odkup", r"dividend|buyback|repurchase"),
]


# Automaticky generované zprávy bez informační hodnoty (ty by jinak zkreslovaly sentiment i počty):
#  - hlášení o nákupu/prodeji akcií fondy ("XY LLC Buys 1,087 Shares of ...", "... Stock Acquired by ...")
#  - srovnávací šablony ("A versus B Head to Head Contrast")
#  - spotřebitelské slevy a akce ("Save $220 on ... at Verizon")
SUM = re.compile("|".join([
    r"\b(shares|stake|position|holdings|stock)\b[^|]*\b(bought|sold|acquired|purchased|trimmed|lowered|raised|boosted|"
    r"increased|decreased|reduced|cut|added|grown|lifted|sold off)\b by\b",
    r"\b(buys|sells|acquires|purchases|trims|boosts|lowers|raises|increases|decreases|reduces|cuts|adds|lifts|grows|"
    r"takes|makes|initiates|establishes|invests)\b[^|]{0,40}\b(shares|stake|position|holdings|investment)\b[^|]{0,25}\b(of|in)\b",
    r"\bnew \$?[\d.,]+ ?(k|m|million|billion)? ?(investment|position|stake) in\b",
    r"\bhas \$?[\d.,]+ ?(k|m|million|billion)? ?(stock )?(holdings|position|stake|investment) in\b",
    r"\b13f\b", r"head to head", r"\b(contrasting|comparing)\b.*\b(and|&)\b", r"\bcritical (comparison|survey|review)\b",
    r"\bfinancial comparison\b", r"side.by.side comparison",
    r"\bsave (a huge |big |up to |over )?\$", r"\$[\d,.]+ off\b", r"\d+% off\b", r"black friday|cyber monday|prime day",
    r"\bcoupon|\bpromo code|\bat best buy\b|\bdeal of the day\b",
]), re.I)
# výjimka: prodeje a nákupy akcií vedením firmy ("CEO Sells 1,100 Shares of Company Stock") jsou užitečná informace
INSIDER = re.compile(r"\b(ceo|cfo|coo|cto|director|insider|chairman|president|evp|svp|officer|executive)\b", re.I)


def je_sum(titulek: str) -> bool:
    return bool(SUM.search(titulek)) and not INSIDER.search(titulek)


def cisty_titulek(t: str) -> str:
    """GDELT vrací titulky s mezerami kolem interpunkce ("AT & T Drops 4 %, Verizon ( NYSE : VZ )")."""
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+([,.:;?!%)])", r"\1", t)
    for _ in range(2):  # 1 . 6 . 1 -> 1.6.1
        t = re.sub(r"(\d)\. (\d)", r"\1.\2", t)
    t = re.sub(r"(\d), (\d{3})\b", r"\1,\2", t)
    t = re.sub(r"\$ (\d)", r"$\1", t)
    t = re.sub(r"\b([A-Z]{1,3}) & ([A-Z]{1,3})\b", r"\1&\2", t)  # AT & T -> AT&T
    t = re.sub(r"\b(\w{1,3}) - (\w)", r"\1-\2", t)                # T - Mobile, TP - Link, 16 - Pin
    t = re.sub(r"(\w) - ([a-z0-9])", r"\1-\2", t)                 # third - quarter -> third-quarter
    return t


def kategorie(titulek: str) -> str:
    t = titulek.lower()
    for nazev, vzor in KATEGORIE:
        if re.search(vzor, t):
            return nazev
    return "Ostatní"


# ---------- názvy firem: hledání a ověření relevance ----------

PRAVNI = {"corp", "corporation", "inc", "incorporated", "co", "company", "ltd", "limited", "plc", "llc", "lp",
          "holdings", "holding", "group", "the", "sa", "nv", "ag", "se", "asa", "ab", "publ", "de", "class", "a",
          "b", "adr", "n.v.", "s.a.", "oyj", "spa", "s.p.a.", "kk", "trust"}
OBECNA = {"general", "american", "united", "international", "national", "science", "applications", "global",
          "systems", "technologies", "technology", "first", "energy", "solutions", "services", "industries",
          "federal", "digital", "advanced", "applied", "data", "health", "healthcare", "communications", "power",
          "network", "networks", "security", "defense", "aerospace", "engineering", "management", "capital",
          "financial", "resources", "electric", "motors", "pacific", "atlantic", "northern", "southern",
          "western", "eastern", "royal", "standard", "universal", "consolidated", "integrated", "dynamics",
          "new"}
# obecná slova na konci názvu, která se v titulcích zpráv obvykle vynechávají ("Huntington Ingalls")
KONCOVKY = {"industries", "international", "solutions", "technologies", "technology", "systems", "services",
            "enterprises", "worldwide", "global", "communications", "incorporated", "holdings", "group"}
# tickery, které jsou zároveň běžná anglická slova nebo zkratky (v titulku by dávaly falešné shody)
TICKER_SLOVA = {"A", "ALL", "ARE", "BIG", "CAN", "CAR", "CAT", "DE", "EAT", "FOR", "GO", "HAS", "IT", "J", "KEY",
                "LOW", "MAN", "NOW", "ON", "ONE", "OUT", "PAY", "RUN", "SEE", "SO", "T", "TV", "TWO", "UP", "US",
                "WELL", "AI", "CEO", "IPO", "GDP", "USA", "NEW", "EV", "ESG", "AM", "PM"}


def jadro_nazvu(nazev: str) -> str:
    """'LOCKHEED MARTIN CORP' -> 'lockheed martin', 'Saab AB (publ)' -> 'saab'."""
    n = re.sub(r"/[A-Z]{2}/?", " ", nazev or "")           # např. /DE/
    n = re.sub(r"\(.*?\)", " ", n).replace(",", " ").replace(".", " ")
    slova = [w for w in n.lower().split() if w not in PRAVNI]
    while len(slova) > 1 and slova[-1] in PRAVNI | KONCOVKY | {"&", "and"}:
        slova.pop()
    while slova and slova[-1] in PRAVNI | {"&", "and"}:
        slova.pop()
    return " ".join(slova)


class Firma:
    def __init__(self, ticker: str, nazev: str, dalsi: list[str], prvni_slova: dict | None = None,
                 rucne: list[str] | None = None, vyloucit: list[str] | None = None):
        self.ticker = ticker
        # slova, při jejichž výskytu v titulku zpráva o této firmě není (např. RTX: nvidia, geforce)
        self.vyloucit = [w.strip().lower() for w in (vyloucit or []) if w.strip()]
        # hvězdička na konci = začátek slova ("rtx 5*" vyloučí "RTX 5090")
        casti = [re.escape(w[:-1]) if w.endswith("*") else re.escape(w) + r"(?![a-z0-9])" for w in self.vyloucit]
        self.vyloucit_vzor = re.compile(r"(?<![a-z0-9])(" + "|".join(casti) + ")") if casti else None
        self.nazev = nazev
        self.jadro = jadro_nazvu(nazev)
        if rucne:  # ruční nastavení v zpravy_nazvy.csv má přednost před automatikou
            self.klice = {k.strip().lower() for k in rucne if len(k.strip()) >= 2}
            self.jadro = sorted(self.klice, key=len)[0] if self.klice else self.jadro
            prvni_slova = {self.jadro.split()[0]: 99} if self.jadro else {}
        else:
            self.klice = {self.jadro} if len(self.jadro) >= 3 else set()
            for d in dalsi:
                j = jadro_nazvu(d)
                if len(j) >= 4 and re.fullmatch(r"[a-z0-9&' ]+", j):
                    self.klice.add(j)
        # samotné první slovo ("Lockheed") jen tehdy, když jím nezačíná název žádné jiné firmy na burze
        # (např. "Huntington" by zachytil i Huntington Bancshares)
        prvni = self.jadro.split()[0] if self.jadro else ""
        if (len(prvni) >= 5 and prvni not in OBECNA and " " in self.jadro
                and (prvni_slova or {}).get(prvni, 1) <= 1):
            self.klice.add(prvni)
        # delší klíč, který obsahuje kratší (např. "kbr wyle" a "kbr"), je zbytečný
        self.klice = {k for k in self.klice if not any(j != k and j in k for j in self.klice)}
        zaklad = ticker.split(".")[0].split("-")[0]
        self.ticker_vzor = None
        if len(zaklad) >= 2 and zaklad not in TICKER_SLOVA:
            # ticker se v titulku počítá jen v jednoznačném tvaru: "$LMT", "(NYSE: LMT)", "(LMT)", "LMT stock"
            z = re.escape(zaklad)
            self.ticker_vzor = re.compile(rf"\${z}\b|\b(NYSE|NASDAQ|Nasdaq|NYSEAMERICAN|AMEX|OTC)\s*:\s*{z}\b|"
                                          rf"\({z}\)|\b{z}\s+(stock|shares)\b")
        self.vzory = [re.compile(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])") for k in sorted(self.klice, key=len, reverse=True)]

    def v_titulku(self, titulek: str) -> bool:
        t = titulek.lower().replace("’", "'")
        if self.vyloucit_vzor and self.vyloucit_vzor.search(t):
            return False
        if any(v.search(t) for v in self.vzory):
            return True
        return bool(self.ticker_vzor and self.ticker_vzor.search(titulek))

    def dotaz_gdelt(self) -> str:
        klice = sorted(self.klice, key=len)[:3]
        casti = " OR ".join(f'"{k}"' if " " in k or not k.isalnum() else k for k in klice)
        minus = "".join(f" -{w}" for w in [w for w in self.vyloucit if w.isalnum()][:6])
        return (f"({casti})" if len(klice) > 1 else casti) + minus + " sourcelang:english"


# ---------- stahování ----------

class Stahovac:
    def __init__(self, pauza: float, robots: bool = True):
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        odkaz = f"; +https://github.com/{repo}" if repo else ""
        self.s = requests.Session()
        self.s.mount("https://", HTTPAdapter(max_retries=Retry(total=1, backoff_factor=3, status_forcelist=(500, 502, 503, 504),
                                                                 allowed_methods=frozenset(["GET"]))))
        self.s.headers.update({"User-Agent": f"Mozilla/5.0 (compatible; GovContractTracker/1.0{odkaz})",
                               "Accept-Language": "en-US,en;q=0.9"})
        self.s.headers["Accept"] = "application/json"
        self.pauza, self.robots = pauza, robots
        self.roboti: dict[str, RobotFileParser | None] = {}
        self.posledni = 0.0

    def _cekej(self):
        zbyva = self.pauza - (time.monotonic() - self.posledni)
        if zbyva > 0:
            time.sleep(zbyva)
        self.posledni = time.monotonic()

    def povoleno(self, url: str) -> bool:
        """Kontrola robots.txt. Když robots.txt nejde načíst kvůli chybě serveru, raději se dotaz neprovede."""
        p = urlparse(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self.roboti:
            rp = RobotFileParser()
            try:
                self._cekej()
                r = self.s.get(host + "/robots.txt", timeout=20)
                if r.status_code >= 500:
                    rp = None
                elif r.status_code >= 400:
                    rp.parse([])            # robots.txt neexistuje = bez omezení
                else:
                    rp.parse(r.text.splitlines())
            except Exception as e:  # noqa: BLE001
                log.warning("robots.txt pro %s nejde načíst: %s", host, e)
                rp = None
            self.roboti[host] = rp
        rp = self.roboti[host]
        if rp is None:
            raise PermissionError(f"robots.txt serveru {p.netloc} nejde načíst, dotaz se z opatrnosti neprovedl.")
        if not rp.can_fetch("GovContractTracker", url):
            raise PermissionError(f"Server {p.netloc} automatický odběr zakazuje (robots.txt).")
        return True

    def json(self, url: str, params: dict, hlavicky: dict | None = None):
        if self.robots:
            self.povoleno(url)
        self._cekej()
        r = self.s.get(url, params=params, headers=hlavicky or {}, timeout=30)
        if r.status_code in (401, 403):
            raise PermissionError(f"{urlparse(url).netloc} odmítl přístup (chyba {r.status_code}), zkontroluj klíč.")
        if r.status_code == 429:
            raise RuntimeError("překročen limit dotazů (429), zkusí se při dalším běhu")
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            # GDELT vrací při chybném dotazu nebo příliš častých dotazech prostý text
            raise RuntimeError(r.text.strip()[:160] or "neplatná odpověď") from None


def _id(titulek: str) -> str:
    norm = " ".join(re.findall(r"[a-z0-9]+", titulek.lower()))
    return hashlib.sha1(norm.encode()).hexdigest()[:14]


def _gdelt_polozky(j) -> list[dict]:
    vystup = []
    for a in (j or {}).get("articles", []) or []:
        try:
            cas = dt.datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError):
            continue
        vystup.append({"titulek": a.get("title") or "", "url": a.get("url") or "", "popis": "", "cas": cas,
                       "vydavatel": (a.get("domain") or "").removeprefix("www.")})
    return vystup


def _finnhub_polozky(j) -> list[dict]:
    vystup = []
    for a in j if isinstance(j, list) else []:
        try:
            cas = dt.datetime.fromtimestamp(int(a["datetime"]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (KeyError, ValueError, TypeError):
            continue
        vystup.append({"titulek": a.get("headline") or "", "url": a.get("url") or "", "popis": a.get("summary") or "",
                       "cas": cas, "vydavatel": a.get("source") or ""})
    return vystup


# ---------- ukládání ----------

def nacti_clanky() -> dict:
    db = {}
    if SLOZKA_CLANKY.exists():
        for soubor in sorted(SLOZKA_CLANKY.glob("*.json")):
            db.update(nacti_json(soubor, {}))
    return db


def uloz_clanky(db: dict):
    po_mesicich = defaultdict(dict)
    for k, z in db.items():
        po_mesicich[z["cas"][:7]][k] = z
    SLOZKA_CLANKY.mkdir(parents=True, exist_ok=True)
    for mesic, zaznamy in po_mesicich.items():
        uloz_json(SLOZKA_CLANKY / f"{mesic}.json", zaznamy, kompaktni=True)
    for soubor in SLOZKA_CLANKY.glob("*.json"):
        if soubor.stem not in po_mesicich:
            soubor.unlink()


# ---------- sledované firmy ----------

def nacti_rucni_nazvy() -> dict[str, tuple[list[str], list[str]]]:
    """zpravy_nazvy.csv: ticker;názvy;vyloučit (názvy a vylučovací slova oddělená svislítkem)."""
    vystup = {}
    try:
        with open(ROOT / "zpravy_nazvy.csv", encoding="utf-8") as f:
            for radek in f:
                radek = radek.strip()
                if not radek or radek.startswith("#") or radek.lower().startswith("ticker;"):
                    continue
                t, nazvy, vyloucit = (radek.split(";") + ["", ""])[:3]
                if t.strip():
                    vystup[t.strip().upper()] = ([n for n in nazvy.split("|") if n.strip()],
                                                 [n for n in vyloucit.split("|") if n.strip()])
    except FileNotFoundError:
        pass
    return vystup


def sledovane_firmy(cfg: dict, db: dict, seznam: dict) -> dict[str, Firma]:
    """Firmy s tickerem, které mají v listu Kontrakty kontrakt za posledních zpravy_mesicu měsíců."""
    mesicu = int(cfg.get("zpravy_mesicu", 6))
    hranice = (dt.date.today() - dt.timedelta(days=round(mesicu * 30.44))).isoformat()
    nazvy_sec = {t["ticker"]: t.get("nazev", "") for t in seznam.get("seznam", [])}
    prvni_slova = defaultdict(set)
    for t in seznam.get("seznam", []):
        j = jadro_nazvu(t.get("nazev", ""))
        if j:
            prvni_slova[j.split()[0]].add(j)
    pocty = {w: len(v) for w, v in prvni_slova.items()}
    rucne = nacti_rucni_nazvy()
    dalsi = defaultdict(set)
    for z in db.values():
        t = z.get("ticker")
        if t and z["datum"] >= hranice:
            dalsi[t].update(filter(None, [z.get("materska"), z.get("spolecnost")]))
    firmy = {}
    for t, nazvy in dalsi.items():
        nazev = nazvy_sec.get(t) or sorted(nazvy, key=len)[0]
        # u kontraktu bývá název dceřiné firmy, mateřská firma (z SEC) je ale hlavní klíč
        firmy[t] = Firma(t, nazev, [n for n in nazvy if jadro_nazvu(n).split()[:1] == jadro_nazvu(nazev).split()[:1]],
                         pocty, *rucne.get(t, (None, None)))
    return firmy


# ---------- hlavní běh: sběr zpráv ----------

def _pridej(clanky: dict, polozka: dict, kanal: str, firmy: dict[str, Firma], dotazovana: Firma,
            nejstarsi: str, verejne: bool, limit_den: int, pocty_den: dict) -> bool:
    titulek = cisty_titulek(polozka["titulek"])
    cas = polozka["cas"]
    if not titulek or not cas or cas < nejstarsi or not polozka["url"].startswith("http") or je_sum(titulek):
        return False
    tickery = [t for t, f in firmy.items() if f.v_titulku(titulek)]
    if dotazovana.ticker not in tickery and not dotazovana.v_titulku(titulek):
        return False  # zpráva firmu v titulku nezmiňuje (souhrny trhu, články o jiných firmách)
    if dotazovana.ticker not in tickery:
        tickery.append(dotazovana.ticker)
    klic = _id(titulek)
    z = clanky.get(klic)
    if z:
        z["tickery"] = sorted(set(z["tickery"]) | set(tickery))
        if kanal not in z["kanaly"]:
            z["kanaly"].append(kanal)
        if verejne and not z.get("titulek") and not z.get("plno"):
            # stejnou zprávu teď máme ze zdroje, který smíme zveřejnit
            z.update(titulek=titulek[:300], url=polozka["url"], zdroj=(polozka["vydavatel"] or kanal)[:60])
        return False
    # sentiment z titulku a krátkého popisku; popisek se neukládá
    nazvy = [f.nazev for f in firmy.values() if f.ticker in tickery] + \
            [k for f in firmy.values() if f.ticker in tickery for k in f.klice]
    s = sentiment(f"{titulek}. {polozka['popis'][:400]}", nazvy)
    zaznam = {"cas": cas, "tickery": sorted(tickery), "kanaly": [kanal], "s": s, "kat": kategorie(titulek)}
    # u velmi sledovaných firem (stovky zpráv denně) se titulky drží jen u prvních X zpráv za den,
    # ostatní se jen započítají do statistiky; databáze tak zůstane malá
    den_klice = [(t, cas[:10]) for t in tickery]
    plno = all(pocty_den.get(k, 0) >= limit_den for k in den_klice)
    if verejne and not plno:
        zaznam.update(titulek=titulek[:300], url=polozka["url"], zdroj=(polozka["vydavatel"] or kanal)[:60])
        for k in den_klice:
            pocty_den[k] = pocty_den.get(k, 0) + 1
    elif plno:
        zaznam["plno"] = True
    clanky[klic] = zaznam
    return True


def stahni(cfg: dict, firmy: dict[str, Firma], clanky: dict, kontrola: dict) -> dict:
    """Projde zdroje pro firmy, které jsou na řadě. Vrací statistiku pro hlavičku webu."""
    ted = dt.datetime.now(dt.timezone.utc)
    interval = dt.timedelta(hours=float(cfg.get("zpravy_kazdych_hodin", 3)))
    max_za_beh = int(cfg.get("zpravy_max_tickeru_za_beh", 40))
    limit_den = int(cfg.get("zpravy_max_titulku_na_firmu_den", 15))
    nejstarsi = (ted - dt.timedelta(days=STATISTIKA_DNI - 3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pocty_den = defaultdict(int)
    for z in clanky.values():
        if z.get("titulek"):
            for t in z["tickery"]:
                pocty_den[(t, z["cas"][:10])] += 1
    klic_fh = os.environ.get("FINNHUB_API_KEY", "").strip()
    kanaly = {
        "GDELT": {"zapnuto": cfg.get("zpravy_gdelt", True), "zk": "gd", "verejne": True,
                  "stahovac": Stahovac(float(cfg.get("zpravy_gdelt_pauza_s", 6)), robots=True)},
        "Finnhub": {"zapnuto": cfg.get("zpravy_finnhub", True), "zk": "fh",
                    "verejne": bool(cfg.get("zpravy_finnhub_verejne", False)),
                    # Finnhub je placené/klíčované API s vlastními podmínkami, robots.txt se týká jen webu
                    "stahovac": Stahovac(float(cfg.get("zpravy_finnhub_pauza_s", 1.2)), robots=False)},
    }
    stat = {}
    for kanal, k in kanaly.items():
        if not k["zapnuto"]:
            stat[kanal] = {"ok": None, "info": "vypnuto v config.yaml"}
            continue
        if kanal == "Finnhub" and not klic_fh:
            stat[kanal] = {"ok": None, "info": "chybí klíč FINNHUB_API_KEY v GitHub Secrets"}
            continue
        zk = k["zk"]

        def posledni(t, zk=zk):
            return kontrola.get(t, {}).get(zk, "")
        na_rade = [t for t in firmy
                   if not posledni(t) or dt.datetime.fromisoformat(posledni(t).replace("Z", "+00:00")) <= ted - interval]
        if kanal == "Finnhub":
            na_rade = [t for t in na_rade if "." not in t]  # bezplatný tarif: jen severoamerické firmy
        na_rade.sort(key=posledni)  # nejdéle nekontrolované první
        nove = chyby = hotovo = 0
        posledni_chyba = ""
        zakazano = False
        for t in na_rade[:max_za_beh]:
            f = firmy[t]
            prvne = not posledni(t)
            try:
                if kanal == "GDELT":
                    if not f.klice:
                        continue
                    params = {"query": f.dotaz_gdelt(), "mode": "artlist", "format": "json", "sort": "datedesc",
                              "maxrecords": 250 if prvne else 100, "timespan": "30d" if prvne else "3d"}
                    polozky = _gdelt_polozky(k["stahovac"].json(GDELT_URL, params))
                else:
                    od = (ted - dt.timedelta(days=30 if prvne else 3)).strftime("%Y-%m-%d")
                    params = {"symbol": t, "from": od, "to": ted.strftime("%Y-%m-%d")}
                    polozky = _finnhub_polozky(k["stahovac"].json(FINNHUB_URL, params, {"X-Finnhub-Token": klic_fh}))
            except PermissionError as e:
                posledni_chyba, zakazano = str(e), True
                break
            except Exception as e:  # noqa: BLE001
                chyby += 1
                # adresa dotazu se do hlášení nedává (u Finnhub je klíč stejně jen v hlavičce)
                posledni_chyba = f"{t}: {str(e).split(' for url:')[0][:160]}"
                log.warning("%s %s: %s", kanal, t, posledni_chyba)
                if (chyby >= 5 and hotovo == 0) or chyby >= 8:
                    break  # zdroj zřejmě nefunguje, nemá smysl zkoušet další tickery
                continue
            nove += sum(_pridej(clanky, p, kanal, firmy, f, nejstarsi, k["verejne"], limit_den, pocty_den)
                        for p in polozky)
            kontrola.setdefault(t, {})[zk] = iso_ted()
            kontrola[t].setdefault("od", iso_ted())
            hotovo += 1
        if zakazano or (hotovo == 0 and chyby):
            stat[kanal] = {"ok": False, "chyba": posledni_chyba}
        else:
            stat[kanal] = {"ok": True, "zkontrolovano": hotovo, "nove": nove, "chyby": chyby,
                           "ceka": max(len(na_rade) - max_za_beh, 0)}
            if chyby:
                stat[kanal]["posledni_chyba"] = posledni_chyba
        log.info("Zprávy %s: zkontrolováno %d tickerů, %d nových zpráv, %d chyb", kanal, hotovo, nove, chyby)
    return stat


# ---------- denní souhrny ----------

def prepocti_denni(clanky: dict, denni: dict, od_data: str):
    """Souhrny za dny, pro které máme uložené zprávy, se přepočítají; starší dny zůstanou beze změny."""
    nove = defaultdict(lambda: defaultdict(lambda: [0, 0.0, 0, 0]))
    for z in clanky.values():
        den = z["cas"][:10]
        if den < od_data:
            continue
        for t in z["tickery"]:
            r = nove[t][den]
            r[0] += 1
            r[1] = round(r[1] + z["s"], 3)
            r[2] += z["s"] <= NEGATIVNI
            r[3] += z["s"] >= POZITIVNI
    for t in set(denni) | set(nove):
        dny = {d: v for d, v in denni.get(t, {}).items() if d < od_data}
        dny.update(nove.get(t, {}))
        if dny:
            denni[t] = dict(sorted(dny.items()))
        else:
            denni.pop(t, None)


def okno(dny: dict, od: str, do: str) -> tuple[int, float | None, int, int]:
    n = s = neg = pos = 0
    for d, (pn, ps, pneg, ppos) in dny.items():
        if od <= d <= do:
            n += pn
            s += ps
            neg += pneg
            pos += ppos
    return n, (round(s / n, 3) if n else None), neg, pos


# ---------- tržní kapitalizace ----------

def aktualizuj_kapitalizaci(tickery: set[str], kap: dict) -> dict:
    """Tržní kapitalizace z Yahoo Finance, jednou denně pro tickery s čerstvým kontraktem."""
    dnes = dt.date.today().isoformat()
    potreba = [t for t in tickery if kap.get(t, {}).get("d") != dnes]
    if potreba:
        import yfinance as yf
        for t in potreba[:80]:
            try:
                fi = yf.Ticker(t).fast_info
                hodnota = fi.get("marketCap") or fi.get("market_cap")
                if hodnota:
                    kap[t] = {"v": float(hodnota), "m": fi.get("currency") or mena(t), "d": dnes}
            except Exception as e:  # noqa: BLE001
                log.warning("Kapitalizace %s: %s", t, e)
    return {t: v for t, v in kap.items() if t in tickery}


# ---------- upozornění ----------

def _pct(v: float) -> str:
    return f"{v * 100:.1f}".replace(".", ",") + " %"


def _s(v: float | None) -> str:
    return "-" if v is None else (f"{v:+.2f}").replace(".", ",")


def _zprav(n: int, pridavne: str = "") -> str:
    """Česká čísla: 1 negativní zpráva, 3 negativní zprávy, 5 negativních zpráv."""
    if n == 1:
        return f"1 {pridavne + ' ' if pridavne else ''}zpráva"
    if 2 <= n <= 4:
        return f"{n} {pridavne + ' ' if pridavne else ''}zprávy"
    return f"{n} {pridavne + 'ch ' if pridavne else ''}zpráv"


def _datum(iso: str) -> str:
    r, m, d = iso[:10].split("-")
    return f"{int(d)}. {int(m)}. {r}"


def _mil(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.1f}".replace(".", ",") + " mld. $"
    return f"{v / 1e6:.0f} mil. $"


def vyhodnot_upozorneni(cfg: dict, firmy: dict[str, Firma], denni: dict, kontrola: dict, db: dict,
                        kap: dict, upozorneni: dict) -> dict:
    """Vyhodnotí trendy. Upozornění má stabilní ID po celou dobu, kdy trvá (bublinka na webu ho tak
    nehlásí opakovaně). Skončená upozornění se drží 60 dní kvůli historii."""
    dnes = dt.date.today()
    d = lambda n: (dnes - dt.timedelta(days=n)).isoformat()  # noqa: E731
    vlna_min = int(cfg.get("zpravy_vlna_min", 3))
    posun_min = float(cfg.get("zpravy_posun_min", 0.3))
    zajem_nasobek = float(cfg.get("zpravy_zajem_nasobek", 3))
    vyznamny_pct = float(cfg.get("zpravy_vyznamny_kontrakt_pct", 2)) / 100
    aktualni = {}  # (typ, ticker) -> (smer, text, hodnoty, platne_od)

    for t, f in firmy.items():
        dny = denni.get(t, {})
        n7, s7, neg7, pos7 = okno(dny, d(6), d(0))
        n30, s30, neg30, pos30 = okno(dny, d(36), d(7))
        sledovano_od = kontrola.get(t, {}).get("od", "")[:10]
        historie = bool(sledovano_od) and sledovano_od <= d(10)
        hodnoty = {"n7": n7, "s7": s7, "neg7": neg7, "pos7": pos7, "n30": n30, "s30": s30}
        # Vlny se posuzují proti běžnému stavu dané firmy: titulky zpráv bývají v průměru mírně pozitivní,
        # takže samotná převaha pozitivních zpráv nic neznamená. Upozornění vzniká, jen když je podíl
        # negativních (pozitivních) zpráv výrazně vyšší než v předchozích 30 dnech.
        podil7_neg, podil7_pos = (neg7 / n7, pos7 / n7) if n7 else (0, 0)
        podil30_neg, podil30_pos = (neg30 / n30, pos30 / n30) if n30 else (0, 0)
        ma_zaklad = n30 >= 5
        if (neg7 >= vlna_min and neg7 >= pos7 and (s7 or 0) <= -0.05
                and podil7_neg >= max(0.3, 1.5 * podil30_neg if ma_zaklad else 0)):
            aktualni[("negativni_vlna", t)] = ("neg", f"{_zprav(neg7, 'negativní')} za 7 dní (z {n7}), průměrný "
                                               f"sentiment {_s(s7)}" + (f", obvykle {_s(s30)}." if ma_zaklad else "."),
                                               hodnoty)
        elif (ma_zaklad and pos7 >= vlna_min and podil7_pos >= max(0.5, 1.5 * podil30_pos)
              and s7 is not None and s30 is not None and s7 - s30 >= 0.15):
            aktualni[("pozitivni_vlna", t)] = ("pos", f"{_zprav(pos7, 'pozitivní')} za 7 dní (z {n7}), průměrný "
                                               f"sentiment {_s(s7)}, obvykle {_s(s30)}.", hodnoty)
        if n7 >= 3 and n30 >= 3 and s7 is not None and s30 is not None and abs(s7 - s30) >= posun_min:
            smer = "neg" if s7 < s30 else "pos"
            if ("negativni_vlna" if smer == "neg" else "pozitivni_vlna", t) not in aktualni:
                typ = "zhorseni" if smer == "neg" else "zlepseni"
                aktualni[(typ, t)] = (smer, f"Sentiment za 7 dní {_s(s7)} proti {_s(s30)} v předchozích 30 dnech "
                                            f"({n7} a {n30} zpráv).", hodnoty)
        tydenni_prumer = n30 * 7 / 30
        if historie and n7 >= 5 and n7 >= zajem_nasobek * max(1.0, tydenni_prumer):
            aktualni[("zajem", t)] = ("info", f"{_zprav(n7)} za 7 dní, obvykle zhruba {tydenni_prumer:.0f} za týden. "
                                              f"Sentiment {_s(s7)}.", hodnoty)

    # významný kontrakt vůči velikosti firmy (jen akcie obchodované v USD, kapitalizace v USD)
    for klic, z in db.items():
        t = z.get("ticker")
        if not t or t not in firmy or z["datum"] < d(14) or "Sdílený strop" in (z.get("poznamka") or ""):
            continue
        k = kap.get(t)
        castka = castka_zaznamu(z)
        if not k or k.get("m") != "USD" or not castka:
            continue
        podil = castka / k["v"]
        if podil >= vyznamny_pct:
            n7, s7, *_ = okno(denni.get(t, {}), d(6), d(0))
            typ_k = "navýšení kontraktu" if z["typ"] == "Navýšení" else "kontrakt"
            text = (f"{typ_k.capitalize()} {_mil(castka)} ze dne {_datum(z['datum'])} = {_pct(podil)} tržní kapitalizace "
                    f"({_mil(k['v'])}). " + (f"Zprávy za 7 dní: {n7}, sentiment {_s(s7)}." if n7 else "Za 7 dní zatím bez zpráv."))
            aktualni[(f"kontrakt:{klic}", t)] = ("pos", text, {"podil": round(podil, 4), "castka": castka,
                                                             "kapitalizace": k["v"], "n7": n7, "s7": s7},
                                                 z["datum"])

    ted = iso_ted()
    otevrene = {(u["typ"], u["ticker"]): uid for uid, u in upozorneni.items() if u["aktivni"]}
    for (typ, t), (smer, text, hodnoty, *od) in aktualni.items():
        uid = otevrene.pop((typ, t), None)
        if uid is None:
            # stejné upozornění, které skončilo před méně než 3 dny, se jen znovu otevře (žádná nová bublinka)
            nedavne = [k for k, u in upozorneni.items() if u["typ"] == typ and u["ticker"] == t and u["do"] >= d(3)]
            uid = nedavne[0] if nedavne else f"{typ}|{t}|{dnes.isoformat()}"
        u = upozorneni.setdefault(uid, {"typ": typ, "ticker": t, "od": (od[0] if od else dnes.isoformat()),
                                        "zachyceno": ted})
        u.update(aktivni=True, do=dnes.isoformat(), smer=smer, text=text, hodnoty=hodnoty, firma=firmy[t].nazev,
                 aktualizace=ted)
    for uid in otevrene.values():
        upozorneni[uid]["aktivni"] = False
    return {k: u for k, u in upozorneni.items() if u["aktivni"] or u["do"] >= d(60)}


# ---------- celý běh ----------

def beh(cfg: dict, db: dict, seznam: dict, stav: dict) -> dict:
    """Volá se z hodinového běhu. Výjimky zachytí volající, kontrakty se tím neohrozí."""
    if stav.get("zpravy_verze") != VERZE_DAT:
        # data uložená starší verzí (bez filtrů šumu) se smažou a stáhnou znovu (30 dní zpětně)
        for soubor in (SOUBOR_DENNI, SOUBOR_KONTROLA, SOUBOR_UPOZ):
            soubor.unlink(missing_ok=True)
        for soubor in SLOZKA_CLANKY.glob("*.json") if SLOZKA_CLANKY.exists() else []:
            soubor.unlink()
        stav["zpravy_verze"] = VERZE_DAT
        log.info("Zprávy: data starší verze smazána, stahuji znovu")
    clanky = nacti_clanky()
    denni = nacti_json(SOUBOR_DENNI, {})
    kontrola = nacti_json(SOUBOR_KONTROLA, {})
    kap = nacti_json(SOUBOR_KAP, {})
    upozorneni = nacti_json(SOUBOR_UPOZ, {})
    firmy = sledovane_firmy(cfg, db, seznam)
    kontrola = {t: v for t, v in kontrola.items() if t in firmy}

    stat = stahni(cfg, firmy, clanky, kontrola)

    dnes = dt.datetime.now(dt.timezone.utc).date()
    hranice = (dnes - dt.timedelta(days=int(cfg.get("zpravy_uchovavat_dni", 180)))).isoformat()
    hranice_stat = (dnes - dt.timedelta(days=STATISTIKA_DNI)).isoformat()
    # souhrny za posledních 40 dní se přepočítají ze záznamů; starší dny jsou už uzavřené
    prepocti_denni(clanky, denni, hranice_stat)
    clanky = {k: z for k, z in clanky.items()
              if z["cas"][:10] >= (hranice if z.get("titulek") else hranice_stat)}

    cerstve = {z["ticker"] for z in db.values()
               if z.get("ticker") in firmy and z["datum"] >= (dt.date.today() - dt.timedelta(days=14)).isoformat()}
    try:
        kap = aktualizuj_kapitalizaci(cerstve, kap)
    except Exception as e:  # noqa: BLE001
        log.warning("Kapitalizace selhala: %s", e)
    upozorneni = vyhodnot_upozorneni(cfg, firmy, denni, kontrola, db, kap, upozorneni)

    uloz_clanky(clanky)
    uloz_json(SOUBOR_DENNI, denni, kompaktni=True)
    uloz_json(SOUBOR_KONTROLA, kontrola, kompaktni=True)
    uloz_json(SOUBOR_KAP, kap, kompaktni=True)
    uloz_json(SOUBOR_UPOZ, upozorneni)
    aktivni = sum(u["aktivni"] for u in upozorneni.values())
    log.info("Zprávy: %d uložených, %d sledovaných firem, %d aktivních upozornění", len(clanky), len(firmy), aktivni)
    return {"kanaly": stat, "clanku": len(clanky), "firem": len(firmy), "upozorneni": aktivni}


def nacti_pro_vystup(cfg: dict, db: dict, seznam: dict) -> dict:
    """Data pro web a Excel (bez stahování)."""
    return {"clanky": nacti_clanky(), "denni": nacti_json(SOUBOR_DENNI, {}),
            "upozorneni": nacti_json(SOUBOR_UPOZ, {}), "firmy": sledovane_firmy(cfg, db, seznam)}
