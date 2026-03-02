import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestHaikuClassifier(unittest.TestCase):
    def setUp(self):
        self.mock_session = MagicMock()

    @patch('core.haiku_classifier.Anthropic')
    def test_classify_news_returns_none_for_low_relevance(self, mock_anthropic):
        from core.haiku_classifier import HaikuClassifier

        # Mock API response
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            '{"niche":"crypto","relevance":0.3,"market_id":null,'
            '"estimated_edge_yes":0.05,"estimated_edge_no":0.02,'
            '"best_direction":"SKIP","rationale":"Low relevance"}'
        ))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_anthropic.return_value.messages.create.return_value = mock_response

        classifier = HaikuClassifier(self.mock_session)
        result = classifier.classify_news(
            {'title': 'Test News', 'summary': 'Test summary'},
            [{'id': '1', 'question': 'Test market?', 'yes_price': 0.5, 'no_price': 0.5}]
        )
        self.assertIsNone(result)

    @patch('core.haiku_classifier.Anthropic')
    def test_classify_news_returns_result_for_high_relevance(self, mock_anthropic):
        from core.haiku_classifier import HaikuClassifier

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            '{"niche":"nba","relevance":0.85,"market_id":"123",'
            '"estimated_edge_yes":0.18,"estimated_edge_no":0.05,'
            '"best_direction":"YES","rationale":"Strong signal"}'
        ))]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_anthropic.return_value.messages.create.return_value = mock_response

        classifier = HaikuClassifier(self.mock_session)
        result = classifier.classify_news(
            {'title': 'NBA News', 'summary': 'Lakers win'},
            [{'id': '123', 'question': 'Lakers championship?', 'yes_price': 0.4, 'no_price': 0.6}]
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['best_direction'], 'YES')
        self.assertGreater(result['relevance'], 0.7)


class TestSonnetDecider(unittest.TestCase):
    def setUp(self):
        self.mock_session = MagicMock()

    @patch('core.sonnet_decider.check_daily_sonnet_limit', return_value=True)
    @patch('core.sonnet_decider.Anthropic')
    def test_decide_bet_skip_on_low_confidence(self, mock_anthropic, mock_limit):
        from core.sonnet_decider import SonnetDecider

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=(
            '{"probability_real":0.55,"edge_yes":0.05,"edge_no":0.03,'
            '"direction":"SKIP","confidence":"LOW",'
            '"bet_rationale":"Not enough edge","risk_factors":["uncertainty"]}'
        ))]
        mock_response.usage = MagicMock(input_tokens=200, output_tokens=100)
        mock_anthropic.return_value.messages.create.return_value = mock_response

        decider = SonnetDecider(self.mock_session)
        result = decider.decide_bet(
            haiku_result={
                'relevance': 0.8,
                'estimated_edge_yes': 0.15,
                'estimated_edge_no': 0.05,
                'niche': 'nba',
            },
            market_details={
                'question': 'Test?',
                'yes_price': 0.5,
                'no_price': 0.5,
                'volume': 50000,
            },
            news_context={'title': 'Test', 'summary': 'Test'}
        )
        self.assertEqual(result['direction'], 'SKIP')


class TestPositionSizer(unittest.TestCase):
    def test_kelly_size_yes_direction(self):
        from core.position_sizer import PositionSizer
        sizer = PositionSizer({'kelly_fraction': 0.25})
        size = sizer.kelly_size(p_win=0.65, market_price=0.50, direction='YES', bankroll=1000)
        self.assertGreater(size, 0)
        self.assertLessEqual(size, 50)  # Max 5% of bankroll

    def test_kelly_size_no_direction(self):
        from core.position_sizer import PositionSizer
        sizer = PositionSizer({'kelly_fraction': 0.25})
        size = sizer.kelly_size(p_win=0.65, market_price=0.50, direction='NO', bankroll=1000)
        self.assertGreater(size, 0)

    def test_kelly_size_zero_for_bad_odds(self):
        from core.position_sizer import PositionSizer
        sizer = PositionSizer({'kelly_fraction': 0.25})
        size = sizer.kelly_size(p_win=0.3, market_price=0.80, direction='YES', bankroll=1000)
        self.assertEqual(size, 0)


class TestRiskManager(unittest.TestCase):
    def test_check_liquidity_pass(self):
        from core.risk_manager import RiskManager
        rm = RiskManager({'min_market_volume': 10000, 'max_spread': 0.05}, MagicMock())
        ok, reason = rm.check_liquidity(50000, 0.03)
        self.assertTrue(ok)

    def test_check_liquidity_fail_volume(self):
        from core.risk_manager import RiskManager
        rm = RiskManager({'min_market_volume': 10000, 'max_spread': 0.05}, MagicMock())
        ok, reason = rm.check_liquidity(5000, 0.03)
        self.assertFalse(ok)


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv()
    unittest.main()
