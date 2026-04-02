"""GDELT geopolitical news API (free, no key required)."""

import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

GDELT_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


class GDELTAPI:
    """
    GDELT Document API client for structured geopolitical news.

    Free, no API key required. Always enabled.
    Provides global news coverage with tone/sentiment data.
    """

    def __init__(self):
        self.settings = get_settings()
        self.enabled = self.settings.enable_gdelt
        self.session = self._create_session()

        # Circuit breaker: skip remaining searches after 2 consecutive 429s
        self._consecutive_429s = 0
        self._quota_exhausted = False

        if self.enabled:
            logger.info("GDELT API initialized (free, no key required)")

    def reset_cycle(self):
        """Reset circuit breaker at the start of each collection cycle."""
        self._consecutive_429s = 0
        self._quota_exhausted = False

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
            backoff_factor=1
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.headers.update({
            "User-Agent": "PolymarketGapDetector/1.0 (Educational Research)",
        })
        return session

    def search_news(self, query: str, days_back: int = 3, max_results: int = 25) -> List[Dict]:
        """
        Search GDELT for news articles about a topic.

        Args:
            query: Search query
            days_back: How many days to look back
            max_results: Maximum articles to return

        Returns:
            List of article dicts in standard post format
        """
        if not self.enabled:
            return []

        if self._quota_exhausted:
            logger.debug("GDELT circuit breaker active — skipping search")
            return []

        try:
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": min(max_results, 250),
                "timespan": f"{days_back}d",
                "sort": "hybridrel",
                "format": "json"
            }

            response = self.session.get(GDELT_API_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            articles = data.get("articles", [])
            if not articles:
                return []

            results = []
            for article in articles[:max_results]:
                url = article.get("url", "")
                url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]

                # GDELT provides tone as a float (positive = positive tone)
                tone = article.get("tone", 0)

                title = article.get("title", "N/A")
                domain = article.get("domain", "unknown")
                seen_date = article.get("seendate", "")

                # Parse GDELT date format (YYYYMMDDTHHmmssZ)
                try:
                    posted_at = datetime.strptime(seen_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    posted_at = datetime.now(timezone.utc)

                results.append({
                    "post_id": f"gdelt_{url_hash}",
                    "platform": "gdelt",
                    "author": domain,
                    "content": title[:1000],
                    "url": url,
                    "posted_at": posted_at,
                    "engagement_score": 60,
                    "gdelt_tone": tone,
                })

            self._consecutive_429s = 0  # Reset on success
            logger.info(f"GDELT: found {len(results)} articles for '{query}'")
            return results

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                self._consecutive_429s += 1
                if self._consecutive_429s >= 2:
                    self._quota_exhausted = True
                    logger.warning("GDELT rate limited — circuit breaker tripped for this cycle")
                else:
                    logger.warning("GDELT rate limit hit (429)")
            else:
                logger.error(f"GDELT HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"GDELT search error: {e}")
            return []
