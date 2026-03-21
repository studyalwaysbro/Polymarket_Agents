"""X/Twitter mirror scraper using Playwright + stealth to bypass JS challenges.

Uses xcancel.com with playwright-stealth to pass proof-of-work bot detection.
Falls back to plain HTTP for any instances that don't require JS.
Reuses a single browser instance across searches to minimize overhead.
"""

import hashlib
import re
import time
import atexit
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Instances requiring Playwright (JS challenge)
PLAYWRIGHT_INSTANCES = [
    "https://xcancel.com",
]

# Instances that might work with plain HTTP (no JS challenge)
HTTP_INSTANCES = [
    "https://nitter.net",
]

# Browser launch args to reduce detection surface
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class XMirrorScraper:
    """
    Scrape public X/Twitter posts via xcancel.com using Playwright stealth.

    Uses a persistent browser instance for efficiency. Falls back to plain
    HTTP for instances without JS challenges.
    """

    def __init__(self):
        self.settings = get_settings()
        self.enabled = self.settings.enable_x_mirror and not self.settings.has_grok_credentials
        self.delay = self.settings.scraper_request_delay
        self.user_agent = self.settings.scraper_user_agent

        # Playwright browser state (lazy-initialized)
        self._playwright = None
        self._browser = None
        self._context = None
        self._rate_limited_until = 0  # timestamp when rate limit expires
        self._consecutive_429s = 0

        if self.enabled:
            logger.info("X Mirror Scraper initialized (Grok unavailable, using Nitter/XCancel fallback)")
        else:
            logger.debug("X Mirror Scraper disabled")

    def _ensure_browser(self) -> bool:
        """Lazy-initialize the Playwright browser. Returns True if ready."""
        if self._browser is not None:
            return True

        try:
            from playwright.sync_api import sync_playwright
            from playwright_stealth import stealth_sync
            self._stealth_sync = stealth_sync

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True, args=BROWSER_ARGS
            )
            self._context = self._browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            atexit.register(self._cleanup)
            logger.info("Playwright browser initialized for X mirror scraping")
            return True

        except ImportError:
            logger.warning("playwright or playwright-stealth not installed — X mirror disabled")
            self.enabled = False
            return False
        except Exception as e:
            logger.warning(f"Failed to launch Playwright browser: {e}")
            return False

    def _cleanup(self):
        """Clean up browser on exit."""
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def _parse_engagement(self, tweet_item) -> int:
        """Extract engagement score from tweet stats."""
        try:
            stats = tweet_item.find_all("span", class_="tweet-stat")
            total = 0
            for stat in stats:
                text = stat.get_text(strip=True).replace(",", "")
                match = re.search(r'(\d+)', text)
                if match:
                    total += int(match.group(1))
            return total if total > 0 else 1
        except Exception:
            return 1

    def _parse_author(self, tweet_item) -> str:
        """Extract author username from tweet."""
        try:
            username = tweet_item.find("a", class_="username")
            if username:
                return username.get_text(strip=True).lstrip("@")
            fullname = tweet_item.find("a", class_="fullname")
            if fullname:
                return fullname.get_text(strip=True)
        except Exception:
            pass
        return "unknown"

    def _parse_timestamp(self, tweet_item) -> datetime:
        """Extract timestamp from tweet date element."""
        try:
            date_link = tweet_item.find("span", class_="tweet-date")
            if date_link:
                a_tag = date_link.find("a")
                if a_tag and a_tag.get("title"):
                    title = a_tag["title"].replace("\u00b7", "").strip()
                    title = re.sub(r'\s+', ' ', title)
                    for fmt in ["%b %d, %Y %I:%M %p %Z", "%b %d, %Y %I:%M %p"]:
                        try:
                            return datetime.strptime(title, fmt).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
        except Exception:
            pass
        return datetime.now(timezone.utc)

    def _parse_url(self, tweet_item, instance: str) -> str:
        """Extract permalink for the tweet."""
        try:
            link = tweet_item.find("a", class_="tweet-link")
            if link and link.get("href"):
                return f"{instance}{link['href'].strip()}"
            date_link = tweet_item.find("span", class_="tweet-date")
            if date_link:
                a_tag = date_link.find("a")
                if a_tag and a_tag.get("href"):
                    return f"{instance}{a_tag['href'].strip()}"
        except Exception:
            pass
        return instance

    def _parse_tweets_html(self, html: str, instance: str, max_results: int) -> List[Dict]:
        """Parse Nitter/XCancel HTML into standardized post format."""
        soup = BeautifulSoup(html, "html.parser")
        tweet_items = soup.find_all("div", class_="timeline-item")

        if not tweet_items:
            # Fallback: bare tweet-content divs
            tweet_divs = soup.find_all("div", class_="tweet-content")
            if not tweet_divs:
                return []
            results = []
            for div in tweet_divs[:max_results]:
                text = div.get_text(strip=True)
                if not text or len(text) < 10:
                    continue
                post_hash = hashlib.sha256(f"xmirror_{text[:100]}".encode()).hexdigest()[:16]
                results.append({
                    "post_id": f"xmirror_{post_hash}",
                    "platform": "x_mirror",
                    "author": "unknown",
                    "content": text[:1000],
                    "posted_at": datetime.now(timezone.utc),
                    "url": instance,
                    "engagement_score": 1,
                })
            return results

        results = []
        for item in tweet_items[:max_results]:
            content_div = item.find("div", class_="tweet-content")
            if not content_div:
                continue
            text = content_div.get_text(strip=True)
            if not text or len(text) < 10:
                continue

            author = self._parse_author(item)
            engagement = self._parse_engagement(item)
            posted_at = self._parse_timestamp(item)
            tweet_url = self._parse_url(item, instance)

            post_hash = hashlib.sha256(f"xmirror_{author}_{text[:100]}".encode()).hexdigest()[:16]
            results.append({
                "post_id": f"xmirror_{post_hash}",
                "platform": "x_mirror",
                "author": author,
                "content": text[:1000],
                "posted_at": posted_at,
                "url": tweet_url,
                "engagement_score": engagement,
            })

        return results

    def _search_playwright(self, query: str, max_results: int) -> List[Dict]:
        """Search using Playwright with stealth for JS-gated instances."""
        if not self._ensure_browser():
            return []

        # Check rate limit backoff
        now = time.time()
        if now < self._rate_limited_until:
            remaining = int(self._rate_limited_until - now)
            logger.debug(f"X mirror rate limited, {remaining}s remaining")
            return []

        for instance in PLAYWRIGHT_INSTANCES:
            page = None
            try:
                time.sleep(self.delay)
                page = self._context.new_page()
                self._stealth_sync(page)

                url = f"{instance}/search?q={query}&f=tweets"
                page.goto(url, timeout=25000)

                # Wait for tweets to render (JS challenge + content load)
                try:
                    page.wait_for_selector(
                        "div.timeline-item, div.tweet-content",
                        timeout=20000,
                    )
                except Exception:
                    # Check if we're rate limited
                    title = page.title()
                    if "429" in title:
                        self._consecutive_429s += 1
                        # Exponential backoff: 60s, 120s, 240s, 480s
                        backoff = min(60 * (2 ** (self._consecutive_429s - 1)), 600)
                        self._rate_limited_until = time.time() + backoff
                        logger.warning(f"X mirror rate limited (429), backing off {backoff}s")
                        page.close()
                        return []
                    elif "Verifying" in title:
                        logger.debug(f"X mirror JS challenge not solved at {instance}")
                        page.close()
                        continue
                    else:
                        logger.debug(f"X mirror no tweets found at {instance}: {title}")
                        page.close()
                        continue

                # Success — reset 429 counter
                self._consecutive_429s = 0
                html = page.content()
                page.close()

                results = self._parse_tweets_html(html, instance, max_results)
                if results:
                    logger.info(f"X Mirror (Playwright): {len(results)} posts for '{query}' from {instance}")
                    return results

            except Exception as e:
                logger.debug(f"Playwright X mirror error at {instance}: {e}")
                if page:
                    try:
                        page.close()
                    except Exception:
                        pass
                continue

        return []

    def _search_http(self, query: str, max_results: int) -> List[Dict]:
        """Search using plain HTTP for instances without JS challenges."""
        headers = {"User-Agent": self.user_agent}

        for instance in HTTP_INSTANCES:
            try:
                time.sleep(self.delay)
                url = f"{instance}/search?q={query}&f=tweets"
                r = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)

                if r.status_code != 200:
                    logger.debug(f"X mirror {instance} returned {r.status_code}")
                    continue

                if len(r.content) < 1000:
                    logger.debug(f"X mirror {instance} returned empty/minimal response")
                    continue

                results = self._parse_tweets_html(r.text, instance, max_results)
                if results:
                    logger.info(f"X Mirror (HTTP): {len(results)} posts for '{query}' from {instance}")
                    return results

            except Exception as e:
                logger.debug(f"X mirror HTTP error at {instance}: {e}")
                continue

        return []

    def search_posts(self, query: str, max_results: int = 15) -> List[Dict]:
        """
        Search for X posts via mirror instances.

        Tries Playwright+stealth first (for xcancel.com), then falls back
        to plain HTTP for any non-JS-gated instances.
        """
        if not self.enabled:
            return []

        # Try Playwright instances first (xcancel)
        results = self._search_playwright(query, max_results)
        if results:
            return results

        # Fall back to HTTP instances
        results = self._search_http(query, max_results)
        if results:
            return results

        logger.warning(f"X mirror scraping failed for: {query} -- all instances unavailable")
        return []
