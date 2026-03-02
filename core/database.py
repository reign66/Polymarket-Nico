"""
core/database.py

SQLite + SQLAlchemy database layer for Polymarket Bot.
Database file: data/polymarket_bot.db
"""

import os
import logging
from datetime import datetime, date, timedelta

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    Float,
    String,
    Boolean,
    DateTime,
    Date,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base & path helpers
# ---------------------------------------------------------------------------

Base = declarative_base()

_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DB_PATH = os.path.join(_DB_DIR, "polymarket_bot.db")

CAPITAL_INITIAL = float(os.environ.get("CAPITAL_INITIAL", 1000))


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class Signal(Base):
    """One row per signal evaluated by the pipeline."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Context
    bot_niche = Column(String, nullable=False)
    news_title = Column(String, nullable=True)
    news_summary = Column(String, nullable=True)

    # Haiku (fast filter)
    haiku_score = Column(Float, nullable=True)
    haiku_edge_yes = Column(Float, nullable=True)
    haiku_edge_no = Column(Float, nullable=True)
    haiku_direction = Column(String, nullable=True)  # YES / NO / SKIP

    # Sonnet (deep analysis)
    sonnet_called = Column(Boolean, default=False, nullable=False)
    sonnet_direction = Column(String, nullable=True)   # YES / NO / SKIP
    sonnet_confidence = Column(String, nullable=True)  # LOW / MEDIUM / HIGH
    sonnet_edge = Column(Float, nullable=True)

    # Market
    market_id = Column(String, nullable=True)
    market_question = Column(String, nullable=True)

    # Outcome
    action_taken = Column(String, nullable=True)   # BET / SKIP / FILTERED / BLOCKED
    skip_reason = Column(String, nullable=True)


class Position(Base):
    """One row per betting position (open or closed)."""

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)

    bot_niche = Column(String, nullable=False)
    market_id = Column(String, nullable=False)
    market_question = Column(String, nullable=True)

    direction = Column(String, nullable=False)      # YES / NO
    entry_price = Column(Float, nullable=False)
    current_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)

    size_usdc = Column(Float, nullable=False)
    pnl_latent = Column(Float, default=0.0, nullable=False)
    pnl_realized = Column(Float, nullable=True)

    exit_reason = Column(String, nullable=True)     # resolution / take-profit / stop-loss / manual
    status = Column(String, default="open", nullable=False)  # open / closed

    # Signal metadata kept for analytics
    haiku_score = Column(Float, nullable=True)
    sonnet_confidence = Column(String, nullable=True)
    edge_at_entry = Column(Float, nullable=True)


class Trade(Base):
    """Ledger of individual buy/sell transactions linked to a position."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    position_id = Column(Integer, ForeignKey("positions.id", ondelete="SET NULL"), nullable=True)
    market_id = Column(String, nullable=False)
    direction = Column(String, nullable=False)       # YES / NO
    action = Column(String, nullable=False)          # buy / sell
    price = Column(Float, nullable=False)
    amount_usdc = Column(Float, nullable=False)
    paper_trading = Column(Boolean, default=True, nullable=False)


class PnlHistory(Base):
    """Daily PnL snapshot (one row per calendar day)."""

    __tablename__ = "pnl_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)

    pnl_day = Column(Float, default=0.0, nullable=False)
    pnl_cumulative = Column(Float, default=0.0, nullable=False)
    capital = Column(Float, nullable=False)
    nb_bets = Column(Integer, default=0, nullable=False)
    nb_wins = Column(Integer, default=0, nullable=False)
    nb_losses = Column(Integer, default=0, nullable=False)


class ApiUsage(Base):
    """Tracks every Claude API call for cost monitoring."""

    __tablename__ = "api_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    model = Column(String, nullable=False)      # haiku / sonnet
    bot_niche = Column(String, nullable=False)
    tokens_in = Column(Integer, default=0, nullable=False)
    tokens_out = Column(Integer, default=0, nullable=False)
    cost_usd = Column(Float, default=0.0, nullable=False)
    purpose = Column(String, nullable=True)


class KpiHistory(Base):
    """Periodic KPI snapshots per bot niche (or global when bot_niche is NULL)."""

    __tablename__ = "kpi_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False)
    period = Column(String, nullable=False)         # daily / weekly
    bot_niche = Column(String, nullable=True)       # NULL means global

    win_rate = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    roi = Column(Float, nullable=True)
    nb_bets = Column(Integer, nullable=True)
    sharpe_ratio = Column(Float, nullable=True)
    max_drawdown = Column(Float, nullable=True)
    avg_edge = Column(Float, nullable=True)
    best_bet_pnl = Column(Float, nullable=True)
    worst_bet_pnl = Column(Float, nullable=True)


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------


