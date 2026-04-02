"""Bluesky social media data collection via AT Protocol API (free account required)."""

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from ratelimit import limits, sleep_and_retry

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

API_URL = "https://bsky.social/xrpc"


class BlueskyScraper:
    """
    Bluesky data collector using the AT Protocol API.

    Free to use - only requires a free Bluesky account.
    Authenticates via app password for search access.
    """

    def __init__(self):
        """Initialize Bluesky scraper."""
        self.settings = get_settings()
        self.enabled = self.settings.enable_bluesky and self.settings.has_bluesky_credentials
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
        })
        self.access_token: Optional[str] = None

        if self.enabled:
            self._authenticate()
        else:
            logger.warning("Bluesky scraper disabled (no credentials configured)")

    def _authenticate(self) -> bool:
        """Authenticate with Bluesky using handle + app password.

        Returns:
            True if authentication succeeded, False otherwise.
        """
        try:
            resp = self.session.post(
                f"{API_URL}/com.atproto.server.createSession",
                json={
                    "identifier": self.settings.bluesky_handle,
                    "password": self.settings.bluesky_app_password,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            self.access_token = data["accessJwt"]
            self.session.headers["Authorization"] = f"Bearer {self.access_token}"
            self._auth_time = time.monotonic()
            logger.info(f"Bluesky authenticated as {self.settings.bluesky_handle}")
            return True
        except Exception as e:
            logger.error(f"Bluesky authentication failed: {e}")
            self.enabled = False
            return False

    def _ensure_authenticated(self) -> bool:
        """Check if the current session is likely valid; re-authenticate if needed.

        AT Protocol access tokens expire after ~2 hours. This proactively
        refreshes when approaching expiry rather than waiting for a 401.

        Returns:
            True if authenticated and ready, False if auth failed.
        """
        if not self.access_token:
            logger.warning("Bluesky: no access token, attempting authentication...")
            return self._authenticate()

        # Proactively re-auth if token is older than 90 minutes (tokens last ~2h)
        elapsed = time.monotonic() - getattr(self, "_auth_time", 0)
        if elapsed > 5400:  # 90 minutes
            logger.info("Bluesky: token nearing expiry, refreshing session...")
            return self._authenticate()

        return True

    @sleep_and_retry
    @limits(calls=30, period=60)
    def search_posts(
        self,
        query: str,
        max_results: int = 25,
        hours_back: int = 6,
    ) -> List[Dict]:
        """
        Search Bluesky posts matching query.

        Args:
            query: Search query string
            max_results: Maximum posts to return (API max 100)
            hours_back: How many hours back to search

        Returns:
            List of post dictionaries in standardized format
        """
        if not self.enabled:
            return []

        if not self._ensure_authenticated():
            return []

        return self._do_search(query, max_results, hours_back, allow_retry=True)

    def _do_search(
        self,
        query: str,
        max_results: int,
        hours_back: int,
        allow_retry: bool = False,
    ) -> List[Dict]:
        """Execute the actual search request.

        Args:
            query: Search query string
            max_results: Maximum posts to return
            hours_back: How many hours back to search
            allow_retry: If True, retry once after re-auth on 401

        Returns:
            List of post dictionaries in standardized format
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

            params = {
                "q": query,
                "limit": min(max_results, 100),
                "sort": "latest",
                "since": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }

            response = self.session.get(
                f"{API_URL}/app.bsky.feed.searchPosts",
                params=params,
                timeout=15,
            )
            response.raise_for_status()

            data = response.json()
            raw_posts = data.get("posts", [])

            if not raw_posts:
                logger.debug(f"No Bluesky posts found for: {query}")
                return []

            posts = []
            for post in raw_posts:
                parsed = self._parse_post(post)
                if parsed:
                    posts.append(parsed)

            logger.info(f"Found {len(posts)} Bluesky posts for: {query}")
            return posts

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 429:
                logger.warning("Bluesky rate limit hit, backing off...")
                time.sleep(30)
            elif e.response is not None and e.response.status_code == 401:
                if allow_retry:
                    logger.warning("Bluesky token expired mid-search, re-authenticating and retrying...")
                    if self._authenticate():
                        return self._do_search(query, max_results, hours_back, allow_retry=False)
                    else:
                        logger.warning("Bluesky re-auth failed after 401; disabling scraper for this run")
                else:
                    logger.error("Bluesky 401 after re-auth retry; disabling scraper")
                    self.enabled = False
            else:
                logger.error(f"Bluesky API error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error searching Bluesky: {e}")
            return []

    def search_by_keywords(
        self,
        keywords: List[str],
        max_per_keyword: int = 25,
        hours_back: int = 6,
    ) -> List[Dict]:
        """
        Search Bluesky for multiple keywords with deduplication.

        Args:
            keywords: List of search keywords
            max_per_keyword: Max posts per keyword
            hours_back: Hours to look back

        Returns:
            Combined list of unique posts
        """
        all_posts = []
        seen_ids = set()

        for keyword in keywords:
            posts = self.search_posts(
                query=keyword,
                max_results=max_per_keyword,
                hours_back=hours_back,
            )

            for post in posts:
                if post["post_id"] not in seen_ids:
                    all_posts.append(post)
                    seen_ids.add(post["post_id"])

            time.sleep(1)

        logger.info(f"Collected {len(all_posts)} unique Bluesky posts across {len(keywords)} keywords")
        return all_posts

    def _parse_post(self, post: Dict) -> Dict:
        """
        Parse a Bluesky post into standardized format.

        Args:
            post: Raw post object from API

        Returns:
            Standardized post dictionary
        """
        try:
            record = post.get("record", {})
            author = post.get("author", {})

            # Extract post URI and convert to web URL
            uri = post.get("uri", "")
            handle = author.get("handle", "unknown")

            # URI format: at://did:plc:xxx/app.bsky.feed.post/rkey
            # Web URL format: https://bsky.app/profile/handle/post/rkey
            rkey = uri.split("/")[-1] if uri else ""
            web_url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else ""

            # Parse timestamp
            created_at_str = record.get("createdAt", "")
            if created_at_str:
                posted_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            else:
                posted_at = datetime.now(timezone.utc)

            return {
                "post_id": f"bsky_{rkey}",
                "platform": "bluesky",
                "author": handle,
                "content": record.get("text", ""),
                "url": web_url,
                "engagement_score": self._calculate_engagement(post),
                "posted_at": posted_at,
            }

        except Exception as e:
            logger.debug(f"Error parsing Bluesky post: {e}")
            return None

    @staticmethod
    def _calculate_engagement(post: Dict) -> int:
        """
        Calculate engagement score from Bluesky post metrics.

        Args:
            post: Raw post object from API

        Returns:
            Engagement score
        """
        likes = post.get("likeCount", 0)
        reposts = post.get("repostCount", 0)
        replies = post.get("replyCount", 0)

        # Weight reposts and replies higher (same logic as Twitter scraper)
        return likes + (reposts * 3) + (replies * 2)
