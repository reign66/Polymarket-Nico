"""
core/haiku_confirmer.py — V2.1 Haiku edge confirmation.
Threshold lowered: 5% (was 12%).
"""

import os
import logging
import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class HaikuResult:
    confirmed: bool
    adjusted_edge: float
    reason: str
    tokens_used: int = 0


class HaikuConfirmer:
    # Haiku 4.5 pricing per token
    PRICE_IN = 0.80 / 1_000_000   # $0.80 per 1M input tokens
    PRICE_OUT = 4.00 / 1_000_000  # $4.00 per 1M output tokens

    def __init__(self, config: dict, session):
        self.config = config
        self.session = session
        self.limits = config.get('api_limits', {})
        self.filters = config.get('filters', {})
        self._client = None
        # Hard in-memory daily counter — guards against DB race conditions
        self._calls_today: int = 0
        self._calls_date: datetime.date | None = None
        self._api_disabled: bool = False

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

    def confirm_edge(self, market, model_result: dict, edge_result) -> HaikuResult:
        """Confirm or deny an edge found by the math model."""
        from core.database import record_api_call

        max_calls = self.limits.get('max_haiku_calls_per_day', 30)

        # In-memory check (fast, no DB race condition)
        if self._api_disabled:
            return HaikuResult(
                confirmed=False,
                adjusted_edge=edge_result.confidence_adjusted_edge,
                reason="Haiku API disabled (credit/auth error)"
            )
        today = datetime.date.today()
        if self._calls_date != today:
            self._calls_today = 0
            self._calls_date = today
        if self._calls_today >= max_calls:
            return HaikuResult(
                confirmed=False,
                adjusted_edge=edge_result.confidence_adjusted_edge,
                reason=f"Daily Haiku limit reached ({self._calls_today}/{max_calls})"
            )

        client = self._get_client()
        if not client:
            return HaikuResult(
                confirmed=False,
                adjusted_edge=edge_result.confidence_adjusted_edge,
                reason="Anthropic client unavailable"
            )

        question = market.question if hasattr(market, 'question') else market.get('question', '')
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        direction = edge_result.best_direction
        edge_pct = edge_result.best_edge * 100

        prompt = (
            f"You are a pragmatic prediction market analyst. Approve or reject this trading edge.\n\n"
            f"Market: {question}\n"
            f"Market price (YES): {yes_price:.2f}\n"
            f"Model probability: {model_result.get('probability', 0.5):.2f}\n"
            f"Model method: {model_result.get('method', 'unknown')}\n"
            f"Model reasoning: {model_result.get('reasoning', '')}\n"
            f"Suggested direction: {direction}\n"
            f"Raw edge: +{edge_pct:.1f}%\n\n"
            f"RULES:\n"
            f"- CONFIRM if the method is data-driven (Elo, GBM, momentum, RF, base rate, stats)\n"
            f"- CONFIRM if edge is between 2% and 50%\n"
            f"- DENY only if: edge >55% (extreme model error) OR method is pure guessing\n"
            f"- A large edge (15-45%) from RF or momentum is NORMAL — do not penalize it\n"
            f"- When in doubt, CONFIRM. False negatives cost money.\n\n"
            f"Reply: CONFIRM or DENY, then one sentence.\n"
            f"Example: CONFIRM Momentum edge is valid given recent price movement."
        )

        # Increment BEFORE the call so concurrent calls can't slip through
        self._calls_today += 1
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=80,
                messages=[{"role": "user", "content": prompt}]
            )

            tokens_in = response.usage.input_tokens
            tokens_out = response.usage.output_tokens
            cost = tokens_in * self.PRICE_IN + tokens_out * self.PRICE_OUT

            record_api_call(
                self.session,
                model='haiku',
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                market_id=edge_result.market_id,
                was_useful=False,  # Updated below if confirmed
            )

            text = response.content[0].text.strip()
            confirmed = text.upper().startswith('CONFIRM')
            reason = text[7:].strip() if confirmed else text[5:].strip()

            # V2.1: threshold lowered to 5% (was 12%)
            min_confirmed_edge = self.filters.get('min_confirmed_edge', 0.02)
            adj_edge = edge_result.confidence_adjusted_edge

            if confirmed and adj_edge < min_confirmed_edge:
                confirmed = False
                reason = f"Haiku confirmed but adj_edge {adj_edge:.1%} < {min_confirmed_edge:.0%}"
            elif confirmed:
                # Update was_useful on the record
                try:
                    from core.database import ApiUsage
                    from sqlalchemy import desc
                    latest = (
                        self.session.query(ApiUsage)
                        .filter(ApiUsage.model == 'haiku', ApiUsage.market_id == edge_result.market_id)
                        .order_by(desc(ApiUsage.timestamp))
                        .first()
                    )
                    if latest:
                        latest.was_useful = True
                        self.session.commit()
                except Exception:
                    pass

            logger.info(
                f"Haiku [{edge_result.market_id}]: {'CONFIRMED' if confirmed else 'DENIED'} "
                f"| adj_edge={adj_edge:.1%} | {reason[:60]}"
            )

            return HaikuResult(
                confirmed=confirmed,
                adjusted_edge=adj_edge,
                reason=reason,
                tokens_used=tokens_in + tokens_out,
            )

        except Exception as e:
            err_str = str(e).lower()
            if any(x in err_str for x in ("credit", "balance", "402", "401", "insufficient")):
                logger.warning(f"Haiku API disabled: {e}")
                self._api_disabled = True
            else:
                logger.error(f"Haiku API error: {e}", exc_info=True)
            # Don't charge the counter if the call failed
            self._calls_today -= 1
            return HaikuResult(
                confirmed=False,
                adjusted_edge=edge_result.confidence_adjusted_edge,
                reason=f"API error: {str(e)[:50]}"
            )
