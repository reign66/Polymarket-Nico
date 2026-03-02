import json
import logging
import os
from typing import Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class HaikuResult:
    confirmed: bool
    adjusted_edge: float
    reason: str
    tokens_used: int = 0
    cost_usd: float = 0.0

class HaikuConfirmer:
    MODEL = 'claude-haiku-4-5-20251001'
    MAX_TOKENS = 250
    # Haiku pricing: $0.80/1M input, $4.00/1M output
    INPUT_COST_PER_TOKEN = 0.80 / 1_000_000
    OUTPUT_COST_PER_TOKEN = 4.00 / 1_000_000

    def __init__(self, config: dict, db_session):
        self.config = config
        self.session = db_session
        self.max_daily = config.get('api_limits', {}).get('max_haiku_calls_per_day', 20)

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

    def confirm_edge(self, market, model_result, edge_result) -> HaikuResult:
        """Use Haiku to confirm or deny the mathematical edge.
        Called ONLY when math model finds edge > 8%."""

        # Guard: check daily limit
        from core.database import get_daily_api_calls
        daily_calls = get_daily_api_calls(self.session, 'haiku')
        if daily_calls >= self.max_daily:
            logger.warning(f"Haiku daily limit reached ({daily_calls}/{self.max_daily})")
            return HaikuResult(confirmed=False, adjusted_edge=0, reason="daily limit reached")

        if not self.enabled or not self.client:
            logger.warning("Haiku not available (no API key)")
            return HaikuResult(confirmed=False, adjusted_edge=0, reason="API not configured")

        try:
            # Build SHORT prompt (minimize tokens = minimize cost)
            direction = edge_result.best_direction
            edge_pct = edge_result.best_edge * 100
            conf_pct = model_result.confidence * 100

            prompt = (
                f'Marché: "{market.question}"\n'
                f'Prix YES: {market.yes_price:.2f} | Prix NO: {market.no_price:.2f}\n'
                f'Mon modèle dit: proba={model_result.probability:.0%}, '
                f'edge={edge_pct:.0f}% vers {direction}\n'
                f'Méthode: {model_result.method}\n'
                f'Facteurs: {json.dumps(model_result.factors, default=str)[:200]}\n'
                f'Confirmes-tu cet edge? Réponds JSON uniquement:\n'
                f'{{"confirmed":true/false,"adjusted_edge":float,"reason":"max 10 mots"}}'
            )

            response = self.client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )

            # Parse response
            text = response.content[0].text.strip()
            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = tokens_in * self.INPUT_COST_PER_TOKEN + tokens_out * self.OUTPUT_COST_PER_TOKEN

            # Log API usage
            from core.database import record_api_call

            # Parse JSON from response (handle markdown code blocks)
            json_text = text
            if '```' in json_text:
                json_text = json_text.split('```')[1]
                if json_text.startswith('json'):
                    json_text = json_text[4:]
                json_text = json_text.strip()

            # Find JSON object
            start = json_text.find('{')
            end = json_text.rfind('}')
            if start >= 0 and end > start:
                json_text = json_text[start:end+1]

            parsed = json.loads(json_text)
            confirmed = parsed.get('confirmed', False)
            adjusted_edge = float(parsed.get('adjusted_edge', 0))
            reason = str(parsed.get('reason', ''))[:100]

            # Check if adjusted edge meets threshold
            min_confirmed = self.config.get('filters', {}).get('min_confirmed_edge', 0.12)
            if confirmed and adjusted_edge < min_confirmed:
                confirmed = False
                reason = f"adjusted edge {adjusted_edge:.0%} below {min_confirmed:.0%} threshold"

            record_api_call(self.session, 'haiku', tokens_in, tokens_out, cost,
                          market_id=market.market_id, was_useful=confirmed)

            logger.info(f"Haiku [{market.market_id[:8]}]: confirmed={confirmed}, "
                       f"adj_edge={adjusted_edge:.1%}, reason='{reason}', cost=${cost:.4f}")

            return HaikuResult(
                confirmed=confirmed,
                adjusted_edge=adjusted_edge,
                reason=reason,
                tokens_used=tokens_in + tokens_out,
                cost_usd=cost
            )

        except json.JSONDecodeError as e:
            logger.error(f"Haiku JSON parse error: {e}")
            record_api_call(self.session, 'haiku', 0, 0, 0,
                          market_id=market.market_id, was_useful=False)
            return HaikuResult(confirmed=False, adjusted_edge=0, reason=f"JSON parse error: {e}")
        except Exception as e:
            logger.error(f"Haiku error: {e}")
            return HaikuResult(confirmed=False, adjusted_edge=0, reason=f"Error: {str(e)[:50]}")
