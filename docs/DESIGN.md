# Design: Stock Council

Read with `~/.claude/skills/design-principles/references/principles.md`. This file wins where they differ.

## Who uses it
- Dad: daily, on his phone, after the evening India run; acts on the council's call (broker GTT orders by Gmail draft).
- Parth: the US side on his iPhone; also India.
- The one question each visit answers: which stocks are worth a look tonight, and how sure are the AIs.

## Choices
- Look: "Instrument" (approved 24 Nov 2026): few framed panels, shared-border grids, every number against its limit, 7-block votes and a vote ring. Mockup: cook/mockup/instrument-pages.html.
- Fonts: IBM Plex Sans for words, IBM Plex Mono for numbers (Parth asked for a trading font). Overrides the "default font" ban.
- Colours: dark navy first, light too; tokens in static/style.css. DII ownership is pink, not violet (violet ban).
- Save model: autosave everywhere (Settings, Screener edits on the server for everyone; broker order drafts in the browser per stock) with a visible status.
- Actions: filled = does something (Screen, Save, Check, Run now, Ask the council, Open in Gmail); outline = secondary or navigation.
- Navigation: India and US are the same site; switching keeps the section (Screener, History, Settings, ...); a stock or run page goes to that market's Today.
- Missing values show "–" for now (open question: Parth's no-dash rule vs table convention).

## Checks
- `scripts/deploy.sh` runs `scripts/page_sweep.py` (both markets) and the UX check (`ux-check.json`) on a copy of the live data, then deploys and checks live. Never deploy around it.

## Lessons
See the design-principles skill's lessons.md (24 Nov 2026 entries are all from this project): market switch keeps the page, no clipped chips, edits stick, related actions together and alike, no white control flash, no bare error pages, short screen list, no emoji flags, no generic card piles.
