from betfair.BetfairObject import BetfairObject
import pandas as pd
from output import Output as log

class Event(BetfairObject):
    def __init__(self, id=None, name=None, countryCode=None, timezone=None, openDate=None, marketCount=None, EventJson=None):
        log.log_debug("Event Object instantiated")
        self.__id = id
        self.__name = name
        self.__countryCode=countryCode
        self.__timezone=timezone
        self.__openDate=openDate
        self.__marketCount = marketCount
        if EventJson != None:
            self.buildFromJSON(EventJson)

    def buildFromJSON(self, json):
        log.log_debug(json['event'])
        log.log_debug(json["event"]['id'])
        log.log_debug(json["event"]['name'])
        self.__marketCount = json['marketCount']
        self.__id = json["event"]['id']
        self.__name = json["event"]['name']
        try:
            self.__countryCode = json["event"]["countryCode"]
        except KeyError:
            self.__countryCode = ""
        self.__timezone = json["event"]["timezone"]
        self.__openDate = json["event"]["openDate"]
        return Event(id=self.__id, name=self.__name, countryCode=self.__countryCode, timezone=self.__timezone, openDate=self.__openDate, marketCount=self.__marketCount)
   

    def buildFrameFromJSON(self, json):
        log.log_debug("buildFrameFromJSON called")
        log.log_debug("json: {}".format(json))
        df = pd.read_json(json.text)
        log.log_debug("df: {}".format(df.head()))

        compiled_df = pd.DataFrame({'eventID': pd.Series(dtype='str'),
                            'eventName': pd.Series(dtype='str'),
                            'marketCount': pd.Series(dtype='int'),
                            'countryCode': pd.Series(dtype='str'),
                            'timezone': pd.Series(dtype='str'),
                            'openDate': pd.Series(dtype='datetime64[ns]')})

        eventdf = df["result"]
        log.log_debug("eventdf: {}".format(eventdf.head()))

        event_list = []

        for key,event in eventdf.items():
            log.log_debug(event)
            event_list.append(self.buildFromJSON(event))
            compiled_df.loc[len(compiled_df)] = {'eventID': self.__id, 'eventName': self.__name, 'marketCount': self.__marketCount, 'countryCode': self.__countryCode, 'timezone': self.__timezone, 'openDate': self.__openDate}
        return compiled_df, event_list
    
    def __str__(self):
        return ("eventID: {}, eventName: {}, marketCount: {}, countryCode: {}, timezone: {}, openDate: {}".format(self.__id, self.__name, self.__marketCount, self.__countryCode, self.__timezone, self.__openDate))
    
    @property
    def id(self):
        return self.__id
    @property
    def name(self):
        return self.__name
    @property
    def marketCount(self):
        return self.__marketCount
    @property
    def countryCode(self):
        return self.__countryCode
    @property
    def timezone(self):
        return self.__timezone
    @property
    def openDate(self):
        return self.__openDate