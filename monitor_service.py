import json
import api.auth.auth_details as bf_auth
from BFDriver import BFDriver, BFDriverException
from api.http_methods import Methods
from api.urls import Urls
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log

class MonitorServiceException(Exception):
    pass

class MonitorService:
    def __init__(self):
        self.BF = BFDriver(FromFileStrategy(), Log.DEBUG)
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
            raw_targets = self.db_connection.db_read("SELECT target_id, event_id, market_id, start_time, status FROM bf.target WHERE status in ('IDENTIFIED', 'OPEN');")
            Log.log_info(f"##############    Step 1 Complete - {len(raw_targets)} targets in IDENTIFIED")
            return raw_targets
        except Exception as e:
            raise MonitorServiceException(f"Failed to get targets: {e}")

    def update_odds_for_targets(self, raw_targets):
        try:
            targets = []
            for target in raw_targets:
                market = target[2]
                Log.log_debug("Looking up odds for {}".format(market))
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
                targets.append((market, status, len(runner_list), runners))
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

    def update_runner_odds(self, open_targets):
        try:
            for target in open_targets:
                Log.log_debug(f"Looking up odds for target {target}")
                runner_details = target[3].split("|")
                for individual_runner in runner_details:
                    selection_id = individual_runner.split("-")[0]
                    Log.log_debug(f"Looking up odds {target[2]} for selection id {selection_id}")
                    resp = self.BF.call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                                 request_body=self.BF.request_body_obj.populate_template(
                                                     "listRunnerBook",
                                                     {"<MarketID>": str(target[2]), "<RunnerID>": str(selection_id)}
                                                 ))
                    Log.log_debug(resp)
                    json_resp = resp.json()
                    status = json_resp["result"][0]["status"]
                    Log.log_info(f"Market: {target[2]}, Runner: {selection_id}, Status: {status}")
                    odds = json_resp["result"][0]["runners"][0]["ex"]
                    Log.log_info(f"odds back: {odds}")
                    odds_str = json.dumps(odds) if type(odds) != dict else str(odds)
                    sql_command = ("INSERT INTO bf.market_table(\"timestamp\", market_id, runner_id, odds) VALUES (current_timestamp, %s, %s, %s);")
                    success = self.db_connection.db_write(sql_command, (target[2], selection_id, odds_str))
                    Log.log_debug(f"Setting {target[0]} as {target[1]} status: {success}")
        except Exception as e:
            raise MonitorServiceException(f"Failed to update runner odds: {e}")

    def run(self):
        try:
            db_details_string = self.BF.get_local_db_details()
            self.db_connection = DBOutputConnection()
            self.db_connection.open_connection(db_details_string)
            self.db_connection.db_write_log("Monitor Service: Starting run")
            self.authenticate_and_get_token()
            raw_targets = self.get_targets()
            targets = self.update_odds_for_targets(raw_targets)
            self.update_target_status(targets)
            open_targets = self.get_open_targets()
            self.update_runner_odds(open_targets)
        except Exception as e:
            Log.log_error(f"Failed to update targets: {e}")
            raise MonitorServiceException(f"Failed to update targets: {e}")

# Example usage
if __name__ == "__main__":
    service = MonitorService()
    service.run()