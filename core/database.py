"""
core/database.py — V2 Database Layer
"MATH FIRST, AI LAST" Polymarket Trading Bot

SQLite + SQLAlchemy ORM.
DB file: data/polymarket_bot.db (auto-created).
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timedelta, date
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    String,
    Boolean,
    DateTime,
    Date,
    Text,
    func,
    and_,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

Base = declarative_base()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class MarketCache(Base):
    __tablename__ = "markets_cache"

    id = Column(Integer, primary_key=True)
    market_id = Column(String, unique=True, index=True, nullable=False)
    question = Column(String, nullable=True)
    slug = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    yes_price = Column(Float, nullable=True)
    no_price = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    liquidity = Column(Float, nullable=True)
    end_date = Column(String, nullable=True)
    last_fetched = Column(DateTime, default=datetime.utcnow)
    niche = Column(String, nullable=True)
    math_probability = Column(Float, nullable=True)
    math_confidence = Column(Float, nullable=True)
    math_method = Column(String, nullable=True)
    edge_yes = Column(Float, nullable=True)
    edge_no = Column(Float, nullable=True)
    best_direction = Column(String, nullable=True)  # YES / NO / SKIP
    status = Column(String, default="active")


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    market_id = Column(String, index=True, nullable=False)
    market_question = Column(String, nullable=True)
    niche = Column(String, nullable=True)
    math_probability = Column(Float, nullable=True)
    math_confidence = Column(Float, nullable=True)
    math_method = Column(String, nullable=True)
    math_edge = Column(Float, nullable=True)
    haiku_called = Column(Boolean, default=False)
    haiku_confirmed = Column(Boolean, nullable=True)
    haiku_adjusted_edge = Column(Float, nullable=True)
    sonnet_called = Column(Boolean, default=False)
    sonnet_go = Column(Boolean, nullable=True)
    sonnet_direction = Column(String, nullable=True)
    sonnet_confidence = Column(String, nullable=True)
    sonnet_edge = Column(Float, nullable=True)
    direction = Column(String, nullable=True)  # YES / NO
    was_bet_placed = Column(Boolean, default=False)
    skip_reason = Column(String, nullable=True)
    funnel_step = Column(String, nullable=True)  # fetched/filtered/classified/math_edge/haiku/sonnet/bet


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, index=True, nullable=False)
    market_question = Column(String, nullable=True)
    direction = Column(String, nullable=False)  # YES / NO
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=True)
    exit_price = Column(Float, nullable=True)
    amount_usdc = Column(Float, nullable=False)
    pnl_unrealized = Column(Float, default=0.0)
    pnl_realized = Column(Float, nullable=True)
    entry_time = Column(DateTime, default=datetime.utcnow)
    exit_time = Column(DateTime, nullable=True)
    exit_reason = Column(String, nullable=True)  # resolution/take-profit/stop-loss/manual
    bot_niche = Column(String, nullable=True)
    math_edge = Column(Float, nullable=True)
    confidence = Column(String, nullable=True)
    status = Column(String, default="open")  # open / closed


class ApiUsage(Base):
    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    model = Column(String, nullable=False)  # haiku / sonnet
    tokens_in = Column(Integer, nullable=True)
    tokens_out = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    market_id = Column(String, nullable=True)
    was_useful = Column(Boolean, default=False)


class KpiHistory(Base):
    __tablename__ = "kpi_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    period = Column(String, nullable=False)  # daily / weekly
    niche = Column(String, nullable=True)  # null = global
    metrics_json = Column(Text, nullable=True)  # JSON string with all KPI data


class NewsHash(Base):
    __tablename__ = "news_hashes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hash = Column(String, unique=True, index=True, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


class NicheCache(Base):
    __tablename__ = "niche_cache"

    market_id = Column(String, primary_key=True)
    niche = Column(String, nullable=False)
    classified_by = Column(String, nullable=False)  # gamma_tag / haiku / gamma_tag_sport
    classified_at = Column(DateTime, default=datetime.utcnow)
    market_active = Column(Boolean, default=True)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    market_id = Column(String, nullable=False, index=True)
    timestamp = Column(Float, nullable=False)
    yes_price = Column(Float, nullable=False)
    no_price = Column(Float, nullable=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_text(text: str) -> str:
    """Return SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _today_start() -> datetime:
    """Midnight UTC today."""
    now = datetime.utcnow()
    return datetime(now.year, now.month, now.day)


def _month_start() -> datetime:
    """First second of the current UTC month."""
    now = datetime.utcnow()
    return datetime(now.year, now.month, 1)


