import json
import requests
from output import Output as Log
from api.urls import Urls
from api.auth.auth_details import Auth


class CallException(Exception):
    pass


class Call:
    def __init__(self, auth):
        self.__url = None
        self.__auth = auth
        self.headers = {"X-Application": auth.appKey}
        Log.log_debug("Call object instantiated")

    def call(self, http_method, url, request_body):
        self.url = url
        Log.log_info("Making request to {}".format(url))
        Log.log_debug("headers: {}, RequestBody: {}".format(self.headers, request_body))
        r = requests.post(headers=self.headers, url=self.url, json=request_body)
        Log.log_debug(r.text)
        return r

    '''This is a special instance of "call" design to authenticate users with a certificate and capture the login 
    session'''

    # @api.decorators.SimpleDecorator
    def call_auth(self, request_body):
        try:
            self.url = Urls.CERT_LOGIN
            Log.log_debug("Attempting to authentication via {}".format(self.url))
            Log.log_debug("X-Application: {}".format(self.__auth.appKey))

            r = requests.post(headers={"X-Application": Auth.appKey}, url=self.url, params=request_body,
                              cert=(Auth.crtfile, Auth.keyfile))
            Log.log_debug("Response Message: {}".format(r.text))
            json_resp = json.loads(r.text)

            if json_resp['loginStatus'] != 'SUCCESS':
                raise Exception("Login unsuccessful! \n" + r.text)
            else:
                self.headers.update({"X-Authentication": json_resp['sessionToken']})
                return json_resp['sessionToken']

        except Exception as a:
            raise CallException("Failed to authenticate!") from a

    @property
    def url(self):
        return self.__url

    @url.setter
    def url(self, value):
        self.__url = value
