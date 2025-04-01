import unittest
from unittest.mock import patch, MagicMock

from analyse_service import MarketAnalyser
import pandas as pd
from output.log import Output as Log

class TestMarketAnalyser(unittest.TestCase):
    Log.LOG_FILE=False

    @patch('analyse_service.DBOutputConnection')
    @patch('analyse_service.BFDriver')
    def setUp(self, MockBFDriver, MockDBOutputConnection):
        self.mock_bf_driver = MockBFDriver.return_value
        self.mock_db_connection = MockDBOutputConnection.return_value
        self.analyser = MarketAnalyser('1.240323372')

    def test_open_db_connection(self):
        self.analyser.open_db_connection()
        self.mock_db_connection.open_connection.assert_called_once()
        self.mock_db_connection.db_write_log.assert_called_once_with("Analyse Service: Starting run")

    def test_get_runners(self):
        self.mock_db_connection.db_read.return_value = [('runner1-1|runner2-2',)]
        runners = self.analyser.get_runners()
        self.assertEqual(runners, [('runner1', '1'), ('runner2', '2')])

    def test_get_market_data(self):
        self.analyser.start_time = '2025-03-01 00:00:00'
        self.analyser.end_time = '2025-03-10 23:59:59'
        self.mock_db_connection.db_read.return_value = [('2025-03-01 12:00:00', '1.240323372', 'runner1', "{'availableToBack': [{'price': 2.0}], 'availableToLay': [{'price': 2.2}]}")]
        data_points = self.analyser.get_market_data()
        self.assertEqual(len(data_points), 1)

    def test_process_data(self):
        raw_data_points = [
            ('2025-03-01 12:00:00', '1.240323372', 'runner1',
             "{'availableToBack': [{'price': 2.0}, {'price': 2.1}, {'price': 2.2}], 'availableToLay': [{'price': 2.3}, {'price': 2.4}, {'price': 2.5}]}"),
            ('2025-03-01 12:05:00', '1.240323372', 'runner2',
             "{'availableToBack': [{'price': 3.0}, {'price': 3.1}, {'price': 3.2}], 'availableToLay': [{'price': 3.3}, {'price': 3.4}, {'price': 3.5}]}")
        ]
        self.analyser.process_data(raw_data_points)
        self.assertEqual(len(self.analyser.df), 2)  # Two rows should be added
        self.assertEqual(self.analyser.df.iloc[0]['odds_to_back'], 2.1)
        self.assertEqual(self.analyser.df.iloc[0]['odds_to_lay'], 2.4)
        self.assertEqual(self.analyser.df.iloc[1]['odds_to_back'], 3.1)
        self.assertEqual(self.analyser.df.iloc[1]['odds_to_lay'], 3.4)

    @patch('analyse_service.plt')
    def test_plot_data(self, mock_plt):
        self.analyser.df = pd.DataFrame({
            'timestamp': pd.to_datetime(['2025-03-01 12:00:00']),
            'runner_id': ['runner1'],
            'odds_to_back': [2.0],
            'odds_to_lay': [2.2]
        })
        self.analyser.plot_data()
        mock_plt.savefig.assert_called_once()

if __name__ == '__main__':
    unittest.main()