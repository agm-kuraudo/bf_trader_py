import traceback
import api.auth.auth_details as bf_auth
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody
from betfair.competitions import Competition
from output import Output as Log
from api.http_methods import Methods
from betfair.eventType import EventType
from betfair.event import Event
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from datetime import datetime, timedelta
from betfair.market import Market, Target


class BFDriver:

    def __init__(self, my_strategy, log_level):

        self.TargetsList = None
        self.myCompIds = None
        self.myEventTypeIds = None
        self.my_strategy = my_strategy
        Log.set_log_level(log_level)

        self.myRequestBody = RequestBody()
        self.myAuth = bf_auth.Auth()
        self.myCall = Call(self.myAuth)
        self.myMarket = Market()
        self.myEventTypes = EventType()
        self.myCompetitions = Competition()
        self.myEvent = Event()

    def authenticate_to_betfair(self) -> bool:
        try:
            self.myAuth.get_credentials_from_vault()
            self.myAuth.security_token = self.myCall.call_auth(self.myRequestBody.populate_template("CertAuth", {
                "<USERID>": self.myAuth.bf_userid, "<PWD>": self.myAuth.bf_pwd}))
            Log.log_info("Token: {}".format(self.myAuth.security_token))
            return True
        except bf_auth.AuthException as g:
            Log.log_error(g.__cause__)
            return False

    def get_event_types(self) -> list[EventType]:
        df, event_type_list = self.myEventTypes.build_frame_from_json(
            self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC,
                             request_body=self.myRequestBody.get_template("listEventTypes")))
        selected_event_types = []
        for event_type in event_type_list:
            if event_type.name in self.my_strategy.EVENTS:
                selected_event_types.append(event_type)
                Log.log_info(event_type)
        if len(selected_event_types) == 0:
            Log.log_error(
                "No event types found matching: {}. Possible options will be listed below".format(
                    self.my_strategy.EVENTS))
            for event_type in event_type_list:
                Log.log_error(event_type)
            return 0
        else:
            self.myEventTypeIds = []
            for eventType in selected_event_types:
                self.myEventTypeIds.append(eventType.id)

            return selected_event_types

    def get_competition_ids(self) -> list[Competition]:
        df, my_comps = self.myCompetitions.build_frame_from_json(
            self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC,
                             request_body=self.myRequestBody.populate_template("listCompetitions", {
                                 "<list_of_event_ids>": self.myEventTypeIds})))
        Log.log_debug(df.head())

        selected_comps = []
        for ev in my_comps:
            if ev.name in self.my_strategy.COMPETITIONS:
                selected_comps.append(ev)
                Log.log_info(ev)
        if len(selected_comps) == 0:
            Log.log_error("No competitions found matching: {}. Possible options will be listed below".format(
                self.my_strategy.COMPETITIONS))
            for ev in my_comps:
                Log.log_error(ev)
            return 0
        else:
            self.myCompIds = []
            for compType in selected_comps:
                self.myCompIds.append(compType.id)
            return selected_comps

    def get_events(self) -> list[Event]:
        df, my_event_list = self.myEvent.build_frame_from_json(
            self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC,
                             request_body=self.myRequestBody.populate_template(
                                 "listEvents",
                                 {"<list_of_event_ids>": self.myEventTypeIds,
                                  "<list_of_competition_ids>": self.myCompIds,
                                  "<list_of_market_types>": self.my_strategy.MARKET_TYPEs})))

        Log.log_debug(df.info())

        return self.filter_events(my_event_list)[:self.my_strategy.MAX_EVENTS]

    def filter_events(self, all_events: list[Event]) -> list[Event]:
        filtered_events = []
        for ev in all_events:
            # Log.log_info(event.open_date)
            if (ev.open_date - datetime.now()) < timedelta(days=self.my_strategy.MIN_DAYS_TILL_START):
                Log.log_debug(f"Event to soon: {ev.open_date}")
            elif(ev.open_date - datetime.now()) > timedelta(days=self.my_strategy.MAX_DAYS_TILL_START):
                Log.log_debug(f"Event to far away: {ev.open_date}")
            else:
                Log.log_info(f"Event {ev.name} in range: {ev.open_date}")
                filtered_events.append(ev)
        return sorted(filtered_events, key=lambda item: item.open_date, reverse=not self.my_strategy.NEWEST_FIRST)

    def get_target_markets(self, my_events) -> list[Target]:
        markets_list = []
        self.TargetsList = []

        for event in my_events:
            df, market = self.myMarket.build_frame_from_json(
                self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC,
                                 request_body=self.myRequestBody.populate_template(
                                     "marketCatalogue",
                                     {"<list_of_event_ids>": [event.id],
                                      "<list_of_market_types>": self.my_strategy.MARKET_TYPEs})))

            markets_list.append(market[0])
            self.TargetsList.append(Target(event, market[0]))

            Log.log_debug(self.myMarket.description['marketTime'] - datetime.now())

            for runner in self.myMarket.runners:
                Log.log_debug(runner)

        return self.TargetsList

    def update_odds_for_targets(self, target_list) -> None:
        for target in target_list:
            Log.log_debug("Looking up odds for {}".format(target.myMarkets.id))
            Log.log_debug("Runners odds {}".format(target.myMarkets.runners))
            json_resp = self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC,
                                         request_body=self.myRequestBody.populate_template("listMarketBook", {
                                             "<ListOfMarketIDs>": [target.myMarkets.id]}))
            Log.log_debug(json_resp)
            runner_list = json_resp.json()["result"][0]["runners"]
            Log.log_debug(len(target.myMarkets.runners))
            for runners in target.myMarkets.runners:
                runners.odds = runner_list


# BF = BFDriver(DefaultStrategy(), Log.INFO)
# BF = BFDriver(DefaultStrategy(), Log.DEBUG)
BF = BFDriver(FromFileStrategy(), Log.INFO)

# Step 1: Authenticate and get a Session Token!
if not BF.authenticate_to_betfair():
    raise bf_auth.AuthException("Failed to authenticate to vault.  Validate that it is running (and unsealed) on port "
                                "8200.  See error message above for exception details")

Log.log_info("##############    Step 1 Complete")

# Step 2: Extract the update to date for Event ID(s) for selected events
myEventTypes = BF.get_event_types()
if myEventTypes == 0:
    exit(1)

Log.log_info("##############    Step 2 Complete")

# Step 3: Extract the competition IDs for my selected competitions
myComps = BF.get_competition_ids()
if myComps == 0:
    exit(1)

Log.log_info("##############    Step 3 Complete")

# Step 4 : Extract the events matching our competition and event types
myEvents = BF.get_events()
if myEvents == 0:
    exit(1)

Log.log_info("##############    Step 4 Complete")

Log.log_info("There are {} events available".format(len(myEvents)))

for event in myEvents:
    Log.log_info(f"Event: {event.name}")

myTargets = BF.get_target_markets(myEvents)
if len(myTargets) == 0:
    exit(1)
Log.log_info("{} Targets identified".format(len(myTargets)))
Log.log_debug(myTargets[0].myMarkets.runners)

Log.log_info("##############  Step 5 Complete")

BF.update_odds_for_targets(myTargets)
