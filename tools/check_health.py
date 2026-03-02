import os
import sys
import requests
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def check_health():
    results = {}
    total = 0
    passed = 0

    # 1. Check .env exists or env vars are set
    total += 1
    if os.environ.get('ANTHROPIC_API_KEY') or os.path.exists(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    ):
        results['env_config'] = 'PASS'
        passed += 1
    else:
        results['env_config'] = 'WARN - No .env file and no ANTHROPIC_API_KEY env var'

    # 2. Check config.yaml
    total += 1
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    if os.path.exists(config_path):
        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)
        if config and 'bots' in config:
            results['config_yaml'] = 'PASS'
            passed += 1
        else:
            results['config_yaml'] = 'FAIL - Invalid config'
    else:
        results['config_yaml'] = 'FAIL - Missing config.yaml'

    # 3. Check SQLite / database module
    total += 1
    try:
        from core.database import init_db
        engine, Session = init_db()
        session = Session()
        session.close()
        results['database'] = 'PASS'
        passed += 1
    except Exception as e:
        results['database'] = f'FAIL - {e}'

    # 4. Check Gamma API (Polymarket)
    total += 1
    try:
        resp = requests.get(
            'https://gamma-api.polymarket.com/markets?limit=1&active=true',
            timeout=10,
            headers={'User-Agent': 'PolymarketBot/1.0'}
        )
        if resp.status_code == 200:
            results['gamma_api'] = 'PASS'
            passed += 1
        else:
            results['gamma_api'] = f'WARN - Status {resp.status_code}'
    except Exception as e:
        results['gamma_api'] = f'FAIL - {e}'

    # 5. Check WorldMonitor
    total += 1
    try:
        resp = requests.get(
            'https://worldmonitor.app/api/news-digest',
            timeout=10,
            headers={'User-Agent': 'PolymarketBot/1.0'}
        )
        if resp.status_code == 200:
            results['worldmonitor'] = 'PASS'
            passed += 1
        else:
            results['worldmonitor'] = f'WARN - Status {resp.status_code} (will use fallback)'
            passed += 1  # Not critical
    except Exception as e:
        results['worldmonitor'] = f'WARN - {e} (will use fallback)'
        passed += 1  # Not critical, we have fallback

    # 6. Check Telegram
    total += 1
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    if token and token != 'your-bot-token':
        try:
            resp = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=10)
            if resp.status_code == 200:
                results['telegram'] = 'PASS'
                passed += 1
            else:
                results['telegram'] = f'WARN - Status {resp.status_code}'
        except Exception as e:
            results['telegram'] = f'WARN - {e}'
    else:
        results['telegram'] = 'WARN - No token configured'

    # 7. Check imports
    total += 1
    try:
        import anthropic
        import aiohttp
        import flask
        import apscheduler
        import sqlalchemy
        results['imports'] = 'PASS'
        passed += 1
    except ImportError as e:
        results['imports'] = f'FAIL - Missing: {e}'

    # Print results
    print("\n" + "=" * 50)
    print("POLYMARKET BOT — HEALTH CHECK")
    print("=" * 50)
    for check, result in results.items():
        status = 'v' if 'PASS' in result else ('!' if 'WARN' in result else 'x')
        print(f"  [{status}] {check}: {result}")
    print("=" * 50)
    print(f"  {passed}/{total} checks passed")
    print("=" * 50 + "\n")

    return passed >= total - 2  # Allow 2 non-critical failures


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    success = check_health()
    sys.exit(0 if success else 1)
