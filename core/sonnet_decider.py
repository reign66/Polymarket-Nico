import json
import logging
import os
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class SonnetResult:
    go: bool
    direction: str         # YES, NO
    confidence: str        # HIGH, MEDIUM
    edge_estimate: float
    rationale: str
    risk: str
    tokens_used: int = 0
    cost_usd: float = 0.0

class SonnetDecider:
    MODEL = 'claude-sonnet-4-5-20250929'
    MAX_TOKENS = 400
    # Sonnet pricing: $3.00/1M input, $15.00/1M output
    INPUT_COST_PER_TOKEN = 3.00 / 1_000_000
    OUTPUT_COST_PER_TOKEN = 15.00 / 1_000_000

    def __init__(self, config: dict, db_session):
        self.config = config
        self.session = db_session
        self.max_daily = config.get('api_limits', {}).get('max_sonnet_calls_per_day', 5)

        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        self.enabled = bool(api_key)
        self.client = None
        if self.enabled:
            try:
                from anthropic import Anthropic
                self.client = Anthropic(api_key=api_key)
            except Exception as e:
                logger.error(f"Failed to init Anthropic client: {e}")
                self.enabled = False

    def decide_bet(self, market, model_result, edge_result, haiku_result) -> SonnetResult:
        """Final decision: should we place this bet?
        Called ONLY when Haiku confirms the edge."""

        # Guard: check daily limit
        from core.database import get_daily_api_calls
        daily_calls = get_daily_api_calls(self.session, 'sonnet')
        if daily_calls >= self.max_daily:
            logger.warning(f"Sonnet daily limit reached ({daily_calls}/{self.max_daily})")
            return SonnetResult(go=False, direction='SKIP', confidence='LOW',
                              edge_estimate=0, rationale="daily limit reached", risk="N/A")

        if not self.enabled or not self.client:
            logger.warning("Sonnet not available (no API key)")
            return SonnetResult(go=False, direction='SKIP', confidence='LOW',
                              edge_estimate=0, rationale="API not configured", risk="N/A")

        try:
            # Calculate days to resolution
            days_left = "unknown"
            if market.end_date:
                try:
                    from datetime import datetime
                    end = datetime.fromisoformat(market.end_date.replace('Z', '+00:00'))
                    days_left = max(0, (end - datetime.now(end.tzinfo)).days)
                except:
                    pass

            prompt = (
                f'Tu es analyste de marchés de prédiction.\n'
                f'MARCHÉ: "{market.question}"\n'
                f'PRIX: YES={market.yes_price:.2f}$ NO={market.no_price:.2f}$ '
                f'Volume=${market.volume:,.0f}\n'
                f'RÉSOLUTION: {market.end_date or "N/A"} ({days_left} jours)\n'
                f'MON MODÈLE: proba={model_result.probability:.0%} '
                f'(méthode: {model_result.method})\n'
                f'EDGE CALCULÉ: {edge_result.best_edge:.0%} vers {edge_result.best_direction}\n'
                f'FACTEURS: {json.dumps(model_result.factors, default=str)[:300]}\n'
                f'HAIKU DIT: {haiku_result.reason}\n'
                f'Analyse et retourne JSON uniquement:\n'
                f'{{"go":true/false,"direction":"YES"|"NO",'
                f'"confidence":"HIGH"|"MEDIUM",'
                f'"edge_estimate":float,"rationale":"max 20 mots",'
                f'"risk":"max 15 mots"}}'
            )

            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = tokens_in * self.INPUT_COST_PER_TOKEN + tokens_out * self.OUTPUT_COST_PER_TOKEN

            # Parse JSON
            json_text = text
            if '```' in json_text:
                json_text = json_text.split('```')[1]
                if json_text.startswith('json'):
                    json_text = json_text[4:]
                json_text = json_text.strip()
            start = json_text.find('{')
            end = json_text.rfind('}')
            if start >= 0 and end > start:
                json_text = json_text[start:end+1]

            parsed = json.loads(json_text)

            go = parsed.get('go', False)
            direction = parsed.get('direction', 'SKIP')
            confidence = parsed.get('confidence', 'LOW')
            edge_est = float(parsed.get('edge_estimate', 0))
            rationale = str(parsed.get('rationale', ''))[:200]
            risk = str(parsed.get('risk', ''))[:150]

            # Final guard: only go if confidence is HIGH or MEDIUM
            min_final_edge = self.config.get('filters', {}).get('min_final_edge', 0.15)
            if go and confidence not in ('HIGH', 'MEDIUM'):
                go = False
                rationale = f"Low confidence override: {rationale}"
            if go and edge_est < min_final_edge:
                go = False
                rationale = f"Edge {edge_est:.0%} below {min_final_edge:.0%} threshold"

            from core.database import record_api_call
            record_api_call(self.session, 'sonnet', tokens_in, tokens_out, cost,
                          market_id=market.market_id, was_useful=go)

            logger.info(f"Sonnet [{market.market_id[:8]}]: go={go}, dir={direction}, "
                       f"conf={confidence}, edge={edge_est:.1%}, cost=${cost:.4f}")

            return SonnetResult(
                go=go, direction=direction, confidence=confidence,
                edge_estimate=edge_est, rationale=rationale, risk=risk,
                tokens_used=tokens_in + tokens_out, cost_usd=cost
            )

        except json.JSONDecodeError as e:
            logger.error(f"Sonnet JSON parse error: {e}")
            record_api_call(self.session, 'sonnet', 0, 0, 0,
                          market_id=market.market_id, was_useful=False)
            return SonnetResult(go=False, direction='SKIP', confidence='LOW',
                              edge_estimate=0, rationale=f"JSON error", risk="parse failure")
        except Exception as e:
            logger.error(f"Sonnet error: {e}")
            return SonnetResult(go=False, direction='SKIP', confidence='LOW',
                              edge_estimate=0, rationale=f"Error: {str(e)[:50]}", risk="N/A")
