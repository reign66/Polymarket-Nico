import json
import logging
import os
from typing import Optional, Dict, List
from anthropic import Anthropic

from core.database import record_api_call

logger = logging.getLogger(__name__)


class HaikuClassifier:
    """
    First-pass classifier using Claude Haiku to quickly evaluate whether a news item
    is relevant to available Polymarket markets and estimate the potential edge.
    """

    HAIKU_MODEL = "claude-haiku-4-5-20251001"
    # Pricing in USD per token
    INPUT_COST_PER_TOKEN = 0.80 / 1_000_000   # $0.80 per 1M input tokens
    OUTPUT_COST_PER_TOKEN = 4.00 / 1_000_000  # $4.00 per 1M output tokens

    RELEVANCE_THRESHOLD = 0.70
    EDGE_THRESHOLD = 0.12

    def __init__(self, db_session):
        self.client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        self.db_session = db_session

    def _format_markets(self, available_markets: list) -> str:
        lines = []
        for market in available_markets:
            lines.append(
                f"- id: {market.get('id', 'N/A')} | "
                f"question: {market.get('question', 'N/A')} | "
                f"yes_price: {market.get('yes_price', 'N/A')} | "
                f"no_price: {market.get('no_price', 'N/A')}"
            )
        return "\n".join(lines) if lines else "Aucun marché disponible"

    def _build_prompt(self, news_item: dict, available_markets: list) -> str:
        formatted_markets = self._format_markets(available_markets)
        summary = news_item.get("summary", news_item.get("description", "N/A"))

        return (
            "Analyse cette news pour les marchés de prédiction Polymarket.\n\n"
            "NEWS:\n"
            f"Titre: {news_item['title']}\n"
            f"Résumé: {summary}\n\n"
            "MARCHÉS POLYMARKET DISPONIBLES:\n"
            f"{formatted_markets}\n\n"
            'Retourne UNIQUEMENT un JSON valide:\n'
            '{"niche": "category", "relevance": 0.0-1.0, "market_id": "id or null", '
            '"estimated_edge_yes": 0.0-0.5, "estimated_edge_no": 0.0-0.5, '
            '"best_direction": "YES|NO|SKIP", "rationale": "one sentence explanation"}\n\n'
            "IMPORTANT: Évalue les DEUX directions (YES et NO). "
            "L'edge est la différence entre ta probabilité estimée et le prix du marché."
        )

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.INPUT_COST_PER_TOKEN
            + output_tokens * self.OUTPUT_COST_PER_TOKEN
        )

    def _parse_json_response(self, content: str) -> Optional[dict]:
        """Extract and parse JSON from the model response text."""
        content = content.strip()
        # Find the first '{' and last '}' to extract the JSON block
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            logger.error("HaikuClassifier: no JSON object found in response: %s", content)
            return None
        json_str = content[start : end + 1]
        return json.loads(json_str)

    def classify_news(
        self, news_item: dict, available_markets: list
    ) -> Optional[dict]:
        """
        Classify a news item against available markets.

        Returns a classification dict if the news is relevant and has sufficient edge,
        or None if it should be skipped.
        """
        try:
            prompt = self._build_prompt(news_item, available_markets)

            response = self.client.messages.create(
                model=self.HAIKU_MODEL,
                max_tokens=300,
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
                    "HaikuClassifier: JSON parsing failed: %s | raw: %s",
                    parse_err,
                    raw_text,
                )
                # Still log the API call even on parse failure
                try:
                    record_api_call(
                        session=self.db_session,
                        model=self.HAIKU_MODEL,
                        bot_niche="unknown",
                        tokens_in=input_tokens,
                        tokens_out=output_tokens,
                        cost_usd=cost_usd,
                    )
                except Exception as db_err:
                    logger.error("HaikuClassifier: failed to record API call: %s", db_err)
                return None

            if result is None:
                return None

            bot_niche = result.get("niche", "unknown")

            try:
                record_api_call(
                    session=self.db_session,
                    model=self.HAIKU_MODEL,
                    bot_niche=bot_niche,
                    tokens_in=input_tokens,
                    tokens_out=output_tokens,
                    cost_usd=cost_usd,
                )
            except Exception as db_err:
                logger.error("HaikuClassifier: failed to record API call: %s", db_err)

            relevance = result.get("relevance", 0.0)
            edge_yes = result.get("estimated_edge_yes", 0.0)
            edge_no = result.get("estimated_edge_no", 0.0)
            max_edge = max(edge_yes, edge_no)

            if relevance < self.RELEVANCE_THRESHOLD or max_edge < self.EDGE_THRESHOLD:
                logger.info(
                    "HaikuClassifier: news skipped — relevance=%.2f (min %.2f), "
                    "max_edge=%.2f (min %.2f) | title: %s",
                    relevance,
                    self.RELEVANCE_THRESHOLD,
                    max_edge,
                    self.EDGE_THRESHOLD,
                    news_item.get("title", "N/A"),
                )
                return None

            logger.info(
                "HaikuClassifier: news accepted — niche=%s, relevance=%.2f, "
                "edge_yes=%.2f, edge_no=%.2f, direction=%s | title: %s",
                bot_niche,
                relevance,
                edge_yes,
                edge_no,
                result.get("best_direction", "N/A"),
                news_item.get("title", "N/A"),
            )

            return result

        except Exception as api_err:
            error_str = str(api_err).lower()
            if "rate" in error_str or "429" in error_str:
                logger.warning(
                    "HaikuClassifier: rate limited by Anthropic API: %s", api_err
                )
            else:
                logger.error(
                    "HaikuClassifier: API call failed: %s", api_err, exc_info=True
                )
            return None
