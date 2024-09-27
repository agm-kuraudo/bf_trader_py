from datetime import datetime
from io import StringIO
from requests import Response
from betfair.BetfairObject import BetfairObject, BetfairObjectException
import pandas as pd
from output import Output as Log
import decorators.log_attrib


class Event(BetfairObject):
    @decorators.log_attrib.dump_args
    def __init__(self, event_id=None, name=None, country_code=None, timezone=None, open_date=None, market_count=None,
                 event_json=None):
        Log.log_debug("Event Object instantiated")
        self.__id = event_id
        self.__name = name
        self.__countryCode = country_code
        self.__timezone = timezone
        self.__openDate = open_date
        self.__marketCount = market_count
        if event_json is not None:
            self.build_from_json(event_json)

    @decorators.log_attrib.dump_args
    def build_from_json(self, json):
        if type(json) is str:
            json = eval(json)
        Log.log_debug(json.get('event'))
        Log.log_debug(json.get("event").get('id'))
        Log.log_debug(json.get("event").get('name'))
        self.__marketCount = json.get('marketCount')
        self.__id = json.get("event").get('id')
        self.__name = json.get("event").get('name')
        try:
            self.__countryCode = json["event"]["countryCode"]
        except KeyError:
            self.__countryCode = ""
        self.__timezone = json.get("event").get("timezone")

        try:
            self.__openDate = datetime.strptime(json["event"]["openDate"], '%Y-%m-%dT%H:%M:%S.000Z')
        except KeyError:
            self.__openDate = None

        if not all(attr is not None for attr in
                   [self.__marketCount, self.__timezone, self.__id, self.__name, self.__openDate]):
            raise BetfairObjectException("Event Object can't initialise as all values not returned in json")

        return Event(event_id=self.__id, name=self.__name, country_code=self.__countryCode, timezone=self.__timezone,
                     open_date=self.__openDate, market_count=self.__marketCount)

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

            compiled_df = pd.DataFrame({'eventID': pd.Series(dtype='str'),
                                        'eventName': pd.Series(dtype='str'),
                                        'marketCount': pd.Series(dtype='int'),
                                        'countryCode': pd.Series(dtype='str'),
                                        'timezone': pd.Series(dtype='str'),
                                        'openDate': pd.Series(dtype='datetime64[ns]')})

            event_df = df["result"]
            Log.log_debug("event_df: {}".format(event_df.head()))

            event_list = []

            for key, event in event_df.items():
                Log.log_debug(event)
                event_list.append(self.build_from_json(event))
                compiled_df.loc[len(compiled_df)] = {'eventID': self.__id, 'eventName': self.__name,
                                                     'marketCount': self.__marketCount, 'countryCode': self.__countryCode,
                                                     'timezone': self.__timezone, 'openDate': self.__openDate}
            return compiled_df, event_list
        except Exception as e:
            raise BetfairObjectException("Unexpected error Event Cannot build frame from json") from e

    def __str__(self):
        return (
            "eventID: {}, eventName: {}, marketCount: {}, countryCode: {}, timezone: {}, openDate: {}".format(
                self.__id,
                self.__name,
                self.__marketCount,
                self.__countryCode,
                self.__timezone,
                self.__openDate))

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
    def country_code(self):
        return self.__countryCode

    @property
    def timezone(self):
        return self.__timezone

    @property
    def open_date(self):
        return self.__openDate
