"""NBA Elo model — V2.3

Usage matrix:
  HEAD-TO-HEAD ("Team A vs Team B")  → Elo formula, high confidence
  SEASON questions (single team)     → standings-based ranking probability
  INDIVIDUAL awards                  → fallback (market price, low conf)

V2.3: removed flat 0.50 fallback for single-team season questions.
"""

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
    "mammoth": "UTA",  # Utah Mammoth (new name)
}

BDL_BASE = "https://api.balldontlie.io/v1"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"

# Default 2025-26 Elo ratings (real standings)
DEFAULT_ELO = {
    "CLE": 1650, "OKC": 1640, "BOS": 1620, "MEM": 1590, "HOU": 1580,
    "GSW": 1560, "DEN": 1555, "NYK": 1545, "MIN": 1540, "LAC": 1530,
    "MIL": 1525, "IND": 1520, "MIA": 1515, "PHX": 1510, "LAL": 1505,
    "ATL": 1495, "SAC": 1490, "CHI": 1485, "POR": 1480, "NOP": 1475,
    "TOR": 1465, "ORL": 1460, "BKN": 1450, "DAL": 1445, "DET": 1440,
    "SAS": 1435, "UTA": 1420, "WAS": 1415, "CHA": 1410, "PHI": 1400,
}


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
                if len(set(data.values())) > 1:
                    logger.info(f"Elo ratings loaded from {self.elo_file}: {len(data)} teams")
                    return data
            except Exception:
                pass
        logger.info("Elo: using default 2025-26 ratings")
        return dict(DEFAULT_ELO)

    def _save_ratings(self):
        os.makedirs("data", exist_ok=True)
        with open(self.elo_file, "w") as f:
            json.dump(self.ratings, f, indent=2)

    def _get_rank(self, abbr: str) -> int:
        """Return 1-based ranking from best (1) to worst (30) by Elo."""
        sorted_teams = sorted(self.ratings.items(), key=lambda x: x[1], reverse=True)
        for i, (team, _) in enumerate(sorted_teams, 1):
            if team == abbr:
                return i
        return 15  # default mid-rank

    def _season_probability(self, abbr: str, question_type: str) -> tuple:
        """
        Compute season-long probability based on Elo rank.
        Returns (probability, confidence, reasoning).

        Types: playoff, championship, conference, division, worst_record, award
        """
        rank = self._get_rank(abbr)
        elo = self.ratings.get(abbr, 1500)

        if question_type == "playoff":
            # Top 8 per conference = 16/30 make playoffs total
            # Based on rank: top 3 very likely, 13-17 bubble, bottom 10 very unlikely
            if rank <= 6:
                prob = 0.85 - (rank - 1) * 0.05
            elif rank <= 10:
                prob = 0.60 - (rank - 7) * 0.08
            elif rank <= 16:
                prob = 0.35 - (rank - 11) * 0.04
            elif rank <= 22:
                prob = 0.15 - (rank - 17) * 0.015
            else:
                prob = max(0.04, 0.07 - (rank - 23) * 0.01)
            confidence = 0.35

        elif question_type == "championship":
            # Only top teams have real shot; drops off quickly
            if rank == 1:
                prob = 0.18
            elif rank <= 3:
                prob = 0.10 - (rank - 2) * 0.02
            elif rank <= 6:
                prob = 0.06 - (rank - 4) * 0.01
            elif rank <= 10:
                prob = 0.025
            elif rank <= 16:
                prob = 0.010
            else:
                prob = 0.003
            confidence = 0.40

        elif question_type == "conference":
            # Win a conference (top 8 teams, 4 rounds)
            if rank <= 2:
                prob = 0.28
            elif rank <= 4:
                prob = 0.18
            elif rank <= 8:
                prob = 0.10
            elif rank <= 12:
                prob = 0.04
            elif rank <= 18:
                prob = 0.01
            else:
                prob = 0.003
            confidence = 0.38

        elif question_type == "division":
            # Win a division (usually top 5 per division ~6 teams)
            if rank <= 3:
                prob = 0.40
            elif rank <= 6:
                prob = 0.25
            elif rank <= 10:
                prob = 0.12
            elif rank <= 16:
                prob = 0.05
            else:
                prob = 0.02
            confidence = 0.35

        elif question_type == "worst_record":
            # Worst record = highest rank from bottom
            bottom_rank = 31 - rank  # bottom_rank=1 means worst team
            if bottom_rank <= 2:
                prob = 0.25
            elif bottom_rank <= 4:
                prob = 0.12
            elif bottom_rank <= 6:
                prob = 0.06
            elif bottom_rank <= 10:
                prob = 0.02
            else:
                prob = 0.005
            confidence = 0.35

        else:
            # Unknown season question type
            return None, None, "unknown season question type"

        prob = max(0.02, min(0.95, prob))

        # ── V2.3 MARKET PRICE SANITY CHECK ───────────────────────────────
        # If the market already prices this below 6¢, it knows more than our
        # static ELO (current standings, injuries, roster news).
        # Postmortem 21/03: Hornets conference (-10.76) + Grizzlies worst record (-30.81)
        # were both < 5¢ markets where ELO still saw an edge. Market was right.
        # Rule: for "championship", "conference", "worst_record" types,
        # if market_price < 0.06 and our model_prob < market_price * 2:
        #   → cap confidence to 0.15 (not enough signal to bet)
        # This check is applied in calculate_probability() using market.yes_price.

        reasoning = f"Elo rank #{rank} ({abbr} {elo:.0f}) → {question_type} prob={prob:.1%}"
        return prob, confidence, reasoning

    def _detect_question_type(self, question: str) -> str:
        """Detect season question type from question text."""
        q = question.lower()
        if any(w in q for w in ["make the nba playoffs", "reach the playoffs", "qualify for the playoffs",
                                  "make the playoffs"]):
            return "playoff"
        if any(w in q for w in ["nba champion", "win the nba", "nba finals", "nba title",
                                  "larry o'brien", "win it all"]):
            return "championship"
        if any(w in q for w in ["eastern conference", "western conference",
                                  "conference champion", "conference title",
                                  "conference finals", "win the east", "win the west"]):
            return "conference"
        if any(w in q for w in ["division", "atlantic", "central", "southeast",
                                  "northwest", "pacific", "southwest"]):
            return "division"
        if any(w in q for w in ["worst record", "fewest wins", "most losses", "lottery",
                                  "first overall pick", "finish last", "finish with the worst"]):
            return "worst_record"
        if any(w in q for w in ["mvp", "rookie", "sixth man", "dpoy", "defensive player",
                                  "most improved", "coach of the year", "award", "all-star",
                                  "all-nba", "scoring title", "triple-double"]):
            return "award"
        return "unknown"

    def update_ratings(self):
        """Called every 2h to update from recent games via BDL."""
        try:
            import datetime
            today = datetime.date.today().isoformat()
            yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
            r = requests.get(f"{BDL_BASE}/games", params={"dates[]": [yesterday, today]}, timeout=10)
            if r.status_code != 200:
                return
            games = r.json().get("data", [])
            updated = 0
            for game in games:
                if game.get("status") != "Final":
                    continue
                home = game.get("home_team", {}).get("abbreviation", "")
                away = game.get("visitor_team", {}).get("abbreviation", "")
                home_score = game.get("home_team_score", 0)
                away_score = game.get("visitor_team_score", 0)
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
                logger.info(f"Elo updated for {updated} games")
        except Exception as e:
            logger.warning(f"Elo update failed: {e}")

    def _parse_matchup(self, question: str):
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
        if time.time() - self._injury_cache_time < 14400 and self._injury_cache:
            return self._injury_cache.get(team_abbr, [])
        try:
            r = requests.get(f"{ESPN_BASE}/injuries", timeout=10)
            if r.status_code == 200:
                data = r.json()
                self._injury_cache = {}
                for team in data.get("items", []):
                    t_abbr = team.get("team", {}).get("abbreviation", "")
                    injuries = []
                    for athlete in team.get("injuries", []):
                        status = athlete.get("status", "").lower()
                        name = athlete.get("athlete", {}).get("displayName", "")
                        if status in ["out", "doubtful"]:
                            injuries.append({"name": name, "status": status})
                    self._injury_cache[t_abbr] = injuries
                self._injury_cache_time = time.time()
            return self._injury_cache.get(team_abbr, [])
        except Exception:
            return []

    def calculate_probability(self, market, external_data=None) -> dict:
        question = market.question if hasattr(market, "question") else market.get("question", "")
        team1, team2 = self._parse_matchup(question)

        # No team found → fall back
        if not team1:
            return self._fallback(market)

        q_lower = question.lower()

        # ── CASE 1: Head-to-head matchup (team2 found) ─────────────────
        if team2:
            elo1 = self.ratings.get(team1, 1500)
            elo2 = self.ratings.get(team2, 1500)
            adjustments1 = 0
            adjustments2 = 0
            confidence = 0.50

            if "home" in q_lower or "at home" in q_lower:
                adjustments1 += 60
                confidence += 0.03

            injuries1 = self._fetch_injuries(team1)
            injuries2 = self._fetch_injuries(team2)
            adjustments1 -= len(injuries1) * 40
            adjustments2 -= len(injuries2) * 40
            if injuries1 or injuries2:
                confidence += 0.05

            adj_elo1 = elo1 + adjustments1
            adj_elo2 = elo2 + adjustments2
            prob = 1 / (1 + 10 ** ((adj_elo2 - adj_elo1) / 400))
            confidence = min(confidence, 0.75)

            return {
                "probability": prob,
                "confidence": confidence,
                "method": f"Elo_H2H({team1}={adj_elo1:.0f} vs {team2}={adj_elo2:.0f})",
                "factors": {
                    "team1": team1, "team2": team2,
                    "elo1": round(elo1), "elo2": round(elo2),
                    "adj1": adjustments1, "adj2": adjustments2,
                    "injuries1": len(injuries1), "injuries2": len(injuries2),
                },
                "reasoning": (
                    f"{team1}(Elo {adj_elo1:.0f}) vs {team2}(Elo {adj_elo2:.0f}). "
                    f"Prob={prob:.1%}. Injuries: {len(injuries1)} vs {len(injuries2)}."
                )
            }

        # ── CASE 2: Individual award question → fall back ───────────────
        q_type = self._detect_question_type(question)
        if q_type == "award":
            return self._fallback(market)

        # ── CASE 3: Season prediction (single team) ─────────────────────
        if q_type == "unknown":
            # Can't determine question type — use fallback
            return self._fallback(market)

        prob, confidence, reasoning = self._season_probability(team1, q_type)
        if prob is None:
            return self._fallback(market)

        # ── V2.3 MARKET PRICE SANITY CHECK ──────────────────────────────
        # Postmortem 21/03: Hornets conference (entry=3.95¢, -21.5%) and
        # Grizzlies worst_record (entry=4.3¢, -61.6%) both lost because:
        # 1. Market price < 5¢ already encodes current-season info (standings,
        #    injuries, form) that our static ELO doesn't see.
        # 2. For "win championship/conference/finals" at < 6¢: team is already
        #    near-eliminated by the market. ELO edge is likely a model error.
        # Fix: if market price < 0.06 on season-outcome markets, cap confidence.
        yes_price = market.yes_price if hasattr(market, 'yes_price') else market.get('yes_price', 0.5)
        if q_type in ('championship', 'conference', 'worst_record', 'finals') and yes_price < 0.06:
            old_conf = confidence
            confidence = min(confidence, 0.15)
            logger.info(
                f"ELO SANITY [{team1}] {q_type}: market={yes_price:.3f} < 6¢ → "
                f"confidence capped {old_conf:.0%}→{confidence:.0%}. "
                f"Market has more info than static ELO on current-season outcomes."
            )
            reasoning += f" | SANITY_CAP: market {yes_price:.3f} < 0.06 → conf capped to 15%"

        return {
            "probability": prob,
            "confidence": confidence,
            "method": f"Elo_season_{q_type}(#{self._get_rank(team1)})",
            "factors": {
                "team": team1,
                "elo": self.ratings.get(team1, 1500),
                "rank": self._get_rank(team1),
                "question_type": q_type,
                "market_price": yes_price,
                "sanity_cap_applied": yes_price < 0.06 and q_type in ('championship', 'conference', 'worst_record', 'finals'),
            },
            "reasoning": reasoning,
        }
