import datetime
import json
import api.auth.auth_details as bf_auth
from BFDriver import BFDriver, BFDriverException
from api.http_methods import Methods
from api.urls import Urls
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log
from datetime import datetime, timedelta, timezone
import time

class MonitorServiceException(Exception):
    pass

class MonitorService:
    def __init__(self, log_level=Log.INFO, strategy=FromFileStrategy()):
        self.BF = BFDriver(strategy, log_level)
        self.db_connection = None

    def authenticate_and_get_token(self):
        try:
            if not self.BF.get_token():
                self.db_connection.db_write_log("Monitor Service: ERROR : Ending Run : Failed to retrieve token")
                raise bf_auth.AuthException("Failed to authenticate to vault. Validate that it is running (and unsealed) on port 8200.")
            #self.db_connection.db_write_log("Token retrieved")
            Log.log_info("##############    Login Token Retrieved")
        except Exception as e:
            raise MonitorServiceException(f"Authentication failed: {e}")

    def get_targets(self):
        try:
            raw_targets = self.db_connection.db_read("SELECT target_id, event_id, market_id, runner_ids, start_time, status, update_frequency, last_updated, notes FROM bf.target WHERE status in ('IDENTIFIED', 'OPEN');")
            Log.log_info(f"##############    Step 1 Complete - {len(raw_targets)} targets in IDENTIFIED", force_console_log=True)
            return raw_targets
        except Exception as e:
            self.db_connection.db_write_log(f"Monitor Service: ERROR : Ending Run : Failed to get targets: {e}")
            raise MonitorServiceException(f"Failed to get targets: {e}")

    def process_targets(self, raw_targets):
        try:
            targets = []
            for target in raw_targets:
                market = target[2]
                Log.log_debug("Looking up runners for {}".format(market))
                json_resp = self.BF.call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                                  request_body=self.BF.request_body_obj.populate_template(
                                                      "listMarketBook",
                                                      {"<ListOfMarketIDs>": [market]}
                                                  ))
                Log.log_debug(json_resp)
                json = json_resp.json()
                status = json["result"][0]["status"]
                Log.log_debug(f"Market: {market}, Status: {status}")
                runner_list = json["result"][0]["runners"]
                Log.log_debug(f"Market {market}, Runners: {len(runner_list)}")
                runners = [runner["selectionId"] for runner in runner_list]
                targets.append((market, status, len(runner_list), runners, target[6], target[7], target[4]))
            Log.log_info(f"##############    Step 2 Complete {len(targets)} processed targets:", force_console_log=True)
            Log.log_debug(targets)
            return targets
        except Exception as e:
            self.db_connection.db_write_log(f"Monitor Service: ERROR : Ending Run : Failed to update odds for targets: {e}")
            raise MonitorServiceException(f"Failed to update odds for targets: {e}")

    def update_target_status(self, targets):
        try:
            KNOWN_MARKET_STATES = ["OPEN", "CLOSED"]
            for target in targets:
                if target[1] in KNOWN_MARKET_STATES:
                    sql_command = f"UPDATE bf.target SET status='{target[1]}' WHERE market_id='{target[0]}';"
                    success = self.db_connection.db_write(sql_command)
                    Log.log_debug(f"Setting {target[0]} as {target[1]} status: {success}")
                else:
                    self.db_connection.db_write_log(
                        f"Monitor Service: ERROR : Ending Run : Unknown market state: {target}")
                    raise MonitorServiceException(f"Unknown market state: {target}")
        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to update target status: {e}")
            raise MonitorServiceException(f"Failed to update target status: {e}")

    # This function is essentially deprecated as the "get_filtered_targets" now selects only open targets
    def get_open_targets(self):
        try:
            open_targets = self.db_connection.db_read("SELECT target_id, event_id, market_id, runner_ids, start_time, status, notes FROM bf.target WHERE status = 'OPEN';")
            if len(open_targets) == 0:
                Log.log_warning("No open targets found")
            else:
                Log.log_info("Active Targets: {}".format(open_targets))
            return open_targets
        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to get open targets: {e}")
            raise MonitorServiceException(f"Failed to get open targets: {e}")

    def get_filtered_targets(self, open_targets):
        try:
            targets_to_update = []
            Log.log_info("##############    Step 3 get_filtered_targets")

            # Initialise the nearest update time to something far in the future
            nearest_update_time = 99999

            for target in open_targets:
                Log.log_debug(target)

                # Retrieve the current date and time
                current_time = datetime.now(timezone.utc)
                Log.log_debug(f"Current date and time: {current_time}")

                seconds_until_next_update = target[4]
                Log.log_debug(f"Seconds until next update: {seconds_until_next_update}")

                # Assuming target[5] contains the last updated time as a datetime object
                last_update_time = target[5]
                Log.log_debug(f"Last update time: {last_update_time}")

                # Calculate the next update time
                next_update_time = last_update_time + timedelta(seconds=seconds_until_next_update)
                Log.log_debug(f"Next update time: {next_update_time}")

                # Calculate the number of seconds until the next update is required
                time_until_next_update = next_update_time - current_time
                seconds_until_next_update_required = time_until_next_update.total_seconds()
                Log.log_debug(f"Seconds until next update is required: {seconds_until_next_update_required}")

                if seconds_until_next_update_required < nearest_update_time:
                    nearest_update_time = seconds_until_next_update_required
                    Log.log_debug(f"Nearest update time updated: {nearest_update_time}")

                if target[1] == 'OPEN' and seconds_until_next_update_required < 0:
                    targets_to_update.append(target)

            return (targets_to_update, nearest_update_time)
        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to filter targets that require update: {e}")
            raise MonitorServiceException(f"Failed to filter targets that require update: {e}")

    def update_runner_odds(self, open_targets):
        try:
            for target in open_targets:
                Log.log_debug(f"Looking up odds for target {target}")
                # runner_details = target[3].split("|")
                for individual_runner in target[3]:
                    #selection_id = individual_runner.split("-")[0]
                    Log.log_debug(f"Looking up odds {target[2]} for selection id {individual_runner}")
                    resp = self.BF.call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                                 request_body=self.BF.request_body_obj.populate_template(
                                                     "listRunnerBook",
                                                     {"<MarketID>": str(target[0]), "<RunnerID>": str(individual_runner)}
                                                 ))
                    Log.log_debug(resp)
                    json_resp = resp.json()
                    status = json_resp["result"][0]["status"]
                    Log.log_debug(f"Market: {target[2]}, Runner: {individual_runner}, Status: {status}")
                    odds = json_resp["result"][0]["runners"][0]["ex"]
                    Log.log_debug(f"odds back: {odds}")
                    odds_str = json.dumps(odds) if type(odds) != dict else str(odds)
                    sql_command = ("INSERT INTO bf.market_table(\"timestamp\", market_id, runner_id, odds) VALUES (current_timestamp, %s, %s, %s);")
                    success = self.db_connection.db_write(sql_command, (target[0], individual_runner, odds_str))
                    Log.log_info(f"Updating odds for {target[0]} runner {individual_runner} status: {success}")

                # Target[6] is the event start time
                now = datetime.now(timezone.utc)
                target_time = target[6]
                next_update_time_seconds = 0

                if target_time < now:
                    Log.log_info(f"Target {target[0]} is open")
                    next_update_time_seconds = 5
                elif target_time < now + timedelta(hours=3):
                    Log.log_info(f"Target {target[0]} is less than 3 hours away")
                    next_update_time_seconds = 300
                elif target_time < now + timedelta(hours=6):
                    Log.log_info(f"Target {target[0]} is less than 6 hours away")
                    next_update_time_seconds = 900
                elif target_time < now + timedelta(hours=12):
                    Log.log_info(f"Target {target[0]} is less than 12 hours away")
                    next_update_time_seconds = 3600
                else:
                    Log.log_info(f"Target {target[0]} is more than 12 hours away")
                    next_update_time_seconds = 14400

                #Updating the last updated time for that target
                sql_command = (f"UPDATE bf.target SET last_updated=NOW(), update_frequency=%s WHERE market_id=%s;")
                success = self.db_connection.db_write(sql_command, (next_update_time_seconds, target[0],))
                Log.log_debug(f"Updating last updated time for {target[0]} status: {success}")


        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to update runner odds: {e}")
            raise MonitorServiceException(f"Failed to update runner odds: {e}")

    def run(self):
        try:

            # reload from db needs to be true for the first iteration
            reload_from_db = True

            db_details_string = self.BF.get_local_db_details()
            self.db_connection = DBOutputConnection()
            self.db_connection.open_connection(db_details_string)

            # Logic to make sure only 1 instance can run at a time
            for i in range(5):
                start_count, finish_count = self.db_connection.db_read("SELECT SUM(CASE WHEN message = 'Monitor Service: INFO: Starting run' THEN 1 ELSE 0 END) AS starting_run_count,  SUM(CASE WHEN message = 'Monitor Service: INFO: Ending run successfully' THEN 1 ELSE 0 END) AS ending_run_count FROM bf.log_file;")[0]

                if start_count != finish_count:
                    Log.log_warning("Monitor Service: Appears to be already running. Will retry every 60 seconds for 5 minutes")
                    time.sleep(60)
                else:
                    break

                if i == 4:
                    raise MonitorServiceException("Monitor Service: Failed to acquire lock")

            self.db_connection.db_write_log("Monitor Service: INFO: Starting run")
            self.authenticate_and_get_token()

            for i in range(15* 60):

                if reload_from_db:
                    # Get all of our raw target data from the database
                    raw_targets = self.get_targets()

                    # Process the targets into data we can work with easily
                    targets = self.process_targets(raw_targets)

                    # Update the status of targets (for example, CLOSE any markets that have closed!)
                    self.update_target_status(targets)

                reload_from_db = False

                # Start the timer
                # start_time = time.time()

                # Filter only for targets that need to be updated
                filtered_targets, nearest_update_seconds = self.get_filtered_targets(targets)

                Log.log_info(f"##############    Filtered Targets Count : {len(filtered_targets)}. Nearest Update Time: {nearest_update_seconds}")

                if len(filtered_targets) == 0:
                    Log.log_info("##############    No targets to update")
                    break
                else:
                    Log.log_info("##############    Updating Targets", force_console_log=True)
                    # Updating odds for targets
                    self.update_runner_odds(filtered_targets)
                    reload_from_db = True

                if nearest_update_seconds > 900:
                    Log.log_info("##############    Next update time not within 15 minutes", force_console_log=True)
                    break

                # # Wait for the remaining time (to 1 second)
                # elapsed_time = time.time() - start_time
                # remaining_time = max(0.0, 1.0 - elapsed_time)
                time.sleep(max(0.1, nearest_update_seconds - 1))

            self.db_connection.db_write_log("Monitor Service: INFO: Ending run successfully")
            Log.log_info("Monitor Service: INFO: Ending run successfully", force_console_log=True)

        except Exception as e:
            Log.log_error(f"Failed to update targets: {e}")
            raise MonitorServiceException(f"Failed to update targets: {e}")

if __name__ == "__main__":
    service = MonitorService(log_level=Log.INFO)

    # Call your service.run() function
    service.run()


