from bots.base_bot import BotBase
from core.worldmonitor_reader import WorldMonitorReader


class BotGeopolitics(BotBase):
    def __init__(self, **kwargs):
        super().__init__(niche_name='geopolitics', **kwargs)
        self.keywords = ['war', 'ceasefire', 'sanction', 'sanctions', 'conflict',
                        'military', 'nato', 'treaty', 'invasion', 'troops',
                        'nuclear', 'missile', 'peace', 'diplomacy', 'alliance']
        self.wm_reader = WorldMonitorReader()
        # More conservative Kelly for geopolitics
        self.kelly_override = 0.15

    def get_news(self):
        news = []
        try:
            geo_news = self.wm_reader._run_sync(self.wm_reader.get_geopolitics_news())
            if geo_news:
                news.extend(geo_news)
        except Exception as e:
            self.logger.error(f"Error getting geopolitics news: {e}")

        # Filter by CII scores > 60
        try:
            cii_scores = self.wm_reader._run_sync(self.wm_reader.get_cii_scores())
            if cii_scores:
                high_cii = [s for s in cii_scores if isinstance(s, dict) and s.get('score', 0) > 60]
                for item in high_cii[:3]:
                    news.append({
                        'title': f"High CII Alert: {item.get('country', item.get('region', 'Unknown'))}",
                        'summary': f"CII Score: {item.get('score', 'N/A')} - {item.get('description', item.get('factors', ''))}",
                        'source': 'cii_scores'
                    })
        except Exception as e:
            self.logger.error(f"Error getting CII scores: {e}")

        return news

    def get_extra_context(self):
        context = {}
        try:
            context['cii_scores'] = self.wm_reader._run_sync(self.wm_reader.get_cii_scores())
        except:
            pass
        try:
            context['trending_keywords'] = self.wm_reader._run_sync(self.wm_reader.get_trending_keywords())
        except:
            pass
        return context

    def _process_news(self, news, markets):
        """Override to use reduced Kelly fraction"""
        # Temporarily set reduced kelly for geopolitics
        original_fraction = self.config.get('kelly_fraction', 0.25)
        self.config['kelly_fraction'] = self.kelly_override
        try:
            super()._process_news(news, markets)
        finally:
            self.config['kelly_fraction'] = original_fraction

    def get_improvement_suggestions(self):
        return [
            "Add UN Security Council vote tracking",
            "Monitor satellite imagery analysis feeds",
            "Track diplomatic meeting schedules",
            "Add economic sanctions impact modeling",
            "Integrate refugee flow data as conflict intensity proxy"
        ]
