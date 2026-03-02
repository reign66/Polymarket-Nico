import json
import logging
import os
from typing import Optional, Dict, List
from anthropic import Anthropic

from core.database import record_api_call, check_daily_sonnet_limit

logger = logging.getLogger(__name__)


class SonnetDecider:
    """
    Second-pass decision engine using Claude Sonnet to perform a deep analysis
    of a candidate trade identified by HaikuClassifier. Sonnet calls are rate-limited
    per day per niche to control costs.
    """

    SONNET_MODEL = "claude-sonnet-4-5-20250929"
    # Pricing in USD per token
    INPUT_COST_PER_TOKEN = 3.00 / 1_000_000    # $3.00 per 1M input tokens
    OUTPUT_COST_PER_TOKEN = 15.00 / 1_000_000  # $15.00 per 1M output tokens

    EDGE_THRESHOLD = 0.15

    def __init__(self, db_session):
        self.client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.db_session = db_session

    def _build_prompt(
        self,
        haiku_result: dict,
        market_details: dict,
        news_context: dict,
        cii_scores: list,
        trending_keywords: list,
    ) -> str:
        summary = news_context.get("summary", "N/A")
        end_date = market_details.get("end_date", "N/A")

        cii_str = str(cii_scores) if cii_scores else "N/A"
        keywords_str = str(trending_keywords) if trending_keywords else "N/A"

        return (
            "Tu es un analyste de marchés de prédiction senior avec 10 ans d'expérience.\n\n"
            "NEWS:\n"
            f"Titre: {news_context['title']}\n"
            f"Résumé: {summary}\n\n"
            "MARCHÉ POLYMARKET:\n"
            f"Question: {market_details['question']}\n"
            f"Prix YES: {market_details['yes_price']}$\n"
            f"Prix NO: {market_details['no_price']}$\n"
            f"Volume: ${market_details['volume']}\n"
            f"Résolution: {end_date}\n\n"
            "CONTEXTE ADDITIONNEL:\n"
            f"Scores CII: {cii_str}\n"
            f"Keywords tendance: {keywords_str}\n"
            f"Score Haiku: {haiku_result['relevance']} | "
            f"Edge Haiku YES: {haiku_result['estimated_edge_yes']} | "
            f"Edge Haiku NO: {haiku_result['estimated_edge_no']}\n\n"
            "Analyse les DEUX directions (YES et NO) en profondeur.\n"
            "Retourne UNIQUEMENT un JSON valide:\n"
            '{"probability_real": 0.0-1.0, "edge_yes": float, "edge_no": float, '
            '"direction": "YES|NO|SKIP", "confidence": "LOW|MEDIUM|HIGH", '
            '"bet_rationale": "max 2 sentences", "risk_factors": ["factor1", "factor2"]}'
        )

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.INPUT_COST_PER_TOKEN
            + output_tokens * self.OUTPUT_COST_PER_TOKEN
        )

    def _parse_json_response(self, content: str) -> Optional[dict]:
        """Extract and parse JSON from the model response text."""
        content = content.strip()
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            logger.error("SonnetDecider: no JSON object found in response: %s", content)
            return None
        json_str = content[start : end + 1]
        return json.loads(json_str)

    def decide_bet(
        self,
        haiku_result: dict,
        market_details: dict,
        news_context: dict,
        cii_scores: list = None,
        trending_keywords: list = None,
    ) -> Optional[dict]:
        """
        Perform a deep analysis to decide whether to place a bet and in which direction.

        Returns a decision dict with direction, confidence, and rationale,
        or None if the call should be skipped (daily limit reached, low confidence,
        insufficient edge, or any error).
        """
        bot_niche = haiku_result.get("niche", "unknown")

        # Check daily Sonnet call limit before making the (expensive) API call.
        # check_daily_sonnet_limit returns True when another call IS allowed.
        try:
            if not check_daily_sonnet_limit(session=self.db_session, bot_niche=bot_niche):
                logger.warning(
                    "SonnetDecider: daily Sonnet limit reached for niche '%s', skipping.",
                    bot_niche,
                )
                return None
        except Exception as limit_err:
            logger.error(
                "SonnetDecider: failed to check daily limit: %s", limit_err, exc_info=True
            )
            return None

        try:
            prompt = self._build_prompt(
                haiku_result=haiku_result,
                market_details=market_details,
                news_context=news_context,
                cii_scores=cii_scores,
                trending_keywords=trending_keywords,
            )

            response = self.client.messages.create(
                model=self.SONNET_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost_usd = self._calculate_cost(input_tokens, output_tokens)

            raw_text = response.content[0].text if response.content else ""

            try:
                result = self._parse_json_response(raw_text)
            except (json.JSONDecodeError, ValueError) as parse_err:
                logger.error(
                    "SonnetDecider: JSON parsing failed: %s | raw: %s",
                    parse_err,
                    raw_text,
                )
                try:
                    record_api_call(
                        session=self.db_session,
                        model=self.SONNET_MODEL,
                        bot_niche=bot_niche,
                        tokens_in=input_tokens,
                        tokens_out=output_tokens,
                        cost_usd=cost_usd,
                    )
                except Exception as db_err:
                    logger.error("SonnetDecider: failed to record API call: %s", db_err)
                return None

            if result is None:
                return None

            try:
                record_api_call(
                    session=self.db_session,
                    model=self.SONNET_MODEL,
                    bot_niche=bot_niche,
                    tokens_in=input_tokens,
                    tokens_out=output_tokens,
                    cost_usd=cost_usd,
                )
            except Exception as db_err:
                logger.error("SonnetDecider: failed to record API call: %s", db_err)

            confidence = result.get("confidence", "LOW")
            edge_yes = result.get("edge_yes", 0.0)
            edge_no = result.get("edge_no", 0.0)
            max_edge = max(edge_yes, edge_no)

            if confidence == "LOW" or max_edge < self.EDGE_THRESHOLD:
                logger.info(
                    "SonnetDecider: bet skipped — confidence=%s, max_edge=%.2f (min %.2f) "
                    "| niche=%s | question: %s",
                    confidence,
                    max_edge,
                    self.EDGE_THRESHOLD,
                    bot_niche,
                    market_details.get("question", "N/A"),
                )
                result["direction"] = "SKIP"

            logger.info(
                "SonnetDecider: decision=%s, confidence=%s, prob_real=%.2f, "
                "edge_yes=%.2f, edge_no=%.2f | niche=%s | question: %s",
                result.get("direction", "N/A"),
                confidence,
                result.get("probability_real", 0.0),
                edge_yes,
                edge_no,
                bot_niche,
                market_details.get("question", "N/A"),
            )

            return result

        except Exception as api_err:
            error_str = str(api_err).lower()
            if "rate" in error_str or "429" in error_str:
                logger.warning(
                    "SonnetDecider: rate limited by Anthropic API: %s", api_err
                )
            else:
                logger.error(
                    "SonnetDecider: API call failed: %s", api_err, exc_info=True
                )
            return None
