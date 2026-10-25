"""Přiřazení tickeru k názvu firmy.

Pořadí:  1) ruční aliasy (aliasy.csv, včetně dceřiných firem)
         2) přesná shoda normalizovaného názvu se seznamem tickerů
         3) shoda začátku názvu ("LOCKHEED MARTIN ROTARY..." -> "LOCKHEED MARTIN")
         4) podobnost názvů (fuzzy), jen velmi vysoká shoda
Zkouší se nejdřív mateřská společnost (z USAspending), potom samotný příjemce.
"""
import csv
import difflib
import re

from .common import ROOT

PRAVNI_FORMY = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LLC", "L", "C", "LTD",
    "LIMITED", "PLC", "LP", "LLP", "HOLDINGS", "HOLDING", "GROUP", "NV", "SA", "AG", "SE",
    "THE", "DEL", "DE", "NEW", "CLASS", "A", "B", "ADR", "PBC", "TRUST", "USA", "US", "JV",
}
OBECNA_SLOVA = {
    "AMERICAN", "NATIONAL", "GLOBAL", "UNITED", "GENERAL", "INTERNATIONAL", "ADVANCED", "FEDERAL",
    "SYSTEMS", "TECHNOLOGIES", "TECHNOLOGY", "SOLUTIONS", "SERVICES", "PACIFIC", "ATLANTIC",
    "NORTHERN", "SOUTHERN", "WESTERN", "EASTERN", "CENTRAL", "FIRST", "PREMIER", "APPLIED",
    "STRATEGIC", "INTEGRATED", "DEFENSE", "AEROSPACE", "ENERGY", "CONSTRUCTION", "ENGINEERING",
    "MEDICAL", "HEALTH", "DYNAMICS", "DIGITAL", "SECURITY", "MISSION", "PRECISION", "PIONEER",
    "SUMMIT", "LIBERTY", "PATRIOT", "EAGLE", "ALPHA", "APEX", "VECTOR", "SIGMA", "DELTA",
}
BURZA_PRIORITA = {"NYSE": 0, "Nasdaq": 0, "CBOE": 1, "OTC": 3, "": 4}


def norm(nazev: str) -> str:
    s = (nazev or "").upper().replace("&", " AND ")
    s = re.split(r"\b(?:D/?B/?A|DOING BUSINESS AS)\b", s)[0]
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    slova = s.split()
    while slova and slova[0] == "THE":
        slova.pop(0)
    while slova and slova[-1] in PRAVNI_FORMY:
        slova.pop()
    return " ".join(slova)


class Parovac:
    def __init__(self, tickery: list[dict]):
        self.aliasy: list[tuple[str, str, str]] = []
        cesta = ROOT / "aliasy.csv"
        if cesta.exists():
            with open(cesta, encoding="utf-8") as f:
                for z in csv.DictReader((r for r in f if not r.startswith("#")), delimiter=";"):
                    vzor = norm(z.get("vzor") or "")
                    if vzor:
                        self.aliasy.append((vzor, (z.get("ticker") or "").strip().upper(), z.get("vzor")))
        self.aliasy.sort(key=lambda a: -len(a[0]))
        self.info = {t["ticker"]: t for t in tickery}
        self.index: dict[str, str] = {}
        for t in sorted(tickery, key=lambda t: BURZA_PRIORITA.get(t.get("burza", ""), 2)):
            n = norm(t.get("nazev", ""))
            if n and n not in self.index:
                self.index[n] = t["ticker"]
        self.klice = list(self.index)
        self._cache: dict[str, tuple] = {}

    def _jeden(self, nazev: str):
        n = norm(nazev)
        if not n:
            return None
        if n in self._cache:
            return self._cache[n]
        vysledek = None
        obal = f" {n} "
        for vzor, ticker, _ in self.aliasy:
            if f" {vzor} " in obal:
                vysledek = (ticker, "alias" if ticker else "soukromá (alias)")
                break
        if vysledek is None and n in self.index:
            vysledek = (self.index[n], "přesná")
        if vysledek is None:
            slova = n.split()
            for k in range(len(slova) - 1, 0, -1):
                pref = " ".join(slova[:k])
                if pref in self.index:
                    if k == 1 and (len(pref) < 6 or pref in OBECNA_SLOVA):
                        continue
                    if k >= 2 and all(w in OBECNA_SLOVA for w in slova[:k]):
                        continue
                    vysledek = (self.index[pref], "začátek názvu")
                    break
        if vysledek is None and len(n.split()) >= 2:
            kandidati = [k for k in self.klice if k[:1] == n[:1]]
            shoda = difflib.get_close_matches(n, kandidati, n=1, cutoff=0.93)
            if shoda:
                vysledek = (self.index[shoda[0]], "podobnost")
        self._cache[n] = vysledek
        return vysledek

    def prirad(self, materska: str, spolecnost: str):
        """Vrátí (ticker, typ_shody, burza, nazev_tickeru) nebo ("", "", "", "")."""
        for nazev in (materska, spolecnost):
            v = self._jeden(nazev)
            if v:
                ticker, typ = v
                if not ticker:
                    return "", typ, "", ""
                inf = self.info.get(ticker, {})
                return ticker, typ, inf.get("burza", ""), inf.get("nazev", "")
        return "", "", "", ""
