import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class BotBase(ABC):
    def __init__(self, niche_name: str, config: dict, db_session,
                 polymarket_client, haiku_classifier, sonnet_decider,
                 position_sizer, risk_manager, telegram_alerter=None):
        self.niche = niche_name
        self.config = config
        self.session = db_session
        self.pm_client = polymarket_client
        self.haiku = haiku_classifier
        self.sonnet = sonnet_decider
        self.sizer = position_sizer
        self.risk = risk_manager
        self.telegram = telegram_alerter
        self.keywords = []  # Override in subclass
        self.logger = logging.getLogger(f'bot.{niche_name}')

    def run_cycle(self):
        """Complete pipeline for one cycle"""
        try:
            self.logger.info(f"[{self.niche.upper()}] Starting cycle")

            # a) Get news
            news_items = self.get_news()
            if not news_items:
                self.logger.info(f"[{self.niche.upper()}] No news found")
                return

            # b) Get active markets for this niche
            markets = self.pm_client.get_active_markets(self.keywords)
            if not markets:
                self.logger.info(f"[{self.niche.upper()}] No active markets found")
                return

            self.logger.info(f"[{self.niche.upper()}] {len(news_items)} news, {len(markets)} markets")

            for news in news_items[:5]:  # Process max 5 news per cycle
                self._process_news(news, markets)

        except Exception as e:
            self.logger.error(f"[{self.niche.upper()}] Cycle error: {e}", exc_info=True)

    def _process_news(self, news: dict, markets: list):
        """Process a single news item through the full pipeline"""
        from core.database import record_signal

        # c) Haiku classification
        haiku_result = self.haiku.classify_news(news, markets)

        if haiku_result is None:
            # Filtered by Haiku
            record_signal(self.session,
                bot_niche=self.niche,
                news_title=news.get('title', 'Unknown'),
                news_summary=news.get('summary', news.get('description', '')),
                haiku_score=0, haiku_edge_yes=0, haiku_edge_no=0,
                haiku_direction='SKIP', sonnet_called=False,
                action_taken='FILTERED', skip_reason='Haiku filtered')
            return

        # d) Check circuit breakers
        cb_ok, cb_reason = self.risk.check_circuit_breakers()
        if not cb_ok:
            record_signal(self.session,
                bot_niche=self.niche,
                news_title=news.get('title', 'Unknown'),
                news_summary=news.get('summary', ''),
                haiku_score=haiku_result.get('relevance', 0),
                haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
                haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
                haiku_direction=haiku_result.get('best_direction', 'SKIP'),
                sonnet_called=False,
                action_taken='BLOCKED', skip_reason=f'Circuit breaker: {cb_reason}')
            if self.telegram:
                self.telegram.send_circuit_breaker_alert(cb_reason)
            return

        # e) Check market dedup
        market_id = haiku_result.get('market_id')
        if market_id:
            dedup_ok, dedup_reason = self.risk.check_market_dedup(market_id)
            if not dedup_ok:
                record_signal(self.session,
                    bot_niche=self.niche,
                    news_title=news.get('title', 'Unknown'),
                    news_summary=news.get('summary', ''),
                    haiku_score=haiku_result.get('relevance', 0),
                    haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
                    haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
                    haiku_direction=haiku_result.get('best_direction', 'SKIP'),
                    sonnet_called=False, market_id=market_id,
                    action_taken='BLOCKED', skip_reason=dedup_reason)
                return

        # f) Sonnet decision
        market_details = self.pm_client.get_market_details(market_id) if market_id else None
        if not market_details:
            return

        extra_context = self.get_extra_context()
        sonnet_result = self.sonnet.decide_bet(
            haiku_result, market_details, news,
            cii_scores=extra_context.get('cii_scores'),
            trending_keywords=extra_context.get('trending_keywords'))

        if sonnet_result is None or sonnet_result.get('direction') == 'SKIP':
            record_signal(self.session,
                bot_niche=self.niche,
                news_title=news.get('title', 'Unknown'),
                news_summary=news.get('summary', ''),
                haiku_score=haiku_result.get('relevance', 0),
                haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
                haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
                haiku_direction=haiku_result.get('best_direction', 'SKIP'),
                sonnet_called=True,
                sonnet_direction=sonnet_result.get('direction', 'SKIP') if sonnet_result else 'SKIP',
                sonnet_confidence=sonnet_result.get('confidence', 'LOW') if sonnet_result else 'LOW',
                sonnet_edge=max(sonnet_result.get('edge_yes', 0), sonnet_result.get('edge_no', 0)) if sonnet_result else 0,
                market_id=market_id,
                market_question=market_details.get('question', ''),
                action_taken='SKIP', skip_reason='Sonnet: SKIP')
            return

        direction = sonnet_result['direction']
        edge = sonnet_result.get(f'edge_{direction.lower()}', 0)

        # g) Check liquidity
        volume = market_details.get('volume', 0)
        spread = market_details.get('spread', 1.0)
        liq_ok, liq_reason = self.risk.check_liquidity(volume, spread)
        if not liq_ok:
            record_signal(self.session,
                bot_niche=self.niche, news_title=news.get('title', 'Unknown'),
                news_summary=news.get('summary', ''),
                haiku_score=haiku_result.get('relevance', 0),
                haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
                haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
                haiku_direction=haiku_result.get('best_direction', 'SKIP'),
                sonnet_called=True, sonnet_direction=direction,
                sonnet_confidence=sonnet_result.get('confidence'),
                sonnet_edge=edge, market_id=market_id,
                market_question=market_details.get('question', ''),
                action_taken='BLOCKED', skip_reason=liq_reason)
            return

        # h) Position sizing
        from core.database import get_capital
        bankroll = get_capital(self.session)
        price = market_details.get('yes_price', 0.5) if direction == 'YES' else market_details.get('no_price', 0.5)
        p_win = sonnet_result.get('probability_real', 0.5)

        kelly_fraction = self.config.get('kelly_fraction', 0.25)
        size = self.sizer.kelly_size(p_win, price, direction, bankroll, kelly_fraction)

        if size <= 0:
            record_signal(self.session,
                bot_niche=self.niche, news_title=news.get('title', 'Unknown'),
                news_summary=news.get('summary', ''),
                haiku_score=haiku_result.get('relevance', 0),
                haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
                haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
                haiku_direction=haiku_result.get('best_direction', 'SKIP'),
                sonnet_called=True, sonnet_direction=direction,
                sonnet_confidence=sonnet_result.get('confidence'),
                sonnet_edge=edge, market_id=market_id,
                market_question=market_details.get('question', ''),
                action_taken='SKIP', skip_reason='Kelly size = 0')
            return

        # i) Check daily capital exposure
        signal_info = {'edge': edge, 'volume': volume, 'confidence': sonnet_result.get('confidence')}
        cap_ok, cap_reason = self.risk.check_daily_capital_exposure(size, signal_info)
        if not cap_ok:
            record_signal(self.session,
                bot_niche=self.niche, news_title=news.get('title', 'Unknown'),
                news_summary=news.get('summary', ''),
                haiku_score=haiku_result.get('relevance', 0),
                haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
                haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
                haiku_direction=haiku_result.get('best_direction', 'SKIP'),
                sonnet_called=True, sonnet_direction=direction,
                sonnet_confidence=sonnet_result.get('confidence'),
                sonnet_edge=edge, market_id=market_id,
                market_question=market_details.get('question', ''),
                action_taken='BLOCKED', skip_reason=cap_reason)
            return

        # j) Place bet!
        result = self.pm_client.place_bet(market_id, direction, size, price)

        # k) Record signal as BET
        record_signal(self.session,
            bot_niche=self.niche, news_title=news.get('title', 'Unknown'),
            news_summary=news.get('summary', ''),
            haiku_score=haiku_result.get('relevance', 0),
            haiku_edge_yes=haiku_result.get('estimated_edge_yes', 0),
            haiku_edge_no=haiku_result.get('estimated_edge_no', 0),
            haiku_direction=haiku_result.get('best_direction', 'SKIP'),
            sonnet_called=True, sonnet_direction=direction,
            sonnet_confidence=sonnet_result.get('confidence'),
            sonnet_edge=edge, market_id=market_id,
            market_question=market_details.get('question', ''),
            action_taken='BET')

        # l) Telegram notification
        if self.telegram:
            self.telegram.send_entry_notification(
                niche=self.niche,
                market_name=market_details.get('question', 'Unknown'),
                direction=direction,
                entry_price=price,
                amount=size,
                edge=edge,
                confidence=sonnet_result.get('confidence'),
                score=haiku_result.get('relevance'),
                end_date=market_details.get('end_date'),
                volume=volume,
                rationale=sonnet_result.get('bet_rationale'),
                risk_factors=sonnet_result.get('risk_factors', []),
                paper=self.pm_client.paper_trading)

        self.logger.info(f"[{self.niche.upper()}] BET PLACED: {direction} on {market_details.get('question', '')} | Size: ${size} | Edge: {edge:.1%}")

    def get_extra_context(self) -> dict:
        """Override in subclass to provide extra context for Sonnet"""
        return {}

    @abstractmethod
    def get_news(self) -> List[dict]:
        """Get relevant news for this niche"""
        pass

    @abstractmethod
    def get_improvement_suggestions(self) -> List[str]:
        """Return improvement suggestions for this bot"""
        pass
