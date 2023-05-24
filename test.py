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

    def __init__(self):
        self.myRequestBody = RequestBody()
        self.myAuth = bf_auth.Auth()
        self.myCall = Call(self.myAuth)
        self.myStrat = SimpleStrategy()

    def authenticateToBetfair(self, myAuth, myCall, myRequestBody):
        try:
            myAuth.get_credentials_from_vault()
            myAuth.securityToken = myCall.callAuth(myRequestBody.populateTemplate("CertAuth", {"<USERID>": myAuth.bf_userid, "<PWD>": myAuth.bf_pwd}))
            return True
        except bf_auth.AuthException as g:
            log.log_error("\n".join(traceback.format_tb(g.__traceback__)))
            return False

    def getEventTypes(self,myAuth, myCall, myRequestBody, myStrat):
        myEventTypes=EventType()
        df, event_list = myEventTypes.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.getTemplate("listEventTypes")))
        selected_events = []
        for event in event_list:
            if event.name in myStrat.EVENTS:
                selected_events.append(event)
                log.log_info(event)
        if len(selected_events) == 0:
            log.log_error("No events found matching: {}. Possible options will be listed below".format(myStrat.EVENTS))
            for event in event_list:
                log.log_error(event)
            return 0
        else:
            self.myEventTypeIds = []
            for eventType in selected_events:
                self.myEventTypeIds.append(eventType.id)

            return selected_events

    def getCompetitionIds(self, myAuth, myCall, myRequestBody, myStrat):
        myCompetitions = Competition()


        df, myComps = myCompetitions.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.populateTemplate("listCompetitions", {"<ListOfEventIDs>" : self.myEventTypeIds})))

        print (df.head())

        selected_comps = []
        for event in myComps:
            if event.name in myStrat.COMPETITIONS:
                selected_comps.append(event)
                log.log_info(event)
        if len(selected_comps) == 0:
            log.log_error("No competitions found matching: {}. Possible options will be listed below".format(myStrat.COMPETITIONS))
            for event in myComps:
                log.log_error(event)
            return 0
        else:
            self.myCompIds = []
            for compType in selected_comps:
                self.myCompIds.append(compType.id)
            return selected_comps
        
    def getEvents(self, myCall, myRequestBody, myStrat):
        myEvent=Event()
        df, myEventList = myEvent.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.populateTemplate("listEvents", {"<ListOfEventIDs>" : self.myEventTypeIds, 
                                                    "<ListOfcompetitionIds>": self.myCompIds,
                                                    "<ListOfmarketType>": myStrat.MARKET_TYPEs})))
        print(df.info())

        return myEventList

BF = BFDriver()

#Step 1: Authenticate and get a Session Token!
if not BF.authenticateToBetfair(BF.myAuth, BF.myCall, BF.myRequestBody):
    exit(1)

#Step 2: Extract the update to date for Event ID(s) for selected events
myEventTypes = BF.getEventTypes(myAuth=BF.myAuth, myCall=BF.myCall, myRequestBody=BF.myRequestBody, myStrat=BF.myStrat)
if myEventTypes == 0:
    exit(1)

#Step 3: Extract the competition IDs for my selected competitions
myComps = BF.getCompetitionIds(myAuth=BF.myAuth, myCall=BF.myCall, myRequestBody=BF.myRequestBody, myStrat=BF.myStrat)
if myComps == 0:
    exit(1)

#Step 4 : Extract the events matching our competition and event types
myEvents = BF.getEvents(myCall=BF.myCall, myRequestBody=BF.myRequestBody, myStrat=BF.myStrat)
if myEvents == 0:
    exit(1)

log.log_info("There are {} events available".format(len(myEvents)))

myMarket = Market()

myMarket.buildFrameFromJSON(BF.myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=BF.myRequestBody.populateTemplate("marketCatalogue", {"<ListOfEventIDs>":[myEvents[0].id], "<ListOfmarketType>": BF.myStrat.MARKET_TYPEs})))

print(myMarket.description['marketTime'] - datetime.now())

for runner in myMarket.runners:
    print (runner)

#TODO: Add the specific event and market to a Target object