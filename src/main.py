"""Hlavní program.

  python -m src.main kontrakty   každou hodinu: kontrakty + ceny + výstup (web, Excel)
  python -m src.main tickery     jednou denně: aktualizace seznamu tickerů
  python -m src.main vystup      jen znovu sestaví výstup z uložených dat

Data se ukládají trvale (větev "data" v repozitáři) a každý běh je jen doplní a aktualizuje.
Kontrakty jsou rozdělené po měsících: data/kontrakty/RRRR-MM.json.
"""
import datetime as dt
import sys
import traceback
from collections import defaultdict

from . import ceny, tickery, vystup, zdroj_dod, zdroj_sam, zdroj_sec8k, zdroj_usaspending
from .common import DATA, iso_ted, log, nacti_config, nacti_json, uloz_json
from .parovani import Parovac

SLOZKA_KONTRAKTY = DATA / "kontrakty"
SOUBOR_CENY = DATA / "ceny.json"
SOUBOR_DETAILY = DATA / "usaspending_detaily.json"
SOUBOR_STAV = DATA / "stav.json"


# ---------- ukládání ----------

def nacti_kontrakty() -> dict:
    db = {}
    stary = DATA / "kontrakty.json"  # převod ze staršího formátu (jeden soubor)
    if stary.exists():
        db.update(nacti_json(stary, {}))
        stary.unlink()
    if SLOZKA_KONTRAKTY.exists():
        for soubor in sorted(SLOZKA_KONTRAKTY.glob("*.json")):
            db.update(nacti_json(soubor, {}))
    return db


def uloz_kontrakty(db: dict):
    po_mesicich = defaultdict(dict)
    for k, z in db.items():
        po_mesicich[z["datum"][:7]][k] = z
    SLOZKA_KONTRAKTY.mkdir(parents=True, exist_ok=True)
    for mesic, zaznamy in po_mesicich.items():
        uloz_json(SLOZKA_KONTRAKTY / f"{mesic}.json", zaznamy)
    for soubor in SLOZKA_KONTRAKTY.glob("*.json"):  # měsíc bez záznamů (po pročištění)
        if soubor.stem not in po_mesicich:
            soubor.unlink()


# ---------- slučování záznamů z více zdrojů ----------

class Databaze:
    def __init__(self, db: dict, ted: str):
        self.db, self.ted = db, ted
        self.podle_piid = defaultdict(set)
        for k, z in db.items():
            self.podle_piid[z["piid"]].add(k)

    def _presun(self, stary: str, novy: str):
        z = self.db.pop(stary)
        self.podle_piid[z["piid"]].discard(stary)
        self.db[novy] = z
        self.podle_piid[z["piid"]].add(novy)

    def sloucit(self, novy: dict, zdroj: str) -> bool:
        """Vloží nebo doplní záznam. Vrací True, pokud jde o nový záznam."""
        klic = f"{novy['piid']}|{novy['mod']}"
        # navýšení bez čísla modifikace (DoD, SAM): spáruj s USAspending podle kontraktu, data a částky
        if zdroj == "USAspending" and klic not in self.db and novy["mod"] != "0":
            for k in list(self.podle_piid.get(novy["piid"], ())):
                z = self.db[k]
                puvodni = z.get("castka_dod") or z.get("castka_sam")
                if (z["mod"].startswith("?") and puvodni
                        and abs((dt.date.fromisoformat(z["datum"]) - dt.date.fromisoformat(novy["datum"])).days) <= 10
                        and abs(puvodni - novy["castka_usa"]) <= 0.05 * puvodni):
                    self._presun(k, klic)
                    self.db[klic]["mod"] = novy["mod"]
                    break
        if klic in self.db:
            z = self.db[klic]
            for pole, hodnota in novy.items():
                if hodnota in (None, "", []):
                    continue
                if pole == "datum" and z.get(pole):
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
                z["doplneno"] = self.ted
            return False
        self.db[klic] = dict(novy, zdroje=[zdroj], prvni_zaznam=self.ted)
        self.podle_piid[novy["piid"]].add(klic)
        return True

    def pripoj_8k(self, z: dict) -> bool:
        """Pokud hlášení 8-K zmiňuje číslo kontraktu, který už máme, připojí ho k němu."""
        nalezeno = False
        for cislo in z.get("cisla_kontraktu", []):
            for k in self.podle_piid.get(cislo, ()):
                zaznam = self.db[k]
                zaznam["odkaz_8k"] = z["odkaz_8k"]
                if "SEC 8-K" not in zaznam["zdroje"]:
                    zaznam["zdroje"].append("SEC 8-K")
                    zaznam["doplneno"] = self.ted
                nalezeno = True
        return nalezeno


