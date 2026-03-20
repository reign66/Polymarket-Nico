import os
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class TelegramAlerter:
    def __init__(self, db_session=None):
        self.token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.environ.get('TELEGRAM_CHAT_ID', '')
        self.session = db_session
        self.enabled = bool(self.token and self.chat_id)
        if not self.enabled:
            logger.warning("Telegram disabled: missing token or chat_id")

    def _send(self, text: str) -> bool:
        if not self.enabled:
            logger.info(f"TG (off): {text[:80]}...")
            return False
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            resp = requests.post(url, json={
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': 'HTML'
            }, timeout=10)
            if resp.status_code != 200:
                logger.error(f"TG error {resp.status_code}: {resp.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.error(f"TG send error: {e}")
            return False

    def send_entry_notification(
        self,
        niche: str,
        question: str,
        direction: str,
        price: float,
        amount: float,
        math_edge: float,
        method: str,
        confidence_pct: float,
        haiku_reason: str,
        sonnet_confidence: str,
        sonnet_rationale: str,
        sonnet_risk: str,
        end_date: str,
        volume: float,
    ) -> bool:
        text = (
            f"<b>NOUVELLE POSITION [{niche.upper()}]</b>\n"
            f"📊 {question}\n"
            f"→ Direction : <b>{direction}</b> a {price:.2f}$\n"
            f"💰 Mise : {amount:.2f}€ | Edge math : +{math_edge:.1%}\n"
            f"📐 Modele : {method} (confiance {confidence_pct:.0%})\n"
            f"🧠 Haiku : {haiku_reason} | Sonnet : {sonnet_confidence}\n"
            f"📅 Resolution : {end_date} | Volume : ${volume:,.0f}\n"
            f"📝 {sonnet_rationale}\n"
            f"⚠️ {sonnet_risk}\n"
            f"📦 Mode : PAPER"
        )
        return self._send(text)

    def send_exit_notification(self, position) -> bool:
        niche = getattr(position, 'bot_niche', 'N/A')
        question = getattr(position, 'market_question', 'N/A')
        direction = getattr(position, 'direction', 'N/A')
        entry_price = getattr(position, 'entry_price', 0.0) or 0.0
        exit_price = getattr(position, 'exit_price', None)
        if exit_price is None:
            exit_price = getattr(position, 'current_price', 0.0) or 0.0
        amount = getattr(position, 'amount_usdc', 0.0) or 0.0
        pnl = getattr(position, 'pnl_realized', 0.0) or 0.0
        exit_reason = getattr(position, 'exit_reason', 'N/A')
        entry_time = getattr(position, 'entry_time', None)

        pnl_pct = (pnl / amount) if amount else 0.0
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"

        if entry_time:
            if isinstance(entry_time, str):
                try:
                    entry_time = datetime.fromisoformat(entry_time)
                except ValueError:
                    entry_time = None
        if entry_time:
            delta = datetime.utcnow() - entry_time
            total_seconds = int(delta.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes = remainder // 60
            duration = f"{hours}h{minutes:02d}m"
        else:
            duration = "N/A"

        text = (
            f"<b>POSITION FERMEE [{niche.upper()}]</b>\n"
            f"📊 {question}\n"
            f"{pnl_emoji} Entree : {entry_price}$ → Sortie : {exit_price}$\n"
            f"💰 P&L : {pnl:+.2f}€ ({pnl_pct:+.1%})\n"
            f"⏰ Duree : {duration}\n"
            f"📄 Raison : {exit_reason}"
        )
        return self._send(text)

    def send_circuit_breaker_alert(self, reason: str) -> bool:
        return False  # disabled — positions only
        text = (
            f"<b>CIRCUIT BREAKER ACTIVE</b>\n"
            f"{reason}"
        )
        return self._send(text)

    def send_near_resolution_alert(self, position, hours_left: float) -> bool:
        return False  # disabled — positions only
        niche = getattr(position, 'bot_niche', 'N/A')
        question = getattr(position, 'market_question', 'N/A')
        direction = getattr(position, 'direction', 'N/A')
        entry_price = getattr(position, 'entry_price', 0.0) or 0.0
        current_price = getattr(position, 'current_price', 0.0) or 0.0
        amount = getattr(position, 'amount_usdc', 0.0) or 0.0

        if entry_price and current_price:
            pnl = (current_price - entry_price) * amount
        else:
            pnl = 0.0

        text = (
            f"<b>RESOLUTION PROCHE [{niche.upper()}]</b>\n"
            f"📊 {question}\n"
            f"→ {direction} @ {entry_price}$ | Actuel : {current_price}$\n"
            f"⏳ Resolution dans {hours_left:.0f}h\n"
            f"💰 P&L latent : {pnl:+.2f}€\n"
            f"💡 Cash-out recommande si en profit"
        )
        return self._send(text)

    def send_daily_report(self, stats: dict) -> bool:
        return False  # disabled — positions only
        date_str = stats.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        pnl = stats.get('pnl_day', 0.0)
        capital = stats.get('capital', 0.0)
        nb_bets = stats.get('nb_bets', 0)
        nb_wins = stats.get('nb_wins', 0)
        nb_losses = stats.get('nb_losses', 0)
        win_rate_30d = stats.get('win_rate_30d', 0.0)
        roi_month = stats.get('roi_month', 0.0)
        n_haiku = stats.get('n_haiku', 0)
        n_sonnet = stats.get('n_sonnet', 0)
        api_cost = stats.get('api_cost', 0.0)
        total_scanned = stats.get('total_scanned', 0)
        total_filtered = stats.get('total_filtered', 0)
        total_edged = stats.get('total_edged', 0)
        total_bet = stats.get('total_bet', 0)
        niches: dict = stats.get('niches', {})
        open_positions: list = stats.get('open_positions', [])
        improvements: dict = stats.get('improvements', {})

        niche_lines = []
        for niche_name, niche_data in niches.items():
            n_pnl = niche_data.get('pnl', 0.0)
            n_wr = niche_data.get('wr', 0.0)
            n_acc = niche_data.get('accuracy', 0.0)
            niche_lines.append(
                f"  [{niche_name.upper()}] P&L: {n_pnl:.2f}€ | WR: {n_wr:.0%} | Modele : {n_acc:.0%}"
            )
        niche_section = "\n".join(niche_lines) if niche_lines else "  Aucune niche active"

        pos_lines = []
        for pos in open_positions:
            q = pos.get('question', 'N/A')
            d = pos.get('direction', 'N/A')
            pr = pos.get('price', 0.0)
            p = pos.get('pnl', 0.0)
            pos_lines.append(f"  {q} | {d} @ {pr} | P&L : {p:+.2f}€")
        pos_section = "\n".join(pos_lines) if pos_lines else "  Aucune position ouverte"

        improv_lines = []
        for key, suggestion in improvements.items():
            improv_lines.append(f"  - {suggestion}")
        improv_section = "\n".join(improv_lines) if improv_lines else "  Aucune suggestion"

        text = (
            f"<b>BILAN JOURNALIER — {date_str}</b>\n"
            f"───────────────────\n"
            f"💰 P&L jour : {pnl:+.2f}€ | Capital : {capital:.2f}€\n"
            f"🎯 Bets : {nb_bets} | ✅ {nb_wins} | ❌ {nb_losses}\n"
            f"📈 Win rate 30j : {win_rate_30d:.0%} | ROI mois : {roi_month:.1%}\n"
            f"🤖 API : {n_haiku}x Haiku + {n_sonnet}x Sonnet = {api_cost:.4f}€\n"
            f"📐 Entonnoir : {total_scanned} scannes → {total_filtered} filtres → {total_edged} edge → {total_bet} bets\n"
            f"\n<b>PAR NICHE :</b>\n{niche_section}\n"
            f"\n<b>POSITIONS OUVERTES ({len(open_positions)}) :</b>\n{pos_section}\n"
            f"\n<b>AMELIORATIONS :</b>\n{improv_section}"
        )
        return self._send(text)

    def send_weekly_report(self, stats: dict) -> bool:
        return False  # disabled — positions only
        date_str = stats.get('date', datetime.utcnow().strftime('%Y-%m-%d'))
        pnl = stats.get('pnl_day', 0.0)
        capital = stats.get('capital', 0.0)
        nb_bets = stats.get('nb_bets', 0)
        nb_wins = stats.get('nb_wins', 0)
        nb_losses = stats.get('nb_losses', 0)
        win_rate_30d = stats.get('win_rate_30d', 0.0)
        roi_month = stats.get('roi_month', 0.0)
        n_haiku = stats.get('n_haiku', 0)
        n_sonnet = stats.get('n_sonnet', 0)
        api_cost = stats.get('api_cost', 0.0)
        total_scanned = stats.get('total_scanned', 0)
        total_filtered = stats.get('total_filtered', 0)
        total_edged = stats.get('total_edged', 0)
        total_bet = stats.get('total_bet', 0)
        niches: dict = stats.get('niches', {})
        open_positions: list = stats.get('open_positions', [])
        improvements: dict = stats.get('improvements', {})

        sharpe = stats.get('sharpe', 0.0)
        max_drawdown = stats.get('max_drawdown', 0.0)
        best_bet = stats.get('best_bet', {})
        worst_bet = stats.get('worst_bet', {})

        niche_lines = []
        for niche_name, niche_data in niches.items():
            n_pnl = niche_data.get('pnl', 0.0)
            n_wr = niche_data.get('wr', 0.0)
            n_acc = niche_data.get('accuracy', 0.0)
            niche_lines.append(
                f"  [{niche_name.upper()}] P&L: {n_pnl:.2f}€ | WR: {n_wr:.0%} | Modele : {n_acc:.0%}"
            )
        niche_section = "\n".join(niche_lines) if niche_lines else "  Aucune niche active"

        pos_lines = []
        for pos in open_positions:
            q = pos.get('question', 'N/A')
            d = pos.get('direction', 'N/A')
            pr = pos.get('price', 0.0)
            p = pos.get('pnl', 0.0)
            pos_lines.append(f"  {q} | {d} @ {pr} | P&L : {p:+.2f}€")
        pos_section = "\n".join(pos_lines) if pos_lines else "  Aucune position ouverte"

        improv_lines = []
        for key, suggestion in improvements.items():
            improv_lines.append(f"  - {suggestion}")
        improv_section = "\n".join(improv_lines) if improv_lines else "  Aucune suggestion"

        best_q = best_bet.get('question', 'N/A') if best_bet else 'N/A'
        best_pnl = best_bet.get('pnl', 0.0) if best_bet else 0.0
        worst_q = worst_bet.get('question', 'N/A') if worst_bet else 'N/A'
        worst_pnl = worst_bet.get('pnl', 0.0) if worst_bet else 0.0

        text = (
            f"<b>BILAN HEBDOMADAIRE — {date_str}</b>\n"
            f"───────────────────\n"
            f"💰 P&L semaine : {pnl:+.2f}€ | Capital : {capital:.2f}€\n"
            f"🎯 Bets : {nb_bets} | ✅ {nb_wins} | ❌ {nb_losses}\n"
            f"📈 Win rate 30j : {win_rate_30d:.0%} | ROI mois : {roi_month:.1%}\n"
            f"📉 Sharpe : {sharpe:.2f} | Max Drawdown : {max_drawdown:.1%}\n"
            f"🏆 Meilleur bet : {best_q} ({best_pnl:+.2f}€)\n"
            f"💀 Pire bet : {worst_q} ({worst_pnl:+.2f}€)\n"
            f"🤖 API : {n_haiku}x Haiku + {n_sonnet}x Sonnet = {api_cost:.4f}€\n"
            f"📐 Entonnoir : {total_scanned} scannes → {total_filtered} filtres → {total_edged} edge → {total_bet} bets\n"
            f"\n<b>PAR NICHE :</b>\n{niche_section}\n"
            f"\n<b>POSITIONS OUVERTES ({len(open_positions)}) :</b>\n{pos_section}\n"
            f"\n<b>AMELIORATIONS :</b>\n{improv_section}"
        )
        return self._send(text)
