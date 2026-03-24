"""
dashboard/app.py — V2 Dashboard Flask Application
"MATH FIRST, AI LAST" Polymarket Trading Bot

Serves the monitoring dashboard with real-time data from the SQLite database.
All routes handle missing/None session gracefully.
"""

import os
import json
import logging
from flask import Flask, render_template, jsonify, request
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

app = Flask(__name__)

db_session_factory = None
is_paused = False

# EUR/USD conversion rate (approximate)
EUR_USD_RATE = 0.92

# Budget limit in EUR for circuit breaker
API_BUDGET_EUR = float(os.environ.get("API_BUDGET_EUR", 50.0))

# Paper trading flag
PAPER_TRADING = os.environ.get("PAPER_TRADING", "true").lower() in ("true", "1", "yes")


def init_dashboard(session_factory):
    """Register the session factory used by all routes."""
    global db_session_factory
    db_session_factory = session_factory


def get_session():
    """Return a new DB session or None if no factory is registered."""
    if db_session_factory:
        return db_session_factory()
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _close_session(session):
    """Safely close a SQLAlchemy session."""
    try:
        if session:
            session.close()
    except Exception:
        pass


def _fmt_float(value, default=0.0):
    """Safely convert a value to float."""
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _fmt_int(value, default=0):
    """Safely convert a value to int."""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _position_age(entry_time):
    """Return human-readable age string from entry_time datetime."""
    if not entry_time:
        return "—"
    try:
        delta = datetime.utcnow() - entry_time
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        if hours >= 24:
            days = hours // 24
            return f"{days}d {hours % 24}h"
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "—"


