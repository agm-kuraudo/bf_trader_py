from datetime import datetime

from requests import Response

from betfair.BetfairObject import BetfairObject, BetfairObjectException
from output.log import Output as Log
import pandas as pd
from io import StringIO
import decorators.log_attrib


class Target:
    @decorators.log_attrib.dump_args
    def __init__(self, my_event, my_market):
        self.__my_event = my_event
        self.__my_market = my_market

    def __str__(self):
        Log.log_debug(self.my_event)
        Log.log_debug(self.my_market)
        return "Target is event: {}, market: {}".format(self.__my_event.name, self.__my_market.name)

    def update_odds_from_json(self, json_resp):
        pass

    @property
    def my_event(self):
        return self.__my_event

    @property
    def my_market(self):
        return self.__my_market


class Market(BetfairObject):
    @decorators.log_attrib.dump_args
    def __init__(self, market_id=None, name=None, description=None, total_matched=None, runners=None):
        #self.__marketTime = []
        self.__market_time = None
        self.__runnerList = None
        if runners is None:
            runners = []
        self.__id = market_id
        self.__name = name
        self.__description = description
        self.__totalMatched = total_matched
        self.__runners = runners

    @decorators.log_attrib.dump_args
    def build_from_json(self, json):
        if type(json) is str:
            json = json.replace("\n", "")
            json = eval(json)
        Log.log_debug(json['marketId'])
        Log.log_debug(json["marketName"])
        Log.log_debug(json["runners"])
        self.__runnerList = json["runners"]
        self.__runners = []
        # Log.log_debug("Runners... {}".format(self.__runners))
        self.add_runners()
        self.__totalMatched = json['totalMatched']
        self.__id = json["marketId"]
        self.__name = json["marketName"]
        self.__description = json["description"]
        self.__market_time = datetime.strptime(self.__description["marketTime"], '%Y-%m-%dT%H:%M:%S.000Z')

        if not all(attr is not None for attr in [self.__totalMatched, self.__description, self.__id, self.__name]):
            raise BetfairObjectException("Market Object can't initialise as all values not returned in json")

        return Market(market_id=self.__id, name=self.__name, description=self.__description,
                      total_matched=self.__totalMatched, runners=self.__runners)

    @decorators.log_attrib.dump_args
    def build_frame_from_json(self, json):
        try:

            if type(json) is Response:
                json_text = json.text
            else:
                json_text = json

            Log.log_debug("buildFrameFromJSON called")
            Log.log_debug("json: {}".format(json))
            df = pd.read_json(StringIO(json_text))
            Log.log_debug("df: {}".format(df.head()))

            compiled_df = pd.DataFrame({'MarketID': pd.Series(dtype='str'),
                                        'MarketName': pd.Series(dtype='str'),
                                        'Description': pd.Series(dtype='object'),
                                        'totalMatched': pd.Series(dtype='int')})

            event_df = df["result"]
            Log.log_debug("event_df: {}".format(event_df.head()))

            event_list = []

            for key, event in event_df.items():
                Log.log_debug(event)
                event_list.append(self.build_from_json(event))
                compiled_df.loc[len(compiled_df)] = {'MarketID': self.__id, 'MarketName': self.__name,
                                                     'Description': self.__description, 'totalMatched': self.__totalMatched}
            return compiled_df, event_list
        except Exception as e:
            raise BetfairObjectException("Cannot build Market Objects from Json") from e

    def __str__(self):
        return ("marketId: {}, marketName: {}, description: {}, totalMatched: {}".format(self.__id, self.__name,
                                                                                         self.__description,
                                                                                         self.__totalMatched))

    @decorators.log_attrib.dump_args
    def add_runners(self):
        try:
            Log.log_debug("Adding runners {}".format(self.__runnerList))
            for runner_dict in self.__runnerList:
                self.__runners.append(Runner(runner_dict['selectionId'], runner_dict['runnerName']))
                Log.log_debug("Adding runner {}".format(self.__runners[-1]))
        except Exception as e:
            raise BetfairObjectException("Cannot add runners to Market Object") from e

    @property
    def id(self):
        return self.__id

    @property
    def market_time(self):
        return self.__market_time

    @property
    def name(self):
        return self.__name

    @property
    def description(self):
        return self.__description

    @property
    def total_matched(self):
        return self.__totalMatched

    @property
    def runner_list(self):
        return self.__runnerList

    @property
    def runners(self):
        return self.__runners

    @runners.setter
    def runners(self, value):
        self.__runners = value


class Runner:
    @decorators.log_attrib.dump_args
    def __init__(self, runner_id, name):
        self.__id = runner_id
        self.__name = name
        self.__odds = []
        self.__status = None
        self.__totalMatched = None
        self.__toBack = []
        self.__toLay = []

    def __str__(self):
        return "Runner: {}, ID: {}".format(self.__name, self.__id)

    @property
    def odds(self):
        return self.__odds[-1]

    @decorators.log_attrib.dump_args
    @odds.setter
    def odds(self, value):
        for odds in value:
            if odds["selectionId"] == self.__id:
                Log.log_info("Selection {} matched!".format(self.__id))
                self.__odds.append(odds["selectionId"])
                self.__status = odds["status"]
                self.__totalMatched = odds["totalMatched"]
                self.__toBack = odds['ex']['availableToBack']
                self.__toLay = odds['ex']['availableToLay']

    @property
    def status(self):
        return self.__status

    @property
    def id(self):
        return self.__id

    @property
    def name(self):
        return self.__name

    @property
    def total_matched(self):
        return self.__totalMatched

    @property
    def to_back(self):
        return self.__toBack

    @property
    def to_lay(self):
        return self.__toLay

    def get_odds_list(self):
        return self.__odds
