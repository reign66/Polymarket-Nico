import re
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from .base_model import MathModel, ProbabilityResult

logger = logging.getLogger(__name__)

JOLPICA_BASE = 'https://api.jolpi.ca/ergast/f1'
ESPN_F1 = 'https://site.api.espn.com/apis/site/v2/sports/racing/f1'
HEADERS = {'User-Agent': 'PolymarketBot/2.0'}

F1_DRIVERS = {
    'verstappen': 'VER', 'hamilton': 'HAM', 'leclerc': 'LEC', 'norris': 'NOR',
    'sainz': 'SAI', 'russell': 'RUS', 'piastri': 'PIA', 'alonso': 'ALO',
    'stroll': 'STR', 'gasly': 'GAS', 'ocon': 'OCO', 'tsunoda': 'TSU',
    'ricciardo': 'RIC', 'bottas': 'BOT', 'zhou': 'ZHO', 'magnussen': 'MAG',
    'hulkenberg': 'HUL', 'albon': 'ALB', 'sargeant': 'SAR', 'perez': 'PER',
    'max verstappen': 'VER', 'lewis hamilton': 'HAM', 'charles leclerc': 'LEC',
    'lando norris': 'NOR', 'carlos sainz': 'SAI', 'george russell': 'RUS',
    'oscar piastri': 'PIA', 'fernando alonso': 'ALO', 'sergio perez': 'PER',
    'lance stroll': 'STR', 'pierre gasly': 'GAS', 'esteban ocon': 'OCO',
    'yuki tsunoda': 'TSU', 'daniel ricciardo': 'RIC', 'valtteri bottas': 'BOT'
}

F1_CONSTRUCTORS_TIERS = {
    'red bull': 1, 'ferrari': 1, 'mclaren': 1, 'mercedes': 1,
    'aston martin': 2, 'alpine': 2, 'williams': 3, 'haas': 3,
    'alpha tauri': 3, 'alphatauri': 3, 'rb': 2, 'sauber': 3, 'kick sauber': 3
}


