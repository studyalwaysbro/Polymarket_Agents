"""Gap Detection Agent - Identifies pricing inefficiencies in prediction markets."""

import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

sys.path.insert(0, str(Path.home() / ".api-monitor"))

from crewai import Agent, Task
from sqlalchemy import func

from ..config import get_settings, get_llm
from ..database import get_db_manager
from ..database.models import Contract, DetectedGap, SentimentAnalysis, HistoricalOdds
from ..services.kalshi_api import KalshiAPI
from ..services.manifold_api import ManifoldAPI
from ..utils.logger import get_logger

logger = get_logger(__name__)


class GapDetectionAgent:
    """
    Agent responsible for detecting pricing gaps in prediction markets.

    Identifies four types of gaps:
    1. Sentiment-Probability Mismatches: Market odds don't align with social sentiment
    2. Information Asymmetry: New information not yet reflected in prices
    3. Cross-Market Arbitrage: Pricing inconsistencies across Kalshi and Manifold
    4. Historical Pattern Deviations: Unusual odds movements compared to history
    """

    def __init__(self):
        """Initialize Gap Detection Agent."""
        self.settings = get_settings()
        self.db_manager = get_db_manager()

        # Initialize LLM (OpenAI or Ollama based on config)
        self.llm = get_llm()

        # Initialize cross-market API clients for arbitrage detection
        self.kalshi_api = KalshiAPI()
        self.manifold_api = ManifoldAPI()

        # Initialize confidence scorer
        self.confidence_scorer = None
        try:
            from ..scoring import ConfidenceScorer
            self.confidence_scorer = ConfidenceScorer()
        except Exception as e:
            logger.warning(f"Confidence scorer unavailable: {e}")

        # Initialize contract feature engine
        self.feature_engine = None
        try:
            from ..features import ContractFeatureEngine
            self.feature_engine = ContractFeatureEngine()
        except Exception as e:
            logger.warning(f"Feature engine unavailable: {e}")

        logger.info(f"Gap Detection Agent initialized with {self.settings.llm_provider}")

    def _invoke_llm(self, prompt: str) -> str:
        """Call LLM and return the response text, handling provider differences."""
        response = self.llm.invoke(prompt)
        try:
            from api_logger import log_api_call
            meta = getattr(response, "response_metadata", {}) or {}
            usage = meta.get("token_usage", {}) or {}
            log_api_call("deepseek", "/chat/completions", project="polymarket-agents",
                         tokens_in=usage.get("prompt_tokens", 0),
                         tokens_out=usage.get("completion_tokens", 0))
        except Exception:
            pass
        if hasattr(response, 'content'):
            return response.content.strip()
        elif isinstance(response, str):
            return response.strip()
        return str(response).strip()

    @staticmethod
    def _clean_json(text: str) -> str:
        """Strip markdown code fences and repair common LLM JSON issues."""
        import re

        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()

        first_bracket = min(
            (text.find('[') if text.find('[') >= 0 else len(text)),
            (text.find('{') if text.find('{') >= 0 else len(text))
        )
        if first_bracket < len(text):
            text = text[first_bracket:]

        last_close = max(text.rfind(']'), text.rfind('}'))
        if last_close >= 0:
            text = text[:last_close + 1]

        text = re.sub(r',\s*([}\]])', r'\1', text)

        return text

    def create_crewai_agent(self) -> Agent:
        """
        Create CrewAI agent definition.

        Returns:
            CrewAI Agent instance
        """
        return Agent(
            role='Market Inefficiency Detector',
            goal='Identify pricing gaps and inefficiencies in prediction markets',
            backstory="""You are an expert quantitative analyst specializing in prediction
            markets. You excel at identifying mispricings by analyzing sentiment data,
            historical patterns, and market dynamics. You provide clear reasoning for
            each identified opportunity.""",
            verbose=True,
            allow_delegation=False,
            llm=self.llm
        )

    def detect_sentiment_mismatch(self, contract_id: str) -> Optional[Dict]:
        """
        Detect sentiment-probability mismatch for a contract.

        Args:
            contract_id: Contract UUID as string

        Returns:
            Gap dictionary if detected, None otherwise
        """
        try:
            with self.db_manager.get_session() as session:
                # Get contract
                contract = session.query(Contract).filter(
                    Contract.id == UUID(contract_id)
                ).first()

                if not contract or not contract.current_yes_odds:
                    return None

                # Get sentiment data
                sentiment_analyses = session.query(SentimentAnalysis).filter(
                    SentimentAnalysis.contract_id == UUID(contract_id)
                ).all()

                if not sentiment_analyses or len(sentiment_analyses) < 3:
                    # Need sufficient data
                    return None

                # Calculate aggregate sentiment (prefer ensemble_score if available)
                scores = []
                for s in sentiment_analyses:
                    if s.ensemble_score is not None:
                        scores.append(float(s.ensemble_score))
                    elif s.sentiment_score is not None:
                        scores.append(float(s.sentiment_score))
                avg_sentiment = sum(scores) / len(scores) if scores else 0.0
                positive_count = sum(1 for s in sentiment_analyses if s.sentiment_label == 'positive')
                positive_ratio = positive_count / len(sentiment_analyses)

                # Current market odds
                market_odds = float(contract.current_yes_odds)

                # Use sentiment as a RELATIVE adjustment to market price.
                # Positive sentiment suggests the market should be higher;
                # negative sentiment suggests the market should be lower.
                scale = getattr(self.settings, 'gap_sentiment_prob_scale', 0.4)
                sentiment_adjustment = avg_sentiment * scale
                implied_prob = market_odds + sentiment_adjustment
                implied_prob = max(0.05, min(0.95, implied_prob))  # Clamp to valid range
                implied_odds = Decimal(str(round(implied_prob, 4)))

                # Calculate gap
                gap_size = abs(implied_prob - market_odds)

                # Check if gap exceeds threshold
                if gap_size < self.settings.gap_detection_threshold:
                    return None

                # Determine direction and confidence
                if implied_prob > market_odds:
                    direction = "bullish"
                    edge = (implied_prob - market_odds) * 100
                else:
                    direction = "bearish"
                    edge = (market_odds - implied_prob) * 100

                # Calculate confidence score (0-100)
                # Count distinct social platforms
                social_platforms = set()
                for s in sentiment_analyses:
                    if hasattr(s, 'post') and s.post and hasattr(s.post, 'platform'):
                        social_platforms.add(s.post.platform)
                social_sources_count = len(social_platforms)

                # Compute contract features if engine available
                contract_features = {}
                if self.feature_engine:
                    try:
                        hist = session.query(HistoricalOdds).filter(
                            HistoricalOdds.contract_id == UUID(contract_id)
                        ).order_by(HistoricalOdds.recorded_at.asc()).all()
                        hist_dicts = [{'yes_odds': float(h.yes_odds), 'volume': float(h.volume or 0)} for h in hist]
                        contract_features = self.feature_engine.compute_features(
                            contract.to_dict(), hist_dicts
                        )
                    except Exception:
                        pass

                if self.confidence_scorer:
                    consistency = abs(positive_ratio - 0.5) * 2
                    confidence = self.confidence_scorer.score(
                        gap_type='sentiment_mismatch',
                        gap_size=gap_size,
                        data_volume=len(sentiment_analyses),
                        sentiment_consistency=consistency,
                        social_sources_count=social_sources_count,
                        contract_features=contract_features,
                    )
                else:
                    # Fallback to inline calculation
                    gap_factor = min(gap_size / 0.15, 1.0) * 40
                    volume_factor = min(len(sentiment_analyses) / 15, 1.0) * 30
                    consistency_factor = abs(positive_ratio - 0.5) * 2 * 30
                    confidence = min(max(int(gap_factor + volume_factor + consistency_factor), 0), 100)

                # Generate explanation using LLM
                explanation = self._generate_gap_explanation(
                    contract=contract,
                    gap_type="sentiment_mismatch",
                    market_odds=market_odds,
                    implied_odds=float(implied_odds),
                    sentiment_data={
                        'avg_sentiment': avg_sentiment,
                        'positive_ratio': positive_ratio,
                        'total_posts': len(sentiment_analyses),
                        'direction': direction
                    }
                )

                return {
                    'contract_id': contract_id,
                    'gap_type': 'sentiment_mismatch',
                    'confidence_score': confidence,
                    'explanation': explanation,
                    'market_odds': contract.current_yes_odds,
                    'implied_odds': implied_odds,
                    'edge_percentage': Decimal(str(round(edge, 2))),
                    'social_sources_count': social_sources_count,
                    'contract_features': contract_features,
                    'evidence': {
                        'avg_sentiment': round(avg_sentiment, 3),
                        'positive_ratio': round(positive_ratio, 3),
                        'total_posts': len(sentiment_analyses),
                        'direction': direction,
                        'gap_size': round(gap_size, 3),
                        'social_sources': list(social_platforms),
                    }
                }

        except Exception as e:
            logger.error(f"Error detecting sentiment mismatch: {e}")
            return None

    def detect_information_asymmetry(self, contract_id: str) -> Optional[Dict]:
        """
        Detect information asymmetry gaps (recent news not reflected in odds).

        Requires three concurrent signals to reduce false positives:
        1. A source-weighted sentiment shift in the recent window vs baseline
        2. A volume spike (more posts per hour than baseline rate)
        3. The market price not having caught up to the expected move

        News articles (news_rss) are weighted 3x; Reddit 1.5x; social 1x.
        Engagement score provides a secondary weight boost (up to 1.5x).
        """
        # Source quality weights — news > reddit > social
        SOURCE_WEIGHTS = {'news_rss': 3.0, 'reddit': 1.5}
        DEFAULT_WEIGHT = 1.0

        # Time windows: 2-hour "recent" vs 2-6 hour "baseline"
        RECENT_HOURS = 2
        BASELINE_HOURS = 6

        try:
            with self.db_manager.get_session() as session:
                contract = session.query(Contract).filter(
                    Contract.id == UUID(contract_id)
                ).first()

                if not contract or not contract.current_yes_odds:
                    return None

                now = datetime.now(timezone.utc)
                recent_cutoff = now - timedelta(hours=RECENT_HOURS)
                baseline_cutoff = now - timedelta(hours=BASELINE_HOURS)

                # Fetch recent sentiment (with posts for platform/engagement data)
                recent_analyses = session.query(SentimentAnalysis).join(
                    SentimentAnalysis.post
                ).filter(
                    SentimentAnalysis.contract_id == UUID(contract_id),
                    SentimentAnalysis.analyzed_at >= recent_cutoff
                ).all()

                # Fetch baseline sentiment
                baseline_analyses = session.query(SentimentAnalysis).join(
                    SentimentAnalysis.post
                ).filter(
                    SentimentAnalysis.contract_id == UUID(contract_id),
                    SentimentAnalysis.analyzed_at >= baseline_cutoff,
                    SentimentAnalysis.analyzed_at < recent_cutoff
                ).all()

                # Minimum volume requirements
                if len(recent_analyses) < 5 or len(baseline_analyses) < 3:
                    return None

                # --- Volume spike check ---
                # Normalize both windows to posts/hour for a fair comparison
                baseline_window_hours = BASELINE_HOURS - RECENT_HOURS  # 4 hours
                recent_rate = len(recent_analyses) / RECENT_HOURS
                baseline_rate = len(baseline_analyses) / baseline_window_hours
                volume_spike_ratio = recent_rate / baseline_rate if baseline_rate > 0 else 1.0

                # --- Source-weighted sentiment ---
                def weighted_avg(analyses):
                    total_weight = 0.0
                    weighted_sum = 0.0
                    for s in analyses:
                        score = float(
                            s.ensemble_score if s.ensemble_score is not None
                            else s.sentiment_score
                        )
                        platform = s.post.platform if s.post else 'unknown'
                        weight = SOURCE_WEIGHTS.get(platform, DEFAULT_WEIGHT)
                        # Engagement boost: up to 1.5x for highly-engaged posts
                        engagement = (s.post.engagement_score or 0) if s.post else 0
                        weight *= 1.0 + (min(engagement, 100) / 100.0) * 0.5
                        weighted_sum += score * weight
                        total_weight += weight
                    return weighted_sum / total_weight if total_weight > 0 else 0.0

                recent_avg = weighted_avg(recent_analyses)
                baseline_avg = weighted_avg(baseline_analyses)
                sentiment_shift = recent_avg - baseline_avg

                if abs(sentiment_shift) < 0.15:
                    return None

                # --- Directional consistency ---
                # At least 60% of recent posts must agree on direction
                shift_direction = 1 if sentiment_shift > 0 else -1
                agreeing = sum(
                    1 for s in recent_analyses
                    if (float(s.sentiment_score) * shift_direction) > 0
                )
                consistency = agreeing / len(recent_analyses)

                if consistency < 0.60:
                    return None

                # --- Odds movement over the same window ---
                # Get the most recent historical odds record before recent_cutoff
                # so we compare market movement over exactly the sentiment window.
                odds_at_window_start = session.query(HistoricalOdds).filter(
                    HistoricalOdds.contract_id == UUID(contract_id),
                    HistoricalOdds.recorded_at <= recent_cutoff
                ).order_by(HistoricalOdds.recorded_at.desc()).first()

                if not odds_at_window_start:
                    # Fall back to the oldest available record
                    odds_at_window_start = session.query(HistoricalOdds).filter(
                        HistoricalOdds.contract_id == UUID(contract_id)
                    ).order_by(HistoricalOdds.recorded_at.asc()).first()

                if not odds_at_window_start:
                    return None

                odds_then = float(odds_at_window_start.yes_odds)
                odds_now = float(contract.current_yes_odds)
                odds_movement = odds_now - odds_then

                # Estimate how much the market should have moved given the signal
                scale = getattr(self.settings, 'gap_sentiment_prob_scale', 0.4)
                expected_move = abs(sentiment_shift) * scale
                market_lag = expected_move - abs(odds_movement)

                # If the market has already moved as much or more than expected, no asymmetry
                if abs(odds_movement) >= expected_move:
                    return None

                # Counter-move: market moved opposite to sentiment (stronger signal)
                odds_direction = 1 if odds_movement > 0 else -1 if odds_movement < 0 else 0
                counter_move = (odds_direction != 0 and odds_direction != shift_direction)

                # --- Confidence scoring (max 100) ---
                # Shift magnitude: 0-30
                shift_factor = min(abs(sentiment_shift) / 0.5, 1.0) * 30
                # Volume spike: 0-25 (0 at 1x rate, 25 at 3x+ rate)
                volume_factor = max(0.0, min((volume_spike_ratio - 1.0) / 2.0, 1.0) * 25)
                # Directional consistency: 0-20 (0 at 60%, 20 at 100%)
                consistency_factor = max(0.0, (consistency - 0.60) / 0.40 * 20)
                # Market lag size: 0-15
                lag_factor = min(market_lag / 0.15, 1.0) * 15
                # Counter-move bonus: 10
                counter_factor = 10 if counter_move else 0

                confidence = int(shift_factor + volume_factor + consistency_factor + lag_factor + counter_factor)
                confidence = max(0, min(confidence, 100))

                # --- Implied probability and edge ---
                implied_move = sentiment_shift * scale
                implied_prob = max(0.05, min(0.95, odds_now + implied_move))
                edge_pct = abs(implied_prob - odds_now) * 100

                # Source breakdown for evidence
                sources_breakdown: Dict[str, int] = {}
                for s in recent_analyses:
                    platform = s.post.platform if s.post else 'unknown'
                    sources_breakdown[platform] = sources_breakdown.get(platform, 0) + 1

                direction_str = "bullish" if sentiment_shift > 0 else "bearish"

                explanation = self._generate_gap_explanation(
                    contract=contract,
                    gap_type="info_asymmetry",
                    market_odds=odds_now,
                    implied_odds=implied_prob,
                    sentiment_data={
                        'weighted_sentiment_shift': round(sentiment_shift, 3),
                        'recent_avg': round(recent_avg, 3),
                        'baseline_avg': round(baseline_avg, 3),
                        'volume_spike_ratio': round(volume_spike_ratio, 2),
                        'consistency': round(consistency, 2),
                        'odds_movement_in_window': round(odds_movement, 4),
                        'has_news_sources': 'news_rss' in sources_breakdown,
                        'direction': direction_str,
                    }
                )

                return {
                    'contract_id': contract_id,
                    'gap_type': 'info_asymmetry',
                    'confidence_score': confidence,
                    'explanation': explanation,
                    'market_odds': contract.current_yes_odds,
                    'implied_odds': Decimal(str(round(implied_prob, 4))),
                    'edge_percentage': Decimal(str(round(edge_pct, 2))),
                    'evidence': {
                        'weighted_sentiment_shift': round(sentiment_shift, 3),
                        'recent_avg_sentiment': round(recent_avg, 3),
                        'baseline_avg_sentiment': round(baseline_avg, 3),
                        'recent_posts': len(recent_analyses),
                        'baseline_posts': len(baseline_analyses),
                        'volume_spike_ratio': round(volume_spike_ratio, 2),
                        'consistency': round(consistency, 2),
                        'sources_breakdown': sources_breakdown,
                        'has_news_sources': 'news_rss' in sources_breakdown,
                        'odds_at_window_start': round(odds_then, 4),
                        'odds_movement': round(odds_movement, 4),
                        'market_lag': round(market_lag, 4),
                        'direction': direction_str,
                        'counter_move': counter_move,
                    }
                }

        except Exception as e:
            logger.error(f"Error detecting information asymmetry: {e}")
            return None

    def detect_pattern_deviation(self, contract_id: str) -> Optional[Dict]:
        """
        Detect historical pattern deviations.

        Args:
            contract_id: Contract UUID as string

        Returns:
            Gap dictionary if detected, None otherwise
        """
        try:
            with self.db_manager.get_session() as session:
                # Get contract
                contract = session.query(Contract).filter(
                    Contract.id == UUID(contract_id)
                ).first()

                if not contract or not self.settings.enable_historical_analysis:
                    return None

                # Get historical odds
                historical = session.query(HistoricalOdds).filter(
                    HistoricalOdds.contract_id == UUID(contract_id)
                ).order_by(HistoricalOdds.recorded_at.asc()).all()

                if len(historical) < 10:
                    # Need sufficient history
                    return None

                # Calculate volatility and trends
                odds_values = [float(h.yes_odds) for h in historical]
                current_odds = odds_values[-1]

                # Calculate moving average and standard deviation
                avg_odds = sum(odds_values) / len(odds_values)
                variance = sum((x - avg_odds) ** 2 for x in odds_values) / len(odds_values)
                std_dev = variance ** 0.5

                # Check for unusual deviations
                z_score = (current_odds - avg_odds) / std_dev if std_dev > 0 else 0

                if abs(z_score) < 1.5:  # Not unusual enough (was 2.0)
                    return None

                # Calculate confidence based on deviation magnitude
                confidence = int(min(abs(z_score) / 3.0, 1.0) * 70 + 10)

                # Generate explanation
                explanation = self._generate_gap_explanation(
                    contract=contract,
                    gap_type="pattern_deviation",
                    market_odds=current_odds,
                    implied_odds=avg_odds,
                    sentiment_data={
                        'z_score': z_score,
                        'std_dev': std_dev,
                        'avg_odds': avg_odds
                    }
                )

                return {
                    'contract_id': contract_id,
                    'gap_type': 'pattern_deviation',
                    'confidence_score': confidence,
                    'explanation': explanation,
                    'market_odds': contract.current_yes_odds,
                    'implied_odds': Decimal(str(round(avg_odds, 4))),
                    'edge_percentage': Decimal(str(round(abs(current_odds - avg_odds) * 100, 2))),
                    'evidence': {
                        'z_score': round(z_score, 2),
                        'std_dev': round(std_dev, 4),
                        'avg_odds': round(avg_odds, 4),
                        'historical_points': len(historical)
                    }
                }

        except Exception as e:
            logger.error(f"Error detecting pattern deviation: {e}")
            return None

    def _generate_gap_explanation(
        self,
        contract: Contract,
        gap_type: str,
        market_odds: float,
        implied_odds: Optional[float],
        sentiment_data: Dict
    ) -> str:
        """
        Generate human-readable explanation using LLM.

        Args:
            contract: Contract object
            gap_type: Type of gap detected
            market_odds: Current market odds
            implied_odds: Implied odds from analysis
            sentiment_data: Supporting sentiment data

        Returns:
            Explanation string
        """
        try:
            prompt = f"""Generate a clear, concise explanation for a pricing gap in a prediction market.

Market Question: "{contract.question}"
Current Market Odds: {market_odds:.1%} YES
Gap Type: {gap_type}
{f"Implied Odds: {implied_odds:.1%}" if implied_odds else ""}

Supporting Data: {json.dumps(sentiment_data, indent=2)}

Provide a 2-3 sentence explanation that:
1. Describes the gap clearly
2. Explains why it exists
3. Notes the direction (bullish/bearish opportunity)

Be specific and actionable. Do not use phrases like "might" or "could be" - be direct.
"""

            explanation = self._invoke_llm(prompt)

            return explanation

        except Exception as e:
            logger.error(f"Error generating explanation: {e}")
            # Fallback explanation
            return f"{gap_type.replace('_', ' ').title()} detected. Market odds at {market_odds:.1%}."

    def _extract_search_query(self, question: str) -> str:
        """
        Extract a concise search query from a Polymarket contract question.

        Removes filler words and keeps the core subject for cross-platform search.
        """
        stop_words = {
            'will', 'the', 'be', 'in', 'on', 'by', 'a', 'an', 'of', 'to',
            'for', 'is', 'at', 'or', 'and', 'this', 'that', 'with', 'from',
            'before', 'after', 'during', 'than', 'more', 'less', 'over',
            'under', 'between', 'above', 'below', 'how', 'many', 'much',
            'what', 'which', 'who', 'when', 'where', 'does', 'do', 'did',
            'has', 'have', 'had', 'been', 'being', 'are', 'was', 'were',
            'would', 'could', 'should', 'may', 'might', 'can', 'shall',
        }

        # Remove question mark and common punctuation
        cleaned = question.replace('?', '').replace(',', ' ').replace("'s", '')
        words = cleaned.split()

        # Keep significant words (proper nouns, key terms)
        significant = []
        for word in words:
            lower = word.lower()
            if lower not in stop_words and len(word) > 1:
                significant.append(word)

        # Return first 4-5 significant words as the search query
        return ' '.join(significant[:5])

    def _match_markets_with_llm(self, polymarket_question: str, candidates: List[Dict]) -> List[Dict]:
        """
        Use LLM to determine which candidate markets match the Polymarket question.

        Args:
            polymarket_question: The Polymarket contract question
            candidates: List of candidate markets from other platforms

        Returns:
            List of confirmed matching markets with match_confidence
        """
        if not candidates:
            return []

        # Limit to top 8 candidates to keep prompt small for local LLMs
        candidates = candidates[:8]

        numbered = "\n".join(
            f'{i+1}. [{c["platform"]}] "{c["question"]}" (probability: {c["probability"]:.1%})'
            for i, c in enumerate(candidates)
        )

        prompt = f"""Determine which of these prediction markets are about the SAME event as the Polymarket question.

Polymarket question: "{polymarket_question}"

Candidate markets from other platforms:
{numbered}

For each candidate, respond with ONLY a valid JSON array. Each element should have:
- "index": the candidate number (1-based)
- "match": true or false — true only if they resolve on the same real-world event
- "confidence": 0.0 to 1.0 (how confident this is the same event)
- "inverted": true if the questions are semantically OPPOSITE (e.g. "Will X resign?" vs "Will X remain in office?"), false otherwise

Key rule: markets can cover the same event but be framed in opposite directions.
For example, "Will Trump resign by 2026?" and "Will Trump be president at end of 2026?" resolve
on the same underlying fact but YES on one corresponds to NO on the other — mark inverted=true.
Only mark match=true if the markets genuinely resolve on the same underlying event.
Respond with ONLY the JSON array, no extra text.
"""

        try:
            result_text = self._invoke_llm(prompt)
            result_text = self._clean_json(result_text)

            parsed = json.loads(result_text)
            if not isinstance(parsed, list):
                parsed = [parsed]

            matches = []
            for item in parsed:
                try:
                    if item.get("match") and item.get("confidence", 0) >= 0.6:
                        idx = int(item["index"]) - 1
                        if 0 <= idx < len(candidates):
                            candidate = candidates[idx].copy()
                            candidate["match_confidence"] = float(item["confidence"])
                            candidate["inverted"] = bool(item.get("inverted", False))
                            matches.append(candidate)
                except (KeyError, ValueError, TypeError):
                    continue

            return matches

        except json.JSONDecodeError as e:
            logger.warning(f"Cross-market match JSON parse failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Error in LLM market matching: {e}")
            return []

    def detect_cross_market_arbitrage(self, contract_id: str) -> List[Dict]:
        """
        Detect cross-market arbitrage by comparing Polymarket odds against
        Kalshi and Manifold Markets.

        Args:
            contract_id: Contract UUID as string

        Returns:
            List of arbitrage gap dicts (one per platform with price discrepancy)
        """
        if not self.settings.enable_arbitrage_detection:
            return []

        try:
            with self.db_manager.get_session() as session:
                contract = session.query(Contract).filter(
                    Contract.id == UUID(contract_id)
                ).first()

                if not contract or not contract.current_yes_odds:
                    return []

                polymarket_prob = float(contract.current_yes_odds)
                question = contract.question
                search_query = self._extract_search_query(question)

                if not search_query or len(search_query) < 3:
                    return []

                logger.debug(f"Arbitrage search for: '{search_query}' (from: {question[:60]}...)")

                # Search competitor platforms
                all_candidates = []

                kalshi_markets = self.kalshi_api.search_markets(search_query, limit=10)
                all_candidates.extend(kalshi_markets)

                manifold_markets = self.manifold_api.search_markets(search_query, limit=10)
                all_candidates.extend(manifold_markets)

                if not all_candidates:
                    return []

                # Use LLM to confirm matches
                confirmed_matches = self._match_markets_with_llm(question, all_candidates)

                if not confirmed_matches:
                    return []

                # Check for arbitrage on each confirmed match
                gaps = []
                for match in confirmed_matches:
                    raw_prob = match["probability"]
                    # If the questions are semantically inverted (e.g. "Will X resign?" vs
                    # "Will X remain in office?"), the YES probabilities are complements.
                    # Flip so we're comparing apples to apples before computing the edge.
                    inverted = match.get("inverted", False)
                    competitor_prob = (1.0 - raw_prob) if inverted else raw_prob
                    edge = abs(polymarket_prob - competitor_prob)

                    if edge < self.settings.arbitrage_min_edge:
                        continue

                    # Determine direction
                    if polymarket_prob < competitor_prob:
                        direction = "bullish"
                        explanation_hint = f"Polymarket prices YES at {polymarket_prob:.1%} while {match['platform'].title()} prices it at {competitor_prob:.1%}"
                    else:
                        direction = "bearish"
                        explanation_hint = f"Polymarket prices YES at {polymarket_prob:.1%} while {match['platform'].title()} prices it at {competitor_prob:.1%}"

                    # Confidence based on edge size and match confidence
                    edge_factor = min(edge / 0.3, 1.0) * 50
                    match_factor = match.get("match_confidence", 0.7) * 30
                    base_confidence = 20
                    confidence = int(edge_factor + match_factor + base_confidence)

                    # Generate explanation
                    explanation = self._generate_gap_explanation(
                        contract=contract,
                        gap_type="arbitrage",
                        market_odds=polymarket_prob,
                        implied_odds=competitor_prob,
                        sentiment_data={
                            "competitor_platform": match["platform"],
                            "competitor_question": match["question"],
                            "competitor_probability": competitor_prob,
                            "direction": direction,
                            "edge": round(edge, 3),
                        }
                    )

                    gaps.append({
                        "contract_id": contract_id,
                        "gap_type": "arbitrage",
                        "confidence_score": confidence,
                        "explanation": explanation,
                        "market_odds": contract.current_yes_odds,
                        "implied_odds": Decimal(str(round(competitor_prob, 4))),
                        "edge_percentage": Decimal(str(round(edge * 100, 2))),
                        "evidence": {
                            "competitor_platform": match["platform"],
                            "competitor_market_id": match["market_id"],
                            "competitor_question": match["question"],
                            "competitor_probability_raw": round(raw_prob, 4),
                            "competitor_probability_adjusted": round(competitor_prob, 4),
                            "competitor_url": match.get("url", ""),
                            "match_confidence": match.get("match_confidence", 0),
                            "inverted": inverted,
                            "direction": direction,
                            "polymarket_probability": round(polymarket_prob, 4),
                        }
                    })

                    logger.info(
                        f"Arbitrage found: {question[:50]}... | "
                        f"Polymarket={polymarket_prob:.1%} vs {match['platform']}={competitor_prob:.1%} "
                        f"(edge={edge:.1%})"
                    )

                return gaps

        except Exception as e:
            logger.error(f"Error detecting cross-market arbitrage: {e}")
            return []

    def detect_volume_spike(self, contract_id: str) -> Optional[Dict]:
        """
        Detect volume spike gaps — sudden surges in trading volume that haven't
        yet been reflected in the contract price.

        A spike indicates informed traders are positioning before public information
        catches up. The gap is strongest when volume surges but price stays flat.

        Logic:
        - Split HistoricalOdds records into a recent window and a baseline window
        - Compare volume rates (per hour) between windows
        - If recent_rate / baseline_rate >= threshold AND price lag exists, flag it
        """
        if not getattr(self.settings, 'enable_volume_spike_detection', True):
            return None

        RECENT_HOURS = getattr(self.settings, 'volume_spike_recent_hours', 2)
        BASELINE_HOURS = getattr(self.settings, 'volume_spike_baseline_hours', 12)
        MIN_SPIKE_RATIO = getattr(self.settings, 'volume_spike_min_ratio', 3.0)

        try:
            with self.db_manager.get_session() as session:
                contract = session.query(Contract).filter(
                    Contract.id == UUID(contract_id)
                ).first()

                if not contract or not contract.current_yes_odds:
                    return None

                now = datetime.now(timezone.utc)
                recent_cutoff = now - timedelta(hours=RECENT_HOURS)
                baseline_cutoff = now - timedelta(hours=BASELINE_HOURS)

                # Fetch recent and baseline HistoricalOdds records (volume stored per snapshot)
                recent_records = session.query(HistoricalOdds).filter(
                    HistoricalOdds.contract_id == UUID(contract_id),
                    HistoricalOdds.recorded_at >= recent_cutoff,
                    HistoricalOdds.volume.isnot(None),
                ).order_by(HistoricalOdds.recorded_at.asc()).all()

                baseline_records = session.query(HistoricalOdds).filter(
                    HistoricalOdds.contract_id == UUID(contract_id),
                    HistoricalOdds.recorded_at >= baseline_cutoff,
                    HistoricalOdds.recorded_at < recent_cutoff,
                    HistoricalOdds.volume.isnot(None),
                ).order_by(HistoricalOdds.recorded_at.asc()).all()

                # Need enough data points in both windows
                if len(recent_records) < 2 or len(baseline_records) < 3:
                    return None

                # Volume stored is volume_24h (a snapshot level). To get traded volume
                # *within* a window, sum the incremental increases between consecutive snapshots.
                # Increases represent new volume; decreases (counter-intuitive) are ignored.
                def incremental_volume(records: list) -> float:
                    total = 0.0
                    for i in range(1, len(records)):
                        delta = float(records[i].volume) - float(records[i - 1].volume)
                        if delta > 0:
                            total += delta
                    return total

                recent_vol = incremental_volume(recent_records)
                baseline_vol = incremental_volume(baseline_records)

                # Normalize to per-hour rates
                baseline_window_hours = BASELINE_HOURS - RECENT_HOURS
                recent_rate = recent_vol / RECENT_HOURS
                baseline_rate = baseline_vol / baseline_window_hours if baseline_window_hours > 0 else 0.0

                if baseline_rate <= 0:
                    return None

                spike_ratio = recent_rate / baseline_rate

                if spike_ratio < MIN_SPIKE_RATIO:
                    return None

                # --- Price lag check ---
                # Compare price at the start of the recent window vs now.
                # A large spike with minimal price movement is the core gap signal.
                odds_at_spike_start = float(recent_records[0].yes_odds)
                odds_now = float(contract.current_yes_odds)
                price_move = abs(odds_now - odds_at_spike_start)

                # --- Confidence scoring (max 100) ---
                # Spike magnitude: 3x→10x+ mapped to 0–40 pts
                spike_factor = min((spike_ratio - MIN_SPIKE_RATIO) / (10.0 - MIN_SPIKE_RATIO), 1.0) * 40

                # Price lag: 0–30 pts. Full score if price moved < 1% despite spike.
                # Score decays as the market starts to catch up (up to 10% move).
                price_lag_factor = max(0.0, 1.0 - (price_move / 0.10)) * 30

                # Absolute volume size: bigger markets get higher weight — 0–15 pts
                abs_vol_factor = min(recent_vol / 50_000, 1.0) * 15

                # Recency: how close to "now" the spike peak is — 0–15 pts
                # Use the most recent record timestamp
                most_recent_ts = recent_records[-1].recorded_at
                if most_recent_ts.tzinfo is None:
                    most_recent_ts = most_recent_ts.replace(tzinfo=timezone.utc)
                minutes_since_last_record = (now - most_recent_ts).total_seconds() / 60
                recency_factor = max(0.0, 1.0 - (minutes_since_last_record / (RECENT_HOURS * 60))) * 15

                confidence = int(spike_factor + price_lag_factor + abs_vol_factor + recency_factor)
                confidence = max(0, min(confidence, 100))

                # Direction: if price hasn't moved, direction is ambiguous — flag as "unknown"
                # until cross-referenced with sentiment. If price started moving, use that direction.
                if price_move < 0.02:
                    direction = "unknown"
                elif odds_now > odds_at_spike_start:
                    direction = "bullish"
                else:
                    direction = "bearish"

                explanation = self._generate_gap_explanation(
                    contract=contract,
                    gap_type="volume_spike",
                    market_odds=odds_now,
                    implied_odds=None,
                    sentiment_data={
                        'spike_ratio': round(spike_ratio, 2),
                        'recent_volume': round(recent_vol, 2),
                        'baseline_volume_rate_per_hour': round(baseline_rate, 2),
                        'recent_volume_rate_per_hour': round(recent_rate, 2),
                        'price_move': round(price_move, 4),
                        'direction': direction,
                    }
                )

                return {
                    'contract_id': contract_id,
                    'gap_type': 'volume_spike',
                    'confidence_score': confidence,
                    'explanation': explanation,
                    'market_odds': contract.current_yes_odds,
                    'implied_odds': None,
                    'edge_percentage': Decimal('0'),
                    'evidence': {
                        'spike_ratio': round(spike_ratio, 2),
                        'recent_volume': round(recent_vol, 2),
                        'baseline_volume_rate_per_hour': round(baseline_rate, 2),
                        'recent_volume_rate_per_hour': round(recent_rate, 2),
                        'recent_snapshots': len(recent_records),
                        'baseline_snapshots': len(baseline_records),
                        'price_at_spike_start': round(odds_at_spike_start, 4),
                        'price_now': round(odds_now, 4),
                        'price_move': round(price_move, 4),
                        'direction': direction,
                    }
                }

        except Exception as e:
            logger.error(f"Error detecting volume spike: {e}")
            return None

    def detect_all_gaps(self, contract_id: str) -> List[Dict]:
        """
        Run all gap detection methods for a contract.

        Args:
            contract_id: Contract UUID as string

        Returns:
            List of detected gaps
        """
        gaps = []

        # Sentiment mismatch
        gap = self.detect_sentiment_mismatch(contract_id)
        if gap:
            gaps.append(gap)

        # Information asymmetry
        gap = self.detect_information_asymmetry(contract_id)
        if gap:
            gaps.append(gap)

        # Pattern deviation
        gap = self.detect_pattern_deviation(contract_id)
        if gap:
            gaps.append(gap)

        # Cross-market arbitrage
        arbitrage_gaps = self.detect_cross_market_arbitrage(contract_id)
        gaps.extend(arbitrage_gaps)

        # Volume spike
        gap = self.detect_volume_spike(contract_id)
        if gap:
            gaps.append(gap)

        return gaps

    def analyze_all_contracts(self) -> List[Dict]:
        """
        Analyze all active contracts for gaps.

        Returns:
            List of all detected gaps
        """
        logger.info("Starting gap detection for all contracts...")

        all_gaps = []

        try:
            with self.db_manager.get_session() as session:
                from sqlalchemy import exists, or_
                now = datetime.now(timezone.utc)
                # Include contracts that have sentiment data OR recent volume history —
                # volume spikes can appear before social discussion catches up.
                has_sentiment = exists().where(
                    SentimentAnalysis.contract_id == Contract.id
                )
                has_volume_history = exists().where(
                    HistoricalOdds.contract_id == Contract.id,
                    HistoricalOdds.volume.isnot(None),
                )
                contracts = session.query(Contract).filter(
                    Contract.active == True,
                    (Contract.end_date > now) | (Contract.end_date == None),
                    or_(has_sentiment, has_volume_history)
                ).all()

                dedupe_hours = getattr(self.settings, 'gap_dedupe_hours', 24)
                dedupe_since = now - timedelta(hours=dedupe_hours)

                for contract in contracts:
                    logger.info(f"Analyzing gaps for: {contract.question[:50]}...")

                    gaps = self.detect_all_gaps(str(contract.id))

                    # Store gaps in database (skip if same contract+type already detected recently)
                    for gap in gaps:
                        if gap['confidence_score'] < self.settings.min_confidence_score:
                            continue
                        # Avoid duplicate rows: skip if we already have this gap type for this contract recently
                        existing = session.query(DetectedGap).filter(
                            DetectedGap.contract_id == UUID(gap['contract_id']),
                            DetectedGap.gap_type == gap['gap_type'],
                            DetectedGap.detected_at >= dedupe_since,
                        ).first()
                        if existing:
                            logger.debug(
                                "Skipping duplicate gap: contract=%s type=%s (already detected at %s)",
                                gap['contract_id'], gap['gap_type'], existing.detected_at
                            )
                            continue

                        detected_gap = DetectedGap(
                            contract_id=UUID(gap['contract_id']),
                            gap_type=gap['gap_type'],
                            confidence_score=gap['confidence_score'],
                            explanation=gap['explanation'],
                            evidence=gap['evidence'],
                            market_odds=gap['market_odds'],
                            implied_odds=gap.get('implied_odds'),
                            edge_percentage=gap['edge_percentage'],
                            social_sources_count=gap.get('social_sources_count', 0),
                            contract_features=gap.get('contract_features'),
                        )
                        session.add(detected_gap)
                        all_gaps.append(gap)

                session.commit()

        except Exception as e:
            logger.error(f"Error analyzing contracts: {e}")

        logger.info(f"Gap detection complete: {len(all_gaps)} gaps found")
        return all_gaps

    def create_detection_task(self) -> Task:
        """
        Create CrewAI task for gap detection.

        Returns:
            CrewAI Task instance
        """
        return Task(
            description="""Detect pricing gaps and inefficiencies in prediction markets:
            1. Identify sentiment-probability mismatches
            2. Detect information asymmetry (recent news not reflected)
            3. Find historical pattern deviations
            4. Calculate confidence scores (0-100) for each gap
            5. Generate clear explanations with supporting evidence
            6. Store detected gaps in database
            """,
            agent=self.create_crewai_agent(),
            expected_output="List of detected pricing gaps with confidence scores and explanations"
        )

    def run(self) -> List[Dict]:
        """
        Execute gap detection workflow.

        Returns:
            List of detected gaps
        """
        logger.info("=== Starting Gap Detection Agent ===")

        gaps = self.analyze_all_contracts()

        logger.info(f"Gap detection complete: {len(gaps)} opportunities identified")

        return gaps
