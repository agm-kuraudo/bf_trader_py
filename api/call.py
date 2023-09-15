import json
import requests
from requests import Response

from api.http_methods import Methods
from output import Output as Log
from api.urls import Urls
from api.auth.auth_details import Auth


class CallException(Exception):
    pass


class Call:
    def __init__(self, auth):
        """
        Call init function, sets up basic variables, requires an auth object with credentials
        :param auth:
        """
        self.__url = None
        self.__auth = auth
        self.headers = {"X-Application": auth.app_key}
        Log.log_debug("Call object instantiated")

    def call(self, http_method: Methods, url: Urls, request_body: dict) -> Response:
        """
        This method makes a HTTP request to a URL. It currently only needs to do POST requests
        TODO: utilise the http_method to be able to send all relevant HTTP verbs
        :param http_method: GET/POST/PUT/DELETE etc - currently redundant as all requests are POST
        :param url: The URL of the end point
        :param request_body: The body of the HTTP message
        :return: Returns the "Response" object (part of the requests module)
        """
        self.url = url
        Log.log_info("Making request to {}".format(url))
        Log.log_debug("headers: {}, RequestBody: {}".format(self.headers, request_body))
        r = requests.post(headers=self.headers, url=self.url, json=request_body)
        Log.log_debug(r.text)
        return r

    def call_auth(self, request_body: dict) -> str:
        """
        This is a special instance of "call" design to authenticate users with a certificate and capture the login
        session
        :param request_body this will be the auth template with replacements
        :return:
        """
        try:
            self.url = Urls.CERT_LOGIN
            Log.log_debug("Attempting to authentication via {}".format(self.url))
            Log.log_debug("X-Application: {}".format(self.__auth.app_key))

            r = requests.post(headers={"X-Application": Auth.app_key}, url=self.url, params=request_body,
                              cert=(Auth.crt_file, Auth.key_file))
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
