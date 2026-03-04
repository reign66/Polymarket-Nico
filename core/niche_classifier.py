"""
core/niche_classifier.py — V2.2 Four-level niche classifier.

Level 1  : Gamma API tags/category/groupSlugs (free, instant)
Level 1.5: Keyword matching on question + slug + description (free, instant)
Level 2  : DB cache (free, instant)
Level 3  : Haiku (max 15/day hard in-memory limit, result cached forever)
Level 4  : Fallback → "generic" (never drops a market)
"""

import logging
import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gamma API tag → niche mapping
# ---------------------------------------------------------------------------
GAMMA_TAG_MAP = {
    "basketball": "nba", "nba": "nba",
    "formula-1": "f1", "f1": "f1", "motorsport": "f1", "racing": "f1",
    "golf": "golf", "pga": "golf",
    "soccer": "soccer", "football": "soccer", "epl": "soccer",
    "premier-league": "soccer", "champions-league": "soccer",
    "mma": "mma", "ufc": "mma", "fighting": "mma",
    "tennis": "sports_other", "baseball": "sports_other",
    "hockey": "sports_other",
    "crypto": "crypto", "bitcoin": "crypto", "ethereum": "crypto",
    "defi": "crypto", "blockchain": "crypto", "web3": "crypto",
    "finance": "crypto",
    "politics": "politics", "elections": "politics",
    "us-politics": "politics", "us-elections": "politics",
    "world-politics": "politics",
    "geopolitics": "geopolitics", "conflict": "geopolitics",
    "war": "geopolitics", "international": "geopolitics",
    "entertainment": "entertainment", "culture": "entertainment",
    "pop-culture": "entertainment", "awards": "entertainment",
    "gaming": "entertainment", "video-games": "entertainment",
    "science": "science", "technology": "tech", "ai": "tech",
    "climate": "science", "space": "science",
    # Generic "sports" tag triggers sport-specific detection
    "sports": "_detect_sport",
}

# For sport detection when tag is generic "sports"
SPORT_DETECT_KEYWORDS = {
    "nba": ["nba", "basketball", "lakers", "celtics", "warriors", "bucks",
            "nuggets", "playoffs", "mvp", "finals", "eastern", "western"],
    "f1": ["formula", "verstappen", "hamilton", "leclerc", "grand prix",
           "constructors", "mclaren", "ferrari", "red bull"],
    "golf": ["golf", "masters", "pga", "open championship", "augusta",
             "rahm", "scheffler", "mcilroy", "dechambeau"],
    "soccer": ["premier league", "champions league", "la liga",
               "world cup", "messi", "ronaldo", "haaland", "mbappe"],
    "mma": ["ufc", "mma", "knockout", "submission", "dana white"],
}

# ---------------------------------------------------------------------------
# Level 1.5 — keyword matching (safety net, catches ~70-80% of markets)
# ---------------------------------------------------------------------------
KEYWORD_MAP = {
    "nba": [
        "nba", "basketball", "lakers", "celtics", "warriors", "bucks",
        "nuggets", "heat", "knicks", "suns", "clippers", "76ers",
        "playoffs", "nba finals", "nba mvp", "nba champion",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
        "xrp", "ripple", "doge", "dogecoin", "binance", "coinbase",
        "blockchain", "defi", "nft", "altcoin", "stablecoin",
        "100k", "200k", "1m", "halving", "satoshi",
    ],
    "f1": [
        "formula 1", "formula one", " f1 ", "grand prix", "verstappen",
        "hamilton", "leclerc", "norris", "ferrari", "mclaren",
        "red bull", "mercedes f1", "constructors championship",
    ],
    "politics": [
        "election", "president", "congress", "senate", "democrat",
        "republican", "vote", "ballot", "trump", "biden", "harris",
        "white house", "supreme court", "governor", "midterm",
        "polling", "polling average",
    ],
    "geopolitics": [
        "war", "ceasefire", "sanction", "conflict", "nato", "ukraine",
        "russia", "israel", "gaza", "taiwan", "iran", "north korea",
        "united nations", "coup", "invasion", "missile",
    ],
    "golf": [
        "golf", "masters", "pga tour", "open championship", "augusta",
        "scheffler", "mcilroy", "rahm", "dechambeau", "ryder cup",
        "the open", "us open golf",
    ],
    "soccer": [
        "soccer", "premier league", "champions league", "la liga",
        "world cup", "messi", "ronaldo", "haaland", "mbappe", "fifa",
        "bundesliga", "serie a", "ligue 1", "ballon d'or",
    ],
    "mma": [
        "ufc", " mma ", "knockout", "submission", "dana white",
        "bellator", "one championship", "ilia topuria", "jon jones",
    ],
    "entertainment": [
        "oscar", "grammy", "emmy", "golden globe", "celebrity",
        "box office", "tv show", "series finale", "album", "tour",
        "gta", "grand theft auto", "game of thrones", "marvel",
        "star wars", "netflix", "disney", "taylor swift", "beyonce",
    ],
    "science": [
        "nasa", "spacex", "rocket", "moon", "mars", "asteroid",
        "climate change", "earthquake", "hurricane", "pandemic",
        "vaccine", "fda approval", "clinical trial",
    ],
    "tech": [
        "apple", "google", "microsoft", "meta", "amazon", "tesla",
        "ipo", "stock market", "s&p 500", "fed rate", "interest rate",
        "openai", "chatgpt", "gpt-", "anthropic", "llm", "ai model",
        "nvidia", "semiconductor",
    ],
}

