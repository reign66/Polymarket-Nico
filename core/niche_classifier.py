"""
core/niche_classifier.py — V2.1 Three-level niche classifier.

Level 1: Gamma API tags (free, instant)
Level 2: DB cache (free, instant)
Level 3: Haiku (rare, ~$0.001, result cached forever)
"""

import logging

logger = logging.getLogger(__name__)

# Mapping Gamma API tags → niche
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
    "science": "science", "technology": "tech", "ai": "tech",
    "climate": "science", "space": "science",
    # Special: "sports" tag needs sub-detection
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

# Valid niches for Haiku classification output
VALID_NICHES = [
    "nba", "f1", "crypto", "geopolitics", "politics",
    "golf", "soccer", "mma", "entertainment", "science", "tech", "other"
]


class NicheClassifier:
    def __init__(self, config, session=None, anthropic_client=None):
        self.config = config
        self.session = session
        self.anthropic_client = anthropic_client
        self.stats = {"gamma": 0, "cache": 0, "haiku": 0, "unknown": 0}

    def classify(self, market) -> str:
        """Classify a market. Returns niche string or None."""
        market_id = str(market.get('id') or market.get('market_id', ''))
        if not market_id:
            self.stats["unknown"] += 1
            return None

        # Level 1: Gamma API tags
        niche = self._classify_by_tags(market)
        if niche and niche != "_detect_sport":
            self.stats["gamma"] += 1
            if self.session:
                try:
                    from core.database import set_niche_cache
                    set_niche_cache(self.session, market_id, niche, "gamma_tag")
                except Exception:
                    pass
            return niche

        if niche == "_detect_sport":
            niche = self._detect_sport(market)
            if niche:
                self.stats["gamma"] += 1
                if self.session:
                    try:
                        from core.database import set_niche_cache
                        set_niche_cache(self.session, market_id, niche, "gamma_tag_sport")
                    except Exception:
                        pass
                return niche

        # Level 2: DB cache
        if self.session:
            try:
                from core.database import get_niche_cache
                cached = get_niche_cache(self.session, market_id)
                if cached:
                    self.stats["cache"] += 1
                    return cached
            except Exception:
                pass

        # Level 3: Haiku (if budget allows)
        if self.anthropic_client and self.session:
            try:
                from core.database import get_daily_api_calls
                max_classify = self.config.get('api_limits', {}).get(
                    'max_haiku_classify_per_day', 15)
                daily_classify = get_daily_api_calls(self.session, 'haiku_classify')
                if daily_classify < max_classify:
                    niche = self._classify_by_haiku(market, market_id)
                    if niche:
                        self.stats["haiku"] += 1
                        return niche
            except Exception as e:
                logger.debug(f"Haiku classify unavailable: {e}")

        self.stats["unknown"] += 1
        return None

    def _classify_by_tags(self, market) -> str:
        """Level 1: use Gamma API tags/category/groupSlugs."""
        tags = market.get('tags', [])
        category = market.get('category', '')
        group_slugs = market.get('groupSlugs', [])

        all_tags = []
        if isinstance(tags, list):
            all_tags.extend([str(t).lower().strip() for t in tags])
        elif isinstance(tags, str):
            all_tags.append(tags.lower().strip())
        if category:
            all_tags.append(str(category).lower().strip())
        if isinstance(group_slugs, list):
            all_tags.extend([str(s).lower().strip() for s in group_slugs])

        for tag in all_tags:
            if tag in GAMMA_TAG_MAP:
                return GAMMA_TAG_MAP[tag]
            for key, niche in GAMMA_TAG_MAP.items():
                if key in tag or tag in key:
                    return niche

        return None

    def _detect_sport(self, market) -> str:
        """When tag is generic 'sports', identify specific sport."""
        question = market.get('question', '').lower()
        description = market.get('description', '').lower()
        text = f"{question} {description}"

        best_sport = None
        best_score = 0
        for sport, keywords in SPORT_DETECT_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > best_score:
                best_score = score
                best_sport = sport

        return best_sport if best_score > 0 else "sports_other"

    def _classify_by_haiku(self, market, market_id) -> str:
        """Level 3: call Haiku for classification. Result is cached forever."""
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

            # Cache the result
            if self.session:
                from core.database import set_niche_cache, record_api_call
                set_niche_cache(self.session, market_id, niche, "haiku")
                record_api_call(
                    self.session,
                    model="haiku_classify",
                    tokens_in=30,
                    tokens_out=5,
                    cost_usd=0.000008,
                    market_id=market_id,
                    was_useful=(niche != "other"),
                )

            return niche if niche != "other" else None

        except Exception as e:
            logger.warning(f"Haiku classify failed for {market_id}: {e}")
            return None

    def classify_batch(self, markets) -> tuple:
        """
        Classify a list of MarketData objects.
        Sets market.niche on each object.
        Returns (classified_list, unclassified_count).
        """
        classified = []
        unclassified = 0
        for market in markets:
            # Convert MarketData to dict for classify()
            if hasattr(market, '__dict__'):
                market_dict = market.__dict__.copy()
                market_dict['id'] = market.market_id
            else:
                market_dict = market

            niche = self.classify(market_dict)
            if niche:
                market.niche = niche
                classified.append(market)
            else:
                unclassified += 1

        return classified, unclassified

    def get_stats_and_reset(self) -> dict:
        """Return cycle stats and reset counters."""
        stats = dict(self.stats)
        self.stats = {"gamma": 0, "cache": 0, "haiku": 0, "unknown": 0}
        return stats
