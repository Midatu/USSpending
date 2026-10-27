"""Přiřazení tickeru k názvu firmy.

Pořadí:  1) ruční aliasy (aliasy.csv, včetně dceřiných firem)
         2) přesná shoda normalizovaného názvu se seznamem tickerů
         3) podobnost názvů (fuzzy), jen velmi vysoká shoda a stejné první slovo
Zkouší se nejdřív mateřská společnost (z USAspending), potom samotný příjemce a jeho další názvy.
Dceřiné firmy (např. Sikorsky) tak pokrývají aliasy a mateřská společnost.
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
BURZA_PRIORITA = {"NYSE": 0, "Nasdaq": 0, "CBOE": 1, "OTC": 3, "": 4}


def norm(nazev: str) -> str:
    s = re.sub(r"^\s*UPDATE:\s*", "", (nazev or "").upper()).replace("&", " AND ")
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
        self.aliasy: list[tuple[str, str]] = []  # (normalizovaný vzor, ticker)
        cesta = ROOT / "aliasy.csv"
        if cesta.exists():
            with open(cesta, encoding="utf-8") as f:
                for z in csv.DictReader((r for r in f if not r.startswith("#")), delimiter=";"):
                    vzor = norm(z.get("vzor") or "")
                    if vzor:
                        self.aliasy.append((vzor, (z.get("ticker") or "").strip().upper()))
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
        for vzor, ticker in self.aliasy:
            if f" {vzor} " in obal:
                vysledek = (ticker, "alias" if ticker else "soukromá (alias)")
                break
        if vysledek is None and n in self.index:
            vysledek = (self.index[n], "přesná")
        if vysledek is None and len(n.split()) >= 2:
            prvni = n.split()[0]
            kandidati = [k for k in self.klice if k.split()[0] == prvni and len(k.split()) >= 2]
            shoda = difflib.get_close_matches(n, kandidati, n=1, cutoff=0.94)
            if shoda:
                vysledek = (self.index[shoda[0]], "podobnost")
        self._cache[n] = vysledek
        return vysledek

    def prirad(self, *nazvy: str):
        """Zkusí názvy v daném pořadí. Vrátí (ticker, typ_shody, burza) nebo ("", "", "")."""
        for nazev in nazvy:
            v = self._jeden(nazev)
            if v:
                ticker, typ = v
                return ticker, typ, self.info.get(ticker, {}).get("burza", "") if ticker else ""
        return "", "", ""
