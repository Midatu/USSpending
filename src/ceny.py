"""Ceny akcií z Yahoo Finance (knihovna yfinance, zdarma, zpoždění cca 15 minut).

Cache v data/ceny.json: {ticker: {"h": {"YYYY-MM-DD": close}, "od": "YYYY-MM-DD", "cas": iso}}
"""
import datetime as dt

from .common import iso_ted, log

MIN_DNU_PA = 30  # roční zhodnocení počítáme jen u kontraktů starších 30 dní

MENY = {
    ".L": "GBp", ".PA": "EUR", ".DE": "EUR", ".MI": "EUR", ".AS": "EUR", ".ST": "SEK",
    ".OL": "NOK", ".AX": "AUD", ".T": "JPY", ".KS": "KRW", ".TO": "CAD", ".TA": "ILA",
}


def mena(ticker: str) -> str:
    for suf, m in MENY.items():
        if ticker.endswith(suf):
            return m
    return "USD"


def _serie(df, ticker, vice):
    import pandas as pd  # noqa: F401
    try:
        if vice:
            sub = df[ticker] if ticker in df.columns.get_level_values(0) else None
            if sub is None:
                return {}
            col = sub["Close"]
        else:
            col = df["Close"]
            if hasattr(col, "columns"):
                col = col.iloc[:, 0]
        col = col.dropna()
        return {i.strftime("%Y-%m-%d"): round(float(v), 4) for i, v in col.items()}
    except Exception:  # noqa: BLE001
        return {}


def _stahni(tickery: list[str], **kw) -> dict[str, dict]:
    import yfinance as yf
    vystup = {}
    for i in range(0, len(tickery), 40):
        davka = tickery[i:i + 40]
        try:
            df = yf.download(davka, interval="1d", auto_adjust=False, group_by="ticker",
                             progress=False, threads=True, **kw)
        except Exception as e:  # noqa: BLE001
            log.warning("yfinance selhal pro dávku %s: %s", davka[:3], e)
            continue
        if df is None or df.empty:
            continue
        vice = getattr(df.columns, "nlevels", 1) > 1
        for t in davka:
            h = _serie(df, t, vice)
            if h:
                vystup[t] = h
    return vystup


def aktualizuj(potreba: dict[str, str], cache: dict) -> dict:
    """potreba = {ticker: nejstarší datum, od kdy potřebujeme historii}."""
    if not potreba:
        return cache
    plne = []
    for t, od in potreba.items():
        c = cache.get(t)
        if not c or not c.get("h") or c.get("od", "9999") > od:
            plne.append(t)
    # plná historie pro nové tickery (seskupené podle data začátku)
    if plne:
        start = min(potreba[t] for t in plne)
        start = (dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat()
        for t, h in _stahni(plne, start=start).items():
            cache[t] = {"h": h, "od": potreba[t], "cas": iso_ted()}
        log.info("Ceny: plná historie pro %d/%d tickerů", sum(1 for t in plne if t in cache), len(plne))
    # rychlá aktualizace posledních dní pro všechny (aktuální cena)
    ostatni = [t for t in potreba if t not in plne]
    for t, h in _stahni(ostatni, period="5d").items():
        cache.setdefault(t, {"h": {}, "od": potreba[t]})
        cache[t]["h"].update(h)
        cache[t]["cas"] = iso_ted()
    log.info("Ceny: aktualizováno %d tickerů", len(ostatni))
    # ořízni historii, která už není potřeba
    for t in list(cache):
        if t not in potreba:
            cache.pop(t)
            continue
        od = (dt.date.fromisoformat(potreba[t]) - dt.timedelta(days=7)).isoformat()
        cache[t]["h"] = {d: v for d, v in cache[t]["h"].items() if d >= od}
    return cache


def vyhodnot(ticker: str, datum: str, cache: dict, max_bodu: int = 60) -> dict:
    h = (cache.get(ticker) or {}).get("h") or {}
    dny = sorted(d for d in h if d >= datum)
    if not dny:
        return {}
    pri = h[dny[0]]
    ted = h[dny[-1]]
    rada = [h[d] for d in dny]
    if len(rada) > max_bodu:
        krok = (len(rada) - 1) / (max_bodu - 1)
        rada = [rada[round(i * krok)] for i in range(max_bodu)]
    drzeni = (dt.date.fromisoformat(dny[-1]) - dt.date.fromisoformat(dny[0])).days
    zmena = (ted / pri - 1) if pri else None
    # roční zhodnocení (% p.a.) má smysl až po delší době, jinak by krátké výkyvy vycházely v tisících %
    pa = round(((1 + zmena) ** (365 / drzeni) - 1) * 100, 2) if zmena is not None and drzeni >= MIN_DNU_PA else None
    return {
        "cena_pri": pri, "cena_datum": dny[0], "cena_nyni": ted, "cena_nyni_datum": dny[-1],
        "zmena_pct": round(zmena * 100, 2) if zmena is not None else None,
        "zmena_pa": pa, "dni": drzeni, "graf": rada, "mena": mena(ticker),
    }
