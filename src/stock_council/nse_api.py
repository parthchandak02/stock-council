"""Official NSE disclosures: quarterly shareholding pattern (with FII/DII from XBRL) and promoter pledge.

NSE's site moved to www.nseindia.in in 2026 and sits behind bot protection: curl_cffi with a Chrome
fingerprint plus one homepage visit for cookies is enough. Keep requests few and spaced out.
"""
from __future__ import annotations

import re
import time

from curl_cffi import requests

BASE = "https://www.nseindia.in"

# XBRL contexts in the SEBI shareholding-pattern filing -> our labels
HOLDER_CONTEXTS = {
    "promoter": "ShareholdingOfPromoterAndPromoterGroup_ContextI",
    "fii": "InstitutionsForeign_ContextI",
    "dii": "InstitutionsDomestic_ContextI",
    "mutual_funds": "MutualFundsOrUTI_ContextI",
    "public_non_institutions": "NonInstitutions_ContextI",
    "retail_small": "ResidentIndividualShareholdersHoldingNominalShareCapitalUpToRsTwoLakh_ContextI",
}


class NSE:
    def __init__(self) -> None:
        self.s = requests.Session(impersonate="chrome")
        self._warm = False

    def _get_json(self, path: str):
        if not self._warm:
            self.s.get(BASE + "/", timeout=30)
            self._warm = True
            time.sleep(1.5)
        for attempt in range(3):
            r = self.s.get(BASE + "/api/" + path, headers={"Referer": BASE + "/"}, timeout=30)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (attempt + 1))
            self.s.get(BASE + "/", timeout=30)  # refresh cookies
        raise RuntimeError(f"NSE API {path} -> HTTP {r.status_code}")

    def shareholding(self, symbol: str, quarters: int = 5, xbrl_for: set[int] | None = None) -> list[dict]:
        """Latest `quarters` filings, newest first, each with promoter/FII/DII/... in percent.
        xbrl_for: which of them (0 = newest) to read the fund split for; default all."""
        rows = self._get_json(f"corporate-share-holdings-master?index=equities&symbol={symbol}")
        out = []
        for n, row in enumerate(rows[:quarters]):
            q = {"quarter": row.get("date"), "company": row.get("name"), "promoter": _f(row.get("pr_and_prgrp")),
                 "public": _f(row.get("public_val"))}
            if row.get("xbrl") and (xbrl_for is None or n in xbrl_for):
                time.sleep(1)
                try:
                    q.update(self._holders_from_xbrl(row["xbrl"]))
                except Exception as e:  # keep the summary numbers even if one XBRL fails
                    q["xbrl_error"] = str(e)
            out.append(q)
        return out

    def _holders_from_xbrl(self, url: str) -> dict:
        xml = self.s.get(url, timeout=60).text
        out = {}
        for label, ctx in HOLDER_CONTEXTS.items():
            m = re.search(
                rf'<in-bse-shp:ShareholdingAsAPercentageOfTotalNumberOfShares contextRef="{ctx}"[^>]*>([^<]+)<',
                xml,
            )
            if m:
                out[label] = round(float(m.group(1)) * 100, 2)
        return out

    def pledge(self, symbol: str) -> dict | None:
        data = self._get_json(f"corporate-pledgedata?index=equities&symbol={symbol}").get("data") or []
        if not data:
            return None
        d = data[0]
        return {
            "as_of": d.get("shp"),
            "pledged_pct_of_promoter_shares": _f(d.get("percPromoterShares")),
            "pledged_pct_of_total_shares": _f(d.get("percTotShares")),
            "promoter_holding_pct": _f(d.get("percPromoterHolding")),
            "disclosed": d.get("broadcastDt"),
        }


def _f(x):
    try:
        return float(str(x).strip())
    except (TypeError, ValueError):
        return None
