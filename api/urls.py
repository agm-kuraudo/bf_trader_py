from enum import Enum

class Urls(Enum):
    BASE_URL = "https://identitysso-cert.betfair.com"
    CERT_LOGIN = BASE_URL + "/api/certlogin"
    