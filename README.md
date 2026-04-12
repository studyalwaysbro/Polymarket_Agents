# Polymarket Pricing Gap Detection System

> **Disclaimer:** This is a solo educational and academic research project. For learning purposes only. All outputs are theoretical. Don't trade based on this.

Multi-agent system I built to learn about NLP, agentic AI, and public API integration using prediction market data.

Latest changes are in [CHANGELOG.md](CHANGELOG.md). For provenance tracking (who wrote what and when), see [TRUTH.md](TRUTH.md).

## How It Works

Four agents coordinated through **CrewAI**:

1. **Data Collection**: fetches Polymarket contracts and social/news data from 8 sources
2. **Sentiment Analysis**: scores sentiment using VADER + TextBlob + LLM ensemble
3. **Gap Detection**: finds pricing inefficiencies across 5 gap types with confidence scoring
4. **Ranking/Reporting**: prioritizes results and formats output

### Data Sources (8 total)

| Source | Type | Cost | Notes |
|--------|------|------|-------|
| Polymarket | Market data | Free | Contract odds, volume, liquidity via Gamma API |
| Bluesky | Social | Free | Public posts via AT Protocol |
| GDELT | News | Free | Global news, 65+ languages |
| RSS Feeds | News | Free | Reuters, BBC, CNN, AP, Google News |
| Polymarket Comments | Social | Free | Comments from Gamma API |
| X Mirror (Nitter) | Social | Free | Public tweets via Nitter mirrors (fallback) |
| Tavily | Web search | Paid | Real-time web search |
| Grok/xAI | Social | Paid | X/Twitter sentiment via xAI API |

All paid sources are optional. System works fine without them.

### LLM Options

| Provider | Cost | Speed | Quality | Config |
|----------|------|-------|---------|--------|
| DeepSeek (recommended) | ~$0.50/cycle | Fast | Excellent | `LLM_PROVIDER=deepseek` |
| Ollama (free) | $0 | Moderate | Good | `LLM_PROVIDER=ollama` |
| OpenAI | ~$0.30/cycle | Fast | Excellent | `LLM_PROVIDER=openai` |

`get_fast_llm()` always uses Ollama for cheap tasks like keyword extraction regardless of your primary LLM.

### Smart Contract Selection

Instead of analyzing everything equally:

1. Fetch the full universe (500+ active contracts)
2. Store all of them in the DB for historical tracking
3. Filter out garbage: dead markets, no-odds, basically-resolved (97%+/3%-)
4. Rank what's left by: volume (30%), volatility (25%), uncertainty (20%), near-expiry (10%), liquidity + spread (15%)
5. Process best-first so volatile/breaking contracts get priority

### Dual Search

Each social source searches twice per contract:
- Keyword search for broad sentiment (e.g. "Trump immigration")
- Contract title search for people talking about the specific bet

GDELT and RSS use keyword-only since news covers topics, not bet titles.

### Gap Types (5)

