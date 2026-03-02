from bots.base_bot import BotBase
from core.sport_reader import SportReader


class BotF1(BotBase):
    def __init__(self, **kwargs):
        super().__init__(niche_name='f1', **kwargs)
        self.keywords = ['f1', 'formula 1', 'formula one', 'verstappen', 'hamilton',
                        'leclerc', 'norris', 'constructors', 'grand prix', 'gp',
                        'pole position', 'fastest lap', 'podium', 'race winner']
        self.sport_reader = SportReader()

    def get_news(self):
        news = []
        try:
            f1_news = self.sport_reader.get_f1_news()
            if f1_news:
                news.extend(f1_news)
        except Exception as e:
            self.logger.error(f"Error getting F1 news: {e}")

        try:
            schedule = self.sport_reader.get_f1_schedule()
            for race in schedule[:3]:
                news.append({
                    'title': f"F1: {race.get('raceName', 'Unknown GP')}",
                    'summary': f"Round {race.get('round', '?')} at {race.get('circuitName', '?')} on {race.get('date', 'TBD')}",
                    'source': 'f1_schedule'
                })
        except Exception as e:
            self.logger.error(f"Error getting F1 schedule: {e}")

        return news

    def get_extra_context(self):
        context = {}
        try:
            standings = self.sport_reader.get_f1_standings()
            context['driver_standings'] = standings.get('drivers', [])[:10]
            context['constructor_standings'] = standings.get('constructors', [])[:5]
        except:
            pass
        return context

    def get_improvement_suggestions(self):
        return [
            "Add weather data for race weekends (rain predictions)",
            "Track qualifying vs race performance correlation",
            "Add tire strategy analysis from practice sessions",
            "Integrate team radio sentiment for driver confidence",
            "Track DNF rates per circuit for reliability predictions"
        ]
