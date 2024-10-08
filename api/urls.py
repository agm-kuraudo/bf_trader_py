# This class holds the URLs - I am not going to encapsulate this as accessing/adding/updating values directly
# is ok
class Urls:
    BASE_URL = "https://api.betfair.com"
    SSO_BASE_URL = "https://identitysso-cert.betfair.com"
    CERT_LOGIN = SSO_BASE_URL + "/api/certlogin"
    JSON_RPC_BET = BASE_URL + "/exchange/betting/json-rpc/v1"
    JSON_RPC_ACCOUNT = BASE_URL + "/exchange/account/json-rpc/v1"
