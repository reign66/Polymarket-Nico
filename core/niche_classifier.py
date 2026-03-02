import re
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


class NicheClassifier:
    def __init__(self, config: dict):
        self.niches = config.get('niches', {})

    def classify(self, market) -> Optional[str]:
        """
        Classify a market into a niche based on keyword matching.
        Returns niche name or None if no match.
        Uses zero AI — pure regex/keyword logic only.
        """
        text = (market.question + ' ' + (market.description or '')).lower()

        scores: Dict[str, int] = {}
        for niche_name, niche_config in self.niches.items():
            keywords = niche_config.get('keywords', [])
            count = 0
            for kw in keywords:
                # Use word boundary matching for short keywords to avoid false positives
                if len(kw) <= 3:
                    pattern = r'\b' + re.escape(kw) + r'\b'
                    if re.search(pattern, text):
                        count += 1
                else:
                    if kw.lower() in text:
                        count += 1
            if count > 0:
                scores[niche_name] = count

        if not scores:
            return None

        # Return niche with most keyword matches
        best_niche = max(scores, key=scores.get)
        logger.debug(
            f"Classified '{market.question[:60]}' as [{best_niche}] (scores: {scores})"
        )
        return best_niche

    def classify_batch(self, markets: list) -> tuple:
        """
        Classify a batch of markets.
        Returns (classified, unclassified) where classified is a list of markets
        that matched a niche (with market.niche set), and unclassified is the count
        of markets that did not match any niche.
        """
        classified = []
        unclassified = 0

        for market in markets:
            niche = self.classify(market)
            if niche:
                market.niche = niche
                classified.append(market)
            else:
                unclassified += 1

        logger.info(
            f"Niche classifier: {len(markets)} -> {len(classified)} classified, "
            f"{unclassified} unclassified"
        )
        return classified, unclassified