def init_db():
    """
    Initialise the SQLite database.

    - Creates the data/ directory if it does not exist.
    - Creates all tables if they do not exist.

    Returns
    -------
    engine : sqlalchemy.engine.Engine
    SessionLocal : sessionmaker
    """
    os.makedirs(_DB_DIR, exist_ok=True)

    db_url = f"sqlite:///{_DB_PATH}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    logger.info("Database initialised at %s", _DB_PATH)
    return engine, SessionLocal


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_monthly_api_cost(session: Session) -> float:
    """Return total API cost (USD) for the current calendar month."""
    try:
        today = date.today()
        start_of_month = today.replace(day=1)
        result = (
            session.query(func.sum(ApiUsage.cost_usd))
            .filter(ApiUsage.timestamp >= datetime.combine(start_of_month, datetime.min.time()))
            .scalar()
        )
        return float(result or 0.0)
    except Exception as exc:
        logger.error("get_monthly_api_cost failed: %s", exc)
        return 0.0


def check_daily_sonnet_limit(session: Session, bot_niche: str, max_calls: int = 5) -> bool:
    """
    Return True if the number of Sonnet calls today for *bot_niche* is strictly
    below *max_calls* (i.e. another call is allowed).
    """
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        count = (
            session.query(func.count(ApiUsage.id))
            .filter(
                ApiUsage.model == "sonnet",
                ApiUsage.bot_niche == bot_niche,
                ApiUsage.timestamp >= today_start,
            )
            .scalar()
        )
        return int(count or 0) < max_calls
    except Exception as exc:
        logger.error("check_daily_sonnet_limit failed: %s", exc)
        return False


def get_daily_exposure(session: Session) -> float:
    """Return the total USDC currently committed to open positions opened today."""
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        result = (
            session.query(func.sum(Position.size_usdc))
            .filter(
                Position.status == "open",
                Position.opened_at >= today_start,
            )
            .scalar()
        )
        return float(result or 0.0)
    except Exception as exc:
        logger.error("get_daily_exposure failed: %s", exc)
        return 0.0


def get_weekly_drawdown_pct(session: Session, current_capital: float) -> float:
    """
    Return the maximum drawdown percentage over the last 7 days.

    Compares *current_capital* against the highest daily capital recorded in the
    past week.  Returns 0.0 if there is no history yet.
    """
    try:
        week_ago = date.today() - timedelta(days=7)
        peak = (
            session.query(func.max(PnlHistory.capital))
            .filter(PnlHistory.date >= week_ago)
            .scalar()
        )
        if not peak or peak <= 0:
            return 0.0
        drawdown = (peak - current_capital) / peak * 100.0
        return max(drawdown, 0.0)
    except Exception as exc:
        logger.error("get_weekly_drawdown_pct failed: %s", exc)
        return 0.0


def get_open_positions(session: Session) -> list:
    """Return all positions with status='open'."""
    try:
        return session.query(Position).filter(Position.status == "open").all()
    except Exception as exc:
        logger.error("get_open_positions failed: %s", exc)
        return []


def get_positions_by_market(session: Session, market_id: str) -> list:
    """Return all positions (any status) for a given market ID."""
    try:
        return session.query(Position).filter(Position.market_id == market_id).all()
    except Exception as exc:
        logger.error("get_positions_by_market failed: %s", exc)
        return []


def get_daily_pnl(session: Session) -> float:
    """Return the sum of realized PnL for positions closed today."""
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        result = (
            session.query(func.sum(Position.pnl_realized))
            .filter(
                Position.status == "closed",
                Position.closed_at >= today_start,
                Position.pnl_realized.isnot(None),
            )
            .scalar()
        )
        return float(result or 0.0)
    except Exception as exc:
        logger.error("get_daily_pnl failed: %s", exc)
        return 0.0


def get_bot_kpis(session: Session, bot_niche: str) -> dict:
    """
    Return the most recent KPI row for *bot_niche*.

    Falls back to an empty dict if no data is available.
    """
    try:
        row = (
            session.query(KpiHistory)
            .filter(KpiHistory.bot_niche == bot_niche)
            .order_by(KpiHistory.date.desc(), KpiHistory.id.desc())
            .first()
        )
        if row is None:
            return {}
        return {
            "date": row.date.isoformat() if row.date else None,
            "period": row.period,
            "bot_niche": row.bot_niche,
            "win_rate": row.win_rate,
            "pnl": row.pnl,
            "roi": row.roi,
            "nb_bets": row.nb_bets,
            "sharpe_ratio": row.sharpe_ratio,
            "max_drawdown": row.max_drawdown,
            "avg_edge": row.avg_edge,
            "best_bet_pnl": row.best_bet_pnl,
            "worst_bet_pnl": row.worst_bet_pnl,
        }
    except Exception as exc:
        logger.error("get_bot_kpis failed: %s", exc)
        return {}


