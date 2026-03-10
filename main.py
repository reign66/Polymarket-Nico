import os
import sys
import signal
import logging
import time
import threading
import yaml
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Setup logging to stdout (Railway captures stdout)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger('main')


def run_cycle(session, market_fetcher, mech_filter, niche_classifier,
              math_models, edge_calculator, haiku_confirmer, sonnet_decider,
              pm_client, position_sizer, risk_manager, exit_manager, telegram):
    """Main cycle: MATH FIRST, AI LAST.

    Funnel:
    1. Fetch markets          (Gamma API, free)
    2. Mechanical filter      (code, free)
    3. Classify by niche      (code/keyword, free)
    4. Math scoring ALL       (code, free)
    5. Sort by edge desc      (pick best N for Haiku)
    6. Haiku confirmation     (rare API call, ≤30/day)
    7. Sonnet decision        (very rare, ≤5/day)
    8. Execute bet
    """
    cycle_start = time.time()
    stats = {
        'fetched': 0, 'filtered': 0, 'classified': 0,
        'math_edge': 0, 'haiku_actual': 0, 'haiku_confirmed': 0,
        'sonnet_called': 0, 'bets': 0,
        'best_edge': 0.0, 'best_edge_niche': '',
    }

    try:
        # Step 1: Fetch active markets
        logger.info("=" * 50)
        from core.database import get_daily_api_calls, get_monthly_api_cost
        daily_haiku = get_daily_api_calls(session, 'haiku')
        daily_sonnet = get_daily_api_calls(session, 'sonnet')
        monthly_cost = get_monthly_api_cost(session)
        logger.info(f"CYCLE START | API today: {daily_haiku}H {daily_sonnet}S | Cost: ${monthly_cost:.4f}")
        markets = market_fetcher.fetch_active_markets()
        stats['fetched'] = len(markets)
        logger.info(f"Step 1 - Fetched: {len(markets)} markets")

        if not markets:
            logger.info("No markets fetched, ending cycle")
            return stats

        # Step 2: Mechanical filter
        filtered = mech_filter.filter_markets(markets)
        stats['filtered'] = len(filtered)
        logger.info(f"Step 2 - After filter: {len(filtered)} markets")

        if not filtered:
            logger.info("All markets filtered out, ending cycle")
            return stats

        # Step 3: Classify by niche
        classified, _ = niche_classifier.classify_batch(filtered)
        stats['classified'] = len(classified)
        clf_stats = niche_classifier.get_stats_and_reset()
        logger.info(
            f"Step 3 - Classified: {len(classified)} markets | "
            f"gamma={clf_stats['gamma']} kw={clf_stats.get('keyword', 0)} "
            f"cache={clf_stats['cache']} haiku={clf_stats['haiku']} "
            f"generic={clf_stats.get('generic', 0)}"
        )

        # Step 4: Math scoring for ALL classified markets (zero API cost)
        math_candidates = []
        skipped_math = 0
        for market in classified:
            try:
                model = math_models.get(market.niche) or math_models.get('generic')
                if not model:
                    continue
                model_result = model.calculate_probability(market, external_data={'session': session})
                if model_result is None:
                    continue
                edge_result = edge_calculator.calculate_edge(market, model_result)
                if edge_result.should_call_ai:
                    math_candidates.append((market, model_result, edge_result))
                    if edge_result.best_edge > stats['best_edge']:
                        stats['best_edge'] = edge_result.best_edge
                        stats['best_edge_niche'] = market.niche
                else:
                    skipped_math += 1
            except Exception as e:
                logger.error(f"Math error {market.market_id[:8]}: {e}", exc_info=True)

        stats['math_edge'] = len(math_candidates)

        # Sort by confidence-adjusted edge DESCENDING so best edges get Haiku first
        math_candidates.sort(key=lambda x: x[2].confidence_adjusted_edge, reverse=True)

        best_str = f"{stats['best_edge']:.1%} ({stats['best_edge_niche']})" if stats['best_edge'] > 0 else "none"
        logger.info(
            f"Step 4 - Math: {len(math_candidates)} edges found "
            f"(best={best_str}, skipped={skipped_math})"
        )

        # Steps 5-7: AI confirmation on top edges (sorted, limit enforced inside haiku_confirmer)
        for market, model_result, edge_result in math_candidates:
            try:
                _process_with_ai(
                    market, model_result, edge_result,
                    session, haiku_confirmer, sonnet_decider,
                    pm_client, position_sizer, risk_manager, telegram, stats,
                )
            except Exception as e:
                logger.error(f"AI error {market.market_id[:8]}: {e}", exc_info=True)

        # Step: Check exits
        exit_manager.check_positions()

        elapsed = time.time() - cycle_start
        best_edge_str = f" | Best: {stats['best_edge']:.1%} ({stats['best_edge_niche']})" if stats['best_edge'] > 0 else ""
        logger.info(
            f"CYCLE COMPLETE in {elapsed:.1f}s | "
            f"Funnel: {stats['fetched']}"
            f"→{stats['filtered']}"
            f"→{stats['classified']}"
            f"→{stats['math_edge']} edges"
            f"→{stats['haiku_actual']} haiku"
            f"→{stats['haiku_confirmed']} confirmed"
            f"→{stats['sonnet_called']} sonnet"
            f"→{stats['bets']} bets"
            f"{best_edge_str}"
        )
        logger.info("=" * 50)

    except Exception as e:
        logger.error(f"Cycle error: {e}", exc_info=True)

    return stats


