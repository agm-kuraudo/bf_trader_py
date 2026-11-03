import json
import time

import requests
from requests import Response

import decorators.log_attrib
from api.http_methods import Methods
from api.urls import Urls
from output.log import Output as Log

# SP-487: bounded retry-with-backoff around the HTTP request so a single
# TRANSIENT transport failure (e.g. a one-in-a-thousand DNS blip:
# "Temporary failure in name resolution", which surfaces as
# requests.exceptions.ConnectionError) does not take down an otherwise healthy
# long-running monitor loop. Only transient transport errors are retried; a
# real API response (any HTTP status) or a non-transport error (e.g. a bad URL)
# is NOT retried. Defaults are module-level constants (mirroring the
# scripts/check_quality.py CONNECT_ATTEMPTS precedent) and are overridable via
# the Call constructor so tests can drive them fast without real sleeping.
RETRY_ATTEMPTS = 4  # total attempts, i.e. the first try + up to 3 retries
RETRY_BACKOFF_BASE_S = 0.5  # exponential backoff: 0.5s, 1s, 2s between attempts
REQUEST_TIMEOUT_S = 30  # per-request timeout so a hung connection cannot stall forever

# Transient transport errors worth retrying. ConnectionError covers DNS
# resolution failures (NameResolutionError -> socket.gaierror), connection
# resets and refused connections; Timeout covers connect/read timeouts.
TRANSIENT_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


class CallException(Exception):
    pass


class Call:
    @decorators.log_attrib.dump_args
    def __init__(
        self,
        auth=None,
        retry_attempts: int = RETRY_ATTEMPTS,
        retry_backoff_base_s: float = RETRY_BACKOFF_BASE_S,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        sleep=time.sleep,
    ):
        """
        Call init function, sets up basic variables, requires an auth object with credentials
        :param auth:
        :param retry_attempts: total attempts for a transient-failing request (first try + retries)
        :param retry_backoff_base_s: base for exponential backoff between retries
        :param request_timeout_s: per-request timeout passed to requests
        :param sleep: sleep function (injectable so tests need not wait)
        """
        self.__url = None
        self.__headers = {}
        self.__retry_attempts = max(1, int(retry_attempts))
        self.__retry_backoff_base_s = retry_backoff_base_s
        self.__request_timeout_s = request_timeout_s
        self.__sleep = sleep

        if auth is not None:
            self.__auth = auth
            self.__headers["X-Application"] = auth.app_key

        Log.log_debug("Call object instantiated")

    def call(self, http_method: Methods, url: Urls, request_body: dict) -> Response:
        """
        This method makes a HTTP request to a URL. It currently only needs to do POST requests.

        Transient transport failures (see TRANSIENT_ERRORS) are retried up to
        ``retry_attempts`` times with exponential backoff (SP-487). A real HTTP
        response is returned as-is regardless of status code, and a non-transient
        error raises CallException without retrying.

        :param http_method: GET/POST/PUT/DELETE etc.
        :param url: The URL of the end point
        :param request_body: The body of the HTTP message
        :return: Returns the "Response" object (part of the requests' module)
        """
        self.url = url
        Log.log_debug(f"Making request to {url}")
        Log.log_debug(f"headers: {self.__headers}, RequestBody: {request_body}")

        last_error = None
        for attempt in range(1, self.__retry_attempts + 1):
            try:
                r = requests.request(
                    method=str(http_method).replace("Methods.", ""),
                    headers=self.__headers,
                    url=self.url,
                    json=request_body,
                    timeout=self.__request_timeout_s,
                )
                Log.log_debug(r.text)
                return r
            except TRANSIENT_ERRORS as e:
                # Transient transport failure: retry with backoff unless this was
                # the last attempt, in which case fall through and raise below.
                last_error = e
                if attempt < self.__retry_attempts:
                    backoff = self.__retry_backoff_base_s * (2 ** (attempt - 1))
                    Log.log_warning(
                        f"Transient transport error on attempt {attempt}/{self.__retry_attempts} "
                        f"calling {self.url}: {e}. Retrying in {backoff}s."
                    )
                    self.__sleep(backoff)
                    continue
                Log.log_warning(
                    f"Transient transport error persisted after {self.__retry_attempts} attempts "
                    f"calling {self.url}: {e}."
                )
            except Exception as e:
                # Non-transient error (e.g. a malformed URL): do not retry.
                raise CallException("Unexpected Exception during call method") from e

        # Exhausted all retries on transient transport errors.
        raise CallException(
            f"Transient transport error persisted after {self.__retry_attempts} attempts"
        ) from last_error

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