def _duration_str(entry_time, exit_time):
    """Return duration string between two datetimes."""
    if not entry_time or not exit_time:
        return "—"
    try:
        delta = exit_time - entry_time
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        if hours >= 24:
            days = hours // 24
            return f"{days}d"
        if hours > 0:
            return f"{hours}h"
        return f"{minutes}m"
    except Exception:
        return "—"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the main dashboard page."""
    return render_template("index.html")


@app.route("/api/overview")
def api_overview():
    """
    Global KPI overview.

    Returns:
        capital, pnl_total, pnl_latent, open_positions,
        win_rate_30d, roi_month, api_cost_month, paper_trading
    """
    session = None
    try:
        from core.database import (
            get_capital,
            get_open_positions,
            get_closed_positions,
            get_monthly_api_cost,
        )

        session = get_session()
        if not session:
            return jsonify({
                "capital": 0.0, "pnl_total": 0.0, "pnl_latent": 0.0,
                "open_positions": 0, "win_rate_30d": 0.0, "roi_month": 0.0,
                "api_cost_month": 0.0, "paper_trading": PAPER_TRADING
            })

        capital = get_capital(session)
        initial = float(os.environ.get("CAPITAL_INITIAL", 1000.0))
        pnl_total = capital - initial

        open_positions = get_open_positions(session)
        pnl_latent = sum(
            _fmt_float(p.pnl_unrealized) for p in open_positions
        )

        # 30-day win rate
        since_30d = datetime.utcnow() - timedelta(days=30)
        from core.database import Position
        from sqlalchemy import func
        closed_30d = (
            session.query(Position)
            .filter(
                Position.status == "closed",
                Position.exit_time >= since_30d,
                Position.pnl_realized.isnot(None),
            )
            .all()
        )
        total_30d = len(closed_30d)
        wins_30d = sum(1 for p in closed_30d if _fmt_float(p.pnl_realized) > 0)
        win_rate_30d = (wins_30d / total_30d * 100.0) if total_30d > 0 else 0.0

        # Monthly ROI
        month_start = datetime(datetime.utcnow().year, datetime.utcnow().month, 1)
        closed_month = (
            session.query(Position)
            .filter(
                Position.status == "closed",
                Position.exit_time >= month_start,
                Position.pnl_realized.isnot(None),
            )
            .all()
        )
        pnl_month = sum(_fmt_float(p.pnl_realized) for p in closed_month)
        invested_month = sum(_fmt_float(p.amount_usdc) for p in closed_month)
        roi_month = (pnl_month / invested_month * 100.0) if invested_month > 0 else 0.0

        api_cost_month = get_monthly_api_cost(session)

        return jsonify({
            "capital": round(capital, 2),
            "pnl_total": round(pnl_total, 2),
            "pnl_latent": round(pnl_latent, 2),
            "open_positions": len(open_positions),
            "win_rate_30d": round(win_rate_30d, 1),
            "roi_month": round(roi_month, 2),
            "api_cost_month": round(api_cost_month, 4),
            "paper_trading": PAPER_TRADING,
        })

    except Exception as exc:
        logger.error("api_overview error: %s", exc)
        return jsonify({
            "capital": 0.0, "pnl_total": 0.0, "pnl_latent": 0.0,
            "open_positions": 0, "win_rate_30d": 0.0, "roi_month": 0.0,
            "api_cost_month": 0.0, "paper_trading": PAPER_TRADING,
            "error": str(exc)
        })
    finally:
        _close_session(session)


@app.route("/api/positions")
def api_positions():
    """Return all open positions with P&L details."""
    session = None
    try:
        from core.database import get_open_positions

        session = get_session()
        if not session:
            return jsonify([])

        positions = get_open_positions(session)
        result = []
        for p in positions:
            pnl = _fmt_float(p.pnl_unrealized)
            entry = _fmt_float(p.entry_price)
            current = _fmt_float(p.current_price)
            pnl_pct = ((current - entry) / entry * 100.0) if entry > 0 else 0.0

            result.append({
                "id": p.id,
                "niche": p.bot_niche or "—",
                "market": (p.market_question or p.market_id or "—")[:80],
                "direction": p.direction or "—",
                "entry_price": round(entry, 4),
                "current_price": round(current, 4),
                "size": round(_fmt_float(p.amount_usdc), 2),
                "pnl": round(pnl, 4),
                "pnl_pct": round(pnl_pct, 2),
                "age": _position_age(p.entry_time),
                "edge": round(_fmt_float(p.math_edge) * 100, 1),
            })

        return jsonify(result)

    except Exception as exc:
        logger.error("api_positions error: %s", exc)
        return jsonify({"error": str(exc), "data": []})
    finally:
        _close_session(session)


@app.route("/api/positions/history")
def api_positions_history():
    """Return last 20 closed positions."""
    session = None
    try:
        from core.database import get_closed_positions

        session = get_session()
        if not session:
            return jsonify([])

        positions = get_closed_positions(session, limit=20)
        result = []
        for p in positions:
            entry = _fmt_float(p.entry_price)
            exit_p = _fmt_float(p.exit_price)
            pnl = _fmt_float(p.pnl_realized)

            result.append({
                "id": p.id,
                "niche": p.bot_niche or "—",
                "market": (p.market_question or p.market_id or "—")[:80],
                "direction": p.direction or "—",
                "entry_price": round(entry, 4),
                "exit_price": round(exit_p, 4),
                "pnl": round(pnl, 4),
                "duration": _duration_str(p.entry_time, p.exit_time),
                "exit_reason": p.exit_reason or "—",
                "exit_time": p.exit_time.isoformat() if p.exit_time else None,
            })

        return jsonify(result)

    except Exception as exc:
        logger.error("api_positions_history error: %s", exc)
        return jsonify({"error": str(exc), "data": []})
    finally:
        _close_session(session)


@app.route("/api/signals")
def api_signals():
    """Return last 30 signals with full pipeline data."""
    session = None
    try:
        from core.database import get_recent_signals

        session = get_session()
        if not session:
            return jsonify([])

        signals = get_recent_signals(session, limit=30)
        result = []
        for s in signals:
            # Determine badge type
            if s.was_bet_placed:
                badge = "BET"
            elif s.funnel_step == "filtered":
                badge = "FILTERED"
            elif s.funnel_step in ("fetched",):
                badge = "SCANNED"
            elif s.sonnet_called and not s.sonnet_go:
                badge = "BLOCKED"
            else:
                badge = "SKIP"

            result.append({
                "id": s.id,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "niche": s.niche or "—",
                "market": (s.market_question or s.market_id or "—")[:100],
                "math_edge": round(_fmt_float(s.math_edge) * 100, 1),
                "math_probability": round(_fmt_float(s.math_probability) * 100, 1),
                "math_method": s.math_method or "—",
                "haiku_called": bool(s.haiku_called),
                "haiku_confirmed": s.haiku_confirmed,
                "sonnet_called": bool(s.sonnet_called),
                "sonnet_go": s.sonnet_go,
                "was_bet_placed": bool(s.was_bet_placed),
                "skip_reason": s.skip_reason or "",
                "funnel_step": s.funnel_step or "—",
                "badge": badge,
                "direction": s.direction or s.sonnet_direction or "—",
            })

        return jsonify(result)

    except Exception as exc:
        logger.error("api_signals error: %s", exc)
        return jsonify({"error": str(exc), "data": []})
    finally:
        _close_session(session)


@app.route("/api/stats/niches")
def api_stats_niches():
    """Return KPIs per niche: win_rate, pnl, roi, total_bets, model_accuracy."""
    session = None
    try:
        from core.database import get_bot_kpis

        session = get_session()

        niches = ["NBA", "F1", "Crypto", "Geo", "Politics"]

        # Model accuracy suggestions per niche
        model_map = {
            "NBA": "Elo",
            "F1": "Poisson",
            "Crypto": "GBM",
            "Geo": "BaseRate",
            "Politics": "Ensemble",
        }

        result = {}
        for niche in niches:
            if session:
                kpis = get_bot_kpis(session, niche)
            else:
                kpis = {"win_rate": 0.0, "total_bets": 0, "pnl": 0.0, "roi": 0.0}

            # Fetch model accuracy from signals for this niche
            model_accuracy = 0.0
            prediction_count = 0
            if session:
                try:
                    from core.database import Signal
                    from sqlalchemy import func
                    signals = (
                        session.query(Signal)
                        .filter(
                            Signal.niche == niche,
                            Signal.was_bet_placed == True,
                        )
                        .all()
                    )
                    prediction_count = len(signals)
                    if prediction_count > 0:
                        correct = sum(
                            1 for s in signals
                            if s.haiku_confirmed is True or s.sonnet_go is True
                        )
                        model_accuracy = round(correct / prediction_count * 100, 1)
                except Exception:
                    pass

            result[niche] = {
                "win_rate": round(kpis.get("win_rate", 0.0) * 100, 1),
                "total_bets": kpis.get("total_bets", 0),
                "pnl": kpis.get("pnl", 0.0),
                "roi": kpis.get("roi", 0.0),
                "model": model_map.get(niche, "—"),
                "model_accuracy": model_accuracy,
                "prediction_count": prediction_count,
            }

        return jsonify(result)

    except Exception as exc:
        logger.error("api_stats_niches error: %s", exc)
        return jsonify({"error": str(exc)})
    finally:
        _close_session(session)


@app.route("/api/pnl-history")
def api_pnl_history():
    """Return 30-day daily P&L for chart."""
    session = None
    try:
        from core.database import get_pnl_history

        session = get_session()
        if not session:
            return jsonify({"labels": [], "daily": [], "cumulative": []})

        history = get_pnl_history(session, days=30)

        labels = [entry["date"] for entry in history]
        daily = [entry["pnl"] for entry in history]

        # Build cumulative from daily
        cumulative = []
        running = 0.0
        for v in daily:
            running += v
            cumulative.append(round(running, 4))

        return jsonify({
            "labels": labels,
            "daily": daily,
            "cumulative": cumulative,
        })

    except Exception as exc:
        logger.error("api_pnl_history error: %s", exc)
        return jsonify({"labels": [], "daily": [], "cumulative": [], "error": str(exc)})
    finally:
        _close_session(session)


@app.route("/api/kpis")
def api_kpis():
    """Alias for /api/stats/niches."""
    return api_stats_niches()


@app.route("/api/api-costs")
def api_api_costs():
    """Return API cost breakdown: monthly total, per-model daily usage."""
    session = None
    try:
        from core.database import get_monthly_api_cost

        session = get_session()
        if not session:
            return jsonify({
                "monthly_cost_usd": 0.0, "monthly_cost_eur": 0.0,
                "budget_eur": API_BUDGET_EUR, "usage_pct": 0.0,
                "haiku_today": 0, "sonnet_today": 0,  # disabled
            })

        monthly_cost_usd = get_monthly_api_cost(session)
        monthly_cost_eur = round(monthly_cost_usd * EUR_USD_RATE, 4)
        usage_pct = round(monthly_cost_eur / API_BUDGET_EUR * 100, 1) if API_BUDGET_EUR > 0 else 0.0

        return jsonify({
            "monthly_cost_usd": round(monthly_cost_usd, 4),
            "monthly_cost_eur": monthly_cost_eur,
            "budget_eur": API_BUDGET_EUR,
            "usage_pct": usage_pct,
            "haiku_today": 0,
            "sonnet_today": 0,
        })

    except Exception as exc:
        logger.error("api_api_costs error: %s", exc)
        return jsonify({
            "monthly_cost_usd": 0.0, "monthly_cost_eur": 0.0,
            "budget_eur": API_BUDGET_EUR, "usage_pct": 0.0,
            "haiku_today": 0, "sonnet_today": 0,
            "error": str(exc)
        })
    finally:
        _close_session(session)


@app.route("/api/circuit-breakers")
def api_circuit_breakers():
    """Return status of each circuit breaker with current value, limit, and ok flag."""
    session = None
    try:
        from core.database import (
            get_capital,
            get_open_positions,
            get_monthly_api_cost,
            get_weekly_drawdown_pct,
        )

        session = get_session()

        # Config from env
        max_daily_loss_pct = float(os.environ.get("MAX_DAILY_LOSS_PCT", 5.0))
        max_weekly_drawdown_pct = float(os.environ.get("MAX_WEEKLY_DRAWDOWN_PCT", 15.0))
        max_open_positions = int(os.environ.get("MAX_OPEN_POSITIONS", 10))

        if not session:
            return jsonify({
                "daily_loss": {"current": 0.0, "limit": max_daily_loss_pct, "ok": True, "pct": 0.0},
                "weekly_drawdown": {"current": 0.0, "limit": max_weekly_drawdown_pct, "ok": True, "pct": 0.0},
                "open_positions": {"current": 0, "limit": max_open_positions, "ok": True, "pct": 0.0},
                "api_cost": {"current": 0.0, "limit": API_BUDGET_EUR, "ok": True, "pct": 0.0},
            })

        capital = get_capital(session)

        # Daily loss: compare today's realized PnL vs capital
        today_start = datetime(datetime.utcnow().year, datetime.utcnow().month, datetime.utcnow().day)
        from core.database import Position
        from sqlalchemy import func

        today_pnl = (
            session.query(func.sum(Position.pnl_realized))
            .filter(
                Position.status == "closed",
                Position.exit_time >= today_start,
                Position.pnl_realized.isnot(None),
            )
            .scalar()
        )
        today_pnl = _fmt_float(today_pnl)
        daily_loss_pct = abs(min(today_pnl, 0)) / capital * 100 if capital > 0 else 0.0

        # Weekly drawdown
        weekly_dd = get_weekly_drawdown_pct(session, capital)

        # Open positions count
        open_pos = get_open_positions(session)
        open_count = len(open_pos)

        # API cost
        monthly_cost_usd = get_monthly_api_cost(session)
        monthly_cost_eur = monthly_cost_usd * EUR_USD_RATE

        def _pct_of_limit(current, limit):
            return round(current / limit * 100, 1) if limit > 0 else 0.0

        return jsonify({
            "daily_loss": {
                "current": round(daily_loss_pct, 2),
                "limit": max_daily_loss_pct,
                "ok": daily_loss_pct < max_daily_loss_pct,
                "pct": _pct_of_limit(daily_loss_pct, max_daily_loss_pct),
            },
            "weekly_drawdown": {
                "current": round(weekly_dd, 2),
                "limit": max_weekly_drawdown_pct,
                "ok": weekly_dd < max_weekly_drawdown_pct,
                "pct": _pct_of_limit(weekly_dd, max_weekly_drawdown_pct),
            },
            "open_positions": {
                "current": open_count,
                "limit": max_open_positions,
                "ok": open_count < max_open_positions,
                "pct": _pct_of_limit(open_count, max_open_positions),
            },
            "api_cost": {
                "current": round(monthly_cost_eur, 4),
                "limit": API_BUDGET_EUR,
                "ok": monthly_cost_eur < API_BUDGET_EUR,
                "pct": _pct_of_limit(monthly_cost_eur, API_BUDGET_EUR),
            },
        })

    except Exception as exc:
        logger.error("api_circuit_breakers error: %s", exc)
        return jsonify({"error": str(exc)})
    finally:
        _close_session(session)


@app.route("/api/funnel")
def api_funnel():
    """
    V2.1 Funnel stats for today.

    Returns counts per pipeline step:
    fetched → filtered → classified → math_edge → haiku → sonnet → bet
    Also returns classifier_cache stats from niche_cache table.
    """
    session = None
    try:
        from core.database import get_funnel_stats

        session = get_session()
        if not session:
            return jsonify({
                "fetched": 0, "filtered": 0, "classified": 0,
                "math_edge": 0, "haiku": 0, "sonnet": 0, "bet": 0,
            })

        stats = get_funnel_stats(session)

        # Classifier stats from niche_cache table
        try:
            from core.database import NicheCache
            total_cached = session.query(NicheCache).filter(NicheCache.market_active == True).count()
            gamma_count = session.query(NicheCache).filter(
                NicheCache.classified_by.like('gamma%'),
                NicheCache.market_active == True
            ).count()
            haiku_count = session.query(NicheCache).filter(
                NicheCache.classified_by == 'haiku',
                NicheCache.market_active == True
            ).count()
        except Exception:
            total_cached = 0
            gamma_count = 0
            haiku_count = 0

        return jsonify({
            'funnel': stats,
            'classifier_cache': {
                'total': total_cached,
                'via_gamma': gamma_count,
                'via_haiku': haiku_count,
                'cache_hit_rate': (gamma_count + haiku_count) / max(total_cached, 1),
            },
            # Keep top-level keys for backward compatibility with existing funnel JS
            **stats,
        })

    except Exception as exc:
        logger.error("api_funnel error: %s", exc)
        return jsonify({
            "fetched": 0, "filtered": 0, "classified": 0,
            "math_edge": 0, "haiku": 0, "sonnet": 0, "bet": 0,
            "funnel": {}, "classifier_cache": {},
            "error": str(exc)
        })
    finally:
        _close_session(session)


@app.route("/api/improvements")
def api_improvements():
    """Return improvement suggestions per niche (curated recommendations)."""
    suggestions = {
        "NBA": [
            "Augmenter le seuil d'edge minimum à 8% (actuellement 5%)",
            "Exclure les matchs avec moins de 72h de repos équipe",
            "Filtrer les marchés avec liquidité < 5000 USDC",
            "Intégrer les stats de blessures en temps réel",
        ],
        "F1": [
            "Prendre en compte les conditions météo sur le circuit",
            "Pondérer l'avantage piste (Monaco vs Monza)",
            "Ajouter les qualifications comme signal predictif",
            "Modèle Poisson plus précis pour les DNF",
        ],
        "Crypto": [
            "Réduire l'exposition max à 3% du capital par position",
            "Intégrer les données on-chain (volume DEX, flows exchanges)",
            "Éviter les marchés pendant les périodes de haute volatilité (VIX crypto > 80)",
            "GBM calibré sur 90 jours plutôt que 30 jours",
        ],
        "Geo": [
            "Sourcer des données d'experts géopolitiques via API",
            "Augmenter le nombre de prédictions (actuellement trop faible)",
            "Limiter aux événements avec resolution < 30 jours",
            "Vérifier la corrélation avec les marchés Metaculus",
        ],
        "Politics": [
            "Intégrer les données de sondages (538, PredictIt)",
            "Éviter les marchés à moins de 7 jours de l'événement",
            "Pondérer l'historique Sonnet sur les élections passées",
            "Limiter le budget à 1% du capital sur ce niche (haute incertitude)",
        ],
    }
    return jsonify(suggestions)


@app.route("/api/model-accuracy")
def api_model_accuracy():
    """
    V2.1 Per-niche model accuracy over the last 7 days.

    Returns edges found, haiku calls, bets placed and average confidence
    for each niche that had any activity.
    """
    session = None
    try:
        from core.database import Signal
        from sqlalchemy import func

        session = get_session()
        if not session:
            return jsonify([])

        since = datetime.utcnow() - timedelta(days=7)

        niches = ['nba', 'f1', 'crypto', 'geopolitics', 'politics', 'golf', 'soccer', 'mma', 'other']
        result = []

        for niche in niches:
            # Signals with edge found
            total_edge = session.query(func.count(Signal.id)).filter(
                Signal.niche == niche,
                Signal.timestamp >= since,
                Signal.funnel_step == 'math_edge',
            ).scalar() or 0

            # Signals that went to Haiku
            haiku_calls = session.query(func.count(Signal.id)).filter(
                Signal.niche == niche,
                Signal.timestamp >= since,
                Signal.haiku_called == True,
            ).scalar() or 0

            # Bets placed
            bets = session.query(func.count(Signal.id)).filter(
                Signal.niche == niche,
                Signal.timestamp >= since,
                Signal.was_bet_placed == True,
            ).scalar() or 0

            # Average confidence
            avg_conf = session.query(func.avg(Signal.math_confidence)).filter(
                Signal.niche == niche,
                Signal.timestamp >= since,
                Signal.math_confidence.isnot(None),
            ).scalar() or 0

            if total_edge > 0 or bets > 0:
                result.append({
                    'niche': niche,
                    'edges_found': total_edge,
                    'haiku_calls': haiku_calls,
                    'bets': bets,
                    'avg_confidence': round(float(avg_conf), 3),
                })

        return jsonify(result)

    except Exception as exc:
        logger.error("api_model_accuracy error: %s", exc)
        return jsonify([])
    finally:
        _close_session(session)


@app.route("/api/status")
def api_status():
    """Return current bot status: paused flag and paper trading mode."""
    return jsonify({
        "paused": is_paused,
        "paper_trading": PAPER_TRADING,
    })


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """Pause all bot activity."""
    global is_paused
    try:
        is_paused = True
        logger.info("Dashboard: bot PAUSED by user")
        return jsonify({"ok": True, "paused": True})
    except Exception as exc:
        logger.error("api_pause error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/resume", methods=["POST"])
def api_resume():
    """Resume all bot activity."""
    global is_paused
    try:
        is_paused = False
        logger.info("Dashboard: bot RESUMED by user")
        return jsonify({"ok": True, "paused": False})
    except Exception as exc:
        logger.error("api_resume error: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500
