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
    """Simple data class for market info"""
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

    def to_dict(self):
        return self.__dict__


class MarketFetcher:
    def __init__(self, db_session):
        self.session = db_session
        self._cache: Dict[str, tuple] = {}  # url_key -> (timestamp, data)

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
                response = requests.get(url, params=params, headers=HEADERS, timeout=10)
                response.raise_for_status()
                data = response.json()
                self._cache[cache_key] = (now, data)
                return data
            except requests.RequestException as e:
                logger.warning(f"Request failed (attempt {attempt + 1}/3) for {url}: {e}")
                if attempt < len(delays) - 1:
                    time.sleep(delay)
            except Exception as e:
                logger.warning(f"Unexpected error (attempt {attempt + 1}/3) for {url}: {e}")
                if attempt < len(delays) - 1:
                    time.sleep(delay)

        return None

    def _parse_market_item(self, item: dict) -> Optional[MarketData]:
        try:
            market_id = item.get('id') or item.get('conditionId', '')
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
                        volume = float(raw_vol)
                        if volume > 0:
                            break
                    except (ValueError, TypeError):
                        pass

            liquidity = 0.0
            for liq_key in ('liquidity', 'liquidityNum'):
                raw_liq = item.get(liq_key)
                if raw_liq is not None:
                    try:
                        liquidity = float(raw_liq)
                        if liquidity > 0:
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
            )
        except Exception as e:
            logger.warning(f"Failed to parse market item: {e}")
            return None

    def _get_cached_market_price(self, market_id: str) -> Optional[float]:
        try:
            from core.database import is_market_in_cache, MarketCache
            # Check if fresh cache exists (within 10 minutes)
            if is_market_in_cache(self.session, market_id, max_age_minutes=10):
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
            logger.debug(f"Could not update DB cache for market {market.market_id}: {e}")

    def fetch_active_markets(self) -> List[MarketData]:
        all_markets: List[MarketData] = []
        page_size = 100
        max_pages = 5

        for page in range(max_pages):
            offset = page * page_size
            params = {
                'active': 'true',
                'closed': 'false',
                'limit': page_size,
                'offset': offset,
            }
            data = self._request(f"{GAMMA_API}/markets", params=params, cache_seconds=600)

            if not data:
                break

            items = data if isinstance(data, list) else data.get('markets', data.get('data', []))

            if not items:
                break

            for item in items:
                market = self._parse_market_item(item)
                if not market or not market.market_id:
                    continue

                cached_price = self._get_cached_market_price(market.market_id)
                if cached_price is not None:
                    price_change = abs(market.yes_price - cached_price)
                    if price_change < 0.01:
                        continue

                self._update_db_cache(market)
                all_markets.append(market)

            if len(items) < page_size:
                break

        logger.info(f"fetch_active_markets: fetched {len(all_markets)} new/changed markets")
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

            best_bid = 0.0
            best_ask = 1.0

            if bids:
                try:
                    best_bid = float(bids[0].get('price', 0))
                except (ValueError, TypeError, AttributeError):
                    pass

            if asks:
                try:
                    best_ask = float(asks[0].get('price', 1))
                except (ValueError, TypeError, AttributeError):
                    pass

            spread = best_ask - best_bid
            midpoint = (best_bid + best_ask) / 2.0 if (best_bid or best_ask) else 0.5

            return {
                'bids': bids,
                'asks': asks,
                'spread': spread,
                'midpoint': midpoint,
                'best_bid': best_bid,
                'best_ask': best_ask,
            }
        except Exception as e:
            logger.warning(f"Failed to parse orderbook for token {token_id}: {e}")
            return None

    def get_price_history(self, market_id: str, days: int = 14) -> List[dict]:
        endpoints = [
            f"{GAMMA_API}/markets/{market_id}/history",
            f"{GAMMA_API}/prices",
        ]
        params_map = {
            f"{GAMMA_API}/markets/{market_id}/history": None,
            f"{GAMMA_API}/prices": {'market': market_id},
        }

        for url in endpoints:
            data = self._request(url, params=params_map[url], cache_seconds=1800)
            if not data:
                continue

            try:
                items = data if isinstance(data, list) else data.get('history', data.get('prices', []))
                result = []
                for entry in items:
                    try:
                        ts = entry.get('t') or entry.get('timestamp') or entry.get('time', '')
                        yes = float(entry.get('p') or entry.get('yes_price') or entry.get('price', 0.5))
                        no = 1.0 - yes
                        result.append({'timestamp': ts, 'yes_price': yes, 'no_price': no})
                    except (ValueError, TypeError):
                        continue
                if result:
                    logger.debug(f"Got {len(result)} price history points for market {market_id}")
                    return result
            except Exception as e:
                logger.debug(f"Failed to parse price history from {url}: {e}")
                continue

        return []
