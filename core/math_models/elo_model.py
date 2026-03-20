"""NBA Elo model with ESPN injuries and BDL game data."""

import json
import os
import time
import logging
import requests
from core.math_models.base_model import MathModel

logger = logging.getLogger(__name__)

NBA_TEAMS = {
    "lakers": "LAL", "los angeles lakers": "LAL", "lal": "LAL",
    "celtics": "BOS", "boston celtics": "BOS", "bos": "BOS",
    "warriors": "GSW", "golden state warriors": "GSW", "gsw": "GSW",
    "bucks": "MIL", "milwaukee bucks": "MIL", "mil": "MIL",
    "nuggets": "DEN", "denver nuggets": "DEN", "den": "DEN",
    "heat": "MIA", "miami heat": "MIA", "mia": "MIA",
    "suns": "PHX", "phoenix suns": "PHX", "phx": "PHX",
    "nets": "BKN", "brooklyn nets": "BKN", "bkn": "BKN",
    "knicks": "NYK", "new york knicks": "NYK", "nyk": "NYK",
    "76ers": "PHI", "sixers": "PHI", "philadelphia 76ers": "PHI", "phi": "PHI",
    "clippers": "LAC", "los angeles clippers": "LAC", "lac": "LAC",
    "mavericks": "DAL", "mavs": "DAL", "dallas mavericks": "DAL", "dal": "DAL",
    "thunder": "OKC", "oklahoma city thunder": "OKC", "okc": "OKC",
    "cavaliers": "CLE", "cavs": "CLE", "cleveland cavaliers": "CLE", "cle": "CLE",
    "timberwolves": "MIN", "wolves": "MIN", "minnesota timberwolves": "MIN", "min": "MIN",
    "hawks": "ATL", "atlanta hawks": "ATL", "atl": "ATL",
    "bulls": "CHI", "chicago bulls": "CHI", "chi": "CHI",
    "pistons": "DET", "detroit pistons": "DET", "det": "DET",
    "pacers": "IND", "indiana pacers": "IND", "ind": "IND",
    "grizzlies": "MEM", "memphis grizzlies": "MEM", "mem": "MEM",
    "pelicans": "NOP", "new orleans pelicans": "NOP", "nop": "NOP",
    "magic": "ORL", "orlando magic": "ORL", "orl": "ORL",
    "raptors": "TOR", "toronto raptors": "TOR", "tor": "TOR",
    "wizards": "WAS", "washington wizards": "WAS", "was": "WAS",
    "hornets": "CHA", "charlotte hornets": "CHA", "cha": "CHA",
    "jazz": "UTA", "utah jazz": "UTA", "uta": "UTA",
    "kings": "SAC", "sacramento kings": "SAC", "sac": "SAC",
    "spurs": "SAS", "san antonio spurs": "SAS", "sas": "SAS",
    "blazers": "POR", "trail blazers": "POR", "portland trail blazers": "POR", "por": "POR",
    "rockets": "HOU", "houston rockets": "HOU", "hou": "HOU",
}

BDL_BASE = "https://api.balldontlie.io/v1"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"


