"""
tools/daily_postmortem.py — V1.0 Analyse quotidienne automatique des paris perdants.

Inspiré du trading bot : chaque soir, analyse ce qui s'est passé, pourquoi les paris
ont échoué, et génère des suggestions d'amélioration du modèle.

Appelé automatiquement en fin de journée (21h UTC) via main.py.
"""

import logging
import math
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Seuils de diagnostic
LOW_PRICE_THRESHOLD = 0.08    # < 8¢ = marché longshot
VOLATILITY_THRESHOLD = 0.30   # perte > 30% = volatilité excessive
CONFIDENCE_WEAK = 0.40        # confiance < 40% = entrée douteuse


class DailyPostmortem:
    """Analyse automatique quotidienne des paris — postmortem + suggestions modèle."""

    def __init__(self, db_session, telegram=None):
        self.session = db_session
        self.telegram = telegram

    def run(self) -> dict:
        """
        Lance l'analyse complète de la journée.
        Retourne un dict avec les diagnostics et suggestions.
        """
        from core.database import get_closed_positions_today, get_open_positions

        today = datetime.now(timezone.utc).date()
        positions_today = get_closed_positions_today(self.session)
        open_positions = get_open_positions(self.session)

        wins = [p for p in positions_today if getattr(p, 'pnl_realized', 0) > 0]
        losses = [p for p in positions_today if getattr(p, 'pnl_realized', 0) <= 0]

        total_pnl = sum(getattr(p, 'pnl_realized', 0) for p in positions_today)
        total_bets = len(positions_today)
        win_rate = len(wins) / total_bets if total_bets > 0 else 0

        # ── Analyse des pertes ─────────────────────────────────────────
        loss_diagnoses = []
        suggestions = []

        for loss in losses:
            entry = getattr(loss, 'entry_price', 0.5)
            pnl_pct = getattr(loss, 'pnl_realized', 0) / getattr(loss, 'amount_usdc', 50) if getattr(loss, 'amount_usdc', 0) > 0 else 0
            reason = getattr(loss, 'close_reason', 'unknown')
            niche = getattr(loss, 'niche', 'unknown')

            diagnosis = {
                'position_id': loss.id,
                'niche': niche,
                'entry_price': entry,
                'pnl_pct': pnl_pct,
                'reason': reason,
                'issues': [],
            }

            # Diagnostic 1: marché low-price
            if entry < LOW_PRICE_THRESHOLD:
                diagnosis['issues'].append(
                    f"LOW_PRICE: entrée à {entry:.3f} ({entry*100:.1f}¢) — longshot hyper-volatil. "
                    f"Sur un marché à {entry*100:.1f}¢, un mouvement de 2¢ = {2/entry*100:.0f}% de perte. "
                    f"Le modèle n'a pas pris en compte cette asymétrie."
                )
                if 'LOW_PRICE' not in [s['type'] for s in suggestions]:
                    suggestions.append({
                        'type': 'LOW_PRICE',
                        'action': f'Relever min_price à 0.05 dans config.yaml (actuellement {entry:.3f}). '
                                  f'Appliquer confidence penalty × 0.4 sur marchés < 5¢.',
                        'priority': 'HIGH',
                    })

            # Diagnostic 2: volatilité excessive (perte > 30% avant stop)
            if abs(pnl_pct) > VOLATILITY_THRESHOLD and reason == 'stop-loss':
                diagnosis['issues'].append(
                    f"STOP_LAG: perte finale {pnl_pct:.1%} >> stop_loss configuré (15%). "
                    f"Le cycle de 30 min est trop lent pour les marchés volatils. "
                    f"Le prix s'est effondré entre deux vérifications."
                )
                if 'STOP_LAG' not in [s['type'] for s in suggestions]:
                    suggestions.append({
                        'type': 'STOP_LAG',
                        'action': 'Réduire cycle_interval_minutes à 10 min pour les positions ouvertes. '
                                  'Ou ajouter un thread de monitoring continu des positions actives.',
                        'priority': 'HIGH',
                    })

            # Diagnostic 3: niche avec mauvais track record
            niche_losses = [l for l in losses if getattr(l, 'niche', '') == niche]
            if len(niche_losses) >= 2:
                diagnosis['issues'].append(
                    f"NICHE_ISSUE: {len(niche_losses)} pertes sur {niche} aujourd'hui. "
                    f"Le modèle {niche} surévalue peut-être ses edges."
                )
                suggestions.append({
                    'type': f'NICHE_{niche.upper()}',
                    'action': f'Réduire cycle_weight de {niche} dans config.yaml. '
                              f'Auditer les features du modèle {niche}_model.py.',
                    'priority': 'MEDIUM',
                })

            loss_diagnoses.append(diagnosis)

        # ── Analyse des positions encore ouvertes ─────────────────────
        at_risk = []
        for pos in open_positions:
            entry = getattr(pos, 'entry_price', 0.5)
            current = getattr(pos, 'current_price', entry)
            if entry > 0:
                unrealized_pct = (current - entry) / entry
                if unrealized_pct < -0.10 and entry < LOW_PRICE_THRESHOLD:
                    at_risk.append({
                        'id': pos.id,
                        'entry': entry,
                        'current': current,
                        'unrealized_pct': unrealized_pct,
                        'warning': f'Position {pos.id}: entry={entry:.3f} current={current:.3f} ({unrealized_pct:.1%}) — LOW PRICE + en perte'
                    })

        # ── Calcul des métriques globales ─────────────────────────────
        if total_bets > 0:
            avg_win = sum(getattr(p, 'pnl_realized', 0) for p in wins) / len(wins) if wins else 0
            avg_loss = sum(abs(getattr(p, 'pnl_realized', 0)) for p in losses) / len(losses) if losses else 0
            profit_factor = (avg_win * len(wins)) / (avg_loss * len(losses)) if losses and avg_loss > 0 else float('inf')
        else:
            avg_win = avg_loss = profit_factor = 0

        result = {
            'date': today.isoformat(),
            'total_bets': total_bets,
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'loss_diagnoses': loss_diagnoses,
            'suggestions': suggestions,
            'at_risk_positions': at_risk,
        }

        # ── Envoyer le rapport ────────────────────────────────────────
        if self.telegram:
            self._send_postmortem_report(result)

        logger.info(
            f"Postmortem {today}: {total_bets} bets, {len(wins)}W/{len(losses)}L, "
            f"PnL={total_pnl:+.2f}, {len(suggestions)} suggestions"
        )

        return result

    def _send_postmortem_report(self, result: dict):
        """Envoie le rapport postmortem via Telegram."""
        if not self.telegram:
            return

        date = result['date']
        pnl = result['total_pnl']
        wins = result['wins']
        losses = result['losses']
        wr = result['win_rate']
        pf = result['profit_factor']
        diagnoses = result['loss_diagnoses']
        suggestions = result['suggestions']
        at_risk = result['at_risk_positions']

        # Section pertes
        loss_lines = []
        for d in diagnoses:
            issues_str = " | ".join(d['issues'][:2]) if d['issues'] else "Pas de problème identifié"
            loss_lines.append(
                f"  ❌ Pos#{d['position_id']} [{d['niche'].upper()}] "
                f"entry={d['entry_price']:.3f} pnl={d['pnl_pct']:.1%}\n"
                f"     → {issues_str[:120]}"
            )
        loss_section = "\n".join(loss_lines) if loss_lines else "  Aucune perte aujourd'hui ✅"

        # Section suggestions
        sugg_lines = []
        for s in suggestions[:4]:
            priority_icon = "🔴" if s['priority'] == 'HIGH' else "🟡"
            sugg_lines.append(f"  {priority_icon} [{s['type']}] {s['action'][:100]}")
        sugg_section = "\n".join(sugg_lines) if sugg_lines else "  Aucune suggestion"

        # Section positions à risque
        risk_lines = []
        for r in at_risk[:3]:
            risk_lines.append(f"  ⚠️ Pos#{r['id']}: {r['entry']:.3f}→{r['current']:.3f} ({r['unrealized_pct']:.1%})")
        risk_section = "\n".join(risk_lines) if risk_lines else "  Aucune position critique"

        pf_str = f"{pf:.2f}" if pf != float('inf') else "∞"

        text = (
            f"<b>📊 POSTMORTEM QUOTIDIEN — {date}</b>\n"
            f"─────────────────────────────\n"
            f"💰 P&L jour : <b>{pnl:+.2f} USDC</b>\n"
            f"🎯 {wins}W / {losses}L | WR: {wr:.0%} | Profit Factor: {pf_str}\n"
            f"\n<b>🔍 ANALYSE DES PERTES :</b>\n{loss_section}\n"
            f"\n<b>💡 AMÉLIORATIONS SUGGÉRÉES :</b>\n{sugg_section}\n"
            f"\n<b>⚠️ POSITIONS À RISQUE :</b>\n{risk_section}"
        )

        self.telegram._send(text)
