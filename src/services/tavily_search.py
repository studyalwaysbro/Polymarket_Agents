"""Tavily web search integration for real-time news and information."""

import hashlib
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"


class TavilySearch:
    """
    Tavily web search client for real-time information retrieval.

    Requires TAVILY_API_KEY in .env. Silently disabled if key is missing.
    """

    def __init__(self):
        self.settings = get_settings()
        self.enabled = self.settings.enable_tavily and self.settings.has_tavily_credentials
        self.session = self._create_session()

        # Circuit breaker: skip remaining searches after 2 consecutive 429s
        self._consecutive_429s = 0
        self._quota_exhausted = False

        if self.enabled:
            logger.info("Tavily Search initialized")
        else:
            logger.debug("Tavily Search disabled (no API key)")

    def reset_cycle(self):
        """Reset circuit breaker at the start of each collection cycle."""
        self._consecutive_429s = 0
        self._quota_exhausted = False

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST"],
            backoff_factor=1
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        return session

    def search(self, query: str, max_results: int = 10) -> List[Dict]:
        """
        Search the web for real-time news and information.

        Args:
            query: Search query string
            max_results: Maximum number of results

        Returns:
            List of result dicts with keys: post_id, platform, content, url, posted_at
        """
        if not self.enabled:
            return []

        if self._quota_exhausted:
            logger.debug("Tavily circuit breaker active — skipping search")
            return []

        try:
            payload = {
                "api_key": self.settings.tavily_api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": min(max_results, 20),
                "include_raw_content": False
            }

            response = self.session.post(TAVILY_API_URL, json=payload, timeout=15)
            response.raise_for_status()
            data = response.json()

            results = []
            for r in data.get("results", []):
                url = r.get("url", "")
                url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
                content = f"{r.get('title', '')}: {r.get('content', '')}"

                results.append({
                    "post_id": f"tavily_{url_hash}",
                    "platform": "tavily_web",
                    "author": r.get("url", "web"),
                    "content": content[:2000],
                    "url": url,
                    "posted_at": datetime.now(timezone.utc),
                    "engagement_score": int((r.get("score", 0.5)) * 100),
                })

            self._consecutive_429s = 0  # Reset on success
            logger.info(f"Tavily: found {len(results)} results for '{query}'")
            return results

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                self._consecutive_429s += 1
                if self._consecutive_429s >= 2:
                    self._quota_exhausted = True
                    logger.warning("Tavily quota exhausted — circuit breaker tripped for this cycle")
                else:
                    logger.warning("Tavily rate limit hit (429)")
            else:
                logger.error(f"Tavily HTTP error: {e}")
            return []
        except Exception as e:
            logger.error(f"Tavily search error: {e}")
            return []