class F1Model(MathModel):
    """F1 race and championship probability model using live standings."""

    def __init__(self, config: dict):
        super().__init__(config)
        self._cache: Dict = {}

    def _fetch_standings(self) -> Dict:
        """Fetch current driver and constructor standings from Jolpica/Ergast API.

        Caches results for 2 hours. Returns:
          {drivers: [{name, code, points, position, wins, constructor}],
           constructors: [{name, points, position, wins}]}
        """
        cache_key = 'f1_standings'
        now = datetime.utcnow()
        if cache_key in self._cache:
            cached_data, cached_time = self._cache[cache_key]
            if (now - cached_time).total_seconds() < 7200:
                return cached_data

        result = {'drivers': [], 'constructors': []}

        try:
            driver_url = f'{JOLPICA_BASE}/current/driverStandings.json'
            resp = requests.get(driver_url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            standings_table = (
                data
                .get('MRData', {})
                .get('StandingsTable', {})
                .get('StandingsLists', [{}])
            )
            standings_list = standings_table[0].get('DriverStandings', []) if standings_table else []

            for entry in standings_list:
                driver = entry.get('Driver', {})
                constructor_list = entry.get('Constructors', [{}])
                constructor_name = constructor_list[0].get('name', '') if constructor_list else ''

                result['drivers'].append({
                    'name': f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                    'code': driver.get('code', ''),
                    'points': float(entry.get('points', 0)),
                    'position': int(entry.get('position', 99)),
                    'wins': int(entry.get('wins', 0)),
                    'constructor': constructor_name,
                })
        except Exception as e:
            logger.warning(f"Could not fetch F1 driver standings: {e}")

        try:
            constructor_url = f'{JOLPICA_BASE}/current/constructorStandings.json'
            resp = requests.get(constructor_url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            standings_table = (
                data
                .get('MRData', {})
                .get('StandingsTable', {})
                .get('StandingsLists', [{}])
            )
            standings_list = standings_table[0].get('ConstructorStandings', []) if standings_table else []

            for entry in standings_list:
                constructor = entry.get('Constructor', {})
                result['constructors'].append({
                    'name': constructor.get('name', ''),
                    'points': float(entry.get('points', 0)),
                    'position': int(entry.get('position', 99)),
                    'wins': int(entry.get('wins', 0)),
                })
        except Exception as e:
            logger.warning(f"Could not fetch F1 constructor standings: {e}")

        self._cache[cache_key] = (result, now)
        return result

    def _find_driver_in_question(self, question: str) -> Optional[str]:
        """Find F1 driver code mentioned in question text."""
        q_lower = question.lower()
        for alias in sorted(F1_DRIVERS.keys(), key=len, reverse=True):
            if alias in q_lower:
                return F1_DRIVERS[alias]
        return None

    def _get_driver_stats(self, driver_code: str, standings: Dict) -> Dict:
        """Find driver in standings by code, return their stats dict."""
        for driver in standings.get('drivers', []):
            if driver.get('code', '').upper() == driver_code.upper():
                return driver
        return {}

    def _get_constructor_tier(self, constructor_name: str) -> int:
        """Return constructor tier (1=top, 2=mid, 3=low)."""
        name_lower = constructor_name.lower()
        for key, tier in F1_CONSTRUCTORS_TIERS.items():
            if key in name_lower:
                return tier
        return 2  # default mid-tier

    def _calculate_race_win_probability(self, driver_code: str, standings: Dict) -> float:
        """Calculate probability of driver winning a single race."""
        drivers = standings.get('drivers', [])
        if not drivers:
            return 0.05

        base = 1.0 / max(20, len(drivers))

        stats = self._get_driver_stats(driver_code, standings)
        if not stats:
            return base

        position = stats.get('position', 20)
        if position == 1:
            position_factor = 3.0
        elif position == 2:
            position_factor = 2.5
        elif position == 3:
            position_factor = 2.0
        elif position <= 5:
            position_factor = 1.5
        elif position <= 10:
            position_factor = 1.2
        else:
            position_factor = 0.8

        constructor_name = stats.get('constructor', '')
        tier = self._get_constructor_tier(constructor_name)
        if tier == 1:
            constructor_factor = 1.5
        elif tier == 2:
            constructor_factor = 1.0
        else:
            constructor_factor = 0.5

        raw_prob = base * position_factor * constructor_factor

        # Compute weights for all drivers to normalize
        total_weight = 0.0
        for d in drivers:
            p = d.get('position', 20)
            if p == 1:
                pf = 3.0
            elif p == 2:
                pf = 2.5
            elif p == 3:
                pf = 2.0
            elif p <= 5:
                pf = 1.5
            elif p <= 10:
                pf = 1.2
            else:
                pf = 0.8

            cn = d.get('constructor', '')
            t = self._get_constructor_tier(cn)
            cf = 1.5 if t == 1 else (1.0 if t == 2 else 0.5)
            total_weight += base * pf * cf

        if total_weight > 0:
            return raw_prob / total_weight
        return raw_prob

    def _calculate_wdc_probability(self, driver_code: str, standings: Dict) -> float:
        """Calculate probability of driver winning the World Drivers Championship."""
        drivers = standings.get('drivers', [])
        if not drivers:
            return 0.05

        stats = self._get_driver_stats(driver_code, standings)
        if not stats:
            return 0.02

        driver_points = stats.get('points', 0)
        driver_position = stats.get('position', 20)

        # Points leader
        leader = min(drivers, key=lambda d: d.get('position', 99))
        leader_points = leader.get('points', 0)

        # Remaining points estimate: assume ~24 races total, 25 points per win
        # Ergast doesn't give round number easily; use a rough estimate
        total_races = 24
        races_done = max(1, leader.get('wins', 0) + 5)  # rough heuristic
        races_remaining = max(0, total_races - races_done)
        max_remaining = races_remaining * 25

        gap = leader_points - driver_points

        if driver_position == 1:
            # Currently leading — probability depends on gap to second
            if max_remaining <= 0:
                return 0.97
            second = drivers[1] if len(drivers) > 1 else None
            if second:
                gap_to_second = driver_points - second.get('points', 0)
                if gap_to_second > max_remaining:
                    return 0.97
            # Leading but not clinched
            return min(0.90, 0.65 + (gap_to_second / max(max_remaining, 1)) * 0.25) if second else 0.75

        if max_remaining > 0 and gap > max_remaining:
            return 0.01  # mathematically eliminated

        if max_remaining <= 0:
            return 0.01

        # Estimate based on points percentage and position
        total_points = sum(d.get('points', 0) for d in drivers if d.get('points', 0) > 0) or 1
        points_fraction = driver_points / total_points
        position_factor = max(0.1, 1.0 - (driver_position - 1) * 0.08)

        prob = points_fraction * position_factor * len(drivers) * 0.1
        return max(0.01, min(0.95, prob))

    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate F1 race win, championship, or podium probability."""
        question = getattr(market, 'question', '') or ''
        q_lower = question.lower()

        driver_code = self._find_driver_in_question(question)
        if not driver_code:
            return None

        standings = self._fetch_standings()
        stats = self._get_driver_stats(driver_code, standings)
        driver_name = stats.get('name', driver_code)

        is_championship = any(word in q_lower for word in ['championship', 'wdc', 'world', 'season'])
        is_podium = 'podium' in q_lower
        is_win = any(word in q_lower for word in ['win', 'winner', 'first place', 'victory'])

        if is_championship:
            prob = self._calculate_wdc_probability(driver_code, standings)
            confidence = 0.45 + (0.15 * (1 - abs(prob - 0.5) * 2))  # higher confidence for extreme values
            confidence = max(0.45, min(0.60, confidence))

            return ProbabilityResult(
                probability=prob,
                confidence=confidence,
                method='f1_wdc',
                factors={
                    'driver': driver_code,
                    'position': stats.get('position'),
                    'points': stats.get('points'),
                    'wins': stats.get('wins'),
                    'constructor': stats.get('constructor'),
                },
                reasoning=(
                    f"WDC probability for {driver_name}: {prob:.1%}. "
                    f"Current position: P{stats.get('position', '?')}, Points: {stats.get('points', '?')}"
                )
            )

        if is_podium:
            # Podium probability ~ roughly 3x race win probability (top 3 instead of top 1)
            race_win_prob = self._calculate_race_win_probability(driver_code, standings)
            prob = min(0.95, race_win_prob * 3.0)
            confidence = 0.50

            return ProbabilityResult(
                probability=prob,
                confidence=confidence,
                method='f1_podium',
                factors={
                    'driver': driver_code,
                    'race_win_prob': round(race_win_prob, 4),
                    'position': stats.get('position'),
                    'constructor': stats.get('constructor'),
                },
                reasoning=(
                    f"Podium probability for {driver_name}: {prob:.1%} "
                    f"(3x race win prob of {race_win_prob:.1%})"
                )
            )

        # Default: race win probability
        prob = self._calculate_race_win_probability(driver_code, standings)
        confidence = 0.50 + min(0.15, stats.get('wins', 0) * 0.01)
        confidence = max(0.50, min(0.65, confidence))

        return ProbabilityResult(
            probability=prob,
            confidence=confidence,
            method='f1_race_win',
            factors={
                'driver': driver_code,
                'position': stats.get('position'),
                'points': stats.get('points'),
                'wins': stats.get('wins'),
                'constructor': stats.get('constructor'),
                'constructor_tier': self._get_constructor_tier(stats.get('constructor', '')),
            },
            reasoning=(
                f"Race win probability for {driver_name}: {prob:.1%}. "
                f"Championship P{stats.get('position', '?')}, {stats.get('points', 0)} pts."
            )
        )

    def can_handle(self, market) -> bool:
        """True if an F1 driver is found in the market question."""
        question = getattr(market, 'question', '') or ''
        return self._find_driver_in_question(question) is not None
