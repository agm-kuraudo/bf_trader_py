from logic.simpleStategy import FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log
from BFDriver import BFDriver
import pandas as pd
import ast
import matplotlib.pyplot as plt


class MarketAnalyser:
    def __init__(self, market_id, start_time=None, end_time=None):
        self.market_id = str(market_id)
        self.start_time = start_time
        self.end_time = end_time
        self.db_connection = DBOutputConnection()
        self.df = pd.DataFrame(columns=['timestamp', 'runner_id', 'odds_to_back', 'odds_to_lay'])
        self.BF = BFDriver(FromFileStrategy(), Log.DEBUG)
        self.db_details_string = self.BF.get_local_db_details()

    def open_db_connection(self):
        try:
            self.db_connection.open_connection(self.db_details_string)
            self.db_connection.db_write_log("Analyse Service: Starting run")
        except Exception as e:
            Log.log_error(f"Failed to open database connection: {e}")
            raise

    def get_runners(self):
        try:
            runners = self.db_connection.db_read(
                f"SELECT runner_ids FROM bf.target where market_id = '{self.market_id}';")
            runners = runners[0][0]  # Extract the runner string
            split_string = runners.split('|')
            runners_list = [(part.split('-')[0], part.split('-')[1]) for part in split_string]
            Log.log_info("List of Runners: {}".format(runners_list))
            for runner in runners_list:
                Log.log_debug(runner[1])
            return runners_list
        except Exception as e:
            Log.log_error(f"Failed to read runner IDs: {e}")
            raise

    def get_market_data(self):
        if self.start_time and self.end_time:
            query = f"SELECT \"timestamp\", market_id, runner_id, odds FROM bf.market_table where market_id='{self.market_id}' AND \"timestamp\" BETWEEN '{self.start_time}' AND '{self.end_time}';"
        else:
            query = f"SELECT \"timestamp\", market_id, runner_id, odds FROM bf.market_table where market_id='{self.market_id}';"

        try:
            raw_data_points = self.db_connection.db_read(query)
            return raw_data_points
        except Exception as e:
            Log.log_error(f"Failed to read market data points: {e}")
            raise

    def process_data(self, raw_data_points):
        for data_point in raw_data_points:
            odds_dict = ast.literal_eval(data_point[3])
            new_row = pd.DataFrame([{
                'timestamp': data_point[0],
                'runner_id': data_point[2],
                'odds_to_back': odds_dict['availableToBack'][1]['price'],
                'odds_to_lay': odds_dict['availableToLay'][1]['price']
            }])
            # Exclude empty or all-NA entries before concatenation
            if not new_row.isna().all().all():
                self.df = pd.concat([self.df, new_row], ignore_index=True)
        self.df['timestamp'] = pd.to_datetime(self.df['timestamp'])

    def plot_data(self):
        for runner_id in self.df['runner_id'].unique():
            runner_df = self.df[self.df['runner_id'] == runner_id]
            plt.figure(figsize=(10, 6))
            plt.plot(runner_df['timestamp'], runner_df['odds_to_back'], label=f'Runner {runner_id} - Back')
            plt.plot(runner_df['timestamp'], runner_df['odds_to_lay'], label=f'Runner {runner_id} - Lay')
            plt.xlabel('Timestamp')
            plt.ylabel('Odds')
            plt.title(f'Odds Over Time for Runner {runner_id}')
            plt.legend()
            plt.grid(True)
            plt.savefig(f'charts/{self.market_id.replace(".", "-")}_runner_{runner_id}_odds_over_time.png')
            plt.close()

    def run_analysis(self):
        self.open_db_connection()
        self.get_runners()
        raw_data_points = self.get_market_data()
        self.process_data(raw_data_points)
        self.plot_data()

# Example usage
if __name__ == "__main__":
    service = MarketAnalyser("1.240323372", '2025-03-14 00:00:00', '2025-03-29 00:00:00')
    service.run_analysis()