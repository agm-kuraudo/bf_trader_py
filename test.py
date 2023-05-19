import traceback
import api.auth.auth_details as bf_auth
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody
from output import Output as log
from api.http_methods import Methods
from betfair.event import Event
import pandas as pd


def authenticateToBetfair(myAuth, myCall, myRequestBody):
    try:
        myAuth.get_credentials_from_vault()
        myAuth.securityToken = myCall.callAuth(myRequestBody.populateTemplate("CertAuth", {"<USERID>": myAuth.bf_userid, "<PWD>": myAuth.bf_pwd}))
        return True
    except bf_auth.AuthException as g:
        log.log_error("\n".join(traceback.format_tb(g.__traceback__)))
        return False

myRequestBody = RequestBody()
myAuth = bf_auth.Auth()
myCall = Call(myAuth)
myEvent=Event()

#Step 1: Authenticate and get a Session Token!
if not authenticateToBetfair(myAuth, myCall, myRequestBody):
    exit(1)

#Step 2: Extract the update to date for Event ID(s) for selected events
df, list = myEvent.buildFrameFromJSON(myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.getTemplate("listEvents")))

for event in list:
    log.log_info(event)