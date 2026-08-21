"""
test.py is used to "Power" the betfair trading application.  It may be replaced in future.  BFDriver class is also
defined in this file and it does the heavy lifting of creating and storing the various objects. Not sure why I did
it this way but its not worth changing at the moment. The methods in BFDriver are not really discrete methods as they
have to run in order to work as expected.
"""

from datetime import datetime, timedelta

import api.auth.auth_details as bf_auth
from api.auth.dotenv_loader import ConfigurationException, DotenvLoader
from api.call import Call
from api.http_methods import Methods
from api.request_body import RequestBody
from api.urls import Urls
from betfair.competitions import Competition
from betfair.event import Event
from betfair.eventType import EventType
from betfair.market import Market, Target
from betfair.position import Position
from logic.simpleStategy import DefaultStrategy
from output.log import Output as Log


class BFDriverException(Exception):
    pass


class BFDriver:
    # Class requires a defined strategy and log level
    def __init__(self, my_strategy: DefaultStrategy, log_level: int):
        # When initialised the BFDriver class creates a lot of variables that can be used throughout all the methods
        # these act basically as Global variables for the program
        self.__targets_list = None
        self.__my_competitions = None
        self.__my_event_types = None
        self.__my_strategy = my_strategy
        Log.set_log_level(log_level)

        self.__loader = DotenvLoader()
        self.__request_body_obj = RequestBody()
        self.__auth_obj = bf_auth.Auth(self.__loader)
        self.__call_obj = Call(self.__auth_obj)
        self.__market_obj = Market()
        self.__event_type_obj = EventType()
        self.__competition_obj = Competition()
        self.__event_obj = Event()
        self.__position_obj = Position()

    def get_local_db_details(self):
        try:
            host = self.__loader.get_secret("DB_HOST")
            port = self.__loader.get_secret("DB_PORT")
            db_name = self.__loader.get_secret("DB_NAME")
            db_user = self.__loader.get_secret("DB_USER")
            db_pwd = self.__loader.get_secret("DB_PWD")
        except ConfigurationException as f:
            raise BFDriverException(f"Could not load DB credentials from .env: {f}") from f
        return {"host": host, "port": port, "db_name": db_name, "db_user": db_user, "db_pwd": db_pwd}

    # get_token method obtains a fresh SSO Token from the Betfair API via certificate authentication.
    # A fresh token is always obtained on each run — no cached token is used.
    def get_token(self):
        self.__auth_obj.get_credentials()
        self.__call_obj.auth = self.__auth_obj
        self.__auth_obj.security_token = self.__call_obj.call_auth(
            self.__request_body_obj.populate_template(
                "CertAuth",
                {"<USERID>": self.__auth_obj.bf_userid, "<PWD>": self.__auth_obj.bf_pwd},
                add_quotes=True,
            )
        )
        self.__call_obj.auth = self.__auth_obj
        Log.log_debug(f"Token: {self.__auth_obj.security_token}")
        return True

    # get_event_types method will return a list of all events that Betfair supports and will filter it out
    # against the event types specified in our strategies and return the correct IDs only for those in a list
    # Event types include things like "Soccer" and "Horse Racing"
    def get_event_types(self) -> list[EventType]:
        # This code calls the build_frame_from_json method in betfair/eventType.py and returns a dataframe and list of
        # Event type objects based on the JSON returned from the "listEventTypes" api call.
        # The dataframe element isn't really used much but have left it in case it's useful in the future.
        df, event_type_list = self.__event_type_obj.build_frame_from_json(
            self.__call_obj.call(
                http_method=Methods.POST,
                url=Urls.JSON_RPC_BET,
                request_body=self.__request_body_obj.get_template("listEventTypes"),
            )
        )

        # Create a new list which is going to hold the events that match our filter
        selected_event_types = []

        # for each event type returned..
        for event_type in event_type_list:
            # If the event type is one of those mentioned in our Strategy...
            if event_type.name in self.__my_strategy.EVENTS:
                # Add to our selected events list
                selected_event_types.append(event_type)
                Log.log_debug(event_type)

        # if we didn't get any matched events, raise a useful error message
        if len(selected_event_types) == 0:
            Log.log_error(
                f"No event types found matching: {self.__my_strategy.EVENTS}. Possible options will be listed below"
            )
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
        df, my_comps = self.__competition_obj.build_frame_from_json(
            self.__call_obj.call(
                http_method=Methods.POST,
                url=Urls.JSON_RPC_BET,
                request_body=self.__request_body_obj.populate_template(
                    "listCompetitions", {"<list_of_event_ids>": self.__my_event_types}
                ),
            )
        )

        # Log.log_debug("" + df.head())

        # selected comps is initialised as a blank list
        selected_comps = []

        # For each competition in our list...
        for ev in my_comps:
            # If the competition name matches ones in our strategy...
            if ev.name in self.__my_strategy.COMPETITIONS:
                # append it to our selected list
                selected_comps.append(ev)
                Log.log_debug(f"Event: {ev}")
        # If we didn't find any matching competitions output a useful error message
        if len(selected_comps) == 0:
            Log.log_error(
                f"No competitions found matching: {self.__my_strategy.COMPETITIONS}. Possible options will be listed below"  # noqa: E501
            )
            for ev in my_comps:
                Log.log_debug(f"Event: {ev}")
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
            self.__call_obj.call(
                http_method=Methods.POST,
                url=Urls.JSON_RPC_BET,
                request_body=self.__request_body_obj.populate_template(
                    "listEvents",
                    {
                        "<list_of_event_ids>": self.__my_event_types,
                        "<list_of_competition_ids>": self.__my_competitions,
                        "<list_of_market_types>": self.__my_strategy.MARKET_TYPEs,
                    },
                ),
            )
        )

        # Log.log_debug(df.info())
        # This return statement calls the "fiter_events" method also contained in this class remove events outside the
        # required timeline and those which already have an active position open. It also uses slicing to cut down the
        # events to the Maximum number supplied in the strategy
        return self.filter_events(my_event_list)[: self.__my_strategy.MAX_EVENTS]

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
                Log.log_debug(f"Event {ev.name} in range: {ev.open_date}")
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
                self.__call_obj.call(
                    http_method=Methods.POST,
                    url=Urls.JSON_RPC_BET,
                    request_body=self.__request_body_obj.populate_template(
                        "marketCatalogue",
                        {"<list_of_event_ids>": [event.id], "<list_of_market_types>": self.__my_strategy.MARKET_TYPEs},
                    ),
                )
            )
            # add the market extracted to the list of market ids
            markets_list.append(market[0])
            # append a new Target object to the list with the relevant event and Market.
            self.__targets_list.append(Target(event, market[0]))

            Log.log_debug(
                datetime.strptime(self.__market_obj.description["marketTime"], "%Y-%m-%dT%H:%M:%S.%fZ") - datetime.now()
            )
            # The market object contains "runners" e.g. Spurs, Draw, Arsenal, just outputting them to log here
            for runner in self.__market_obj.runners:
                Log.log_debug(runner)

        # Return the complete list of Targets
        return self.__targets_list

    @property
    def call_obj(self) -> Call:
        return self.__call_obj

    @property
    def request_body_obj(self) -> RequestBody:
        return self.__request_body_obj
