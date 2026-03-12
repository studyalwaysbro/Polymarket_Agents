"""RSS news feed scraper - completely free, no API keys needed."""

import feedparser
import socket
import time
from datetime import datetime, timedelta
from typing import List, Dict
from ratelimit import limits, sleep_and_retry

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


class RSSNewsScraper:
    """
    RSS feed scraper for news articles.

    Uses public RSS feeds from major news outlets (100% free, no API keys needed).
    """

    def __init__(self):
        """Initialize RSS news scraper."""
        self.settings = get_settings()

        # Free RSS feeds from major news outlets
        self.feeds = {
            'reuters': 'https://www.reutersagency.com/feed/',
            'bbc': 'http://feeds.bbci.co.uk/news/rss.xml',
            'cnn': 'http://rss.cnn.com/rss/cnn_topstories.rss',
            'google_news': 'https://news.google.com/rss',
            'associated_press': 'https://apnews.com/apf-topnews',
        }

        logger.info("RSS News Scraper initialized with free news feeds")

    @sleep_and_retry
    @limits(calls=10, period=60)  # 10 feeds per minute to be respectful
    def _fetch_feed(self, url: str) -> feedparser.FeedParserDict:
        """
        Fetch RSS feed with rate limiting and timeout.

        Args:
            url: RSS feed URL

        Returns:
            Parsed feed
        """
        old_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(10)  # 10 second timeout per feed
            feed = feedparser.parse(url)
            return feed
        except Exception as e:
            logger.error(f"Error fetching RSS feed {url}: {e}")
            return feedparser.FeedParserDict()
        finally:
            socket.setdefaulttimeout(old_timeout)

    def search_news(self, keywords: List[str], hours_back: int = 24) -> List[Dict]:
        """
        Search news articles across RSS feeds for keywords.

        Args:
            keywords: List of keywords to search for
            hours_back: How many hours back to search (default 24)

        Returns:
            List of matching articles
        """
        if not keywords:
            logger.debug("No keywords provided, skipping RSS search")
            return []

        articles = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)

        # Convert keywords to lowercase for case-insensitive matching
        keywords_lower = [k.lower() for k in keywords]

        logger.info(f"Searching RSS feeds for keywords: {keywords}")

        for source_name, feed_url in self.feeds.items():
            try:
                feed = self._fetch_feed(feed_url)

                if not feed or not hasattr(feed, 'entries'):
                    logger.debug(f"No entries in feed: {source_name}")
                    continue

                for entry in feed.entries:
                    # Check if article is recent
                    published = None
                    if hasattr(entry, 'published_parsed'):
                        published = datetime(*entry.published_parsed[:6])
                    elif hasattr(entry, 'updated_parsed'):
                        published = datetime(*entry.updated_parsed[:6])

                    # Skip old articles
                    if published and published < cutoff_time:
                        continue

                    # Get article text
                    title = entry.get('title', '')
                    summary = entry.get('summary', '') or entry.get('description', '')
                    content = f"{title} {summary}".lower()

                    # Check if any keyword matches
                    if any(keyword in content for keyword in keywords_lower):
                        articles.append({
                            'source': source_name,
                            'title': title,
                            'content': summary,
                            'url': entry.get('link', ''),
                            'published_at': published or datetime.utcnow(),
                            'author': entry.get('author', 'Unknown')
                        })

                        logger.debug(f"Found matching article: {title[:50]}...")

                logger.debug(f"Searched {source_name}: {len(articles)} matches so far")

            except Exception as e:
                logger.error(f"Error searching {source_name}: {e}")
                continue

        logger.info(f"Found {len(articles)} articles matching keywords")
        return articles

    def get_recent_news(self, hours_back: int = 24, limit: int = 50) -> List[Dict]:
        """
        Get recent news articles from all feeds.

        Args:
            hours_back: How many hours back to fetch
            limit: Maximum number of articles

        Returns:
            List of recent articles
        """
        articles = []
        cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)

        logger.info(f"Fetching recent news from {len(self.feeds)} sources")

        for source_name, feed_url in self.feeds.items():
            try:
                feed = self._fetch_feed(feed_url)

                if not feed or not hasattr(feed, 'entries'):
                    continue

                for entry in feed.entries:
                    # Check if article is recent
                    published = None
                    if hasattr(entry, 'published_parsed'):
                        published = datetime(*entry.published_parsed[:6])
                    elif hasattr(entry, 'updated_parsed'):
                        published = datetime(*entry.updated_parsed[:6])

                    if published and published < cutoff_time:
                        continue

                    articles.append({
                        'source': source_name,
                        'title': entry.get('title', ''),
                        'content': entry.get('summary', '') or entry.get('description', ''),
                        'url': entry.get('link', ''),
                        'published_at': published or datetime.utcnow(),
                        'author': entry.get('author', 'Unknown')
                    })

                    if len(articles) >= limit:
                        break

                if len(articles) >= limit:
                    break

            except Exception as e:
                logger.error(f"Error fetching from {source_name}: {e}")
                continue

        logger.info(f"Fetched {len(articles)} recent articles")
        return articles[:limit]
