import os
import sys
import requests
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def check_health():
    results = {}
    total = 0
    passed = 0

    # ------------------------------------------------------------------
    # 1. Environment: ANTHROPIC_API_KEY
    # ------------------------------------------------------------------
    total += 1
    check_name = "ENV: ANTHROPIC_API_KEY"
    if os.environ.get("ANTHROPIC_API_KEY"):
        results[check_name] = ("OK", "Key present")
        passed += 1
    else:
        results[check_name] = ("FAIL", "ANTHROPIC_API_KEY not set")

    # ------------------------------------------------------------------
    # 2. config.yaml valid
    # ------------------------------------------------------------------
    total += 1
    check_name = "CONFIG: config.yaml"
    try:
        import yaml
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.yaml",
        )
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)
        required_keys = ["filters", "kelly", "risk", "niches"]
        missing = [k for k in required_keys if k not in cfg]
        if missing:
            results[check_name] = ("FAIL", f"Missing keys: {missing}")
        else:
            results[check_name] = ("OK", f"{len(cfg)} top-level keys loaded")
            passed += 1
    except FileNotFoundError:
        results[check_name] = ("FAIL", "config.yaml not found")
    except Exception as exc:
        results[check_name] = ("FAIL", str(exc))

    # ------------------------------------------------------------------
    # 3. Database init
    # ------------------------------------------------------------------
    total += 1
    check_name = "DB: SQLite init"
    try:
        from core.database import init_db
        engine, SessionLocal = init_db()
        session = SessionLocal()
        # Simple ping: run a trivial query
        from sqlalchemy import text
        session.execute(text("SELECT 1"))
        session.close()
        results[check_name] = ("OK", "Database reachable and tables created")
        passed += 1
    except Exception as exc:
        results[check_name] = ("FAIL", str(exc))

    # ------------------------------------------------------------------
    # 4. Gamma API (Polymarket)
    # ------------------------------------------------------------------
    total += 1
    check_name = "API: Gamma (Polymarket)"
    try:
        url = "https://gamma-api.polymarket.com/markets?limit=1&active=true"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            count = len(data) if isinstance(data, list) else "?"
            results[check_name] = ("OK", f"HTTP 200 — {count} market(s) returned")
            passed += 1
        else:
            results[check_name] = ("FAIL", f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        results[check_name] = ("FAIL", "Timeout after 8s")
    except Exception as exc:
        results[check_name] = ("FAIL", str(exc))

    # ------------------------------------------------------------------
    # 5. CoinGecko API
    # ------------------------------------------------------------------
    total += 1
    check_name = "API: CoinGecko"
    try:
        url = "https://api.coingecko.com/api/v3/ping"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            gecko_status = resp.json().get("gecko_says", "pong")
            results[check_name] = ("OK", f"HTTP 200 — {gecko_status}")
            passed += 1
        else:
            results[check_name] = ("FAIL", f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        results[check_name] = ("FAIL", "Timeout after 8s")
    except Exception as exc:
        results[check_name] = ("FAIL", str(exc))

    # ------------------------------------------------------------------
    # 6. ESPN API (NBA scoreboard)
    # ------------------------------------------------------------------
    total += 1
    check_name = "API: ESPN (NBA)"
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            events = data.get("events", [])
            results[check_name] = ("OK", f"HTTP 200 — {len(events)} event(s)")
            passed += 1
        else:
            results[check_name] = ("FAIL", f"HTTP {resp.status_code}")
    except requests.exceptions.Timeout:
        results[check_name] = ("FAIL", "Timeout after 8s")
    except Exception as exc:
        results[check_name] = ("FAIL", str(exc))

    # ------------------------------------------------------------------
    # 7. Telegram bot (getMe)
    # ------------------------------------------------------------------
    total += 1
    check_name = "BOT: Telegram getMe"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        results[check_name] = ("SKIP", "TELEGRAM_BOT_TOKEN not configured")
        # Count as passed so it does not penalise setups without Telegram
        passed += 1
    else:
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                bot_data = resp.json().get("result", {})
                bot_name = bot_data.get("username", "unknown")
                results[check_name] = ("OK", f"@{bot_name} reachable")
                passed += 1
            else:
                results[check_name] = ("FAIL", f"HTTP {resp.status_code}: {resp.text[:100]}")
        except requests.exceptions.Timeout:
            results[check_name] = ("FAIL", "Timeout after 8s")
        except Exception as exc:
            results[check_name] = ("FAIL", str(exc))

    # ------------------------------------------------------------------
    # 8. Python imports
    # ------------------------------------------------------------------
    total += 1
    check_name = "IMPORTS: Python packages"
    required_imports = ["anthropic", "flask", "sqlalchemy", "numpy", "scipy"]
    failed_imports = []
    for pkg in required_imports:
        try:
            __import__(pkg)
        except ImportError:
            failed_imports.append(pkg)
    if not failed_imports:
        results[check_name] = ("OK", f"All {len(required_imports)} packages importable")
        passed += 1
    else:
        results[check_name] = ("FAIL", f"Missing: {', '.join(failed_imports)}")

    # ------------------------------------------------------------------
    # Pretty print results table
    # ------------------------------------------------------------------
    col_w = 35
    print()
    print("=" * 60)
    print(f"  POLYMARKET BOT — HEALTH CHECK")
    print("=" * 60)
    for name, (status, detail) in results.items():
        icon = "✓" if status == "OK" else ("~" if status == "SKIP" else "✗")
        print(f"  {icon}  {name:<{col_w}}  {status:<5}  {detail}")
    print("=" * 60)
    print(f"  RESULT: {passed}/{total} checks passed")
    print("=" * 60)
    print()

    overall = passed >= 6
    if overall:
        logger.info("Health check PASSED (%d/%d)", passed, total)
    else:
        logger.warning("Health check FAILED (%d/%d) — at least 6/8 required", passed, total)
    return overall


if __name__ == "__main__":
    success = check_health()
    sys.exit(0 if success else 1)
