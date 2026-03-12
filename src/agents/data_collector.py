"""Data Collection Agent - Fetches market and social media data."""

import hashlib
from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID

from crewai import Agent, Task

from ..config import get_settings
from ..database import get_db_manager
from ..database.models import Contract, SocialPost, HistoricalOdds
from ..services import PolymarketAPI, TwitterScraper, RedditScraper
from ..utils.logger import get_logger

logger = get_logger(__name__)


class DataCollectionAgent:
    """
    Agent responsible for collecting data from Polymarket and social media.

    Responsibilities:
    - Fetch active Polymarket contracts
    - Collect Twitter/X posts related to contracts
    - Collect Reddit posts related to contracts
    - Store all data in PostgreSQL database
    - Track historical odds changes
    """

    def __init__(self):
        """Initialize Data Collection Agent."""
        self.settings = get_settings()
        self.db_manager = get_db_manager()

        # Initialize service integrations
        self.polymarket = PolymarketAPI()
        self.twitter = TwitterScraper()
        self.reddit = RedditScraper()
        self.rss_news = None

        # Initialize RSS news scraper (always available, no API keys needed)
        try:
            from ..services import RSSNewsScraper
            self.rss_news = RSSNewsScraper()
            logger.info("RSS News scraper initialized (FREE news source)")
        except Exception as e:
            logger.warning(f"Could not initialize RSS news scraper: {e}")

        # Initialize Bluesky scraper (always available, no API keys needed)
        self.bluesky = None
        try:
            from ..services import BlueskyScraper
            self.bluesky = BlueskyScraper()
            logger.info("Bluesky scraper initialized (FREE social media source)")
        except Exception as e:
            logger.warning(f"Could not initialize Bluesky scraper: {e}")

        # Initialize Tavily web search (requires API key)
        self.tavily = None
        try:
            from ..services import TavilySearch
            self.tavily = TavilySearch()
            if self.tavily.enabled:
                logger.info("Tavily Search initialized")
        except Exception as e:
            logger.warning(f"Could not initialize Tavily: {e}")

        # Initialize Grok X sentiment (requires API key)
        self.grok = None
        try:
            from ..services import GrokSentiment
            self.grok = GrokSentiment()
            if self.grok.enabled:
                logger.info("Grok X sentiment initialized")
        except Exception as e:
            logger.warning(f"Could not initialize Grok: {e}")

        # Initialize X mirror scraper (free fallback when Grok unavailable)
        self.x_mirror = None
        try:
            from ..services import XMirrorScraper
            self.x_mirror = XMirrorScraper()
            if self.x_mirror.enabled:
                logger.info("X Mirror scraper initialized (Grok fallback)")
        except Exception as e:
            logger.warning(f"Could not initialize X mirror scraper: {e}")

        # Initialize GDELT (free, no key required)
        self.gdelt = None
        try:
            from ..services import GDELTAPI
            self.gdelt = GDELTAPI()
            if self.gdelt.enabled:
                logger.info("GDELT API initialized (FREE geopolitical news)")
        except Exception as e:
            logger.warning(f"Could not initialize GDELT: {e}")

        # Initialize Manifold API for comments (reuse existing if available in gap_detector)
        self.manifold = None
        try:
            from ..services import ManifoldAPI
            self.manifold = ManifoldAPI()
            if self.manifold.enabled:
                logger.info("Manifold API initialized for comment collection")
        except Exception as e:
            logger.warning(f"Could not initialize Manifold API: {e}")

        # Initialize contract feature engine
        self.feature_engine = None
        try:
            from ..features import ContractFeatureEngine
            self.feature_engine = ContractFeatureEngine()
        except Exception as e:
            logger.warning(f"Could not initialize feature engine: {e}")

        logger.info("Data Collection Agent initialized")

    def create_crewai_agent(self) -> Agent:
        """
        Create CrewAI agent definition.

        Returns:
            CrewAI Agent instance
        """
        return Agent(
            role='Data Collection Specialist',
            goal='Gather comprehensive market and social media data for analysis',
            backstory="""You are an expert data collector specializing in prediction markets
            and social media intelligence. You know how to efficiently gather relevant data
            from multiple sources while respecting rate limits and API terms of service.""",
            verbose=True,
            allow_delegation=False
        )

    def _filter_and_rank_contracts(self, parsed_markets: List[Dict]) -> List[Dict]:
        """
        Filter out garbage contracts, keep everything relevant, sort best-first.

        Garbage = contracts that are truly not worth social-searching:
        - No volume AND no liquidity (dead markets nobody trades)
        - Odds at 97%+ or 3%- (basically resolved, no mispricing possible)
        - No odds data at all (can't analyze what we can't measure)

        Everything else stays. Sorted by composite score so the most
        interesting contracts get processed first (high volume, volatile,
        uncertain, near-expiry).

        Args:
            parsed_markets: All parsed contract dicts (with raw_data attached)

        Returns:
            Filtered + scored list, best contracts first
        """
        import math
        now = datetime.now(timezone.utc)

        kept = []
        garbage_count = 0

        for c in parsed_markets:
            raw = c.get('raw_data', {})
            vol_24h = float(c.get('volume_24h') or 0)
            liquidity = float(c.get('liquidity') or 0)
            yes_odds = float(c.get('current_yes_odds') or 0)

            # === GARBAGE FILTER — toss truly worthless contracts ===

            # Dead market: no volume AND no liquidity
            if vol_24h == 0 and liquidity == 0:
                garbage_count += 1
                continue

            # No odds data — can't do gap detection without a price
            if yes_odds == 0:
                garbage_count += 1
                continue

            # Basically resolved: odds at extreme ends (97%+ or 3%-)
            # No room for mispricing, social sentiment won't move these
            if yes_odds >= 0.97 or yes_odds <= 0.03:
                garbage_count += 1
                continue

            # === SCORING — for sort order (best first) ===
            spread = float(raw.get('spread') or 0)
            day_change = abs(float(raw.get('oneDayPriceChange') or 0))
            hour_change = abs(float(raw.get('oneHourPriceChange') or 0))

            end_date = c.get('end_date')
            hours_to_expiry = None
            if end_date:
                delta = end_date - now
                hours_to_expiry = max(delta.total_seconds() / 3600, 0)

            # Volume (log scale)
            vol_score = min(100, math.log10(max(vol_24h, 1)) * 20) if vol_24h > 0 else 0

            # Uncertainty (50/50 = max opportunity)
            uncertainty_score = (1 - abs(yes_odds - 0.5) * 2) * 100

            # Volatility (recent price movement = breaking news / repricing)
            volatility_score = min(100, (day_change * 500) + (hour_change * 2000))

            # Time pressure (expiring within 7 days = actionable)
            time_score = 0
            if hours_to_expiry is not None and hours_to_expiry < 168:
                time_score = max(0, 100 - (hours_to_expiry / 168 * 100))

            # Liquidity quality
            liq_score = min(100, math.log10(max(liquidity, 1)) * 15) if liquidity > 0 else 0

            # Composite — determines processing order
            composite = (
                vol_score * 0.30 +
                volatility_score * 0.25 +
                uncertainty_score * 0.20 +
                time_score * 0.10 +
                liq_score * 0.10 +
                max(0, 5 - min(50, spread * 1000)) * 0.05
            )

            c['_score'] = composite
            kept.append(c)

        # Sort best-first so most interesting contracts get social-searched first
        kept.sort(key=lambda x: x['_score'], reverse=True)

        logger.info(f"Contract filter: kept {len(kept)}, trashed {garbage_count} "
                     f"(dead/no-odds/resolved)")
        if kept:
            top3 = [(s['question'][:50], f"score={s['_score']:.0f}") for s in kept[:3]]
            bot3 = [(s['question'][:50], f"score={s['_score']:.0f}") for s in kept[-3:]]
            logger.info(f"  Top: {top3}")
            logger.info(f"  Bottom: {bot3}")

        return kept

    def collect_market_data(self) -> List[Dict]:
        """
        Collect active Polymarket contracts with smart selection.

        Fetches all active markets, stores them in DB, then selects a diverse
        representative sample for social media analysis.

        Returns:
            List of contract dictionaries with metadata
        """
        logger.info("Starting market data collection...")

        try:
            # Fetch a large pool — we want the full universe to select from
            fetch_limit = max(500, self.settings.max_contracts_per_cycle * 5)
            markets = self.polymarket.get_active_markets(
                limit=fetch_limit
            )

            all_parsed = []
            contracts_data = []

            with self.db_manager.get_session() as session:
                for market in markets:
                    # Parse market to standardized format
                    contract_data = self.polymarket.parse_market_to_contract(market)
                    if not contract_data.get('contract_id'):
                        continue

                    # Skip expired contracts
                    if contract_data.get('end_date'):
                        if contract_data['end_date'] < datetime.now(timezone.utc):
                            continue

                    # Store ALL contracts in DB (full universe for historical tracking)
                    existing = session.query(Contract).filter(
                        Contract.contract_id == contract_data['contract_id']
                    ).first()

                    if existing:
                        for key, value in contract_data.items():
                            if key not in ['raw_data', 'created_at']:
                                setattr(existing, key, value)

                        if (contract_data.get('current_yes_odds') and
                            contract_data['current_yes_odds'] != existing.current_yes_odds):
                            historical = HistoricalOdds(
                                contract_id=existing.id,
                                yes_odds=contract_data['current_yes_odds'],
                                no_odds=contract_data['current_no_odds'],
                                volume=contract_data.get('volume_24h')
                            )
                            session.add(historical)
                        contract_obj = existing
                    else:
                        db_data = {k: v for k, v in contract_data.items() if k != 'raw_data'}
                        contract_obj = Contract(**db_data)
                        session.add(contract_obj)
                        session.flush()

                        if contract_data.get('current_yes_odds'):
                            historical = HistoricalOdds(
                                contract_id=contract_obj.id,
                                yes_odds=contract_data['current_yes_odds'],
                                no_odds=contract_data['current_no_odds'],
                                volume=contract_data.get('volume_24h')
                            )
                            session.add(historical)

                    all_parsed.append({
                        'id': str(contract_obj.id),
                        'contract_id': contract_obj.contract_id,
                        'question': contract_obj.question,
                        'category': contract_obj.category,
                        'current_yes_odds': contract_data.get('current_yes_odds'),
                        'volume_24h': contract_data.get('volume_24h'),
                        'liquidity': contract_data.get('liquidity'),
                        'end_date': contract_data.get('end_date'),
                        'raw_data': contract_data.get('raw_data', {}),
                    })

                session.commit()

            logger.info(f"Stored {len(all_parsed)} contracts in database")

            # Filter out garbage, keep everything relevant, sorted best-first
            filtered = self._filter_and_rank_contracts(all_parsed)

            # Strip internal scoring fields for downstream use
            for c in filtered:
                contracts_data.append({
                    'id': c['id'],
                    'contract_id': c['contract_id'],
                    'question': c['question'],
                    'category': c['category'],
                })

            logger.info(f"Passing {len(contracts_data)}/{len(all_parsed)} contracts for social analysis "
                         f"(garbage removed, best-first ordering)")
            return contracts_data

        except Exception as e:
            logger.error(f"Error collecting market data: {e}")
            return []

    def collect_social_media_data(self, contracts: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Collect social media posts related to contracts.

        Args:
            contracts: List of contract dictionaries

        Returns:
            Dictionary mapping contract IDs to social posts
        """
        logger.info(f"Starting social media data collection for {len(contracts)} contracts...")

        results = {}
        hours_back = self.settings.data_collection_lookback_hours

        # Process all contracts passed in (garbage already filtered out,
        # sorted best-first so most interesting contracts get processed first)
        for contract in contracts:
            contract_id = contract['id']
            question = contract['question']

            logger.info(f"Collecting social data for: {question[:50]}...")

            posts = []

            # Extract keywords from question
            keywords = self._extract_keywords(question)

            if not keywords:
                logger.debug(f"No meaningful keywords for: {question[:50]}... - skipping social collection")
                continue

            # Collect Twitter data
            if self.twitter.enabled:
                try:
                    for keyword in keywords[:3]:  # Limit keywords
                        twitter_posts = self.twitter.search_tweets(
                            query=keyword,
                            max_results=20,
                            hours_back=hours_back
                        )
                        posts.extend(twitter_posts)
                except Exception as e:
                    logger.error(f"Error collecting Twitter data: {e}")

            # Collect Reddit data
            if self.reddit.enabled:
                try:
                    # Get relevant subreddits based on category
                    subreddits = self.reddit.get_relevant_subreddits(
                        contract.get('category', '')
                    )

                    for keyword in keywords[:3]:
                        reddit_posts = self.reddit.search_multiple_subreddits(
                            subreddits=subreddits[:3],  # Limit subreddits
                            query=keyword,
                            max_per_subreddit=10,
                            hours_back=hours_back
                        )
                        posts.extend(reddit_posts)
                except Exception as e:
                    logger.error(f"Error collecting Reddit data: {e}")

            # Collect Bluesky data (FREE, always available)
            # Keyword search + contract title search for direct bet discussion
            if self.bluesky and self.bluesky.enabled:
                try:
                    bsky_all = []
                    for keyword in keywords[:3]:
                        bsky_posts = self.bluesky.search_posts(
                            query=keyword,
                            max_results=25,
                            hours_back=hours_back
                        )
                        bsky_all.extend(bsky_posts)

                    # Title search: people discussing the actual Polymarket question
                    title_query = question[:120].rstrip('?').strip()
                    bsky_title = self.bluesky.search_posts(
                        query=title_query,
                        max_results=25,
                        hours_back=hours_back
                    )
                    keyword_count = len(bsky_all)
                    seen_ids = {p.get('post_id') for p in bsky_all}
                    title_new = 0
                    for p in bsky_title:
                        if p.get('post_id') not in seen_ids:
                            bsky_all.append(p)
                            seen_ids.add(p.get('post_id'))
                            title_new += 1

                    posts.extend(bsky_all)
                    logger.info(f"Collected {len(bsky_all)} Bluesky posts "
                                f"({keyword_count} keyword + {title_new} title-search)")
                except Exception as e:
                    logger.error(f"Error collecting Bluesky data: {e}")

            # Collect RSS news data (FREE, always available)
            if self.rss_news:
                try:
                    news_articles = self.rss_news.search_news(
                        keywords=keywords[:5],  # Use more keywords for news
                        hours_back=hours_back
                    )

                    # Convert news articles to social post format.
                    # Use stable hash (SHA-256 of URL) so same article is deduplicated across runs.
                    for article in news_articles[:20]:  # Limit to 20 articles
                        url_hash = hashlib.sha256(article['url'].encode('utf-8')).hexdigest()[:16]
                        posts.append({
                            'post_id': f"rss_{url_hash}",
                            'platform': 'news_rss',
                            'author': article['author'],
                            'content': f"{article['title']}: {article['content']}",
                            'posted_at': article['published_at'],  # Fixed: changed from created_at to posted_at
                            'url': article['url'],
                            'engagement_score': 50,  # Default score for news
                            'source_name': article['source']
                        })

                    logger.info(f"Collected {len(news_articles)} news articles from RSS feeds")
                except Exception as e:
                    logger.error(f"Error collecting RSS news data: {e}")

            # Collect Tavily web search data (requires API key)
            # Keyword search + contract title search
            if self.tavily and self.tavily.enabled:
                try:
                    search_query = ' '.join(keywords[:3])
                    tavily_results = self.tavily.search(query=search_query, max_results=10)

                    # Title search: find articles about the specific market question
                    title_query = question[:120].rstrip('?').strip()
                    tavily_title = self.tavily.search(query=title_query, max_results=5)
                    keyword_count = len(tavily_results)
                    seen_ids = {p.get('post_id') for p in tavily_results}
                    title_new = 0
                    for p in tavily_title:
                        if p.get('post_id') not in seen_ids:
                            tavily_results.append(p)
                            seen_ids.add(p.get('post_id'))
                            title_new += 1

                    posts.extend(tavily_results)
                    logger.info(f"Collected {len(tavily_results)} Tavily web results "
                                f"({keyword_count} keyword + {title_new} title-search)")
                except Exception as e:
                    logger.error(f"Error collecting Tavily data: {e}")

            # Collect Grok X sentiment (requires API key)
            # Keyword search + contract title search
            if self.grok and self.grok.enabled:
                try:
                    search_query = ' '.join(keywords[:3])
                    grok_results = self.grok.analyze_x_sentiment(query=search_query)

                    # Title search: X posts discussing the actual bet
                    title_query = question[:120].rstrip('?').strip()
                    grok_title = self.grok.analyze_x_sentiment(query=title_query)
                    keyword_count = len(grok_results)
                    seen_ids = {p.get('post_id') for p in grok_results}
                    title_new = 0
                    for p in grok_title:
                        if p.get('post_id') not in seen_ids:
                            grok_results.append(p)
                            seen_ids.add(p.get('post_id'))
                            title_new += 1

                    posts.extend(grok_results)
                    logger.info(f"Collected {len(grok_results)} Grok X posts "
                                f"({keyword_count} keyword + {title_new} title-search)")
                except Exception as e:
                    logger.error(f"Error collecting Grok data: {e}")

            # Collect X mirror posts (free fallback, only when Grok unavailable)
            # Two searches: keywords for broad topic sentiment + contract title
            # for people specifically discussing the Polymarket bet
            if self.x_mirror and self.x_mirror.enabled:
                try:
                    # Search 1: keyword-based (broad topic)
                    search_query = ' '.join(keywords[:3])
                    mirror_results = self.x_mirror.search_posts(query=search_query)

                    # Search 2: contract title (people discussing the actual bet)
                    # Truncate long questions to keep the search focused
                    title_query = question[:120].rstrip('?').strip()
                    title_results = self.x_mirror.search_posts(query=title_query)

                    # Deduplicate by post_id before merging
                    keyword_count = len(mirror_results)
                    seen_ids = {p['post_id'] for p in mirror_results}
                    title_new = 0
                    for p in title_results:
                        if p['post_id'] not in seen_ids:
                            mirror_results.append(p)
                            seen_ids.add(p['post_id'])
                            title_new += 1

                    posts.extend(mirror_results)
                    logger.info(f"Collected {len(mirror_results)} X mirror posts "
                                f"({keyword_count} keyword + {title_new} title-search)")
                except Exception as e:
                    logger.error(f"Error collecting X mirror data: {e}")

            # Collect GDELT geopolitical news (free, no key required)
            # Keyword-only — GDELT indexes news articles, not betting markets,
            # so contract title search won't match news headlines
            if self.gdelt and self.gdelt.enabled:
                try:
                    search_query = ' '.join(keywords[:3])
                    gdelt_results = self.gdelt.search_news(query=search_query, days_back=3)
                    posts.extend(gdelt_results)
                    logger.info(f"Collected {len(gdelt_results)} GDELT articles")
                except Exception as e:
                    logger.error(f"Error collecting GDELT data: {e}")

            # Collect Polymarket comments (uses existing API, always available)
            try:
                poly_contract_id = contract.get('contract_id', '')
                if poly_contract_id:
                    poly_comments = self.polymarket.get_market_comments(
                        condition_id=poly_contract_id, limit=30
                    )
                    posts.extend(poly_comments)
                    if poly_comments:
                        logger.info(f"Collected {len(poly_comments)} Polymarket comments")
            except Exception as e:
                logger.debug(f"Error collecting Polymarket comments: {e}")

            # Collect Manifold comments (free, cross-reference matching markets)
            if self.manifold and self.manifold.enabled:
                try:
                    # Search for matching Manifold market to get its comments
                    search_query = ' '.join(keywords[:3])
                    manifold_markets = self.manifold.search_markets(query=search_query, limit=3)
                    for mm in manifold_markets:
                        mm_id = mm.get('market_id', '')
                        if mm_id:
                            manifold_comments = self.manifold.get_market_comments(
                                market_id=mm_id, limit=20
                            )
                            posts.extend(manifold_comments)
                    total_mc = sum(1 for p in posts if p.get('platform') == 'manifold_comment')
                    if total_mc:
                        logger.info(f"Collected {total_mc} Manifold comments")
                except Exception as e:
                    logger.debug(f"Error collecting Manifold comments: {e}")

            # Store posts in database
            if posts:
                stored_posts = self._store_social_posts(posts, contract_id)
                results[contract_id] = stored_posts

            logger.info(f"Collected {len(posts)} social posts for contract {contract_id}")

        logger.info(f"Social media collection complete: {sum(len(p) for p in results.values())} total posts")
        return results

    def _store_social_posts(self, posts: List[Dict], contract_id: str) -> List[Dict]:
        """
        Store social media posts in database.

        Deduplicates posts by post_id before inserting. Commits after each
        successful post so one failing post does not roll back the whole batch.

        Args:
            posts: List of post dictionaries
            contract_id: Associated contract UUID

        Returns:
            List of stored post dictionaries
        """
        stored_posts = []

        # Deduplicate incoming posts by post_id
        seen = set()
        unique_posts = []
        for p in posts:
            pid = p.get('post_id')
            if pid and pid not in seen:
                seen.add(pid)
                unique_posts.append(p)

        try:
            with self.db_manager.get_session() as session:
                for post_data in unique_posts:
                    try:
                        # Check if post already exists
                        existing = session.query(SocialPost).filter(
                            SocialPost.post_id == post_data['post_id']
                        ).first()

                        if existing:
                            # Update related contracts if needed
                            if contract_id not in [str(c) for c in (existing.related_contracts or [])]:
                                contracts_list = list(existing.related_contracts or [])
                                contracts_list.append(UUID(contract_id))
                                existing.related_contracts = contracts_list
                            session.commit()
                            continue

                        # Create new post
                        post = SocialPost(
                            post_id=post_data['post_id'],
                            platform=post_data['platform'],
                            author=post_data.get('author'),
                            content=post_data['content'],
                            url=post_data.get('url'),
                            engagement_score=post_data.get('engagement_score', 0),
                            posted_at=post_data['posted_at'],
                            related_contracts=[UUID(contract_id)]
                        )
                        session.add(post)
                        session.commit()
                        stored_posts.append(post_data)

                    except Exception as e:
                        session.rollback()
                        logger.debug(f"Skipped post {post_data.get('post_id', '?')}: {e}")

        except Exception as e:
            logger.error(f"Error storing social posts: {e}")

        return stored_posts

    @staticmethod
    def _extract_keywords(question: str) -> List[str]:
        """
        Extract meaningful search keywords from a Polymarket question.

        Filters out stop words, numbers, price tokens, and generic terms
        to produce keywords that will return relevant social media results.

        Args:
            question: Market question

        Returns:
            List of keywords (most specific first)
        """
        import re

        stop_words = {
            # Determiners / articles
            'the', 'a', 'an', 'this', 'that', 'these', 'those',
            # Prepositions
            'in', 'on', 'at', 'to', 'for', 'of', 'by', 'with', 'from',
            'into', 'through', 'during', 'before', 'after', 'above', 'below',
            'between', 'under', 'over', 'about', 'against', 'within',
            # Conjunctions
            'and', 'or', 'but', 'nor', 'yet', 'so',
            # Pronouns
            'he', 'she', 'it', 'they', 'them', 'his', 'her', 'its', 'their',
            'who', 'whom', 'which', 'what', 'whose',
            # Common verbs
            'will', 'would', 'could', 'should', 'shall', 'may', 'might',
            'can', 'does', 'did', 'has', 'have', 'had', 'been', 'being',
            'was', 'were', 'are', 'is', 'be', 'do', 'get', 'got',
            'become', 'reach', 'exceed', 'fall', 'rise', 'drop', 'hit',
            'remain', 'stay', 'happen', 'occur', 'take', 'make', 'go',
            'win', 'lose', 'pass', 'fail', 'sign', 'announce', 'report',
            'increase', 'decrease', 'collect', 'receive', 'give', 'keep',
            'hold', 'release', 'close', 'open', 'set', 'run', 'lead',
            'move', 'change', 'turn', 'show', 'come', 'leave', 'call',
            'pay', 'play', 'put', 'bring', 'use', 'try', 'ask', 'tell',
            'say', 'said', 'know', 'think', 'see', 'want', 'need', 'look',
            'find', 'give', 'work', 'seem', 'feel', 'provide', 'include',
            'consider', 'appear', 'allow', 'meet', 'add', 'expect',
            'continue', 'create', 'offer', 'serve', 'cause', 'require',
            'follow', 'agree', 'support', 'produce', 'lose', 'return',
            # Generic nouns (too broad for useful search)
            'yes', 'no', 'more', 'less', 'than',
            'least', 'most', 'end', 'start', 'begin', 'next', 'last', 'first',
            'many', 'much', 'some', 'any', 'each', 'every', 'all',
            'other', 'another', 'such', 'only', 'also', 'just',
            'how', 'when', 'where', 'why', 'whether',
            'per', 'cost', 'price', 'total', 'number', 'amount',
            'people', 'person', 'year', 'years', 'month', 'months',
            'day', 'days', 'week', 'weeks', 'time', 'date',
            'level', 'rate', 'share', 'point', 'part', 'place',
            'case', 'group', 'company', 'system', 'program', 'question',
            'government', 'world', 'area', 'state', 'states',
            'market', 'markets', 'billion', 'million', 'trillion',
            'average', 'high', 'low', 'new', 'old', 'long', 'short',
            'revenue', 'value', 'growth', 'result', 'report', 'data',
            'percent', 'currently', 'based', 'likely', 'according',
            'announced', 'expected', 'still', 'even', 'well', 'back',
            'official', 'officially', 'current', 'annual', 'daily',
            'approximately', 'roughly', 'estimated', 'around',
        }

        # Clean the question
        text = question.replace('?', '').replace(',', '').replace("'s", '')

        # Remove dollar amounts, percentages, and number ranges
        text = re.sub(r'\$[\d,.]+\+?', '', text)
        text = re.sub(r'[\d,.]+%', '', text)
        text = re.sub(r'[\d,.]+-[\d,.]+', '', text)
        text = re.sub(r'\b\d{1,3}(,\d{3})+\b', '', text)  # e.g. 1,750,000

        words = text.split()

        # Keep capitalized words (proper nouns) with priority
        proper_nouns = []
        regular_words = []
        for w in words:
            clean = re.sub(r'[^a-zA-Z]', '', w)
            if len(clean) < 3:
                continue
            if clean.lower() in stop_words:
                continue
            if w[0].isupper():
                proper_nouns.append(clean)
            else:
                regular_words.append(clean.lower())

        # Proper nouns first (Trump, Bitcoin, etc.), then other meaningful words
        keywords = proper_nouns + regular_words

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for kw in keywords:
            kw_lower = kw.lower()
            if kw_lower not in seen:
                seen.add(kw_lower)
                unique.append(kw)

        # If no proper nouns were found and only 1 generic word remains,
        # the keywords aren't specific enough for useful search results
        if not proper_nouns and len(unique) <= 1:
            return []

        return unique[:5]

    def create_collection_task(self) -> Task:
        """
        Create CrewAI task for data collection.

        Returns:
            CrewAI Task instance
        """
        return Task(
            description="""Collect comprehensive data from Polymarket and social media:
            1. Fetch active Polymarket contracts (up to {max_contracts})
            2. For each contract, extract relevant keywords
            3. Search Twitter/X for related posts from last {hours} hours
            4. Search Reddit for related posts from last {hours} hours
            5. Store all data in PostgreSQL database
            6. Track historical odds changes
            """.format(
                max_contracts=self.settings.max_contracts_per_cycle,
                hours=self.settings.data_collection_lookback_hours
            ),
            agent=self.create_crewai_agent(),
            expected_output="Dictionary containing collected contracts and social media posts"
        )

    def run(self) -> Dict:
        """
        Execute data collection workflow.

        Returns:
            Dictionary with collection results
        """
        logger.info("=== Starting Data Collection Agent ===")

        # Collect market data
        contracts = self.collect_market_data()

        # Collect social media data
        social_data = self.collect_social_media_data(contracts)

        results = {
            'contracts': contracts,
            'social_posts': social_data,
            'timestamp': datetime.utcnow().isoformat()
        }

        logger.info(f"Data collection complete: {len(contracts)} contracts, "
                   f"{sum(len(p) for p in social_data.values())} social posts")

        return results
