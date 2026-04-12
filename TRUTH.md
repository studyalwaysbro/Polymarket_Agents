# Truth System — Polymarket Agents

> **Purpose:** Track provenance of every change — who did what, when, and why. This is the single source of truth for understanding the history of this fork vs upstream.

## Repository Provenance

- **Upstream:** [mwhite732/Polymarket_Agents](https://github.com/mwhite732/Polymarket_Agents)
- **Fork:** [studyalwaysbro/Polymarket_Agents](https://github.com/studyalwaysbro/Polymarket_Agents)
- **Local mirror:** `/home/yeeterson/projects/Polymarket_Agents`
- **Upstream remote:** `upstream` → `https://github.com/mwhite732/Polymarket_Agents.git`
- **Fork remote:** `origin` → `https://github.com/studyalwaysbro/Polymarket_Agents.git`

---

## Sync Protocol

When syncing from upstream:

1. `git fetch upstream`
2. `git log --oneline upstream/main --not main` — review what's new
3. `git diff --stat main..upstream/main` — review scope of changes
4. Record new upstream commits in the **Upstream Sync Log** below BEFORE merging
5. `git merge upstream/main --no-edit`
6. `git push origin main`
7. Update this file with the sync entry

---

## Commit Provenance Map

Every commit, tagged by author origin:

| Hash | Date | Author | Origin | Summary |
|------|------|--------|--------|---------|
| `12b3ad5` | 2026-02-05 | mwhite732 | UPSTREAM | Initial commit (GitHub) |
| `2d9c379` | 2026-02-05 | mwhite732 | UPSTREAM | Initial commit (code) |
| `cb12c2e` | 2026-02-06 | mwhite732 | UPSTREAM | New features and performance improvements |
| `39e61d4` | 2026-02-06 | studyalwaysbro | FORK | Add auto table creation, fix sentiment JSON parsing |
| `435a821` | 2026-02-07 | studyalwaysbro | FORK | Tune gap detection, fix rate limiting, data quality |
| `b6d0242` | 2026-02-07 | mwhite732 | UPSTREAM | Minor performance improvements |
| `08a9c18` | 2026-02-07 | mwhite732 | UPSTREAM | Merge (upstream self-merge) |
| `906cf49` | 2026-03-11 | studyalwaysbro | FORK | v2.0: Multi-source pipeline, dashboard, DeepSeek, smart contracts |
| `f1db3a0` | 2026-03-11 | studyalwaysbro | FORK | Merge upstream into fork |
| `a03e93d` | 2026-03-19 | studyalwaysbro | FORK | Add educational/academic disclaimers |
| `ea00491` | 2026-03-21 | studyalwaysbro | FORK | Upgrade scrapers, tune gap thresholds |
| `9e855a9` | 2026-03-21 | studyalwaysbro | FORK | Auto-commit: python |
| `f06d17f` | 2026-04-01 | studyalwaysbro | FORK | Update data collection and analysis modules |
| `cad81df` | 2026-04-02 | studyalwaysbro | FORK (via upstream) | Auto-commit: polymarket report PDF |
| `2f18d34` | 2026-04-06 | mwhite732 | UPSTREAM | Volume spike gap detection + .env comment fix |
| `d5f5400` | 2026-04-06 | mwhite732 | UPSTREAM | Merge (upstream self-merge) |
| `dbde351` | 2026-04-07 | mwhite732 | UPSTREAM | Semantic inversion detection, dashboard pipeline progress fix, sentiment overflow fix |

---

## Upstream Sync Log

### Sync #3 — 2026-04-12

**Synced commits:** `cad81df`, `2f18d34`, `d5f5400`, `dbde351`
**Upstream date range:** 2026-04-02 → 2026-04-07
**Merge type:** Fast-forward

**What Matt White added:**
- **Semantic inversion detection** in cross-market arbitrage — detects when related markets have inverted semantics (e.g., "Will X happen?" vs "Will X fail?") that create false arbitrage signals
- **Volume spike gap detection** (`gap_detector.py` +390 lines) — new gap type based on unusual volume patterns
- **Dashboard pipeline progress fix** — progress tracker now only shows current cycle, not cumulative
- **Sentiment overflow fix** — fixed overflow in sentiment analysis tracking on dashboard
- **`.env` inline comment parsing fix** (`config.py`) — inline comments in `.env` no longer break value parsing
- **New report PDF** added (`report/polymarket_report.pdf`)
- **Dependency updates** (`requirements.txt` — 80 line changes)

**Files changed:**
- `report/polymarket_report.pdf` — NEW (4586 lines)
- `requirements.txt` — 80 changes
- `src/agents/data_collector.py` — 21 changes
- `src/agents/gap_detector.py` — 390 changes (volume spike detection)
- `src/config.py` — 21 changes (.env parsing)
- `src/dashboard/app.py` — 42 changes (progress fix)
- `src/dashboard/static/index.html` — 2 changes
- `src/services/gdelt_api.py` — 26 changes
- `src/services/polymarket_api.py` — 6 changes
- `src/services/x_mirror_scraper.py` — 11 changes

### Sync #2 — 2026-03-11

**Synced commits:** `b6d0242`, `08a9c18`
**What Matt White added:** Minor performance improvements
**Merge commit:** `f1db3a0`

### Sync #1 — 2026-02-06 (initial fork)

**Forked from:** `cb12c2e`
**Upstream state:** Initial codebase with basic Polymarket pipeline

---

## Fork Contributions (studyalwaysbro)

Things Nick built on top of upstream:

| Date | What | Why |
|------|------|-----|
| 2026-02-06 | Auto table creation, sentiment JSON fix | DB setup automation, parsing robustness |
| 2026-02-07 | Gap detection tuning, rate limiting fix | Data quality, API stability |
| 2026-03-11 | **v2.0 overhaul** | 5 new data sources, dashboard, DeepSeek, smart contracts, ensemble sentiment, backtesting — see CHANGELOG.md |
| 2026-03-19 | Compliance disclaimers | Educational/academic framing |
| 2026-03-21 | Scraper upgrades, gap threshold tuning | Better data quality |
| 2026-04-01 | Data collection + analysis module updates | Ongoing improvements |

---

## How to Use This File

- **Before any upstream sync:** Check `git log upstream/main --not main` and record here FIRST
- **After any local change:** Add to Fork Contributions table
- **When investigating a bug:** Check the Provenance Map to see who wrote the code and when
- **When Matt pushes updates:** The Sync Log tells you exactly what changed and whether it conflicts with fork work
- `git log --format="%h|%ai|%an|%s" --all` regenerates the raw provenance data
