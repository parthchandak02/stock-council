"""Find a company from whatever someone types: a ticker, a BSE code, or (part of) its name.

Searches the latest day's exchange file of this market, and can also look in the other market's
database so an Indian company typed on the US side (or the other way round) is pointed to the right place.
"""
from __future__ import annotations

import re
import sqlite3
import time

from .config import DATA_DIR, MARKET, MARKETS, PRIMARY_EXCH

_cache: dict[str, tuple[float, list[dict]]] = {}
_WORD = re.compile(r"[a-z0-9]+")
_FUND = re.compile(r"\b(mutual fund|etf|exchange traded|bees|index fund)\b|^hdfcamc|amc -", re.I)
_FILLER = {"ltd", "limited", "inc", "corp", "corporation", "co", "company", "the", "plc", "sa", "nv", "ag", "i"}


def _words(s: str) -> list[str]:
    return _WORD.findall((s or "").lower())


def _rows(conn, code: str, primary: str) -> list[dict]:
    """One entry per company: symbols, BSE code and every name it goes by. Cached for 10 minutes."""
    hit = _cache.get(code)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    has_u = conn.execute("SELECT 1 FROM sqlite_master WHERE name='universe'").fetchone()
    q = (f"SELECT d.isin, MAX(CASE WHEN d.exch='{primary}' THEN d.symbol END) sym, "
         "MAX(CASE WHEN d.exch='BSE' THEN d.symbol END) bsym, MAX(CASE WHEN d.exch='BSE' THEN d.code END) bcode, "
         "group_concat(d.name, ' | ') names" + (", MAX(u.name) uname" if has_u else ", NULL uname") +
         " FROM daily d" + (" LEFT JOIN universe u ON u.isin=d.isin" if has_u else "") +
         " WHERE d.date=(SELECT MAX(date) FROM daily) GROUP BY d.isin")
    out = []
    for r in conn.execute(q):
        sym = r["sym"] or r["bsym"]
        if not sym and not r["bcode"]:
            continue
        names = [n for n in [r["uname"], *(r["names"] or "").split(" | ")] if n]
        name = r["uname"] or (max(names, key=len) if names else sym)
        if name.count("(") > name.count(")"):          # exchange files cut long names mid-bracket
            name = name[:name.rfind("(")].strip()
        if _FUND.search(" ".join(names)):              # ETFs and fund units aren't companies to judge
            continue
        out.append({"listed": bool(r["uname"]), "isin": r["isin"], "symbol": sym or r["bcode"], "code": r["bcode"] or "",
                    "name": name, "words": set(w for n in names for w in _words(n))})
    _cache[code] = (time.time(), out)
    return out


def _score(e: dict, q: str, qw: list[str]) -> int:
    Q = q.upper()
    if Q in (e["symbol"].upper(), e["code"]):
        return 100
    key = [w for w in qw if w not in _FILLER] or qw
    if not key:
        return 0
    nw = _words(e["name"])
    if [w for w in nw if w not in _FILLER] == key:
        return 95
    if all(w in e["words"] for w in key):              # every word typed is in its name
        return 80 if nw and nw[0] == key[0] else 70
    if len(key) == 1 and len(key[0]) >= 2:
        if e["symbol"].upper().startswith(Q):
            return 60
        if any(w.startswith(key[0]) for w in e["words"]):
            return 50
    if len(key) > 1 and all(any(w.startswith(k) for w in e["words"]) for k in key):
        return 45
    return 0


def find(conn, q: str, limit: int = 8, *, code: str | None = None, primary: str | None = None) -> list[dict]:
    q = (q or "").strip().removesuffix(".NS").removesuffix(".BO")
    if not q:
        return []
    qw = _words(q)
    rows = _rows(conn, code or MARKET["code"], primary or PRIMARY_EXCH)
    hits = [(s, e) for e in rows if (s := _score(e, q, qw))]
    hits.sort(key=lambda x: (-x[0], not x[1]["listed"], len(x[1]["name"])))   # companies we track before funds
    return [{"isin": e["isin"], "symbol": e["symbol"], "name": e["name"], "score": s} for s, e in hits[:limit]]


def find_other(q: str, limit: int = 3) -> tuple[dict, list[dict]]:
    """The same search in the other market's database (read-only). Returns (that market, hits)."""
    other = MARKETS["us" if MARKET["code"] == "in" else "in"]
    path = DATA_DIR / other["db"]
    if not path.exists():
        return other, []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            hits = find(conn, q, limit, code=other["code"], primary="US" if other["code"] == "us" else "NSE")
        finally:
            conn.close()
    except sqlite3.Error:
        return other, []
    return other, [h for h in hits if h["score"] >= 70]


def resolve(conn, q: str) -> dict | None:
    """The one company the text clearly means, or None when it is unclear or unknown."""
    hits = find(conn, q, 3)
    if not hits:
        return None
    top = hits[0]
    if top["score"] >= 95 or len(hits) == 1 and top["score"] >= 70:
        return top
    if top["score"] >= 70 and hits[1]["score"] < 70:
        return top
    return None
