"""
Politics model V2.0 — Base rates indépendants du marché.

V1 (cassé): prob = yes_price + incumbency → edge = 0 toujours.
V2 (fix): base rates indépendants par type de question, comme EloModel pour NBA.

Sources des base rates:
- Historique elections US (incumbents win ~73% des midterms Senate)
- Marchés prédictifs historiques (résolutions Polymarket/PredictIt)
- Recherches académiques (forecasting tournaments)
"""

import time
import logging
import requests
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)

# ── BASE RATES PAR ENTITÉ (indépendants du marché) ──────────────────────────
# Probabilités basées sur données historiques, pas sur le prix Polymarket

# Candidats/partis US — base rate de victoire dans leur contexte
KNOWN_ENTITIES = {
    # Présidentielles
    'trump': 0.48, 'harris': 0.46, 'biden': 0.38, 'desantis': 0.12,
    'democrat': 0.48, 'republican': 0.50, 'gop': 0.50,
    # Institutions
    'fed': 0.70,   # Fed suit généralement les attentes du marché sur les taux
    'supreme court': 0.55,
    'congress': 0.48,
    'senate': 0.48,
    # International
    'macron': 0.55, 'le pen': 0.38, 'scholz': 0.40,
    'putin': 0.90, 'xi': 0.92,   # Régimes stables
}

# Types de questions et leurs base rates par défaut
QUESTION_BASE_RATES = {
    # Questions économiques — tendance historique
    'recession': 0.30,     # US recession ~30% sur 12 mois (moyenne historique)
    'rate cut': 0.55,      # Fed dovish recently
    'rate hike': 0.25,
    'inflation': 0.40,
    'unemployment': 0.35,
    # Tech/Business
    'ipo': 0.35,
    'acquisition': 0.40,
    'bankruptcy': 0.20,
    'launch': 0.55,
    'release': 0.60,
    # Politique générale
    'impeach': 0.12,
    'resign': 0.15,
    'arrest': 0.20,
    'indictment': 0.25,
    'convicted': 0.35,
    'election': 0.50,     # 50/50 par défaut
    'win': 0.50,
    'pass': 0.45,
    'bill': 0.40,
    'law': 0.40,
    'ban': 0.35,
    'sanction': 0.50,
    'war': 0.25,
    'peace': 0.35,
    'deal': 0.45,
    'agreement': 0.45,
    'default': 0.15,
    'crisis': 0.30,
    # Prix/Marchés
    'hit': 0.40,          # "will X hit $Y" — dépend du contexte
    'reach': 0.40,
    'above': 0.45,
    'below': 0.45,
    'before': 0.40,
    # Divers
    'die': 0.10,
    'survive': 0.75,
    'win award': 0.20,
}


class PoliticsModel(MathModel):
    """Politics/news model with independent base rates — V2.0"""

    def _detect_base_rate(self, question: str) -> tuple[float, float, str]:
        """
        Return (probability, confidence, method) independent of market price.
        Uses known entities + question type keywords.
        """
        q = question.lower()

        # 1. Check known entities first (more specific)
        entity_prob = None
        entity_name = None
        for entity, prob in KNOWN_ENTITIES.items():
            if entity in q:
                entity_prob = prob
                entity_name = entity
                break

        # 2. Detect question type from keywords
        kw_prob = None
        kw_name = None
        for keyword, prob in QUESTION_BASE_RATES.items():
            if keyword in q:
                kw_prob = prob
                kw_name = keyword
                break

        # 3. Combine: entity is more specific → weight it more
        if entity_prob is not None and kw_prob is not None:
            # Blend: 60% entity, 40% keyword context
            final_prob = entity_prob * 0.60 + kw_prob * 0.40
            confidence = 0.38
            method = f'politics_entity({entity_name})+kw({kw_name})'
        elif entity_prob is not None:
            final_prob = entity_prob
            confidence = 0.32
            method = f'politics_entity({entity_name})'
        elif kw_prob is not None:
            final_prob = kw_prob
            confidence = 0.28
            method = f'politics_kw({kw_name})'
        else:
            # No match — use 0.45 (slight NO lean, markets tend to be YES-biased)
            final_prob = 0.45
            confidence = 0.20
            method = 'politics_default'

        # Clip
        final_prob = max(0.03, min(0.97, final_prob))
        return final_prob, confidence, method

    def _fetch_momentum(self, market_id: str, external_data: dict) -> float:
        """7-day price momentum from DB or Gamma API."""
        history = []
        try:
            from core.database import get_price_history
            if external_data and 'session' in external_data:
                history = get_price_history(external_data['session'], market_id, days=7)
        except Exception:
            pass

        if len(history) < 3:
            try:
                resp = requests.get(
                    f"https://gamma-api.polymarket.com/markets/{market_id}/prices-history",
                    params={'days': 7},
                    headers={'User-Agent': 'PolymarketBot/2.0'},
                    timeout=6
                )
                if resp.status_code == 200:
                    records = resp.json()
                    if isinstance(records, list):
                        history = [
                            {'yes_price': float(r.get('p', r.get('price', 0.5))),
                             'timestamp': float(r.get('t', 0))}
                            for r in records if r.get('p') or r.get('price')
                        ]
            except Exception:
                pass

        if len(history) >= 3:
            prices = [h['yes_price'] for h in history]
            return (prices[-1] - prices[0]) / max(prices[0], 0.01)
        return 0.0

    def calculate_probability(self, market, external_data=None) -> dict:
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        question = market.question if hasattr(market, 'question') else market.get('question', '')
        market_id = market.market_id if hasattr(market, 'market_id') else str(market.get('id', ''))

        # Independent base rate — NOT anchored on yes_price
        base_prob, base_conf, method = self._detect_base_rate(question)

        # Momentum adjustment (small weight — trust base rate more)
        momentum = self._fetch_momentum(market_id, external_data or {})
        prob = max(0.03, min(0.97, base_prob + momentum * 0.10))

        # Boost confidence if momentum confirms direction
        if abs(momentum) > 0.08:
            base_conf = min(base_conf + 0.05, 0.55)

        edge = prob - yes_price

        logger.debug(
            f"PoliticsModel [{market_id[:8]}]: base={base_prob:.1%} "
            f"mom={momentum:+.1%} final={prob:.1%} mkt={yes_price:.1%} "
            f"edge={edge:+.1%} conf={base_conf:.0%}"
        )

        return {
            'probability': prob,
            'confidence': base_conf,
            'method': method,
            'factors': {
                'base_prob': round(base_prob, 3),
                'momentum': round(momentum, 4),
                'yes_price': yes_price,
            },
            'reasoning': (
                f'{method}: base={base_prob:.1%} mom={momentum:+.1%} '
                f'→ final={prob:.1%} vs market {yes_price:.1%} (edge={edge:+.1%})'
            )
        }
