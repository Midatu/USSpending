"""USAspending.gov API: úplná data o kontraktech všech federálních úřadů.

Hledáme transakce (nové kontrakty i modifikace), které byly do databáze nově přidány
nebo upraveny za posledních N dní. Díky tomu zachytíme i DoD kontrakty, které se
v USAspending objevují s cca 90denním zpožděním.
"""
import datetime as dt
import time

from .common import http_session, log, norm_mod, norm_piid

API = "https://api.usaspending.gov/api/v2"
POLE = [
    "Award ID", "Mod", "Recipient Name", "Action Date", "Transaction Amount",
    "Transaction Description", "Awarding Agency", "Awarding Sub Agency",
    "Recipient UEI", "internal_id", "generated_internal_id",
]
KONTRAKTY = ["A", "B", "C", "D"]  # typy kontraktů (ne granty, ne půjčky)


def _hledej(s, filtry: dict, min_castka: float, max_stran: int = 60) -> list[dict]:
    vysledky, strana = [], 1
    while strana <= max_stran:
        telo = {
            "filters": filtry, "fields": POLE, "page": strana, "limit": 100,
            "sort": "Transaction Amount", "order": "desc",
        }
        r = s.post(f"{API}/search/spending_by_transaction/", json=telo, timeout=120)
        if r.status_code == 422:
            raise ValueError(r.text[:300])
        r.raise_for_status()
        j = r.json()
        davka = j.get("results", [])
        vysledky.extend(davka)
        # řazeno sestupně podle částky: jakmile klesneme pod minimum, končíme
        if not davka or (davka[-1].get("Transaction Amount") or 0) < min_castka:
            break
        if not j.get("page_metadata", {}).get("hasNext"):
            break
        strana += 1
        time.sleep(0.5)
    return vysledky


def stahni(cfg: dict, stav: dict) -> list[dict]:
    s = http_session()
    min_castka = float(cfg.get("min_castka", 500000))
    dnes = dt.date.today()
    od = dnes - dt.timedelta(days=int(cfg.get("usaspending_dny_zpet", 3)))
    nejstarsi = dnes - dt.timedelta(days=int(cfg.get("max_stari_kontraktu_dni", 150)))
    zaklad = {
        "award_type_codes": KONTRAKTY,
        "award_amounts": [{"lower_bound": min_castka}],
    }
    try:
        filtry = dict(zaklad, time_period=[{
            "start_date": od.isoformat(), "end_date": dnes.isoformat(),
            "date_type": "last_modified_date"}])
        surova = _hledej(s, filtry, min_castka)
        rezim = "podle data zápisu do databáze"
    except ValueError as e:
        log.warning("Filtr last_modified_date nebyl přijat (%s), používám datum akce.", e)
        filtry = dict(zaklad, time_period=[{
            "start_date": (dnes - dt.timedelta(days=10)).isoformat(), "end_date": dnes.isoformat()}])
        surova = _hledej(s, filtry, min_castka)
        rezim = "podle data akce (náhradní režim)"
    log.info("USAspending (%s): %d transakcí", rezim, len(surova))
    stav["usaspending_rezim"] = rezim

    vystup = []
    for t in surova:
        castka = t.get("Transaction Amount") or 0
        datum = t.get("Action Date") or ""
        if castka < min_castka or not datum or datum < nejstarsi.isoformat():
            continue
        mod = norm_mod(t.get("Mod"))
        gid = t.get("generated_internal_id") or ""
        vystup.append({
            "piid": norm_piid(t.get("Award ID")), "mod": mod,
            "typ": "Nový" if mod == "0" else "Navýšení",
            "datum": datum[:10], "spolecnost": (t.get("Recipient Name") or "").strip(),
            "castka_usa": float(castka), "popis_usa": (t.get("Transaction Description") or "").strip(),
            "agentura": t.get("Awarding Sub Agency") or t.get("Awarding Agency") or "",
            "uei": t.get("Recipient UEI") or "", "award_gid": gid,
            "odkaz_usa": f"https://www.usaspending.gov/award/{gid}" if gid else "",
        })
    return vystup


def dotahni_detaily(gids: list[str], cache: dict, limit: int) -> int:
    """Mateřská společnost a souhrnný popis kontraktu. cache = {gid: {...}}."""
    s = http_session()
    hotovo = 0
    for gid in gids:
        if hotovo >= limit:
            break
        if not gid or gid in cache:
            continue
        try:
            r = s.get(f"{API}/awards/{gid}/", timeout=60)
            if r.status_code == 404:
                cache[gid] = {}
                continue
            r.raise_for_status()
            j = r.json()
            rec = j.get("recipient") or {}
            cache[gid] = {
                "materska": (rec.get("parent_recipient_name") or "").strip(),
                "popis": (j.get("description") or "").strip(),
                "celkem": j.get("base_and_all_options_value") or j.get("total_obligation"),
            }
            hotovo += 1
            time.sleep(0.2)
        except Exception as e:  # noqa: BLE001
            log.warning("Detail %s selhal: %s", gid, e)
    return hotovo
