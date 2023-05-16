import traceback
import api.auth.auth_details as bf_auth
import api.auth.vault as bf_vault
from api.urls import Urls
from api.call import Call
from api.request_body import RequestBody


try:
    myAuth = bf_auth.Auth()
    myAuth.get_credentials_from_vault()

    myRequestBody = RequestBody()

    myCall = Call(Urls.CERT_LOGIN, "post", myAuth)

    myAuth.securityToken = myCall.callAuth(myRequestBody.populateTemplate("CertAuth", {"<USERID>": myAuth.bf_userid, "<PWD>": myAuth.bf_pwd}))

    print (myAuth.securityToken)

except bf_auth.AuthException as g:
    print("\n".join(traceback.format_tb(g.__traceback__)))