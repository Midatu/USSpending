"""Denní oznámení kontraktů ministerstva obrany USA (Department of War / Defense).

Ministerstvo každý pracovní den kolem 17:00 východního času (23:00 u nás) zveřejní
seznam kontraktů nad 7,5 mil. USD. Je to nejrychlejší zdroj, USAspending je doplní později.
"""
import datetime as dt
import re

from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .common import Neaktivni, http_session, log, norm_mod, norm_piid

RSS_URLS = [
    "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max={max}",
    "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max={max}",
]
SEZNAM_URLS = ["https://www.war.gov/News/Contracts/", "https://www.defense.gov/News/Contracts/"]

# Číslo kontraktu, např. W912DW-26-D-A003, N0001920C0047, FA853226DB028
CISLO = re.compile(r"\b([A-Z][A-Z0-9]{5}-?\d{2}-?[A-Z0-9]-?[A-Z0-9]{4})\b")
MOD_ZAVORKA = re.compile(r"modification\s*\(\s*([A-Z0-9]{5,6})\s*\)", re.I)
MOD_VOLNY = re.compile(r"\bmodification\s+(P[0-9A-Z]{5}|A\d{5})\b", re.I)
CASTKA = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")
SLOVESO = re.compile(
    r"\b(?:is|was|has been|have been|are|were|will be)\s+(?:each\s+)?(?:being\s+)?awarded\b"
    r"|\bawarded\b|\bwill compete for each order\b",
    re.I,
)
MULTI = re.compile(
    r"\b(?:have been|are|were|are each being|were each|will each be)\s+(?:each\s+)?(?:being\s+)?awarded\b"
    r"|\bwill compete for each order\b|\bshare (?:a|the)\b",
    re.I,
)
UDELENO_DNE = re.compile(r"\(Awarded\s+([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})\)")


