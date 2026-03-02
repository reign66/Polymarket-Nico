import os
import json
import re
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from .base_model import MathModel, ProbabilityResult

logger = logging.getLogger(__name__)

# NBA team name aliases for matching market questions
NBA_TEAMS = {
    'lakers': 'LAL', 'celtics': 'BOS', 'warriors': 'GSW', 'bucks': 'MIL',
    'nuggets': 'DEN', 'heat': 'MIA', 'sixers': 'PHI', '76ers': 'PHI',
    'nets': 'BKN', 'knicks': 'NYK', 'suns': 'PHX', 'mavericks': 'DAL',
    'clippers': 'LAC', 'rockets': 'HOU', 'spurs': 'SAS', 'grizzlies': 'MEM',
    'timberwolves': 'MIN', 'thunder': 'OKC', 'pelicans': 'NOP', 'kings': 'SAC',
    'pacers': 'IND', 'hawks': 'ATL', 'bulls': 'CHI', 'pistons': 'DET',
    'magic': 'ORL', 'raptors': 'TOR', 'hornets': 'CHA', 'wizards': 'WAS',
    'trail blazers': 'POR', 'blazers': 'POR', 'jazz': 'UTA', 'cavaliers': 'CLE',
    'los angeles lakers': 'LAL', 'boston celtics': 'BOS', 'golden state warriors': 'GSW',
    'milwaukee bucks': 'MIL', 'denver nuggets': 'DEN', 'miami heat': 'MIA',
    'philadelphia 76ers': 'PHI', 'brooklyn nets': 'BKN', 'new york knicks': 'NYK',
    'phoenix suns': 'PHX', 'dallas mavericks': 'DAL', 'la clippers': 'LAC',
    'houston rockets': 'HOU', 'san antonio spurs': 'SAS', 'memphis grizzlies': 'MEM',
    'minnesota timberwolves': 'MIN', 'oklahoma city thunder': 'OKC',
    'new orleans pelicans': 'NOP', 'sacramento kings': 'SAC', 'indiana pacers': 'IND',
    'atlanta hawks': 'ATL', 'chicago bulls': 'CHI', 'detroit pistons': 'DET',
    'orlando magic': 'ORL', 'toronto raptors': 'TOR', 'charlotte hornets': 'CHA',
    'washington wizards': 'WAS', 'portland trail blazers': 'POR', 'utah jazz': 'UTA',
    'cleveland cavaliers': 'CLE'
}

ELO_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'elo_ratings.json'
)
DEFAULT_ELO = 1500
K_FACTOR = 20
HOME_ADVANTAGE = 60
BACK_TO_BACK_PENALTY = -30
WIN_STREAK_BONUS = 15
LOSS_STREAK_PENALTY = -15
STREAK_THRESHOLD = 3


