import logging
import statistics
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class KPITracker:
    def __init__(self, config, db_session, bots=None):
        self.config = config
        self.session = db_session
        self.bots = bots or {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_bets_for_period(self, start: datetime, end: datetime) -> list:
        """Return closed positions whose closed_at falls within [start, end]."""
        try:
            from core.database import Position
            bets = (
                self.session.query(Position)
                .filter(
                    Position.status == 'closed',
                    Position.closed_at >= start,
                    Position.closed_at <= end,
                )
                .all()
            )
            return bets
        except Exception as e:
            logger.error(f"_get_bets_for_period error: {e}")
            return []

    def _get_open_bets(self) -> list:
        """Return all currently open positions."""
        try:
            from core.database import Position
            return self.session.query(Position).filter(Position.status == 'open').all()
        except Exception as e:
            logger.error(f"_get_open_bets error: {e}")
            return []

    def _compute_max_drawdown(self, bets: list) -> float:
        """Compute max drawdown from a sorted list of positions by closed_at."""
        if not bets:
            return 0.0
        sorted_bets = sorted(bets, key=lambda b: b.closed_at)
        peak = 0.0
        cumulative = 0.0
        max_dd = 0.0
        for bet in sorted_bets:
            pnl = getattr(bet, 'pnl_realized', 0) or 0
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 4)

    def _compute_sharpe(self, bets: list) -> float:
        """Simplified Sharpe ratio annualised from daily PnL returns."""
        if len(bets) < 2:
            return 0.0
        returns = []
        for bet in bets:
            stake = getattr(bet, 'size_usdc', 0) or 0
            pnl = getattr(bet, 'pnl_realized', 0) or 0
            ret = pnl / stake if stake > 0 else 0
            returns.append(ret)
        avg = statistics.mean(returns)
        std = statistics.stdev(returns) if len(returns) > 1 else 1
        sharpe = (avg / std) * (252 ** 0.5) if std > 0 else 0
        return round(sharpe, 2)

    def _get_monthly_api_cost_eur(self) -> float:
        try:
            from core.database import get_monthly_api_cost
            return get_monthly_api_cost(self.session) * 0.92
        except Exception as e:
            logger.error(f"_get_monthly_api_cost_eur error: {e}")
            return 0.0

    def _save_kpi_record(self, period: str, data: dict):
        """Persist a KPI record in kpi_history table."""
        try:
            from core.database import KpiHistory
            record = KpiHistory(
                date=date.today(),
                period=period,
                win_rate=data.get('win_rate'),
                pnl=data.get('pnl'),
                nb_bets=data.get('nb_bets'),
                avg_edge=data.get('avg_edge'),
                sharpe_ratio=data.get('sharpe'),
                max_drawdown=data.get('max_drawdown'),
            )
            self.session.add(record)
            self.session.commit()
            logger.info(f"KPI record saved for period: {period}")
        except Exception as e:
            logger.warning(f"_save_kpi_record: KPIHistory table may not exist yet — {e}")
            self.session.rollback()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_daily_kpis(self):
        """Calculate and store daily KPIs: win_rate, pnl, nb_bets, avg_edge, api_cost. Also per niche."""
        logger.info("Computing daily KPIs...")
        end = datetime.utcnow()
        start = datetime(end.year, end.month, end.day, 0, 0, 0)

        bets = self._get_bets_for_period(start, end)

        nb_bets = len(bets)
        wins = sum(1 for b in bets if (getattr(b, 'pnl_realized', 0) or 0) > 0)
        win_rate = wins / nb_bets if nb_bets > 0 else 0.0
        pnl = sum((getattr(b, 'pnl_realized', 0) or 0) for b in bets)
        edges = [getattr(b, 'edge_at_entry', 0) or 0 for b in bets if getattr(b, 'edge_at_entry', None) is not None]
        avg_edge = statistics.mean(edges) if edges else 0.0
        api_cost_eur = self._get_monthly_api_cost_eur()

        data = {
            'win_rate': round(win_rate, 4),
            'pnl': round(pnl, 2),
            'nb_bets': nb_bets,
            'avg_edge': round(avg_edge, 4),
            'api_cost_eur': round(api_cost_eur, 2),
        }

        # Per niche
        niches: Dict[str, dict] = {}
        for bet in bets:
            niche = getattr(bet, 'bot_niche', 'unknown') or 'unknown'
            if niche not in niches:
                niches[niche] = {'nb': 0, 'wins': 0, 'pnl': 0.0}
            niches[niche]['nb'] += 1
            bet_pnl = getattr(bet, 'pnl_realized', 0) or 0
            if bet_pnl > 0:
                niches[niche]['wins'] += 1
            niches[niche]['pnl'] += bet_pnl

        niche_kpis = {}
        for niche, stats in niches.items():
            niche_kpis[niche] = {
                'nb_bets': stats['nb'],
                'win_rate': round(stats['wins'] / stats['nb'], 4) if stats['nb'] > 0 else 0.0,
                'pnl': round(stats['pnl'], 2),
            }
        data['niche_kpis'] = niche_kpis

        period = start.strftime('%Y-%m-%d')
        self._save_kpi_record(period, data)
        logger.info(f"Daily KPIs [{period}]: win_rate={win_rate:.1%}, pnl={pnl:.2f}€, bets={nb_bets}")
        return data

    def compute_weekly_kpis(self):
        """Compute weekly KPIs: sharpe_ratio, max_drawdown, best/worst bet, confidence/result correlation."""
        logger.info("Computing weekly KPIs...")
        end = datetime.utcnow()
        start = end - timedelta(days=7)

        bets = self._get_bets_for_period(start, end)

        nb_bets = len(bets)
        wins = sum(1 for b in bets if (getattr(b, 'pnl_realized', 0) or 0) > 0)
        win_rate = wins / nb_bets if nb_bets > 0 else 0.0
        pnl = sum((getattr(b, 'pnl_realized', 0) or 0) for b in bets)

        sharpe = self._compute_sharpe(bets)
        max_dd = self._compute_max_drawdown(bets)

        best_bet = None
        worst_bet = None
        if bets:
            best_bet_obj = max(bets, key=lambda b: getattr(b, 'pnl_realized', 0) or 0)
            worst_bet_obj = min(bets, key=lambda b: getattr(b, 'pnl_realized', 0) or 0)
            best_bet = {
                'market_id': getattr(best_bet_obj, 'market_id', '?'),
                'niche': getattr(best_bet_obj, 'bot_niche', '?'),
                'pnl': getattr(best_bet_obj, 'pnl_realized', 0),
            }
            worst_bet = {
                'market_id': getattr(worst_bet_obj, 'market_id', '?'),
                'niche': getattr(worst_bet_obj, 'bot_niche', '?'),
                'pnl': getattr(worst_bet_obj, 'pnl_realized', 0),
            }

        # Confidence vs result correlation
        confidence_result = self._compute_confidence_correlation(bets)

        data = {
            'period_start': start.strftime('%Y-%m-%d'),
            'period_end': end.strftime('%Y-%m-%d'),
            'nb_bets': nb_bets,
            'win_rate': round(win_rate, 4),
            'pnl': round(pnl, 2),
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'best_bet': best_bet,
            'worst_bet': worst_bet,
            'confidence_result_correlation': confidence_result,
        }

        period = f"week_{start.strftime('%Y-%m-%d')}"
        self._save_kpi_record(period, data)
        logger.info(f"Weekly KPIs: sharpe={sharpe}, max_dd={max_dd:.1%}, pnl={pnl:.2f}€")
        return data

    def _compute_confidence_correlation(self, bets: list) -> float:
        """Compute correlation between confidence score and binary win outcome."""
        pairs = []
        for bet in bets:
            confidence = getattr(bet, 'haiku_score', None)
            pnl = getattr(bet, 'pnl_realized', 0) or 0
            if confidence is not None:
                pairs.append((float(confidence), 1.0 if pnl > 0 else 0.0))
        if len(pairs) < 3:
            return 0.0
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        try:
            mean_x = statistics.mean(xs)
            mean_y = statistics.mean(ys)
            cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            std_x = statistics.stdev(xs)
            std_y = statistics.stdev(ys)
            if std_x == 0 or std_y == 0:
                return 0.0
            corr = cov / (len(xs) * std_x * std_y)
            return round(corr, 4)
        except Exception:
            return 0.0

    def get_daily_report_data(self) -> dict:
        """Return all data needed for daily Telegram/email report."""
        end = datetime.utcnow()
        start = datetime(end.year, end.month, end.day, 0, 0, 0)

        bets = self._get_bets_for_period(start, end)
        open_bets = self._get_open_bets()

        nb_bets = len(bets)
        wins = sum(1 for b in bets if (getattr(b, 'pnl_realized', 0) or 0) > 0)
        pnl = sum((getattr(b, 'pnl_realized', 0) or 0) for b in bets)
        win_rate = wins / nb_bets if nb_bets > 0 else 0.0

        # Total capital from config + cumulative pnl
        initial_capital = float(
            self.config.get('initial_capital',
                            self.config.get('capital', 1000))
        )
        try:
            from core.database import get_capital
            total_pnl = get_capital(self.session) - initial_capital
        except Exception:
            total_pnl = pnl

        current_capital = initial_capital + total_pnl
        roi = (current_capital - initial_capital) / initial_capital if initial_capital > 0 else 0.0

        api_cost_eur = self._get_monthly_api_cost_eur()

        # Per niche daily
        niches: Dict[str, dict] = {}
        for bet in bets:
            niche = getattr(bet, 'bot_niche', 'unknown') or 'unknown'
            if niche not in niches:
                niches[niche] = {'nb': 0, 'wins': 0, 'pnl': 0.0}
            niches[niche]['nb'] += 1
            bet_pnl = getattr(bet, 'pnl_realized', 0) or 0
            if bet_pnl > 0:
                niches[niche]['wins'] += 1
            niches[niche]['pnl'] += bet_pnl

        niche_summary = {}
        for niche, stats in niches.items():
            niche_summary[niche] = {
                'nb_bets': stats['nb'],
                'win_rate': round(stats['wins'] / stats['nb'], 4) if stats['nb'] > 0 else 0.0,
                'pnl': round(stats['pnl'], 2),
            }

        return {
            'date': start.strftime('%Y-%m-%d'),
            'nb_bets': nb_bets,
            'wins': wins,
            'losses': nb_bets - wins,
            'win_rate': round(win_rate, 4),
            'daily_pnl': round(pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'current_capital': round(current_capital, 2),
            'roi': round(roi, 4),
            'open_positions': len(open_bets),
            'api_cost_eur': round(api_cost_eur, 2),
            'niche_breakdown': niche_summary,
        }

    def get_weekly_report_data(self) -> dict:
        """Return all data needed for weekly Telegram/email report."""
        end = datetime.utcnow()
        start = end - timedelta(days=7)

        bets = self._get_bets_for_period(start, end)

        nb_bets = len(bets)
        wins = sum(1 for b in bets if (getattr(b, 'pnl_realized', 0) or 0) > 0)
        pnl = sum((getattr(b, 'pnl_realized', 0) or 0) for b in bets)
        win_rate = wins / nb_bets if nb_bets > 0 else 0.0

        sharpe = self._compute_sharpe(bets)
        max_dd = self._compute_max_drawdown(bets)
        confidence_corr = self._compute_confidence_correlation(bets)

        initial_capital = float(
            self.config.get('initial_capital',
                            self.config.get('capital', 1000))
        )
        try:
            from core.database import get_capital
            total_pnl = get_capital(self.session) - initial_capital
        except Exception:
            total_pnl = pnl
        current_capital = initial_capital + total_pnl
        roi = (current_capital - initial_capital) / initial_capital if initial_capital > 0 else 0.0

        best_bet = None
        worst_bet = None
        if bets:
            best_bet_obj = max(bets, key=lambda b: getattr(b, 'pnl_realized', 0) or 0)
            worst_bet_obj = min(bets, key=lambda b: getattr(b, 'pnl_realized', 0) or 0)
            best_bet = {
                'market_id': getattr(best_bet_obj, 'market_id', '?'),
                'niche': getattr(best_bet_obj, 'bot_niche', '?'),
                'pnl': round(getattr(best_bet_obj, 'pnl_realized', 0) or 0, 2),
                'direction': getattr(best_bet_obj, 'direction', '?'),
            }
            worst_bet = {
                'market_id': getattr(worst_bet_obj, 'market_id', '?'),
                'niche': getattr(worst_bet_obj, 'bot_niche', '?'),
                'pnl': round(getattr(worst_bet_obj, 'pnl_realized', 0) or 0, 2),
                'direction': getattr(worst_bet_obj, 'direction', '?'),
            }

        # Per niche weekly
        niches: Dict[str, dict] = {}
        for bet in bets:
            niche = getattr(bet, 'bot_niche', 'unknown') or 'unknown'
            if niche not in niches:
                niches[niche] = {'nb': 0, 'wins': 0, 'pnl': 0.0}
            niches[niche]['nb'] += 1
            bet_pnl = getattr(bet, 'pnl_realized', 0) or 0
            if bet_pnl > 0:
                niches[niche]['wins'] += 1
            niches[niche]['pnl'] += bet_pnl

        niche_summary = {}
        for niche, stats in niches.items():
            niche_summary[niche] = {
                'nb_bets': stats['nb'],
                'win_rate': round(stats['wins'] / stats['nb'], 4) if stats['nb'] > 0 else 0.0,
                'pnl': round(stats['pnl'], 2),
                'roi': round(stats['pnl'] / initial_capital, 4) if initial_capital > 0 else 0.0,
            }

        go_nogo = self.check_go_nogo()

        return {
            'period_start': start.strftime('%Y-%m-%d'),
            'period_end': end.strftime('%Y-%m-%d'),
            'nb_bets': nb_bets,
            'wins': wins,
            'losses': nb_bets - wins,
            'win_rate': round(win_rate, 4),
            'weekly_pnl': round(pnl, 2),
            'total_pnl': round(total_pnl, 2),
            'current_capital': round(current_capital, 2),
            'roi': round(roi, 4),
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'best_bet': best_bet,
            'worst_bet': worst_bet,
            'confidence_result_correlation': confidence_corr,
            'niche_breakdown': niche_summary,
            'go_nogo': go_nogo,
        }

    def check_go_nogo(self) -> dict:
        """
        Check Go/No-Go criteria:
          - Win rate > 55% on 20+ bets
          - Max drawdown < 20%
          - At least 2 niches with positive ROI
          - API cost < 10EUR over 14 days
          - Zero system crashes (approximated by log check)
        Returns {'status': 'GO' or 'NO-GO', 'details': {...}}
        """
        end = datetime.utcnow()
        start_14d = end - timedelta(days=14)
        bets_14d = self._get_bets_for_period(start_14d, end)

        nb_bets = len(bets_14d)
        wins = sum(1 for b in bets_14d if (getattr(b, 'pnl_realized', 0) or 0) > 0)
        win_rate = wins / nb_bets if nb_bets > 0 else 0.0
        max_dd = self._compute_max_drawdown(bets_14d)

        # Niche ROI
        niches: Dict[str, float] = {}
        for bet in bets_14d:
            niche = getattr(bet, 'bot_niche', 'unknown') or 'unknown'
            niches[niche] = niches.get(niche, 0.0) + (getattr(bet, 'pnl_realized', 0) or 0)
        niches_positive = sum(1 for pnl in niches.values() if pnl > 0)

        # API cost over 14 days (we use monthly cost as approximation)
        api_cost_14d = self._get_monthly_api_cost_eur() * (14 / 30)

        # System crashes: count ERROR lines in recent log (best effort)
        crashes = 0
        log_path = 'logs/bot.log'
        try:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            # Check last 5000 lines for critical errors
            for line in lines[-5000:]:
                if 'CRITICAL' in line or 'SystemExit' in line or 'Traceback' in line:
                    crashes += 1
        except Exception:
            crashes = 0

        # Criteria evaluation
        criteria = {
            'win_rate_ok': win_rate > 0.55 and nb_bets >= 20,
            'max_drawdown_ok': max_dd < 0.20,
            'niches_positive_ok': niches_positive >= 2,
            'api_cost_ok': api_cost_14d < 10.0,
            'no_crashes': crashes == 0,
        }

        details = {
            'win_rate': round(win_rate, 4),
            'nb_bets': nb_bets,
            'max_drawdown': max_dd,
            'niches_with_positive_roi': niches_positive,
            'api_cost_14d_eur': round(api_cost_14d, 2),
            'system_crash_indicators': crashes,
            'criteria': criteria,
        }

        status = 'GO' if all(criteria.values()) else 'NO-GO'
        failed = [k for k, v in criteria.items() if not v]
        if failed:
            details['failed_criteria'] = failed

        logger.info(f"Go/No-Go: {status} — {details}")
        return {'status': status, 'details': details}
