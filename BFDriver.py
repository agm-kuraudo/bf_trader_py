"""
test.py is used to "Power" the betfair trading application.  It may be replaced in future.  BFDriver class is also
defined in this file and it does the heavy lifting of creating and storing the various objects. Not sure why I did
it this way but its not worth changing at the moment. The methods in BFDriver are not really discrete methods as they
have to run in order to work as expected.
"""

from datetime import datetime, timedelta

import api.auth.auth_details as bf_auth
from api.auth.vault.vault_reader import VaultReader as Vault
from api.call import Call
from api.http_methods import Methods
from api.request_body import RequestBody
from api.urls import Urls
from betfair.competitions import Competition
from betfair.event import Event
from betfair.eventType import EventType
from betfair.market import Market, Target
from betfair.position import Position
from logic.simpleStategy import DefaultStrategy, FromFileStrategy
from output import Output as Log

class BFDriverException(Exception):
    pass

class BFDriver:

    # Class requires a defined strategy and log level
    def __init__(self, my_strategy:DefaultStrategy, log_level:int):
        # When initialised the BFDriver class creates a lot of variables that can be used throughout all the methods
        # these act basically as Global variables for the program
        self.__targets_list = None
        self.__my_competitions = None
        self.__my_event_types = None
        self.__my_strategy = my_strategy
        Log.set_log_level(log_level)

        self.__vault_obj = Vault()
        self.__request_body_obj = RequestBody()
        self.__auth_obj = bf_auth.Auth()
        self.__call_obj = Call(self.__auth_obj)
        self.__market_obj = Market()
        self.__event_type_obj = EventType()
        self.__competition_obj = Competition()
        self.__event_obj = Event()
        self.__position_obj = Position()

    # get_token method is used to retrieve the SSO Token required to access the Betfair API.  THe last token
    # stored in the vault will be used initially for a test call, if It's still valid we will continue with it.
    # If its no longer valid (24 hour lifespan?) we will call the relevant authentication calls and get a new one
    def get_token(self):
        # myAuth was created when the BFDriver was instantiated. It is an Auth object based on class defined in
        # api/auth/auth_details.py. The get_credentials_from_vault method does not return anything, it directly enriches
        # the Auth object with information gathered from the vault
        self.__auth_obj.get_credentials_from_vault()

        # myCall is an instance of the Call class - api/call.py. Here we are updating a variable in that class that
        # holds the auth details with the new values picked up in last call
        self.__call_obj.auth = self.__auth_obj

        # Here we are calling the "getAccountFunds" api on betfair via the Call object and then passing the result
        # to the validate_betfair_token function. This will return a True/False result based on whether we got
        # a successful response.  We will be picking up the last token used from Vault which may well have timed out
        # depending on how long it's been since the script was run.
        token_valid = self.__auth_obj.validate_betfair_token(
            self.__call_obj.call(http_method=Methods.POST,
                                 url=Urls.JSON_RPC_ACCOUNT,
                                 request_body=self.__request_body_obj.
                                 get_template("getAccountFunds")
                                 )
        )

        # If the token isn't value then... Let's Authenticate again and get a fresh token
        if token_valid is not True:
            # call_auth function is a special request method that uses the certificate along with the request
            # The myRequestBody variable is a RequestBody (api/request_body.py) object, We are swapping the placeholder
            # values with the username and password (held in the Auth object).
            self.__auth_obj.security_token = self.__call_obj.call_auth(
                self.__request_body_obj.populate_template(
                    "CertAuth",
                    {"<USERID>": self.__auth_obj.bf_userid, "<PWD>": self.__auth_obj.bf_pwd}
                )
            )
            # Update the myCall object with the update Auth object (which will have the right token)
            self.__call_obj.auth = self.__auth_obj
            # Update the vault with the new SSO token so it can be referenced on the next run
            self.__vault_obj.update_secret(path="bf_token", key_value_dict={"bf_sso_token": self.__auth_obj.security_token})
        Log.log_debug("Token: {}".format(self.__auth_obj.security_token))
        return True

    # get_event_types method will return a list of all events that Betfair supports and will filter it out
    # against the event types specified in our strategies and return the correct IDs only for those in a list
    # Event types include things like "Soccer" and "Horse Racing"
    def get_event_types(self) -> list[EventType]:

        # This code calls the build_frame_from_json method in betfair/eventType.py and returns a dataframe and list of
        # Event type objects based on the JSON returned from the "listEventTypes" api call.
        # The dataframe element isn't really used much but have left it in case it's useful in the future.
        df, event_type_list = self.__event_type_obj.build_frame_from_json(
            self.__call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                 request_body=self.__request_body_obj.get_template("listEventTypes")))

        # Create a new list which is going to hold the events that match our filter
        selected_event_types = []

        # for each event type returned..
        for event_type in event_type_list:
            # If the event type is one of those mentioned in our Strategy...
            if event_type.name in self.__my_strategy.EVENTS:
                # Add to our selected events list
                selected_event_types.append(event_type)
                Log.log_info(event_type)

        # if we didn't get any matched events, raise a useful error message
        if len(selected_event_types) == 0:
            Log.log_error(
                "No event types found matching: {}. Possible options will be listed below".format(
                    self.__my_strategy.EVENTS))
            for event_type in event_type_list:
                Log.log_error(event_type)
            return 0
        # If we did get matches, save just the event type ids to a separate list variable we will use in subsequent
        # methods
        else:
            self.__my_event_types = []
            for eventType in selected_event_types:
                self.__my_event_types.append(eventType.id)

        # Return the selected events as a list of Event objects
        return selected_event_types

    # get_competition_ids method calls the listCompetitions api and returns all competitions associated with our
    # selected event ids. For example Soccer has the Premier League, Championship etc.  It then filters based
    # on competitions specified in our strategy and gets only the desired ids.
    def get_competition_ids(self) -> list[Competition]:

        # This calls the build_frame_from_json method on the competition object - betfair/competitions.py. It
        # returns a dataframe and a list of Competition objects
        df, my_comps = (self.__competition_obj.build_frame_from_json(
            self.__call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                 request_body=self.__request_body_obj.populate_template("listCompetitions",
                                                                                    {
                                                                                   "<list_of_event_ids>":
                                                                                       self.__my_event_types
                                                                               }
                                                                                    )
                                 )
        )
        )

        Log.log_debug(df.head())

        # selected comps is initialised as a blank list
        selected_comps = []

        # For each competition in our list...
        for ev in my_comps:
            # If the competition name matches ones in our strategy...
            if ev.name in self.__my_strategy.COMPETITIONS:
                # append it to our selected list
                selected_comps.append(ev)
                Log.log_info(ev)
        # If we didn't find any matching competitions output a useful error message
        if len(selected_comps) == 0:
            Log.log_error("No competitions found matching: {}. Possible options will be listed below".format(
                self.__my_strategy.COMPETITIONS))
            for ev in my_comps:
                Log.log_error(ev)
            return 0
        # else we save the competition ids only to a list
        else:
            self.__my_competitions = []
            for compType in selected_comps:
                self.__my_competitions.append(compType.id)
        # return the selected competitions as a list of competition objects
        return selected_comps

    # get_events method calls "listEvents" api call supplying the event ids and competition ids that have been
    # gleamed from the get_event_types and get_competition_id methods as well as the Market Type specified in the
    # strategy.
    def get_events(self) -> list[Event]:
        # build_frame_from_json called on betfair/event.py object which returns all possible events based on the
        # supplied filters
        df, my_event_list = self.__event_obj.build_frame_from_json(
            self.__call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                 request_body=self.__request_body_obj.populate_template(
                                 "listEvents",
                                 {
                                     "<list_of_event_ids>": self.__my_event_types,
                                     "<list_of_competition_ids>": self.__my_competitions,
                                    "<list_of_market_types>": self.__my_strategy.MARKET_TYPEs
                                 }
                             )
                                 )
        )

        Log.log_debug(df.info())
        # This return statement calls the "fiter_events" method also contained in this class remove events outside the
        # required timeline and those which already have an active position open. It also uses slicing to cut down the
        # events to the Maximum number supplied in the strategy
        return self.filter_events(my_event_list)[:self.__my_strategy.MAX_EVENTS]

    # filter_events method is supplied a full list of events that match the basic filter and returns a list of
    # events that are within the supplied timeline (MIN and MAX days until start) and do not already have a position
    # on them
    def filter_events(self, all_events: list[Event]) -> list[Event]:
        filtered_events = []
        # loop through all the events and include all that don't match the IF and ELIF statements
        for ev in all_events:
            # Log.log_info(event.open_date)
            if ev.id in self.__position_obj.position_events:
                Log.log_debug(f"Event already has a position taken {ev.id}")
            elif (ev.open_date - datetime.now()) < timedelta(days=self.__my_strategy.MIN_DAYS_TILL_START):
                Log.log_debug(f"Event to soon: {ev.open_date}")
            elif (ev.open_date - datetime.now()) > timedelta(days=self.__my_strategy.MAX_DAYS_TILL_START):
                Log.log_debug(f"Event to far away: {ev.open_date}")
            else:
                Log.log_info(f"Event {ev.name} in range: {ev.open_date}")
                filtered_events.append(ev)
        # return a sorted list either newest or oldest first - depending on what is set in the strategy
        return sorted(filtered_events, key=lambda item: item.open_date, reverse=not self.__my_strategy.NEWEST_FIRST)

    # get_target_markets calls the marketCatalogue api. The market is something that can be bet on - typically
    # MATCH_ODDs for example and will include the "Runners" e.g. Home Team Win, Draw etc. Based on the events
    # supplied we extract the market details and return them as a list of Target objects (betfair/market.py)
    def get_target_markets(self, my_events) -> list[Target]:
        # Here we create placeholder variables for lists of Markets and Targets.  Note that Target is basically a
        # wrapper class that contains an Event object and an Associated Market object. We already have the event,
        # this function extracts the Market details and enriches the Target object with it
        markets_list = []
        self.__targets_list = []

        # for each event supplied...
        for event in my_events:
            # Create a market object based on calling the "marketCatalogue" api with just the individual event id
            df, market = self.__market_obj.build_frame_from_json(
                self.__call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                     request_body=self.__request_body_obj.populate_template(
                                     "marketCatalogue",
                                     {"<list_of_event_ids>": [event.id],
                                      "<list_of_market_types>": self.__my_strategy.MARKET_TYPEs})))
            # add the market extracted to the list of market ids
            markets_list.append(market[0])
            # append a new Target object to the list with the relevant event and Market.
            self.__targets_list.append(Target(event, market[0]))

            Log.log_debug(datetime.strptime(self.__market_obj.description['marketTime'], '%Y-%m-%dT%H:%M:%S.%fZ') - datetime.now())
            # The market object contains "runners" e.g. Spurs, Draw, Arsenal, just outputting them to log here
            for runner in self.__market_obj.runners:
                Log.log_debug(runner)

        # Return the complete list of Targets
        return self.__targets_list

    # update_odds_for_targets calls "listMarketBook" to update the odds for all the targets supplied in the
    # target_list. It updates the values directly in the object so it doesn't directly return anything.
    def update_odds_for_targets(self, target_list) -> None:
        for target in target_list:
            Log.log_debug("Looking up odds for {}".format(target.my_market.id))
            Log.log_debug("Runners odds {}".format(target.my_market.runners))
            json_resp = self.__call_obj.call(http_method=Methods.POST, url=Urls.JSON_RPC_BET,
                                             request_body=self.__request_body_obj.populate_template(
                                             "listMarketBook",
                                             {
                                             "<ListOfMarketIDs>": [target.my_market.id]
                                             }
                                         )
                                             )
            Log.log_debug(json_resp)
            # the updated odds will be returned in the runners section
            runner_list = json_resp.json()["result"][0]["runners"]
            Log.log_debug(len(target.my_market.runners))
            # Update each running in our object with the right odds.  Writing these comments
            # way after the code and I am not 100% sure here! need to refresh
            for runners in target.my_market.runners:
                runners.odds = runner_list


