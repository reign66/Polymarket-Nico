#!/usr/bin/env python3
"""
Script de migration : injecte l'historique connu dans la DB Polymarket.
À exécuter UNE FOIS sur Railway via: python migrate_history.py
"""
import sys, os, json
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.database import get_engine_and_session, Position, KpiHistory, Base

engine, SessionLocal = get_engine_and_session()
session = SessionLocal()

print("=== Migration historique Polymarket ===")

# ─── 1. POSITIONS FERMÉES ───────────────────────────────────────────────────

closed_positions = [
    # Indiana Pacers — stop-loss
    {
        "market_id": "indiana-pacers-worst-record-nba",
        "market_question": "Will the Indiana Pacers have the worst record in the NBA?",
        "direction": "NO",
        "entry_price": 0.493,
        "exit_price": 0.401,
        "amount_usdc": 49.84,  # pnl=-9.33 / (0.493-0.401) ≈ 50
        "pnl_realized": -9.33,
        "entry_time": datetime(2026, 3, 21, 12, 0, 0),
        "exit_time": datetime(2026, 3, 23, 6, 28, 0),
        "exit_reason": "stop-loss",
        "bot_niche": "nba",
        "status": "closed",
    },
    # New Hampshire Senate — take-profit (+100€)
    {
        "market_id": "democrats-win-new-hampshire-senate-2026",
        "market_question": "Will the Democrats win the New Hampshire Senate race in 2026?",
        "direction": "NO",
        "entry_price": 0.11,
        "exit_price": 0.33,
        "amount_usdc": 45.45,  # pnl=+100 / (0.33-0.11) ≈ 45
        "pnl_realized": 100.0,
        "entry_time": datetime(2026, 3, 21, 20, 0, 0),
        "exit_time": datetime(2026, 3, 24, 3, 28, 0),
        "exit_reason": "take-profit",
        "bot_niche": "politics",
        "status": "closed",
    },
    # Mississippi Senate — take-profit (+121€)
    {
        "market_id": "republicans-win-mississippi-senate-2026",
        "market_question": "Will the Republicans win the Mississippi Senate race in 2026?",
        "direction": "NO",
        "entry_price": 0.095,
        "exit_price": 0.325,
        "amount_usdc": 52.63,  # pnl=+121 / (0.325-0.095) ≈ 52.6
        "pnl_realized": 121.05,
        "entry_time": datetime(2026, 3, 21, 21, 0, 0),
        "exit_time": datetime(2026, 3, 24, 7, 58, 0),
        "exit_reason": "take-profit",
        "bot_niche": "politics",
        "status": "closed",
    },
    # Position #8 UNKNOWN stop-loss (-16.7%)
    {
        "market_id": "unknown-pos8-march23",
        "market_question": "Unknown position (recovered from logs)",
        "direction": "NO",
        "entry_price": 0.300,
        "exit_price": 0.250,
        "amount_usdc": 29.94,
        "pnl_realized": -4.99,
        "entry_time": datetime(2026, 3, 21, 18, 0, 0),
        "exit_time": datetime(2026, 3, 23, 10, 0, 0),
        "exit_reason": "stop-loss",
        "bot_niche": "unknown",
        "status": "closed",
    },
]

inserted_closed = 0
for p in closed_positions:
    existing = session.query(Position).filter_by(
        market_id=p["market_id"], status="closed"
    ).first()
    if not existing:
        pos = Position(**p)
        session.add(pos)
        inserted_closed += 1
        print(f"  + Fermée: {p['market_question'][:60]} | PnL: {p['pnl_realized']:+.2f}€")

# ─── 2. POSITIONS OUVERTES ───────────────────────────────────────────────────

open_positions = [
    {"market_id": "bitcoin-1m-before-gta-vi", "market_question": "Will bitcoin hit $1m before GTA VI?", "direction": "YES", "entry_price": 0.489, "current_price": 0.490, "amount_usdc": 10.0, "pnl_unrealized": 0.05, "bot_niche": "crypto"},
    {"market_id": "okc-thunder-western-conference-finals", "market_question": "Will the Oklahoma City Thunder win the NBA Western Conference Finals?", "direction": "NO", "entry_price": 0.47, "current_price": 0.35, "amount_usdc": 22.17, "pnl_unrealized": 2.66, "bot_niche": "nba"},
    {"market_id": "putin-out-president-2026", "market_question": "Putin out as President of Russia by December 31, 2026?", "direction": "YES", "entry_price": 0.125, "current_price": 0.125, "amount_usdc": 10.0, "pnl_unrealized": 0.0, "bot_niche": "geopolitics"},
    {"market_id": "democrats-win-new-york-governor-2026", "market_question": "Will the Democrats win the New York governor race in 2026?", "direction": "NO", "entry_price": 0.085, "current_price": 0.085, "amount_usdc": 10.0, "pnl_unrealized": 0.0, "bot_niche": "politics"},
    {"market_id": "republicans-win-tennessee-governor-2026", "market_question": "Will the Republicans win the Tennessee governor race in 2026?", "direction": "NO", "entry_price": 0.0825, "current_price": 0.075, "amount_usdc": 10.0, "pnl_unrealized": 0.91, "bot_niche": "politics"},
    {"market_id": "democrats-win-illinois-senate-2026", "market_question": "Will the Democrats win the Illinois Senate race in 2026?", "direction": "NO", "entry_price": 0.095, "current_price": 0.12, "amount_usdc": 10.0, "pnl_unrealized": -2.63, "bot_niche": "politics"},
    {"market_id": "democrats-win-maine-governor-2026", "market_question": "Will the Democrats win the Maine governor race in 2026?", "direction": "NO", "entry_price": 0.1, "current_price": 0.1, "amount_usdc": 10.0, "pnl_unrealized": 0.0, "bot_niche": "politics"},
    {"market_id": "republicans-win-louisiana-senate-2026", "market_question": "Will the Republicans win the Louisiana Senate race in 2026?", "direction": "NO", "entry_price": 0.09, "current_price": 0.09, "amount_usdc": 10.0, "pnl_unrealized": 0.0, "bot_niche": "politics"},
    {"market_id": "republicans-win-kentucky-senate-2026", "market_question": "Will the Republicans win the Kentucky Senate race in 2026?", "direction": "NO", "entry_price": 0.095, "current_price": 0.12, "amount_usdc": 10.0, "pnl_unrealized": -2.63, "bot_niche": "politics"},
    {"market_id": "republicans-win-mississippi-senate-2026-open", "market_question": "Will the Republicans win the Mississippi Senate race in 2026?", "direction": "NO", "entry_price": 0.095, "current_price": 0.045, "amount_usdc": 21.93, "pnl_unrealized": 5.26, "bot_niche": "politics"},
]

