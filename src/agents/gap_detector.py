"""Gap Detection Agent - Identifies pricing inefficiencies in prediction markets."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

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

                if not contract:
                    return None

                # Get very recent sentiment (last 3 hours) vs older (3-6 hours ago)
                now = datetime.now(timezone.utc)
                recent_cutoff = now - timedelta(hours=3)
                older_cutoff = now - timedelta(hours=6)

                # Recent sentiment
                recent_sentiment = session.query(SentimentAnalysis).join(
                    SentimentAnalysis.post
                ).filter(
                    SentimentAnalysis.contract_id == UUID(contract_id),
                    SentimentAnalysis.analyzed_at >= recent_cutoff
                ).all()

                # Older sentiment
                older_sentiment = session.query(SentimentAnalysis).join(
                    SentimentAnalysis.post
                ).filter(
                    SentimentAnalysis.contract_id == UUID(contract_id),
                    SentimentAnalysis.analyzed_at >= older_cutoff,
                    SentimentAnalysis.analyzed_at < recent_cutoff
                ).all()

                if len(recent_sentiment) < 3 or len(older_sentiment) < 3:
                    return None

                # Calculate sentiment shifts
                recent_avg = sum(float(s.sentiment_score) for s in recent_sentiment) / len(recent_sentiment)
                older_avg = sum(float(s.sentiment_score) for s in older_sentiment) / len(older_sentiment)

                sentiment_shift = recent_avg - older_avg

                # Check if there's a significant shift
                if abs(sentiment_shift) < 0.10:  # Threshold for significant shift (was 0.2)
                    return None

                # Check if odds have moved accordingly
                historical_odds = session.query(HistoricalOdds).filter(
                    HistoricalOdds.contract_id == UUID(contract_id)
                ).order_by(HistoricalOdds.recorded_at.desc()).limit(10).all()

                if len(historical_odds) < 2:
                    return None

                recent_odds = float(historical_odds[0].yes_odds)
                older_odds = float(historical_odds[-1].yes_odds)
                odds_movement = recent_odds - older_odds

                # Information asymmetry: sentiment shifted but odds haven't
                # Positive sentiment shift → odds should increase
                # Negative sentiment shift → odds should decrease
                expected_direction = 1 if sentiment_shift > 0 else -1
                actual_direction = 1 if odds_movement > 0 else -1 if odds_movement < 0 else 0

                if expected_direction == actual_direction and abs(odds_movement) > 0.05:
                    # Odds already moved - no asymmetry
                    return None

                # Calculate confidence
                shift_magnitude = abs(sentiment_shift)
                confidence = int(min(shift_magnitude / 0.5, 1.0) * 60 + 20)

                # Generate explanation
                explanation = self._generate_gap_explanation(
                    contract=contract,
                    gap_type="info_asymmetry",
                    market_odds=recent_odds,
                    implied_odds=None,
                    sentiment_data={
                        'recent_avg': recent_avg,
                        'older_avg': older_avg,
                        'shift': sentiment_shift,
                        'recent_posts': len(recent_sentiment)
                    }
                )

                return {
                    'contract_id': contract_id,
                    'gap_type': 'info_asymmetry',
                    'confidence_score': confidence,
                    'explanation': explanation,
                    'market_odds': contract.current_yes_odds,
                    'implied_odds': None,
                    'edge_percentage': Decimal(str(round(abs(sentiment_shift) * 50, 2))),
                    'evidence': {
                        'sentiment_shift': round(sentiment_shift, 3),
                        'recent_avg_sentiment': round(recent_avg, 3),
                        'older_avg_sentiment': round(older_avg, 3),
                        'recent_posts': len(recent_sentiment),
                        'odds_movement': round(odds_movement, 4)
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
- "match": true or false
- "confidence": 0.0 to 1.0 (how confident this is the same event)

Only mark as match=true if the markets are about essentially the same question/outcome.
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
                    competitor_prob = match["probability"]
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
                            "competitor_probability": round(competitor_prob, 4),
                            "competitor_url": match.get("url", ""),
                            "match_confidence": match.get("match_confidence", 0),
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
                # Only analyze contracts that have sentiment data (from social posts)
                from sqlalchemy import exists
                now = datetime.now(timezone.utc)
                has_sentiment = exists().where(
                    SentimentAnalysis.contract_id == Contract.id
                )
                contracts = session.query(Contract).filter(
                    Contract.active == True,
                    (Contract.end_date > now) | (Contract.end_date == None),
                    has_sentiment
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
