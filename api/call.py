import requests
import api
from api.api import Api

class Call(Api):
    def __init__(self, url, http_method, auth):
        self.url = url
        super().__init__(url, http_method)
        self.__auth = auth

    def call(self, RequestBody):
        #TODO: This should handle normal request params and JSON type messages
        #TODO - should handle all HTTP post types
        r = requests.post(headers={"X-Application": "wId8CbMYLNRjCwWm"}, url=self.url, params=RequestBody, cert=("D:/OneDrive - XHT/06_Projects/bookmaking/API_Auth/client-2048.crt", "D:/OneDrive - XHT/06_Projects/bookmaking/API_Auth/client-2048.key"))
        requests.Request
        return r.text


