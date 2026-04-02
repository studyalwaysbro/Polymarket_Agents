"""X/Twitter mirror scraper using Playwright + stealth and HTTP fallbacks.

Uses multiple Nitter mirror instances with:
  - Session warmup (solve JS challenge once, reuse cookies)
  - Persistent single page (no page create/destroy per request)
  - Cycle time budget (hard cap to prevent blowing cron window)
  - Circuit breaker (skip after consecutive failures)
  - HTML structure canary (detect if mirrors change their DOM)

Instances (all use identical Nitter HTML structure):
  Playwright (JS challenge): xcancel.com, nitter.tiekoetter.com, nitter.privacyredirect.com
  HTTP-only (browser UA required): xcancel.com (also works via HTTP with proper UA)
"""

import hashlib
import json
import re
import time
import atexit
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

# ── Instance Configuration ────────────────────────────────────────────

# Playwright instances (JS challenge / Cloudflare / Anubis PoW)
PLAYWRIGHT_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.tiekoetter.com",
]

# HTTP-only instances (no JS needed, but require browser User-Agent)
HTTP_INSTANCES = [
    "https://xcancel.com",
]

# Browser launch args to reduce detection surface
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# ── Timeout & Budget Configuration ────────────────────────────────────

GOTO_TIMEOUT = 40000          # ms — page.goto timeout (generous for redirects)
SELECTOR_TIMEOUT = 30000      # ms — wait_for_selector timeout
WARMUP_TIMEOUT = 45000        # ms — warmup page.goto (extra generous)
CHALLENGE_WAIT_SEC = 8        # seconds to wait when JS challenge detected
CYCLE_TIME_BUDGET_SEC = 180   # 3 minutes max wall time on X mirror per cycle
CIRCUIT_BREAKER_THRESHOLD = 3 # consecutive genuine failures before skip
CIRCUIT_BREAKER_COOLDOWN_SEC = 900  # 15 min cooldown before retrying after breaker trips

# Rate limit state persistence
RATE_LIMIT_STATE_FILE = Path.home() / ".openclaw" / "x-mirror-state.json"


