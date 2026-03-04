import logging
from datetime import datetime, timedelta
from typing import List

logger = logging.getLogger(__name__)


class MechanicalFilter:
    def __init__(self, config: dict, db_session):
        self.config = config
        self.session = db_session
        self.filters_config = config.get('filters', {})

    def _parse_end_date(self, end_date_str: str) -> datetime:
        formats = [
            '%Y-%m-%dT%H:%M:%SZ',
            '%Y-%m-%dT%H:%M:%S.%fZ',
            '%Y-%m-%dT%H:%M:%S',
            '%Y-%m-%dT%H:%M:%S.%f',
            '%Y-%m-%d',
        ]
        for fmt in formats:
            try:
                return datetime.strptime(end_date_str, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse date: {end_date_str}")

    def filter_markets(self, markets: list) -> list:
        passed = []
        stats = {
            'total': len(markets),
            'volume': 0, 'spread': 0, 'time': 0,
            'price': 0, 'dedup': 0, 'question': 0,
        }

        min_volume = self.filters_config.get('min_volume_usd', 5000)
        max_spread = self.filters_config.get('max_spread', 0.08)
        min_hours = self.filters_config.get('min_hours_to_resolution', 0.5)
        max_days = self.filters_config.get('max_days_to_resolution', 180)
        min_price = self.filters_config.get('min_price', 0.03)
        max_price = self.filters_config.get('max_price', 0.97)

        for market in markets:
            q_short = market.question[:50] if hasattr(market, 'question') else ''
            market_id = market.market_id if hasattr(market, 'market_id') else '?'

            # a) Volume check
            if market.volume < min_volume:
                stats['volume'] += 1
                logger.debug(f"FILTERED [{market_id}] '{q_short}' reason=volume (${market.volume:.0f} < ${min_volume})")
                continue

            # b) Spread check
            spread = abs(1.0 - market.yes_price - market.no_price)
            if spread > max_spread:
                stats['spread'] += 1
                logger.debug(f"FILTERED [{market_id}] '{q_short}' reason=spread ({spread:.1%} > {max_spread:.0%})")
                continue

            # c) Time to resolution
            if market.end_date:
                try:
                    end = self._parse_end_date(market.end_date)
                    now = datetime.utcnow()
                    hours_left = (end - now).total_seconds() / 3600
                    if hours_left < min_hours:
                        stats['time'] += 1
                        logger.debug(f"FILTERED [{market_id}] '{q_short}' reason=time_too_close ({hours_left:.1f}h)")
                        continue
                    if hours_left > max_days * 24:
                        stats['time'] += 1
                        logger.debug(f"FILTERED [{market_id}] '{q_short}' reason=time ({hours_left/24:.0f}d > {max_days}d)")
                        continue
                except Exception:
                    pass  # If unparseable, let through

            # d) Price sanity check
            if market.yes_price < min_price or market.yes_price > max_price:
                stats['price'] += 1
                logger.debug(f"FILTERED [{market_id}] '{q_short}' reason=price ({market.yes_price:.2f} out of [{min_price},{max_price}])")
                continue

            # e) No existing open position (dedup)
            try:
                from core.database import get_positions_by_market
                existing = get_positions_by_market(self.session, market.market_id)
                if existing:
                    stats['dedup'] += 1
                    continue
            except Exception as e:
                logger.debug(f"Could not check existing positions for {market.market_id}: {e}")

            # f) Question meaningful
            if len(market.question) < 10:
                stats['question'] += 1
                continue

            passed.append(market)

        logger.info(
            f"Mechanical filter: {len(markets)} -> {len(passed)} "
            f"(volume:{stats['volume']}, spread:{stats['spread']}, "
            f"time:{stats['time']}, price:{stats['price']}, "
            f"dedup:{stats['dedup']}, question:{stats['question']})"
        )
        return passed
