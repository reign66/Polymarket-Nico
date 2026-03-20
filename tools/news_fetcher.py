"""
tools/news_fetcher.py — GDELT news fetcher for market catalyst detection.

GDELT 2.0 Doc API: free, no key needed, ~15 min lag.
Used to boost model confidence when relevant news confirms a direction.
"""

import time
import re
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
HEADERS = {"User-Agent": "PolymarketBot/2.0 Research"}

# Keywords per niche to query GDELT
NICHE_QUERIES = {
    "geopolitics": "war OR ceasefire OR conflict OR sanctions OR invasion OR coup OR diplomacy",
    "politics": "election OR president OR congress OR senate OR vote OR poll OR democrat OR republican",
    "crypto": "bitcoin OR ethereum OR crypto OR SEC OR fed rate OR FOMC OR blockchain",
    "nba": "NBA OR basketball OR playoffs OR injury",
    "f1": "Formula 1 OR F1 OR grand prix OR championship",
    "golf": "golf OR PGA OR masters OR open championship",
}

# Tone score: GDELT returns avg tone (-10 bearish .. +10 bullish)
# We map to confidence boost
def _tone_to_boost(tone: float) -> float:
    """Map GDELT tone score to confidence adjustment (+/-)."""
    if tone > 3:
        return 0.08
    elif tone > 1:
        return 0.05
    elif tone < -3:
        return -0.05
    elif tone < -1:
        return -0.03
    return 0.0


class NewsFetcher:
    def __init__(self):
        self._cache = {}  # niche -> (timestamp, result)
        self._cache_ttl = 900  # 15 min

    def get_news_signal(self, niche: str, market_question: str = "") -> dict:
        """
        Fetch GDELT news for a niche.
        Returns dict: {articles: int, tone: float, boost: float, headlines: list}
        Boost is the confidence adjustment to apply to the math model.
        """
        now = time.time()
        cache_key = niche

        if cache_key in self._cache:
            cached_ts, cached_result = self._cache[cache_key]
            if now - cached_ts < self._cache_ttl:
                return cached_result

        result = self._fetch_gdelt(niche, market_question)
        self._cache[cache_key] = (now, result)
        return result

    def _fetch_gdelt(self, niche: str, market_question: str) -> dict:
        empty = {"articles": 0, "tone": 0.0, "boost": 0.0, "headlines": []}

        query = NICHE_QUERIES.get(niche)
        if not query:
            return empty

        # Add market-specific keywords from question (top 3 nouns)
        if market_question:
            words = [w for w in market_question.split() if len(w) > 4 and w[0].isupper()]
            if words:
                query = f"({query}) {words[0]}"

        try:
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": 10,
                "timespan": "6h",  # last 6 hours only
                "format": "json",
                "sort": "tonedesc",
            }
            resp = requests.get(GDELT_DOC_API, params=params, headers=HEADERS, timeout=10)
            if resp.status_code != 200:
                logger.debug(f"GDELT {niche}: HTTP {resp.status_code}")
                return empty

            data = resp.json()
            articles_raw = data.get("articles", [])
            if not articles_raw:
                return empty

            # Compute average tone across articles
            tones = []
            headlines = []
            for art in articles_raw[:10]:
                tone = art.get("tone")
                title = art.get("title", "")
                if tone is not None:
                    try:
                        tones.append(float(tone))
                    except (ValueError, TypeError):
                        pass
                if title:
                    headlines.append(title[:80])

            avg_tone = sum(tones) / len(tones) if tones else 0.0
            boost = _tone_to_boost(avg_tone)
            n = len(articles_raw)

            logger.info(
                f"GDELT [{niche}]: {n} articles, tone={avg_tone:.1f}, boost={boost:+.2f}"
            )

            return {
                "articles": n,
                "tone": round(avg_tone, 2),
                "boost": boost,
                "headlines": headlines[:3],
            }

        except Exception as e:
            logger.debug(f"GDELT fetch failed for {niche}: {e}")
            return empty


# Singleton
_fetcher = None

def get_news_fetcher() -> NewsFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = NewsFetcher()
    return _fetcher
