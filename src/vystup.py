"""Výstupy: webová stránka (site/), data.json, tickery.json a Excel kontrakty.xlsx."""
import shutil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import ceny
from .common import SITE, WEB, iso_ted, log, uloz_json

BLOKY = "▁▂▃▄▅▆▇█"


def _hezky(text: str) -> str:
    text = (text or "").strip()
    if text and text.isupper():
        text = text.capitalize()
    return text


def yahoo(ticker: str) -> str:
    return f"https://finance.yahoo.com/quote/{ticker}"


def text_graf(rada: list) -> str:
    if not rada or len(rada) < 2:
        return ""
    lo, hi = min(rada), max(rada)
    if hi == lo:
        return BLOKY[3] * min(len(rada), 30)
    if len(rada) > 30:
        krok = (len(rada) - 1) / 29
        rada = [rada[round(i * krok)] for i in range(30)]
    return "".join(BLOKY[int((v - lo) / (hi - lo) * 7)] for v in rada)


def priprav(cfg: dict, db: dict, cache_cen: dict) -> list[dict]:
    nove_piid = {z["piid"] for z in db.values() if z["mod"] == "0"}
    radky = []
    for klic, z in db.items():
        r = {
            "id": klic, "datum": z["datum"], "typ": z["typ"],
            "spolecnost": z["spolecnost"], "materska": z.get("materska", ""),
            "ticker": z.get("ticker", ""), "burza": z.get("burza", ""), "shoda": z.get("shoda", ""),
            "castka": z.get("castka_dod") or z.get("castka_usa") or z.get("castka_sam") or z.get("castka_8k"),
            "castka_usa": z.get("castka_usa"),
            "popis": _hezky(z.get("popis_dod") or z.get("popis_award") or z.get("popis_usa")
                            or z.get("popis_sam") or z.get("popis_8k")),
            "poznamka": z.get("poznamka", ""), "agentura": z.get("agentura", ""),
            "zdroje": z.get("zdroje", []),
            "odkaz": (z.get("odkaz_usa") or z.get("odkaz_dod") or z.get("odkaz_sam") or z.get("odkaz_8k") or ""),
            "odkaz_dod": z.get("odkaz_dod", ""), "odkaz_usa": z.get("odkaz_usa", ""),
            "odkaz_sam": z.get("odkaz_sam", ""), "odkaz_8k": z.get("odkaz_8k", ""),
            "piid": z["piid"], "mod": z["mod"], "prvni_zaznam": z.get("prvni_zaznam", ""),
            "puvodni_v_seznamu": z["typ"] == "Navýšení" and z["piid"] in nove_piid,
        }
        if r["ticker"]:
            r.update(ceny.vyhodnot(r["ticker"], r["datum"], cache_cen, int(cfg.get("graf_max_bodu", 60))))
        radky.append(r)
    radky.sort(key=lambda r: (r["datum"], r["castka"] or 0), reverse=True)
    return radky


