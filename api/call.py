import json
import requests
from api.output import Output as log
from api.urls import Urls
from api.auth.auth_details import Auth

class CallException(Exception):
    pass

class Call():
    def __init__(self, auth):
        self.__auth = auth
        log.log_debug("Call object instantiated")
    
    def call(self, headers, http_method, url, RequestBody):
        #TODO: This should handle normal request params and JSON type messages
        #TODO - should handle all HTTP post types (Also shold should be ENUM http methods)
        self.url = url

        r = requests.post(headers={"X-Application": "wId8CbMYLNRjCwWm"}, url=self.url, params=RequestBody, cert=("D:/OneDrive - XHT/06_Projects/bookmaking/API_Auth/client-2048.crt", "D:/OneDrive - XHT/06_Projects/bookmaking/API_Auth/client-2048.key"))
        requests.Request
        return r.text

    '''This is a special isntance of "call" design to authenticate users with a certificate and capture the login session'''
   #@api.decorators.SimpleDecorator
    def callAuth(self, RequestBody):
        try:
            self.url = Urls.CERT_LOGIN
            log.log_debug("Attempting to authentication via {}".format(self.url))
            log.log_debug("X-Application: {}".format(self.__auth.appKey))

            r = requests.post(headers={"X-Application": Auth.appKey}, url=self.url, params=RequestBody, cert=(Auth.crtfile, Auth.keyfile))
            log.log_debug("Response Message: {}".format(r.text))
            json_resp = json.loads(r.text)

            if json_resp['loginStatus'] != 'SUCCESS':
                raise Exception("Login unsuccessful! \n" + r.text)
            else:
                return json_resp['sessionToken']

        except Exception as a:
            raise CallException("Failed to authenticate!") from a
