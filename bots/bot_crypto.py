from bots.base_bot import BotBase
from core.worldmonitor_reader import WorldMonitorReader


class BotCrypto(BotBase):
    def __init__(self, **kwargs):
        super().__init__(niche_name='crypto', **kwargs)
        self.keywords = ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto', 'cryptocurrency',
                        'fed', 'federal reserve', 'rate', 'interest rate', 'etf', 'sec',
                        'stablecoin', 'defi', 'blockchain', 'halving', 'mining']
        self.wm_reader = WorldMonitorReader()

    def get_news(self):
        news = []
        try:
            finance_news = self.wm_reader._run_sync(self.wm_reader.get_finance_news())
            if finance_news:
                news.extend(finance_news)
        except Exception as e:
            self.logger.error(f"Error getting finance news: {e}")

        try:
            macro = self.wm_reader._run_sync(self.wm_reader.get_macro_signals())
            if macro:
                for signal in macro[:5]:
                    if isinstance(signal, dict):
                        news.append({
                            'title': signal.get('title', signal.get('signal', 'Macro Signal')),
                            'summary': signal.get('description', signal.get('details', '')),
                            'source': 'macro_signals'
                        })
        except Exception as e:
            self.logger.error(f"Error getting macro signals: {e}")

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
            "Add on-chain metrics (whale movements, exchange flows)",
            "Track funding rates for sentiment analysis",
            "Integrate Fear & Greed Index",
            "Add correlation with traditional markets (S&P500, Gold)",
            "Monitor stablecoin market cap changes"
        ]