class EloModel(MathModel):
    """NBA Elo rating model for match outcome probability."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.ratings: Dict[str, float] = self._load_ratings()

    def _load_ratings(self) -> Dict[str, float]:
        """Read from JSON file, return dict of team_code -> rating."""
        try:
            if os.path.exists(ELO_FILE):
                with open(ELO_FILE, 'r') as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load Elo ratings from {ELO_FILE}: {e}")
        return {}

    def _save_ratings(self):
        """Write ratings dict to JSON file."""
        try:
            os.makedirs(os.path.dirname(ELO_FILE), exist_ok=True)
            with open(ELO_FILE, 'w') as f:
                json.dump(self.ratings, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save Elo ratings: {e}")

    def _get_team_from_text(self, text: str) -> Optional[str]:
        """Scan text for any NBA team name/alias, return team code."""
        text_lower = text.lower()
        # Sort by length descending so longer aliases match first
        for alias in sorted(NBA_TEAMS.keys(), key=len, reverse=True):
            if alias in text_lower:
                return NBA_TEAMS[alias]
        return None

    def _find_teams_in_question(self, question: str) -> Tuple[Optional[str], Optional[str]]:
        """Find two teams mentioned in market question.

        Tries patterns like 'Team A vs Team B', 'Will Team A beat Team B', 'Team A win'.
        Returns (team_a, team_b) or (team_a, None).
        """
        q_lower = question.lower()

        # Pattern: "X vs Y" or "X v Y"
        vs_patterns = [
            r'(.+?)\s+vs\.?\s+(.+)',
            r'(.+?)\s+v\.?\s+(.+)',
            r'will\s+(.+?)\s+beat\s+(.+)',
            r'will\s+(.+?)\s+defeat\s+(.+)',
        ]
        for pattern in vs_patterns:
            match = re.search(pattern, q_lower)
            if match:
                team_a = self._get_team_from_text(match.group(1))
                team_b = self._get_team_from_text(match.group(2))
                if team_a and team_b:
                    return team_a, team_b
                if team_a:
                    return team_a, None

        # Fallback: find all teams mentioned in order
        found = []
        text_lower = q_lower
        for alias in sorted(NBA_TEAMS.keys(), key=len, reverse=True):
            if alias in text_lower:
                code = NBA_TEAMS[alias]
                if code not in found:
                    found.append(code)
                # Replace to avoid double-matching substrings
                text_lower = text_lower.replace(alias, '', 1)

        if len(found) >= 2:
            return found[0], found[1]
        if len(found) == 1:
            return found[0], None
        return None, None

    def _get_team_elo(self, team: str) -> float:
        """Return team's Elo rating, or DEFAULT_ELO if unknown."""
        return self.ratings.get(team, DEFAULT_ELO)

    def _calculate_expected_win(self, elo_a: float, elo_b: float) -> float:
        """Standard Elo formula: probability that A beats B."""
        return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400))

    def _fetch_recent_games(self) -> List[Dict]:
        """Fetch ESPN NBA scoreboard, return list of game dicts."""
        try:
            url = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            games = []
            for event in data.get('events', []):
                competitions = event.get('competitions', [])
                if not competitions:
                    continue
                comp = competitions[0]
                competitors = comp.get('competitors', [])
                if len(competitors) < 2:
                    continue

                completed = comp.get('status', {}).get('type', {}).get('completed', False)
                home = next((c for c in competitors if c.get('homeAway') == 'home'), None)
                away = next((c for c in competitors if c.get('homeAway') == 'away'), None)

                if not home or not away:
                    continue

                home_abbr = home.get('team', {}).get('abbreviation', '')
                away_abbr = away.get('team', {}).get('abbreviation', '')
                home_score = int(home.get('score', 0) or 0)
                away_score = int(away.get('score', 0) or 0)

                games.append({
                    'home_team': home_abbr,
                    'away_team': away_abbr,
                    'home_score': home_score,
                    'away_score': away_score,
                    'completed': completed,
                })
            return games
        except Exception as e:
            logger.warning(f"Could not fetch recent NBA games: {e}")
            return []

    def update_ratings(self):
        """Fetch recent NBA results and update Elo for each completed game."""
        games = self._fetch_recent_games()
        updated = 0
        for game in games:
            if not game.get('completed'):
                continue
            home = game['home_team']
            away = game['away_team']
            home_score = game['home_score']
            away_score = game['away_score']

            if home_score == 0 and away_score == 0:
                continue

            elo_home = self._get_team_elo(home) + HOME_ADVANTAGE
            elo_away = self._get_team_elo(away)

            expected_home = self._calculate_expected_win(elo_home, elo_away)
            actual_home = 1.0 if home_score > away_score else 0.0

            delta_home = K_FACTOR * (actual_home - expected_home)

            self.ratings[home] = self._get_team_elo(home) + delta_home
            self.ratings[away] = self._get_team_elo(away) - delta_home
            updated += 1

        if updated > 0:
            self._save_ratings()
            logger.info(f"Updated Elo ratings for {updated} completed games.")

    def _get_team_streaks(self) -> Dict[str, int]:
        """Return dict of team -> streak (positive = win streak, negative = loss streak)."""
        try:
            url = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard'
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            data = response.json()

            streaks: Dict[str, int] = {}
            for event in data.get('events', []):
                for comp in event.get('competitions', []):
                    for competitor in comp.get('competitors', []):
                        abbr = competitor.get('team', {}).get('abbreviation', '')
                        records = competitor.get('records', [])
                        for record in records:
                            if record.get('type') == 'streak':
                                summary = record.get('summary', '')
                                match = re.match(r'([WL])(\d+)', summary)
                                if match:
                                    wl = match.group(1)
                                    count = int(match.group(2))
                                    streaks[abbr] = count if wl == 'W' else -count
            return streaks
        except Exception as e:
            logger.warning(f"Could not fetch team streaks: {e}")
            return {}

    def _get_rest_days(self, team: str) -> int:
        """Estimate rest days based on schedule. Default 1 if can't determine."""
        return 1

    def calculate_probability(self, market, external_data: dict = None) -> Optional[ProbabilityResult]:
        """Calculate NBA match or championship probability using Elo ratings."""
        question = getattr(market, 'question', '') or ''
        team_a, team_b = self._find_teams_in_question(question)

        if not team_a:
            return None

        if team_a and team_b:
            # Head-to-head match market
            streaks = self._get_team_streaks()

            elo_a = self._get_team_elo(team_a)
            elo_b = self._get_team_elo(team_b)

            # Home advantage: crude detection from question
            q_lower = question.lower()
            home_team_a = any(word in q_lower for word in ['at home', 'host', 'home game'])
            if home_team_a:
                elo_a += HOME_ADVANTAGE
            else:
                # Default: assume team_a is home if listed first
                elo_a += HOME_ADVANTAGE // 2

            # Streak adjustments
            streak_a = streaks.get(team_a, 0)
            streak_b = streaks.get(team_b, 0)
            if abs(streak_a) >= STREAK_THRESHOLD:
                elo_a += WIN_STREAK_BONUS if streak_a > 0 else LOSS_STREAK_PENALTY
            if abs(streak_b) >= STREAK_THRESHOLD:
                elo_b += WIN_STREAK_BONUS if streak_b > 0 else LOSS_STREAK_PENALTY

            prob = self._calculate_expected_win(elo_a, elo_b)

            elo_diff = abs(self._get_team_elo(team_a) - self._get_team_elo(team_b))
            confidence = min(0.8, 0.6 + (elo_diff / 1000))

            return ProbabilityResult(
                probability=prob,
                confidence=confidence,
                method='elo_match',
                factors={
                    'team_a': team_a,
                    'team_b': team_b,
                    'elo_a_base': self._get_team_elo(team_a),
                    'elo_b_base': self._get_team_elo(team_b),
                    'elo_a_adjusted': round(elo_a, 1),
                    'elo_b_adjusted': round(elo_b, 1),
                    'streak_a': streak_a,
                    'streak_b': streak_b,
                    'elo_diff': round(elo_diff, 1),
                },
                reasoning=(
                    f"Elo match: {team_a} ({elo_a:.0f}) vs {team_b} ({elo_b:.0f}). "
                    f"Win probability for {team_a}: {prob:.1%}"
                )
            )

        else:
            # Single team: championship/playoff/MVP market
            team_elo = self._get_team_elo(team_a)
            avg_elo = sum(self.ratings.values()) / len(self.ratings) if self.ratings else DEFAULT_ELO

            elo_diff = team_elo - avg_elo
            # Rough probability: sigmoid-like scaling
            prob = 0.5 + (elo_diff / 800)
            prob = max(0.05, min(0.90, prob))

            confidence = 0.3 if abs(elo_diff) > 100 else 0.4 if abs(elo_diff) > 50 else 0.35

            return ProbabilityResult(
                probability=prob,
                confidence=confidence,
                method='elo_ranking',
                factors={
                    'team': team_a,
                    'team_elo': team_elo,
                    'avg_elo': round(avg_elo, 1),
                    'elo_diff': round(elo_diff, 1),
                },
                reasoning=(
                    f"Elo ranking: {team_a} Elo={team_elo:.0f}, league avg={avg_elo:.0f}. "
                    f"Rough probability: {prob:.1%}"
                )
            )

    def can_handle(self, market) -> bool:
        """True if at least one NBA team found in question."""
        question = getattr(market, 'question', '') or ''
        team, _ = self._find_teams_in_question(question)
        return team is not None
