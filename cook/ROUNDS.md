# Cook ledger: [project]

Copy to `cook/ROUNDS.md` at the start of a run. Keep the header current. One entry per round, newest at the bottom. Label every number measured or eyeballed.

Started: [YYYY-MM-DD HH:MM UTC]
Size: [light / full] | Time: floor [none / 8h] | cap [2h / none]
Bar: layout [99] | type [99] | color [99] | components [99] | polish [99] | motion [9/10]
Calibration offset: [none yet] (from [Parth's estimate, date])
Gate: stills approved [no] | final [not started]
Best so far: round [N], `cook/stills/r[NN]/`, commit [sha]

## Decisions

Made without asking, one line each.
- [time] [decision], because [reason]

## Feedback inbox

Parth's exact words. Close an item only when a still proves it.
- [time] "[quote]" | round [N] | [open / done, proof: path]

---

## Round [NN]

Time: [start] to [end] | elapsed [h]
Focus: [shot and dimension], because [feedback / lowest score / largest gap]

Changes:
- [what changed, where]

Stills: `cook/stills/r[NN]/` | Side by side: `cook/compare/r[NN]/`

Scores, eyeballed: critic median after calibration ([n] critics, [models])

| Shot | Layout | Type | Color | Components | Polish | Motion | vs last |
|---|---|---|---|---|---|---|---|
| S01 | | | | | | | |

Raw critic medians: [ ]
Own score (logged, never decides): [ ]

Measured (command and result):
- [color check S01-390: saturation +0.00, brightness +0.00]
- [shoot S01-390: overflow none, small taps 0, words 38]

Not checked this round:
- [what, why]

Top gaps from critics:
1. [ ]
2. [ ]
3. [ ]

Progress note sent: [time]
Stop check: bar [no] | floor [n/a] | stills approved [no] | final checked [no] -> [next round / ship / stop at cap]
Next focus: [ ]

## Round 1 (2026-11-24 ~16:30 to 17:15 UTC): mockups of every page
- Focus: Parth asked for examples of all pages in the Instrument style before building.
- Built cook/mockup/instrument-pages.html: 8 pages (Today, Live run, Stock, Screener, How, History, Settings, US Today) + update bar; container queries so Desktop/Phone frames reflow.
- Measured: no page errors; overflow at 390 fixed (Screener inline grid override); 0 overflow after fix.
- Fixes this round: phone charts drawn at 340 wide, ownership end labels value-only on phone, How tab label.
- Published artifact YZzfhEMPSFpPwqw6e92THy for approval. Critics: not run yet (run on the real build).
- Decisions: DII colour violet -> pink (ban on violet accent); no eyebrow labels; status chips sans, not mono caps; IBM Plex kept.
- Next: wait for approval while planning the CSS port; build update bar (/version endpoint + poll).
