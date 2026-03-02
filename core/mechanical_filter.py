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
        """Attempt to parse an ISO 8601 end date string into a datetime object."""
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
        """
        Apply all mechanical filter criteria to a list of markets.
        Tracks rejection reasons for logging and returns only markets that pass all filters.
        """
        passed = []
        stats = {
            'total': len(markets),
            'volume': 0,
            'spread': 0,
            'time': 0,
            'price': 0,
            'dedup': 0,
            'question': 0,
        }

        min_volume = self.filters_config.get('min_volume_usd', 10000)
        max_spread = self.filters_config.get('max_spread', 0.05)
        min_hours = self.filters_config.get('min_hours_to_resolution', 1)
        max_days = self.filters_config.get('max_days_to_resolution', 60)

        for market in markets:
            # a) Volume check
            if market.volume < min_volume:
                stats['volume'] += 1
                continue

            # b) Spread check
            spread = abs(1.0 - market.yes_price - market.no_price)
            if spread > max_spread:
                stats['spread'] += 1
                continue

            # c) Time to resolution check
            if market.end_date:
                try:
                    end = self._parse_end_date(market.end_date)
                    now = datetime.utcnow()
                    hours_left = (end - now).total_seconds() / 3600
                    if hours_left < min_hours:
                        stats['time'] += 1
                        continue
                    if hours_left > max_days * 24:
                        stats['time'] += 1
                        continue
                except Exception:
                    pass  # If we can't parse the date, let it through

            # d) Price sanity check: yes_price must be between 0.05 and 0.95
            if market.yes_price < 0.05 or market.yes_price > 0.95:
                stats['price'] += 1
                continue

            # e) Not already in an open position (dedup)
            try:
                from core.database import get_positions_by_market
                existing = get_positions_by_market(self.session, market.market_id)
                if existing:
                    stats['dedup'] += 1
                    continue
            except Exception as e:
                logger.debug(f"Could not check existing positions for {market.market_id}: {e}")

            # f) Question must be meaningful (length > 10 chars)
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
