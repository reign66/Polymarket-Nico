import json
import logging
import math
from datetime import datetime, date, timedelta
from typing import Dict, List

logger = logging.getLogger(__name__)


class KPITracker:
    def __init__(self, config: dict, db_session):
        self.config = config
        self.session = db_session

    # ------------------------------------------------------------------
    # Public: compute and persist daily KPIs
    # ------------------------------------------------------------------

    def compute_daily_kpis(self):
        """Compute and save daily KPIs to kpi_history.

        Covers:
        - Global metrics: PnL, win rate, bets, API costs, funnel
        - Per-niche breakdown: PnL, win rate, model accuracy
        """
        try:
            from core.database import (
                get_daily_pnl,
                get_capital,
                get_funnel_stats,
                get_daily_api_calls,
                get_monthly_api_cost,
                get_bot_kpis,
                save_kpi,
                Position,
            )
            from sqlalchemy import func

            session = self.session
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

            # --- Closed positions today ---
            closed_today = (
                session.query(Position)
                .filter(
                    Position.status == "closed",
                    Position.exit_time >= today_start,
                    Position.pnl_realized.isnot(None),
                )
                .all()
            )

            nb_bets = len(closed_today)
            nb_wins = sum(1 for p in closed_today if (p.pnl_realized or 0) > 0)
            nb_losses = nb_bets - nb_wins
            pnl_day = sum((p.pnl_realized or 0) for p in closed_today)

            # --- 30-day win rate ---
            since_30d = datetime.utcnow() - timedelta(days=30)
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
            wins_30d = sum(1 for p in closed_30d if (p.pnl_realized or 0) > 0)
            win_rate_30d = (wins_30d / total_30d) if total_30d else 0.0

            # --- Capital ---
            capital = get_capital(session)

            # --- API usage ---
            n_haiku = get_daily_api_calls(session, "haiku")
            n_sonnet = get_daily_api_calls(session, "sonnet")
            api_cost = get_monthly_api_cost(session)

            # --- Funnel ---
            funnel = get_funnel_stats(session)

            # --- Niche breakdown ---
            niches_raw = (
                session.query(Position.bot_niche)
                .filter(Position.bot_niche.isnot(None))
                .distinct()
                .all()
            )
            niche_names = [row[0] for row in niches_raw]

            niches_data = {}
            for niche in niche_names:
                kpis = get_bot_kpis(session, niche)
                accuracy = self._compute_model_accuracy(niche)
                niches_data[niche] = {
                    "pnl": kpis["pnl"],
                    "wr": kpis["win_rate"],
                    "accuracy": accuracy,
                }

            # --- Assemble global metrics dict ---
            global_metrics = {
                "date": date.today().isoformat(),
                "pnl_day": round(pnl_day, 4),
                "capital": round(capital, 4),
                "nb_bets": nb_bets,
                "nb_wins": nb_wins,
                "nb_losses": nb_losses,
                "win_rate_30d": round(win_rate_30d, 4),
                "n_haiku": n_haiku,
                "n_sonnet": n_sonnet,
                "api_cost": round(api_cost, 6),
                "funnel": funnel,
                "niches": niches_data,
            }

            # --- Save global KPI ---
            save_kpi(session, niche=None, period="daily", metrics=global_metrics)

            # --- Save per-niche KPIs ---
            for niche, niche_metrics in niches_data.items():
                save_kpi(session, niche=niche, period="daily", metrics=niche_metrics)

            logger.info(
                "compute_daily_kpis: saved global + %d niche KPIs for %s",
                len(niches_data),
                date.today().isoformat(),
            )
            return global_metrics

        except Exception as exc:
            logger.error("compute_daily_kpis error: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Public: gather data for daily Telegram report
    # ------------------------------------------------------------------

    def get_daily_report_data(self) -> dict:
        """Collect all data needed for the daily Telegram report.

        Returns a dict compatible with TelegramAlerter.send_daily_report().
        """
        try:
            from core.database import (
                get_daily_pnl,
                get_capital,
                get_funnel_stats,
                get_daily_api_calls,
                get_monthly_api_cost,
                get_open_positions,
                get_bot_kpis,
                Position,
            )

            session = self.session
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            since_30d = datetime.utcnow() - timedelta(days=30)
            month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

            # --- Bets today ---
            closed_today = (
                session.query(Position)
                .filter(
                    Position.status == "closed",
                    Position.exit_time >= today_start,
                    Position.pnl_realized.isnot(None),
                )
                .all()
            )
            nb_bets = len(closed_today)
            nb_wins = sum(1 for p in closed_today if (p.pnl_realized or 0) > 0)
            nb_losses = nb_bets - nb_wins
            pnl_day = get_daily_pnl(session)

            # --- 30-day win rate ---
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
            wins_30d = sum(1 for p in closed_30d if (p.pnl_realized or 0) > 0)
            win_rate_30d = (wins_30d / total_30d) if total_30d else 0.0

            # --- Monthly ROI ---
            capital = get_capital(session)
            initial = float(__import__("os").environ.get("CAPITAL_INITIAL", 1000.0))
            closed_month = (
                session.query(Position)
                .filter(
                    Position.status == "closed",
                    Position.exit_time >= month_start,
                    Position.pnl_realized.isnot(None),
                )
                .all()
            )
            pnl_month = sum((p.pnl_realized or 0) for p in closed_month)
            roi_month = (pnl_month / initial) if initial else 0.0

            # --- API usage ---
            n_haiku = get_daily_api_calls(session, "haiku")
            n_sonnet = get_daily_api_calls(session, "sonnet")
            api_cost = get_monthly_api_cost(session)

            # --- Funnel ---
            funnel = get_funnel_stats(session)
            total_scanned = funnel.get("fetched", 0)
            total_filtered = funnel.get("filtered", 0)
            total_edged = funnel.get("math_edge", 0)
            total_bet = funnel.get("bet", 0)

            # --- Niche breakdown ---
            niches_raw = (
                session.query(Position.bot_niche)
                .filter(Position.bot_niche.isnot(None))
                .distinct()
                .all()
            )
            niche_names = [row[0] for row in niches_raw]
            niches = {}
            for niche in niche_names:
                kpis = get_bot_kpis(session, niche)
                accuracy = self._compute_model_accuracy(niche)
                niches[niche] = {
                    "pnl": kpis["pnl"],
                    "wr": kpis["win_rate"],
                    "accuracy": accuracy,
                }

            # --- Open positions ---
            open_pos_orm = get_open_positions(session)
            open_positions = []
            for p in open_pos_orm:
                current = p.current_price or p.entry_price or 0.0
                pnl_latent = (current - (p.entry_price or 0)) * ((p.amount_usdc or 0) / (p.entry_price or 1))
                open_positions.append({
                    "question": p.market_question or "N/A",
                    "direction": p.direction or "N/A",
                    "price": p.entry_price or 0.0,
                    "pnl": round(pnl_latent, 2),
                })

            # --- Improvements (placeholder — can be extended with ML feedback) ---
            improvements = self._generate_improvements(closed_30d, niches)

            return {
                "date": date.today().isoformat(),
                "pnl_day": round(pnl_day, 2),
                "capital": round(capital, 2),
                "nb_bets": nb_bets,
                "nb_wins": nb_wins,
                "nb_losses": nb_losses,
                "win_rate_30d": round(win_rate_30d, 4),
                "roi_month": round(roi_month, 4),
                "n_haiku": n_haiku,
                "n_sonnet": n_sonnet,
                "api_cost": round(api_cost, 6),
                "total_scanned": total_scanned,
                "total_filtered": total_filtered,
                "total_edged": total_edged,
                "total_bet": total_bet,
                "niches": niches,
                "open_positions": open_positions,
                "improvements": improvements,
            }

        except Exception as exc:
            logger.error("get_daily_report_data error: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Public: gather data for weekly Telegram report
    # ------------------------------------------------------------------

    def get_weekly_report_data(self) -> dict:
        """Collect all data needed for the weekly Telegram report.

        Extends daily report with sharpe, drawdown, best/worst bet.
        """
        try:
            from core.database import get_weekly_drawdown_pct, get_capital, get_pnl_history, Position

            daily = self.get_daily_report_data()
            if not daily:
                return {}

            session = self.session
            since_7d = datetime.utcnow() - timedelta(days=7)

            # Aggregate weekly bets
            closed_week = (
                session.query(Position)
                .filter(
                    Position.status == "closed",
                    Position.exit_time >= since_7d,
                    Position.pnl_realized.isnot(None),
                )
                .all()
            )

            nb_bets_week = len(closed_week)
            nb_wins_week = sum(1 for p in closed_week if (p.pnl_realized or 0) > 0)
            nb_losses_week = nb_bets_week - nb_wins_week
            pnl_week = sum((p.pnl_realized or 0) for p in closed_week)

            # Sharpe from daily PnL history
            pnl_history = get_pnl_history(session, days=30)
            daily_returns = [entry["pnl"] for entry in pnl_history]
            sharpe = self._compute_sharpe(daily_returns)

            # Max drawdown
            capital = get_capital(session)
            max_drawdown = get_weekly_drawdown_pct(session, capital) / 100.0

            # Best and worst bet this week
            best_bet = None
            worst_bet = None
            if closed_week:
                best_pos = max(closed_week, key=lambda p: p.pnl_realized or 0)
                worst_pos = min(closed_week, key=lambda p: p.pnl_realized or 0)
                best_bet = {
                    "question": best_pos.market_question or "N/A",
                    "pnl": round(best_pos.pnl_realized or 0, 2),
                    "direction": best_pos.direction or "N/A",
                }
                worst_bet = {
                    "question": worst_pos.market_question or "N/A",
                    "pnl": round(worst_pos.pnl_realized or 0, 2),
                    "direction": worst_pos.direction or "N/A",
                }

            weekly = dict(daily)
            weekly.update({
                "pnl_day": round(pnl_week, 2),
                "nb_bets": nb_bets_week,
                "nb_wins": nb_wins_week,
                "nb_losses": nb_losses_week,
                "sharpe": round(sharpe, 4),
                "max_drawdown": round(max_drawdown, 4),
                "best_bet": best_bet,
                "worst_bet": worst_bet,
            })
            return weekly

        except Exception as exc:
            logger.error("get_weekly_report_data error: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_sharpe(self, returns: list) -> float:
        """Compute annualized Sharpe ratio from a list of daily PnL returns.

        Uses 252 trading days for annualisation.
        Returns 0.0 when there is insufficient data or zero std deviation.
        """
        try:
            if len(returns) < 2:
                return 0.0
            n = len(returns)
            mean = sum(returns) / n
            variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
            std = math.sqrt(variance)
            if std == 0:
                return 0.0
            daily_sharpe = mean / std
            annualized = daily_sharpe * math.sqrt(252)
            return round(annualized, 4)
        except Exception as exc:
            logger.error("_compute_sharpe error: %s", exc)
            return 0.0

    def _compute_model_accuracy(self, niche: str) -> float:
        """Compare model predictions vs actual outcomes for closed positions.

        A prediction is "correct" when:
        - direction == YES and pnl_realized > 0, OR
        - direction == NO  and pnl_realized < 0 (price went down for YES holder)

        Returns accuracy as a float between 0 and 1.
        """
        try:
            from core.database import Position

            session = self.session
            positions = (
                session.query(Position)
                .filter(
                    Position.bot_niche == niche,
                    Position.status == "closed",
                    Position.pnl_realized.isnot(None),
                    Position.direction.isnot(None),
                )
                .all()
            )

            if not positions:
                return 0.0

            correct = 0
            for p in positions:
                pnl = p.pnl_realized or 0
                direction = (p.direction or "").upper()
                if direction == "YES" and pnl > 0:
                    correct += 1
                elif direction == "NO" and pnl < 0:
                    correct += 1

            accuracy = correct / len(positions)
            return round(accuracy, 4)

        except Exception as exc:
            logger.error("_compute_model_accuracy error niche=%s: %s", niche, exc)
            return 0.0

    def _generate_improvements(self, closed_positions: list, niches: dict) -> dict:
        """Generate simple improvement suggestions based on recent performance.

        Heuristic rules — can be extended with ML feedback loops.
        """
        suggestions = {}
        index = 1

        try:
            # Low win rate overall
            total = len(closed_positions)
            if total >= 5:
                wins = sum(1 for p in closed_positions if (p.pnl_realized or 0) > 0)
                wr = wins / total
                if wr < 0.4:
                    suggestions[f"s{index}"] = (
                        f"Win rate 30j a {wr:.0%} — envisager de durcir les criteres d'entree (edge minimum plus eleve)"
                    )
                    index += 1

            # Niche-level underperformers
            for niche, data in niches.items():
                wr = data.get("wr", 0)
                pnl = data.get("pnl", 0)
                if wr < 0.35 and pnl < 0:
                    suggestions[f"s{index}"] = (
                        f"Niche {niche.upper()} sous-performe (WR {wr:.0%}, P&L {pnl:.2f}€) — suspendre ou reajuster"
                    )
                    index += 1

            # High average loss vs average win
            wins_pnl = [p.pnl_realized for p in closed_positions if (p.pnl_realized or 0) > 0]
            losses_pnl = [p.pnl_realized for p in closed_positions if (p.pnl_realized or 0) < 0]
            if wins_pnl and losses_pnl:
                avg_win = sum(wins_pnl) / len(wins_pnl)
                avg_loss = abs(sum(losses_pnl) / len(losses_pnl))
                if avg_loss > avg_win * 1.5:
                    suggestions[f"s{index}"] = (
                        f"Ratio gain/perte defavorable ({avg_win:.2f}€ / -{avg_loss:.2f}€) — ajuster stop-loss"
                    )
                    index += 1

        except Exception as exc:
            logger.error("_generate_improvements error: %s", exc)

        return suggestions
