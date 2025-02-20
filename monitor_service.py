import api.auth.auth_details as bf_auth
from BFDriver import BFDriver, BFDriverException
from api.http_methods import Methods
from api.urls import Urls
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log


# update_odds_for_targets calls "listMarketBook" to update the odds for all the targets supplied in the
# target_list. It updates the values directly in the object so it doesn't directly return anything.


BF = BFDriver(FromFileStrategy(), Log.DEBUG)
global db_connection

try:
    db_details_string = BF.get_local_db_details()
    db_connection = DBOutputConnection()

    db_connection.open_connection(db_details_string)
    db_connection.db_write_log("Monitor Service: Starting run")

except Exception as e:
    raise BFDriverException("Failed to get local DB details: {}".format(e))

# Step 1: Authenticate and get a Session Token!
if not BF.get_token():
    db_connection.db_write_log("Failed to retrieve token")
    raise bf_auth.AuthException("Failed to authenticate to vault.  Validate that it is running (and unsealed) on "
                                "port 8200.  See error message above for exception details")

db_connection.db_write_log("Token retrieved")
Log.log_info("##############    Step 1 Complete")


#'STEP 1: Get all targets that are in status of INDENTIFIED'
raw_targets = db_connection.db_read("SELECT target_id, event_id, market_id, start_time, status FROM bf.target WHERE status = 'IDENTIFIED';")

targets = []

#'STEP 2: For each of these targets call "listMarketBook" and retrieve the status and the runners'
for target in raw_targets:
    market = target[2]
    Log.log_debug("Looking up odds for {}".format(market))
    json_resp = BF.call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                     request_body=BF.request_body_obj.populate_template(
                                         "listMarketBook",
                                         {
                                             "<ListOfMarketIDs>": [market]
                                         }
                                     )
                                     )
    Log.log_debug(json_resp)

    json = json_resp.json()

    status = json["result"][0]["status"]
    Log.log_info(f"Market: {market}, Status: {status}")
    # the updated odds will be returned in the runners section
    runner_list = json["result"][0]["runners"]
    Log.log_info(f"Market {market}, Runners: {len(runner_list)}")

    runners = []

    for runner in runner_list:
        Log.log_debug(f"Looking up odds in {runner}")
        runners.append(runner["selectionId"])

    targets.append((market, status, len(runner_list), runners))

Log.log_info("##############    Step 2 Complete")
Log.log_debug(targets)

#STEP 3: Update Target status in Postgres - Active or Closed based on response above

KNOWN_MARKET_STATES = ["OPEN", "CLOSED"]

for target in targets:
    if target[1] in KNOWN_MARKET_STATES:
        sql_command = f"UPDATE bf.target SET status='{target[1]}' WHERE market_id='{target[0]}';"
        success = db_connection.db_write(sql_command)
        Log.log_debug(f"Setting {target[0]} as {target[1]} status: {success}")
    else:
        raise Exception("Unknown market state: {}".format(target))

#STEP 4: Get all targets that are in status of OPEN - This may include data from previous runs so we have to go back
# and refer to the database rather than rely on data we already have
open_targets = db_connection.db_read("SELECT target_id, event_id, market_id, runner_ids, start_time, status, notes "
                                     "FROM bf.target WHERE status = 'OPEN';")

Log.log_info("Active Targets: {}".format(open_targets))

#STEP 6. Get the current odds for each runner

for target in open_targets:

    Log.log_debug(f"Looking up odds for target {target}")
    runner_details = target[3]
    runner_details.split("|")

    for individual_runner in runner_details:
        Log.log_debug(f"Looking up odds for runner {individual_runner}")
        selection_id = individual_runner.split("-")[0]

        Log.log_debug(f"Looking up odds {target[2]} : {type(target[2])}for selection id {selection_id} : type {type(selection_id)}")

        json_resp = BF.call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                     request_body=BF.request_body_obj.populate_template(
                                         "listRunnerBook",
                                         {
                                             "<MarketID>": target[2],
                                             "<RunnerID>": selection_id
                                         }
                                     )
                                     )
        Log.log_debug(json_resp)


#STEP 7 - Add the odds to the database table
#
# for target in open_targets:
#     sql_command = f"INSERT INTO bf.market_table(\"timestamp\", market_id, runner_id, odds) VALUES (current_timestamp, ?, ?, ?);"
#     success = db_connection.db_write(sql_command)
#     Log.log_debug(f"Setting {target[0]} as {target[1]} status: {success}")
