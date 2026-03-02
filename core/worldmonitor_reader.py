import asyncio
import aiohttp
import time
import logging
import concurrent.futures
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class WorldMonitorReader:
    BASE_URLS = {
        'tech': 'https://tech.worldmonitor.app',
        'finance': 'https://finance.worldmonitor.app',
        'general': 'https://worldmonitor.app',
    }
    GAMMA_API = 'https://gamma-api.polymarket.com'
    CACHE_TTL = 600  # 10 minutes
    MAX_RETRIES = 3
    HEADERS = {'User-Agent': 'PolymarketBot/1.0 Research'}

    def __init__(self):
        self._cache: Dict[str, Dict] = {}  # {url: {'data': ..., 'ts': float}}
        self._failure_count: int = 0

    # ------------------------------------------------------------------
    # Sync runner helper
    # ------------------------------------------------------------------

    def _run_sync(self, coro):
        """Run an async coroutine from a synchronous context."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(asyncio.run, coro).result(timeout=30)
            return loop.run_until_complete(coro)
        except RuntimeError:
            return asyncio.run(coro)

    # ------------------------------------------------------------------
    # Core async fetch
    # ------------------------------------------------------------------

    async def _fetch(self, url: str, params: Optional[Dict] = None):
        """
        Async GET with:
        - 10s timeout
        - Up to 3 retries with exponential backoff (1s, 2s, 4s)
        - 429/403 -> wait 60s then retry
        - On all failures -> log warning, increment failure counter, return []
        - Results cached by URL for CACHE_TTL seconds
        """
        cache_key = url + str(sorted(params.items()) if params else '')

        # Return cached result if still fresh
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached['ts']) < self.CACHE_TTL:
            logger.debug("Cache hit for %s", url)
            return cached['data']

        timeout = aiohttp.ClientTimeout(total=10)
        last_exception = None

        for attempt in range(self.MAX_RETRIES):
            try:
                async with aiohttp.ClientSession(headers=self.HEADERS, timeout=timeout) as session:
                    async with session.get(url, params=params) as response:
                        if response.status in (429, 403):
                            logger.warning(
                                "Rate-limited or forbidden (HTTP %s) on %s — waiting 60s",
                                response.status, url
                            )
                            await asyncio.sleep(60)
                            continue  # retry without counting as a backoff step

                        response.raise_for_status()

                        try:
                            data = await response.json(content_type=None)
                        except Exception:
                            text = await response.text()
                            logger.warning("Non-JSON response from %s: %s", url, text[:200])
                            data = []

                        # Store in cache
                        self._cache[cache_key] = {'data': data, 'ts': time.time()}
                        return data

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exception = exc
                backoff = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Attempt %d/%d failed for %s (%s) — retrying in %ds",
                    attempt + 1, self.MAX_RETRIES, url, exc, backoff
                )
                await asyncio.sleep(backoff)

            except Exception as exc:
                last_exception = exc
                logger.warning("Unexpected error fetching %s: %s", url, exc)
                break

        # All retries exhausted
        self._failure_count += 1
        logger.warning(
            "All retries failed for %s (total failures: %d). Last error: %s",
            url, self._failure_count, last_exception
        )
        return []

    # ------------------------------------------------------------------
    # Async public methods
    # ------------------------------------------------------------------

    async def get_tech_news(self) -> list:
        """Fetch technology news digest from WorldMonitor tech endpoint."""
        url = f"{self.BASE_URLS['tech']}/api/news-digest"
        try:
            data = await self._fetch(url)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("get_tech_news failed: %s", exc)
            return []

    async def get_finance_news(self) -> list:
        """Fetch finance news digest from WorldMonitor finance endpoint."""
        url = f"{self.BASE_URLS['finance']}/api/news-digest"
        try:
            data = await self._fetch(url)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("get_finance_news failed: %s", exc)
            return []

    async def get_geopolitics_news(self) -> list:
        """Fetch geopolitics news digest from WorldMonitor general endpoint."""
        url = f"{self.BASE_URLS['general']}/api/news-digest"
        try:
            # Attempt with category filter first; fall back without it
            data = await self._fetch(url, params={'category': 'geopolitics'})
            if not isinstance(data, list) or not data:
                data = await self._fetch(url)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("get_geopolitics_news failed: %s", exc)
            return []

    async def get_trending_keywords(self) -> list:
        """Fetch trending keywords from WorldMonitor general endpoint."""
        url = f"{self.BASE_URLS['general']}/api/trending-keywords"
        try:
            data = await self._fetch(url)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("get_trending_keywords failed: %s", exc)
            return []

    async def get_cii_scores(self) -> list:
        """Fetch CII (Confidence/Impact/Importance) scores from WorldMonitor."""
        url = f"{self.BASE_URLS['general']}/api/cii-scores"
        try:
            data = await self._fetch(url)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("get_cii_scores failed: %s", exc)
            return []

    async def get_polymarket_markets(self, keywords: Optional[List[str]] = None) -> list:
        """
        Fetch prediction market data.
        Primary:  WorldMonitor general /api/prediction-markets
        Fallback: Gamma API /markets (when WorldMonitor has failed 3+ times)
        """
        # ------ Primary: WorldMonitor ------
        if self._failure_count < self.MAX_RETRIES:
            url = f"{self.BASE_URLS['general']}/api/prediction-markets"
            params = {'keywords': ','.join(keywords)} if keywords else None
            try:
                data = await self._fetch(url, params=params)
                if isinstance(data, list) and data:
                    return data
            except Exception as exc:
                logger.warning("WorldMonitor prediction-markets failed: %s", exc)

        # ------ Fallback: Gamma API ------
        logger.info(
            "WorldMonitor failure count=%d — falling back to Gamma API",
            self._failure_count
        )
        gamma_url = f"{self.GAMMA_API}/markets"
        gamma_params: Dict = {'active': 'true', 'limit': 100}
        if keywords:
            gamma_params['tag_slug'] = ','.join(keywords)
        try:
            data = await self._fetch(gamma_url, params=gamma_params)
            if isinstance(data, list):
                return data
            # Gamma sometimes wraps results
            if isinstance(data, dict):
                for key in ('markets', 'data', 'results'):
                    if key in data and isinstance(data[key], list):
                        return data[key]
        except Exception as exc:
            logger.warning("Gamma API fallback also failed: %s", exc)

        return []

    async def get_macro_signals(self) -> list:
        """Fetch macro market signals from WorldMonitor finance endpoint."""
        url = f"{self.BASE_URLS['finance']}/api/market-signals"
        try:
            data = await self._fetch(url)
            return data if isinstance(data, list) else []
        except Exception as exc:
            logger.warning("get_macro_signals failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Sync wrappers
    # ------------------------------------------------------------------

    def sync_get_tech_news(self) -> list:
        return self._run_sync(self.get_tech_news())

    def sync_get_finance_news(self) -> list:
        return self._run_sync(self.get_finance_news())

    def sync_get_geopolitics_news(self) -> list:
        return self._run_sync(self.get_geopolitics_news())

    def sync_get_trending_keywords(self) -> list:
        return self._run_sync(self.get_trending_keywords())

    def sync_get_cii_scores(self) -> list:
        return self._run_sync(self.get_cii_scores())

    def sync_get_polymarket_markets(self, keywords: Optional[List[str]] = None) -> list:
        return self._run_sync(self.get_polymarket_markets(keywords=keywords))

    def sync_get_macro_signals(self) -> list:
        return self._run_sync(self.get_macro_signals())
