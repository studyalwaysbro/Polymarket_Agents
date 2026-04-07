"""FastAPI dashboard for Polymarket Agents."""

import io
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import func, distinct

from ..database import get_db_manager
from ..database.models import (
    Contract, DetectedGap, SentimentAnalysis, SocialPost,
    SentimentSnapshot, BacktestResult, HistoricalOdds, CycleRun
)
from ..config import get_settings

app = FastAPI(title="Polymarket Agents Dashboard")


# --- Gap endpoints ---

@app.get("/api/gaps")
def get_gaps(
    gap_type: Optional[str] = Query(None),
    min_confidence: int = Query(0),
    market: Optional[str] = Query(None),
    limit: int = Query(50),
    resolved: bool = Query(False)
):
    """Top gaps with filters."""
    db = get_db_manager()
    with db.get_session() as session:
        query = session.query(DetectedGap).join(Contract).filter(
            DetectedGap.resolved == resolved,
            DetectedGap.confidence_score >= min_confidence,
        ).order_by(DetectedGap.confidence_score.desc())

        if gap_type:
            query = query.filter(DetectedGap.gap_type == gap_type)
        if market:
            query = query.filter(Contract.question.ilike(f"%{market}%"))

        gaps = query.limit(limit).all()
        results = []
        for g in gaps:
            d = g.to_dict()
            d['contract_title'] = g.contract.question if g.contract else ''
            results.append(d)

        return {"gaps": results, "count": len(results)}


