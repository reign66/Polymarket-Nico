"""F1 model using Jolpica/Ergast standings API."""

import requests
import time
import logging
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)


class F1Model(MathModel):
    def __init__(self):
        self._driver_standings = None
        self._constructor_standings = None
        self._cache_time = 0

    def _fetch_standings(self):
        if self._driver_standings and time.time() - self._cache_time < 14400:
            return self._driver_standings, self._constructor_standings
        try:
            r = requests.get(
                "https://api.jolpi.ca/ergast/f1/current/driverStandings.json",
                timeout=10
            )
            lists = r.json()['MRData']['StandingsTable']['StandingsLists']
            driver_standings = lists[0]['DriverStandings'] if lists else []

            r2 = requests.get(
                "https://api.jolpi.ca/ergast/f1/current/constructorStandings.json",
                timeout=10
            )
            lists2 = r2.json()['MRData']['StandingsTable']['StandingsLists']
            constructor_standings = lists2[0]['ConstructorStandings'] if lists2 else []

            self._driver_standings = driver_standings
            self._constructor_standings = constructor_standings
            self._cache_time = time.time()
            return driver_standings, constructor_standings
        except Exception as e:
            logger.warning(f"F1 standings fetch failed: {e}")
            return self._driver_standings or [], self._constructor_standings or []

    def _find_driver(self, question: str, standings: list):
        q = question.lower()
        for s in standings:
            driver = s.get('Driver', {})
            last = driver.get('familyName', '').lower()
            full = f"{driver.get('givenName','')} {driver.get('familyName','')}".lower()
            if last and (last in q or full in q):
                return s
        return None

    def calculate_probability(self, market, external_data=None) -> dict:
        question = market.question if hasattr(market, 'question') else market.get('question', '')
        drivers, constructors = self._fetch_standings()

        if not drivers:
            return self._fallback(market)

        driver = self._find_driver(question, drivers)
        if not driver:
            return self._fallback(market)

        position = int(driver.get('position', 99))
        points = float(driver.get('points', 0))
        wins = int(driver.get('wins', 0))

        q = question.lower()
        is_wdc = any(w in q for w in ['wdc', 'world champion', 'championship'])
        is_race = any(w in q for w in ['win the', 'win at', 'grand prix', 'race win'])

        total_races = 24
        leader_pts = float(drivers[0].get('points', points)) if drivers else points
        rounds_done = max(1, sum(1 for d in drivers if float(d.get('points', 0)) > 0))
        races_remaining = max(1, total_races - rounds_done)
        max_pts_remaining = races_remaining * 25

        if is_wdc:
            gap = leader_pts - points
            if gap > max_pts_remaining:
                prob = 0.02
            elif position == 1:
                adv = gap / max_pts_remaining if max_pts_remaining > 0 else 0
                prob = min(0.95, 0.50 + adv * 0.4)
            else:
                deficit = gap / max_pts_remaining if max_pts_remaining > 0 else 1
                prob = max(0.02, 0.45 * (1 - deficit))
            confidence = 0.40 if races_remaining > 5 else (0.55 if races_remaining > 2 else 0.65)
        elif is_race:
            if position <= 3:
                prob = 0.20 - (position - 1) * 0.05
            elif position <= 6:
                prob = 0.05
            elif position <= 10:
                prob = 0.02
            else:
                prob = 0.005
            # Constructor bonus
            constructors_list = driver.get('Constructors', [{}])
            constructor = constructors_list[0].get('name', '').lower() if constructors_list else ''
            if any(t in constructor for t in ['red bull', 'ferrari', 'mclaren', 'mercedes']):
                prob *= 1.3
            prob = max(0.01, min(0.50, prob))
            confidence = 0.35
        else:
            return self._fallback(market)

        return {
            'probability': prob,
            'confidence': confidence,
            'method': f'F1_standings P{position}({points}pts)',
            'factors': {
                'position': position, 'points': points, 'wins': wins,
                'races_left': races_remaining, 'is_wdc': is_wdc,
            },
            'reasoning': f'P{position} {points}pts. {"WDC" if is_wdc else "Race"} prob={prob:.1%}'
        }
