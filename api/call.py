import json

import requests
from requests import Response

import decorators.log_attrib
from api.http_methods import Methods
from api.urls import Urls
from output.log import Output as Log


class CallException(Exception):
    pass


class Call:
    @decorators.log_attrib.dump_args
    def __init__(self, auth=None):
        """
        Call init function, sets up basic variables, requires an auth object with credentials
        :param auth:
        """
        self.__url = None
        self.__headers = {}

        if auth is not None:
            self.__auth = auth
            self.__headers["X-Application"] = auth.app_key

        Log.log_debug("Call object instantiated")

    def call(self, http_method: Methods, url: Urls, request_body: dict) -> Response:
        """
        This method makes a HTTP request to a URL. It currently only needs to do POST requests
        :param http_method: GET/POST/PUT/DELETE etc.
        :param url: The URL of the end point
        :param request_body: The body of the HTTP message
        :return: Returns the "Response" object (part of the requests' module)
        """
        try:
            self.url = url
            Log.log_debug(f"Making request to {url}")
            Log.log_debug(f"headers: {self.__headers}, RequestBody: {request_body}")

            r = requests.request(
                method=str(http_method).replace("Methods.", ""), headers=self.__headers, url=self.url, json=request_body
            )
            Log.log_debug(r.text)
            return r
        except Exception as e:
            raise CallException("Unexpected Exception during call method") from e

    @decorators.log_attrib.dump_args
    def call_auth(self, request_body: dict) -> str:
        """1
        This is a special instance of "call" design to authenticate users with a certificate and capture the login
        session
        :param request_body this will be the auth template with replacements
        :return:
        """
        try:
            self.url = Urls.CERT_LOGIN
            Log.log_debug(f"Attempting to authentication via {self.url}")
            Log.log_debug(f"X-Application: {self.__auth.app_key}")

            r = requests.post(
                headers={"X-Application": self.__auth.app_key},
                url=self.url,
                params=request_body,
                cert=(self.__auth.crt_file, self.__auth.key_file),
            )
            Log.log_debug(f"Response Message: {r.text}")
            json_resp = json.loads(r.text)

            if json_resp["loginStatus"] != "SUCCESS":
                raise Exception("Login unsuccessful! \n" + r.text)
            else:
                self.__headers.update({"X-Authentication": json_resp["sessionToken"]})
                return json_resp["sessionToken"]

        except Exception as a:
            raise CallException("Failed to authenticate!") from a

    @property
    def url(self):
        return self.__url

    @url.setter
    def url(self, value):
        self.__url = value

    @property
    def auth(self):
        return self.__auth

    @property
    def headers(self):
        return self.__headers

    @headers.setter
    def headers(self, value):
        Log.log_warning(f"Directly setting headers to {str(value)}- is this correct?")
        self.__headers = value

    # If a new auth object is added, it needs to be reflected in the header
    @auth.setter
    def auth(self, value):
        self.__auth = value
        self.__headers.update({"X-Authentication": value.security_token})