@app.get("/api/gaps/export")
def export_gaps_csv(
    min_confidence: int = Query(50),
    gap_type: Optional[str] = Query(None)
):
    """Export gaps to CSV."""
    db = get_db_manager()
    with db.get_session() as session:
        query = session.query(DetectedGap).join(Contract).filter(
            DetectedGap.confidence_score >= min_confidence,
        ).order_by(DetectedGap.confidence_score.desc())

        if gap_type:
            query = query.filter(DetectedGap.gap_type == gap_type)

        gaps = query.limit(500).all()

        if not gaps:
            return {"message": "No gaps found"}

        # Build CSV
        import csv
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow([
            'contract_title', 'gap_type', 'confidence_score', 'market_odds',
            'implied_odds', 'edge_percentage', 'social_sources_count',
            'explanation', 'resolved', 'was_correct', 'realized_edge', 'detected_at'
        ])
        for g in gaps:
            writer.writerow([
                g.contract.question if g.contract else '',
                g.gap_type,
                g.confidence_score,
                float(g.market_odds) if g.market_odds else '',
                float(g.implied_odds) if g.implied_odds else '',
                float(g.edge_percentage) if g.edge_percentage else '',
                g.social_sources_count or 0,
                (g.explanation or '')[:200],
                g.resolved,
                g.was_correct,
                float(g.realized_edge) if g.realized_edge else '',
                g.detected_at.isoformat() if g.detected_at else '',
            ])

        stream.seek(0)
        return StreamingResponse(
            iter([stream.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=polymarket_gaps.csv"}
        )


@app.get("/api/sentiment/{contract_id}")
def get_sentiment_history(
    contract_id: str,
    window_hours: int = Query(24)
):
    """Sentiment vs price over time for a contract."""
    from uuid import UUID
    db = get_db_manager()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    with db.get_session() as session:
        # Sentiment data
        sentiments = session.query(SentimentAnalysis).filter(
            SentimentAnalysis.contract_id == UUID(contract_id),
            SentimentAnalysis.analyzed_at >= cutoff,
        ).order_by(SentimentAnalysis.analyzed_at).all()

        sentiment_data = [{
            'score': float(s.ensemble_score or s.sentiment_score or 0),
            'label': s.sentiment_label,
            'time': s.analyzed_at.isoformat(),
        } for s in sentiments]

        # Odds data
        odds = session.query(HistoricalOdds).filter(
            HistoricalOdds.contract_id == UUID(contract_id),
            HistoricalOdds.recorded_at >= cutoff,
        ).order_by(HistoricalOdds.recorded_at).all()

        odds_data = [{
            'yes_probability': float(o.yes_odds),
            'time': o.recorded_at.isoformat(),
        } for o in odds]

        return {
            "contract_id": contract_id,
            "sentiment_data": sentiment_data,
            "odds_data": odds_data,
        }


@app.get("/api/backtest")
def get_backtest(
    confidence_threshold: int = Query(60),
    top_k: int = Query(50)
):
    """Run backtest at given threshold."""
    try:
        from ..analysis import Backtester
        backtester = Backtester()
        return backtester.run_backtest(
            confidence_threshold=confidence_threshold,
            top_k=top_k
        )
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/stats")
def get_stats():
    """System statistics."""
    db = get_db_manager()
    return db.get_stats()


@app.get("/api/progress")
def get_progress():
    """Pipeline progress: collection, sentiment, gap detection stages."""
    db = get_db_manager()
    with db.get_session() as session:
        total_contracts = session.query(func.count(Contract.id)).scalar() or 0
        active_contracts = session.query(func.count(Contract.id)).filter(Contract.active == True).scalar() or 0
        total_gaps = session.query(func.count(DetectedGap.id)).scalar() or 0
        unresolved_gaps = session.query(func.count(DetectedGap.id)).filter(DetectedGap.resolved == False).scalar() or 0

        # Scope sentiment progress to the current cycle only.
        # Use the most recent CycleRun.started_at as the window start; fall back
        # to polling_interval seconds ago if no cycle has been recorded yet.
        latest_cycle = session.query(CycleRun).order_by(CycleRun.started_at.desc()).first()
        if latest_cycle:
            cycle_start = latest_cycle.started_at
            # Ensure timezone-aware for comparisons
            if cycle_start.tzinfo is None:
                cycle_start = cycle_start.replace(tzinfo=timezone.utc)
        else:
            settings = get_settings()
            cycle_start = datetime.now(timezone.utc) - timedelta(seconds=settings.polling_interval)

        cycle_posts = session.query(func.count(SocialPost.id)).filter(
            SocialPost.fetched_at >= cycle_start
        ).scalar() or 0
        cycle_sentiments = session.query(func.count(SentimentAnalysis.id)).filter(
            SentimentAnalysis.analyzed_at >= cycle_start
        ).scalar() or 0

        sentiment_pct = round((cycle_sentiments / cycle_posts * 100), 1) if cycle_posts > 0 else 0
        remaining = max(0, cycle_posts - cycle_sentiments)

        # Posts by platform breakdown (current cycle)
        platform_counts = session.query(
            SocialPost.platform, func.count(SocialPost.id)
        ).filter(SocialPost.fetched_at >= cycle_start).group_by(SocialPost.platform).all()
        platforms = {p: c for p, c in platform_counts}

        # Sentiment label breakdown (current cycle)
        label_counts = session.query(
            SentimentAnalysis.sentiment_label, func.count(SentimentAnalysis.id)
        ).filter(
            SentimentAnalysis.analyzed_at >= cycle_start
        ).group_by(SentimentAnalysis.sentiment_label).all()
        labels = {l: c for l, c in label_counts if l}

        # Recent activity (last 5 min)
        five_min_ago = datetime.now(timezone.utc) - timedelta(minutes=5)
        recent_posts = session.query(func.count(SocialPost.id)).filter(
            SocialPost.fetched_at >= five_min_ago
        ).scalar() or 0
        recent_sentiments = session.query(func.count(SentimentAnalysis.id)).filter(
            SentimentAnalysis.analyzed_at >= five_min_ago
        ).scalar() or 0

        # Pipeline stage
        if cycle_posts == 0:
            stage = "idle"
            stage_label = "Idle - No data collected yet"
        elif sentiment_pct < 95:
            stage = "sentiment"
            stage_label = f"Analyzing sentiment ({sentiment_pct}%)"
        elif total_gaps == 0 and sentiment_pct >= 95:
            stage = "gap_detection"
            stage_label = "Running gap detection..."
        else:
            stage = "complete"
            stage_label = f"Cycle complete - {total_gaps} gaps found"

        return {
            "pipeline": {
                "stage": stage,
                "stage_label": stage_label,
                "cycle_start": cycle_start.isoformat(),
            },
            "collection": {
                "contracts": total_contracts,
                "active_contracts": active_contracts,
                "total_posts": cycle_posts,
                "platforms": platforms,
                "recent_posts_5m": recent_posts,
            },
            "sentiment": {
                "analyzed": cycle_sentiments,
                "remaining": remaining,
                "percent": sentiment_pct,
                "labels": labels,
                "recent_5m": recent_sentiments,
            },
            "gaps": {
                "total": total_gaps,
                "unresolved": unresolved_gaps,
            },
        }


@app.get("/api/contracts")
def get_contracts(limit: int = Query(50)):
    """Active contracts."""
    db = get_db_manager()
    with db.get_session() as session:
        contracts = session.query(Contract).filter(
            Contract.active == True,
        ).order_by(Contract.updated_at.desc()).limit(limit).all()

        return {"contracts": [c.to_dict() for c in contracts]}


# --- Cycle History ---

@app.get("/api/cycles")
def get_cycles(limit: int = Query(20)):
    """Cycle run history."""
    db = get_db_manager()
    with db.get_session() as session:
        cycles = session.query(CycleRun).order_by(
            CycleRun.started_at.desc()
        ).limit(limit).all()
        return {"cycles": [c.to_dict() for c in cycles]}


# --- Top Contracts ---

@app.get("/api/top-contracts")
def get_top_contracts(limit: int = Query(20)):
    """Contracts ranked by social buzz and price movement."""
    db = get_db_manager()
    with db.get_session() as session:
        from sqlalchemy import case
        from sqlalchemy.sql import literal_column

        # Subquery: post count per contract
        post_counts = session.query(
            SentimentAnalysis.contract_id,
            func.count(SentimentAnalysis.id).label('sentiment_count'),
            func.avg(SentimentAnalysis.sentiment_score).label('avg_sentiment'),
        ).group_by(SentimentAnalysis.contract_id).subquery()

        # Subquery: gap count per contract
        gap_counts = session.query(
            DetectedGap.contract_id,
            func.count(DetectedGap.id).label('gap_count'),
            func.max(DetectedGap.confidence_score).label('max_confidence'),
        ).group_by(DetectedGap.contract_id).subquery()

        # Join contracts with counts
        results = session.query(
            Contract,
            post_counts.c.sentiment_count,
            post_counts.c.avg_sentiment,
            gap_counts.c.gap_count,
            gap_counts.c.max_confidence,
        ).outerjoin(
            post_counts, Contract.id == post_counts.c.contract_id
        ).outerjoin(
            gap_counts, Contract.id == gap_counts.c.contract_id
        ).filter(
            Contract.active == True
        ).order_by(
            (func.coalesce(post_counts.c.sentiment_count, 0) +
             func.coalesce(gap_counts.c.gap_count, 0) * 10).desc()
        ).limit(limit).all()

        contracts = []
        for row in results:
            c = row[0]
            d = c.to_dict()
            d['sentiment_count'] = row[1] or 0
            d['avg_sentiment'] = round(float(row[2]), 3) if row[2] else None
            d['gap_count'] = row[3] or 0
            d['max_confidence'] = row[4] or 0
            contracts.append(d)

        return {"contracts": contracts}


# --- Data Sources ---

@app.get("/api/sources")
def get_data_sources():
    """Status of all data sources."""
    settings = get_settings()
    db = get_db_manager()

    with db.get_session() as session:
        # Get post counts by platform
        platform_counts = dict(session.query(
            SocialPost.platform, func.count(SocialPost.id)
        ).group_by(SocialPost.platform).all())

        # Recent posts (last hour) by platform
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_counts = dict(session.query(
            SocialPost.platform, func.count(SocialPost.id)
        ).filter(
            SocialPost.fetched_at >= one_hour_ago
        ).group_by(SocialPost.platform).all())

    sources = [
        {
            "name": "Polymarket",
            "type": "market_data",
            "enabled": True,
            "status": "active",
            "details": "Gamma API",
        },
        {
            "name": "Bluesky",
            "type": "social",
            "enabled": settings.has_bluesky_credentials,
            "status": "active" if platform_counts.get("bluesky", 0) > 0 else ("configured" if settings.has_bluesky_credentials else "disabled"),
            "total_posts": platform_counts.get("bluesky", 0),
            "recent_posts": recent_counts.get("bluesky", 0),
        },
        {
            "name": "GDELT",
            "type": "news",
            "enabled": settings.enable_gdelt,
            "status": "active" if platform_counts.get("gdelt", 0) > 0 else ("configured" if settings.enable_gdelt else "disabled"),
            "total_posts": platform_counts.get("gdelt", 0),
            "recent_posts": recent_counts.get("gdelt", 0),
        },
        {
            "name": "Tavily",
            "type": "web_search",
            "enabled": settings.has_tavily_credentials and settings.enable_tavily,
            "status": "active" if platform_counts.get("tavily_web", 0) > 0 else ("configured" if settings.has_tavily_credentials else "disabled"),
            "total_posts": platform_counts.get("tavily_web", 0),
            "recent_posts": recent_counts.get("tavily_web", 0),
        },
        {
            "name": "X Mirror",
            "type": "social",
            "enabled": settings.enable_x_mirror,
            "status": "active" if platform_counts.get("x_mirror", 0) > 0 else ("configured" if settings.enable_x_mirror else "disabled"),
            "total_posts": platform_counts.get("x_mirror", 0),
            "recent_posts": recent_counts.get("x_mirror", 0),
        },
        {
            "name": "Grok/xAI",
            "type": "social",
            "enabled": settings.has_grok_credentials and settings.enable_grok,
            "status": "active" if platform_counts.get("grok_x", 0) > 0 else ("configured" if settings.has_grok_credentials else "disabled"),
            "total_posts": platform_counts.get("grok_x", 0),
            "recent_posts": recent_counts.get("grok_x", 0),
        },
        {
            "name": "FMP",
            "type": "financial",
            "enabled": settings.has_fmp_credentials and settings.enable_fmp,
            "status": "configured" if settings.has_fmp_credentials else "disabled",
            "total_posts": platform_counts.get("fmp", 0),
            "recent_posts": recent_counts.get("fmp", 0),
        },
        {
            "name": "Polymarket Comments",
            "type": "social",
            "enabled": True,
            "status": "active" if platform_counts.get("polymarket_comment", 0) > 0 else "configured",
            "total_posts": platform_counts.get("polymarket_comment", 0),
            "recent_posts": recent_counts.get("polymarket_comment", 0),
        },
    ]

    # LLM info
    llm_info = {
        "provider": settings.llm_provider,
        "model": (settings.deepseek_model if settings.llm_provider == 'deepseek'
                  else settings.ollama_model if settings.llm_provider == 'ollama'
                  else settings.openai_model),
    }

    return {"sources": sources, "llm": llm_info}


# --- New Gaps Alert ---

@app.get("/api/gaps/recent")
def get_recent_gaps(since_hours: int = Query(1)):
    """Gaps detected in the last N hours (for alerts)."""
    db = get_db_manager()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    with db.get_session() as session:
        count = session.query(func.count(DetectedGap.id)).filter(
            DetectedGap.detected_at >= cutoff
        ).scalar() or 0

        gaps = session.query(DetectedGap).join(Contract).filter(
            DetectedGap.detected_at >= cutoff
        ).order_by(DetectedGap.confidence_score.desc()).limit(5).all()

        results = []
        for g in gaps:
            d = g.to_dict()
            d['contract_title'] = g.contract.question if g.contract else ''
            results.append(d)

        return {"count": count, "gaps": results}


# Serve static frontend
import os
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def root():
        return FileResponse(os.path.join(static_dir, "index.html"))


def start_dashboard(host: str = "0.0.0.0", port: int = 8000):
    """Start the dashboard server."""
    import uvicorn
    print(f"Starting Polymarket Agents Dashboard at http://localhost:{port}")
    uvicorn.run(app, host=host, port=port)
