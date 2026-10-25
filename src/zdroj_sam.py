"""SAM.gov: oznámení o udělení zakázek (Award Notices) ze všech federálních úřadů.

Vyžaduje bezplatný API klíč (GitHub Secret SAM_API_KEY). Osobní klíč bez role má nízký
denní limit dotazů, proto se SAM.gov dotazuje jen každých několik hodin (config: sam_kazdych_hodin).
"""
import datetime as dt
import os
import re

from .common import http_session, log, norm_piid

API = "https://api.sam.gov/opportunities/v2/search"


class Neaktivni(Exception):
    """Zdroj není nastavený nebo teď nemá běžet (není to chyba)."""


def _castka(v):
    if v in (None, ""):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(v)) or 0) or None
    except ValueError:
        return None


def _agentura(cesta: str) -> str:
    casti = [c.strip() for c in (cesta or "").split(".") if c.strip()]
    vyber = casti[1] if len(casti) > 1 else (casti[0] if casti else "")
    return vyber.title().replace("Of ", "of ").replace("The ", "the ")


def stahni(cfg: dict, stav: dict) -> list[dict]:
    klic = os.environ.get("SAM_API_KEY", "").strip()
    if not klic:
        raise Neaktivni("Chybí API klíč (GitHub Secret SAM_API_KEY).")
    ted = dt.datetime.now(dt.timezone.utc)
    posledni = stav.get("sam_posledni_dotaz")
    kazdych = float(cfg.get("sam_kazdych_hodin", 3))
    if posledni and (ted - dt.datetime.fromisoformat(posledni)).total_seconds() < kazdych * 3600 - 300:
        raise Neaktivni(f"Další dotaz za méně než {kazdych:g} h (denní limit API).")

    dnes = ted.date()
    od = dnes - dt.timedelta(days=int(cfg.get("sam_dny_zpet", 2)))
    s = http_session()
    vysledky, offset = [], 0
    for _ in range(3):  # max 3 stránky po 1000 = šetříme denní limit
        r = s.get(API, params={
            "api_key": klic, "ptype": "a", "limit": 1000, "offset": offset,
            "postedFrom": od.strftime("%m/%d/%Y"), "postedTo": dnes.strftime("%m/%d/%Y"),
        }, timeout=90)
        if r.status_code == 429:
            raise RuntimeError("Překročen denní limit dotazů SAM.gov, zkusím později.")
        if r.status_code in (401, 403):
            raise RuntimeError("SAM.gov odmítl API klíč (neplatný nebo vypršel, klíč je třeba obnovit).")
        r.raise_for_status()
        j = r.json()
        davka = j.get("opportunitiesData") or []
        vysledky.extend(davka)
        offset += 1000
        if len(vysledky) >= int(j.get("totalRecords") or 0) or not davka:
            break
    stav["sam_posledni_dotaz"] = ted.isoformat()

    min_castka = float(cfg.get("min_castka", 1000000))
    vystup = []
    for o in vysledky:
        award = o.get("award") or {}
        castka = _castka(award.get("amount"))
        if not castka or castka < min_castka:
            continue
        awardee = award.get("awardee") or {}
        cislo = norm_piid(award.get("number") or "")
        notice = o.get("noticeId") or ""
        je_mod = bool(re.search(r"\bmodification\b|\bmod\b", o.get("title") or "", re.I))
        vystup.append({
            "piid": cislo or f"SAM{notice[:16].upper()}",
            "mod": f"?{(award.get('date') or '')[:10]}" if je_mod else "0",
            "typ": "Navýšení" if je_mod else "Nový",
            "datum": (award.get("date") or o.get("postedDate") or "")[:10],
            "spolecnost": (awardee.get("name") or "").strip(),
            "uei": awardee.get("ueiSAM") or "",
            "castka_sam": castka,
            "popis_sam": (o.get("title") or "").strip(),
            "agentura": _agentura(o.get("fullParentPathName")),
            "odkaz_sam": f"https://sam.gov/opp/{notice}/view" if notice else "",
        })
    log.info("SAM.gov: %d oznámení, %d nad minimem", len(vysledky), len(vystup))
    return [z for z in vystup if z["datum"] and z["spolecnost"]]
