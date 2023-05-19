from betfair.BetfairObject import BetfairObject
import pandas as pd
from output import Output as log

class Event(BetfairObject):
    def __init__(self, id=None, name=None, marketCount=None, EventJson=None):
        log.log_debug("Event Object instantiated")
        self.__id = id
        self.__name = name
        self.__marketCount = marketCount
        if EventJson != None:
            self.buildFromJSON(EventJson)

    def buildFrameFromJSON(self, json):
        log.log_debug("buildFrameFromJSON called")
        log.log_debug("json: {}".format(json))
        df = pd.read_json(json.text)
        log.log_debug("df: {}".format(df.head()))

        compiled_df = pd.DataFrame({'EventID': pd.Series(dtype='str'),
                                    'EventName': pd.Series(dtype='str'),
                                    'marketCount': pd.Series(dtype='int')})

        eventdf = df["result"]
        log.log_debug("eventdf: {}".format(eventdf.head()))

        event_list = []

        for key,event in eventdf.items():
            log.log_debug(event)
            event_list.append(self.buildFromJSON(event))
            compiled_df.loc[len(compiled_df)] = {'EventID': self.__id, 'EventName': self.__name, 'marketCount': self.__marketCount}
        return compiled_df, event_list
    
    def buildFromJSON(self, json):
        print(json['marketCount'])
        print(json["eventType"]['id'])
        print(json["eventType"]['name'])
        self.__marketCount = json['marketCount']
        self.__id = json["eventType"]['id']
        self.__name = json["eventType"]['name']
        return Event(id=self.__id, name=self.__name, marketCount=self.__marketCount)
    
    
    def __str__(self):
        return ("Market: {}, ID: {}, Market Count: {}".format(self.__name, self.__id, self.__marketCount))