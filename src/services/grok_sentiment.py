"""Grok/xAI API integration for X/Twitter sentiment analysis."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path.home() / ".api-monitor"))

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)


class GrokSentiment:
    """
    Grok (xAI) API client for X/Twitter live sentiment analysis.

    Uses the OpenAI-compatible API at api.x.ai with native X search.
    Requires GROK_API_KEY in .env. Silently disabled if key is missing.
    """

    def __init__(self):
        self.settings = get_settings()
        self.enabled = self.settings.enable_grok and self.settings.has_grok_credentials
        self.client = None

        if self.enabled:
            try:
                from openai import OpenAI
                self.client = OpenAI(
                    api_key=self.settings.grok_api_key,
                    base_url="https://api.x.ai/v1",
                    timeout=30.0
                )
                logger.info("Grok X sentiment initialized")
            except ImportError:
                logger.warning("Grok requires 'openai' package: pip install openai")
                self.enabled = False
            except Exception as e:
                logger.warning(f"Grok init failed: {e}")
                self.enabled = False
        else:
            logger.debug("Grok X sentiment disabled (no API key)")

    def analyze_x_sentiment(self, query: str) -> List[Dict]:
        """
        Analyze X/Twitter sentiment about a topic using Grok's native X access.

        Args:
            query: Topic to search for on X

        Returns:
            List of post dicts with sentiment data
        """
        if not self.enabled or not self.client:
            return []

        try:
            response = self.client.chat.completions.create(
                model="grok-3",
                messages=[
                    {
                        "role": "system",
                        "content": "You analyze X/Twitter sentiment for prediction market research. "
                                   "Return structured JSON data about what people are saying."
                    },
                    {
                        "role": "user",
                        "content": f"""Search X/Twitter for recent posts about: {query}

Return a JSON object with:
- "posts": array of objects, each with:
  - "text": the post content (paraphrased)
  - "sentiment_score": float -1.0 to 1.0
  - "author_type": "individual" | "news" | "analyst"
- "overall_sentiment": float -1.0 to 1.0
- "volume": "low" | "medium" | "high"
- "key_narratives": list of 3-5 key themes
- "breaking_info": any info not yet in mainstream news, or null

Return ONLY valid JSON."""
                    }
                ],
            )

            try:
                from api_logger import log_openai_response
                log_openai_response("grok", response, project="polymarket-agents")
            except Exception:
                pass

            result_text = response.choices[0].message.content

            # Parse the JSON response
            try:
                data = json.loads(result_text)
            except json.JSONDecodeError:
                # Try to extract JSON from markdown
                if '```' in result_text:
                    json_part = result_text.split('```')[1]
                    if json_part.startswith('json'):
                        json_part = json_part[4:]
                    data = json.loads(json_part.strip())
                else:
                    logger.warning(f"Grok returned non-JSON response")
                    return []

            # Convert to standard post format
            results = []
            for i, post in enumerate(data.get("posts", [])[:15]):
                post_hash = hashlib.sha256(f"grok_{query}_{i}".encode()).hexdigest()[:16]
                results.append({
                    "post_id": f"grok_{post_hash}",
                    "platform": "x_grok",
                    "author": post.get("author_type", "unknown"),
                    "content": post.get("text", ""),
                    "posted_at": datetime.now(timezone.utc),
                    "url": "",
                    "engagement_score": 70,
                    "sentiment_score": post.get("sentiment_score", 0.0),
                    "grok_metadata": {
                        "overall_sentiment": data.get("overall_sentiment"),
                        "volume": data.get("volume"),
                        "key_narratives": data.get("key_narratives", []),
                        "breaking_info": data.get("breaking_info"),
                    }
                })

            logger.info(f"Grok: analyzed {len(results)} X posts for '{query}'")
            return results

        except Exception as e:
            logger.error(f"Grok analysis error: {e}")
            return []
