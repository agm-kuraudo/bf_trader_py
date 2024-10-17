import requests

from api.http_methods import Methods
from api.urls import Urls

headers = {"X-Application": 'wId8CbMYLNRjCwWm',
           'X-Authentication': 'VQHJF1aPUU/3xog7irESPICjBOFYS1h3LCEablC/f6c='}

request_body = {'jsonrpc': '2.0', 'method': 'AccountAPING/v1.0/getAccountFunds', 'params': {'wallet': 'UK'}, 'id': 1}

#r = requests.request(method="POST", headers=headers, url=Urls.JSON_RPC_ACCOUNT, json=request_body)

def my_function(http_methods: Methods):
    print(http_methods)


print(str(Methods.POST).replace("Methods.", ""))