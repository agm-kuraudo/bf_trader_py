from logic.simpleStategy import FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log
from BFDriver import BFDriver
import pandas as pd
import ast
import matplotlib.pyplot as plt

BF = BFDriver(FromFileStrategy(), Log.DEBUG)

db_details_string = BF.get_local_db_details()
db_connection = DBOutputConnection()
db_connection.open_connection(db_details_string)
db_connection.db_write_log("Analyse Service: Starting run")

MARKET_TO_ANALYSE = '1.240323372'

runners = db_connection.db_read("SELECT runner_ids FROM bf.target where market_id = '1.240323372';")
runners = runners[0][0] #extract the runner string

split_string = runners.split('|')
runners_list = [(part.split('-')[0], part.split('-')[1]) for part in split_string]

Log.log_info("List of Runners: {}".format(runners_list))

for runner in runners_list:
    Log.log_debug(runner[1])

raw_data_points = db_connection.db_read("SELECT \"timestamp\", market_id, runner_id, odds FROM bf.market_table where market_id='1.240323372';")

# Create an empty DataFrame with the specified columns
df = pd.DataFrame(columns=['timestamp', 'runner_id', 'odds_to_back', 'odds_to_lay'])

for data_point in raw_data_points:

    # print(data_point[3])
    # print(type(data_point[3]))

    odds_dict = ast.literal_eval(data_point[3])

    # print(odds_dict['availableToBack'][1]['price'])

    new_row = pd.DataFrame([{
        'timestamp': data_point[0],
        'runner_id': data_point[2],
        'odds_to_back': odds_dict['availableToBack'][1]['price'],
        'odds_to_lay': odds_dict['availableToLay'][1]['price']
    }])
    df = pd.concat([df, new_row], ignore_index=True)

# Convert timestamp to datetime
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Plotting the odds over time for each runner and saving the chart
for runner_id in df['runner_id'].unique():
    runner_df = df[df['runner_id'] == runner_id]

    plt.figure(figsize=(10, 6))
    plt.plot(runner_df['timestamp'], runner_df['odds_to_back'], label=f'Runner {runner_id} - Back')
    plt.plot(runner_df['timestamp'], runner_df['odds_to_lay'], label=f'Runner {runner_id} - Lay')

    plt.xlabel('Timestamp')
    plt.ylabel('Odds')
    plt.title(f'Odds Over Time for Runner {runner_id}')
    plt.legend()
    plt.grid(True)

    # Save the chart as a PNG file
    plt.savefig(f'charts/{MARKET_TO_ANALYSE.replace(".", "-")}_runner_{runner_id}_odds_over_time.png')
    plt.close()