# ---------- hlavní běh ----------

def beh_kontrakty():
    cfg = nacti_config()
    ted = iso_ted()
    stav = nacti_json(SOUBOR_STAV, {})
    stav.setdefault("zdroje", {})
    db = nacti_kontrakty()
    pocet_pred = len(db)
    detaily = nacti_json(SOUBOR_DETAILY, {})
    cache_cen = nacti_json(SOUBOR_CENY, {})
    min_castka = float(cfg.get("min_castka", 1000000))
    log.info("Načteno %d uložených záznamů", pocet_pred)

    seznam = tickery.nacti()
    if not seznam.get("seznam"):
        log.info("Seznam tickerů chybí, stahuji poprvé.")
        seznam = tickery.aktualizuj(cfg)

    databaze = Databaze(db, ted)

    def castka(z):
        return z.get("castka_dod") or z.get("castka_usa") or z.get("castka_sam") or z.get("castka_8k") or 0

    def zdroj(nazev, funkce):
        try:
            zaznamy = funkce(cfg, stav)
            nove = 0
            for z in zaznamy:
                if castka(z) < min_castka:
                    continue
                if nazev == "SEC 8-K" and databaze.pripoj_8k(z):
                    continue
                nove += databaze.sloucit(z, nazev)
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

    # volitelné pročištění starých záznamů (0 = uchovávat navždy)
    uchovavat = int(cfg.get("uchovavat_dni", 0) or 0)
    if uchovavat > 0:
        hranice = (dt.date.today() - dt.timedelta(days=uchovavat)).isoformat()
        for k in [k for k, z in db.items() if z["datum"] < hranice]:
            db.pop(k)

    # detaily z USAspending (mateřská firma, lepší popis), nejnovější kontrakty mají přednost
    chybi = {}
    for z in db.values():
        g = z.get("award_gid")
        if g and g not in detaily:
            chybi[g] = max(chybi.get(g, ""), z["datum"])
    gids = sorted(chybi, key=chybi.get, reverse=True)
    try:
        n = zdroj_usaspending.dotahni_detaily(gids, detaily, int(cfg.get("max_detailu_za_beh", 400)))
        log.info("USAspending detaily: dotaženo %d (zbývá %d)", n, max(len(gids) - n, 0))
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

    # pojistka: bez zapnutého pročišťování nesmí záznamů ubýt
    if uchovavat == 0 and len(db) < pocet_pred:
        raise SystemExit(f"Počet záznamů klesl z {pocet_pred} na {len(db)}, data neukládám.")

    stav["posledni_beh"] = ted
    stav["pocet_zaznamu"] = len(db)
    uloz_kontrakty(db)
    uloz_json(SOUBOR_CENY, cache_cen, kompaktni=True)
    uloz_json(SOUBOR_DETAILY, detaily, kompaktni=True)
    uloz_json(SOUBOR_STAV, stav)
    vystup.sestav(cfg, db, cache_cen, seznam, stav)


def beh_vystup():
    cfg = nacti_config()
    vystup.sestav(cfg, nacti_kontrakty(), nacti_json(SOUBOR_CENY, {}),
                  tickery.nacti(), nacti_json(SOUBOR_STAV, {}))


if __name__ == "__main__":
    prikaz = sys.argv[1] if len(sys.argv) > 1 else "kontrakty"
    if prikaz == "tickery":
        tickery.aktualizuj(nacti_config())
    elif prikaz == "vystup":
        beh_vystup()
    else:
        beh_kontrakty()
