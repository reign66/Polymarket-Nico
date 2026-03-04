import time
import json
import hashlib
import logging
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

GAMMA_API = 'https://gamma-api.polymarket.com'
CLOB_API = 'https://clob.polymarket.com'
HEADERS = {'User-Agent': 'PolymarketBot/2.0 Research'}


class MarketData:
    """Data class for market info."""
    def __init__(self, **kwargs):
        self.market_id = kwargs.get('market_id', '')
        self.question = kwargs.get('question', '')
        self.slug = kwargs.get('slug', '')
        self.description = kwargs.get('description', '')
        self.yes_price = kwargs.get('yes_price', 0.5)
        self.no_price = kwargs.get('no_price', 0.5)
        self.volume = kwargs.get('volume', 0)
        self.liquidity = kwargs.get('liquidity', 0)
        self.end_date = kwargs.get('end_date', '')
        self.spread = kwargs.get('spread', 0)
        self.niche = kwargs.get('niche', None)
        self.tokens = kwargs.get('tokens', [])
        # Gamma API metadata for classifier
        self.tags = kwargs.get('tags', [])
        self.category = kwargs.get('category', '')
        self.group_slugs = kwargs.get('groupSlugs', [])

    def to_dict(self):
        return {
            'market_id': self.market_id,
            'question': self.question,
            'slug': self.slug,
            'description': self.description,
            'yes_price': self.yes_price,
            'no_price': self.no_price,
            'volume': self.volume,
            'liquidity': self.liquidity,
            'end_date': self.end_date,
            'spread': self.spread,
            'niche': self.niche,
            'tags': self.tags,
            'category': self.category,
            'groupSlugs': self.group_slugs,
        }