inserted_open = 0
for p in open_positions:
    existing = session.query(Position).filter_by(
        market_id=p["market_id"], status="open"
    ).first()
    if not existing:
        pos = Position(
            market_id=p["market_id"],
            market_question=p["market_question"],
            direction=p["direction"],
            entry_price=p["entry_price"],
            current_price=p["current_price"],
            amount_usdc=p["amount_usdc"],
            pnl_unrealized=p["pnl_unrealized"],
            entry_time=datetime(2026, 3, 20, 12, 0, 0),
            bot_niche=p["bot_niche"],
            status="open",
        )
        session.add(pos)
        inserted_open += 1
        print(f"  + Ouverte: {p['market_question'][:60]} | PnL: {p['pnl_unrealized']:+.2f}€")

# ─── 3. KPI HISTORY ─────────────────────────────────────────────────────────

kpi_records = [
    {
        "date": date(2026, 3, 23),
        "period": "weekly",
        "niche": None,
        "metrics_json": json.dumps({
            "pnl": -16.36, "capital": 983.64, "bets": 7, "wins": 2, "losses": 5,
            "win_rate_30d": 0.29, "roi_month": -0.016, "sharpe": -21.65, "max_drawdown": 0.016,
            "best_bet": "Will the Orlando Magic make the NBA Playoffs? (+12.79€)",
            "worst_bet": "Will the Toronto Raptors make the NBA Playoffs? (-8.33€)",
        })
    },
    {
        "date": date(2026, 3, 23),
        "period": "weekly",
        "niche": "nba",
        "metrics_json": json.dumps({"pnl": -9.22, "win_rate": 0.33, "model_accuracy": 0.50})
    },
    {
        "date": date(2026, 3, 23),
        "period": "weekly",
        "niche": "politics",
        "metrics_json": json.dumps({"pnl": -7.14, "win_rate": 0.0, "model_accuracy": 1.0})
    },
    {
        "date": date(2026, 3, 24),
        "period": "daily",
        "niche": None,
        "metrics_json": json.dumps({
            "pnl": 0.0, "capital": 1074.31, "bets": 0,
            "win_rate_30d": 0.33, "roi_month": 0.074,
        })
    },
    {
        "date": date(2026, 3, 24),
        "period": "daily",
        "niche": "politics",
        "metrics_json": json.dumps({"pnl": 92.86, "win_rate": 0.50, "model_accuracy": 0.50})
    },
    {
        "date": date(2026, 3, 24),
        "period": "daily",
        "niche": "nba",
        "metrics_json": json.dumps({"pnl": -18.55, "win_rate": 0.29, "model_accuracy": 0.57})
    },
]

inserted_kpi = 0
for k in kpi_records:
    existing = session.query(KpiHistory).filter_by(
        date=k["date"], period=k["period"], niche=k["niche"]
    ).first()
    if not existing:
        kpi = KpiHistory(**k)
        session.add(kpi)
        inserted_kpi += 1

# ─── 4. CAPITAL INITIAL ─────────────────────────────────────────────────────
# Le capital actuel est 1074.31€ (après les take-profits du 24 mars)
# On insère un KPI pour que le bot sache d'où il part

existing_capital = session.query(KpiHistory).filter_by(
    date=date(2026, 3, 24), period="daily", niche="__capital__"
).first()
if not existing_capital:
    capital_kpi = KpiHistory(
        date=date(2026, 3, 24),
        period="daily",
        niche="__capital__",
        metrics_json=json.dumps({"capital": 1074.31, "note": "recovered from history migration"})
    )
    session.add(capital_kpi)

session.commit()
session.close()

print(f"\n✅ Migration terminée:")
print(f"   {inserted_closed} positions fermées insérées")
print(f"   {inserted_open} positions ouvertes insérées")
print(f"   {inserted_kpi} entrées KPI insérées")
print(f"   Capital de référence: 1074.31€")
