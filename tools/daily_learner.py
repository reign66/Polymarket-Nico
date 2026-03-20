"""
tools/daily_learner.py — Feedback loop quotidien via Sonnet

Chaque soir (23h UTC) :
1. Analyse les trades du jour (gagnants + perdants)
2. Identifie les patterns (quelle niche, quel modèle, quel edge threshold)
3. Ajuste config.yaml automatiquement
4. Envoie un rapport d'apprentissage via Telegram
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


class DailyLearner:
    PRICE_IN  = 3.00 / 1_000_000
    PRICE_OUT = 15.00 / 1_000_000

    def __init__(self, session, config: dict, telegram=None):
        self.session = session
        self.config = config
        self.telegram = telegram
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(
                    api_key=os.environ.get('ANTHROPIC_API_KEY', '')
                )
            except Exception as e:
                logger.error(f"Sonnet client init error: {e}")
        return self._client

    def _get_today_signals(self) -> list:
        """Récupère tous les signaux du jour depuis la DB."""
        try:
            from core.database import Signal
            from sqlalchemy import and_
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            rows = (
                self.session.query(Signal)
                .filter(Signal.timestamp >= today.isoformat())
                .order_by(Signal.timestamp)
                .all()
            )
            return rows
        except Exception as e:
            logger.error(f"get_today_signals error: {e}")
            return []

    def _get_today_positions(self) -> list:
        """Récupère les positions ouvertes et fermées du jour."""
        try:
            from core.database import Position
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            rows = (
                self.session.query(Position)
                .filter(Position.entry_time >= today.isoformat())
                .all()
            )
            return rows
        except Exception as e:
            logger.error(f"get_today_positions error: {e}")
            return []

    def _build_analysis_prompt(self, signals: list, positions: list) -> str:
        """Construit le prompt d'analyse pour Sonnet."""
        # Résumé signals
        total = len(signals)
        bets_placed = sum(1 for s in signals if getattr(s, 'was_bet_placed', False))
        haiku_denied = sum(1 for s in signals if getattr(s, 'funnel_step', '') == 'haiku' and not getattr(s, 'haiku_confirmed', True))
        sonnet_nogo = sum(1 for s in signals if getattr(s, 'funnel_step', '') == 'sonnet' and not getattr(s, 'sonnet_go', True))

        # Par niche
        niche_stats = {}
        for s in signals:
            n = getattr(s, 'niche', 'unknown') or 'unknown'
            if n not in niche_stats:
                niche_stats[n] = {'total': 0, 'bets': 0, 'edges': []}
            niche_stats[n]['total'] += 1
            if getattr(s, 'was_bet_placed', False):
                niche_stats[n]['bets'] += 1
            edge = getattr(s, 'math_edge', 0) or 0
            if edge > 0:
                niche_stats[n]['edges'].append(round(edge, 3))

        niche_summary = "\n".join([
            f"  {n}: {v['total']} signals, {v['bets']} bets, avg_edge={sum(v['edges'])/len(v['edges']):.1%} max_edge={max(v['edges']):.1%}"
            for n, v in niche_stats.items() if v['edges']
        ])

        # Positions
        closed_pos = [p for p in positions if getattr(p, 'status', '') == 'closed']
        open_pos = [p for p in positions if getattr(p, 'status', '') == 'open']
        wins = [p for p in closed_pos if (getattr(p, 'pnl_realized', 0) or 0) > 0]
        losses = [p for p in closed_pos if (getattr(p, 'pnl_realized', 0) or 0) < 0]
        total_pnl = sum(getattr(p, 'pnl_realized', 0) or 0 for p in closed_pos)

        positions_detail = "\n".join([
            f"  {getattr(p, 'bot_niche', '?')} | {getattr(p, 'market_question', '?')[:50]} | "
            f"dir={getattr(p, 'direction', '?')} | pnl={getattr(p, 'pnl_realized', 0):+.2f} | "
            f"edge={getattr(p, 'math_edge', 0):.1%} | conf={getattr(p, 'confidence', '?')}"
            for p in closed_pos
        ]) or "  Aucune position fermée aujourd'hui"

        config_current = json.dumps({
            'min_adj_edge': self.config.get('filters', {}).get('min_math_edge', 0.03),
            'direct_bet_threshold': '8%',
            'sonnet_threshold': '5-8%',
        }, indent=2)

        return f"""Tu es l'analyste du bot de trading Polymarket. Analyse les résultats du jour et propose des ajustements.

## RÉSUMÉ DU JOUR
- Signals analysés : {total}
- Bets placés : {bets_placed}
- Haiku denied : {haiku_denied} (désactivé aujourd'hui)
- Sonnet no-go : {sonnet_nogo}
- Positions fermées : {len(closed_pos)} (wins: {len(wins)}, losses: {len(losses)})
- PnL du jour : ${total_pnl:+.2f}
- Positions ouvertes : {len(open_pos)}

## PAR NICHE
{niche_summary if niche_summary else "  Aucune donnée de niche"}

## POSITIONS FERMÉES AUJOURD'HUI
{positions_detail}

## CONFIG ACTUELLE
{config_current}

## TA MISSION
Réponds en JSON strict avec ce format :
{{
  "verdict": "BON/MOYEN/MAUVAIS",
  "top_niches": ["niche1", "niche2"],
  "bad_niches": ["niche3"],
  "edge_threshold_adjustment": 0.0,  // +0.01 pour augmenter, -0.01 pour baisser
  "direct_bet_threshold_adjustment": 0.0,  // modifier le seuil 8%
  "key_learnings": ["apprentissage 1", "apprentissage 2"],
  "action_items": ["action 1", "action 2"],
  "confidence_in_analysis": "HIGH/MEDIUM/LOW"
}}

Sois factuel. Si peu de données (premier jour), dis-le dans key_learnings."""

    def _apply_adjustments(self, analysis: dict) -> dict:
        """Applique les ajustements recommandés à config.yaml."""
        changes = {}
        config_path = Path("config.yaml")
        if not config_path.exists():
            return changes

        import yaml
        with open(config_path) as f:
            config = yaml.safe_load(f)

        # Ajuster edge threshold
        edge_adj = float(analysis.get('edge_threshold_adjustment', 0))
        if abs(edge_adj) >= 0.005:
            current = config.get('filters', {}).get('min_math_edge', 0.03)
            new_val = round(max(0.02, min(0.15, current + edge_adj)), 3)
            config.setdefault('filters', {})['min_math_edge'] = new_val
            changes['min_math_edge'] = f"{current:.2%} → {new_val:.2%}"

        if changes:
            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            logger.info(f"DailyLearner: config ajustée: {changes}")

        return changes

    def run_daily_analysis(self) -> dict:
        """Lance l'analyse quotidienne complète."""
        logger.info("DailyLearner: démarrage analyse quotidienne...")

        client = self._get_client()
        if not client:
            return {"error": "Sonnet unavailable"}

        signals = self._get_today_signals()
        positions = self._get_today_positions()

        if not signals and not positions:
            logger.info("DailyLearner: pas de données aujourd'hui — skip")
            return {"error": "no_data"}

        prompt = self._build_analysis_prompt(signals, positions)

        try:
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )

            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = tokens_in * self.PRICE_IN + tokens_out * self.PRICE_OUT

            text = response.content[0].text.strip()

            # Parser le JSON
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                analysis = {"raw": text}

            logger.info(f"DailyLearner: analyse OK | verdict={analysis.get('verdict','?')} | cost=${cost:.4f}")

            # Appliquer les ajustements
            changes = self._apply_adjustments(analysis)
            analysis['config_changes'] = changes

            # Rapport Telegram
            if self.telegram:
                self._send_learning_report(analysis, signals, positions, cost)

            # Enregistrer en DB
            try:
                from core.database import save_kpi
                save_kpi(self.session, niche=None, period='daily_learning', metrics=analysis)
            except Exception:
                pass

            return analysis

        except Exception as e:
            logger.error(f"DailyLearner Sonnet error: {e}", exc_info=True)
            return {"error": str(e)}

    def _send_learning_report(self, analysis: dict, signals: list, positions: list, cost: float):
        """Envoie le rapport d'apprentissage via Telegram."""
        verdict = analysis.get('verdict', '?')
        emoji = "🟢" if verdict == "BON" else ("🟡" if verdict == "MOYEN" else "🔴")

        learnings = "\n".join(f"• {l}" for l in analysis.get('key_learnings', [])[:3])
        actions = "\n".join(f"→ {a}" for a in analysis.get('action_items', [])[:3])
        changes = analysis.get('config_changes', {})
        changes_str = "\n".join(f"• {k}: {v}" for k, v in changes.items()) if changes else "Aucun changement"

        top_niches = ", ".join(analysis.get('top_niches', []))
        bad_niches = ", ".join(analysis.get('bad_niches', []))

        msg = (
            f"<b>🧠 APPRENTISSAGE QUOTIDIEN</b>\n"
            f"───────────────────\n"
            f"{emoji} Verdict : <b>{verdict}</b>\n"
            f"📊 {len(signals)} signals | {len(positions)} positions | coût Sonnet: ${cost:.4f}\n"
            f"\n<b>✅ Niches fortes :</b> {top_niches or 'N/A'}\n"
            f"<b>❌ Niches à éviter :</b> {bad_niches or 'N/A'}\n"
            f"\n<b>💡 Apprentissages :</b>\n{learnings}\n"
            f"\n<b>⚙️ Actions :</b>\n{actions}\n"
            f"\n<b>🔧 Config ajustée :</b>\n{changes_str}"
        )
        self.telegram._send(msg)
