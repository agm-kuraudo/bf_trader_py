import traceback
import api.auth.auth_details as bf_auth
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody
from output import Output as log
from api.http_methods import Methods
from betfair.event import Event
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

def getEvents(myAuth, myCall, myRequestBody, myStrat):
    myEvent=Event()
    df, list = myEvent.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.getTemplate("listEvents")))
    selected_events = []
    for event in list:
        if event.name in myStrat.EVENTS:
            selected_events.append(event)
            log.log_info(event)
    if len(selected_events) != 0:
        log.log_error("No events found matching: {}. Possible options will be listed below".format(myStrat.EVENTS))
        for event in list:
            log.log_error(event)
        return 0
    else:
        return selected_events

myRequestBody = RequestBody()
myAuth = bf_auth.Auth()
myCall = Call(myAuth)
myStrat = SimpleStrategy()

#Step 1: Authenticate and get a Session Token!
if not authenticateToBetfair(myAuth, myCall, myRequestBody):
    exit(1)

#Step 2: Extract the update to date for Event ID(s) for selected events
myEvents = getEvents(myAuth=myAuth, myCall=myCall, myRequestBody=myRequestBody, myStrat=myStrat)
if myEvents == 0:
    exit(1)