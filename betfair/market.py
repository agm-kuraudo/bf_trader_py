from datetime import datetime
from betfair.BetfairObject import BetfairObject
from output import Output as log
import pandas as pd


class Target:
    def __init__(self, myEvents, myMarkets):
        self.myEvents=myEvents
        self.myMarkets=myMarkets

    def __str__(self):
        log.log_debug(self.myEvents)
        log.log_debug(self.myMarkets)
        return "Target is event: {}, market: {}".format(self.myEvents.name, self.myMarkets.name)
    
    def updateOddsFromJSON(self, json_resp):
        pass

class Market(BetfairObject):
    def __init__(self, id=None, name=None, description=None, totalMatched=None, runners=[]):
        self.__id=id
        self.__name=name
        self.__description=description
        self.__totalMatched=totalMatched
        self.__runners = runners

    def buildFromJSON(self, json):
        log.log_debug(json['marketId'])
        log.log_debug(json["marketName"])
        log.log_debug(json["runners"])
        self.__runnerList = json["runners"]
        self.addRunners()
        self.__totalMatched = json['totalMatched']
        self.__id = json["marketId"]
        self.__name = json["marketName"]
        self.__description = json["description"]
        self.__description["marketTime"] = datetime.strptime(self.__description["marketTime"], '%Y-%m-%dT%H:%M:%S.000Z')
        return Market(id=self.__id, name=self.__name, description=self.__description, totalMatched=self.__totalMatched, runners=self.__runners)
    
    def buildFrameFromJSON(self, json):
        log.log_debug("buildFrameFromJSON called")
        log.log_debug("json: {}".format(json))
        df = pd.read_json(json.text)
        log.log_debug("df: {}".format(df.head()))

        compiled_df = pd.DataFrame({'MarketID': pd.Series(dtype='str'),
                                    'MarketName': pd.Series(dtype='str'),
                                    'Description': pd.Series(dtype='object'),
                                    'totalMatched': pd.Series(dtype='int')})

        eventdf = df["result"]
        log.log_debug("eventdf: {}".format(eventdf.head()))

        event_list = []

        for key,event in eventdf.items():
            log.log_debug(event)
            event_list.append(self.buildFromJSON(event))
            compiled_df.loc[len(compiled_df)] = {'MarketID': self.__id, 'MarketName': self.__name, 'Description': self.__description, 'totalMatched': self.__totalMatched}
        return compiled_df, event_list

    def __str__(self):
        return ("marketId: {}, marketName: {}, description: {}, totalMatched: {}".format(self.__id, self.__name, self.__description, self.__totalMatched))
    
    def addRunners(self):
        log.log_debug("Adding runners")
        for runner_dict in self.runnerList:
            self.__runners.append(Runner(runner_dict['selectionId'], runner_dict['runnerName']))
            log.log_debug("Adding runner {}".format(self.__runners[-1]))


    @property
    def id(self):
        return self.__id
    @property
    def name(self):
        return self.__name
    @property
    def description(self):
        return self.__description
    @property
    def totalMatched(self):
        return self.__totalMatched
    @property
    def runnerList(self):
        return self.__runnerList
    @property
    def runners(self):
        return self.__runners

class Runner:
    def __init__(self, id, name):
        self.__id=id
        self.__name=name
        self.__odds = []
        self.__status=None
        self.__totalMatched=None
        self.__toBack = []
        self.__toLay=[]

    def __str__(self):
        return "Runner: {}, ID: {}".format(self.__name, self.__id)
    
    @property
    def odds(self):
        return self.__odds[-1]
    
    @odds.setter
    def odds(self, value):
        for odds in value:
            if odds["selectionId"] == self.__id:
                log.log_info("Selection {} matched!".format(self.__id))
                self.__odds.append(odds["selectionId"])
                self.__status=odds["status"]
                self.__totalMatched=odds["totalMatched"]
                self.__toBack = odds['ex']['availableToBack']
                self.__toLay = odds['ex']['availableToLay']


    @property
    def status(self):
        return self.__status
    @property
    def totalMatched(self):
        return self.__totalMatched
    @property
    def toBack(self):
        return self.__toBack
    @property
    def toLay(self):
        return self.__toLay
    
    def get_odds_list(self):
        return self.__odds
    