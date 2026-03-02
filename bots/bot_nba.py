from bots.base_bot import BotBase
from core.sport_reader import SportReader


class BotNBA(BotBase):
    def __init__(self, **kwargs):
        super().__init__(niche_name='nba', **kwargs)
        self.keywords = ['nba', 'basketball', 'lakers', 'celtics', 'warriors', 'bucks',
                        'playoffs', 'mvp', 'finals', 'eastern', 'western', 'conference',
                        'championship', 'scoring', 'triple-double']
        self.sport_reader = SportReader()

    def get_news(self):
        news = []
        try:
            nba_news = self.sport_reader.get_sport_news('basketball')
            if nba_news:
                news.extend(nba_news)
        except Exception as e:
            self.logger.error(f"Error getting NBA news: {e}")

        try:
            games = self.sport_reader.get_nba_games(days=3)
            # Convert upcoming games to "news" format
            for game in games[:5]:
                news.append({
                    'title': f"NBA: {game.get('home_team', '?')} vs {game.get('away_team', '?')}",
                    'summary': f"Game on {game.get('date', 'TBD')}. Status: {game.get('status', 'scheduled')}",
                    'source': 'nba_schedule'
                })
        except Exception as e:
            self.logger.error(f"Error getting NBA games: {e}")

        return news

    def get_extra_context(self):
        """Add NBA-specific stats to Sonnet context"""
        context = {}
        try:
            games = self.sport_reader.get_nba_games(days=3)
            context['upcoming_games'] = games[:5]
        except:
            pass
        return context

    def get_improvement_suggestions(self):
        return [
            "Add pace, ORTG/DRTG stats for better game predictions",
            "Track back-to-back games (fatigue factor)",
            "Integrate injury reports from ESPN",
            "Add home/away split performance data",
            "Track referee tendencies for over/under markets"
        ]
