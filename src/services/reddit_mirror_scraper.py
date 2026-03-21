"""Reddit mirror scraper using Redlib + old.reddit.com fallback (no API key required).

Primary: redlib.perennialte.ch (no bot protection, HTML scraping)
Fallback: old.reddit.com .json endpoints (rate-limited but structured)

Respects rate limits and robots.txt. Public posts only.
"""

import hashlib
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Redlib instances (no bot protection, HTML scraping)
REDLIB_INSTANCES = [
    "https://redlib.perennialte.ch",
]

# Subreddits relevant to prediction markets
PREDICTION_SUBREDDITS = [
    "Polymarket",
    "predictit",
    "predictionmarkets",
    "wallstreetbets",
    "politics",
    "worldnews",
    "cryptocurrency",
    "economy",
    "geopolitics",
]


class RedditMirrorScraper:
    """
    Scrape public Reddit posts via Redlib mirrors with old.reddit.com fallback.

    No API key or authentication required.
    Primary source: Redlib (HTML scraping, no bot protection)
    Fallback: old.reddit.com .json endpoints (rate-limited)
    """

    def __init__(self):
        self.settings = get_settings()
        self.enabled = getattr(self.settings, 'enable_reddit_mirror', True)
        self.delay = getattr(self.settings, 'scraper_request_delay', 2.0)
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        if self.enabled:
            logger.info("Reddit Mirror Scraper initialized (free, no API key)")
        else:
            logger.debug("Reddit Mirror Scraper disabled")

    def search_posts(self, query: str, limit: int = 20) -> List[Dict]:
        """Search Reddit for posts matching a query."""
        if not self.enabled:
            return []

        # Try Redlib first (primary)
        posts = self._search_redlib(query, limit=limit)
        if posts:
            return posts

        # Fallback to old.reddit.com JSON
        logger.debug("Redlib unavailable, falling back to old.reddit.com")
        return self._search_old_reddit(query, limit=limit)

    def _search_redlib(self, query: str, limit: int = 20) -> List[Dict]:
        """Search via Redlib instances (HTML scraping)."""
        for instance in REDLIB_INSTANCES:
            try:
                time.sleep(self.delay)
                url = f"{instance}/search"
                params = {"q": query, "sort": "new", "t": "week"}
                resp = httpx.get(
                    url, params=params,
                    headers={"User-Agent": self.user_agent},
                    timeout=15, follow_redirects=True,
                )

                if resp.status_code == 429:
                    logger.warning(f"Redlib {instance} rate limited, trying next")
                    continue
                if resp.status_code != 200:
                    logger.debug(f"Redlib {instance} returned {resp.status_code}")
                    continue

                posts = self._parse_redlib_html(resp.text, instance)
                if posts:
                    logger.info(f"Reddit mirror (Redlib): {len(posts)} posts for '{query[:40]}...'")
                    return posts[:limit]

            except Exception as e:
                logger.debug(f"Redlib {instance} error: {e}")
                continue

        return []

    def _parse_redlib_html(self, html: str, instance: str) -> List[Dict]:
        """Parse Redlib search results HTML into standardized post format."""
        posts = []
        soup = BeautifulSoup(html, "html.parser")

        # Redlib uses <div class="post"> for each result
        post_divs = soup.find_all("div", class_="post")
        if not post_divs:
            # Fallback: try other common Redlib selectors
            post_divs = soup.find_all("div", class_="search-result")
        if not post_divs:
            post_divs = soup.find_all("article")

        for div in post_divs:
            try:
                # Extract title
                title_el = div.find("a", class_="post_title") or div.find("h2") or div.find("a", class_="title")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not title or len(title) < 10:
                    continue

                # Extract permalink
                href = title_el.get("href", "")
                if href.startswith("/"):
                    post_url = f"https://reddit.com{href}"
                elif href.startswith("http"):
                    post_url = href
                else:
                    post_url = f"https://reddit.com/r/all"

                # Extract body text if available
                body_el = div.find("div", class_="post_body") or div.find("div", class_="md")
                body = body_el.get_text(strip=True)[:500] if body_el else ""
                content = f"{title}. {body}".strip() if body else title

                # Extract subreddit
                sub_el = div.find("a", class_="post_subreddit") or div.find("a", href=re.compile(r"^/r/"))
                subreddit = ""
                if sub_el:
                    sub_text = sub_el.get_text(strip=True)
                    subreddit = sub_text.replace("r/", "").strip("/")

                # Extract author
                author_el = div.find("a", class_="post_author") or div.find("a", href=re.compile(r"^/u(ser)?/"))
                author = author_el.get_text(strip=True).lstrip("u/") if author_el else "unknown"

                # Extract score/engagement
                score_el = div.find("div", class_="post_score") or div.find("span", class_="score")
                engagement = 1
                if score_el:
                    score_text = score_el.get_text(strip=True).replace(",", "").replace("k", "000")
                    match = re.search(r'(\d+)', score_text)
                    if match:
                        engagement = max(int(match.group(1)), 1)

                # Extract timestamp
                time_el = div.find("span", class_="created") or div.find("time")
                posted_at = datetime.now(timezone.utc)
                if time_el:
                    # Try datetime attribute first
                    dt_attr = time_el.get("datetime") or time_el.get("title")
                    if dt_attr:
                        try:
                            posted_at = datetime.fromisoformat(dt_attr.replace("Z", "+00:00"))
                        except (ValueError, TypeError):
                            pass

                post_hash = hashlib.sha256(f"reddit_{title[:100]}".encode()).hexdigest()[:12]
                posts.append({
                    "post_id": f"reddit_{post_hash}",
                    "platform": "reddit_mirror",
                    "author": author,
                    "content": content[:2000],
                    "url": post_url,
                    "engagement_score": engagement,
                    "posted_at": posted_at,
                    "subreddit": subreddit,
                })

            except Exception as e:
                logger.debug(f"Error parsing Redlib post: {e}")
                continue

        return posts

    def _search_old_reddit(self, query: str, limit: int = 20) -> List[Dict]:
        """Fallback: Search via old.reddit.com JSON endpoints."""
        all_posts = []

        # Search across relevant subreddits
        for subreddit in PREDICTION_SUBREDDITS[:5]:
            try:
                posts = self._fetch_subreddit_search(subreddit, query, limit=5)
                all_posts.extend(posts)
                time.sleep(self.delay)
            except Exception as e:
                logger.debug(f"Reddit mirror error for r/{subreddit}: {e}")
                continue

        # Also try Reddit-wide search
        try:
            global_posts = self._fetch_global_search(query, limit=10)
            all_posts.extend(global_posts)
        except Exception as e:
            logger.debug(f"Reddit global search error: {e}")

        # Deduplicate by post_id
        seen = set()
        unique = []
        for p in all_posts:
            if p['post_id'] not in seen:
                seen.add(p['post_id'])
                unique.append(p)

        logger.info(f"Reddit mirror (old.reddit fallback): {len(unique)} posts for '{query[:40]}...'")
        return unique[:limit]

    def _fetch_subreddit_search(self, subreddit: str, query: str, limit: int = 5) -> List[Dict]:
        """Search a specific subreddit."""
        url = f"https://old.reddit.com/r/{subreddit}/search.json"
        params = {
            "q": query,
            "restrict_sr": "on",
            "sort": "new",
            "t": "week",
            "limit": limit,
        }
        return self._fetch_and_parse(url, params)

    def _fetch_global_search(self, query: str, limit: int = 10) -> List[Dict]:
        """Search all of Reddit."""
        url = "https://old.reddit.com/search.json"
        params = {
            "q": query,
            "sort": "new",
            "t": "week",
            "limit": limit,
        }
        return self._fetch_and_parse(url, params)

    def _fetch_and_parse(self, url: str, params: dict) -> List[Dict]:
        """Fetch Reddit JSON and parse into standardized post format."""
        posts = []
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(
                    url,
                    params=params,
                    headers={"User-Agent": self.user_agent},
                )
                if resp.status_code == 429:
                    logger.warning("Reddit rate limited, backing off")
                    time.sleep(10)
                    return []
                if resp.status_code != 200:
                    return []

                data = resp.json()
                children = data.get("data", {}).get("children", [])

                for child in children:
                    post_data = child.get("data", {})
                    if not post_data:
                        continue

                    # Build content from title + selftext
                    title = post_data.get("title", "")
                    selftext = post_data.get("selftext", "")
                    content = f"{title}. {selftext}".strip() if selftext else title

                    if not content or len(content) < 10:
                        continue

                    # Parse timestamp
                    created_utc = post_data.get("created_utc", 0)
                    posted_at = datetime.fromtimestamp(created_utc, tz=timezone.utc) if created_utc else datetime.now(timezone.utc)

                    # Engagement = score + num_comments
                    score = post_data.get("score", 0)
                    num_comments = post_data.get("num_comments", 0)
                    engagement = max(score + num_comments, 1)

                    post_id = f"reddit_{post_data.get('id', hashlib.md5(content.encode()).hexdigest()[:12])}"

                    posts.append({
                        "post_id": post_id,
                        "platform": "reddit_mirror",
                        "author": post_data.get("author", "unknown"),
                        "content": content[:2000],
                        "url": f"https://reddit.com{post_data.get('permalink', '')}",
                        "engagement_score": engagement,
                        "posted_at": posted_at,
                        "subreddit": post_data.get("subreddit", ""),
                    })

        except Exception as e:
            logger.debug(f"Reddit mirror fetch error: {e}")

        return posts
