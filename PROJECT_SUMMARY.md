# Project Summary

## What This Is

A multi-agent system that looks for pricing inefficiencies in Polymarket prediction markets. It pulls contract data and social/news sentiment from 8 sources, analyzes everything, and surfaces gaps where market odds might not reflect available information.

## What It Actually Does

1. Monitors active Polymarket contracts (auto-filters expired ones)
2. Collects social media and news data from 8 sources (RSS, Bluesky, GDELT, Tavily, Grok, X Mirror, FMP, Polymarket comments)
3. Runs sentiment analysis using an ensemble of VADER + TextBlob + LLM
4. Detects 5 types of pricing gaps including cross-market arbitrage and volume spikes
5. Reports opportunities ranked by confidence in a formatted console output or web dashboard

## Architecture

**Framework:** CrewAI for multi-agent orchestration. Clean separation between agents, easy to add new ones.

**LLM:** DeepSeek (recommended), Ollama (free), or OpenAI. Cheap tasks like keyword extraction always go through Ollama regardless of primary provider.

**Database:** PostgreSQL. JSONB for flexible evidence storage, good time-series support.

## The Four Agents

### 1. Data Collection
Fetches active contracts via paginated API, collects social/news posts from all configured sources. Uses smart contract selection (filter garbage, rank by composite score, process best-first). Dual search strategy: keyword + contract title. Rate limiting and retry logic on all external calls.

### 2. Sentiment Analysis
Batched LLM analysis (5 posts per call for speed). Ensemble scoring with VADER + TextBlob + LLM. JSON repair for messy LLM output. Falls back to single-post analysis if batch parsing fails. Skips contracts with fewer than 3 posts.

### 3. Gap Detection
Five gap types:
- **Sentiment-probability mismatch**: market odds don't match social mood
- **Information asymmetry**: recent news not priced in yet
- **Historical pattern deviation**: odds way off from historical average
- **Cross-market arbitrage**: price differences vs Kalshi/Manifold, with semantic inversion detection
- **Volume spike**: unusual volume patterns signaling potential movement

Dynamic confidence scoring considers gap size, data volume, source diversity, and contract features.

### 4. Reporting
Fetches recent gaps, ranks by confidence + edge, formats with Rich library. Color-coded panels by confidence level.

## Project Structure

```
polymarket_agents/
├── src/
│   ├── agents/                    # Four agent implementations
│   ├── database/                  # SQLAlchemy models + connection
│   ├── services/                  # External API integrations (12 services)
│   ├── analysis/                  # Backtesting
│   ├── features/                  # Contract feature engineering
│   ├── sentiment/                 # Ensemble sentiment
│   ├── scoring/                   # Confidence scoring
│   ├── dashboard/                 # FastAPI web dashboard
│   ├── config.py                  # Settings (40+ fields)
│   └── main.py                    # Main orchestration
├── migrations/                    # DB schema (3 migration files)
├── config/.env.example
├── requirements.txt
├── run.py                         # Entry point
├── CHANGELOG.md
├── TRUTH.md                       # Provenance tracking
└── README.md
```

## Database

9 tables: contracts, social_posts, sentiment_analysis, detected_gaps, historical_odds, cycle_runs, sentiment_snapshots, backtest_results, system_logs.

## Running It

```bash
python run.py              # Interactive (default): one cycle, asks before next
python run.py continuous   # Nonstop
python run.py once         # One cycle then exit
python run.py demo         # Verbose demo
python run.py dashboard    # Dashboard only
python run.py test         # Validate config
```

Dashboard at `http://localhost:8000` when running.

## Config

All through `.env`. Required: `DATABASE_URL` and an LLM provider. Everything else is optional and degrades gracefully.

## Performance

Typical cycle: 60-90 seconds. Mostly I/O bound (API calls). ~200-500MB memory. $0 with Ollama, ~$0.50/cycle with DeepSeek.

## Provenance

See [TRUTH.md](TRUTH.md) for who wrote what and when, including upstream sync history from Matt White's original repo.

## Disclaimer

Educational/academic project only. All outputs are theoretical. Don't trade based on this. See README.md for full disclaimer.
