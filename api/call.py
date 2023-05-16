import json
import requests
from api.auth.auth_details import Auth

class CallException(Exception):
    pass

#TODO - The call object probably shouldnt be initialisd with the URL etc as we could have one object that makes all the calls?
class Call():
    def __init__(self, url, http_method, auth):
        self.url = url
        self.__auth = auth

    def call(self, headers, RequestBody):
        #TODO: This should handle normal request params and JSON type messages
        #TODO - should handle all HTTP post types (Also shold should be ENUM http methods)
        r = requests.post(headers={"X-Application": "wId8CbMYLNRjCwWm"}, url=self.url, params=RequestBody, cert=("D:/OneDrive - XHT/06_Projects/bookmaking/API_Auth/client-2048.crt", "D:/OneDrive - XHT/06_Projects/bookmaking/API_Auth/client-2048.key"))
        requests.Request
        return r.text

    '''This is a special isntance of "call" design to authenticate users with a certificate and capture the login session'''
    def callAuth(self, RequestBody):
        try:
            print({"X-Application": self.__auth.appKey})
            print(self.url)
            print(Auth.crtfile)
            print(Auth.keyfile)

            r = requests.post(headers={"X-Application": Auth.appKey}, url=self.url, params=RequestBody, cert=(Auth.crtfile, Auth.keyfile))
            json_resp = json.loads(r.text)

            if json_resp['loginStatus'] != 'SUCCESS':
                raise Exception("Login unsuccessful! \n" + r.text)
            else:
                return json_resp['sessionToken']

        except Exception as a:
            raise CallException("Failed to autheticate!") from a