class XMirrorScraper:
    """
    Scrape public X/Twitter posts via Nitter mirror instances.

    Uses a persistent browser + single page for efficiency. Session warmup
    solves JS challenges once, then reuses cookies for all subsequent requests.
    Hard cycle time budget prevents runaway scraping.
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
        self._page = None  # persistent page — reused across queries
        self._stealth_sync = None
        self._session_warm = False

        # Rate limiting (persisted across process restarts)
        self._rate_limited_until = 0
        self._consecutive_429s = 0
        self._breaker_tripped_at = 0
        self._load_rate_limit_state()

        # Circuit breaker
        self._consecutive_failures = 0

        # Cycle time budget
        self._cycle_start = 0
        self._cycle_time_used = 0

        # Run stats
        self._run_stats = {
            "queries_attempted": 0,
            "queries_succeeded": 0,
            "challenge_retries": 0,
            "timeouts": 0,
            "rate_limits": 0,
            "budget_skips": 0,
        }

        if self.enabled:
            logger.info("X Mirror Scraper initialized (Grok unavailable, using Nitter mirror fallback)")
        else:
            logger.debug("X Mirror Scraper disabled")

    # ── Rate limit persistence ────────────────────────────────────────

    def _load_rate_limit_state(self):
        """Load rate limit and circuit breaker state from disk (survives process restarts)."""
        try:
            if RATE_LIMIT_STATE_FILE.exists():
                data = json.loads(RATE_LIMIT_STATE_FILE.read_text(encoding="utf-8"))
                self._rate_limited_until = data.get("rate_limited_until", 0)
                self._consecutive_429s = data.get("consecutive_429s", 0)
                self._breaker_tripped_at = data.get("breaker_tripped_at", 0)
        except Exception:
            pass

    def _save_rate_limit_state(self):
        """Persist rate limit and circuit breaker state to disk."""
        try:
            RATE_LIMIT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "rate_limited_until": self._rate_limited_until,
                "consecutive_429s": self._consecutive_429s,
                "breaker_tripped_at": self._breaker_tripped_at,
                "updated": datetime.now().isoformat(),
            }
            RATE_LIMIT_STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ── Browser management ────────────────────────────────────────────

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

    def _ensure_page(self) -> bool:
        """Ensure a persistent page exists. Creates one if needed."""
        if self._page is not None:
            try:
                # Quick check — page still alive
                self._page.title()
                return True
            except Exception:
                self._page = None

        if not self._ensure_browser():
            return False

        try:
            self._page = self._context.new_page()
            self._stealth_sync(self._page)
            return True
        except Exception as e:
            logger.warning(f"Failed to create Playwright page: {e}")
            return False

    def _cleanup(self):
        """Clean up browser on exit."""
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    # ── Session warmup ────────────────────────────────────────────────

    def _warmup_session(self) -> bool:
        """Hit xcancel.com homepage to solve JS challenge and establish cookies.
        Called once per cycle. If warmup fails, X mirror is skipped entirely."""
        if self._session_warm:
            return True
        if not self._ensure_page():
            return False

        instance = PLAYWRIGHT_INSTANCES[0]  # warmup against primary instance
        try:
            start = time.time()
            self._page.goto(f"{instance}/", timeout=WARMUP_TIMEOUT)

            # Wait for page to resolve (may go through JS challenge)
            for attempt in range(3):
                try:
                    self._page.wait_for_selector("a[href]", timeout=15000)
                    break
                except Exception:
                    title = self._page.title()
                    if "Verifying" in title or "challenge" in title.lower():
                        logger.info(f"X mirror warmup: JS challenge in progress (attempt {attempt + 1}/3)")
                        self._run_stats["challenge_retries"] += 1
                        time.sleep(CHALLENGE_WAIT_SEC)
                    else:
                        break

            elapsed = time.time() - start
            title = self._page.title()

            if "Verifying" in title or "challenge" in title.lower():
                logger.warning(f"X mirror warmup FAILED: stuck on JS challenge after {elapsed:.1f}s")
                return False

            self._session_warm = True
            logger.info(f"X mirror warmup OK in {elapsed:.1f}s")
            return True

        except Exception as e:
            logger.warning(f"X mirror warmup failed: {e}")
            return False

    # ── Cycle time budget ─────────────────────────────────────────────

    def _budget_remaining(self) -> float:
        """Seconds remaining in the cycle time budget for X mirror."""
        if self._cycle_start == 0:
            return CYCLE_TIME_BUDGET_SEC
        elapsed = time.time() - self._cycle_start
        return max(0, CYCLE_TIME_BUDGET_SEC - elapsed)

    def _budget_exhausted(self) -> bool:
        """Check if cycle time budget is spent."""
        return self._budget_remaining() <= 0

    # ── HTML parsing ──────────────────────────────────────────────────

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
                # HTML structure canary — detect if mirror changed its DOM
                if len(html) > 5000:
                    logger.warning(
                        f"X mirror HTML canary: got {len(html)} bytes but no timeline-item or "
                        f"tweet-content selectors. DOM structure may have changed at {instance}."
                    )
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

    # ── Playwright search ─────────────────────────────────────────────

    def _search_playwright(self, query: str, max_results: int) -> List[Dict]:
        """Search using Playwright with stealth for JS-gated instances.
        Uses persistent page with session warmup."""

        # Budget check
        if self._budget_exhausted():
            self._run_stats["budget_skips"] += 1
            return []

        # Circuit breaker
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            return []

        # Rate limit check
        now = time.time()
        if now < self._rate_limited_until:
            remaining = int(self._rate_limited_until - now)
            logger.debug(f"X mirror rate limited, {remaining}s remaining")
            self._run_stats["rate_limits"] += 1
            return []

        # Warmup session if needed (solves JS challenge once)
        if not self._warmup_session():
            logger.warning("X mirror warmup failed — skipping Playwright instances for this cycle")
            self._consecutive_failures = CIRCUIT_BREAKER_THRESHOLD  # trip breaker
            if self._breaker_tripped_at == 0:
                self._breaker_tripped_at = time.time()
                self._save_rate_limit_state()
                logger.warning(f"X mirror circuit breaker TRIPPED (warmup failed). Skipping for {CIRCUIT_BREAKER_COOLDOWN_SEC}s.")
            return []

        if not self._ensure_page():
            return []

        for instance in PLAYWRIGHT_INSTANCES:
            if self._budget_exhausted():
                self._run_stats["budget_skips"] += 1
                return []

            try:
                time.sleep(self.delay)
                url = f"{instance}/search?q={query}&f=tweets"
                req_start = time.time()
                self._page.goto(url, timeout=GOTO_TIMEOUT)

                # Wait for tweets to render
                try:
                    self._page.wait_for_selector(
                        "div.timeline-item, div.tweet-content",
                        timeout=SELECTOR_TIMEOUT,
                    )
                except Exception:
                    title = self._page.title()
                    if "429" in title:
                        self._consecutive_429s += 1
                        backoff = min(60 * (2 ** (self._consecutive_429s - 1)), 600)
                        self._rate_limited_until = time.time() + backoff
                        self._save_rate_limit_state()
                        logger.warning(f"X mirror rate limited (429), backing off {backoff}s")
                        self._run_stats["rate_limits"] += 1
                        return []
                    elif "Verifying" in title or "challenge" in title.lower():
                        # JS challenge re-triggered — wait once and try to continue
                        self._run_stats["challenge_retries"] += 1
                        logger.info(f"X mirror JS challenge at {instance} during search, waiting {CHALLENGE_WAIT_SEC}s")
                        time.sleep(CHALLENGE_WAIT_SEC)
                        # Don't retry this query — move to next instance
                        continue
                    else:
                        self._run_stats["timeouts"] += 1
                        self._consecutive_failures += 1
                        logger.debug(f"X mirror no tweets at {instance}: {title}")
                        continue

                # Success — reset counters
                self._consecutive_429s = 0
                self._consecutive_failures = 0
                html = self._page.content()

                results = self._parse_tweets_html(html, instance, max_results)
                if results:
                    logger.info(f"X Mirror (Playwright): {len(results)} posts for '{query}' from {instance}")
                    return results

            except Exception as e:
                self._consecutive_failures += 1
                self._run_stats["timeouts"] += 1
                logger.debug(f"Playwright X mirror error at {instance}: {e}")
                # If page crashed, recreate it
                try:
                    self._page.title()
                except Exception:
                    self._page = None
                    self._session_warm = False
                continue

        return []

    # ── HTTP search ───────────────────────────────────────────────────

    def _search_http(self, query: str, max_results: int) -> List[Dict]:
        """Search using plain HTTP for instances that work without JS.
        xcancel.com works via HTTP with a proper browser User-Agent."""

        if self._budget_exhausted():
            self._run_stats["budget_skips"] += 1
            return []

        headers = {"User-Agent": USER_AGENT}

        for instance in HTTP_INSTANCES:
            if self._budget_exhausted():
                self._run_stats["budget_skips"] += 1
                return []

            try:
                time.sleep(self.delay)
                url = f"{instance}/search?q={query}&f=tweets"
                r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)

                if r.status_code == 403:
                    logger.debug(f"X mirror {instance} returned 403 (may need different UA)")
                    continue
                if r.status_code != 200:
                    logger.debug(f"X mirror {instance} returned {r.status_code}")
                    continue
                if len(r.content) < 1000:
                    logger.debug(f"X mirror {instance} returned empty/minimal response")
                    continue

                results = self._parse_tweets_html(r.text, instance, max_results)
                if results:
                    logger.info(f"X Mirror (HTTP): {len(results)} posts for '{query}' from {instance}")
                    self._consecutive_failures = 0
                    return results

            except Exception as e:
                logger.debug(f"X mirror HTTP error at {instance}: {e}")
                continue

        return []

    # ── Public API ────────────────────────────────────────────────────

    def reset_run_stats(self):
        """Call before starting a collection cycle. Resets per-cycle state.
        Respects persistent circuit breaker — if breaker tripped recently,
        stays tripped until cooldown expires (avoids burning 50s/contract)."""
        self._run_stats = {
            "queries_attempted": 0,
            "queries_succeeded": 0,
            "challenge_retries": 0,
            "timeouts": 0,
            "rate_limits": 0,
            "budget_skips": 0,
        }

        # Check persistent circuit breaker before resetting failures
        now = time.time()
        if self._breaker_tripped_at > 0:
            elapsed = now - self._breaker_tripped_at
            if elapsed < CIRCUIT_BREAKER_COOLDOWN_SEC:
                remaining = int(CIRCUIT_BREAKER_COOLDOWN_SEC - elapsed)
                logger.info(
                    f"X mirror circuit breaker still active ({remaining}s remaining). "
                    f"Skipping this cycle to avoid timeout waste."
                )
                self._consecutive_failures = CIRCUIT_BREAKER_THRESHOLD
            else:
                logger.info("X mirror circuit breaker cooldown expired — retrying this cycle")
                self._consecutive_failures = 0
                self._breaker_tripped_at = 0
                self._save_rate_limit_state()
        else:
            self._consecutive_failures = 0

        self._cycle_start = time.time()
        self._session_warm = False  # fresh warmup each cycle

    def log_run_summary(self):
        """Call after completing a collection cycle. Logs performance stats."""
        s = self._run_stats
        budget_used = time.time() - self._cycle_start if self._cycle_start else 0
        logger.info(
            f"X mirror run summary: {s['queries_succeeded']}/{s['queries_attempted']} succeeded, "
            f"{s['challenge_retries']} challenge retries, {s['timeouts']} timeouts, "
            f"{s['rate_limits']} rate limits, {s['budget_skips']} budget skips, "
            f"{budget_used:.0f}s/{CYCLE_TIME_BUDGET_SEC}s budget used"
        )

    def search_posts(self, query: str, max_results: int = 15) -> List[Dict]:
        """
        Search for X posts via mirror instances.

        Tries Playwright+stealth first (for JS-gated instances), then falls
        back to plain HTTP. Respects cycle time budget and circuit breaker.
        """
        if not self.enabled:
            return []

        self._run_stats["queries_attempted"] += 1

        # Budget check before doing any work
        if self._budget_exhausted():
            self._run_stats["budget_skips"] += 1
            return []

        # Circuit breaker check
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            return []

        # Try Playwright instances first (xcancel + other JS-gated mirrors)
        results = self._search_playwright(query, max_results)
        if results:
            self._run_stats["queries_succeeded"] += 1
            # Success clears persistent breaker
            if self._breaker_tripped_at > 0:
                self._breaker_tripped_at = 0
                self._save_rate_limit_state()
            return results

        # Fall back to HTTP instances
        results = self._search_http(query, max_results)
        if results:
            self._run_stats["queries_succeeded"] += 1
            if self._breaker_tripped_at > 0:
                self._breaker_tripped_at = 0
                self._save_rate_limit_state()
            return results

        # If we just hit the threshold, persist the breaker
        if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD and self._breaker_tripped_at == 0:
            self._breaker_tripped_at = time.time()
            self._save_rate_limit_state()
            logger.warning(
                f"X mirror circuit breaker TRIPPED — all instances down. "
                f"Skipping for {CIRCUIT_BREAKER_COOLDOWN_SEC}s."
            )

        logger.debug(f"X mirror: no results for '{query}'")
        return []
