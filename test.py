import traceback
import api.auth.auth_details as bf_auth
import api.auth.vault as bf_vault
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody
from api.output import Output as log
from api.http_methods import Methods

def authenticateToBetfair(myAuth, myCall, myRequestBody):
    try:
        myAuth.get_credentials_from_vault()
        myAuth.securityToken = myCall.callAuth(myRequestBody.populateTemplate("CertAuth", {"<USERID>": myAuth.bf_userid, "<PWD>": myAuth.bf_pwd}))
    except bf_auth.AuthException as g:
        log.log_error("\n".join(traceback.format_tb(g.__traceback__)))

myRequestBody = RequestBody()
myAuth = bf_auth.Auth()
myCall = Call(myAuth)


#Step 1: Authenticate and get a Session Token!
authenticateToBetfair(myAuth, myCall, myRequestBody)

#Step 2: Extract the update to date for Event ID(s) for selected events
myCall.call(http_method=Methods.POST, url=Urls.JSON_RPC, RequestBody=myRequestBody.getTemplate("listEvents"))