class MarketFetcher:
    def __init__(self, db_session):
        self.session = db_session
        self._cache: Dict[str, tuple] = {}  # url_key -> (timestamp, data)
        self._full_fetch_cache: Optional[tuple] = None  # (timestamp, markets)
        self._full_fetch_ttl = 600  # 10 min

    def _make_cache_key(self, url: str, params: Optional[dict] = None) -> str:
        raw = url + (json.dumps(params, sort_keys=True) if params else '')
        return hashlib.md5(raw.encode()).hexdigest()

    def _request(self, url: str, params: Optional[dict] = None, cache_seconds: int = 600):
        cache_key = self._make_cache_key(url, params)
        now = time.time()

        if cache_key in self._cache:
            cached_ts, cached_data = self._cache[cache_key]
            if now - cached_ts < cache_seconds:
                return cached_data

        delays = [1, 2, 4]
        for attempt, delay in enumerate(delays):
            try:
                response = requests.get(url, params=params, headers=HEADERS, timeout=15)
                response.raise_for_status()
                data = response.json()
                self._cache[cache_key] = (now, data)
                return data
            except requests.RequestException as e:
                logger.warning(f"Request failed (attempt {attempt+1}/3) for {url}: {e}")
                if attempt < len(delays) - 1:
                    time.sleep(delay)
            except Exception as e:
                logger.warning(f"Unexpected error (attempt {attempt+1}/3) for {url}: {e}")
                if attempt < len(delays) - 1:
                    time.sleep(delay)
        return None

    def _parse_market_item(self, item: dict) -> Optional[MarketData]:
        try:
            market_id = str(item.get('id') or item.get('conditionId', ''))
            if not market_id:
                return None

            question = item.get('question', '')
            slug = item.get('slug', '')
            description = item.get('description', '')

            yes_price = 0.5
            no_price = 0.5
            outcome_prices = item.get('outcomePrices')
            if outcome_prices:
                if isinstance(outcome_prices, str):
                    try:
                        outcome_prices = json.loads(outcome_prices)
                    except (json.JSONDecodeError, ValueError):
                        outcome_prices = None
                if isinstance(outcome_prices, list) and len(outcome_prices) >= 2:
                    try:
                        yes_price = float(outcome_prices[0])
                        no_price = float(outcome_prices[1])
                    except (ValueError, TypeError):
                        pass

            volume = 0.0
            for vol_key in ('volume', 'volumeNum'):
                raw_vol = item.get(vol_key)
                if raw_vol is not None:
                    try:
                        v = float(raw_vol)
                        if v > 0:
                            volume = v
                            break
                    except (ValueError, TypeError):
                        pass

            liquidity = 0.0
            for liq_key in ('liquidity', 'liquidityNum'):
                raw_liq = item.get(liq_key)
                if raw_liq is not None:
                    try:
                        l = float(raw_liq)
                        if l > 0:
                            liquidity = l
                            break
                    except (ValueError, TypeError):
                        pass

            end_date = item.get('endDate') or item.get('end_date_iso', '')
            tokens = item.get('tokens') or item.get('clobTokenIds') or []

            spread = 0.0
            try:
                spread = abs(1.0 - yes_price - no_price)
            except Exception:
                pass

            # Preserve Gamma API metadata for classifier
            tags = item.get('tags', [])
            category = item.get('category', '')
            group_slugs = item.get('groupSlugs', [])

            return MarketData(
                market_id=market_id,
                question=question,
                slug=slug,
                description=description,
                yes_price=yes_price,
                no_price=no_price,
                volume=volume,
                liquidity=liquidity,
                end_date=end_date,
                spread=spread,
                tokens=tokens,
                tags=tags,
                category=category,
                groupSlugs=group_slugs,
            )
        except Exception as e:
            logger.warning(f"Failed to parse market item: {e}")
            return None

    def _get_cached_market_price(self, market_id: str) -> Optional[float]:
        try:
            from core.database import is_market_in_cache, MarketCache
            if is_market_in_cache(self.session, market_id, max_age_minutes=60):
                cached = (
                    self.session.query(MarketCache)
                    .filter(MarketCache.market_id == market_id)
                    .first()
                )
                if cached:
                    return cached.yes_price
        except Exception:
            pass
        return None

    def _update_db_cache(self, market: MarketData):
        try:
            from core.database import update_market_cache
            update_market_cache(self.session, market.to_dict())
        except Exception as e:
            logger.debug(f"Could not update DB cache for {market.market_id}: {e}")

    def _record_price(self, market: MarketData):
        """Record price snapshot for momentum models."""
        try:
            from core.database import record_price
            record_price(self.session, market.market_id, market.yes_price, market.no_price)
        except Exception as e:
            logger.debug(f"Could not record price for {market.market_id}: {e}")

    def fetch_active_markets(self) -> List[MarketData]:
        # Full-fetch cache: 10 min
        now = time.time()
        if self._full_fetch_cache:
            cached_ts, cached_markets = self._full_fetch_cache
            if now - cached_ts < self._full_fetch_ttl:
                logger.debug(f"fetch_active_markets: returning {len(cached_markets)} from full cache")
                return cached_markets

        all_markets: List[MarketData] = []
        all_ids = []
        page_size = 100
        max_offset = 1400  # Up to ~1500 markets

        offset = 0
        while offset <= max_offset:
            params = {
                'active': 'true',
                'closed': 'false',
                'limit': page_size,
                'offset': offset,
            }
            data = self._request(f"{GAMMA_API}/markets", params=params, cache_seconds=300)

            if not data:
                break

            items = data if isinstance(data, list) else data.get('markets', data.get('data', []))
            if not items:
                break

            price_change_threshold = 0.02  # 2% minimum change to re-analyze
            for item in items:
                market = self._parse_market_item(item)
                if not market or not market.market_id:
                    continue

                all_ids.append(market.market_id)

                # Record price for every market (builds momentum history)
                self._record_price(market)

                # Skip if price hasn't changed much (dedup)
                cached_price = self._get_cached_market_price(market.market_id)
                if cached_price is not None:
                    price_change = abs(market.yes_price - cached_price)
                    if price_change < price_change_threshold:
                        continue

                self._update_db_cache(market)
                all_markets.append(market)

            if len(items) < page_size:
                break

            offset += page_size

        # Mark inactive markets in niche_cache
        if all_ids:
            try:
                from core.database import mark_inactive_markets
                mark_inactive_markets(self.session, all_ids)
            except Exception as e:
                logger.debug(f"mark_inactive_markets error: {e}")

        self._full_fetch_cache = (now, all_markets)
        logger.info(
            f"fetch_active_markets: {len(all_ids)} total seen, "
            f"{len(all_markets)} new/changed (price moved ≥2%)"
        )
        return all_markets

    def get_market_details(self, market_id: str) -> Optional[MarketData]:
        url = f"{GAMMA_API}/markets/{market_id}"
        data = self._request(url, cache_seconds=600)
        if not data:
            return None
        item = data if isinstance(data, dict) else None
        if not item:
            return None
        return self._parse_market_item(item)

    def get_orderbook(self, token_id: str) -> Optional[dict]:
        url = f"{CLOB_API}/book"
        params = {'token_id': token_id}
        data = self._request(url, params=params, cache_seconds=300)
        if not data:
            return None
        try:
            bids = data.get('bids', [])
            asks = data.get('asks', [])
            best_bid = float(bids[0].get('price', 0)) if bids else 0.0
            best_ask = float(asks[0].get('price', 1)) if asks else 1.0
            spread = best_ask - best_bid
            midpoint = (best_bid + best_ask) / 2.0 if (best_bid or best_ask) else 0.5
            return {
                'bids': bids, 'asks': asks, 'spread': spread,
                'midpoint': midpoint, 'best_bid': best_bid, 'best_ask': best_ask,
            }
        except Exception as e:
            logger.warning(f"Failed to parse orderbook for token {token_id}: {e}")
            return None
