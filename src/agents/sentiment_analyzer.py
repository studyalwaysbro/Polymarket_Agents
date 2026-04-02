"""Sentiment Analysis Agent - Analyzes social media sentiment using LLM."""

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

sys.path.insert(0, str(Path.home() / ".api-monitor"))

from crewai import Agent, Task

from ..config import get_settings, get_llm
from ..database import get_db_manager
from ..database.models import SocialPost, SentimentAnalysis, Contract
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Minimum posts required to bother analyzing a contract
MIN_POSTS_FOR_ANALYSIS = 3

# Number of posts to send per LLM call (keep small for 7b models)
BATCH_SIZE = 5


class SentimentAnalysisAgent:
    """
    Agent responsible for analyzing sentiment of social media posts.

    Responsibilities:
    - Perform sentiment analysis on collected social posts
    - Generate sentiment scores (-1 to +1) and labels
    - Extract key topics and themes
    - Aggregate sentiment per contract
    - Store analysis results in database
    """

    def __init__(self):
        """Initialize Sentiment Analysis Agent."""
        self.settings = get_settings()
        self.db_manager = get_db_manager()

        # Initialize LLM (OpenAI or Ollama based on config)
        self.llm = get_llm()

        # Initialize ensemble sentiment (VADER + TextBlob)
        self.ensemble = None
        if self.settings.enable_ensemble_sentiment:
            try:
                from ..sentiment import EnsembleSentiment
                self.ensemble = EnsembleSentiment()
                logger.info("Ensemble sentiment (VADER + TextBlob) initialized")
            except Exception as e:
                logger.warning(f"Ensemble sentiment unavailable: {e}")

        logger.info(f"Sentiment Analysis Agent initialized with {self.settings.llm_provider}")

    def create_crewai_agent(self) -> Agent:
        """
        Create CrewAI agent definition.

        Returns:
            CrewAI Agent instance
        """
        return Agent(
            role='Sentiment Analysis Specialist',
            goal='Accurately analyze sentiment and extract insights from social media content',
            backstory="""You are an expert in natural language processing and sentiment analysis.
            You excel at understanding nuanced opinions in social media posts, identifying
            bullish and bearish signals, and aggregating sentiment across multiple sources.""",
            verbose=True,
            allow_delegation=False,
            llm=self.llm
        )

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

        # Remove markdown code fences
        if text.startswith('```'):
            text = text.split('```')[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.strip()

        # Remove any text before the first [ or {
        first_bracket = min(
            (text.find('[') if text.find('[') >= 0 else len(text)),
            (text.find('{') if text.find('{') >= 0 else len(text))
        )
        if first_bracket < len(text):
            text = text[first_bracket:]

        # Remove trailing text after last ] or }
        last_close = max(text.rfind(']'), text.rfind('}'))
        if last_close >= 0:
            text = text[:last_close + 1]

        # Fix trailing commas before ] or }
        text = re.sub(r',\s*([}\]])', r'\1', text)

        # Fix missing commas between } and { (e.g. "}{" or "}\n{")
        text = re.sub(r'\}\s*\{', '},{', text)

        # Fix missing commas between } and " (e.g. } "key":)
        text = re.sub(r'\}\s*"', '}, "', text)

        # Fix missing commas between a value and the next key (e.g. "value" "key":)
        text = re.sub(r'"\s*\n\s*"', '", "', text)

        # Fix single quotes used instead of double quotes
        # Only do this if there are no double quotes (avoid breaking strings)
        if '"' not in text:
            text = text.replace("'", '"')

        # Remove any control characters that can break JSON
        text = re.sub(r'[\x00-\x1f\x7f]', ' ', text)
        # But restore newlines and tabs inside the structure (harmless in JSON)
        text = text.replace('\\n', ' ').replace('\\t', ' ')

        return text

    def _analyze_batch(self, contents: List[str]) -> List[Optional[Dict]]:
        """
        Analyze sentiment for a batch of posts in a single LLM call.

        Args:
            contents: List of post content strings (max ~10)

        Returns:
            List of sentiment dicts (same length as contents, None for failures)
        """
        numbered = "\n".join(
            f'Post {i+1}: "{c[:500]}"' for i, c in enumerate(contents)
        )

        prompt = f"""Analyze the sentiment of these {len(contents)} social media posts in the context of prediction markets.

{numbered}

For EACH post, determine:
- sentiment_score: number from -1.0 (very bearish) to +1.0 (very bullish)
- sentiment_label: "positive", "negative", or "neutral"
- confidence: your confidence 0.0 to 1.0
- topics: 2-3 key topics mentioned

Respond with ONLY a valid JSON array of {len(contents)} objects (one per post, in order). No extra text.
Example: [{{"sentiment_score": 0.5, "sentiment_label": "positive", "confidence": 0.8, "topics": ["topic1"]}}]
"""

        try:
            result_text = self._invoke_llm(prompt)
            result_text = self._clean_json(result_text)

            parsed = json.loads(result_text)

            if not isinstance(parsed, list):
                parsed = [parsed]

            results = []
            for item in parsed[:len(contents)]:
                try:
                    results.append({
                        'score': Decimal(str(max(-1.0, min(1.0, float(item['sentiment_score']))))),
                        'label': item['sentiment_label'].lower(),
                        'confidence': Decimal(str(max(0.0, min(1.0, float(item['confidence']))))),
                        'topics': item.get('topics', [])[:5]
                    })
                except (KeyError, ValueError, TypeError):
                    results.append(None)

            # Pad with None if LLM returned fewer items than expected
            while len(results) < len(contents):
                results.append(None)

            return results

        except json.JSONDecodeError as e:
            # Last resort: try to extract individual JSON objects manually
            try:
                import re
                objects = re.findall(r'\{[^{}]+\}', result_text)
                if len(objects) >= len(contents):
                    parsed = [json.loads(obj) for obj in objects[:len(contents)]]
                    results = []
                    for item in parsed:
                        try:
                            results.append({
                                'score': Decimal(str(max(-1.0, min(1.0, float(item['sentiment_score']))))),
                                'label': item['sentiment_label'].lower(),
                                'confidence': Decimal(str(max(0.0, min(1.0, float(item['confidence']))))),
                                'topics': item.get('topics', [])[:5]
                            })
                        except (KeyError, ValueError, TypeError):
                            results.append(None)
                    while len(results) < len(contents):
                        results.append(None)
                    logger.debug(f"Batch JSON repaired by extracting individual objects")
                    return results
            except Exception:
                pass

            logger.warning(f"Batch JSON parse failed ({e}), falling back to single-post analysis")
            return [self._analyze_single_post(c) for c in contents]
        except Exception as e:
            logger.error(f"Batch analysis error: {e}")
            return [None] * len(contents)

    def _analyze_single_post(self, content: str) -> Optional[Dict]:
        """
        Analyze sentiment of a single post using LLM (fallback for failed batches).

        Args:
            content: Post content text

        Returns:
            Sentiment analysis result dictionary
        """
        try:
            prompt = f"""Analyze the sentiment of this social media post in the context of prediction markets.

Post: "{content[:500]}"

Provide a JSON response with:
1. sentiment_score: A number from -1.0 (very negative/bearish) to +1.0 (very positive/bullish)
2. sentiment_label: One of "positive", "negative", or "neutral"
3. confidence: Your confidence in this analysis (0.0 to 1.0)
4. topics: A list of 2-3 key topics or themes mentioned

Respond with ONLY valid JSON, no additional text.
"""

            result_text = self._invoke_llm(prompt)
            result_text = self._clean_json(result_text)

            sentiment = json.loads(result_text)

            return {
                'score': Decimal(str(max(-1.0, min(1.0, float(sentiment['sentiment_score']))))),
                'label': sentiment['sentiment_label'].lower(),
                'confidence': Decimal(str(max(0.0, min(1.0, float(sentiment['confidence']))))),
                'topics': sentiment.get('topics', [])[:5]
            }

        except json.JSONDecodeError as e:
            logger.error(f"Error parsing LLM JSON response: {e}")
            return None
        except Exception as e:
            logger.error(f"Error in sentiment analysis: {e}")
            return None

    def analyze_contract_sentiment(self, contract_id: str) -> Dict:
        """
        Analyze and aggregate sentiment for a specific contract.

        Args:
            contract_id: Contract UUID as string

        Returns:
            Aggregated sentiment dictionary
        """
        logger.info(f"Analyzing sentiment for contract {contract_id}")

        try:
            with self.db_manager.get_session() as session:
                # Get contract
                contract = session.query(Contract).filter(
                    Contract.id == UUID(contract_id)
                ).first()

                if not contract:
                    logger.warning(f"Contract not found: {contract_id}")
                    return {}

                # Get recent social posts
                from sqlalchemy import any_
                contract_uuid = UUID(contract_id)
                posts = session.query(SocialPost).filter(
                    contract_uuid == any_(SocialPost.related_contracts)
                ).order_by(SocialPost.posted_at.desc()).limit(200).all()

                if not posts:
                    return {
                        'contract_id': contract_id,
                        'total_posts': 0,
                        'avg_sentiment': 0.0,
                        'sentiment_distribution': {'positive': 0, 'negative': 0, 'neutral': 0}
                    }

                # Filter to only posts that haven't been analyzed yet
                analyzed_ids = set(
                    row[0] for row in session.query(SentimentAnalysis.post_id).filter(
                        SentimentAnalysis.contract_id == contract_uuid
                    ).all()
                )
                posts_to_analyze = [p for p in posts if p.id not in analyzed_ids]

                # Skip if too few total posts for meaningful analysis
                if len(posts) < MIN_POSTS_FOR_ANALYSIS and not analyzed_ids:
                    logger.info(f"Skipping contract {contract_id}: only {len(posts)} posts (need {MIN_POSTS_FOR_ANALYSIS})")
                    return {}

                # Batch-analyze new posts
                if posts_to_analyze:
                    logger.info(f"Analyzing {len(posts_to_analyze)} new posts in batches of {BATCH_SIZE}...")

                    for i in range(0, len(posts_to_analyze), BATCH_SIZE):
                        batch_posts = posts_to_analyze[i:i + BATCH_SIZE]
                        batch_contents = [p.content for p in batch_posts]

                        sentiments = self._analyze_batch(batch_contents)

                        for post, sentiment in zip(batch_posts, sentiments):
                            if sentiment:
                                # Compute ensemble scores if available
                                vader_score = None
                                textblob_score = None
                                ensemble_score = None

                                if self.ensemble:
                                    try:
                                        lexicon = self.ensemble.score(post.content)
                                        vader_score = Decimal(str(lexicon['vader_score'])) \
                                            if lexicon['vader_score'] is not None else None
                                        textblob_score = Decimal(str(lexicon['textblob_score'])) \
                                            if lexicon['textblob_score'] is not None else None

                                        ens = self.ensemble.ensemble_score(
                                            llm_score=float(sentiment['score']),
                                            vader_score=lexicon['vader_score'],
                                            textblob_score=lexicon['textblob_score'],
                                            llm_weight=0.5
                                        )
                                        ensemble_score = Decimal(str(ens))
                                    except Exception as e:
                                        logger.debug(f"Ensemble scoring failed: {e}")

                                analysis = SentimentAnalysis(
                                    post_id=post.id,
                                    contract_id=contract_uuid,
                                    sentiment_score=sentiment['score'],
                                    sentiment_label=sentiment['label'],
                                    confidence=sentiment['confidence'],
                                    topics=sentiment.get('topics', []),
                                    vader_score=vader_score,
                                    textblob_score=textblob_score,
                                    ensemble_score=ensemble_score,
                                )
                                session.add(analysis)

                    session.commit()

                # Aggregate all sentiment (existing + new)
                analyses = session.query(SentimentAnalysis).filter(
                    SentimentAnalysis.contract_id == contract_uuid
                ).all()

                if not analyses:
                    return {
                        'contract_id': contract_id,
                        'total_posts': len(posts),
                        'avg_sentiment': 0.0,
                        'sentiment_distribution': {'positive': 0, 'negative': 0, 'neutral': 0}
                    }

                # Calculate aggregates
                total = len(analyses)
                avg_sentiment = sum(float(a.sentiment_score) for a in analyses) / total
                positive = sum(1 for a in analyses if a.sentiment_label == 'positive')
                negative = sum(1 for a in analyses if a.sentiment_label == 'negative')
                neutral = sum(1 for a in analyses if a.sentiment_label == 'neutral')

                # Extract common topics
                all_topics = []
                for a in analyses:
                    all_topics.extend(a.topics or [])
                topic_counts = {}
                for topic in all_topics:
                    topic_counts[topic] = topic_counts.get(topic, 0) + 1
                top_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)[:5]

                # Compute rolling sentiment snapshots
                if self.ensemble:
                    try:
                        for window in [6, 12, 24]:
                            self.ensemble.compute_rolling_sentiment(contract_id, window)
                    except Exception as e:
                        logger.debug(f"Rolling sentiment computation failed: {e}")

                return {
                    'contract_id': contract_id,
                    'question': contract.question,
                    'total_posts': len(posts),
                    'analyzed_posts': total,
                    'avg_sentiment': round(avg_sentiment, 3),
                    'sentiment_distribution': {
                        'positive': positive,
                        'negative': negative,
                        'neutral': neutral
                    },
                    'positive_ratio': round(positive / total, 3) if total > 0 else 0,
                    'top_topics': [{'topic': t[0], 'count': t[1]} for t in top_topics]
                }

        except Exception as e:
            logger.error(f"Error analyzing contract sentiment: {e}")
            return {}

    def analyze_all_active_contracts(self) -> List[Dict]:
        """
        Analyze sentiment for all active, non-expired contracts.

        Returns:
            List of sentiment analysis results per contract
        """
        logger.info("Analyzing sentiment for all active contracts...")

        results = []

        try:
            with self.db_manager.get_session() as session:
                # Only analyze contracts that have at least one social post (otherwise no sentiment to analyze)
                from sqlalchemy import any_, exists
                from ..database.models import SocialPost
                has_post = exists().where(
                    Contract.id == any_(SocialPost.related_contracts)
                )
                now = datetime.now(timezone.utc)
                contracts = session.query(Contract).filter(
                    Contract.active == True,
                    (Contract.end_date > now) | (Contract.end_date == None)
                ).order_by(
                    Contract.end_date.asc().nulls_last()
                ).limit(self.settings.max_contracts_per_cycle).all()

                for contract in contracts:
                    sentiment = self.analyze_contract_sentiment(str(contract.id))
                    if sentiment:
                        results.append(sentiment)

        except Exception as e:
            logger.error(f"Error analyzing all contracts: {e}")

        logger.info(f"Sentiment analysis complete for {len(results)} contracts")
        return results

    def create_analysis_task(self) -> Task:
        """
        Create CrewAI task for sentiment analysis.

        Returns:
            CrewAI Task instance
        """
        return Task(
            description="""Perform comprehensive sentiment analysis on social media data:
            1. Analyze sentiment of collected social media posts
            2. Assign sentiment scores from -1 (bearish) to +1 (bullish)
            3. Classify sentiment as positive, negative, or neutral
            4. Extract key topics and themes
            5. Aggregate sentiment per contract
            6. Store analysis results in database
            """,
            agent=self.create_crewai_agent(),
            expected_output="Dictionary containing sentiment analysis results for all contracts"
        )

    def run(self) -> List[Dict]:
        """
        Execute sentiment analysis workflow.

        Returns:
            List of sentiment analysis results
        """
        logger.info("=== Starting Sentiment Analysis Agent ===")

        results = self.analyze_all_active_contracts()

        logger.info(f"Sentiment analysis complete: {len(results)} contracts analyzed")

        return results
