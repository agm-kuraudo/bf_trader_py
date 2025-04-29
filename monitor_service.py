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

class MonitorServiceException(Exception):
    pass

class MonitorService:
    def __init__(self, log_level=Log.INFO, strategy=FromFileStrategy()):
        self.BF = BFDriver(strategy, log_level)
        self.db_connection = None

    def authenticate_and_get_token(self):
        try:
            if not self.BF.get_token():
                self.db_connection.db_write_log("Failed to retrieve token")
                raise bf_auth.AuthException("Failed to authenticate to vault. Validate that it is running (and unsealed) on port 8200.")
            self.db_connection.db_write_log("Token retrieved")
            Log.log_info("##############    Login Token Retrieved")
        except Exception as e:
            raise MonitorServiceException(f"Authentication failed: {e}")

    def get_targets(self):
        try:
            raw_targets = self.db_connection.db_read("SELECT target_id, event_id, market_id, runner_ids, start_time, status, update_frequency, last_updated, notes FROM bf.target WHERE status in ('IDENTIFIED', 'OPEN');")
            Log.log_info(f"##############    Step 1 Complete - {len(raw_targets)} targets in IDENTIFIED")
            return raw_targets
        except Exception as e:
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
                Log.log_info(f"Market: {market}, Status: {status}")
                runner_list = json["result"][0]["runners"]
                Log.log_info(f"Market {market}, Runners: {len(runner_list)}")
                runners = [runner["selectionId"] for runner in runner_list]
                targets.append((market, status, len(runner_list), runners, target[6], target[7]))
            Log.log_info("##############    Step 2 Complete")
            Log.log_debug(targets)
            return targets
        except Exception as e:
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
                    raise MonitorServiceException(f"Unknown market state: {target}")
        except Exception as e:
            raise MonitorServiceException(f"Failed to update target status: {e}")

    def get_open_targets(self):
        try:
            open_targets = self.db_connection.db_read("SELECT target_id, event_id, market_id, runner_ids, start_time, status, notes FROM bf.target WHERE status = 'OPEN';")
            if len(open_targets) == 0:
                Log.log_warning("No open targets found")
            else:
                Log.log_info("Active Targets: {}".format(open_targets))
            return open_targets
        except Exception as e:
            raise MonitorServiceException(f"Failed to get open targets: {e}")

    def get_filtered_targets(self, open_targets):
        try:
            targets_to_update = []
            print("##############    Step 3 get_filtered_targets")
            for target in open_targets:
                print(target)

                # Retrieve the current date and time
                current_time = datetime.now(timezone.utc)
                Log.log_debug(f"Current date and time: {current_time}")

                minutes_until_next_update = target[4]
                Log.log_debug(f"Minutes until next update: {minutes_until_next_update}")

                # Assuming target[5] contains the last updated time as a datetime object
                last_update_time = target[5]
                Log.log_debug(f"Last update time: {last_update_time}")

                # Calculate the next update time
                next_update_time = last_update_time + timedelta(minutes=minutes_until_next_update)
                Log.log_debug(f"Next update time: {next_update_time}")

                # Calculate the number of minutes until the next update is required
                time_until_next_update = next_update_time - current_time
                seconds_until_next_update_required = time_until_next_update.total_seconds() / 60
                Log.log_debug(f"Seconds until next update is required: {seconds_until_next_update_required}")

                if target[1] == 'OPEN' and seconds_until_next_update_required < 0:
                    targets_to_update.append(target)

            return targets_to_update
        except Exception as e:
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
                    Log.log_info(f"Market: {target[2]}, Runner: {individual_runner}, Status: {status}")
                    odds = json_resp["result"][0]["runners"][0]["ex"]
                    Log.log_info(f"odds back: {odds}")
                    odds_str = json.dumps(odds) if type(odds) != dict else str(odds)
                    sql_command = ("INSERT INTO bf.market_table(\"timestamp\", market_id, runner_id, odds) VALUES (current_timestamp, %s, %s, %s);")
                    success = self.db_connection.db_write(sql_command, (target[0], individual_runner, odds_str))
                    Log.log_debug(f"Updating odds for {target[0]} runner {individual_runner} status: {success}")
                #Updating the last updated time for that target
                sql_command = (f"UPDATE bf.target SET last_updated=NOW() WHERE market_id=%s;")
                success = self.db_connection.db_write(sql_command, (target[0],))
                Log.log_debug(f"Updating last updated time for {target[0]} status: {success}")

        except Exception as e:
            raise MonitorServiceException(f"Failed to update runner odds: {e}")

    def run(self):
        try:
            db_details_string = self.BF.get_local_db_details()
            self.db_connection = DBOutputConnection()
            self.db_connection.open_connection(db_details_string)
            self.db_connection.db_write_log("Monitor Service: Starting run")
            self.authenticate_and_get_token()

            # Get all of our raw target data from the database
            raw_targets = self.get_targets()

            # Process the targets into data we can work with easily
            targets = self.process_targets(raw_targets)

            # Update the status of targets (for example CLOSE any markets that have closed!)
            self.update_target_status(targets)

            # Filter only for targets that need to be updated

            filtered_targets = self.get_filtered_targets(targets)

            Log.log_info(f"##############    Filtered Targets Count : {len(filtered_targets)}")

            if len(filtered_targets) == 0:
                Log.log_info("##############    No targets to update")
            else:
                Log.log_info("##############    Updating Targets")
                # Updating odds for targets
                self.update_runner_odds(filtered_targets)
        except Exception as e:
            Log.log_error(f"Failed to update targets: {e}")
            raise MonitorServiceException(f"Failed to update targets: {e}")

# Example usage
if __name__ == "__main__":
    service = MonitorService(log_level=Log.DEBUG)
    service.run()