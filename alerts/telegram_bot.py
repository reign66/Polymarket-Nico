import os
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class TelegramAlerter:
    def __init__(self, db_session=None):
        self.token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        self.session = db_session
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.warning("Telegram alerts disabled: missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    def _send_message(self, text: str, parse_mode: str = 'HTML'):
        """Send message via Telegram Bot API"""
        if not self.enabled:
            logger.info(f"Telegram (disabled): {text[:100]}...")
            return False
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': parse_mode
            }
            resp = requests.post(url, json=data, timeout=10)
            if resp.status_code != 200:
                logger.error(f"Telegram error: {resp.status_code} - {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False

    def send_entry_notification(
        self,
        niche: str,
        market_name: str,
        direction: str,
        entry_price: float,
        amount: float,
        edge: float,
        confidence: str,
        score: float,
        end_date: str,
        volume: float,
        rationale: str,
        risk_factors: List[str],
        paper: bool = True,
    ) -> bool:
        """Notify a new position entry."""
        niche_tag = niche.upper()
        risk_str = ', '.join(risk_factors) if risk_factors else 'Aucun'
        paper_str = 'Oui' if paper else 'Non'

        text = (
            f"🎯 [{niche_tag}] NOUVELLE POSITION\n"
            f"📊 {market_name}\n"
            f"→ Direction : {direction} à {entry_price}$\n"
            f"💰 Montant : {amount}€ | Edge : +{edge:.1%}\n"
            f"🧠 Confiance Sonnet : {confidence} | Score : {score:.2f}/1.0\n"
            f"📅 Résolution : {end_date} | Volume : ${volume:,.0f}\n"
            f"📝 Raison : {rationale}\n"
            f"⚠️ Risques : {risk_str}\n"
            f"📦 Paper trading : {paper_str}"
        )
        return self._send_message(text)

    def send_exit_notification(self, position: Dict, pnl_info: Dict) -> bool:
        """Notify a position exit."""
        niche = str(position.get('bot_niche', 'UNKNOWN')).upper()
        market_question = position.get('market_question', 'N/A')
        direction = position.get('direction', 'N/A')
        entry_price = float(position.get('entry_price', 0))
        exit_price = float(position.get('exit_price', 0))
        size_usdc = float(position.get('size_usdc', 0))
        pnl = float(position.get('pnl_realized', pnl_info.get('pnl', 0)))
        exit_reason = position.get('exit_reason', 'N/A')
        opened_at = position.get('opened_at')

        # Compute duration
        if opened_at:
            if isinstance(opened_at, str):
                try:
                    opened_at_dt = datetime.fromisoformat(opened_at)
                except ValueError:
                    opened_at_dt = None
            elif isinstance(opened_at, datetime):
                opened_at_dt = opened_at
            else:
                opened_at_dt = None

            if opened_at_dt:
                delta = datetime.utcnow() - opened_at_dt
                total_hours = int(delta.total_seconds() // 3600)
                days = total_hours // 24
                hours = total_hours % 24
                if days > 0:
                    duration = f"{days}j {hours}h"
                else:
                    duration = f"{hours}h"
            else:
                duration = 'N/A'
        else:
            duration = 'N/A'

        # Compute P&L percentage based on invested capital
        if size_usdc and size_usdc != 0:
            pnl_pct = pnl / size_usdc
        else:
            pnl_pct = 0.0

        pnl_emoji = '🟢' if pnl >= 0 else '🔴'

        text = (
            f"💸 [{niche}] POSITION FERMÉE\n"
            f"📊 {market_question}\n"
            f"→ Direction : {direction}\n"
            f"{pnl_emoji} Entrée : {entry_price}$ → Sortie : {exit_price}$\n"
            f"💰 P&amp;L : {pnl:+.2f}€ ({pnl_pct:+.1%})\n"
            f"⏰ Durée : {duration}\n"
            f"📄 Raison sortie : {exit_reason}"
        )
        return self._send_message(text)

    def send_circuit_breaker_alert(self, reason: str, capital: Optional[float] = None) -> bool:
        """Notify circuit breaker activation."""
        capital_str = f"{capital}€" if capital is not None else 'N/A'
        text = (
            f"🚨 CIRCUIT BREAKER ACTIVÉ\n"
            f"{reason}\n"
            f"Capital restant : {capital_str}"
        )
        return self._send_message(text)

    def send_daily_report(self, stats: Dict) -> bool:
        """Send a daily performance report."""
        date_str = stats.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        pnl = float(stats.get('pnl', 0))
        nb_bets = int(stats.get('nb_bets', 0))
        nb_wins = int(stats.get('nb_wins', 0))
        nb_losses = int(stats.get('nb_losses', 0))
        wr_day = float(stats.get('wr_day', 0))
        wr_30d = float(stats.get('wr_30d', 0))
        capital = float(stats.get('capital', 0))
        roi_month = float(stats.get('roi_month', 0))
        api_cost = float(stats.get('api_cost', 0))

        niches: List[Dict] = stats.get('niches', [])
        open_positions: List[Dict] = stats.get('open_positions', [])
        suggestions: List[Dict] = stats.get('suggestions', [])

        # Build niche section
        niche_lines = []
        for n in niches:
            n_name = str(n.get('niche', 'N/A')).upper()
            n_pnl = float(n.get('pnl', 0))
            n_nb = int(n.get('nb', 0))
            n_wr = float(n.get('wr', 0))
            niche_lines.append(f"  [{n_name}] P&amp;L: {n_pnl:+.2f}€ | Bets: {n_nb} | WR: {n_wr:.0%}")
        niches_str = '\n'.join(niche_lines) if niche_lines else '  Aucune donnée'

        # Build open positions section
        pos_lines = []
        for p in open_positions:
            mkt = p.get('market', 'N/A')
            d = p.get('direction', 'N/A')
            price = float(p.get('price', 0))
            latent_pnl = float(p.get('pnl', 0))
            pos_lines.append(f"  {mkt} | {d} @ {price}$ | P&amp;L latent : {latent_pnl:+.2f}€")
        positions_str = '\n'.join(pos_lines) if pos_lines else '  Aucune position ouverte'

        # Build suggestions section
        sug_lines = []
        for s in suggestions:
            bot = s.get('bot', 'N/A')
            suggestion = s.get('suggestion', '')
            sug_lines.append(f"  [{bot}] {suggestion}")
        suggestions_str = '\n'.join(sug_lines) if sug_lines else '  Aucune suggestion'

        text = (
            f"📊 BILAN JOURNALIER — {date_str}\n"
            f"─────────────────────\n"
            f"💰 P&amp;L du jour : {pnl:+.2f}€\n"
            f"🎯 Bets placés : {nb_bets} | Gagnés : {nb_wins} | Perdus : {nb_losses}\n"
            f"📈 Win rate jour : {wr_day:.0%} | Win rate 30j : {wr_30d:.0%}\n"
            f"📊 Capital total : {capital:.2f}€ | ROI mois : {roi_month:.1%}\n"
            f"🤖 Coût API Claude : {api_cost:.2f}€ ce mois\n"
            f"\nPAR NICHE :\n{niches_str}\n"
            f"\nPOSITIONS OUVERTES ({len(open_positions)}) :\n{positions_str}\n"
            f"\nAMÉLIORATIONS POTENTIELLES :\n{suggestions_str}"
        )
        return self._send_message(text)

    def send_weekly_report(self, stats: Dict) -> bool:
        """Send a weekly performance report."""
        week_label = stats.get('week_label', datetime.utcnow().strftime('Semaine du %d/%m/%Y'))
        pnl = float(stats.get('pnl', 0))
        nb_bets = int(stats.get('nb_bets', 0))
        nb_wins = int(stats.get('nb_wins', 0))
        nb_losses = int(stats.get('nb_losses', 0))
        wr_week = float(stats.get('wr_week', 0))
        capital = float(stats.get('capital', 0))
        roi_week = float(stats.get('roi_week', 0))
        api_cost = float(stats.get('api_cost', 0))
        sharpe = float(stats.get('sharpe_ratio', 0))
        max_dd = float(stats.get('max_drawdown', 0))

        best_bet: Dict = stats.get('best_bet', {})
        worst_bet: Dict = stats.get('worst_bet', {})

        niches: List[Dict] = stats.get('niches', [])
        open_positions: List[Dict] = stats.get('open_positions', [])
        suggestions: List[Dict] = stats.get('suggestions', [])

        # Niche section
        niche_lines = []
        for n in niches:
            n_name = str(n.get('niche', 'N/A')).upper()
            n_pnl = float(n.get('pnl', 0))
            n_nb = int(n.get('nb', 0))
            n_wr = float(n.get('wr', 0))
            niche_lines.append(f"  [{n_name}] P&amp;L: {n_pnl:+.2f}€ | Bets: {n_nb} | WR: {n_wr:.0%}")
        niches_str = '\n'.join(niche_lines) if niche_lines else '  Aucune donnée'

        # Open positions section
        pos_lines = []
        for p in open_positions:
            mkt = p.get('market', 'N/A')
            d = p.get('direction', 'N/A')
            price = float(p.get('price', 0))
            latent_pnl = float(p.get('pnl', 0))
            pos_lines.append(f"  {mkt} | {d} @ {price}$ | P&amp;L latent : {latent_pnl:+.2f}€")
        positions_str = '\n'.join(pos_lines) if pos_lines else '  Aucune position ouverte'

        # Suggestions section
        sug_lines = []
        for s in suggestions:
            bot = s.get('bot', 'N/A')
            suggestion = s.get('suggestion', '')
            sug_lines.append(f"  [{bot}] {suggestion}")
        suggestions_str = '\n'.join(sug_lines) if sug_lines else '  Aucune suggestion'

        # Best / worst bets
        best_market = best_bet.get('market', 'N/A')
        best_pnl = float(best_bet.get('pnl', 0))
        worst_market = worst_bet.get('market', 'N/A')
        worst_pnl = float(worst_bet.get('pnl', 0))

        text = (
            f"📅 BILAN HEBDOMADAIRE — {week_label}\n"
            f"─────────────────────\n"
            f"💰 P&amp;L semaine : {pnl:+.2f}€\n"
            f"🎯 Bets placés : {nb_bets} | Gagnés : {nb_wins} | Perdus : {nb_losses}\n"
            f"📈 Win rate : {wr_week:.0%} | Capital total : {capital:.2f}€\n"
            f"📊 ROI semaine : {roi_week:.1%} | Coût API : {api_cost:.2f}€\n"
            f"📐 Sharpe Ratio : {sharpe:.2f} | Max Drawdown : {max_dd:.1%}\n"
            f"🏆 Meilleur bet : {best_market} ({best_pnl:+.2f}€)\n"
            f"💀 Pire bet : {worst_market} ({worst_pnl:+.2f}€)\n"
            f"\nPAR NICHE :\n{niches_str}\n"
            f"\nPOSITIONS OUVERTES ({len(open_positions)}) :\n{positions_str}\n"
            f"\nAMÉLIORATIONS POTENTIELLES :\n{suggestions_str}"
        )
        return self._send_message(text)

    def send_near_resolution_alert(self, position: Dict, hours_left: float) -> bool:
        """Notify when a market is near resolution."""
        niche = str(position.get('bot_niche', 'UNKNOWN')).upper()
        market_question = position.get('market_question', 'N/A')
        direction = position.get('direction', 'N/A')
        entry_price = float(position.get('entry_price', 0))
        current_price = float(position.get('current_price', 0))

        # Compute latent P&L
        size_usdc = float(position.get('size_usdc', 0))
        pnl_latent = float(position.get('pnl_latent', 0))

        text = (
            f"⏰ [{niche}] RÉSOLUTION PROCHE\n"
            f"📊 {market_question}\n"
            f"→ {direction} @ {entry_price}$ | Actuel : {current_price}$\n"
            f"⏳ Résolution dans {hours_left:.1f}h\n"
            f"💰 P&amp;L latent : {pnl_latent:+.2f}€\n"
            f"💡 Cash-out recommandé si en profit"
        )
        return self._send_message(text)
