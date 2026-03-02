import requests
import time
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class SportReader:
    BDL_BASE = 'https://api.balldontlie.io/v1'
    JOLPICA_BASE = 'https://api.jolpi.ca/ergast/f1'
    ESPN_BASE = 'https://site.api.espn.com/apis/site/v2/sports'
    CACHE_TTL = 900  # 15 minutes
    HEADERS = {'User-Agent': 'PolymarketBot/1.0 Research'}

    def __init__(self):
        self._cache: Dict[str, Dict] = {}  # {cache_key: {'data': ..., 'ts': float}}

    # ------------------------------------------------------------------
    # Core GET helper
    # ------------------------------------------------------------------

    def _get(self, url: str, params: Optional[Dict] = None) -> any:
        """
        Synchronous GET with:
        - 10s timeout
        - Up to 3 retries with exponential backoff (1s, 2s, 4s)
        - Results cached for CACHE_TTL seconds
        - Never raises — returns [] on all failures
        """
        cache_key = url + str(sorted(params.items()) if params else '')

        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached['ts']) < self.CACHE_TTL:
            logger.debug("Cache hit for %s", url)
            return cached['data']

        last_exception = None
        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers=self.HEADERS,
                    timeout=10
                )
                if response.status_code in (429, 403):
                    backoff = 2 ** attempt
                    logger.warning(
                        "HTTP %s on %s — waiting %ds before retry",
                        response.status_code, url, backoff
                    )
                    time.sleep(backoff)
                    continue

                response.raise_for_status()

                try:
                    data = response.json()
                except Exception:
                    logger.warning("Non-JSON response from %s", url)
                    data = []

                self._cache[cache_key] = {'data': data, 'ts': time.time()}
                return data

            except requests.RequestException as exc:
                last_exception = exc
                backoff = 2 ** attempt
                logger.warning(
                    "Attempt %d/3 failed for %s (%s) — retrying in %ds",
                    attempt + 1, url, exc, backoff
                )
                time.sleep(backoff)

            except Exception as exc:
                last_exception = exc
                logger.warning("Unexpected error for %s: %s", url, exc)
                break

        logger.warning("All retries failed for %s. Last error: %s", url, last_exception)
        return []

    # ------------------------------------------------------------------
    # NBA
    # ------------------------------------------------------------------

    def get_nba_games(self, days: int = 3) -> List[Dict]:
        """
        Fetch upcoming/recent NBA games from Ball Don't Lie + ESPN scoreboard.
        Returns list of dicts: home_team, away_team, date, score, status.
        """
        games: Dict[str, Dict] = {}

        # --- Ball Don't Lie ---
        try:
            start_date = datetime.utcnow().strftime('%Y-%m-%d')
            end_date = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')
            bdl_data = self._get(
                f"{self.BDL_BASE}/games",
                params={
                    'start_date': start_date,
                    'end_date': end_date,
                    'per_page': 100,
                }
            )
            raw_games = []
            if isinstance(bdl_data, dict):
                raw_games = bdl_data.get('data', [])
            elif isinstance(bdl_data, list):
                raw_games = bdl_data

            for g in raw_games:
                try:
                    key = f"{g.get('date', '')}_{g.get('id', '')}"
                    home = g.get('home_team', {})
                    visitor = g.get('visitor_team', {})
                    games[key] = {
                        'home_team': home.get('full_name', home.get('name', 'Unknown')),
                        'away_team': visitor.get('full_name', visitor.get('name', 'Unknown')),
                        'date': g.get('date', ''),
                        'score': {
                            'home': g.get('home_team_score'),
                            'away': g.get('visitor_team_score'),
                        },
                        'status': g.get('status', 'scheduled'),
                        'source': 'balldontlie',
                    }
                except Exception as exc:
                    logger.warning("Error parsing BDL game: %s", exc)

        except Exception as exc:
            logger.warning("BDL get_nba_games failed: %s", exc)

        # --- ESPN scoreboard ---
        try:
            espn_data = self._get(
                f"{self.ESPN_BASE}/basketball/nba/scoreboard"
            )
            events = []
            if isinstance(espn_data, dict):
                events = espn_data.get('events', [])
            elif isinstance(espn_data, list):
                events = espn_data

            for event in events:
                try:
                    competitions = event.get('competitions', [{}])
                    comp = competitions[0] if competitions else {}
                    competitors = comp.get('competitors', [])

                    home_info = next((c for c in competitors if c.get('homeAway') == 'home'), {})
                    away_info = next((c for c in competitors if c.get('homeAway') == 'away'), {})

                    home_team = home_info.get('team', {}).get('displayName', 'Unknown')
                    away_team = away_info.get('team', {}).get('displayName', 'Unknown')
                    date_str = event.get('date', '')
                    status_obj = event.get('status', {})
                    status = status_obj.get('type', {}).get('description', 'Scheduled')

                    key = f"{date_str}_{home_team}_{away_team}"
                    if key not in games:
                        games[key] = {
                            'home_team': home_team,
                            'away_team': away_team,
                            'date': date_str,
                            'score': {
                                'home': home_info.get('score'),
                                'away': away_info.get('score'),
                            },
                            'status': status,
                            'source': 'espn',
                        }
                except Exception as exc:
                    logger.warning("Error parsing ESPN event: %s", exc)

        except Exception as exc:
            logger.warning("ESPN get_nba_games failed: %s", exc)

        return list(games.values())

    def get_nba_team_stats(self, team_id: int) -> Dict:
        """
        Fetch NBA team stats from Ball Don't Lie.
        Returns dict with wins, losses, and key stat averages.
        """
        result: Dict = {}

        # Try season averages endpoint first
        try:
            data = self._get(
                f"{self.BDL_BASE}/season_averages",
                params={'team_ids[]': team_id, 'season': datetime.utcnow().year}
            )
            raw = []
            if isinstance(data, dict):
                raw = data.get('data', [])
            elif isinstance(data, list):
                raw = data

            if raw:
                stats = raw[0]
                result.update({
                    'pts': stats.get('pts'),
                    'reb': stats.get('reb'),
                    'ast': stats.get('ast'),
                    'stl': stats.get('stl'),
                    'blk': stats.get('blk'),
                    'fg_pct': stats.get('fg_pct'),
                    'fg3_pct': stats.get('fg3_pct'),
                    'ft_pct': stats.get('ft_pct'),
                    'turnover': stats.get('turnover'),
                    'source': 'balldontlie_season_averages',
                })
        except Exception as exc:
            logger.warning("BDL season averages failed for team %s: %s", team_id, exc)

        # Try teams endpoint for wins/losses record
        try:
            data = self._get(f"{self.BDL_BASE}/teams/{team_id}")
            if isinstance(data, dict):
                team_data = data.get('data', data)
                result.update({
                    'team_id': team_id,
                    'name': team_data.get('full_name', team_data.get('name', '')),
                    'wins': team_data.get('wins'),
                    'losses': team_data.get('losses'),
                })
        except Exception as exc:
            logger.warning("BDL teams/{id} failed for team %s: %s", team_id, exc)

        return result

    # ------------------------------------------------------------------
    # Formula 1
    # ------------------------------------------------------------------

    def get_f1_standings(self) -> Dict:
        """
        Fetch current F1 driver and constructor standings via Jolpica/Ergast.
        Returns dict with 'drivers' and 'constructors' lists.
        """
        standings: Dict = {'drivers': [], 'constructors': []}

        # Driver standings
        try:
            data = self._get(f"{self.JOLPICA_BASE}/current/driverStandings.json")
            tables = (
                data
                .get('MRData', {})
                .get('StandingsTable', {})
                .get('StandingsLists', [])
            )
            if tables:
                for entry in tables[0].get('DriverStandings', []):
                    driver = entry.get('Driver', {})
                    standings['drivers'].append({
                        'position': entry.get('position'),
                        'points': entry.get('points'),
                        'wins': entry.get('wins'),
                        'driver': f"{driver.get('givenName', '')} {driver.get('familyName', '')}".strip(),
                        'nationality': driver.get('nationality'),
                        'constructor': entry.get('Constructors', [{}])[0].get('name', ''),
                    })
        except Exception as exc:
            logger.warning("F1 driver standings failed: %s", exc)

        # Constructor standings
        try:
            data = self._get(f"{self.JOLPICA_BASE}/current/constructorStandings.json")
            tables = (
                data
                .get('MRData', {})
                .get('StandingsTable', {})
                .get('StandingsLists', [])
            )
            if tables:
                for entry in tables[0].get('ConstructorStandings', []):
                    constructor = entry.get('Constructor', {})
                    standings['constructors'].append({
                        'position': entry.get('position'),
                        'points': entry.get('points'),
                        'wins': entry.get('wins'),
                        'constructor': constructor.get('name', ''),
                        'nationality': constructor.get('nationality'),
                    })
        except Exception as exc:
            logger.warning("F1 constructor standings failed: %s", exc)

        return standings

    def get_f1_schedule(self) -> List[Dict]:
        """
        Fetch current F1 season race schedule via Jolpica/Ergast.
        Returns list of race dicts with name, date, circuit, country.
        """
        races: List[Dict] = []
        try:
            data = self._get(f"{self.JOLPICA_BASE}/current.json")
            raw_races = (
                data
                .get('MRData', {})
                .get('RaceTable', {})
                .get('Races', [])
            )
            for race in raw_races:
                circuit = race.get('Circuit', {})
                location = circuit.get('Location', {})
                races.append({
                    'round': race.get('round'),
                    'race_name': race.get('raceName', ''),
                    'circuit': circuit.get('circuitName', ''),
                    'country': location.get('country', ''),
                    'locality': location.get('locality', ''),
                    'date': race.get('date', ''),
                    'time': race.get('time', ''),
                })
        except Exception as exc:
            logger.warning("F1 schedule fetch failed: %s", exc)

        return races

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def _parse_espn_news(self, data: any) -> List[Dict]:
        """Parse ESPN news API response into a clean list of dicts."""
        items: List[Dict] = []
        try:
            articles = []
            if isinstance(data, dict):
                articles = data.get('articles', data.get('news', []))
            elif isinstance(data, list):
                articles = data

            for article in articles:
                try:
                    items.append({
                        'title': article.get('headline', article.get('title', '')),
                        'description': article.get('description', article.get('summary', '')),
                        'published': article.get('published', article.get('lastModified', '')),
                        'url': article.get('links', {}).get('web', {}).get('href', ''),
                        'source': 'espn',
                    })
                except Exception as exc:
                    logger.warning("Error parsing ESPN article: %s", exc)
        except Exception as exc:
            logger.warning("_parse_espn_news failed: %s", exc)

        return items

    def get_sport_news(self, sport: str = 'basketball') -> List[Dict]:
        """
        Fetch sport news from ESPN.
        sport examples: 'basketball' (NBA), 'football' (NFL), 'baseball' (MLB)
        Returns list of dicts with title, description, published, url.
        """
        try:
            # Try sport/league specific endpoint
            data = self._get(f"{self.ESPN_BASE}/{sport}/nba/news")
            news = self._parse_espn_news(data)
            if news:
                return news

            # Generic sport fallback
            data = self._get(f"{self.ESPN_BASE}/{sport}/news")
            return self._parse_espn_news(data)

        except Exception as exc:
            logger.warning("get_sport_news(%s) failed: %s", sport, exc)
            return []

    def get_f1_news(self) -> List[Dict]:
        """
        Fetch F1 news from ESPN racing endpoint.
        Returns list of dicts with title, description, published, url.
        """
        try:
            data = self._get(f"{self.ESPN_BASE}/racing/f1/news")
            news = self._parse_espn_news(data)
            if news:
                return news

            # Fallback to generic racing news
            data = self._get(f"{self.ESPN_BASE}/racing/news")
            return self._parse_espn_news(data)

        except Exception as exc:
            logger.warning("get_f1_news failed: %s", exc)
            return []
