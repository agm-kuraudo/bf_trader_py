import traceback
import api.auth.auth_details as bf_auth
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody
from betfair.competitions import Competition
from output import Output as log
from api.http_methods import Methods
from betfair.event import EventType
from logic.simpleStategy import SimpleStrategy
import pandas as pd


def authenticateToBetfair(myAuth, myCall, myRequestBody):
    try:
        myAuth.get_credentials_from_vault()
        myAuth.securityToken = myCall.callAuth(myRequestBody.populateTemplate("CertAuth", {"<USERID>": myAuth.bf_userid, "<PWD>": myAuth.bf_pwd}))
        return True
    except bf_auth.AuthException as g:
        log.log_error("\n".join(traceback.format_tb(g.__traceback__)))
        return False

def getEventTypes(myAuth, myCall, myRequestBody, myStrat):
    myEventTypes=EventType()
    df, event_list = myEventTypes.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.getTemplate("listEvents")))
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
        return selected_events

def getCompetitionIds(myAuth, myCall, myRequestBody, myStrat, myEvents):
    myCompetitions = Competition()
    myEventTypeIds = []
    for eventType in myEvents:
        myEventTypeIds.append(eventType.id)


    df, myComps = myCompetitions.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.populateTemplate("listCompetitions", {"<ListOfEventIDs>" : myEventTypeIds})))

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
        return selected_comps

myRequestBody = RequestBody()
myAuth = bf_auth.Auth()
myCall = Call(myAuth)
myStrat = SimpleStrategy()


#Step 1: Authenticate and get a Session Token!
if not authenticateToBetfair(myAuth, myCall, myRequestBody):
    exit(1)

#Step 2: Extract the update to date for Event ID(s) for selected events
myEvents = getEventTypes(myAuth=myAuth, myCall=myCall, myRequestBody=myRequestBody, myStrat=myStrat)
if myEvents == 0:
    exit(1)

#Step 3: Extract the compeition IDs for my selected competitions
myComps = getCompetitionIds(myAuth=myAuth, myCall=myCall, myRequestBody=myRequestBody, myStrat=myStrat, myEvents=myEvents)
if myComps == 0:
    exit(1)