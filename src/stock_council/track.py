"""Track record: how every call has done since, by verdict and by AI. Uses only stored prices."""
from __future__ import annotations

import json

import pandas as pd

from .actions import adjust, aliases
from .council import VOTE_GROUP


def latest_closes(conn, isins: list[str]) -> dict[str, tuple[str, float]]:
    out = {}
    for i in set(isins):
        ids = aliases(conn, i)
        df = pd.read_sql_query(
            f"SELECT date, isin, COALESCE(MAX(CASE WHEN exch='NSE' THEN close END), MAX(close)) close FROM daily "
            f"WHERE isin IN ({','.join('?' * len(ids))}) AND date >= date((SELECT max(date) FROM daily), '-10 days') "
            "GROUP BY date ORDER BY date", conn, params=ids)
        if not df.empty:
            out[i] = (df["date"].iloc[-1], float(df["close"].iloc[-1]))
    return out


def split_factor_since(conn, isin: str, day: str) -> float:
    """Price at the call is in old shares if a split/bonus happened since; scale it to today's terms."""
    df = pd.DataFrame({"date": [day], "isin": [isin], "close": [1.0]})
    return float(adjust(df, conn, price_cols=("close",), volume_cols=()).iloc[0]["close"])


def calls(conn) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT r.id run_id, r.day, r.kind, p.isin, p.symbol, p.name, p.price, p.verdict, p.opinions "
        "FROM picks p JOIN runs r ON r.id=p.run_id WHERE p.verdict IS NOT NULL AND p.price IS NOT NULL").fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    last = latest_closes(conn, df["isin"].tolist())
    df["now_day"] = df["isin"].map(lambda i: last.get(i, (None, None))[0])
    df["now"] = df["isin"].map(lambda i: last.get(i, (None, None))[1])
    df["call_price"] = [p * split_factor_since(conn, i, d) for p, i, d in zip(df["price"], df["isin"], df["day"])]
    df["ret"] = (df["now"] / df["call_price"] - 1) * 100
    df["call"] = df["verdict"].map(lambda v: json.loads(v).get("call"))
    df["score"] = df["verdict"].map(lambda v: json.loads(v).get("avg_score"))
    df["days"] = (pd.to_datetime(df["now_day"]) - pd.to_datetime(df["day"])).dt.days
    return df.sort_values(["day", "run_id"], ascending=False)


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"by_call": [], "by_ai": []}
    df = df.drop_duplicates(["isin", "day"])  # a stock judged twice the same day counts once
    by_call = []
    for call, g in df.groupby("call"):
        by_call.append({"call": call, "n": len(g), "avg": round(float(g["ret"].mean()), 1),
                        "up": int((g["ret"] > 0).sum())})
    by_ai: dict[str, dict] = {}
    for _, r in df.iterrows():
        for o in json.loads(r["opinions"] or "[]"):
            if o.get("error") or not o.get("verdict"):
                continue
            grp = VOTE_GROUP.get(str(o["verdict"]).lower())
            a = by_ai.setdefault(o["name"], {"name": o["name"], "n": 0, "right": 0, "buy_rets": []})
            a["n"] += 1
            # "right" = said buy and it rose, or said sell and it fell (hold is not scored)
            if grp == "BUY":
                a["buy_rets"].append(r["ret"])
                a["right"] += int(r["ret"] > 0)
            elif grp == "SELL":
                a["right"] += int(r["ret"] < 0)
    out = []
    for a in by_ai.values():
        out.append({"name": a["name"], "calls": a["n"], "right": a["right"],
                    "buy_avg": round(float(sum(a["buy_rets"]) / len(a["buy_rets"])), 1) if a["buy_rets"] else None})
    out.sort(key=lambda a: -(a["right"] / a["calls"] if a["calls"] else 0))
    return {"by_call": sorted(by_call, key=lambda c: c["call"]), "by_ai": out}
