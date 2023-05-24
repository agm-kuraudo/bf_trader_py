import traceback
import api.auth.auth_details as bf_auth
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody
from betfair.competitions import Competition
from output import Output as log
from api.http_methods import Methods
from betfair.eventType import EventType
from betfair.event import Event
from logic.simpleStategy import SimpleStrategy
import pandas as pd
from datetime import datetime
from betfair.market import Market, Runner, Target

class BFDriver:

    def __init__(self, myStrategy, logLevel):
        
        self.myStrat = myStrategy
        log.setLogLevel(logLevel)
        
        
        self.myRequestBody = RequestBody()
        self.myAuth = bf_auth.Auth()
        self.myCall = Call(self.myAuth)
        self.myMarket = Market()
        self.myEventTypes=EventType()
        self.myCompetitions = Competition()
        self.myEvent=Event()

    def authenticateToBetfair(self):
        try:
            self.myAuth.get_credentials_from_vault()
            self.myAuth.securityToken = self.myCall.callAuth(self.myRequestBody.populateTemplate("CertAuth", {"<USERID>": self.myAuth.bf_userid, "<PWD>": self.myAuth.bf_pwd}))
            return True
        except bf_auth.AuthException as g:
            log.log_error("\n".join(traceback.format_tb(g.__traceback__)))
            return False

    def getEventTypes(self):
        df, event_list = self.myEventTypes.buildFrameFromJSON(self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=self.myRequestBody.getTemplate("listEventTypes")))
        selected_events = []
        for event in event_list:
            if event.name in self.myStrat.EVENTS:
                selected_events.append(event)
                log.log_info(event)
        if len(selected_events) == 0:
            log.log_error("No events found matching: {}. Possible options will be listed below".format(self.myStrat.EVENTS))
            for event in event_list:
                log.log_error(event)
            return 0
        else:
            self.myEventTypeIds = []
            for eventType in selected_events:
                self.myEventTypeIds.append(eventType.id)

            return selected_events

    def getCompetitionIds(self):
        df, myComps = self.myCompetitions.buildFrameFromJSON(self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=self.myRequestBody.populateTemplate("listCompetitions", {"<ListOfEventIDs>" : self.myEventTypeIds})))
        log.log_debug(df.head())

        selected_comps = []
        for event in myComps:
            if event.name in self.myStrat.COMPETITIONS:
                selected_comps.append(event)
                log.log_info(event)
        if len(selected_comps) == 0:
            log.log_error("No competitions found matching: {}. Possible options will be listed below".format(self.myStrat.COMPETITIONS))
            for event in myComps:
                log.log_error(event)
            return 0
        else:
            self.myCompIds = []
            for compType in selected_comps:
                self.myCompIds.append(compType.id)
            return selected_comps
        
    def getEvents(self):
        df, myEventList = self.myEvent.buildFrameFromJSON(self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=self.myRequestBody.populateTemplate("listEvents", {"<ListOfEventIDs>" : self.myEventTypeIds, 
                                                    "<ListOfcompetitionIds>": self.myCompIds,
                                                    "<ListOfmarketType>": self.myStrat.MARKET_TYPEs})))
        log.log_debug(df.info())

        return myEventList[:self.myStrat.MAX_EVENTS]
    
    def getTargetMarkets(self, myEvents):
        MarketsList=[]
        self.TargetsList=[]

        for event in myEvents:
            df, market = self.myMarket.buildFrameFromJSON(self.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=self.myRequestBody.populateTemplate("marketCatalogue", {"<ListOfEventIDs>":[event.id], "<ListOfmarketType>": self.myStrat.MARKET_TYPEs})))
            MarketsList.append(market[0])
            self.TargetsList.append(Target(event, market[0]))

            log.log_debug(self.myMarket.description['marketTime'] - datetime.now())

            for runner in self.myMarket.runners:
                log.log_info (runner)

        return self.TargetsList

BF = BFDriver(SimpleStrategy(), log.INFO)
#BF = BFDriver(SimpleStrategy(), log.DEBUG)

#Step 1: Authenticate and get a Session Token!
if not BF.authenticateToBetfair():
    exit(1)

#Step 2: Extract the update to date for Event ID(s) for selected events
myEventTypes = BF.getEventTypes()
if myEventTypes == 0:
    exit(1)

#Step 3: Extract the competition IDs for my selected competitions
myComps = BF.getCompetitionIds()
if myComps == 0:
    exit(1)

#Step 4 : Extract the events matching our competition and event types
myEvents = BF.getEvents()
if myEvents == 0:
    exit(1)

log.log_info("There are {} events available".format(len(myEvents)))

myTargets=BF.getTargetMarkets(myEvents)

for target in myTargets:
    log.log_info(target)


#TODO: Add the specific event and market to a Target object