def excel(radky: list[dict], seznam: dict, stav: dict, cesta):
    wb = Workbook()
    hlavicka_font = Font(bold=True, color="FFFFFF")
    hlavicka_fill = PatternFill("solid", fgColor="1F3A5F")

    def hlavicka(ws, sloupce, sirky):
        ws.append(sloupce)
        for i, s in enumerate(sirky, 1):
            ws.column_dimensions[get_column_letter(i)].width = s
            c = ws.cell(row=1, column=i)
            c.font, c.fill = hlavicka_font, hlavicka_fill
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.freeze_panes = "A2"

    ws = wb.active
    ws.title = "Kontrakty"
    sloupce = [
        ("Datum", 12), ("Typ", 12), ("Společnost", 32), ("Ticker", 10), ("Výše ($)", 16),
        ("Popis", 60), ("Zhodnocení", 11), ("Cena start", 12), ("Cena dnes", 12), ("Měna", 7),
        ("Vývoj", 32), ("Detail", 12), ("Mateřská společnost", 28), ("Burza", 12), ("Shoda tickeru", 14),
        ("Navazuje na kontrakt v seznamu", 12), ("Zdroje", 22), ("Agentura", 24), ("Číslo kontraktu", 18),
        ("Modifikace", 11), ("DoD", 8), ("SAM.gov", 9), ("SEC 8-K", 9), ("Poprvé zachyceno (UTC)", 20),
    ]
    hlavicka(ws, [s for s, _ in sloupce], [w for _, w in sloupce])
    zelena, cervena = Font(color="1B7F3B"), Font(color="B3261E")
    for r in radky:
        zmena = r.get("zmena_pct")
        ws.append([
            r["datum"], r["typ"], r["spolecnost"], r["ticker"], r["castka"],
            (r["popis"] + (f" ({r['poznamka']})" if r["poznamka"] else "")),
            (zmena / 100) if zmena is not None else None, r.get("cena_pri"), r.get("cena_nyni"),
            r.get("mena", ""), text_graf(r.get("graf") or []),
            "Otevřít" if r["odkaz"] else "", r["materska"], r["burza"], r["shoda"],
            ("ano" if r["puvodni_v_seznamu"] else "") if r["typ"] == "Navýšení" else "",
            ", ".join(r["zdroje"]), r["agentura"], "" if r["piid"].startswith("8K") else r["piid"],
            "" if r["mod"] == "0" or r["mod"].startswith("?") else r["mod"],
            "Otevřít" if r["odkaz_dod"] else "", "Otevřít" if r["odkaz_sam"] else "",
            "Otevřít" if r["odkaz_8k"] else "", r["prvni_zaznam"].replace("T", " ").replace("Z", ""),
        ])
        i = ws.max_row
        ws.cell(i, 5).number_format = '#,##0'
        ws.cell(i, 7).number_format = '+0.0%;-0.0%;0.0%'
        ws.cell(i, 8).number_format = ws.cell(i, 9).number_format = '#,##0.00'
        if zmena is not None:
            ws.cell(i, 7).font = zelena if zmena >= 0 else cervena
            ws.cell(i, 11).font = zelena if zmena >= 0 else cervena
        odkazy = [(12, r["odkaz"]), (21, r["odkaz_dod"]), (22, r["odkaz_sam"]), (23, r["odkaz_8k"])]
        if r["ticker"]:
            odkazy.append((4, yahoo(r["ticker"])))
        for col, url in odkazy:
            if url:
                ws.cell(i, col).hyperlink = url
                ws.cell(i, col).style = "Hyperlink"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(sloupce))}{max(ws.max_row, 1)}"

    wt = wb.create_sheet("Tickery")
    hlavicka(wt, ["Ticker", "Společnost", "Burza", "CIK", "Zdroj"], [12, 50, 18, 12, 14])
    for t in seznam.get("seznam", []):
        wt.append([t["ticker"], t.get("nazev", ""), t.get("burza", ""), t.get("cik"), t.get("zdroj", "")])
        wt.cell(wt.max_row, 1).hyperlink = yahoo(t["ticker"])
    wt.auto_filter.ref = f"A1:E{max(wt.max_row, 1)}"

    wi = wb.create_sheet("Stav")
    wi.column_dimensions["A"].width, wi.column_dimensions["B"].width = 28, 80
    wi.append(["Poslední běh (UTC)", stav.get("posledni_beh", "")])
    wi.append(["Seznam tickerů aktualizován", seznam.get("aktualizace", "")])
    for nazev, s in stav.get("zdroje", {}).items():
        if s.get("ok") is None:
            wi.append([nazev, "NEAKTIVNÍ: " + s.get("info", "")])
        else:
            wi.append([nazev, ("OK " + s.get("cas", "")) if s.get("ok") else ("CHYBA: " + s.get("chyba", ""))])
    wb.save(cesta)


def sestav(cfg: dict, db: dict, cache_cen: dict, seznam: dict, stav: dict):
    SITE.mkdir(exist_ok=True)
    radky = priprav(cfg, db, cache_cen)
    uloz_json(SITE / "data.json", {
        "aktualizace": iso_ted(), "posledni_beh": stav.get("posledni_beh"),
        "tickery_aktualizace": seznam.get("aktualizace"), "zdroje": stav.get("zdroje", {}),
        "min_castka": cfg.get("min_castka"), "kontrakty": radky,
    }, kompaktni=True)
    uloz_json(SITE / "tickery.json", [[t["ticker"], t.get("nazev", ""), t.get("burza", "")]
                                      for t in seznam.get("seznam", [])], kompaktni=True)
    excel(radky, seznam, stav, SITE / "kontrakty.xlsx")
    for soubor in WEB.iterdir():
        if soubor.is_file():
            shutil.copy(soubor, SITE / soubor.name)
    (SITE / ".nojekyll").write_text("")
    log.info("Výstup sestaven: %d kontraktů", len(radky))