# Valid niches
VALID_NICHES = [
    "nba", "f1", "crypto", "geopolitics", "politics",
    "golf", "soccer", "mma", "entertainment", "science", "tech",
    "sports_other", "generic", "other",
]


class NicheClassifier:
    def __init__(self, config, session=None, anthropic_client=None):
        self.config = config
        self.session = session
        self.anthropic_client = anthropic_client
        self.stats = {"gamma": 0, "keyword": 0, "cache": 0, "haiku": 0, "generic": 0}

        # Hard in-memory daily counter — cannot be bypassed
        self._haiku_calls_today: int = 0
        self._haiku_calls_date: datetime.date | None = None
        # Killed when API returns a credit/auth error
        self._api_disabled: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, market: dict) -> str:
        """
        Classify a market dict. ALWAYS returns a niche string (never None).
        Worst case returns "generic" so the market still flows through the pipeline.
        """
        market_id = str(market.get('id') or market.get('market_id', ''))

        # Level 1 — Gamma tags
        niche = self._classify_by_tags(market)
        if niche == "_detect_sport":
            niche = self._detect_sport(market)
        if niche:
            self.stats["gamma"] += 1
            self._cache_niche(market_id, niche, "gamma_tag")
            return niche

        # Level 1.5 — keyword matching
        niche = self._classify_by_keywords(market)
        if niche:
            self.stats["keyword"] += 1
            self._cache_niche(market_id, niche, "keyword")
            return niche

        # Level 2 — DB cache
        niche = self._classify_by_cache(market_id)
        if niche:
            self.stats["cache"] += 1
            return niche

        # Level 3 — Haiku (hard budget limit)
        niche = self._classify_by_haiku(market, market_id)
        if niche:
            self.stats["haiku"] += 1
            return niche

        # Level 4 — Fallback: generic (never drop a market)
        self.stats["generic"] += 1
        return "generic"

    def classify_batch(self, markets) -> tuple:
        """
        Classify a list of MarketData objects. Sets market.niche on each.
        Returns (all_markets_with_niche, 0) — no market is ever dropped.
        """
        for market in markets:
            if hasattr(market, 'to_dict'):
                market_dict = market.to_dict()
                market_dict['id'] = market.market_id
            elif hasattr(market, '__dict__'):
                market_dict = market.__dict__.copy()
                market_dict['id'] = getattr(market, 'market_id', '')
            else:
                market_dict = market

            niche = self.classify(market_dict)
            market.niche = niche

        return markets, 0  # 0 unclassified — fallback handles all

    def get_stats_and_reset(self) -> dict:
        stats = dict(self.stats)
        self.stats = {"gamma": 0, "keyword": 0, "cache": 0, "haiku": 0, "generic": 0}
        return stats

    # ------------------------------------------------------------------
    # Private levels
    # ------------------------------------------------------------------

    def _classify_by_tags(self, market: dict) -> str | None:
        """Level 1: Gamma API tags / category / groupSlugs."""
        tags_raw = market.get('tags', [])
        category = market.get('category', '')
        group_slugs = market.get('groupSlugs', [])

        # Normalize tags — Gamma may return objects like {"id":1, "label":"NBA"}
        all_tags = []
        if isinstance(tags_raw, list):
            for t in tags_raw:
                if isinstance(t, dict):
                    # Try common label keys
                    for key in ('label', 'name', 'slug', 'value', 'tag'):
                        val = t.get(key)
                        if val:
                            all_tags.append(str(val).lower().strip())
                elif isinstance(t, str):
                    all_tags.append(t.lower().strip())
        elif isinstance(tags_raw, str) and tags_raw:
            all_tags.append(tags_raw.lower().strip())

        if category:
            all_tags.append(str(category).lower().strip())

        if isinstance(group_slugs, list):
            all_tags.extend(str(s).lower().strip() for s in group_slugs if s)
        elif isinstance(group_slugs, str) and group_slugs:
            all_tags.append(group_slugs.lower().strip())

        for tag in all_tags:
            if tag in GAMMA_TAG_MAP:
                return GAMMA_TAG_MAP[tag]
            for key, niche in GAMMA_TAG_MAP.items():
                if key in tag or tag in key:
                    return niche

        return None

    def _detect_sport(self, market: dict) -> str:
        question = market.get('question', '').lower()
        description = market.get('description', '').lower()
        text = f"{question} {description}"
        best_sport, best_score = None, 0
        for sport, keywords in SPORT_DETECT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score, best_sport = score, sport
        return best_sport if best_score > 0 else "sports_other"

    def _classify_by_keywords(self, market: dict) -> str | None:
        """Level 1.5: keyword matching on question + slug + description."""
        question = market.get('question', '').lower()
        slug = market.get('slug', '').lower().replace('-', ' ')
        description = (market.get('description', '') or '').lower()
        text = f"{question} {slug} {description}"

        best_niche, best_score = None, 0
        for niche, keywords in KEYWORD_MAP.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score, best_niche = score, niche

        return best_niche if best_score > 0 else None

    def _classify_by_cache(self, market_id: str) -> str | None:
        """Level 2: DB niche cache."""
        if not self.session or not market_id:
            return None
        try:
            from core.database import get_niche_cache
            return get_niche_cache(self.session, market_id)
        except Exception:
            return None

    def _cache_niche(self, market_id: str, niche: str, source: str):
        if not self.session or not market_id:
            return
        try:
            from core.database import set_niche_cache
            set_niche_cache(self.session, market_id, niche, source)
        except Exception:
            pass

    def _classify_by_haiku(self, market: dict, market_id: str) -> str | None:
        """Level 3: Haiku API call with hard in-memory daily limit."""
        if not self.anthropic_client:
            return None
        if self._api_disabled:
            return None

        # Reset counter at midnight
        today = datetime.date.today()
        if self._haiku_calls_date != today:
            self._haiku_calls_today = 0
            self._haiku_calls_date = today

        max_classify = self.config.get('api_limits', {}).get('max_haiku_classify_per_day', 15)
        if self._haiku_calls_today >= max_classify:
            logger.info(
                f"Haiku classify daily limit reached ({self._haiku_calls_today}/{max_classify}) "
                f"— skipping {market_id}"
            )
            return None

        # Increment BEFORE the API call
        self._haiku_calls_today += 1
        question = market.get('question', '')

        try:
            response = self.anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=10,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Classify this prediction market into one category.\n"
                        f"Market: {question}\n"
                        f"Categories: nba, f1, crypto, geopolitics, politics, "
                        f"golf, soccer, mma, entertainment, science, tech, other\n"
                        f"Reply with ONLY the category name."
                    )
                }]
            )
            niche = response.content[0].text.strip().lower()
            if niche not in VALID_NICHES:
                niche = "other"

            self._cache_niche(market_id, niche, "haiku")
            if self.session:
                try:
                    from core.database import record_api_call
                    record_api_call(
                        self.session,
                        model="haiku_classify",
                        tokens_in=30,
                        tokens_out=5,
                        cost_usd=0.000008,
                        market_id=market_id,
                        was_useful=(niche not in ("other", "generic")),
                    )
                except Exception:
                    pass

            return niche if niche not in ("other", "generic") else None

        except Exception as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ("credit", "balance", "402", "401", "insufficient")):
                logger.warning(f"Haiku API disabled due to credit/auth error: {e}")
                self._api_disabled = True
            else:
                logger.warning(f"Haiku classify failed for {market_id}: {e}")
            # Don't charge the counter if the call failed
            self._haiku_calls_today -= 1
            return None