class EloModel(MathModel):
    def __init__(self):
        self.elo_file = "data/elo_ratings.json"
        self.ratings = self._load_ratings()
        self._injury_cache = {}
        self._injury_cache_time = 0

    def _load_ratings(self):
        if os.path.exists(self.elo_file):
            try:
                with open(self.elo_file) as f:
                    data = json.load(f)
                # Validate: if all values are 1500, ratings not bootstrapped yet
                if len(set(data.values())) > 1:
                    return data
            except Exception:
                pass
        # Bootstrap from BDL standings
        return self._bootstrap_from_bdl()

    def _bootstrap_from_bdl(self) -> dict:
        """Fetch current season win% from BDL and convert to Elo ratings."""
        try:
            import requests as _req
            r = _req.get(
                "https://api.balldontlie.io/v1/standings",
                params={"season": 2025},
                timeout=10
            )
            if r.status_code == 200:
                standings = r.json().get("data", [])
                if standings:
                    ratings = {}
                    for s in standings:
                        abbr = s.get("team", {}).get("abbreviation", "")
                        wins = s.get("wins", 0)
                        losses = s.get("losses", 1)
                        win_pct = wins / max(wins + losses, 1)
                        # Map win% to Elo: 0.200 → 1350, 0.600 → 1600, 0.800 → 1700
                        elo = 1350 + win_pct * 450
                        if abbr:
                            ratings[abbr] = round(elo, 1)
                    if ratings:
                        os.makedirs(os.path.dirname(self.elo_file) or ".", exist_ok=True)
                        with open(self.elo_file, "w") as f:
                            json.dump(ratings, f, indent=2)
                        logger.info(f"Elo bootstrapped from BDL standings: {len(ratings)} teams")
                        return ratings
        except Exception as e:
            logger.warning(f"BDL standings bootstrap failed: {e}")

        # Hard-coded fallback (2025-26 approximation)
        return {
            "CLE": 1650, "OKC": 1640, "BOS": 1620, "MEM": 1590, "HOU": 1580,
            "GSW": 1560, "DEN": 1555, "NYK": 1545, "MIN": 1540, "LAC": 1530,
            "MIL": 1525, "IND": 1520, "MIA": 1515, "PHX": 1510, "LAL": 1505,
            "ATL": 1495, "SAC": 1490, "CHI": 1485, "POR": 1480, "NOP": 1475,
            "TOR": 1465, "ORL": 1460, "BKN": 1450, "DAL": 1445, "DET": 1440,
            "SAS": 1435, "UTA": 1420, "WAS": 1415, "CHA": 1410, "PHI": 1400,
        }

    def _save_ratings(self):
        os.makedirs("data", exist_ok=True)
        with open(self.elo_file, 'w') as f:
            json.dump(self.ratings, f, indent=2)

    def update_ratings(self):
        """Called every 2h by scheduler to update from recent games."""
        try:
            import datetime
            today = datetime.date.today().isoformat()
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            r = requests.get(
                f"{BDL_BASE}/games",
                params={"dates[]": [yesterday, today]},
                timeout=10
            )
            if r.status_code != 200:
                return
            games = r.json().get('data', [])
            updated = 0
            for game in games:
                if game.get('status') != 'Final':
                    continue
                home = game.get('home_team', {}).get('abbreviation', '')
                away = game.get('visitor_team', {}).get('abbreviation', '')
                home_score = game.get('home_team_score', 0)
                away_score = game.get('visitor_team_score', 0)
                if not home or not away or (home_score == 0 and away_score == 0):
                    continue
                home_elo = self.ratings.get(home, 1500)
                away_elo = self.ratings.get(away, 1500)
                expected_home = 1 / (1 + 10 ** ((away_elo - home_elo) / 400))
                result_home = 1 if home_score > away_score else 0
                K = 20
                self.ratings[home] = home_elo + K * (result_home - expected_home)
                self.ratings[away] = away_elo + K * ((1 - result_home) - (1 - expected_home))
                updated += 1
            if updated > 0:
                self._save_ratings()
                logger.info(f"Updated Elo ratings for {updated} completed games.")
        except Exception as e:
            logger.warning(f"Elo update failed: {e}")

    def _parse_matchup(self, question: str):
        """Extract two NBA team abbreviations from a question."""
        question_lower = question.lower()
        found = []
        for name, abbr in NBA_TEAMS.items():
            if name in question_lower:
                if abbr not in [a for _, a in found]:
                    pos = question_lower.index(name)
                    found.append((pos, abbr))
        if len(found) >= 2:
            found.sort(key=lambda x: x[0])
            return found[0][1], found[1][1]
        if len(found) == 1:
            return found[0][1], None
        return None, None

    def _fetch_injuries(self, team_abbr: str) -> list:
        """Fetch injuries via ESPN, cached 4h."""
        if time.time() - self._injury_cache_time < 14400 and self._injury_cache:
            return self._injury_cache.get(team_abbr, [])
        try:
            r = requests.get(f"{ESPN_BASE}/injuries", timeout=10)
            if r.status_code == 200:
                data = r.json()
                self._injury_cache = {}
                for team in data.get('items', []):
                    t_abbr = team.get('team', {}).get('abbreviation', '')
                    injuries = []
                    for athlete in team.get('injuries', []):
                        status = athlete.get('status', '').lower()
                        name = athlete.get('athlete', {}).get('displayName', '')
                        if status in ['out', 'doubtful']:
                            injuries.append({'name': name, 'status': status})
                    self._injury_cache[t_abbr] = injuries
                self._injury_cache_time = time.time()
            return self._injury_cache.get(team_abbr, [])
        except Exception:
            return []

    def calculate_probability(self, market, external_data=None) -> dict:  # noqa: ARG002
        question = market.question if hasattr(market, 'question') else market.get('question', '')
        team1, team2 = self._parse_matchup(question)

        if not team1:
            return self._fallback(market)

        elo1 = self.ratings.get(team1, 1500)
        elo2 = self.ratings.get(team2, 1500) if team2 else 1500

        adjustments1 = 0
        adjustments2 = 0
        # 1 team vs average = weaker signal than a direct matchup
        confidence = 0.50 if team2 else 0.30

        q_lower = question.lower()
        if "home" in q_lower or "at home" in q_lower:
            adjustments1 += 60
            confidence += 0.03

        injuries1 = self._fetch_injuries(team1)
        injuries2 = self._fetch_injuries(team2) if team2 else []
        adjustments1 -= len(injuries1) * 40
        adjustments2 -= len(injuries2) * 40
        if injuries1 or injuries2:
            confidence += 0.05

        adj_elo1 = elo1 + adjustments1
        adj_elo2 = elo2 + adjustments2
        # P(team1 wins a single game)
        prob = 1 / (1 + 10 ** ((adj_elo2 - adj_elo1) / 400))

        # Adjust for multi-round playoff questions (team must win 2+ series)
        is_multi_round = any(kw in q_lower for kw in [
            "conference finals", "finals mvp", "nba finals",
            "championship", "nba champion"
        ])
        if is_multi_round and not team2:
            # Rough approximation: winning 2 rounds ≈ prob^2
            prob = prob ** 2
            confidence = max(0.20, confidence - 0.10)  # less confident about multi-round

        confidence = min(confidence, 0.75)

        return {
            'probability': prob,
            'confidence': confidence,
            'method': f'Elo({team1}={adj_elo1:.0f} vs {team2 or "avg"}={adj_elo2:.0f})',
            'factors': {
                'team1': team1, 'team2': team2,
                'elo1': round(elo1), 'elo2': round(elo2),
                'adj1': adjustments1, 'adj2': adjustments2,
                'injuries1': len(injuries1), 'injuries2': len(injuries2),
                'multi_round': is_multi_round,
            },
            'reasoning': (
                f'{team1}(Elo {adj_elo1:.0f}) vs {team2 or "avg"}(Elo {adj_elo2:.0f}). '
                f'Prob={prob:.1%}{"(multi-round adj)" if is_multi_round else ""}. '
                f'Injuries: {len(injuries1)} vs {len(injuries2)}.'
            )
        }
