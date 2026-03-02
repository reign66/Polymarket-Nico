import os
import logging
from flask import Flask, render_template, jsonify, request
from datetime import datetime

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Will be set by main.py
db_session_factory = None
bot_instances = {}
is_paused = False


def init_dashboard(session_factory, bots=None):
    global db_session_factory, bot_instances
    db_session_factory = session_factory
    bot_instances = bots or {}


def get_session():
    if db_session_factory:
        return db_session_factory()
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/overview')
def api_overview():
    session = get_session()
    if not session:
        return jsonify({'error': 'No database session'}), 500
    try:
        from core.database import (
            get_capital, get_open_positions, get_monthly_api_cost,
            get_pnl_history, get_closed_positions
        )
        capital = get_capital(session)
        open_pos = get_open_positions(session)
        pnl_latent = sum(p.pnl_latent or 0 for p in open_pos)
        initial = float(os.environ.get('CAPITAL_INITIAL', 1000))
        pnl_total = capital - initial + pnl_latent
        api_cost = get_monthly_api_cost(session)

        # Win rate 30d
        closed = get_closed_positions(session, limit=100)
        wins = sum(1 for p in closed if (p.pnl_realized or 0) > 0)
        total = len(closed)
        win_rate = wins / total if total > 0 else 0

        roi_month = (capital - initial) / initial if initial > 0 else 0

        return jsonify({
            'capital': round(capital, 2),
            'pnl_total': round(pnl_total, 2),
            'pnl_latent': round(pnl_latent, 2),
            'open_positions': len(open_pos),
            'win_rate_30d': round(win_rate, 4),
            'roi_month': round(roi_month, 4),
            'api_cost_month': round(api_cost, 4),
            'paper_trading': os.environ.get('PAPER_TRADING', 'true').lower() == 'true'
        })
    except Exception as e:
        logger.error(f"API overview error: {e}")
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/positions')
def api_positions():
    session = get_session()
    if not session:
        return jsonify([])
    try:
        from core.database import get_open_positions
        positions = get_open_positions(session)
        return jsonify([{
            'id': p.id,
            'bot_niche': p.bot_niche,
            'market_question': p.market_question,
            'direction': p.direction,
            'entry_price': p.entry_price,
            'current_price': p.current_price,
            'size_usdc': p.size_usdc,
            'pnl_latent': round(p.pnl_latent or 0, 2),
            'opened_at': p.opened_at.isoformat() if p.opened_at else None,
            'haiku_score': p.haiku_score,
            'edge_at_entry': p.edge_at_entry
        } for p in positions])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/positions/history')
def api_positions_history():
    session = get_session()
    if not session:
        return jsonify([])
    try:
        from core.database import get_closed_positions
        positions = get_closed_positions(session, limit=20)
        return jsonify([{
            'id': p.id,
            'bot_niche': p.bot_niche,
            'market_question': p.market_question,
            'direction': p.direction,
            'entry_price': p.entry_price,
            'exit_price': p.exit_price,
            'size_usdc': p.size_usdc,
            'pnl_realized': round(p.pnl_realized or 0, 2),
            'exit_reason': p.exit_reason,
            'opened_at': p.opened_at.isoformat() if p.opened_at else None,
            'closed_at': p.closed_at.isoformat() if p.closed_at else None
        } for p in positions])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/signals')
def api_signals():
    session = get_session()
    if not session:
        return jsonify([])
    try:
        from core.database import get_recent_signals
        signals = get_recent_signals(session, limit=30)
        return jsonify([{
            'id': s.id,
            'timestamp': s.timestamp.isoformat() if s.timestamp else None,
            'bot_niche': s.bot_niche,
            'news_title': s.news_title,
            'haiku_score': s.haiku_score,
            'haiku_direction': s.haiku_direction,
            'sonnet_called': s.sonnet_called,
            'sonnet_direction': s.sonnet_direction,
            'sonnet_confidence': s.sonnet_confidence,
            'sonnet_edge': s.sonnet_edge,
            'action_taken': s.action_taken,
            'skip_reason': s.skip_reason,
            'market_question': s.market_question
        } for s in signals])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/stats/niches')
def api_stats_niches():
    session = get_session()
    if not session:
        return jsonify({})
    try:
        from core.database import get_bot_kpis
        niches = ['nba', 'f1', 'crypto', 'geopolitics', 'politics']
        stats = {}
        for niche in niches:
            kpis = get_bot_kpis(session, niche)
            stats[niche] = kpis
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/pnl-history')
def api_pnl_history():
    session = get_session()
    if not session:
        return jsonify([])
    try:
        from core.database import get_pnl_history
        history = get_pnl_history(session, days=30)
        return jsonify([{
            'date': h.date.isoformat() if hasattr(h.date, 'isoformat') else str(h.date),
            'pnl_day': h.pnl_day,
            'pnl_cumulative': h.pnl_cumulative,
            'capital': h.capital
        } for h in history])
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/kpis')
def api_kpis():
    return api_stats_niches()


@app.route('/api/api-costs')
def api_api_costs():
    session = get_session()
    if not session:
        return jsonify({})
    try:
        from core.database import get_monthly_api_cost
        cost = get_monthly_api_cost(session)
        return jsonify({
            'monthly_cost_usd': round(cost, 4),
            'monthly_cost_eur': round(cost * 0.92, 4),
            'budget_eur': 15,
            'usage_pct': round((cost * 0.92) / 15 * 100, 1)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/circuit-breakers')
def api_circuit_breakers():
    session = get_session()
    if not session:
        return jsonify({})
    try:
        from core.database import (
            get_capital, get_daily_pnl, get_weekly_drawdown_pct,
            get_open_positions, get_monthly_api_cost
        )
        capital = get_capital(session)
        daily_pnl = get_daily_pnl(session)
        weekly_dd = get_weekly_drawdown_pct(session, capital)
        open_pos = len(get_open_positions(session))
        api_cost = get_monthly_api_cost(session) * 0.92

        return jsonify({
            'daily_loss': {
                'current': round(abs(min(daily_pnl, 0)) / capital * 100, 2) if capital > 0 else 0,
                'limit': 10,
                'ok': abs(min(daily_pnl, 0)) < capital * 0.10
            },
            'weekly_drawdown': {
                'current': round(weekly_dd * 100, 2),
                'limit': 25,
                'ok': weekly_dd < 0.25
            },
            'open_positions': {
                'current': open_pos,
                'limit': 5,
                'ok': open_pos < 5
            },
            'api_cost': {
                'current': round(api_cost, 2),
                'limit': 15,
                'ok': api_cost < 15
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        session.close()


@app.route('/api/improvements')
def api_improvements():
    suggestions = {}
    for name, bot in bot_instances.items():
        try:
            suggestions[name] = bot.get_improvement_suggestions()
        except Exception:
            suggestions[name] = []
    return jsonify(suggestions)


@app.route('/api/pause', methods=['POST'])
def api_pause():
    global is_paused
    is_paused = True
    logger.info("Dashboard: all bots paused")
    return jsonify({'status': 'paused'})


@app.route('/api/resume', methods=['POST'])
def api_resume():
    global is_paused
    is_paused = False
    logger.info("Dashboard: all bots resumed")
    return jsonify({'status': 'running'})


@app.route('/api/status')
def api_status():
    return jsonify({
        'paused': is_paused,
        'paper_trading': os.environ.get('PAPER_TRADING', 'true').lower() == 'true'
    })
