from datetime import datetime, timedelta

import api.auth.auth_details as bf_auth
from BFDriver import BFDriver, BFDriverException
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log

# Create a new BFDriver class, supplying the strategy and the log level.

# BF = BFDriver(DefaultStrategy(), Log.INFO)
# BF = BFDriver(DefaultStrategy(), Log.DEBUG)
BF = BFDriver(FromFileStrategy(), Log.DEBUG)

# Below added just to test the position work for SP-72
# BF.myPosition.position_events = '32866443'

# Pre-Steps 1: Get Local DB Connection Details

global db_connection

try:
    db_details_string = BF.get_local_db_details()
    db_connection = DBOutputConnection()

    db_connection.open_connection(db_details_string)
    db_connection.db_write_log("Target Service: Starting run")

except Exception as e:
    raise BFDriverException("Failed to get local DB details: {}".format(e))

# Step 1: Authenticate and get a Session Token!
if not BF.get_token():
    db_connection.db_write_log("Failed to retrieve token")
    raise bf_auth.AuthException("Failed to authenticate to vault.  Validate that it is running (and unsealed) on "
                                "port 8200.  See error message above for exception details")

db_connection.db_write_log("Token retrieved")
Log.log_info("##############    Step 1 Complete")

# Step 2: Extract the update to date for Event ID(s) for selected events
myEventTypes = BF.get_event_types()
if myEventTypes == 0:
    db_connection.db_write_log("Failed at step 2 - no event types")
    raise BFDriverException("Failed at step 2 - no event types")

print("Event Types: {}".format(myEventTypes))

for event in myEventTypes:
    db_connection.db_write_object_id(object_type="event-type", object_name=event.name, object_id=event.id)

db_connection.db_write_log("Event Types: {}".format(myEventTypes))
Log.log_info("##############    Step 2 Complete")

# Step 3: Extract the competition IDs for my selected competitions
myComps = BF.get_competition_ids()
if myComps == 0:
    db_connection.db_write_log("Failed at step 3 - no Competitions")
    raise BFDriverException("Failed at step 3 - no Competitions")

for comp in myComps:
    db_connection.db_write_object_id(object_type="competition", object_name=comp.name, object_id=comp.id)

db_connection.db_write_log("Competitions: {}".format(myComps))
Log.log_info("##############    Step 3 Complete")

# Step 4 : Extract the events matching our competition and event types
myEvents = BF.get_events()
if myEvents == 0:
    db_connection.db_write_log("Failed at step 4 - no events")
    raise BFDriverException("Failed at step 4 - no events")

db_connection.db_write_log("myEvents: {}".format(myEvents))
Log.log_info("##############    Step 4 Complete")

Log.log_info("There are {} events available".format(len(myEvents)))

for event in myEvents:
    Log.log_info(f"Event: {event.name}")

# Step 5: Get the correct markets associated with these events.

myTargets = BF.get_target_markets(myEvents)
if len(myTargets) == 0:
    db_connection.db_write_log("Failed at step 5 - no targets")
    raise BFDriverException("Failed at step 5 - no targets")
Log.log_info("{} Targets identified".format(len(myTargets)))
Log.log_debug(myTargets[0].my_market.runners)

Log.log_info("##############  Step 5 Complete")

for target in myTargets:
    Log.log_debug("Event ID {}, Name {}.  Market ID {}, Name {}. Time: {}".format(target.my_event.id,
                                                                                  target.my_event.name,
                                                                                  target.my_market.id,
                                                                                  target.my_market.name,
                                                                                  target.my_market.description[
                                                                                      'marketTime']))
    db_connection.db_write_target(target.target_id, target.my_event.id, target.my_market.id,
                                  target.my_market.description['marketTime'],
                                  'IDENTIFIED')