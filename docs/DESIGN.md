# Design: Stock Council

Read with `~/.claude/skills/design-principles/references/principles.md`. This file wins where they differ.

## Who uses it
- Dad: daily, on his phone, after the evening India run; acts on the council's call (broker GTT orders by Gmail draft).
- Parth: the US side on his iPhone; also India.
- The one question each visit answers: which stocks are worth a look tonight, and how sure are the AIs.

## Choices
- Look: "Instrument" (approved 24 Nov 2026): few framed panels, shared-border grids, every number against its limit, 7-block votes and a vote ring. Mockup: cook/mockup/instrument-pages.html.
- Fonts: Instrument Serif for the name, page titles and panel titles; Instrument Sans for everything else, digits even-width (Parth chose option B, 27 Nov 2026: "more consistent" with the header). Tiro Devanagari for सभा.
- Colours: dark navy first, light too; tokens in static/style.css. DII ownership is pink, not violet (violet ban).
- Save model: autosave everywhere (Settings, Screener edits on the server for everyone; broker order drafts in the browser per stock) with a visible status.
- Actions: filled = does something (Screen, Save, Check, Run now, Ask the council, Open in Gmail); outline = secondary or navigation.
- Today (25 Nov 2026, Parth chose "quiet list + tonight card"): a Tonight card (date, screen, count, buy/hold/split/sell bar, compact live timeline, link to how the list was found) then picks grouped by call (name, price, change, votes, score). No funnel, cost or helper text on Today; search is the top-bar icon on every page. First-screen budget: about 50 words.
- Evening run (24 Nov 2026): runs any Screener screen chosen in Settings → Evening run → "Screen to run" (default "Volume shock"), exactly as it reads in the Screener (edits included). No filters in Settings; no person's name anywhere in the UI.
- Charts for the volume-shock study: volume as × usual under the price chart, with the evening screen's spike rule as a line (scale capped so ordinary days stay readable); Results → Health tab (cash conversion, debt/equity, cash cycle by year, with reference lines).
- Navigation: India and US are the same site; switching keeps the section (Screener, History, Settings, ...); a stock or run page goes to that market's Today.
- Charts (24 Nov 2026): interactive price chart on stock and company pages (Lightweight Charts: ranges 1M to Max, line or candles, 50/200-day averages, vs Nifty 50 / S&P 500, volume spikes, call day, hover readout); results as small bar charts; 3-month sparklines in Today and the Screener; return bars in Track record. See the skill's charts.md.
- Company page (/company/<isin>): any listed stock, reached from Screener results and peers; Ask the council from there.
- Missing values show "–" in number slots (Parth approved this exception to the no-dash rule, 24 Nov 2026).

## Checks
- `scripts/deploy.sh` runs `scripts/page_sweep.py` (both markets) and the UX check (`ux-check.json`) on a copy of the live data, then deploys and checks live. Never deploy around it.

## Lessons
See the design-principles skill's lessons.md (24 Nov 2026 entries are all from this project): market switch keeps the page, no clipped chips, edits stick, related actions together and alike, no white control flash, no bare error pages, short screen list, no emoji flags, no generic card piles; 25 Nov: every time says IST or ET, paired labels line up.
