"""The council: one shared news digest per stock, then every model judges the same fact sheet.

All calls go through OpenRouter (key in OPENROUTER_API_KEY). Votes are counted in code.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import logging
import os
import re
import time
from collections import Counter

import httpx

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"

COUNCIL = [
    {"name": "Gemini", "model": "google/gemini-3.8-flash"},
    {"name": "Claude", "model": "anthropic/claude-haiku-5.5"},
    {"name": "ChatGPT", "model": "openai/gpt-6-luna"},
    {"name": "DeepSeek", "model": "deepseek/deepseek-v4.1-flash"},
    {"name": "Llama", "model": "meta-llama/llama-4-maverick"},
    {"name": "Qwen", "model": "qwen/qwen3.8-flash"},
    {"name": "Grok", "model": "x-ai/grok-4.7"},
]
NEWS_MODEL = "deepseek/deepseek-v4.1-flash"
NEWS_ENGINE = "parallel"

# Dad's prompt (his words, Nov 2026). Editable later from the site's settings page.
DEFAULT_PROMPT = """Answer in max 12 lines, pointwise and short, and analyze thoroughly based on the following institutional criteria:
1. Cash Flow Quality of last 3 years: Operating cash flow as a % of (Net Profit + Depreciation) and Free Cash Flow (FCF) generation.
2. Working Capital & Solvency: Working capital cycle days, and whether the Debt-to-Equity ratio is coming down, stable, or rising.
3. Shareholding & Governance: Promoter, FII, DII, and retail stake direction (up/down) with explicit check on promoter pledged shares percentage.
4. Technical Analysis & Volume: Determine if the stock is in an accumulation or distribution zone, and check for any sudden volume spikes following a long consolidation phase.
5. Business Outlook & Moat: Future demand growth for products/services, 3-year sales CAGR, and industry market-share positioning.
6. Valuation & Efficiency Ratios: TTM P/E, P/B, PEG ratio, ROE, ROCE, ROA, and asset turnover.
7. Technological & AI Impact: Assess how automation, digitization, or AI affects this company's operational efficiency.
8. Final Score: Give a final result in the form of marks out of 10.
9. Actionable Recommendation: Suggest Buy / Sell / Hold / Stepwise Buy / Stepwise Sell with risk parameters."""

SYSTEM = """You are one member of an investment council analysing an Indian listed company for a long-time private investor.
You receive a FACT SHEET computed from official NSE/BSE data and company filings, and a NEWS DIGEST.
Rules:
- Treat every number in the fact sheet as correct. Do not replace it with figures from memory. If you need a number that is not there, say "not in data".
- Use your own knowledge only for business outlook, industry position, moat and technology impact.
- Be decisive and specific. Indian context, ₹ and crore.
Return ONLY a JSON object, no other text:
{"verdict": one of "Buy" | "Stepwise Buy" | "Hold" | "Stepwise Sell" | "Sell",
 "score": number from 0 to 10 (one decimal),
 "stop_loss": price in ₹ or null,
 "entry_zone": short text or null,
 "lines": [up to 12 short strings, one per point, following the investor's criteria],
 "key_risk": one short sentence,
 "trigger_to_change_view": one short sentence}"""

VOTE_GROUP = {"buy": "BUY", "stepwise buy": "BUY", "hold": "HOLD", "stepwise sell": "SELL", "sell": "SELL"}


def _key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY")
    if not k:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return k


def _chat(model: str, messages: list[dict], *, json_mode=False, plugins=None, max_tokens=4000, timeout=240) -> dict:
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.3,
            "usage": {"include": True}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if plugins:
        body["plugins"] = plugins
    headers = {"Authorization": f"Bearer {_key()}", "X-Title": "stock-council",
               "HTTP-Referer": "https://screen.parthchandak.info"}
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(URL, json=body, headers=headers, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                if data.get("choices"):
                    return data
                last = f"no choices: {str(data)[:300]}"
            else:
                last = f"HTTP {r.status_code}: {r.text[:300]}"
        except httpx.HTTPError as e:
            last = str(e)
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"{model}: {last}")


def news_digest(name: str, symbol: str) -> dict:
    prompt = (f"Find the most important news from the last 6 months about {name} (NSE: {symbol}), an Indian listed company: "
              "quarterly results, order wins, capacity expansion, management commentary, fund raising, promoter actions, "
              "regulatory issues, and any reason for unusual trading volume in the last week. "
              "Reply with up to 10 dated bullet points, newest first, each with its source site. Facts only, no opinion.")
    data = _chat(NEWS_MODEL, [{"role": "user", "content": prompt}],
                 plugins=[{"id": "web", "engine": NEWS_ENGINE, "max_results": 8}], max_tokens=4000)
    msg = data["choices"][0]["message"]
    sources = [a["url_citation"]["url"] for a in msg.get("annotations") or [] if a.get("type") == "url_citation"]
    return {"text": (msg.get("content") or "").strip(), "sources": sources, "cost": (data.get("usage") or {}).get("cost")}


def _parse(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    d = json.loads(m.group(0))
    v = str(d.get("verdict", "")).strip()
    d["verdict"] = v.title() if v.lower() in VOTE_GROUP else v
    try:
        d["score"] = round(float(d.get("score")), 1)
    except (TypeError, ValueError):
        d["score"] = None
    d["lines"] = [str(x) for x in (d.get("lines") or [])][:12]
    return d


def ask_member(member: dict, prompt: str, sheet_md: str, news: str) -> dict:
    t0 = time.time()
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"{prompt}\n\n=== FACT SHEET ===\n{sheet_md}\n\n=== NEWS DIGEST ===\n{news}"}]
    out = {"name": member["name"], "model": member["model"]}
    try:
        data = _chat(member["model"], msgs, json_mode=True)
        content = data["choices"][0]["message"].get("content") or ""
        try:
            out.update(_parse(content))
        except (ValueError, json.JSONDecodeError):
            # one retry without JSON mode for models that ignore it
            data = _chat(member["model"], msgs)
            out.update(_parse(data["choices"][0]["message"].get("content") or ""))
        out["cost"] = (data.get("usage") or {}).get("cost")
    except Exception as e:
        out["error"] = str(e)[:300]
    out["seconds"] = round(time.time() - t0, 1)
    return out


def tally(opinions: list[dict]) -> dict:
    ok = [o for o in opinions if not o.get("error") and o.get("verdict")]
    groups = Counter(VOTE_GROUP.get(o["verdict"].lower(), "HOLD") for o in ok)
    scores = [o["score"] for o in ok if o.get("score") is not None]
    if not ok:
        return {"call": "NO DATA", "agree": 0, "voters": 0}
    (top, n), *rest = groups.most_common() + [(None, 0)]
    tie = rest and rest[0][1] == n
    majority = n > len(ok) / 2
    # flavour: did the BUY camp mostly say "Stepwise"?
    flavour = Counter(o["verdict"] for o in ok if VOTE_GROUP.get(o["verdict"].lower()) == top).most_common(1)
    return {
        "call": "NO CONSENSUS" if tie or not majority else top,
        "lean": top,
        "flavour": flavour[0][0] if flavour else None,
        "agree": n, "voters": len(ok), "votes": dict(groups),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
        "min_score": min(scores) if scores else None, "max_score": max(scores) if scores else None,
        "stop_losses": sorted(o["stop_loss"] for o in ok if isinstance(o.get("stop_loss"), (int, float))),
    }


def run(sheet: dict, sheet_md: str, prompt: str = DEFAULT_PROMPT, members=COUNCIL) -> dict:
    news = news_digest(sheet["name"], sheet.get("nse_symbol") or sheet.get("bse_symbol"))
    with cf.ThreadPoolExecutor(max_workers=len(members)) as ex:
        opinions = list(ex.map(lambda m: ask_member(m, prompt, sheet_md, news["text"]), members))
    costs = [o.get("cost") or 0 for o in opinions] + [news.get("cost") or 0]
    return {"news": news, "opinions": opinions, "verdict": tally(opinions),
            "cost_usd": round(sum(costs), 4), "prompt": prompt}
