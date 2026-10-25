"""Hlavní program.

  python -m src.main kontrakty   každou hodinu: kontrakty + ceny + výstup (web, Excel)
  python -m src.main tickery     jednou denně: aktualizace seznamu tickerů
  python -m src.main vystup      jen znovu sestaví výstup z uložených dat
"""
import datetime as dt
import sys
import traceback

from . import ceny, tickery, vystup, zdroj_dod, zdroj_sam, zdroj_sec8k, zdroj_usaspending
from .common import DATA, iso_ted, log, nacti_config, nacti_json, uloz_json
from .parovani import Parovac

SOUBOR_KONTRAKTY = DATA / "kontrakty.json"
SOUBOR_CENY = DATA / "ceny.json"
SOUBOR_DETAILY = DATA / "usaspending_detaily.json"
SOUBOR_STAV = DATA / "stav.json"


def _klic(z):
    return f"{z['piid']}|{z['mod']}"


def _sloucit(db: dict, novy: dict, zdroj: str, ted: str):
    """Vloží nebo doplní záznam. Vrací True, pokud jde o nový záznam."""
    klic = _klic(novy)
    # DoD navýšení bez čísla modifikace: zkus spárovat s USAspending podle kontraktu, data a částky
    if zdroj == "USAspending" and klic not in db and novy["mod"] != "0":
        for k, z in db.items():
            puvodni = z.get("castka_dod") or z.get("castka_sam")
            if (z["piid"] == novy["piid"] and z["mod"].startswith("?") and puvodni
                    and abs((dt.date.fromisoformat(z["datum"]) - dt.date.fromisoformat(novy["datum"])).days) <= 10
                    and abs(puvodni - novy["castka_usa"]) <= 0.05 * puvodni):
                db[klic] = db.pop(k)
                db[klic]["mod"] = novy["mod"]
                break
    if klic in db:
        z = db[klic]
        for pole, hodnota in novy.items():
            if hodnota in (None, "", []):
                continue
            if pole in ("datum",) and z.get(pole):
                z[pole] = min(z[pole], hodnota)
            elif pole == "spolecnost" and z.get(pole):
                if zdroj == "DoD":  # DoD má nejčitelnější názvy
                    z.setdefault("alt_nazvy", []).append(z[pole])
                    z[pole] = hodnota
                elif hodnota.lower() != z[pole].lower():
                    z.setdefault("alt_nazvy", []).append(hodnota)
                z["alt_nazvy"] = list(dict.fromkeys(z.get("alt_nazvy", [])))[:5]
            else:
                z[pole] = hodnota
        if zdroj not in z["zdroje"]:
            z["zdroje"].append(zdroj)
            z["doplneno"] = ted
        return False
    novy = dict(novy, zdroje=[zdroj], prvni_zaznam=ted)
    db[klic] = novy
    return True


def _pripoj_8k(db: dict, z: dict, ted: str) -> bool:
    """Pokud hlášení 8-K zmiňuje číslo kontraktu, který už máme, připojí ho k němu."""
    nalezeno = False
    for cislo in z.get("cisla_kontraktu", []):
        for zaznam in db.values():
            if zaznam["piid"] == cislo:
                zaznam["odkaz_8k"] = z["odkaz_8k"]
                if "SEC 8-K" not in zaznam["zdroje"]:
                    zaznam["zdroje"].append("SEC 8-K")
                    zaznam["doplneno"] = ted
                nalezeno = True
    return nalezeno


