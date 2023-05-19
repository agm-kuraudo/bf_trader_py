import json
import requests
from output import Output as log
from api.urls import Urls
from api.auth.auth_details import Auth

class CallException(Exception):
    pass

class Call():
    def __init__(self, auth):
        self.__auth = auth
        self.headers={"X-Application": auth.appKey}
        log.log_debug("Call object instantiated")
    
    def call(self, http_method, url, RequestBody):
        self.url = url
        log.log_info("Making request to {}".format(url))
        log.log_debug("headers: {}, RequestBody: {}".format(self.headers, RequestBody))
        r = requests.post(headers=self.headers, url=self.url, json=RequestBody)
        log.log_debug(r.text)
        return r

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
                self.headers.update({"X-Authentication": json_resp['sessionToken']})
                return json_resp['sessionToken']

        except Exception as a:
            raise CallException("Failed to authenticate!") from a