#Create a new BFDriver class, supplying the strategy and the log level.

# BF = BFDriver(DefaultStrategy(), Log.INFO)
# BF = BFDriver(DefaultStrategy(), Log.DEBUG)
BF = BFDriver(FromFileStrategy(), Log.DEBUG)

# Below added just to test the position work for SP-72
# BF.myPosition.position_events = '32866443'

# Step 1: Authenticate and get a Session Token!
if not BF.get_token():
    raise bf_auth.AuthException("Failed to authenticate to vault.  Validate that it is running (and unsealed) on "
                                "port 8200.  See error message above for exception details")

Log.log_info("##############    Step 1 Complete")

# Step 2: Extract the update to date for Event ID(s) for selected events
myEventTypes = BF.get_event_types()
if myEventTypes == 0:
    raise BFDriverException("Failed at step 2 - no event types")

Log.log_info("##############    Step 2 Complete")

# Step 3: Extract the competition IDs for my selected competitions
myComps = BF.get_competition_ids()
if myComps == 0:
    raise BFDriverException("Failed at step 3 - no Competitions")

Log.log_info("##############    Step 3 Complete")

# Step 4 : Extract the events matching our competition and event types
myEvents = BF.get_events()
if myEvents == 0:
    raise BFDriverException("Failed at step 4 - no events")

Log.log_info("##############    Step 4 Complete")

Log.log_info("There are {} events available".format(len(myEvents)))

for event in myEvents:
    Log.log_info(f"Event: {event.name}")

# Step 5: Get the correct markets associated with these events.

myTargets = BF.get_target_markets(myEvents)
if len(myTargets) == 0:
    raise BFDriverException("Failed at step 5 - no targets")
Log.log_info("{} Targets identified".format(len(myTargets)))
Log.log_debug(myTargets[0].my_market.runners)

Log.log_info("##############  Step 5 Complete")

# Get the updated odds for these targets

BF.update_odds_for_targets(myTargets)
