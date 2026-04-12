# Development Guide

How to extend the system, particularly around the web dashboard.

## Current Setup

```
┌────────────────────────────┐
│    Multi-Agent System      │
│  (Data, Sentiment, Gaps,   │
│   Reporting)               │
└────────────┬───────────────┘
             │
             v
      ┌──────────────┐
      │  PostgreSQL   │
      └──────────────┘
```

The dashboard (FastAPI + vanilla JS) is already built and running. This guide covers extending it further or building a proper frontend on top.

## Adding a React/Vue Frontend

If you want something more than the built-in vanilla JS dashboard:

### Architecture

```
┌────────────────────────────┐
│    Multi-Agent System      │
└────────────┬───────────────┘
             v
      ┌──────────────┐
      │  PostgreSQL   │ <── FastAPI (already exists)
      └──────────────┘          │
                                v
                     ┌──────────────────┐
                     │  React/Vue App   │
                     └──────────────────┘
```

The FastAPI backend already has endpoints. You'd just build a frontend that talks to them.

### Existing API Endpoints

These are already live at `http://localhost:8000`:
- `GET /api/gaps` with confidence/type filters
- `GET /api/gaps/export` for CSV
- `GET /api/contracts` for top contracts
- `GET /api/sentiment/{contract_id}` for sentiment history
- `GET /api/cycles` for pipeline run history
- `GET /api/sources` for data source status
- `GET /api/backtest` for backtesting results
- `GET /api/alerts` for new gap notifications

### Frontend Structure

```
frontend/
├── src/
│   ├── components/
│   │   ├── GapCard.jsx
│   │   ├── GapList.jsx
│   │   ├── SentimentChart.jsx
│   │   └── Dashboard.jsx
│   ├── services/
│   │   └── api.js
│   └── App.jsx
├── package.json
└── tailwind.config.js
```

### WebSocket for Real-Time

If you want live updates instead of polling, add a WebSocket endpoint:

```python
# src/api/routes/websocket.py
from fastapi import WebSocket, WebSocketDisconnect

class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, websocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message):
        for conn in self.active_connections:
            await conn.send_json(message)
```

Then broadcast new gaps from the detection loop.

### Running Both

```python
# In main.py, the API server already starts alongside the agents
# For a separate frontend dev server:
cd frontend && npm start
# Frontend at :3000, API at :8000
```

## Dashboard Views to Build

1. **Live gaps feed** with filters by confidence, type, category
2. **Contract explorer** with historical odds charts
3. **Sentiment trends** broken down by source (RSS vs Bluesky vs Grok etc)
4. **Performance metrics** tracking prediction accuracy over time
5. **Analytics** showing gap frequency, confidence distribution, category breakdown

## Deployment

Docker setup:
```yaml
services:
  postgres:
    image: postgres:14
    environment:
      POSTGRES_DB: polymarket_gaps
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - ./data:/var/lib/postgresql/data

  detector:
    build: .
    environment:
      - DATABASE_URL=postgresql://postgres:${DB_PASSWORD}@postgres:5432/polymarket_gaps
    depends_on:
      - postgres

  api:
    build: .
    command: uvicorn src.api.server:app --host 0.0.0.0
    ports:
      - "8000:8000"
    depends_on:
      - postgres
```

## Things Worth Adding

- **Alerting**: Telegram/email for high-confidence gaps. The bot infra already exists in other projects.
- **ML sentiment**: FinBERT or similar to reduce LLM costs for bulk sentiment work.
- **Redis caching** if query volume gets high.
- **Better indexes** on the PostgreSQL tables as data grows.

## Multi-Exchange (already done)

Cross-market arbitrage is built in. The gap detector searches Kalshi and Manifold for matching contracts, with semantic inversion detection so it doesn't false-flag markets with flipped wording. Config in `.env`:

```env
ENABLE_KALSHI=true
ENABLE_MANIFOLD=true
ARBITRAGE_MIN_EDGE=0.10
```

## Security for Production

- JWT auth on API endpoints
- Rate limiting
- Input validation
- HTTPS
- DB connection pooling (already in place)
