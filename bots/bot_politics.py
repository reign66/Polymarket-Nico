from bots.base_bot import BotBase
from core.worldmonitor_reader import WorldMonitorReader


class BotPolitics(BotBase):
    def __init__(self, **kwargs):
        super().__init__(niche_name='politics', **kwargs)
        self.keywords = ['election', 'president', 'vote', 'congress', 'senate',
                        'policy', 'law', 'bill', 'democrat', 'republican', 'poll',
                        'campaign', 'primary', 'governor', 'supreme court',
                        'legislation', 'executive order', 'inauguration']
        self.wm_reader = WorldMonitorReader()

    def get_news(self):
        news = []
        try:
            # Get general news (includes politics)
            tech_news = self.wm_reader._run_sync(self.wm_reader.get_tech_news())
            if tech_news:
                # Filter for political content
                for item in tech_news:
                    title = (item.get('title', '') + ' ' + item.get('summary', item.get('description', ''))).lower()
                    if any(kw in title for kw in self.keywords):
                        news.append(item)
        except Exception as e:
            self.logger.error(f"Error getting political news: {e}")

        try:
            trending = self.wm_reader._run_sync(self.wm_reader.get_trending_keywords())
            if trending:
                political_trending = [kw for kw in trending
                                    if isinstance(kw, (str, dict)) and
                                    any(pk in str(kw).lower() for pk in self.keywords)]
                if political_trending:
                    news.append({
                        'title': f"Political Trending: {', '.join(str(k) for k in political_trending[:5])}",
                        'summary': f"Trending political keywords detected",
                        'source': 'trending_keywords'
                    })
        except Exception as e:
            self.logger.error(f"Error getting trending keywords: {e}")

        return news

    def get_extra_context(self):
        context = {}
        try:
            context['trending_keywords'] = self.wm_reader._run_sync(self.wm_reader.get_trending_keywords())
        except:
            pass
        return context

    def get_improvement_suggestions(self):
        return [
            "Add polling aggregator integration (538, RCP)",
            "Track fundraising data as momentum indicator",
            "Monitor congressional voting patterns",
            "Add social media sentiment tracking for candidates",
            "Integrate betting odds from other prediction markets for cross-reference"
        ]
