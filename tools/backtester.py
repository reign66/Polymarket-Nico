import logging
import os
import sys
import statistics
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logger = logging.getLogger(__name__)


class Backtester:
    def __init__(self, config):
        self.config = config

    def run_backtest(self, signals_data: list, initial_capital: float = 1000) -> dict:
        """
        Simulate trades from historical signals.

        signals_data: list of dicts with keys:
            direction   - 'YES' or 'NO'
            entry_price - float in (0, 1)
            exit_price  - float in (0, 1)
            size_pct    - fraction of current capital to stake (e.g. 0.05 = 5%)

        Returns:
            win_rate, roi, sharpe, max_drawdown, total_pnl, nb_trades, final_capital
        """
        capital = initial_capital
        peak = capital
        max_dd = 0.0
        trades = []
        returns = []

        for signal in signals_data:
            direction = signal.get('direction', 'YES')
            entry = signal.get('entry_price', 0.5)
            exit_p = signal.get('exit_price', 0.5)
            size_pct = signal.get('size_pct', 0.05)

            # Guard against degenerate prices
            entry = max(0.001, min(0.999, entry))
            exit_p = max(0.001, min(0.999, exit_p))

            size = capital * size_pct

            if direction == 'YES':
                # Buying YES tokens at entry price, selling at exit price
                pnl = (exit_p - entry) / entry * size
            else:
                # Buying NO tokens: price of NO = (1 - entry)
                no_entry = 1.0 - entry
                no_exit = 1.0 - exit_p
                pnl = (no_exit - no_entry) / no_entry * size if no_entry > 0 else 0

            prev_capital = capital
            capital += pnl
            trades.append({
                'pnl': round(pnl, 4),
                'direction': direction,
                'entry': entry,
                'exit': exit_p,
                'size': round(size, 4),
            })
            ret = pnl / prev_capital if prev_capital > 0 else 0
            returns.append(ret)

            if capital > peak:
                peak = capital
            dd = (peak - capital) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        wins = sum(1 for t in trades if t['pnl'] > 0)
        total = len(trades)

        # Sharpe ratio (simplified, annualised assuming ~252 trading periods)
        if len(returns) > 1:
            avg_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            sharpe = (avg_ret / std_ret) * (252 ** 0.5) if std_ret > 0 else 0.0
        else:
            sharpe = 0.0

        return {
            'win_rate': round(wins / total, 4) if total > 0 else 0.0,
            'roi': round((capital - initial_capital) / initial_capital, 4),
            'sharpe': round(sharpe, 2),
            'max_drawdown': round(max_dd, 4),
            'total_pnl': round(capital - initial_capital, 2),
            'nb_trades': total,
            'final_capital': round(capital, 2),
        }

    def run_backtest_from_db(self, db_session, start_date=None, end_date=None) -> dict:
        """
        Run backtest against historical bets stored in the database.

        Args:
            db_session: SQLAlchemy session
            start_date: datetime or None (defaults to 30 days ago)
            end_date:   datetime or None (defaults to now)

        Returns same dict as run_backtest().
        """
        if end_date is None:
            end_date = datetime.utcnow()
        if start_date is None:
            start_date = end_date - timedelta(days=30)

        try:
            from core.database import Bet
            bets = (
                db_session.query(Bet)
                .filter(Bet.closed_at >= start_date, Bet.closed_at <= end_date)
                .order_by(Bet.closed_at)
                .all()
            )
        except Exception as e:
            logger.error(f"run_backtest_from_db: DB query failed — {e}")
            return {}

        signals = []
        for bet in bets:
            entry = getattr(bet, 'entry_price', None)
            exit_p = getattr(bet, 'exit_price', None)
            direction = getattr(bet, 'direction', 'YES')
            size_pct = getattr(bet, 'size_pct', 0.05)
            if entry is not None and exit_p is not None:
                signals.append({
                    'direction': direction,
                    'entry_price': float(entry),
                    'exit_price': float(exit_p),
                    'size_pct': float(size_pct) if size_pct else 0.05,
                })

        initial_capital = float(
            self.config.get('initial_capital', self.config.get('capital', 1000))
        )
        return self.run_backtest(signals, initial_capital=initial_capital)


if __name__ == '__main__':
    import yaml
    from dotenv import load_dotenv
    load_dotenv()

    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)

    bt = Backtester(config)

    # Simulated data
    test_signals = [
        {'direction': 'YES', 'entry_price': 0.60, 'exit_price': 0.75, 'size_pct': 0.05},
        {'direction': 'NO',  'entry_price': 0.40, 'exit_price': 0.30, 'size_pct': 0.04},
        {'direction': 'YES', 'entry_price': 0.55, 'exit_price': 0.45, 'size_pct': 0.03},
        {'direction': 'YES', 'entry_price': 0.70, 'exit_price': 0.85, 'size_pct': 0.05},
    ]
    result = bt.run_backtest(test_signals)
    print("Backtest Results:", result)
