from io import StringIO

from requests import Response

import decorators.log_attrib
from betfair.BetfairObject import BetfairObject, BetfairObjectException
from output.log import Output as Log
import pandas as pd


class Competition(BetfairObject):
    @decorators.log_attrib.dump_args
    def __init__(self, comp_id=None, name=None, market_count=None, region=None, event_json=None):
        Log.log_debug("Competition Object instantiated")
        self.__id = comp_id
        self.__name = name
        self.__marketCount = market_count
        self.__region = region
        if event_json is not None:
            self.build_from_json(event_json)

    @decorators.log_attrib.dump_args
    def build_from_json(self, json):
        if type(json) is str:
            json = eval(json)
        Log.log_debug(json['marketCount'])
        Log.log_debug(json["competition"]['id'])
        Log.log_debug(json["competition"]['name'])
        self.__marketCount = json['marketCount']
        self.__region = json['competitionRegion']
        self.__id = json["competition"]['id']
        self.__name = json["competition"]['name']
        if not all(attr is not None for attr in [self.__marketCount, self.__region, self.__id, self.__name]):
            raise BetfairObjectException("Competition Object can't initialise as all values not returned in json")

        return Competition(comp_id=self.__id, name=self.__name, market_count=self.__marketCount, region=self.__region)

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

            compiled_df = pd.DataFrame({'competitionID': pd.Series(dtype='str'),
                                        'competitionName': pd.Series(dtype='str'),
                                        'marketCount': pd.Series(dtype='int'),
                                        'marketRegion': pd.Series(dtype='str')})

            event_df = df["result"]
            Log.log_debug("event_df: {}".format(event_df.head()))

            event_list = []

            for key, competition in event_df.items():
                Log.log_debug(competition)
                event_list.append(self.build_from_json(competition))
                compiled_df.loc[len(compiled_df)] = {'competitionID': self.__id, 'competitionName': self.__name,
                                                     'marketCount': self.__marketCount, 'marketRegion': self.__region}
            return compiled_df, event_list
        except Exception as e:
            raise BetfairObjectException("Unexpected error Competition Cannot build frame from json") from e

    def __str__(self):
        return (
            "Competition: {}, ID: {}, Market Count: {}, Region: {}".format(self.__name, self.__id, self.__marketCount,
                                                                           self.__region))

    @property
    def id(self):
        return self.__id

    @property
    def name(self):
        return self.__name

    @property
    def market_count(self):
        return self.__marketCount

    @property
    def region(self):
        return self.__region

