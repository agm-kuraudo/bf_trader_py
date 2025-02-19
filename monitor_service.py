import api.auth.auth_details as bf_auth
from BFDriver import BFDriver, BFDriverException
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log

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
targets = db_connection.db_read("SELECT target_id, event_id, market_id, start_time, status FROM bf.target WHERE status = 'IDENTIFIED';")

#'STEP 2: For each of these targets call "listMarketBook" and retrieve the status and the runners'

#STEP 3: Update Target status in Postgres - Active or Closed based on response above


#STEP 4: Get all targets that are in status of ACTIVE
#STEP 5: IF NOT EXISTS - Add a new reference for this market and each runner
#STEP 6. Get the current odds for each runner'
#STEP 7 - Add the odds to the database table