def _datum(text: str):
    try:
        t = text.replace("Sept.", "Sep.")
        return dateparser.parse(t, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def _castka(text: str):
    m = CASTKA.search(text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _popis(text: str) -> str:
    m = re.search(
        r"\b(?:contract|modification|order|agreement|action|award)\b[^.]{0,200}?\bfor\s+(?:the\s+)?(.+?)(?:\.\s+[A-Z]|\.$)",
        text,
    )
    if m:
        popis = m.group(1)
    else:
        v = SLOVESO.search(text)
        zbytek = text[v.end():] if v else text
        popis = re.sub(r"^\s*(?:a|an)\s+", "", zbytek).split(". ")[0]
    popis = re.sub(r"\s+", " ", popis).strip(" .")
    return (popis[:297] + "...") if len(popis) > 300 else popis


def _nazev(segment: str) -> str:
    nazev = segment.split(",")[0]
    nazev = re.sub(r"^\s*(?:and\s+)", "", nazev)
    return nazev.replace("*", "").strip(" ;")


def _je_hlavicka(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z &,.'()/-]{2,70}", text)) and "CORRECTION" not in text


def rozparsuj_odstavec(text: str, agentura: str, datum: dt.date, url: str) -> list[dict]:
    """Z jednoho odstavce oznámení vytvoří 0..n záznamů."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 60 or text.startswith("CORRECTION") or not SLOVESO.search(text):
        return []
    m_dne = UDELENO_DNE.search(text)
    datum_ud = _datum(m_dne.group(1)) if m_dne else datum
    castka = _castka(text)
    popis = _popis(text)

    # Kontrakty pro více firem najednou (sdílený strop)
    m_multi = MULTI.search(text)
    if m_multi and text[: m_multi.start()].count("(") >= 2:
        hlava = text[: m_multi.start()]
        zaznamy = []
        for seg in re.split(r";\s*", hlava):
            cisla = CISLO.findall(seg)
            nazev = _nazev(seg)
            if not cisla or not nazev or len(nazev) < 2:
                continue
            for c in cisla[:1]:
                zaznamy.append({
                    "piid": norm_piid(c), "mod": "0", "typ": "Nový", "datum": datum_ud.isoformat(),
                    "spolecnost": nazev, "castka_dod": castka, "popis_dod": popis,
                    "agentura": agentura, "odkaz_dod": url,
                })
        pocet = len(zaznamy)
        for z in zaznamy:
            z["poznamka"] = f"Sdílený strop pro {pocet} firem (částka je celkový strop, ne částka pro jednu firmu)."
        return zaznamy

    nazev = _nazev(text)
    cisla = CISLO.findall(text)
    if not cisla:
        return []
    je_mod = bool(re.search(r"\bmodification\b", text, re.I))
    mod = "0"
    if je_mod:
        m = MOD_ZAVORKA.search(text) or MOD_VOLNY.search(text)
        mod = norm_mod(m.group(1)) if m else f"?{datum_ud.isoformat()}"
        m_pred = re.search(r"previously (?:awarded|issued)[^(]{0,60}?\(?([A-Z][A-Z0-9-]{9,16})\)?", text)
        m_to = re.search(r"(?:to|under)\s+(?:[\w-]+\s+){0,6}?contract\s*\(?([A-Z][A-Z0-9-]{9,16})", text)
        kandidat = (m_pred or m_to)
        piid = norm_piid(kandidat.group(1)) if kandidat and CISLO.search(kandidat.group(1)) else norm_piid(cisla[0])
    else:
        m_ord = re.search(r"\border\s*\(([A-Z][A-Z0-9-]{9,16})\)", text)
        piid = norm_piid(m_ord.group(1)) if m_ord else norm_piid(cisla[-1])
    return [{
        "piid": piid, "mod": mod, "typ": "Navýšení" if je_mod else "Nový",
        "datum": datum_ud.isoformat(), "spolecnost": nazev, "castka_dod": castka,
        "popis_dod": popis, "agentura": agentura, "odkaz_dod": url,
    }]


def rozparsuj_clanek(html: str, url: str, datum_fallback: dt.date | None = None) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    titulek = ""
    for sel in ["h1", "title"]:
        el = soup.find(sel)
        if el and "ontract" in el.get_text():
            titulek = el.get_text(" ", strip=True)
            break
    datum = _datum(re.sub(r"(?i).*contracts?\s+for\s+", "", titulek)) if titulek else None
    datum = datum or datum_fallback or dt.date.today()
    agentura, vystup = "", []
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        if not text:
            continue
        if _je_hlavicka(text):
            agentura = text.title().replace("Of ", "of ").replace("And ", "and ").replace("Logistcs", "Logistics")
            agentura = {"Army": "U.S. Army", "Navy": "U.S. Navy", "Air Force": "U.S. Air Force",
                        "Space Force": "U.S. Space Force", "Marine Corps": "U.S. Marine Corps"}.get(agentura, agentura)
            continue
        vystup.extend(rozparsuj_odstavec(text, agentura, datum, url))
    return vystup


def _seznam_clanku(s, max_pocet: int) -> list[tuple[str, dt.date | None]]:
    for rss in RSS_URLS:
        try:
            r = s.get(rss.format(max=max_pocet), timeout=40)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "xml")
            polozky = []
            for it in soup.find_all("item"):
                link = (it.find("link").get_text(strip=True) if it.find("link") else "")
                titulek = it.find("title").get_text(strip=True) if it.find("title") else ""
                if link and "ontract" in titulek:
                    d = _datum(re.sub(r"(?i).*contracts?\s+for\s+", "", titulek))
                    polozky.append((link, d))
            if polozky:
                return polozky
        except Exception as e:  # noqa: BLE001
            log.warning("RSS %s selhalo: %s", rss.split("/")[2], e)
    for seznam in SEZNAM_URLS:
        try:
            r = s.get(seznam, timeout=40)
            r.raise_for_status()
            odkazy = re.findall(r'href="([^"]*/News/Contracts/Contract/Article/\d+/[^"]*)"', r.text)
            uniq = list(dict.fromkeys(odkazy))[:max_pocet]
            base = "/".join(seznam.split("/")[:3])
            return [(u if u.startswith("http") else base + u, None) for u in uniq]
        except Exception as e:  # noqa: BLE001
            log.warning("Seznam %s selhal: %s", seznam, e)
    raise RuntimeError("Nepodařilo se načíst seznam oznámení DoD (RSS ani stránku).")


HLAVICKY_PROHLIZECE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


ARCHIV_DOSTUPNOST = "https://archive.org/wayback/available"
ARCHIV_ULOZIT = "https://web.archive.org/save/"
ARCHIV_ZADOST_KAZDYCH_H = 6  # o archivaci jednoho oznámení žádáme nejvýš jednou za 6 hodin


def _z_archivu(s, url: str):
    """Vrátí (html, cas_snimku) z Wayback Machine (archive.org), nebo (None, None), když kopie ještě není."""
    r = s.get(ARCHIV_DOSTUPNOST, params={"url": url}, timeout=40)
    r.raise_for_status()
    snimek = (r.json().get("archived_snapshots") or {}).get("closest") or {}
    if not snimek.get("available") or str(snimek.get("status")) != "200":
        return None, None
    ts = snimek["timestamp"]
    # "id_" vrátí původní stránku bez lišty archivu
    r = s.get(f"https://web.archive.org/web/{ts}id_/{url}", timeout=60)
    r.raise_for_status()
    return r.text, ts


def _pozadej_archivaci(url: str, zadosti: dict, ted: dt.datetime) -> bool:
    """Požádá archive.org, aby si stránku uložil (jeho servery war.gov neblokuje). Výsledek přijde v dalším běhu."""
    posledni = zadosti.get(url)
    if posledni:
        try:
            if ted - dt.datetime.fromisoformat(posledni) < dt.timedelta(hours=ARCHIV_ZADOST_KAZDYCH_H):
                return False
        except ValueError:
            pass
    zadosti[url] = ted.isoformat(timespec="seconds")
    try:
        import requests
        requests.get(ARCHIV_ULOZIT + url, timeout=30,
                     headers={"User-Agent": "GovContractTracker (github.com/Midatu/USSpending)"})
    except Exception as e:  # noqa: BLE001  (archivace často doběhne, i když spojení vyprší)
        log.info("Žádost o archivaci %s: %s", url, str(e)[:100])
    return True


def stahni(cfg: dict, stav: dict) -> list[dict]:
    """Stáhne nová (a nedávno upravená) oznámení. stav['dod_clanky'] = {url: iso_cas}.

    Seznam oznámení (RSS) jde stáhnout přímo z war.gov. Samotné stránky oznámení ale war.gov
    serverům GitHubu blokuje (403), proto je při blokaci bereme z archivu archive.org.
    """
    s = http_session()
    s.headers.update(HLAVICKY_PROHLIZECE)
    archiv = http_session()  # archive.org bez hlaviček prohlížeče
    zpracovane = stav.setdefault("dod_clanky", {})
    zadosti = stav.setdefault("dod_archiv_zadosti", {})
    clanky = _seznam_clanku(s, int(cfg.get("dod_pocet_oznameni", 10)))
    dnes = dt.date.today()
    ted = dt.datetime.now(dt.timezone.utc)
    vystup, pokusy, uspechy, cekajici, chyby = [], 0, 0, 0, []
    primo_blokovano = False
    for url, d in clanky:
        cerstve = d is None or (dnes - d).days <= 2  # čerstvá oznámení kontrolujeme znovu (opravy)
        if url in zpracovane and not cerstve:
            continue
        pokusy += 1
        html, zdroj = None, ""
        if not primo_blokovano:
            try:
                r = s.get(url, timeout=40)
                r.raise_for_status()
                html, zdroj = r.text, "war.gov"
            except Exception as e:  # noqa: BLE001
                primo_blokovano = True  # v tomto běhu už war.gov znovu nezkoušíme
                log.info("war.gov přímo nejde (%s), použiji archiv.", str(e)[:80])
        if html is None:
            try:
                html, ts = _z_archivu(archiv, url)
                zdroj = f"archive.org {ts}" if html else ""
            except Exception as e:  # noqa: BLE001
                chyby.append(f"archive.org: {str(e)[:120]}")
                log.warning("Archiv pro %s selhal: %s", url, e)
                continue
        if html is None:
            cekajici += 1
            if _pozadej_archivaci(url, zadosti, ted):
                log.info("DoD %s: v archivu zatím není, požádáno o uložení.", url)
            continue
        zaznamy = rozparsuj_clanek(html, url, d)
        if not zaznamy and "awarded" not in html:
            chyby.append(f"{zdroj}: stránka neobsahuje text oznámení")
            continue
        vystup.extend(zaznamy)
        zpracovane[url] = dnes.isoformat()
        zadosti.pop(url, None)
        uspechy += 1
        log.info("DoD %s: %d záznamů (zdroj %s)", url.rstrip("/").split("/")[-2], len(zaznamy), zdroj)
    if pokusy and not uspechy:
        if cekajici and not chyby:
            raise Neaktivni(f"Čeká se na kopii {cekajici} oznámení v archivu (war.gov blokuje GitHub).", cekani=True)
        raise RuntimeError(f"Žádné z {pokusy} oznámení se nepodařilo stáhnout. " + " | ".join(chyby[:3]))
    # udržuj stav malý
    if len(zpracovane) > 200:
        for k in sorted(zpracovane, key=zpracovane.get)[:-200]:
            zpracovane.pop(k)
    for k in [k for k in zadosti if k not in dict(clanky)]:
        zadosti.pop(k)
    return vystup
