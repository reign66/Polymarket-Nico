import os
import sys
import signal
import logging
import threading
import yaml
from datetime import datetime
from dotenv import load_dotenv

# Load environment
load_dotenv()

# Setup logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler()
    ]
)
# Add rotation
from logging.handlers import TimedRotatingFileHandler
file_handler = TimedRotatingFileHandler('logs/bot.log', when='midnight', backupCount=7)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
logging.getLogger().addHandler(file_handler)

logger = logging.getLogger('main')

def main():
    logger.info("=" * 60)
    logger.info("POLYMARKET BOT — Starting up")
    logger.info("=" * 60)

    # Load config
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    paper = os.environ.get('PAPER_TRADING', 'true').lower() == 'true'
    logger.info(f"Mode: {'PAPER TRADING' if paper else 'LIVE TRADING'}")
    logger.info(f"Capital initial: {os.environ.get('CAPITAL_INITIAL', 1000)}€")

    # Init database
    from core.database import init_db
    engine, SessionLocal = init_db()
    session = SessionLocal()
    logger.info("Database initialized")

    # Init core components
    from core.haiku_classifier import HaikuClassifier
    from core.sonnet_decider import SonnetDecider
    from core.polymarket_client import PolymarketClient
    from core.position_sizer import PositionSizer
    from core.risk_manager import RiskManager
    from core.exit_manager import ExitManager
    from alerts.telegram_bot import TelegramAlerter
    from alerts.email_notifier import EmailNotifier

    haiku = HaikuClassifier(session)
    sonnet = SonnetDecider(session)
    pm_client = PolymarketClient(session, paper_trading=paper)
    sizer = PositionSizer(config)
    risk = RiskManager(config, session)
    telegram = TelegramAlerter(session)
    email = EmailNotifier()
    exit_mgr = ExitManager(config, session, pm_client, telegram)

    logger.info("Core components initialized")

    # Init bots
    from bots.bot_nba import BotNBA
    from bots.bot_f1 import BotF1
    from bots.bot_crypto import BotCrypto
    from bots.bot_geopolitics import BotGeopolitics
    from bots.bot_politics import BotPolitics

    bot_kwargs = {
        'config': config,
        'db_session': session,
        'polymarket_client': pm_client,
        'haiku_classifier': haiku,
        'sonnet_decider': sonnet,
        'position_sizer': sizer,
        'risk_manager': risk,
        'telegram_alerter': telegram,
    }

    bots = {
        'nba': BotNBA(**bot_kwargs),
        'f1': BotF1(**bot_kwargs),
        'crypto': BotCrypto(**bot_kwargs),
        'geopolitics': BotGeopolitics(**bot_kwargs),
        'politics': BotPolitics(**bot_kwargs),
    }
    logger.info(f"Bots initialized: {list(bots.keys())}")

    # Init KPI tracker
    from tools.kpi_tracker import KPITracker
    kpi_tracker = KPITracker(config, session, bots)

    # Init dashboard
    from dashboard.app import app, init_dashboard, is_paused
    init_dashboard(SessionLocal, bots)

    # Setup APScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BackgroundScheduler(timezone='UTC')

    def safe_run(bot_name):
        """Wrapper to check pause state and run bot safely."""
        from dashboard.app import is_paused
        if is_paused:
            logger.info(f"[{bot_name}] Skipped (paused)")
            return
        try:
            bots[bot_name].run_cycle()
        except Exception as e:
            logger.error(f"[{bot_name}] Error: {e}", exc_info=True)

    # Bot cycles
    for bot_name, minutes in config.get('bot_cycles', {}).items():
        if bot_name in bots:
            scheduler.add_job(
                safe_run, IntervalTrigger(minutes=minutes),
                args=[bot_name], id=f'bot_{bot_name}',
                name=f'Bot {bot_name} (every {minutes}min)',
                max_instances=1
            )

    # Exit manager — every 30 min
    scheduler.add_job(
        exit_mgr.check_positions, IntervalTrigger(minutes=30),
        id='exit_manager', name='Exit Manager', max_instances=1
    )

    # Daily KPIs at 23:55
    scheduler.add_job(
        kpi_tracker.compute_daily_kpis, CronTrigger(hour=23, minute=55),
        id='daily_kpis', name='Daily KPIs'
    )

    # Daily report at 00:00
    def send_daily_report():
        try:
            stats = kpi_tracker.get_daily_report_data()
            telegram.send_daily_report(stats)
            email.send_daily_report(stats)
        except Exception as e:
            logger.error(f"Daily report error: {e}", exc_info=True)

    scheduler.add_job(
        send_daily_report, CronTrigger(hour=0, minute=0),
        id='daily_report', name='Daily Report'
    )

    # Weekly report Monday 9:00
    def send_weekly_report():
        try:
            stats = kpi_tracker.get_weekly_report_data()
            telegram.send_weekly_report(stats)
            email.send_weekly_report(stats)
        except Exception as e:
            logger.error(f"Weekly report error: {e}", exc_info=True)

    scheduler.add_job(
        send_weekly_report, CronTrigger(day_of_week='mon', hour=9, minute=0),
        id='weekly_report', name='Weekly Report'
    )

    # API cost check every hour
    def check_api_costs():
        try:
            from core.database import get_monthly_api_cost
            cost = get_monthly_api_cost(session) * 0.92  # USD to EUR approx
            limit = config.get('circuit_breakers', {}).get('max_monthly_api_cost_eur', 50)
            if cost > limit * 0.8:
                logger.warning(f"API cost warning: {cost:.2f}€ / {limit}€")
                telegram.send_circuit_breaker_alert(
                    f"API cost approaching limit: {cost:.2f}€",
                    capital=None
                )
        except Exception as e:
            logger.error(f"API cost check error: {e}")

    scheduler.add_job(
        check_api_costs, IntervalTrigger(hours=1),
        id='api_cost_check', name='API Cost Check'
    )

    scheduler.start()
    logger.info("Scheduler started with jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    # Signal handlers
    def shutdown(signum, frame):
        logger.info("Shutdown signal received...")
        scheduler.shutdown(wait=False)
        session.close()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Start Flask in thread
    port = int(os.environ.get('PORT', 5000))
    flask_thread = threading.Thread(
        target=lambda: app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False),
        daemon=True
    )
    flask_thread.start()
    logger.info(f"Dashboard running on http://0.0.0.0:{port}")

    # Send startup notification
    telegram._send_message(
        f"POLYMARKET BOT DEMARRE\n"
        f"Mode: {'PAPER' if paper else 'LIVE'}\n"
        f"Bots: {', '.join(bots.keys())}\n"
        f"Dashboard: port {port}\n"
        f"Capital: {os.environ.get('CAPITAL_INITIAL', 1000)}€"
    )

    logger.info("=" * 60)
    logger.info("Bot is running. Press Ctrl+C to stop.")
    logger.info("=" * 60)

    # Keep main thread alive
    try:
        while True:
            import time
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        shutdown(None, None)

if __name__ == '__main__':
    main()
