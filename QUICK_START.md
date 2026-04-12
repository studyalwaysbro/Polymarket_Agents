# Quick Start

Get this running in about 5 minutes. This guide uses Ollama so you pay nothing.

If you want OpenAI instead, skip to [Option 2](#option-2-openai) below.

## What You Need

- Python 3.9+
- PostgreSQL 12+
- Ollama (instructions below)

## Setup

### 1. Install Ollama

**Linux:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

**Mac:**
```bash
brew install ollama
```

**Windows:**
```bash
winget install Ollama.Ollama
```

Start it and pull a model:
```bash
ollama serve          # keep this running
ollama pull llama3.1:8b   # in another terminal
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Set Up the Database
```bash
createdb polymarket_gaps
psql -d polymarket_gaps -f migrations/init_db.sql
```

### 4. Configure
```bash
cp config/.env.example .env
```

Edit `.env`:
```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/polymarket_gaps
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
```

That's it. No API key needed.

## Run It

```bash
python run.py test    # check everything is wired up
python run.py demo    # run one cycle
python run.py         # run continuously
```

## Common Commands

```bash
python run.py                    # Interactive mode (default)
python run.py continuous         # Nonstop
python run.py once               # One cycle then exit
python run.py demo               # Verbose demo
python run.py test               # Config check

# Database
psql -d polymarket_gaps

# Logs
tail -f logs/app.log
```

---

## Option 2: OpenAI

Same steps but skip Ollama. Just install deps, set up the DB, and set `LLM_PROVIDER=openai` in `.env` with your API key. See [SETUP_GUIDE.md](SETUP_GUIDE.md) for details.

Cost: about $0.10-0.30 per cycle.

---

## Troubleshooting

**"Connection refused" from Ollama**: make sure `ollama serve` is running. Test with `curl http://localhost:11434`.

**Can't connect to database**: check PostgreSQL is running with `pg_isready`.

**"No module named 'crewai'"**: activate your venv and reinstall deps.

**No gaps detected**: might not have enough social data yet, or confidence threshold is too high. Try lowering `MIN_CONFIDENCE_SCORE` in `.env`.

## Config Reference

Minimum `.env` for Ollama:
```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/polymarket_gaps
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
```

Recommended `.env`:
```env
DATABASE_URL=postgresql://postgres:password@localhost:5432/polymarket_gaps
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b

BLUESKY_HANDLE=yourname.bsky.social
BLUESKY_APP_PASSWORD=your-app-password

ENABLE_KALSHI=true
ENABLE_MANIFOLD=true
ARBITRAGE_MIN_EDGE=0.10

POLLING_INTERVAL=300
MIN_CONFIDENCE_SCORE=60
```

## What's Next

- Check [SETUP_GUIDE.md](SETUP_GUIDE.md) for more detailed instructions
- Check [DEVELOPMENT.md](DEVELOPMENT.md) for extending the dashboard
- Check [TRUTH.md](TRUTH.md) for provenance tracking
