# Setup Guide

Step-by-step instructions to get everything running.

## System Requirements

- Python 3.9+
- PostgreSQL 12+
- 8GB RAM minimum (16GB better)
- Internet connection

## API Keys and Accounts

### LLM Provider (pick one)

**Ollama (free, recommended for getting started):**
1. Install Ollama (see [OLLAMA_SETUP.md](OLLAMA_SETUP.md))
2. Pull a model: `ollama pull llama3.1:8b`
3. No API key needed

**DeepSeek (recommended for quality):**
1. Sign up at platform.deepseek.com
2. Get an API key

**OpenAI (paid):**
1. Sign up at platform.openai.com
2. Create an API key

### Bluesky (optional, free)
1. Make an account at bsky.app
2. Go to Settings > App Passwords
3. Generate an app password
4. Save your handle and app password

### Cross-Market APIs (no setup needed)
Kalshi and Manifold APIs are free and don't need auth. Enabled by default.

### Twitter (optional, paid)
1. Apply at developer.twitter.com
2. Create an app
3. Get a Bearer Token

### Reddit (optional)
1. Go to reddit.com/prefs/apps
2. Create a "script" app
3. Save the Client ID and Secret

## Installation

### Step 1: Get the Code
```bash
git clone https://github.com/studyalwaysbro/Polymarket_Agents.git
cd Polymarket_Agents
```

### Step 2: Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate    # Linux/Mac
# or: venv\Scripts\activate  # Windows
```

### Step 3: Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

If that gives trouble, install in groups:
```bash
pip install crewai langchain-openai openai
pip install psycopg2-binary sqlalchemy
pip install praw tweepy
pip install python-dotenv pydantic pydantic-settings rich loguru
```

### Step 4: PostgreSQL

Install it if you haven't:
- **Linux:** `sudo apt-get install postgresql postgresql-contrib`
- **Mac:** `brew install postgresql@14`
- **Windows:** download from postgresql.org

Create the database:
```bash
createdb polymarket_gaps

# Or through psql:
psql -U postgres
CREATE DATABASE polymarket_gaps;
\q
```

Run migrations:
```bash
psql -U postgres -d polymarket_gaps -f migrations/init_db.sql
psql -U postgres -d polymarket_gaps -f migrations/002_upgrade_schema.sql
psql -U postgres -d polymarket_gaps -f migrations/003_cycle_runs.sql
```

### Step 5: Configure

```bash
cp config/.env.example .env
```

Edit `.env`:
```env
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/polymarket_gaps

# Pick your LLM
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b

# Or for DeepSeek:
# LLM_PROVIDER=deepseek
# DEEPSEEK_API_KEY=your_key

# Bluesky (optional)
# BLUESKY_HANDLE=yourname.bsky.social
# BLUESKY_APP_PASSWORD=your-app-password

# Cross-market (enabled by default, free)
ENABLE_KALSHI=true
ENABLE_MANIFOLD=true
ARBITRAGE_MIN_EDGE=0.10

# System
POLLING_INTERVAL=300
MAX_CONTRACTS_PER_CYCLE=20
MIN_CONFIDENCE_SCORE=60
```

### Step 6: Test

```bash
python -c "from src.database import init_database; db = init_database(); print('DB connected' if db.test_connection() else 'Connection failed')"
```

```bash
python -c "from src.config import get_settings; s = get_settings(); s.validate_required_services(); print('Config valid')"
```

## Running

```bash
python run.py demo          # one cycle, verbose
python run.py once          # one cycle, exit
python run.py               # interactive (default)
python run.py continuous    # nonstop
```

## Troubleshooting

**"ModuleNotFoundError"**: activate your venv and reinstall requirements.

**"Could not connect to database"**: check PostgreSQL is running (`pg_isready`), check your `DATABASE_URL` in `.env`.

**"OpenAI API key is required"**: set `LLM_PROVIDER=ollama` if you want free, or add your key.

**No gaps detected**: probably not enough social data yet. Configure Bluesky for more data, or lower `MIN_CONFIDENCE_SCORE` temporarily.

**Rate limiting errors**: increase `POLLING_INTERVAL` or decrease `MAX_CONTRACTS_PER_CYCLE`.

## Logs

Logs go to `logs/`:
- `logs/app.log` for everything INFO and up
- `logs/errors.log` for errors only

Watch them live:
```bash
tail -f logs/app.log
```

## Database Maintenance

```bash
# Stats
python -c "from src.database import get_db_manager; print(get_db_manager().get_stats())"

# Refresh views
python -c "from src.database import get_db_manager; get_db_manager().refresh_materialized_view()"

# Clean old data
python -c "from src.database import get_db_manager; get_db_manager().cleanup_old_data()"
```

## Performance Tuning

Less social media data:
```env
ENABLE_TWITTER=false
ENABLE_REDDIT=false
```

Faster cycles:
```env
MAX_CONTRACTS_PER_CYCLE=5
SENTIMENT_BATCH_SIZE=25
```

Zero LLM cost:
```env
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.1:8b
```

## Security

- Don't commit `.env` (it's in `.gitignore`)
- Use a strong PostgreSQL password
- Keep DB access to localhost
- Rotate API keys occasionally