# ---------------------------------------------------------------------------
# 1. init_db
# ---------------------------------------------------------------------------


def init_db():
    """
    Create (or open) the SQLite database and ensure all tables exist.

    Returns
    -------
    (engine, SessionLocal) tuple where SessionLocal is a session factory.
    """
    try:
        # Resolve path relative to project root (two levels up from this file)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        db_path = os.path.join(data_dir, "polymarket_bot.db")
        db_url = f"sqlite:///{db_path}"

        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        Base.metadata.create_all(engine)

        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

        logger.info("Database initialised at %s", db_path)
        return engine, SessionLocal

    except Exception as exc:
        logger.error("init_db failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# 2. is_market_in_cache
# ---------------------------------------------------------------------------


def is_market_in_cache(session, market_id: str, max_age_minutes: int = 10) -> bool:
    """
    Return True if the market is cached and was fetched within max_age_minutes.
    """
    try:
        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
        row = (
            session.query(MarketCache)
            .filter(
                MarketCache.market_id == market_id,
                MarketCache.last_fetched >= cutoff,
            )
            .first()
        )
        return row is not None
    except Exception as exc:
        logger.error("is_market_in_cache error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 3. update_market_cache
# ---------------------------------------------------------------------------


def update_market_cache(session, market_data: dict):
    """
    Upsert a market row in markets_cache.
    market_data must contain at least 'market_id'.
    """
    try:
        market_id = market_data.get("market_id")
        if not market_id:
            logger.warning("update_market_cache: missing market_id in data")
            return

        row = session.query(MarketCache).filter(MarketCache.market_id == market_id).first()

        if row is None:
            row = MarketCache(market_id=market_id)
            session.add(row)

        # Update all provided fields
        updatable_fields = [
            "question", "slug", "description", "yes_price", "no_price",
            "volume", "liquidity", "end_date", "niche",
            "math_probability", "math_confidence", "math_method",
            "edge_yes", "edge_no", "best_direction", "status",
        ]
        for field in updatable_fields:
            if field in market_data:
                setattr(row, field, market_data[field])

        row.last_fetched = datetime.utcnow()
        session.commit()

    except Exception as exc:
        logger.error("update_market_cache error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 4. get_cached_markets
# ---------------------------------------------------------------------------


def get_cached_markets(session, niche: str = None) -> list:
    """
    Return all cached markets, optionally filtered by niche.
    Returns a list of MarketCache ORM objects.
    """
    try:
        query = session.query(MarketCache).filter(MarketCache.status == "active")
        if niche is not None:
            query = query.filter(MarketCache.niche == niche)
        return query.all()
    except Exception as exc:
        logger.error("get_cached_markets error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 5. cleanup_old_cache
# ---------------------------------------------------------------------------


def cleanup_old_cache(session, hours: int = 24):
    """
    Delete markets_cache rows older than `hours` hours.
    """
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        deleted = (
            session.query(MarketCache)
            .filter(MarketCache.last_fetched < cutoff)
            .delete(synchronize_session=False)
        )
        session.commit()
        logger.info("cleanup_old_cache: removed %d stale rows", deleted)
    except Exception as exc:
        logger.error("cleanup_old_cache error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 6. is_news_already_processed
# ---------------------------------------------------------------------------


def is_news_already_processed(session, text: str) -> bool:
    """
    Hash `text` and check whether the hash exists in news_hashes.
    Returns True if already processed.
    """
    try:
        h = _hash_text(text)
        row = session.query(NewsHash).filter(NewsHash.hash == h).first()
        return row is not None
    except Exception as exc:
        logger.error("is_news_already_processed error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# 7. mark_news_processed
# ---------------------------------------------------------------------------


def mark_news_processed(session, text: str):
    """
    Hash `text` and insert the hash into news_hashes (ignore if duplicate).
    """
    try:
        h = _hash_text(text)
        existing = session.query(NewsHash).filter(NewsHash.hash == h).first()
        if existing is None:
            session.add(NewsHash(hash=h))
            session.commit()
    except Exception as exc:
        logger.error("mark_news_processed error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 8. get_daily_api_calls
# ---------------------------------------------------------------------------


def get_daily_api_calls(session, model: str) -> int:
    """
    Return the count of api_usage rows for `model` created today (UTC).
    """
    try:
        today = _today_start()
        count = (
            session.query(func.count(ApiUsage.id))
            .filter(
                ApiUsage.model == model,
                ApiUsage.timestamp >= today,
            )
            .scalar()
        )
        return count or 0
    except Exception as exc:
        logger.error("get_daily_api_calls error: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# 9. get_monthly_api_cost
# ---------------------------------------------------------------------------


def get_monthly_api_cost(session) -> float:
    """
    Return the sum of cost_usd in api_usage for the current UTC month.
    """
    try:
        month_start = _month_start()
        total = (
            session.query(func.sum(ApiUsage.cost_usd))
            .filter(ApiUsage.timestamp >= month_start)
            .scalar()
        )
        return float(total or 0.0)
    except Exception as exc:
        logger.error("get_monthly_api_cost error: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 10. get_daily_exposure
# ---------------------------------------------------------------------------


def get_daily_exposure(session) -> float:
    """
    Return the sum of amount_usdc for open positions opened today (UTC).
    """
    try:
        today = _today_start()
        total = (
            session.query(func.sum(Position.amount_usdc))
            .filter(
                Position.status == "open",
                Position.entry_time >= today,
            )
            .scalar()
        )
        return float(total or 0.0)
    except Exception as exc:
        logger.error("get_daily_exposure error: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 11. get_weekly_drawdown_pct
# ---------------------------------------------------------------------------


def get_weekly_drawdown_pct(session, current_capital: float) -> float:
    """
    Compute the maximum drawdown percentage over the last 7 days.

    Drawdown is measured as the peak-to-trough decline in capital.
    Returns a positive percentage (e.g. 5.0 means 5 % drawdown).
    Returns 0.0 on any error or when there is no data.
    """
    try:
        seven_days_ago = datetime.utcnow() - timedelta(days=7)

        # Collect daily realized PnL for positions closed in the last 7 days
        rows = (
            session.query(
                func.date(Position.exit_time).label("exit_date"),
                func.sum(Position.pnl_realized).label("daily_pnl"),
            )
            .filter(
                Position.status == "closed",
                Position.exit_time >= seven_days_ago,
                Position.pnl_realized.isnot(None),
            )
            .group_by(func.date(Position.exit_time))
            .order_by(func.date(Position.exit_time))
            .all()
        )

        if not rows:
            return 0.0

        # Reconstruct capital curve (backwards from current_capital)
        total_pnl_in_window = sum(r.daily_pnl for r in rows)
        starting_capital = current_capital - total_pnl_in_window

        capital = starting_capital
        peak = starting_capital
        max_drawdown_pct = 0.0

        for row in rows:
            capital += row.daily_pnl
            if capital > peak:
                peak = capital
            if peak > 0:
                drawdown = (peak - capital) / peak * 100.0
                if drawdown > max_drawdown_pct:
                    max_drawdown_pct = drawdown

        return round(max_drawdown_pct, 4)

    except Exception as exc:
        logger.error("get_weekly_drawdown_pct error: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 12. get_open_positions
# ---------------------------------------------------------------------------


def get_open_positions(session) -> list:
    """Return all positions with status='open'."""
    try:
        return session.query(Position).filter(Position.status == "open").all()
    except Exception as exc:
        logger.error("get_open_positions error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 13. get_positions_by_market
# ---------------------------------------------------------------------------


def get_positions_by_market(session, market_id: str) -> list:
    """Return open positions for a specific market_id."""
    try:
        return (
            session.query(Position)
            .filter(
                Position.market_id == market_id,
                Position.status == "open",
            )
            .all()
        )
    except Exception as exc:
        logger.error("get_positions_by_market error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 14. get_closed_positions
# ---------------------------------------------------------------------------


def get_closed_positions_today(session) -> list:
    """Return closed positions from the last 24h — used by daily postmortem."""
    try:
        cutoff = datetime.utcnow() - timedelta(hours=24)
        return (
            session.query(Position)
            .filter(Position.status == "closed")
            .filter(Position.exit_time >= cutoff)
            .order_by(Position.exit_time.desc())
            .all()
        )
    except Exception as exc:
        logger.error("get_closed_positions_today error: %s", exc)
        return []


def get_closed_positions(session, limit: int = 20) -> list:
    """Return the last `limit` closed positions ordered by exit_time descending."""
    try:
        return (
            session.query(Position)
            .filter(Position.status == "closed")
            .order_by(Position.exit_time.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_closed_positions error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 15. open_position
# ---------------------------------------------------------------------------


def open_position(session, **kwargs) -> Position:
    """
    Create and persist a new Position.

    Required kwargs: market_id, direction, entry_price, amount_usdc.
    All other Position columns are optional.

    Returns the created Position object, or None on failure.
    """
    try:
        position = Position(**kwargs)
        if position.entry_time is None:
            position.entry_time = datetime.utcnow()
        if position.status is None:
            position.status = "open"
        session.add(position)
        session.commit()
        session.refresh(position)
        logger.info(
            "open_position: id=%s market=%s dir=%s amount=%.2f",
            position.id,
            position.market_id,
            position.direction,
            position.amount_usdc,
        )
        return position
    except Exception as exc:
        logger.error("open_position error: %s", exc)
        session.rollback()
        return None


# ---------------------------------------------------------------------------
# 16. close_position
# ---------------------------------------------------------------------------


def close_position(
    session,
    position_id: int,
    exit_price: float,
    exit_reason: str,
) -> Position:
    """
    Close an open position.

    PnL formula:
        pnl_realized = (exit_price - entry_price) * (amount_usdc / entry_price)

    Returns the updated Position object, or None on failure.
    """
    try:
        position = session.query(Position).filter(Position.id == position_id).first()
        if position is None:
            logger.warning("close_position: position id=%s not found", position_id)
            return None

        if position.entry_price and position.entry_price != 0:
            pnl = (exit_price - position.entry_price) * (
                position.amount_usdc / position.entry_price
            )
        else:
            pnl = 0.0

        position.exit_price = exit_price
        position.exit_reason = exit_reason
        position.pnl_realized = round(pnl, 6)
        position.pnl_unrealized = 0.0
        position.exit_time = datetime.utcnow()
        position.status = "closed"

        session.commit()
        session.refresh(position)
        logger.info(
            "close_position: id=%s pnl=%.4f reason=%s",
            position.id,
            pnl,
            exit_reason,
        )
        return position
    except Exception as exc:
        logger.error("close_position error: %s", exc)
        session.rollback()
        return None


# ---------------------------------------------------------------------------
# 17. get_capital
# ---------------------------------------------------------------------------


def get_capital(session) -> float:
    """
    Return current capital:
        CAPITAL_INITIAL (env var, default 1000) + sum of all pnl_realized.
    """
    try:
        initial = float(os.environ.get("CAPITAL_INITIAL", 1000.0))
        realized_sum = (
            session.query(func.sum(Position.pnl_realized))
            .filter(
                Position.status == "closed",
                Position.pnl_realized.isnot(None),
            )
            .scalar()
        )
        return round(initial + float(realized_sum or 0.0), 6)
    except Exception as exc:
        logger.error("get_capital error: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 18. get_daily_pnl
# ---------------------------------------------------------------------------


def get_daily_pnl(session) -> float:
    """Return the sum of pnl_realized for positions closed today (UTC)."""
    try:
        today = _today_start()
        total = (
            session.query(func.sum(Position.pnl_realized))
            .filter(
                Position.status == "closed",
                Position.exit_time >= today,
                Position.pnl_realized.isnot(None),
            )
            .scalar()
        )
        return float(total or 0.0)
    except Exception as exc:
        logger.error("get_daily_pnl error: %s", exc)
        return 0.0


# ---------------------------------------------------------------------------
# 19. record_api_call
# ---------------------------------------------------------------------------


def record_api_call(
    session,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    market_id: str = None,
    was_useful: bool = False,
):
    """Insert a row into api_usage."""
    try:
        row = ApiUsage(
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            market_id=market_id,
            was_useful=was_useful,
            timestamp=datetime.utcnow(),
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.error("record_api_call error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 20. record_signal
# ---------------------------------------------------------------------------


def record_signal(session, **kwargs) -> Signal:
    """
    Insert a new Signal row.

    All Signal columns may be passed as kwargs.
    Returns the created Signal object, or None on failure.
    """
    try:
        signal = Signal(**kwargs)
        if signal.timestamp is None:
            signal.timestamp = datetime.utcnow()
        session.add(signal)
        session.commit()
        session.refresh(signal)
        return signal
    except Exception as exc:
        logger.error("record_signal error: %s", exc)
        session.rollback()
        return None


# ---------------------------------------------------------------------------
# 21. get_recent_signals
# ---------------------------------------------------------------------------


def get_recent_signals(session, limit: int = 30) -> list:
    """Return the last `limit` signals ordered by timestamp descending."""
    try:
        return (
            session.query(Signal)
            .order_by(Signal.timestamp.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_recent_signals error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 22. get_bot_kpis
# ---------------------------------------------------------------------------


def get_bot_kpis(session, niche: str) -> dict:
    """
    Compute KPIs for a given niche.

    Returns a dict with:
        win_rate    - fraction of profitable closed positions (0.0–1.0)
        total_bets  - total closed positions in niche
        pnl         - total realized PnL
        roi         - pnl / total_invested * 100 (%)
    """
    try:
        positions = (
            session.query(Position)
            .filter(
                Position.bot_niche == niche,
                Position.status == "closed",
                Position.pnl_realized.isnot(None),
            )
            .all()
        )

        total_bets = len(positions)
        if total_bets == 0:
            return {"win_rate": 0.0, "total_bets": 0, "pnl": 0.0, "roi": 0.0}

        wins = sum(1 for p in positions if p.pnl_realized > 0)
        pnl = sum(p.pnl_realized for p in positions)
        total_invested = sum(p.amount_usdc for p in positions if p.amount_usdc)

        win_rate = wins / total_bets
        roi = (pnl / total_invested * 100.0) if total_invested else 0.0

        return {
            "win_rate": round(win_rate, 4),
            "total_bets": total_bets,
            "pnl": round(pnl, 4),
            "roi": round(roi, 4),
        }
    except Exception as exc:
        logger.error("get_bot_kpis error for niche=%s: %s", niche, exc)
        return {"win_rate": 0.0, "total_bets": 0, "pnl": 0.0, "roi": 0.0}


# ---------------------------------------------------------------------------
# 23. get_funnel_stats
# ---------------------------------------------------------------------------

_FUNNEL_STEPS = ["fetched", "filtered", "classified", "math_edge", "haiku", "sonnet", "bet"]


def get_funnel_stats(session) -> dict:
    """
    Return a count of signals per funnel_step for today (UTC).

    Example: {'fetched': 120, 'filtered': 45, ..., 'bet': 3}
    """
    try:
        today = _today_start()
        rows = (
            session.query(Signal.funnel_step, func.count(Signal.id))
            .filter(Signal.timestamp >= today)
            .group_by(Signal.funnel_step)
            .all()
        )

        stats = {step: 0 for step in _FUNNEL_STEPS}
        for funnel_step, count in rows:
            if funnel_step in stats:
                stats[funnel_step] = count

        return stats
    except Exception as exc:
        logger.error("get_funnel_stats error: %s", exc)
        return {step: 0 for step in _FUNNEL_STEPS}


# ---------------------------------------------------------------------------
# 24. get_pnl_history
# ---------------------------------------------------------------------------


def get_pnl_history(session, days: int = 30) -> list:
    """
    Return daily PnL history for the last `days` days.

    Each entry is a dict: {'date': 'YYYY-MM-DD', 'pnl': float}
    Ordered oldest to newest.
    """
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            session.query(
                func.date(Position.exit_time).label("exit_date"),
                func.sum(Position.pnl_realized).label("daily_pnl"),
            )
            .filter(
                Position.status == "closed",
                Position.exit_time >= since,
                Position.pnl_realized.isnot(None),
            )
            .group_by(func.date(Position.exit_time))
            .order_by(func.date(Position.exit_time))
            .all()
        )

        return [
            {"date": str(row.exit_date), "pnl": round(float(row.daily_pnl), 4)}
            for row in rows
        ]
    except Exception as exc:
        logger.error("get_pnl_history error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 25. save_kpi
# ---------------------------------------------------------------------------


def save_kpi(session, niche: str, period: str, metrics: dict):
    """
    Persist KPI data into kpi_history as a JSON blob.

    Parameters
    ----------
    niche   : niche name, or None for global KPIs
    period  : 'daily' or 'weekly'
    metrics : arbitrary dict — will be serialised to JSON
    """
    try:
        row = KpiHistory(
            date=date.today(),
            period=period,
            niche=niche,
            metrics_json=json.dumps(metrics, default=str),
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.error("save_kpi error niche=%s period=%s: %s", niche, period, exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 26. get_niche_cache
# ---------------------------------------------------------------------------


def get_niche_cache(session, market_id: str) -> str:
    """Return the cached niche for a market_id, or None if not cached."""
    try:
        row = session.query(NicheCache).filter(
            NicheCache.market_id == market_id,
            NicheCache.market_active == True,
        ).first()
        return row.niche if row else None
    except Exception as exc:
        logger.error("get_niche_cache error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 27. set_niche_cache
# ---------------------------------------------------------------------------


def set_niche_cache(session, market_id: str, niche: str, classified_by: str):
    """Upsert a niche classification into the cache."""
    try:
        row = session.query(NicheCache).filter(
            NicheCache.market_id == market_id
        ).first()
        if row is None:
            row = NicheCache(market_id=market_id)
            session.add(row)
        row.niche = niche
        row.classified_by = classified_by
        row.classified_at = datetime.utcnow()
        row.market_active = True
        session.commit()
    except Exception as exc:
        logger.error("set_niche_cache error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 28. record_price
# ---------------------------------------------------------------------------


def record_price(session, market_id: str, yes_price: float, no_price: float):
    """Record a price snapshot for price history (momentum models)."""
    try:
        import time as _time
        row = PriceHistory(
            market_id=market_id,
            timestamp=_time.time(),
            yes_price=yes_price,
            no_price=no_price,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.error("record_price error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 29. get_price_history
# ---------------------------------------------------------------------------


def get_price_history(session, market_id: str, days: int = 14) -> list:
    """Return price history for a market as list of {timestamp, yes_price}."""
    try:
        import time as _time
        cutoff = _time.time() - days * 86400
        rows = (
            session.query(PriceHistory)
            .filter(
                PriceHistory.market_id == market_id,
                PriceHistory.timestamp >= cutoff,
            )
            .order_by(PriceHistory.timestamp)
            .all()
        )
        return [
            {"timestamp": r.timestamp, "yes_price": r.yes_price, "no_price": r.no_price}
            for r in rows
        ]
    except Exception as exc:
        logger.error("get_price_history error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# 30. mark_inactive_markets
# ---------------------------------------------------------------------------


def mark_inactive_markets(session, active_market_ids: list):
    """Mark markets not in active_market_ids as inactive in niche_cache."""
    try:
        if not active_market_ids:
            return
        session.query(NicheCache).filter(
            NicheCache.market_id.notin_(active_market_ids),
            NicheCache.market_active == True,
        ).update({"market_active": False}, synchronize_session=False)
        session.commit()
    except Exception as exc:
        logger.error("mark_inactive_markets error: %s", exc)
        session.rollback()


# ---------------------------------------------------------------------------
# 31. cleanup_db
# ---------------------------------------------------------------------------


def cleanup_db(session):
    """
    Comprehensive cleanup:
    - niche_cache: delete inactive markets older than 24h
    - price_history: delete entries older than 14 days
    - signals: delete entries older than 30 days
    - api_usage: delete entries older than 30 days
    - news_hashes: delete entries older than 7 days
    - closed positions: delete entries older than 90 days
    """
    try:
        now = datetime.utcnow()

        # niche_cache: inactive > 24h
        cutoff_24h = now - timedelta(hours=24)
        deleted = session.query(NicheCache).filter(
            NicheCache.market_active == False,
            NicheCache.classified_at < cutoff_24h,
        ).delete(synchronize_session=False)
        logger.info(f"cleanup_db: niche_cache removed {deleted} inactive rows")

        # price_history: older than 14 days
        import time as _time
        cutoff_14d = _time.time() - 14 * 86400
        deleted = session.query(PriceHistory).filter(
            PriceHistory.timestamp < cutoff_14d,
        ).delete(synchronize_session=False)
        logger.info(f"cleanup_db: price_history removed {deleted} old rows")

        # signals: older than 30 days
        cutoff_30d = now - timedelta(days=30)
        deleted = session.query(Signal).filter(
            Signal.timestamp < cutoff_30d,
        ).delete(synchronize_session=False)
        logger.info(f"cleanup_db: signals removed {deleted} old rows")

        # api_usage: older than 30 days
        deleted = session.query(ApiUsage).filter(
            ApiUsage.timestamp < cutoff_30d,
        ).delete(synchronize_session=False)
        logger.info(f"cleanup_db: api_usage removed {deleted} old rows")

        # news_hashes: older than 7 days
        cutoff_7d = now - timedelta(days=7)
        deleted = session.query(NewsHash).filter(
            NewsHash.timestamp < cutoff_7d,
        ).delete(synchronize_session=False)
        logger.info(f"cleanup_db: news_hashes removed {deleted} old rows")

        # closed positions: older than 90 days
        cutoff_90d = now - timedelta(days=90)
        deleted = session.query(Position).filter(
            Position.status == "closed",
            Position.exit_time < cutoff_90d,
        ).delete(synchronize_session=False)
        logger.info(f"cleanup_db: old closed positions removed {deleted} rows")

        session.commit()
        logger.info("cleanup_db: complete")

    except Exception as exc:
        logger.error("cleanup_db error: %s", exc)
        session.rollback()