def _process_with_ai(market, model_result, edge_result,
                     session, haiku_confirmer, sonnet_decider,
                     pm_client, position_sizer, risk_manager, telegram, stats):
    """
    Steps 5-7 for a single market: Haiku → Sonnet → execute.
    Math scoring is already done; markets arrive sorted by edge (best first).
    """
    from core.database import record_signal, get_capital

    niche = market.niche

    logger.info(
        f"[{niche.upper()}] Edge {edge_result.best_edge:.1%} "
        f"(adj {edge_result.confidence_adjusted_edge:.1%}, conf {edge_result.model_confidence:.0%}) "
        f"| {market.question[:60]}"
    )

    # Step 5: Haiku confirmation
    haiku_result = haiku_confirmer.confirm_edge(market, model_result, edge_result)

    # Count only real API calls (not limit-skip returns)
    if "limit reached" not in haiku_result.reason.lower() and "unavailable" not in haiku_result.reason.lower():
        stats['haiku_actual'] += 1
    else:
        logger.info(f"Haiku SKIPPED ({haiku_result.reason}): {market.question[:50]}")

    if not haiku_result.confirmed:
        record_signal(
            session,
            market_id=market.market_id,
            market_question=market.question,
            niche=niche,
            math_probability=model_result.get('probability', 0.5),
            math_confidence=model_result.get('confidence', 0.05),
            math_method=model_result.get('method', 'unknown'),
            math_edge=edge_result.best_edge,
            haiku_called=True,
            haiku_confirmed=False,
            haiku_adjusted_edge=haiku_result.adjusted_edge,
            funnel_step='haiku',
            skip_reason=f'Haiku: {haiku_result.reason}',
        )
        return

    stats['haiku_confirmed'] += 1

    # Step 6: Sonnet decision
    sonnet_result = sonnet_decider.decide_bet(market, model_result, edge_result, haiku_result)
    stats['sonnet_called'] += 1

    if not sonnet_result.go:
        record_signal(
            session,
            market_id=market.market_id,
            market_question=market.question,
            niche=niche,
            math_probability=model_result.get('probability', 0.5),
            math_confidence=model_result.get('confidence', 0.05),
            math_method=model_result.get('method', 'unknown'),
            math_edge=edge_result.best_edge,
            haiku_called=True,
            haiku_confirmed=True,
            haiku_adjusted_edge=haiku_result.adjusted_edge,
            sonnet_called=True,
            sonnet_go=False,
            sonnet_direction=sonnet_result.direction,
            sonnet_confidence=sonnet_result.confidence,
            sonnet_edge=sonnet_result.edge_estimate,
            funnel_step='sonnet',
            skip_reason=f'Sonnet: {sonnet_result.rationale}',
        )
        return

    # Step 7: Risk check + sizing + execution
    direction = sonnet_result.direction
    price = market.yes_price if direction == 'YES' else market.no_price
    bankroll = get_capital(session)
    amount = position_sizer.calculate_size(edge_result, bankroll)

    if amount <= 0:
        record_signal(
            session,
            market_id=market.market_id,
            market_question=market.question,
            niche=niche,
            math_probability=model_result.get('probability', 0.5),
            math_confidence=model_result.get('confidence', 0.05),
            math_method=model_result.get('method', 'unknown'),
            math_edge=edge_result.best_edge,
            haiku_called=True,
            haiku_confirmed=True,
            sonnet_called=True,
            sonnet_go=True,
            sonnet_direction=direction,
            sonnet_confidence=sonnet_result.confidence,
            funnel_step='sonnet',
            skip_reason='Kelly size = 0',
            was_bet_placed=False,
        )
        return

    signal_info = {
        'edge': edge_result.best_edge,
        'volume': market.volume,
        'confidence': sonnet_result.confidence,
    }
    approved, adj_amount, risk_reason = risk_manager.check_all(amount, market.market_id, signal_info)

    if not approved:
        record_signal(
            session,
            market_id=market.market_id,
            market_question=market.question,
            niche=niche,
            math_probability=model_result.get('probability', 0.5),
            math_confidence=model_result.get('confidence', 0.05),
            math_method=model_result.get('method', 'unknown'),
            math_edge=edge_result.best_edge,
            haiku_called=True,
            haiku_confirmed=True,
            sonnet_called=True,
            sonnet_go=True,
            sonnet_direction=direction,
            sonnet_confidence=sonnet_result.confidence,
            funnel_step='sonnet',
            skip_reason=f'Risk: {risk_reason}',
            was_bet_placed=False,
        )
        return

    # PLACE BET
    result = pm_client.place_paper_bet(
        market_id=market.market_id,
        question=market.question,
        direction=direction,
        amount=adj_amount,
        price=price,
        niche=niche,
        math_edge=edge_result.best_edge,
        confidence=sonnet_result.confidence,
    )

    if result.get('success'):
        stats['bets'] += 1
        record_signal(
            session,
            market_id=market.market_id,
            market_question=market.question,
            niche=niche,
            math_probability=model_result.get('probability', 0.5),
            math_confidence=model_result.get('confidence', 0.05),
            math_method=model_result.get('method', 'unknown'),
            math_edge=edge_result.best_edge,
            haiku_called=True,
            haiku_confirmed=True,
            haiku_adjusted_edge=haiku_result.adjusted_edge,
            sonnet_called=True,
            sonnet_go=True,
            sonnet_direction=direction,
            sonnet_confidence=sonnet_result.confidence,
            sonnet_edge=sonnet_result.edge_estimate,
            direction=direction,
            was_bet_placed=True,
            funnel_step='bet',
        )

        if telegram:
            telegram.send_entry_notification(
                niche=niche,
                question=market.question,
                direction=direction,
                price=price,
                amount=adj_amount,
                math_edge=edge_result.best_edge,
                method=model_result.get('method', 'unknown'),
                confidence_pct=model_result.get('confidence', 0.05),
                haiku_reason=haiku_result.reason,
                sonnet_confidence=sonnet_result.confidence,
                sonnet_rationale=sonnet_result.rationale,
                sonnet_risk=sonnet_result.risk,
                end_date=market.end_date,
                volume=market.volume,
            )

        logger.info(
            f"[{niche.upper()}] BET PLACED: {direction} on '{market.question[:50]}' "
            f"| ${adj_amount:.2f} @ {price:.2f} | edge={edge_result.best_edge:.1%}"
        )


