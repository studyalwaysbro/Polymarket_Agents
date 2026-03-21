"""Polymarket API integration with ethical data fetching."""

import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
from ratelimit import limits, sleep_and_retry

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


class PolymarketAPI:
    """
    Ethical Polymarket API client with rate limiting and retry logic.

    Respects platform terms of service and implements best practices:
    - Rate limiting per API guidelines
    - Retry logic with exponential backoff
    - User agent identification
    - Proper error handling
    """

    def __init__(self):
        """Initialize Polymarket API client."""
        self.settings = get_settings()
        self.base_url = self.settings.polymarket_api_url
        self.gamma_url = self.settings.polymarket_gamma_api_url
        self.strapi_url = self.settings.polymarket_strapi_url

        # Configure session with retry logic (do NOT retry 429 here; we handle it in _make_request)
        self.session = self._create_session()

        # Rate limiting configuration (10 requests per minute by default)
        self.rate_limit_calls = self.settings.polymarket_rate_limit
        self.rate_limit_period = 60  # seconds
        # Max 429 retries with backoff to avoid infinite recursion / stack overflow
        self._max_429_retries = 5
        self._429_backoff_base_seconds = 60

        logger.info("Polymarket API client initialized")

    def _create_session(self) -> requests.Session:
        """Create requests session with retry logic. 429 excluded so we handle it in _make_request."""
        session = requests.Session()

        # Retry only for server errors (5xx), NOT 429 - we handle 429 in a loop with backoff
        retry_strategy = Retry(
            total=3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            backoff_factor=1
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # Set user agent
        session.headers.update({
            'User-Agent': 'PolymarketGapDetector/1.0 (Educational Research)',
            'Accept': 'application/json'
        })

        return session

    @sleep_and_retry
    @limits(calls=10, period=60)  # Rate limit: 10 calls per minute
    def _make_request(self, url: str, params: Optional[Dict] = None) -> Dict:
        """
        Make rate-limited API request. On 429, backs off in a loop (no recursion).

        Args:
            url: API endpoint URL
            params: Query parameters

        Returns:
            JSON response as dictionary

        Raises:
            requests.RequestException: If request fails after retries
        """
        last_error = None
        for attempt in range(self._max_429_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=30)
                if response.status_code == 429:
                    if attempt < self._max_429_retries:
                        backoff = self._429_backoff_base_seconds * (2 ** attempt)
                        logger.warning("Rate limit (429), backing off %ds (attempt %d/%d)", backoff, attempt + 1, self._max_429_retries)
                        time.sleep(backoff)
                        continue
                    response.raise_for_status()
                response.raise_for_status()
                return response.json()
            except requests.exceptions.HTTPError as e:
                last_error = e
                if e.response is not None and e.response.status_code == 429:
                    if attempt >= self._max_429_retries:
                        logger.error("Rate limit (429) persisted after %d retries", self._max_429_retries)
                    raise
                logger.error(f"HTTP error: {e}")
                raise
            except requests.exceptions.RequestException as e:
                last_error = e
                logger.error(f"Request error: {e}")
                raise
        if last_error:
            raise last_error
        raise requests.exceptions.RequestException("Request failed after retries")

    def get_active_markets(self, limit: int = 100) -> List[Dict]:
        """
        Fetch active prediction markets with pagination.

        Args:
            limit: Maximum total number of markets to fetch

        Returns:
            List of market dictionaries
        """
        try:
            url = f"{self.gamma_url}/markets"
            all_markets = []
            page_size = 100  # API max per request
            offset = 0

            logger.info(f"Fetching active markets (target={limit})")

            while len(all_markets) < limit:
                params = {
                    'closed': 'false',
                    'limit': min(page_size, limit - len(all_markets)),
                    'offset': offset
                }

                response = self._make_request(url, params)
                markets = response if isinstance(response, list) else response.get('data', [])

                if not markets:
                    break  # No more results

                all_markets.extend(markets)
                offset += len(markets)

                # If we got fewer than page_size, there are no more pages
                if len(markets) < page_size:
                    break

            logger.info(f"Fetched {len(all_markets)} active markets across {offset // page_size + 1} page(s)")
            return all_markets

        except Exception as e:
            logger.error(f"Error fetching active markets: {e}")
            return all_markets if all_markets else []

    def get_market_details(self, condition_id: str) -> Optional[Dict]:
        """
        Get detailed information about a specific market.

        Args:
            condition_id: Market condition ID

        Returns:
            Market details dictionary or None if not found
        """
        try:
            url = f"{self.gamma_url}/markets/{condition_id}"
            logger.debug(f"Fetching market details for {condition_id}")

            response = self._make_request(url)
            return response

        except Exception as e:
            logger.error(f"Error fetching market details for {condition_id}: {e}")
            return None

    def get_market_prices(self, condition_id: str) -> Optional[Dict]:
        """
        Get current prices for a market.

        Args:
            condition_id: Market condition ID

        Returns:
            Price data dictionary or None if not found
        """
        try:
            url = f"{self.base_url}/prices"
            params = {'market': condition_id}

            logger.debug(f"Fetching prices for market {condition_id}")
            response = self._make_request(url, params)

            return response

        except Exception as e:
            logger.error(f"Error fetching prices for {condition_id}: {e}")
            return None

    def get_market_orderbook(self, token_id: str) -> Optional[Dict]:
        """
        Get order book for a specific token.

        Args:
            token_id: Token ID

        Returns:
            Order book data or None if not found
        """
        try:
            url = f"{self.base_url}/book"
            params = {'token_id': token_id}

            logger.debug(f"Fetching order book for token {token_id}")
            response = self._make_request(url, params)

            return response

        except Exception as e:
            logger.error(f"Error fetching order book for {token_id}: {e}")
            return None

    @staticmethod
    def _safe_decimal(value) -> Decimal:
        """Safely convert a value to Decimal, returning None on failure."""
        if not value:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    def parse_market_to_contract(self, market: Dict) -> Dict:
        """
        Parse Polymarket API response into standardized contract format.

        Args:
            market: Raw market data from API

        Returns:
            Standardized contract dictionary
        """
        try:
            # Validate input is a dictionary
            if not isinstance(market, dict):
                logger.error(f"Expected dict, got {type(market)}: {str(market)[:100]}")
                return {}

            # Extract key fields (Polymarket API structure may vary)
            contract_id = market.get('condition_id') or market.get('id')
            question = market.get('question') or market.get('title', '')
            description = market.get('description', '')

            # Parse end date
            end_date_str = market.get('end_date_iso') or market.get('endDate')
            end_date = None
            if end_date_str:
                try:
                    end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                except:
                    pass

            # Get current odds from outcomes
            outcomes = market.get('outcomes', [])
            outcome_prices = market.get('outcomePrices', [])
            yes_odds = None
            no_odds = None

            # Gamma API returns JSON-encoded strings: '["0.5", "0.5"]'
            if isinstance(outcome_prices, str):
                try:
                    import json as _json
                    outcome_prices = _json.loads(outcome_prices)
                except Exception:
                    outcome_prices = []
            if isinstance(outcomes, str):
                try:
                    import json as _json
                    outcomes = _json.loads(outcomes)
                except Exception:
                    outcomes = []

            # Method 1: outcomePrices array (most common from Gamma API)
            if outcome_prices and len(outcome_prices) >= 2:
                try:
                    yes_odds = Decimal(str(outcome_prices[0]))
                    no_odds = Decimal(str(outcome_prices[1]))
                except Exception:
                    pass

            # Method 2: outcomes as dicts with .price (fallback)
            if yes_odds is None and len(outcomes) >= 2:
                try:
                    outcome_0 = outcomes[0]
                    outcome_1 = outcomes[1]
                    if isinstance(outcome_0, dict) and isinstance(outcome_1, dict):
                        yes_odds = Decimal(str(outcome_0.get('price', 0)))
                        no_odds = Decimal(str(outcome_1.get('price', 0)))
                except (ValueError, TypeError, KeyError, AttributeError) as e:
                    logger.debug(f"Could not parse outcome prices: {e}")

            # Extract category from events array (Gamma API nests it there)
            category = market.get('category', 'Unknown')
            if category == 'Unknown':
                events = market.get('events', [])
                if events and isinstance(events, list):
                    for ev in events:
                        if isinstance(ev, dict):
                            cat = ev.get('category') or ev.get('title') or ev.get('slug', '')
                            if cat:
                                category = cat
                                break

            # Get volume and liquidity
            volume_24h = market.get('volume24hr', 0)
            liquidity = market.get('liquidity', 0)

            return {
                'contract_id': contract_id,
                'question': question,
                'description': description,
                'end_date': end_date,
                'category': category,
                'current_yes_odds': yes_odds,
                'current_no_odds': no_odds,
                'volume_24h': self._safe_decimal(volume_24h),
                'liquidity': self._safe_decimal(liquidity),
                'active': not market.get('closed', False),
                'raw_data': market  # Keep raw data for reference
            }

        except Exception as e:
            logger.error(f"Error parsing market data: {e}")
            logger.debug(f"Market data type: {type(market)}, value: {str(market)[:200]}")
            return {}

    def get_market_comments(self, condition_id: str, limit: int = 50) -> List[Dict]:
        """
        Fetch comments/activity for a Polymarket market.

        Uses the Gamma API activity endpoint to get user comments and discussions.

        Args:
            condition_id: Market condition ID or slug
            limit: Maximum comments to return

        Returns:
            List of comment dicts with post_id, content, author, posted_at, platform
        """
        comments = []

        try:
            # Polymarket Gamma API exposes activity/comments per market
            url = f"{self.gamma_url}/markets/{condition_id}/activity"
            params = {'limit': limit}

            try:
                response = self._make_request(url, params)
            except Exception:
                # Fallback: try the event-level comments endpoint
                url = f"{self.gamma_url}/events/{condition_id}/comments"
                response = self._make_request(url, params)

            if not response:
                return []

            activities = response if isinstance(response, list) else response.get('data', [])

            for item in activities:
                # Activity items may be comments, trades, or other events
                content = item.get('content') or item.get('body') or item.get('text', '')
                if not content or len(content.strip()) < 5:
                    continue

                comment_id = item.get('id') or item.get('_id', '')
                author = (item.get('user', {}).get('username')
                          or item.get('username')
                          or item.get('author', 'anonymous'))

                posted_at_str = item.get('created_at') or item.get('createdAt') or item.get('timestamp')
                if posted_at_str:
                    try:
                        posted_at = datetime.fromisoformat(posted_at_str.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        posted_at = datetime.now(timezone.utc)
                else:
                    posted_at = datetime.now(timezone.utc)

                comments.append({
                    'post_id': f"polymarket_comment_{comment_id}",
                    'platform': 'polymarket_comment',
                    'author': str(author),
                    'content': content.strip(),
                    'posted_at': posted_at,
                    'url': f"https://polymarket.com/event/{condition_id}",
                    'engagement_score': int(item.get('likes', 0)) + int(item.get('replies', 0)) * 2,
                })

            logger.info(f"Fetched {len(comments)} comments for market {condition_id[:20]}")

        except Exception as e:
            logger.debug(f"Could not fetch Polymarket comments for {condition_id}: {e}")

        return comments

    def search_markets(self, query: str, limit: int = 10) -> List[Dict]:
        """
        Search markets by keyword.

        Args:
            query: Search query
            limit: Maximum results

        Returns:
            List of matching markets
        """
        try:
            # Get all active markets and filter client-side
            # (Polymarket may not have a dedicated search endpoint)
            all_markets = self.get_active_markets(limit=100)

            # Simple keyword matching
            query_lower = query.lower()
            matching = [
                m for m in all_markets
                if query_lower in m.get('question', '').lower() or
                   query_lower in m.get('description', '').lower()
            ]

            return matching[:limit]

        except Exception as e:
            logger.error(f"Error searching markets: {e}")
            return []