1. **Sentiment-probability mismatch**: market odds don't match social sentiment
2. **Information asymmetry**: recent news not yet priced in
3. **Historical pattern deviation**: current odds way off from historical average
4. **Cross-market arbitrage**: price differences vs Kalshi/Manifold (with semantic inversion detection so flipped-meaning markets don't false-flag)
5. **Volume spike**: unusual volume patterns that might signal incoming movement

## Features

- 8-source data pipeline
- Ensemble sentiment (VADER + TextBlob + LLM weighted combo with rolling windows)
- Dynamic confidence scoring based on gap size, data volume, source diversity, contract features
- Contract feature engineering (volatility, momentum, time-to-expiry, spread)
- Backtesting framework (win rate + edge on resolved predictions)
- Live FastAPI dashboard with gap explorer, sentiment charts, cycle history, source monitoring
- CSV export for external analysis
- Cycle history tracking with cost estimates
- PostgreSQL for all historical data
- Rate limiting, robots.txt compliance

## Prerequisites

**Required:**
- Python 3.9+
- PostgreSQL 12+

**LLM (pick one):**
- DeepSeek (recommended): platform.deepseek.com
- Ollama (free): see [OLLAMA_SETUP.md](OLLAMA_SETUP.md)
- OpenAI (paid): platform.openai.com

**Optional data sources:**
- Bluesky account (free, bsky.app)
- Tavily API key (tavily.com)
- Grok/xAI API key (x.ai)
- FMP API key (financialmodelingprep.com)

## Installation

1. Clone and install:
```bash
git clone https://github.com/studyalwaysbro/Polymarket_Agents.git
cd Polymarket_Agents
pip install -r requirements.txt
```

2. Set up the database:
```bash
createdb polymarket_gaps
psql -d polymarket_gaps -f migrations/init_db.sql
psql -d polymarket_gaps -f migrations/002_upgrade_schema.sql
psql -d polymarket_gaps -f migrations/003_cycle_runs.sql
```

3. Configure:
```bash
cp config/.env.example .env
# edit .env with your credentials
```

## Configuration

Edit `.env`:

```env
# Database
DATABASE_URL=postgresql://postgres:password@localhost:5432/polymarket_gaps

# LLM (deepseek, ollama, or openai)
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-chat

# Ollama fallback (used for cheap tasks no matter what primary LLM is)
OLLAMA_MODEL=qwen2.5:7b

# Free sources (no keys needed)
ENABLE_GDELT=true
ENABLE_X_MIRROR=true

# Optional paid sources
TAVILY_API_KEY=your_key_here
ENABLE_TAVILY=true

# Bluesky (free, create account at bsky.app)
BLUESKY_HANDLE=yourhandle.bsky.social
BLUESKY_APP_PASSWORD=your-app-password

# System
POLLING_INTERVAL=300
MAX_CONTRACTS_PER_CYCLE=100
ENABLE_BACKTESTING=true
ENABLE_ENSEMBLE_SENTIMENT=true
```

## Usage

### Interactive Mode (default)
```bash
python run.py
# Runs one cycle, asks before the next. Dashboard stays alive at http://localhost:8000
```

### Other Modes
```bash
python run.py continuous    # Nonstop monitoring
python run.py once          # Single cycle then exit
python run.py demo          # Demo with verbose output
python run.py dashboard     # Dashboard only, no analysis
```

### Dashboard

Hit `http://localhost:8000` after starting any mode:
- Gap explorer with filtering and confidence badges
- Top contracts ranked by social buzz + gap activity
- Sentiment vs price chart per contract
- Cycle history with timing and cost estimates
- Live status of all 8 data sources
- Alert banner for newly detected gaps
- CSV export at `/api/gaps/export`

## Project Structure

```
polymarket_agents/
├── src/
│   ├── agents/                    # CrewAI agents
│   │   ├── data_collector.py            # 8-source collection + smart contract selection
│   │   ├── sentiment_analyzer.py        # Ensemble sentiment
│   │   ├── gap_detector.py              # Gap detection + confidence scoring + volume spikes + semantic inversion
│   │   └── reporter.py                  # Ranking and output
│   ├── database/
│   │   ├── models.py                    # Contract, SocialPost, DetectedGap, CycleRun, etc.
│   │   └── connection.py               # DB manager
│   ├── services/                  # External APIs
│   │   ├── polymarket_api.py            # Polymarket CLOB + Gamma + comments
│   │   ├── bluesky_scraper.py           # Bluesky AT Protocol
│   │   ├── rss_news_scraper.py          # RSS news feeds
│   │   ├── gdelt_api.py                 # GDELT global news
│   │   ├── tavily_search.py             # Tavily web search
│   │   ├── grok_sentiment.py            # Grok/xAI X sentiment
│   │   ├── x_mirror_scraper.py          # Nitter/XCancel scraper
│   │   ├── fmp_api.py                   # Financial Modeling Prep
│   │   ├── kalshi_api.py                # Kalshi arbitrage
│   │   ├── manifold_api.py              # Manifold Markets
│   │   ├── twitter_scraper.py           # Twitter/X (optional)
│   │   └── reddit_scraper.py            # Reddit (optional)
│   ├── analysis/
│   │   └── backtester.py                # Backtesting
│   ├── features/
│   │   └── contract_features.py         # Contract features
│   ├── sentiment/
│   │   └── ensemble_sentiment.py        # VADER + TextBlob ensemble
│   ├── scoring/
│   │   └── confidence_scorer.py         # Multi-factor scoring
│   ├── dashboard/
│   │   ├── app.py                       # FastAPI backend
│   │   └── static/index.html            # Frontend
│   ├── utils/logger.py
│   ├── config.py                  # Settings (40+ fields)
│   └── main.py                    # Orchestration + cycle tracking
├── migrations/
│   ├── init_db.sql
│   ├── 002_upgrade_schema.sql
│   └── 003_cycle_runs.sql
├── config/.env.example
├── requirements.txt
├── run.py
├── CHANGELOG.md
├── TRUTH.md
└── README.md
```

## Database

| Table | What It Stores |
|-------|----------------|
| `contracts` | Polymarket contract data, odds, volume, liquidity, category |
| `social_posts` | Posts from all 8 sources |
| `sentiment_analysis` | Per-post sentiment (LLM + VADER + TextBlob + ensemble) |
| `detected_gaps` | Pricing gaps with confidence and contract features |
| `historical_odds` | Time-series odds for trend/volatility |
| `cycle_runs` | Pipeline execution history |
| `sentiment_snapshots` | Aggregated sentiment windows per contract |
| `backtest_results` | Backtesting metrics |
| `system_logs` | Events and errors |

## Roadmap

Done:
- [x] Free LLM via Ollama
- [x] RSS news (Reuters, BBC, CNN, AP, Google News)
- [x] Bluesky integration
- [x] Batched LLM sentiment (5x faster)
- [x] Cross-market arbitrage (Kalshi + Manifold)
- [x] Expired contract filtering
- [x] Paginated contract fetching
- [x] DeepSeek LLM support (v2.0)
- [x] 5 new data sources: Tavily, GDELT, Grok/xAI, X Mirror, FMP (v2.0)
- [x] Ensemble sentiment: VADER + TextBlob + LLM (v2.0)
- [x] Smart contract selection with multi-factor ranking (v2.0)
- [x] Dual search: keyword + contract title (v2.0)
- [x] Contract feature engineering (v2.0)
- [x] Dynamic confidence scoring (v2.0)
- [x] Backtesting framework (v2.0)
- [x] FastAPI dashboard (v2.0)
- [x] CSV export (v2.0)
- [x] Cycle history tracking (v2.0)
- [x] Fixed Polymarket API parser (v2.0)
- [x] Volume spike gap detection (v2.1, upstream)
- [x] Semantic inversion detection for arbitrage (v2.1, upstream)

To do:
- [ ] More social sources (Farcaster, Lens)
- [ ] ML sentiment model (cut LLM costs)
- [ ] Alerting (email/Telegram)
- [ ] Automated trade execution (with safeguards)
- [ ] Supabase cloud DB
- [ ] LangSmith tracing

## Ethical Use

- Rate limiting on all API calls
- robots.txt compliance for scrapers
- Follows platform ToS
- No personal data collection
- All sources clearly attributed

## License

MIT

## Disclaimer

This is a solo educational and academic research project. All outputs, edge calculations, and analysis are theoretical. Not validated for real-world use. Don't trade based on this. Past performance and backtesting don't predict the future. Use at your own risk for learning only.
