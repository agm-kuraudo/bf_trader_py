from api.auth.auth_details import Auth, AuthException
from api.auth.vault.vault_reader import VaultReader, VaultException
from api.call import Call, CallException
from api.http_methods import Methods
from api.urls import Urls
from betfair.BetfairObject import BetfairObjectException
from betfair.competitions import Competition
from betfair.market import Market
from betfair.position import Position
from betfair.event import Event
from betfair.eventType import EventType
from output import Output
import unittest


class TestBetfairApp(unittest.TestCase):

    Output.LOG_FILE = False
    Output.LOG_CONSOLE = False

    my_auth = None

    def test_vault(self):

        #With an invalid address an exception should be raised
        with self.assertRaises(VaultException):
            VaultReader("192.168.0.7:8888")

        my_vault = VaultReader()
        # Can we authenticate to vault?
        self.assertTrue(my_vault.client.is_authenticated(), "Vault: cannot authenticate")
        # Can we read a secret from the vault
        self.assertTrue(len(str(my_vault.read_secret("bf"))) > 10, "Vault: cannot read secrets")

    def test_auth(self):
        my_auth = Auth()
        #Can we read our BF credentials from vault
        self.assertIsNotNone(my_auth.get_credentials_from_vault(),
                             "Auth Class cannot retrieve sso token from vault")

        with self.assertRaises(AuthException):
            # Invalid non-json response message should cause exception
            my_auth.validate_betfair_token("invalid_response")

        # With a valid response indicating there is an invalid session we should see False
        self.assertFalse(my_auth.validate_betfair_token(
            {
                'jsonrpc': '2.0',
                'error':
                    {
                        'code': -32099,
                        'message': 'AANGX-0002',
                        'data':
                            {
                                'exceptionname': 'AccountAPINGException',
                                'AccountAPINGException':
                                    {
                                        'requestUUID': 'null',
                                        'errorCode': 'INVALID_SESSION_INFORMATION',
                                        'errorDetails': ''
                                    }
                            }
                    },
                'id': 1
            }))

    def test_call(self):
        my_call = Call()

        with self.assertRaises(CallException):
            my_call.call(Methods.GET, "a.b.c", {})

        self.assertEqual(my_call.call(Methods.GET, Urls.BASE_URL, {}).status_code, 400,
                          "Expect a HTTP 400 response")

    def test_betfair_objects(self):
        my_comp = Competition()
        my_event = Event()
        my_event_type = EventType()
        my_market = Market()
        my_position = Position()

        with self.assertRaises(BetfairObjectException):
            my_event_type.build_frame_from_json("Blash")

        df, event_type_list = my_event_type.build_frame_from_json('{"jsonrpc":"2.0","result":[{"eventType":{"id":"1","name":"Soccer"},"marketCount":7672},{"eventType":{"id":"2","name":"Tennis"},"marketCount":5730},{"eventType":{"id":"7522","name":"Basketball"},"marketCount":136},{"eventType":{"id":"3","name":"Golf"},"marketCount":12},{"eventType":{"id":"4","name":"Cricket"},"marketCount":152},{"eventType":{"id":"7524","name":"Ice Hockey"},"marketCount":258},{"eventType":{"id":"5","name":"Rugby Union"},"marketCount":101},{"eventType":{"id":"1477","name":"Rugby League"},"marketCount":27},{"eventType":{"id":"6","name":"Boxing"},"marketCount":57},{"eventType":{"id":"7","name":"Horse Racing"},"marketCount":538},{"eventType":{"id":"8","name":"Motor Sport"},"marketCount":6},{"eventType":{"id":"27454571","name":"Esports"},"marketCount":50},{"eventType":{"id":"10","name":"Special Bets"},"marketCount":7},{"eventType":{"id":"11","name":"Cycling"},"marketCount":1},{"eventType":{"id":"61420","name":"Australian Rules"},"marketCount":108},{"eventType":{"id":"468328","name":"Handball"},"marketCount":96},{"eventType":{"id":"3503","name":"Darts"},"marketCount":1},{"eventType":{"id":"2152880","name":"Gaelic Games"},"marketCount":2},{"eventType":{"id":"26420387","name":"Mixed Martial Arts"},"marketCount":17},{"eventType":{"id":"4339","name":"Greyhound Racing"},"marketCount":312},{"eventType":{"id":"2378961","name":"Politics"},"marketCount":79},{"eventType":{"id":"6422","name":"Snooker"},"marketCount":32},{"eventType":{"id":"7511","name":"Baseball"},"marketCount":31},{"eventType":{"id":"6423","name":"American Football"},"marketCount":130}],"id":1}')

        self.assertEqual(len(event_type_list), 24, "Supplied JSON should have created 24 events")
        self.assertIsInstance(event_type_list[0], EventType, "Objects in Event Type List should be Event Types")

if __name__ == '__main__':
    unittest.main()