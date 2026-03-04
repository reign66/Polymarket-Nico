"""
core/sonnet_decider.py — V2.1 Sonnet final decision.
Min final edge lowered to 6% (was 15%).
"""

import os
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SonnetResult:
    go: bool
    direction: str
    confidence: str   # HIGH / MEDIUM / LOW
    edge_estimate: float
    rationale: str
    risk: str


class SonnetDecider:
    PRICE_IN = 3.00 / 1_000_000   # $3 per 1M input tokens
    PRICE_OUT = 15.00 / 1_000_000  # $15 per 1M output tokens

    def __init__(self, config: dict, session):
        self.config = config
        self.session = session
        self.limits = config.get('api_limits', {})
        self.filters = config.get('filters', {})
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(
                    api_key=os.environ.get('ANTHROPIC_API_KEY', '')
                )
            except Exception as e:
                logger.error(f"Failed to init Anthropic client: {e}")
        return self._client

    def decide_bet(self, market, model_result: dict, edge_result, haiku_result) -> SonnetResult:
        """Make the final go/no-go bet decision."""
        from core.database import get_daily_api_calls, record_api_call

        max_calls = self.limits.get('max_sonnet_calls_per_day', 5)
        daily_calls = get_daily_api_calls(self.session, 'sonnet')

        if daily_calls >= max_calls:
            return SonnetResult(
                go=False,
                direction=edge_result.best_direction,
                confidence='LOW',
                edge_estimate=edge_result.best_edge,
                rationale=f"Daily Sonnet limit reached ({daily_calls}/{max_calls})",
                risk="limit"
            )

        client = self._get_client()
        if not client:
            return SonnetResult(
                go=False,
                direction=edge_result.best_direction,
                confidence='LOW',
                edge_estimate=edge_result.best_edge,
                rationale="Anthropic client unavailable",
                risk="client_error"
            )

        question = market.question if hasattr(market, 'question') else market.get('question', '')
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        volume = market.volume if hasattr(market, 'volume') else market.get('volume', 0)
        end_date = market.end_date if hasattr(market, 'end_date') else market.get('end_date', '')

        prompt = (
            f"You are a prediction market betting strategist. Make a final bet decision.\n\n"
            f"Market: {question}\n"
            f"Current price (YES): {yes_price:.2f} | Volume: ${volume:,.0f}\n"
            f"Resolves: {end_date}\n\n"
            f"Math model analysis:\n"
            f"  Method: {model_result.get('method', 'unknown')}\n"
            f"  Probability: {model_result.get('probability', 0.5):.2f}\n"
            f"  Confidence: {model_result.get('confidence', 0.05):.0%}\n"
            f"  Reasoning: {model_result.get('reasoning', '')}\n\n"
            f"Edge analysis:\n"
            f"  Direction: {edge_result.best_direction}\n"
            f"  Raw edge: {edge_result.best_edge:.1%}\n"
            f"  Adjusted edge: {edge_result.confidence_adjusted_edge:.1%}\n"
            f"  Kelly fraction: {edge_result.kelly_fraction:.3f}\n\n"
            f"Haiku confirmation: {haiku_result.reason}\n\n"
            f"Respond in EXACTLY this format:\n"
            f"GO: YES or NO\n"
            f"DIRECTION: YES or NO\n"
            f"CONFIDENCE: HIGH or MEDIUM or LOW\n"
            f"EDGE: (number like 0.07)\n"
            f"RATIONALE: (one sentence)\n"
            f"RISK: (one sentence about main risk)"
        )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}]
            )

            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = tokens_in * self.PRICE_IN + tokens_out * self.PRICE_OUT

            record_api_call(
                self.session,
                model='sonnet',
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                market_id=edge_result.market_id,
                was_useful=False,
            )

            text = response.content[0].text.strip()
            lines = {k.strip(): v.strip() for line in text.splitlines()
                     if ':' in line for k, v in [line.split(':', 1)]}

            go_str = lines.get('GO', 'NO').upper()
            direction = lines.get('DIRECTION', edge_result.best_direction).upper()
            confidence = lines.get('CONFIDENCE', 'LOW').upper()
            try:
                edge_est = float(lines.get('EDGE', str(edge_result.best_edge)))
            except ValueError:
                edge_est = edge_result.best_edge
            rationale = lines.get('RATIONALE', 'No rationale provided')
            risk = lines.get('RISK', 'No risk analysis')

            go = (go_str == 'YES')

            # Guards: V2.1 threshold = 6%
            min_final_edge = self.filters.get('min_final_edge', 0.06)

            if go and confidence not in ['HIGH', 'MEDIUM']:
                go = False
                rationale = f"Sonnet confidence {confidence} too low (need HIGH or MEDIUM)"

            if go and edge_result.confidence_adjusted_edge < min_final_edge:
                go = False
                rationale = (
                    f"Adj edge {edge_result.confidence_adjusted_edge:.1%} < "
                    f"min_final_edge {min_final_edge:.0%}"
                )

            if go and direction not in ['YES', 'NO']:
                direction = edge_result.best_direction

            if go:
                # Update was_useful
                try:
                    from core.database import ApiUsage
                    from sqlalchemy import desc
                    latest = (
                        self.session.query(ApiUsage)
                        .filter(ApiUsage.model == 'sonnet', ApiUsage.market_id == edge_result.market_id)
                        .order_by(desc(ApiUsage.timestamp))
                        .first()
                    )
                    if latest:
                        latest.was_useful = True
                        self.session.commit()
                except Exception:
                    pass

            logger.info(
                f"Sonnet [{edge_result.market_id}]: {'GO' if go else 'NO-GO'} "
                f"| dir={direction} conf={confidence} | {rationale[:60]}"
            )

            return SonnetResult(
                go=go,
                direction=direction,
                confidence=confidence,
                edge_estimate=edge_est,
                rationale=rationale,
                risk=risk,
            )

        except Exception as e:
            logger.error(f"Sonnet API error: {e}", exc_info=True)
            return SonnetResult(
                go=False,
                direction=edge_result.best_direction,
                confidence='LOW',
                edge_estimate=edge_result.best_edge,
                rationale=f"API error: {str(e)[:50]}",
                risk="api_error"
            )
