# Changelog

## [v2.1] Upstream sync, 2026-04-12

Pulled Matt White's latest changes (Apr 2 to Apr 7). See TRUTH.md for full provenance.

### New: Volume Spike Gap Detection

New gap type in `gap_detector.py`. Flags contracts with unusual volume patterns that might indicate incoming price movement. This is a big addition, roughly 390 new lines.

### New: Semantic Inversion Detection

When checking for cross-market arbitrage, the system now detects cases where two markets look related but have opposite semantics. Example: "Will X pass?" on Polymarket vs "Will X fail?" on Kalshi. Before this fix, those would flag as arbitrage opportunities when they're actually consistent.

### Fixes

- Dashboard pipeline progress was accumulating across all cycles instead of resetting per cycle. Fixed.
- Sentiment tracking on the dashboard had an overflow bug. Fixed.
- `.env` files with inline comments (like `KEY=value # comment`) were breaking config parsing. The comment text was getting included in the value. Fixed in `config.py`.

### Other

- Updated `requirements.txt` with dependency changes
- New `report/polymarket_report.pdf` added
- Minor updates to GDELT, Polymarket API, and X mirror scraper services

---

## [v2.0] 2026-03-11

### The big one: multi-source pipeline + dashboard

Took the system from a basic 2-source pipeline (Bluesky + RSS) to 8 data sources with a live web dashboard, ensemble sentiment, smart contract selection, and backtesting.

### 5 New Data Sources

- **Tavily Web Search**: real-time web search API. Needs `TAVILY_API_KEY`. Falls back if missing.
- **GDELT News API**: free global news monitoring, 65+ languages. No key needed.
- **Grok/xAI Sentiment**: X/Twitter sentiment through xAI's API. Needs `GROK_API_KEY`.
- **X Mirror Scraper**: free Nitter/XCancel scraper as a fallback when Grok isn't set up. Uses BeautifulSoup, respects rate limits.
- **FMP Financial Data**: Financial Modeling Prep for market data on finance-related contracts. Needs `FMP_API_KEY`.
- **Polymarket Comments**: scrapes comments from Polymarket's Gamma API. Always available.

### Dual Search

All social sources now do two searches per contract:
1. Keyword search for broad sentiment (like "Trump immigration")
2. Contract title search for people discussing the actual bet

GDELT and RSS stick to keyword-only since news covers topics, not bet titles. Results get deduplicated by `post_id` within each source.

### Smart Contract Selection

Got rid of the old "grab first N contracts" approach. Now it:
- Fetches the full universe (500+ contracts) and stores everything for historical tracking
- Filters out dead markets, no-odds contracts, and basically-resolved ones (97%+ or 3%-)
- Ranks what's left by: volume (30%), volatility (25%), uncertainty (20%), near-expiry (10%), liquidity (10%), spread (5%)
- Processes best contracts first so volatile/breaking stuff gets priority

### Fixed: Polymarket API Parser

Two bugs that were there from the start:
- Odds were always 0. Parser expected `outcomes[0].price` but the API sends `outcomePrices` as a separate array. Fixed.
- Categories were always "Unknown". API nests category inside `events` array, not top-level. Now reads `events[0].category`.

### DeepSeek LLM

Added `deepseek` as an LLM provider option. OpenAI-compatible API, configurable model via `DEEPSEEK_MODEL`. The `get_fast_llm()` function always uses Ollama for cheap tasks no matter what the primary provider is. Missing DeepSeek key auto-falls back to Ollama.

### Ensemble Sentiment

New `EnsembleSentiment` class combining VADER + TextBlob + LLM scores. Weighted formula: `llm_weight * llm_score + (1-llm_weight) * avg(vader, textblob)`. Rolling sentiment windows at 6h, 12h, 24h per contract.

### Dynamic Confidence Scoring

New `ConfidenceScorer` class. Factors in gap size, data volume, cross-source consistency, social source count, and contract features. Confidence gets down-weighted when social data is thin.

### Contract Features

New `ContractFeatureEngine` computing per-contract: `time_to_expiry_hours`, `volatility_24h`, `momentum`, `volume_momentum`, `spread`, `is_near_resolution`, `implied_volatility_proxy`. Stored as JSONB on `DetectedGap`.

### Backtesting

New `Backtester` class. Queries resolved gaps, computes win rate and average edge. Configurable confidence threshold and gap type filters. Results stored in `backtest_results` table, accessible at `/api/backtest`.

### FastAPI Dashboard

Full web dashboard at `http://localhost:8000`:
- Gap explorer with sorting, filtering, confidence badges
- Top contracts ranked by social buzz + gap activity
- Sentiment vs price chart per contract (Recharts)
- Cycle history log with duration, counts, cost estimates
- Data sources panel showing live status of all 8 sources
- New gaps alert banner
- CSV export at `/api/gaps/export`
- Dark theme, auto-refresh (30s sources, 60s alerts)

### Cycle Tracking

New `CycleRun` model tracking every pipeline execution. Records cycle number, timing, success/failure, counts, LLM provider, errors. Migration: `003_cycle_runs.sql`. API: `/api/cycles`.

### Interactive Mode

`python run.py` now defaults to interactive. Runs one cycle, asks before the next. Dashboard stays alive between cycles. Modes: `interactive`, `continuous`, `once`, `demo`, `dashboard`, `monitor`, `test`.

### New Config

All optional, system degrades gracefully if missing:
- `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`
- `TAVILY_API_KEY`, `ENABLE_TAVILY`
- `GROK_API_KEY`, `ENABLE_GROK`
- `FMP_API_KEY`, `ENABLE_FMP`
- `ENABLE_X_MIRROR`, `ENABLE_GDELT`
- `ENABLE_ENSEMBLE_SENTIMENT`
- `ENABLE_BACKTESTING`
- `SCRAPER_REQUEST_DELAY`, `SCRAPER_USER_AGENT`, `SCRAPER_RESPECT_ROBOTS`

### DB Schema Changes

- `migrations/002_upgrade_schema.sql` + `003_cycle_runs.sql`
- New tables: `sentiment_snapshots`, `backtest_results`, `cycle_runs`
- New columns on `SentimentAnalysis`: `vader_score`, `textblob_score`, `ensemble_score`
- New columns on `DetectedGap`: `social_sources_count`, `contract_features`

### 19 New Files, 12 Modified

See TRUTH.md for full provenance on who wrote what.

---

## [v1.1] 2025-02-07

### Bug fixes

- **429 rate limit crash**: Polymarket 429 responses caused infinite recursion. Replaced with a single retry loop, exponential backoff, max 5 attempts.
- **Data loss on social post save**: one failed post would `session.rollback()` and wipe the whole batch. Now commits after each successful post.
- **RSS deduplication**: used Python's `hash()` which changes between runs. Same articles kept getting re-inserted. Switched to SHA-256 of the URL.
- **Duplicate gaps in DB**: every cycle inserted new rows even for the same contract+gap_type. Now deduplicates within `GAP_DEDUPE_HOURS` (default 24h).

### Behavior changes

- Removed hardcoded 10-contract limit for social data. Now configurable via `MAX_CONTRACTS_FOR_SOCIAL` (default 50).
- Sentiment analysis only runs for contracts that actually have social posts. Gap detection only runs where there's sentiment data.

### New config

- `MAX_CONTRACTS_FOR_SOCIAL` (default: 50)
- `GAP_DEDUPE_HOURS` (default: 24)
- `GAP_SENTIMENT_PROB_SCALE` (default: 0.4)
