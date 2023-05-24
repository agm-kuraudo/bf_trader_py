from betfair.BetfairObject import BetfairObject
from output import Output as log
import pandas as pd

class Competition(BetfairObject):
    def __init__(self, id=None, name=None, marketCount=None, region=None, EventJson=None):
        log.log_debug("Competition Object instantiated")
        self.__id = id
        self.__name = name
        self.__marketCount = marketCount
        self.__region = region
        if EventJson != None:
            self.buildFromJSON(EventJson)

    def buildFromJSON(self, json):
        log.log_debug(json['marketCount'])
        log.log_debug(json["competition"]['id'])
        log.log_debug(json["competition"]['name'])
        self.__marketCount = json['marketCount']
        self.__region = json['competitionRegion']
        self.__id = json["competition"]['id']
        self.__name = json["competition"]['name']
        return Competition(id=self.__id, name=self.__name, marketCount=self.__marketCount, region=self.__region)
   

    def buildFrameFromJSON(self, json):
        log.log_debug("buildFrameFromJSON called")
        log.log_debug("json: {}".format(json))
        df = pd.read_json(json.text)
        log.log_debug("df: {}".format(df.head()))

        compiled_df = pd.DataFrame({'competitionID': pd.Series(dtype='str'),
                            'competitionName': pd.Series(dtype='str'),
                            'marketCount': pd.Series(dtype='int'),
                            'marketRegion': pd.Series(dtype='str')})

        eventdf = df["result"]
        log.log_debug("eventdf: {}".format(eventdf.head()))

        event_list = []

        for key,competition in eventdf.items():
            log.log_debug(competition)
            event_list.append(self.buildFromJSON(competition))
            compiled_df.loc[len(compiled_df)] = {'competitionID': self.__id, 'competitionName': self.__name, 'marketCount': self.__marketCount, 'marketRegion': self.__region}
        return compiled_df, event_list
    
    def __str__(self):
        return ("Competition: {}, ID: {}, Market Count: {}, Region: {}".format(self.__name, self.__id, self.__marketCount, self.__region))
    
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
    def region(self):
        return self.__region