def record_api_call(
    session: Session,
    model: str,
    bot_niche: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    purpose: str = None,
) -> None:
    """Insert one row into api_usage and commit."""
    try:
        row = ApiUsage(
            model=model,
            bot_niche=bot_niche,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            purpose=purpose,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.error("record_api_call failed: %s", exc)
        session.rollback()


def record_signal(session: Session, **kwargs) -> Signal:
    """
    Insert one row into signals and commit.

    Accepts keyword arguments matching Signal column names.
    Returns the newly created Signal instance, or None on failure.
    """
    try:
        row = Signal(**kwargs)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row
    except Exception as exc:
        logger.error("record_signal failed: %s", exc)
        session.rollback()
        return None


def open_position(session: Session, **kwargs) -> "Position":
    """
    Create and persist a new open position.

    Accepts keyword arguments matching Position column names.
    Sets status='open' and opened_at=now() if not provided.
    Returns the Position instance, or None on failure.
    """
    try:
        kwargs.setdefault("status", "open")
        kwargs.setdefault("opened_at", datetime.utcnow())
        # current_price defaults to entry_price if not given
        if "current_price" not in kwargs and "entry_price" in kwargs:
            kwargs["current_price"] = kwargs["entry_price"]
        row = Position(**kwargs)
        session.add(row)
        session.commit()
        session.refresh(row)
        logger.info(
            "Position opened: id=%s market=%s direction=%s size=%.2f",
            row.id,
            row.market_id,
            row.direction,
            row.size_usdc,
        )
        return row
    except Exception as exc:
        logger.error("open_position failed: %s", exc)
        session.rollback()
        return None


def close_position(
    session: Session,
    position_id: int,
    exit_price: float,
    exit_reason: str,
) -> "Position":
    """
    Close an open position and compute realized PnL.

    PnL formula (works for both YES and NO tokens — we track the token price of
    the direction we bought):
        pnl = (exit_price - entry_price) * (size_usdc / entry_price)

    Returns the updated Position, or None on failure.
    """
    try:
        position = session.query(Position).filter(Position.id == position_id).first()
        if position is None:
            logger.warning("close_position: position id=%s not found", position_id)
            return None
        if position.status == "closed":
            logger.warning("close_position: position id=%s already closed", position_id)
            return position

        if position.entry_price and position.entry_price != 0:
            pnl = (exit_price - position.entry_price) * (position.size_usdc / position.entry_price)
        else:
            pnl = 0.0

        position.exit_price = exit_price
        position.exit_reason = exit_reason
        position.pnl_realized = pnl
        position.pnl_latent = 0.0
        position.status = "closed"
        position.closed_at = datetime.utcnow()
        position.current_price = exit_price

        session.commit()
        session.refresh(position)
        logger.info(
            "Position closed: id=%s pnl=%.4f exit_reason=%s",
            position.id,
            pnl,
            exit_reason,
        )
        return position
    except Exception as exc:
        logger.error("close_position failed: %s", exc)
        session.rollback()
        return None


def get_capital(session: Session) -> float:
    """
    Return current capital = CAPITAL_INITIAL + sum of all realized PnL.

    Ignores open/latent PnL — only settled gains/losses count.
    """
    try:
        realized = (
            session.query(func.sum(Position.pnl_realized))
            .filter(
                Position.status == "closed",
                Position.pnl_realized.isnot(None),
            )
            .scalar()
        )
        return CAPITAL_INITIAL + float(realized or 0.0)
    except Exception as exc:
        logger.error("get_capital failed: %s", exc)
        return CAPITAL_INITIAL


def get_closed_positions(session: Session, limit: int = 20) -> list:
    """Return the last *limit* closed positions ordered by closed_at descending."""
    try:
        return (
            session.query(Position)
            .filter(Position.status == "closed")
            .order_by(Position.closed_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_closed_positions failed: %s", exc)
        return []


def get_recent_signals(session: Session, limit: int = 30) -> list:
    """Return the last *limit* signals ordered by timestamp descending."""
    try:
        return (
            session.query(Signal)
            .order_by(Signal.timestamp.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.error("get_recent_signals failed: %s", exc)
        return []


def get_pnl_history(session: Session, days: int = 30) -> list:
    """Return PnL history rows for the last *days* calendar days, oldest first."""
    try:
        cutoff = date.today() - timedelta(days=days)
        return (
            session.query(PnlHistory)
            .filter(PnlHistory.date >= cutoff)
            .order_by(PnlHistory.date.asc())
            .all()
        )
    except Exception as exc:
        logger.error("get_pnl_history failed: %s", exc)
        return []
