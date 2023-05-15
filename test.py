import api.auth.auth_details
import api.auth.vault
from api.urls import Urls

from api.call import Call


myAuth = api.auth.auth_details.Auth()
myAuth.get_credentials_from_vault()

request_body={"username": myAuth.bf_userid, "password": myAuth.bf_pwd}

myCall = Call(Urls.CERT_LOGIN, "post", myAuth)

response = myCall.call(request_body)

print (response)