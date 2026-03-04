import logging
from datetime import datetime

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
        """
        Only 4 hard eliminatory criteria:
        a) yes_price outside 0.02–0.98 (market essentially resolved)
        b) Already have an open position (dedup)
        c) Volume < 1000 AND liquidity < 500 (no liquidity at all)
        d) Question too short to be meaningful (< 10 chars)

        Everything else (spread, time) is noted in logs but NOT eliminatory.
        """
        passed = []
        stats = {
            'total': len(markets),
            'price': 0,
            'dedup': 0,
            'liquidity': 0,
            'question': 0,
        }

        min_price = self.filters_config.get('min_price', 0.02)
        max_price = self.filters_config.get('max_price', 0.98)
        min_volume = self.filters_config.get('min_volume_usd', 1000)

        for market in markets:
            q_short = market.question[:50] if hasattr(market, 'question') else ''
            market_id = market.market_id if hasattr(market, 'market_id') else '?'

            # a) Price sanity — market essentially resolved if outside range
            if market.yes_price < min_price or market.yes_price > max_price:
                stats['price'] += 1
                logger.debug(
                    f"FILTERED [{market_id}] '{q_short}' reason=price "
                    f"({market.yes_price:.2f} outside [{min_price},{max_price}])"
                )
                continue

            # b) Dedup — already have open position on this market
            try:
                from core.database import get_positions_by_market
                existing = get_positions_by_market(self.session, market.market_id)
                if existing:
                    stats['dedup'] += 1
                    continue
            except Exception as e:
                logger.debug(f"Could not check existing positions for {market.market_id}: {e}")

            # c) Absolute minimum liquidity — volume < 1000 AND liquidity < 500
            has_volume = getattr(market, 'volume', 0) >= min_volume
            has_liquidity = getattr(market, 'liquidity', 0) >= 500
            if not has_volume and not has_liquidity:
                stats['liquidity'] += 1
                logger.debug(
                    f"FILTERED [{market_id}] '{q_short}' reason=no_liquidity "
                    f"(vol=${market.volume:.0f}, liq=${market.liquidity:.0f})"
                )
                continue

            # d) Question too short to be meaningful
            if len(market.question) < 10:
                stats['question'] += 1
                continue

            # Log advisory info (spread, time) without filtering
            spread = abs(1.0 - market.yes_price - market.no_price)
            if spread > 0.15:
                logger.debug(f"INFO [{market_id}] high spread {spread:.1%} — keeping, math will penalize")

            if market.end_date:
                try:
                    end = self._parse_end_date(market.end_date)
                    hours_left = (end - datetime.utcnow()).total_seconds() / 3600
                    if hours_left < 0.25:
                        logger.debug(f"INFO [{market_id}] only {hours_left:.1f}h left — keeping, math will penalize")
                    elif hours_left > 999 * 24:
                        logger.debug(f"INFO [{market_id}] {hours_left/24:.0f}d to resolution — keeping")
                except Exception:
                    pass

            passed.append(market)

        logger.info(
            f"Mechanical filter: {len(markets)} -> {len(passed)} "
            f"(price:{stats['price']}, dedup:{stats['dedup']}, "
            f"no_liquidity:{stats['liquidity']}, question:{stats['question']})"
        )
        return passed
