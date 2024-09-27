from io import StringIO

from requests import Response

from betfair.BetfairObject import BetfairObject, BetfairObjectException
import pandas as pd
from output import Output as Log
import decorators.log_attrib


class EventType(BetfairObject):
    @decorators.log_attrib.dump_args
    def __init__(self, event_type_id=None, name=None, market_count=None, event_json=None):
        Log.log_debug("EventType Object instantiated")
        self.__id = event_type_id
        self.__name = name
        self.__marketCount = market_count
        if event_json is not None:
            self.build_from_json(event_json)

    @decorators.log_attrib.dump_args
    def build_frame_from_json(self, json):

        try:
            Log.log_debug("buildFrameFromJSON called")
            Log.log_debug("json: {}".format(json))

            if type(json) is Response:
                json_text = json.text
            else:
                json_text = json

            df = pd.read_json(StringIO(json_text))
            Log.log_debug("df: {}".format(df.head()))

            compiled_df = pd.DataFrame({'EventID': pd.Series(dtype='str'),
                                        'EventName': pd.Series(dtype='str'),
                                        'marketCount': pd.Series(dtype='int')})

            event_df = df["result"]
            Log.log_debug("event_df: {}".format(event_df.head()))

            event_list = []

            for key, event in event_df.items():
                Log.log_debug(event)
                event_list.append(self.build_from_json(event))
                compiled_df.loc[len(compiled_df)] = {'EventID': self.__id, 'EventName': self.__name,
                                                     'marketCount': self.__marketCount}
            return compiled_df, event_list
        except Exception as e:
            raise BetfairObjectException(f"Failed to build data frame from JSON {json}") from e

    @decorators.log_attrib.dump_args
    def build_from_json(self, json):
        if type(json) is str:
            json = eval(json)
        self.__marketCount = json.get('marketCount')
        self.__id = json.get("eventType").get('id')
        self.__name = json.get("eventType").get('name')
        if not all(attr is not None for attr in [self.__marketCount, self.__id, self.__name]):
            raise BetfairObjectException("Event Type Object can't initialise as all values not returned in json")
        Log.log_debug(json['marketCount'])
        Log.log_debug(json["eventType"]['id'])
        Log.log_debug(json["eventType"]['name'])
        return EventType(event_type_id=self.__id, name=self.__name, market_count=self.__marketCount)

    def __str__(self):
        return "Market: {}, ID: {}, Market Count: {}".format(self.__name, self.__id, self.__marketCount)

    @property
    def id(self):
        return self.__id

    @property
    def name(self):
        return self.__name

    @property
    def market_count(self):
        return self.__marketCount

    '''
            self.__id = id
        self.__name = name
        self.__marketCount = marketCount'''
