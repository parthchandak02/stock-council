# stock-council

A daily Indian stock screen with a council of AI analysts.

Every evening after the market closes it:

1. **Screens** every NSE and BSE company for volume shocks: 1-week average volume above 6× the 1-year average, then market cap > ₹500 Cr, P/E < 40, ROE > 12%, debt/equity < 1 (Screener.in definitions, rebuilt from free official data).
2. **Builds a fact sheet** for each hit from official sources: NSE/BSE daily files (price, volume, delivery %), NSE shareholding and pledge filings, and company financials. Cash-flow quality, working-capital days, holdings trend, pledge, technicals and ratios are computed, not guessed.
3. **Asks a council** of seven AI models (via OpenRouter) to judge the same fact sheet plus one shared news search, then counts the votes: Buy / Hold / Sell, how many agree, average score.
4. **Emails** a short table and keeps every call so accuracy can be measured later.

Not investment advice.

## Run it

```bash
uv sync
uv run stock-council update        # first run downloads ~1 year of NSE/BSE files
uv run stock-council screen        # today's list
uv run stock-council sheet HITECH  # one fact sheet
```

Secrets come only from the environment (`OPENROUTER_API_KEY`, Cloudflare email settings); see `src/stock_council/mailer.py`.
