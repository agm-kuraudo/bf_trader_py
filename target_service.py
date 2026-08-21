import api.auth.auth_details as bf_auth
from BFDriver import BFDriver, BFDriverException
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output.dboutput import DBOutputConnection
from output.log import Output as Log

# Create a new BFDriver class, supplying the strategy and the log level.

# BF = BFDriver(DefaultStrategy(), Log.INFO)
# BF = BFDriver(DefaultStrategy(), Log.DEBUG)
BF = BFDriver(FromFileStrategy(), Log.INFO)

# Below added just to test the position work for SP-72
# BF.myPosition.position_events = '32866443'

# Pre-Steps 1: Get Local DB Connection Details

global db_connection

try:
    db_details_string = BF.get_local_db_details()
    db_connection = DBOutputConnection()

    db_connection.open_connection(db_details_string)
    db_connection.db_write_log("Target Service: INFO : Starting run")

except Exception as e:
    raise BFDriverException(f"Failed to get local DB details: {e}") from e

# Step 1: Authenticate and get a Session Token!
if not BF.get_token():
    db_connection.db_write_log("Target Service: ERROR : Ending Run : Failed to authenticate to Betfair API")
    raise bf_auth.AuthException(
        "Failed to authenticate to Betfair API. Check credentials in .env file "
        "(BF_USERID, BF_PWD, BF_CRT_FILE, BF_KEY_FILE)."
    )

# db_connection.db_write_log("Token retrieved")
Log.log_info("##############    Step 1 Complete", force_console_log=True)

# Step 2: Extract the update to date for Event ID(s) for selected events
myEventTypes = BF.get_event_types()
if myEventTypes == 0:
    db_connection.db_write_log("Target Service: ERROR : Ending Run : Failed at step 2 - no event types")
    raise BFDriverException("Failed at step 2 - no event types")

for event in myEventTypes:
    db_connection.db_write_object_id(object_type="event-type", object_name=event.name, object_id=event.id)

# db_connection.db_write_log("Event Types: {}".format(myEventTypes))
Log.log_info("##############    Step 2 Complete", force_console_log=True)

# Step 3: Extract the competition IDs for my selected competitions
myComps = BF.get_competition_ids()
if myComps == 0:
    db_connection.db_write_log("Target Service: ERROR : Ending Run : Failed at step 3 - no Competitions")
    raise BFDriverException("Failed at step 3 - no Competitions")

for comp in myComps:
    db_connection.db_write_object_id(object_type="competition", object_name=comp.name, object_id=comp.id)

# db_connection.db_write_log("Competitions: {}".format(myComps))
Log.log_info("##############    Step 3 Complete", force_console_log=True)

# Step 4 : Extract the events matching our competition and event types
myEvents = BF.get_events()
if myEvents == 0:
    db_connection.db_write_log("Failed at step 4 - no events")
    raise BFDriverException("Failed at step 4 - no events")

# db_connection.db_write_log("myEvents: {}".format(myEvents))
Log.log_info("##############    Step 4 Complete", force_console_log=True)

Log.log_info(f"There are {len(myEvents)} events available")

for event in myEvents:
    Log.log_info(f"Event: {event.name}")

# Step 5: Get the correct markets associated with these events.

myTargets = BF.get_target_markets(myEvents)
if len(myTargets) == 0:
    db_connection.db_write_log("Target Service: ERROR : Ending Run : Failed at step 5 - no targets")
    raise BFDriverException("Failed at step 5 - no targets")
Log.log_info(f"{len(myTargets)} Targets identified")
Log.log_debug(myTargets[0].my_market.runners)

Log.log_info("##############    Step 5 Complete", force_console_log=True)

for target in myTargets:
    runner_string = ""
    for runner in target.my_market.runners:
        runner_string += str(runner.id) + "-" + runner.name + "|"

    runner_string = runner_string[:-1]

    Log.log_debug(
        "Event ID {}, Name {}.  Market ID {}, Name {}. RunnerIDs: {}. Time: {}".format(
            target.my_event.id,
            target.my_event.name,
            target.my_market.id,
            target.my_market.name,
            runner_string,
            target.my_market.description["marketTime"],
        )
    )

    db_connection.db_write_target(
        target_id=target.target_id,
        event_id=target.my_event.id,
        market_id=target.my_market.id,
        runner_ids=runner_string,
        start_time=target.my_market.description["marketTime"],
        status="IDENTIFIED",
        update_frequency=DefaultStrategy.INITIAL_UPDATE_FREQUENCY,
        notes=str(target.my_market.description),
    )

db_connection.db_write_log(f"Target Service: INFO : Ending run : {len(myTargets)} targets identified")
Log.log_info(f"Target Service: INFO : Ending run : {len(myTargets)} targets identified", force_console_log=True)
