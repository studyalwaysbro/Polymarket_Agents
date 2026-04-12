# Truth System

This file tracks who did what and when in this repo. If you're looking at a piece of code and wondering where it came from or why it changed, start here.

## Repo Setup

- Upstream: [mwhite732/Polymarket_Agents](https://github.com/mwhite732/Polymarket_Agents) (Matt White's original)
- My fork: [studyalwaysbro/Polymarket_Agents](https://github.com/studyalwaysbro/Polymarket_Agents)
- Local copy: `/home/yeeterson/projects/Polymarket_Agents`
- Git remotes: `origin` = my fork, `upstream` = Matt's repo

## How to Sync from Upstream

Do this every time Matt pushes new stuff:

```bash
git fetch upstream
git log --oneline upstream/main --not main    # see what's new
git diff --stat main..upstream/main           # see what files changed
# RECORD THE NEW COMMITS IN THE SYNC LOG BELOW BEFORE MERGING
git merge upstream/main --no-edit
git push origin main
# update this file
```

## Every Commit, Who Wrote It

| Hash | Date | Who | Source | What |
|------|------|-----|--------|------|
| `12b3ad5` | 2026-02-05 | mwhite732 | UPSTREAM | Initial commit (GitHub default) |
| `2d9c379` | 2026-02-05 | mwhite732 | UPSTREAM | Initial commit (actual code) |
| `cb12c2e` | 2026-02-06 | mwhite732 | UPSTREAM | Features + performance work |
| `39e61d4` | 2026-02-06 | studyalwaysbro | FORK | Auto table creation, sentiment JSON parsing fix |
| `435a821` | 2026-02-07 | studyalwaysbro | FORK | Gap detection tuning, rate limiting fix, data quality |
| `b6d0242` | 2026-02-07 | mwhite732 | UPSTREAM | Minor perf improvements |
| `08a9c18` | 2026-02-07 | mwhite732 | UPSTREAM | Merge (his own branches) |
| `906cf49` | 2026-03-11 | studyalwaysbro | FORK | v2.0: multi-source pipeline, dashboard, DeepSeek, smart contracts |
| `f1db3a0` | 2026-03-11 | studyalwaysbro | FORK | Merged upstream into fork |
| `a03e93d` | 2026-03-19 | studyalwaysbro | FORK | Educational/academic disclaimers |
| `ea00491` | 2026-03-21 | studyalwaysbro | FORK | Scraper upgrades, gap threshold tuning |
| `9e855a9` | 2026-03-21 | studyalwaysbro | FORK | Auto-commit |
| `f06d17f` | 2026-04-01 | studyalwaysbro | FORK | Data collection + analysis updates |
| `cad81df` | 2026-04-02 | studyalwaysbro | FORK (via upstream) | Auto-commit: polymarket report PDF |
| `2f18d34` | 2026-04-06 | mwhite732 | UPSTREAM | Volume spike gap detection + .env comment parsing fix |
| `d5f5400` | 2026-04-06 | mwhite732 | UPSTREAM | Merge (his own branches) |
| `dbde351` | 2026-04-07 | mwhite732 | UPSTREAM | Semantic inversion for arbitrage, dashboard progress fix, sentiment overflow fix |

## Sync Log

### Sync #3, 2026-04-12

Pulled: `cad81df`, `2f18d34`, `d5f5400`, `dbde351`
Date range in upstream: Apr 2 to Apr 7
Merge: fast-forward, no conflicts

What Matt added this time:
- Semantic inversion detection in cross-market arbitrage. Catches cases where two markets look related but have flipped semantics (like "Will X happen?" vs "Will X fail?") so they don't flag as false arbitrage
- Volume spike gap detection: new gap type in `gap_detector.py` (+390 lines). Flags unusual volume patterns
- Dashboard fix: pipeline progress now only tracks the current cycle instead of accumulating across all cycles
- Sentiment overflow fix on the dashboard
- `.env` inline comment parsing: comments after values no longer break config loading
- Added `report/polymarket_report.pdf`
- Dependency updates in `requirements.txt`

Files touched:
- `report/polymarket_report.pdf` (new, 4586 lines)
- `requirements.txt` (80 changes)
- `src/agents/data_collector.py` (21 changes)
- `src/agents/gap_detector.py` (390 changes)
- `src/config.py` (21 changes)
- `src/dashboard/app.py` (42 changes)
- `src/dashboard/static/index.html` (2 changes)
- `src/services/gdelt_api.py` (26 changes)
- `src/services/polymarket_api.py` (6 changes)
- `src/services/x_mirror_scraper.py` (11 changes)

### Sync #2, 2026-03-11

Pulled: `b6d0242`, `08a9c18`
What Matt added: minor perf improvements
Merge commit: `f1db3a0`

### Sync #1, 2026-02-06 (initial fork)

Forked from `cb12c2e`. This was the original codebase with basic Polymarket pipeline.

## What I Built on Top

| Date | What | Why |
|------|------|-----|
| 2026-02-06 | Auto table creation, sentiment JSON fix | DB setup was manual, JSON parsing was breaking |
| 2026-02-07 | Gap detection tuning, rate limiting | Data quality was low, APIs were getting hammered |
| 2026-03-11 | v2.0 overhaul | 5 new data sources, dashboard, DeepSeek, smart contracts, ensemble sentiment, backtesting. Full details in CHANGELOG.md |
| 2026-03-19 | Educational disclaimers | Academic framing for the project |
| 2026-03-21 | Scraper upgrades, gap thresholds | Better data, tighter detection |
| 2026-04-01 | Data collection + analysis updates | Ongoing improvements |

## How to Use This

- Before syncing upstream: check what's new and write it down here first
- After making local changes: add to the "What I Built" table
- Investigating a bug: check the commit map to see who wrote it and when
- Matt pushed an update: the sync log tells you exactly what changed
- Regenerate raw data: `git log --format="%h|%ai|%an|%s" --all`