def _load_math_models(config):
    """Load all math models using the registry singleton."""
    from core.math_models import get_model
    niches = ['nba', 'f1', 'crypto', 'geopolitics', 'politics', 'golf', 'generic']
    math_models = {}
    for niche in niches:
        try:
            model = get_model(niche)
            math_models[niche] = model
            logger.info(f"Loaded math model: {niche}")
        except Exception as e:
            logger.warning(f"Could not load model for {niche}: {e}")
    return math_models


def _init_dashboard(session_factory):
    """Init dashboard if dashboard/app.py exists, otherwise skip gracefully."""
    try:
        from dashboard.app import app, init_dashboard
        init_dashboard(session_factory)
        return app
    except ImportError:
        logger.warning("dashboard/app.py not found — dashboard disabled")
        return None
    except Exception as e:
        logger.error(f"Dashboard init error: {e}", exc_info=True)
        return None


def _is_paused():
    """Check dashboard pause state, defaulting to False if dashboard is unavailable."""
    try:
        from dashboard.app import is_paused
        return is_paused
    except ImportError:
        return False
    except Exception:
        return False


def main():
    logger.info("=" * 60)
    logger.info("POLYMARKET BOT V2 — MATH FIRST, AI LAST")
    logger.info("=" * 60)

    # Load config
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    paper = os.environ.get('PAPER_TRADING', 'true').lower() == 'true'
    logger.info(f"Mode: {'PAPER TRADING' if paper else 'LIVE TRADING'}")
    logger.info(f"Capital: {os.environ.get('CAPITAL_INITIAL', 1000)}€")
    logger.info(f"API limits: {config.get('api_limits', {})}")

    # Init database
    from core.database import init_db
    engine, SessionLocal = init_db()
    session = SessionLocal()
    logger.info("Database initialized")

    # Init components
    from core.market_fetcher import MarketFetcher
    from core.mechanical_filter import MechanicalFilter
    from core.niche_classifier import NicheClassifier
    from core.edge_calculator import EdgeCalculator
    from core.haiku_confirmer import HaikuConfirmer
    from core.sonnet_decider import SonnetDecider
    from core.polymarket_client import PolymarketClient
    from core.position_sizer import PositionSizer
    from core.risk_manager import RiskManager
    from core.exit_manager import ExitManager
    from alerts.telegram_bot import TelegramAlerter
    from tools.kpi_tracker import KPITracker

    market_fetcher = MarketFetcher(session)
    mech_filter = MechanicalFilter(config, session)
    # Init anthropic client for Haiku classification (optional)
    try:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY', ''))
    except Exception:
        _anthropic_client = None

    niche_classifier = NicheClassifier(config, session=session, anthropic_client=_anthropic_client)
    edge_calculator = EdgeCalculator(config)
    haiku_confirmer = HaikuConfirmer(config, session)
    sonnet_decider = SonnetDecider(config, session)
    pm_client = PolymarketClient(session)
    position_sizer = PositionSizer(config)
    risk_manager = RiskManager(config, session)
    telegram = TelegramAlerter(session)
    exit_manager = ExitManager(config, session, pm_client, telegram)
    kpi_tracker = KPITracker(config, session)

    logger.info("Core components initialized")

    # Init math models (missing models are skipped gracefully)
    math_models = _load_math_models(config)
    logger.info(f"Math models loaded: {list(math_models.keys())}")

    # Init dashboard (optional — skipped if dashboard/app.py does not exist yet)
    flask_app = _init_dashboard(SessionLocal)

    # Setup APScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BackgroundScheduler(timezone='UTC')

    def safe_cycle():
        if _is_paused():
            logger.info("Cycle skipped (paused via dashboard)")
            return
        run_cycle(
            session, market_fetcher, mech_filter, niche_classifier,
            math_models, edge_calculator, haiku_confirmer, sonnet_decider,
            pm_client, position_sizer, risk_manager, exit_manager, telegram,
        )

    cycle_minutes = config.get('cycle_interval_minutes', 30)
    scheduler.add_job(
        safe_cycle,
        IntervalTrigger(minutes=cycle_minutes),
        id='main_cycle',
        name=f'Main Cycle (every {cycle_minutes}min)',
        max_instances=1,
    )

    # Elo ratings update every 2h (only if nba model is loaded)
    def update_elo():
        try:
            nba_model = math_models.get('nba')
            if nba_model and hasattr(nba_model, 'update_ratings'):
                nba_model.update_ratings()
        except Exception as e:
            logger.error(f"Elo update error: {e}", exc_info=True)

    scheduler.add_job(
        update_elo,
        IntervalTrigger(hours=2),
        id='elo_update',
        name='Elo Update (2h)',
    )

    # Daily KPIs at 23:55 UTC
    scheduler.add_job(
        kpi_tracker.compute_daily_kpis,
        CronTrigger(hour=23, minute=55),
        id='daily_kpis',
        name='Daily KPIs',
    )

    # Daily report at 00:00 UTC
    def daily_report():
        try:
            stats = kpi_tracker.get_daily_report_data()
            telegram.send_daily_report(stats)
        except Exception as e:
            logger.error(f"Daily report error: {e}", exc_info=True)

    scheduler.add_job(
        daily_report,
        CronTrigger(hour=0, minute=0),
        id='daily_report',
        name='Daily Report',
    )

    # Weekly report every Monday at 09:00 UTC
    def weekly_report():
        try:
            stats = kpi_tracker.get_weekly_report_data()
            telegram.send_weekly_report(stats)
        except Exception as e:
            logger.error(f"Weekly report error: {e}", exc_info=True)

    scheduler.add_job(
        weekly_report,
        CronTrigger(day_of_week='mon', hour=9, minute=0),
        id='weekly_report',
        name='Weekly Report',
    )

    # Cache cleanup every 6h
    def cleanup():
        try:
            from core.database import cleanup_db
            cleanup_db(session)
        except Exception as e:
            logger.error(f"Cleanup error: {e}", exc_info=True)

    scheduler.add_job(
        cleanup,
        IntervalTrigger(hours=6),
        id='cache_cleanup',
        name='Cache Cleanup (6h)',
    )

    # API cost check every hour
    def check_api_costs():
        try:
            from core.database import get_monthly_api_cost
            cost = get_monthly_api_cost(session) * 0.92  # USD to EUR approx
            limit = config.get('api_limits', {}).get('max_monthly_cost_eur', 5)
            if cost > limit * 0.8:
                logger.warning(f"API cost warning: {cost:.4f}€ / {limit}€")
                telegram.send_circuit_breaker_alert(
                    f"API cost approaching limit: {cost:.4f}€ / {limit}€"
                )
        except Exception as e:
            logger.error(f"API cost check error: {e}", exc_info=True)

    scheduler.add_job(
        check_api_costs,
        IntervalTrigger(hours=1),
        id='api_cost_check',
        name='API Cost Check (1h)',
    )

    scheduler.start()
    logger.info("Scheduler started:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    # Signal handlers
    def shutdown(signum, frame):
        logger.info("Shutdown signal received — stopping...")
        scheduler.shutdown(wait=False)
        session.close()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Start Flask dashboard in background thread (if available)
    port = int(os.environ.get('PORT', 5000))
    if flask_app is not None:
        flask_thread = threading.Thread(
            target=lambda: flask_app.run(
                host='0.0.0.0', port=port, debug=False, use_reloader=False
            ),
            daemon=True,
        )
        flask_thread.start()
        logger.info(f"Dashboard: http://0.0.0.0:{port}")
    else:
        logger.info("Dashboard not started (app not available)")

    # Startup Telegram notification
    api_limits = config.get('api_limits', {})
    telegram._send(
        f"POLYMARKET BOT V2 DEMARRE\n"
        f"Mode: {'PAPER' if paper else 'LIVE'}\n"
        f"Cycle: {cycle_minutes}min\n"
        f"Modeles: {', '.join(math_models.keys())}\n"
        f"Capital: {os.environ.get('CAPITAL_INITIAL', 1000)}€\n"
        f"API max: {api_limits.get('max_haiku_calls_per_day', '?')}H"
        f"/{api_limits.get('max_sonnet_calls_per_day', '?')}S par jour"
    )

    # Run first cycle immediately on startup
    logger.info("Running initial cycle...")
    safe_cycle()

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Keep main thread alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        shutdown(None, None)


if __name__ == '__main__':
    main()
