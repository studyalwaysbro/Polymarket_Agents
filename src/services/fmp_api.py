"""Financial Modeling Prep API for stock/commodity price data."""

import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path.home() / ".api-monitor"))

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import get_settings
from ..utils.logger import get_logger

logger = get_logger(__name__)

FMP_BASE_URL = "https://financialmodelingprep.com/api/v3"


class FMPAPI:
    """
    Financial Modeling Prep API client.

    Provides real-time prices for stocks, ETFs, commodities.
    Requires FMP_API_KEY in .env. Only used for financially-relevant contracts.
    """

    def __init__(self):
        self.settings = get_settings()
        self.enabled = self.settings.enable_fmp and self.settings.has_fmp_credentials
        self.session = self._create_session()

        if self.enabled:
            logger.info("FMP API initialized")
        else:
            logger.debug("FMP API disabled (no API key)")

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            backoff_factor=1
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.headers.update({
            "User-Agent": "PolymarketGapDetector/1.0",
        })
        return session

    def get_quotes(self, tickers: List[str]) -> List[Dict]:
        """
        Get real-time quotes for a list of tickers.

        Args:
            tickers: List of ticker symbols (e.g., ['AAPL', 'CL=F', 'GC=F'])

        Returns:
            List of quote dicts with price, change, volume
        """
        if not self.enabled:
            return []

        results = []
        for ticker in tickers[:5]:  # Limit to conserve API calls
            try:
                response = self.session.get(
                    f"{FMP_BASE_URL}/quote/{ticker}",
                    params={"apikey": self.settings.fmp_api_key},
                    timeout=10
                )
                try:
                    from api_logger import log_api_call
                    log_api_call("fmp", f"/quote/{ticker}", project="polymarket-agents")
                except Exception:
                    pass
                response.raise_for_status()
                data = response.json()

                if data and isinstance(data, list) and len(data) > 0:
                    q = data[0]
                    results.append({
                        "ticker": ticker,
                        "price": q.get("price"),
                        "change": q.get("change"),
                        "change_percent": q.get("changesPercentage"),
                        "volume": q.get("volume"),
                        "day_high": q.get("dayHigh"),
                        "day_low": q.get("dayLow"),
                        "market_cap": q.get("marketCap"),
                    })
                else:
                    results.append({"ticker": ticker, "price": None, "error": "no data"})

            except Exception as e:
                logger.debug(f"FMP quote failed for {ticker}: {e}")
                results.append({"ticker": ticker, "price": None, "error": str(e)})

        logger.info(f"FMP: fetched quotes for {len(results)} tickers")
        return results

    def get_market_movers(self) -> Dict:
        """
        Get top market gainers and losers.

        Returns:
            Dict with 'gainers' and 'losers' lists
        """
        if not self.enabled:
            return {"gainers": [], "losers": []}

        try:
            gainers_resp = self.session.get(
                f"{FMP_BASE_URL}/stock_market/gainers",
                params={"apikey": self.settings.fmp_api_key},
                timeout=10
            )
            losers_resp = self.session.get(
                f"{FMP_BASE_URL}/stock_market/losers",
                params={"apikey": self.settings.fmp_api_key},
                timeout=10
            )
            try:
                from api_logger import log_api_call
                log_api_call("fmp", "/stock_market/gainers", project="polymarket-agents")
                log_api_call("fmp", "/stock_market/losers", project="polymarket-agents")
            except Exception:
                pass

            gainers = gainers_resp.json()[:5] if gainers_resp.status_code == 200 else []
            losers = losers_resp.json()[:5] if losers_resp.status_code == 200 else []

            return {
                "gainers": [{"ticker": g.get("symbol"), "change_pct": g.get("changesPercentage")} for g in gainers],
                "losers": [{"ticker": l.get("symbol"), "change_pct": l.get("changesPercentage")} for l in losers],
            }
        except Exception as e:
            logger.error(f"FMP market movers error: {e}")
            return {"gainers": [], "losers": []}
