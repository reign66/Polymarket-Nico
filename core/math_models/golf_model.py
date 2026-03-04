"""Golf model based on OWGR (Official World Golf Rankings)."""

import logging
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)

GOLF_RANKINGS = {
    "scottie scheffler": 1, "xander schauffele": 2, "rory mcilroy": 3,
    "jon rahm": 4, "collin morikawa": 5, "ludvig aberg": 6,
    "bryson dechambeau": 7, "wyndham clark": 8, "tommy fleetwood": 9,
    "shane lowry": 10, "hideki matsuyama": 11, "viktor hovland": 12,
    "patrick cantlay": 13, "brooks koepka": 14, "justin thomas": 15,
    "jordan spieth": 16, "tony finau": 17, "sahith theegala": 18,
    "robert macintyre": 19, "sungjae im": 20, "cameron smith": 21,
    "tom kim": 22, "russell henley": 23, "cameron young": 24,
    "matt fitzpatrick": 25, "dustin johnson": 26, "max homa": 27,
    "keegan bradley": 28, "si woo kim": 29, "chris kirk": 30,
}

MAJOR_WIN_PROB = {
    1: 0.12, 2: 0.09, 3: 0.08, 4: 0.07, 5: 0.065,
    6: 0.05, 7: 0.045, 8: 0.04, 9: 0.038, 10: 0.035,
}


class GolfModel(MathModel):
    def calculate_probability(self, market, external_data=None) -> dict:
        question = market.question if hasattr(market, 'question') else market.get('question', '')
        q = question.lower()

        golfer = None
        ranking = None
        for name, rank in GOLF_RANKINGS.items():
            if name in q:
                golfer = name
                ranking = rank
                break

        if not golfer:
            return self._fallback(market)

        is_major = any(w in q for w in [
            'masters', 'us open', 'open championship', 'pga championship', 'major'
        ])

        if ranking <= 10:
            prob = MAJOR_WIN_PROB.get(ranking, 0.035)
        elif ranking <= 20:
            prob = 0.025
        elif ranking <= 30:
            prob = 0.015
        else:
            prob = 0.005

        if not is_major:
            prob *= 1.5  # Regular tour events easier to win than majors

        prob = max(0.005, min(0.30, prob))
        confidence = 0.35 if is_major else 0.30

        return {
            'probability': prob,
            'confidence': confidence,
            'method': f'Golf_OWGR#{ranking}',
            'factors': {
                'golfer': golfer, 'ranking': ranking, 'is_major': is_major,
            },
            'reasoning': (
                f'{golfer.title()} #{ranking} OWGR. '
                f'{"Major" if is_major else "Tour event"} prob={prob:.1%}'
            )
        }