def beh_kontrakty():
    cfg = nacti_config()
    ted = iso_ted()
    stav = nacti_json(SOUBOR_STAV, {})
    stav.setdefault("zdroje", {})
    db = nacti_json(SOUBOR_KONTRAKTY, {})
    detaily = nacti_json(SOUBOR_DETAILY, {})
    cache_cen = nacti_json(SOUBOR_CENY, {})
    min_castka = float(cfg.get("min_castka", 500000))

    seznam = tickery.nacti()
    if not seznam.get("seznam"):
        log.info("Seznam tickerů chybí, stahuji poprvé.")
        seznam = tickery.aktualizuj(cfg)

    def castka(z):
        return z.get("castka_dod") or z.get("castka_usa") or z.get("castka_sam") or z.get("castka_8k") or 0

    def zdroj(nazev, funkce):
        try:
            zaznamy = funkce(cfg, stav)
            nove = 0
            for z in zaznamy:
                if castka(z) < min_castka:
                    continue
                if nazev == "SEC 8-K" and _pripoj_8k(db, z, ted):
                    continue
                nove += _sloucit(db, z, nazev, ted)
            stav["zdroje"][nazev] = {"ok": True, "cas": ted, "nalezeno": len(zaznamy), "nove": nove}
            log.info("%s: %d záznamů, z toho %d nových", nazev, len(zaznamy), nove)
        except zdroj_sam.Neaktivni as e:
            predchozi = stav["zdroje"].get(nazev, {})
            if predchozi.get("ok") and "klíč" not in str(e):
                predchozi["info"] = str(e)  # běželo dříve, jen teď čeká na další dotaz
                stav["zdroje"][nazev] = predchozi
            else:
                stav["zdroje"][nazev] = {"ok": None, "info": str(e), "cas": ted}
            log.info("%s: %s", nazev, e)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            stav["zdroje"][nazev] = dict(stav["zdroje"].get(nazev, {}), ok=False, chyba=str(e)[:300], cas_chyby=ted)

    zdroj("DoD", zdroj_dod.stahni)
    zdroj("SAM.gov", zdroj_sam.stahni)
    zdroj("USAspending", zdroj_usaspending.stahni)
    zdroj("SEC 8-K", zdroj_sec8k.stahni)

    # zahoď staré záznamy
    hranice = (dt.date.today() - dt.timedelta(days=int(cfg.get("uchovavat_dni", 365)))).isoformat()
    for k in [k for k, z in db.items() if z["datum"] < hranice]:
        db.pop(k)

    # detaily z USAspending (mateřská firma, lepší popis)
    gids = sorted({z["award_gid"] for z in db.values() if z.get("award_gid")} - set(detaily),
                  key=lambda g: g)
    try:
        n = zdroj_usaspending.dotahni_detaily(gids, detaily, int(cfg.get("max_detailu_za_beh", 400)))
        log.info("USAspending detaily: dotaženo %d", n)
    except Exception as e:  # noqa: BLE001
        log.warning("Detaily selhaly: %s", e)
    platne = {z.get("award_gid") for z in db.values()}
    detaily = {g: v for g, v in detaily.items() if g in platne}
    for z in db.values():
        d = detaily.get(z.get("award_gid") or "")
        if d:
            z["materska"] = d.get("materska") or z.get("materska", "")
            z["popis_award"] = d.get("popis") or ""

    # tickery (znovu při každém běhu, aby se projevily opravy v aliasy.csv)
    parovac = Parovac(seznam.get("seznam", []))
    for z in db.values():
        if z.get("ticker_pevny"):
            t = z["ticker_pevny"]
            inf = parovac.info.get(t, {})
            z.update(ticker=t, shoda="hlášení SEC", burza=inf.get("burza", ""), nazev_tickeru=inf.get("nazev", ""))
            continue
        t, typ, burza, nazev_t = parovac.prirad(z.get("materska", ""), z["spolecnost"])
        for alt in z.get("alt_nazvy", []) + [z.get("spolecnost_usa", "")]:
            if t or typ:
                break
            t, typ, burza, nazev_t = parovac.prirad("", alt)
        z.update(ticker=t, shoda=typ, burza=burza, nazev_tickeru=nazev_t)

    # ceny akcií
    potreba = {}
    for z in db.values():
        if z.get("ticker"):
            potreba[z["ticker"]] = min(potreba.get(z["ticker"], "9999"), z["datum"])
    try:
        cache_cen = ceny.aktualizuj(potreba, cache_cen)
        stav["zdroje"]["Ceny akcií"] = {"ok": True, "cas": ted, "tickeru": len(potreba)}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        stav["zdroje"]["Ceny akcií"] = {"ok": False, "chyba": str(e)[:300], "cas_chyby": ted}

    stav["posledni_beh"] = ted
    uloz_json(SOUBOR_KONTRAKTY, db)
    uloz_json(SOUBOR_CENY, cache_cen, kompaktni=True)
    uloz_json(SOUBOR_DETAILY, detaily, kompaktni=True)
    uloz_json(SOUBOR_STAV, stav)
    vystup.sestav(cfg, db, cache_cen, seznam, stav)


def beh_vystup():
    cfg = nacti_config()
    vystup.sestav(cfg, nacti_json(SOUBOR_KONTRAKTY, {}), nacti_json(SOUBOR_CENY, {}),
                  tickery.nacti(), nacti_json(SOUBOR_STAV, {}))


if __name__ == "__main__":
    prikaz = sys.argv[1] if len(sys.argv) > 1 else "kontrakty"
    if prikaz == "tickery":
        tickery.aktualizuj(nacti_config())
    elif prikaz == "vystup":
        beh_vystup()
    else:
        beh_kontrakty()
