from BFDriver import BFDriver
from api.auth.auth_details import Auth, AuthException
from api.auth.dotenv_loader import DotenvLoader, ConfigurationException
from api.call import Call, CallException
from api.http_methods import Methods
from api.urls import Urls
from betfair.BetfairObject import BetfairObjectException
from betfair.competitions import Competition
from betfair.market import Market
from betfair.position import Position
from betfair.event import Event
from betfair.eventType import EventType
from logic.simpleStategy import FromFileStrategy
from output.log import Output
import unittest
from output.dboutput import DBOutputException, DBOutputConnection
import tempfile
import os
import time

class TestBetfairApp(unittest.TestCase):

    Output.LOG_FILE = False
    Output.LOG_CONSOLE = False
    Output.set_log_level(Output.ERROR)

    my_auth = None

    def test_auth(self):
        # Create a temporary .env file with dummy credentials for testing
        with tempfile.NamedTemporaryFile(mode='w', suffix='.env', delete=False) as f:
            f.write("BF_USERNAME=test_user\n")
            f.write("BF_PASSWORD=test_pass\n")
            f.write("BF_APP_KEY=test_app_key\n")
            f.write("BF_CERT_PATH=test_cert.pem\n")
            f.write("BF_KEY_PATH=test_key.pem\n")
            temp_env_path = f.name

        try:
            loader = DotenvLoader(temp_env_path)
            # Can we read credentials from the .env file
            self.assertEqual(loader.get_secret("BF_USERNAME"), "test_user")
            self.assertEqual(loader.get_secret("BF_PASSWORD"), "test_pass")
            self.assertEqual(loader.get_secret("BF_APP_KEY"), "test_app_key")

            # Missing key should raise ConfigurationException
            with self.assertRaises(ConfigurationException):
                loader.get_secret("NON_EXISTENT_KEY")

            # Non-existent .env file should raise ConfigurationException
            with self.assertRaises(ConfigurationException):
                DotenvLoader("/non/existent/path/.env")
        finally:
            os.unlink(temp_env_path)

        with self.assertRaises(AuthException):
            # Invalid non-json response message should cause exception
            my_auth = Auth()
            my_auth.validate_betfair_token("invalid_response")

        # With a valid response indicating there is an invalid session we should see False
        my_auth = Auth()
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

        self.assertEqual(my_call.call(Methods.GET, Urls.JSON_RPC_BET, {}).status_code, 400,
                          "Expect a HTTP 400 response")

    def test_betfair_objects(self):
        my_comp = Competition()
        my_event = Event()
        my_event_type = EventType()
        my_market = Market()
        my_position = Position()


        # Try to build data frames with invalid JSON response - should raise BetfairObjectException
        with self.assertRaises(BetfairObjectException, msg="BetfairObjectException should have been raised for this"):
            my_event_type.build_frame_from_json("Blah")
            my_event_type.build_from_json("{'eventType': {'id': '1', 'name': 'Soccer'}")
            my_comp.build_frame_from_json("blash")
            my_comp.build_from_json("blash")
            my_event.build_frame_from_json("blash")
            my_event.build_from_json("blash")
            my_market.build_frame_from_json("blash")
            my_market.build_from_json("blash")

        # Event Type Build Frame/List from JSON Test
        df, event_type_list = my_event_type.build_frame_from_json('{"jsonrpc":"2.0","result":[{"eventType":{"id":"1","name":"Soccer"},"marketCount":7672},{"eventType":{"id":"2","name":"Tennis"},"marketCount":5730},{"eventType":{"id":"7522","name":"Basketball"},"marketCount":136},{"eventType":{"id":"3","name":"Golf"},"marketCount":12},{"eventType":{"id":"4","name":"Cricket"},"marketCount":152},{"eventType":{"id":"7524","name":"Ice Hockey"},"marketCount":258},{"eventType":{"id":"5","name":"Rugby Union"},"marketCount":101},{"eventType":{"id":"1477","name":"Rugby League"},"marketCount":27},{"eventType":{"id":"6","name":"Boxing"},"marketCount":57},{"eventType":{"id":"7","name":"Horse Racing"},"marketCount":538},{"eventType":{"id":"8","name":"Motor Sport"},"marketCount":6},{"eventType":{"id":"27454571","name":"Esports"},"marketCount":50},{"eventType":{"id":"10","name":"Special Bets"},"marketCount":7},{"eventType":{"id":"11","name":"Cycling"},"marketCount":1},{"eventType":{"id":"61420","name":"Australian Rules"},"marketCount":108},{"eventType":{"id":"468328","name":"Handball"},"marketCount":96},{"eventType":{"id":"3503","name":"Darts"},"marketCount":1},{"eventType":{"id":"2152880","name":"Gaelic Games"},"marketCount":2},{"eventType":{"id":"26420387","name":"Mixed Martial Arts"},"marketCount":17},{"eventType":{"id":"4339","name":"Greyhound Racing"},"marketCount":312},{"eventType":{"id":"2378961","name":"Politics"},"marketCount":79},{"eventType":{"id":"6422","name":"Snooker"},"marketCount":32},{"eventType":{"id":"7511","name":"Baseball"},"marketCount":31},{"eventType":{"id":"6423","name":"American Football"},"marketCount":130}],"id":1}')
        self.assertEqual(len(event_type_list), 24, "Supplied JSON should have created 24 events")
        self.assertIsInstance(event_type_list[0], EventType, "Objects in Event Type List should be Event Types")

        # Event Type - create single event based on JSON
        self.assertIsInstance(my_event_type.build_from_json(
            "{'eventType': {'id': '1', 'name': 'Soccer'}, 'marketCount': 7672}"),
            EventType, "Event Type object not create via build from json")

        self.assertEqual(my_event_type.build_from_json(
            "{'eventType': {'id': '1', 'name': 'Soccer'}, 'marketCount': 7672}").name,
                         "Soccer", "EventType created should be soccer")

        # Competition Object - create from frame
        df, comp_list = my_comp.build_frame_from_json('{"jsonrpc":"2.0","result":[{"competition":{"id":"12199359",'
        '"name":"N Premiership"},"marketCount":144,"competitionRegion":"GBR"},'
        '{"competition":{"id":"3172302","name":"Brazilian"},"marketCount":24,"competitionRegion":"BRA"}],"id":1}')

        self.assertEqual(len(comp_list), 2, "Supplied JSON should have created 2 comps")
        self.assertIsInstance(comp_list[0], Competition, "Objects in Competition Type List should be Competition Types")

        # Competition Type - create single Competition based on JSON
        self.assertIsInstance(my_comp.build_from_json(
            "{'competition': {'id': '12199359', 'name': 'Premiership'}, 'marketCount': 144, 'competitionRegion': 'GBR'}"),
            Competition, "Competition object not created via build from json")

        self.assertEqual(my_comp.build_from_json(
            "{'competition': {'id': '12199359', 'name': 'Premiership'}, 'marketCount': 144, 'competitionRegion': 'GBR'}").name,
                         "Premiership", "Competition created should be Premiership")

        #Event object - create frame from json
        df, event_list = my_event.build_frame_from_json('{"jsonrpc":"2.0","result":[{"event":{"id":"33589826","name":"Newcastle v Man City","countryCode":"GB","timezone":"GMT","openDate":"2024-09-28T11:30:00.000Z"},"marketCount":1},{"event":{"id":"33589827","name":"Nottm Forest v Fulham","countryCode":"GB","timezone":"GMT","openDate":"2024-09-28T14:00:00.000Z"},"marketCount":1}],"id":1}')

        self.assertEqual(len(event_list), 2, "Supplied JSON should have created 2 Events")
        self.assertIsInstance(event_list[0], Event, "Objects in Event List should be Event Types")

        #Event object - create from json
        self.assertIsInstance(my_event.build_from_json("{'event': {'id': '33589826', 'name': 'Newcastle v Man City', "
                "'countryCode': 'GB', 'timezone': 'GMT', 'openDate': '2024-09-28T11:30:00.000Z'}, 'marketCount': 1}"),
                              Event, "Event object not created via build from json")

        self.assertEqual(my_event.build_from_json(
            "{'event': {'id': '33589826', 'name': 'Newcastle v Man City', "
                "'countryCode': 'GB', 'timezone': 'GMT', 'openDate': '2024-09-28T11:30:00.000Z'}, 'marketCount': 1}").name,
                         "Newcastle v Man City", "Competition created should be Newcastle v Man City")

        #Market Object Type
        df, market_list = my_market.build_frame_from_json('{"jsonrpc":"2.0","result":[{"marketId":"1.232498611","marketName":"Match Odds","description":{"persistenceEnabled":true,"bspMarket":true,"marketTime":"2024-09-17T16:45:00.000Z","suspendTime":"2024-09-17T16:45:00.000Z","bettingType":"ODDS","turnInPlayEnabled":true,"marketType":"MATCH_ODDS","regulator":"MALTA LOTTERIES AND GAMBLING AUTHORITY","marketBaseRate":5.0,"discountAllowed":false,"wallet":"UK wallet","rules":"<!--Football - Match Odds --><br>Predict the result of this match.<br> All bets apply to Full Time according to the match officials, plus any stoppage time. Extra-time/penalty shoot-outs are not included.<br><br></b>For further information please see <a href=http://content.betfair.com/aboutus/content.asp?sWhichKey=Rules%20and%20Regulations#undefined.do style=color:0163ad; text-decoration: underline; target=_blank>Rules & Regs<br><br>\n","rulesHasDate":true,"priceLadderDescription":{"type":"CLASSIC"}},"totalMatched":12303.74,"runners":[{"selectionId":65778,"runnerName":"Young Boys","handicap":0.0,"sortPriority":1,"metadata":{"runnerId":"65778"}},{"selectionId":63908,"runnerName":"Aston Villa","handicap":0.0,"sortPriority":2,"metadata":{"runnerId":"63908"}},{"selectionId":58805,"runnerName":"The Draw","handicap":0.0,"sortPriority":3,"metadata":{"runnerId":"58805"}}]}],"id":1}')

        self.assertEqual(len(market_list), 1, "Supplied JSON should have created 1 Market")
        self.assertIsInstance(market_list[0], Market, "Objects in Market List should be Market Types")

        #Market object - create from json
        self.assertIsInstance(my_market.build_from_json("{'marketId': '1.232498611', 'marketName': 'Match Odds', 'description': {'persistenceEnabled': True, 'bspMarket': True, 'marketTime': '2024-09-17T16:45:00.000Z', 'suspendTime': '2024-09-17T16:45:00.000Z', 'bettingType': 'ODDS', 'turnInPlayEnabled': True, 'marketType': 'MATCH_ODDS', 'regulator': 'MALTA LOTTERIES AND GAMBLING AUTHORITY', 'marketBaseRate': 5.0, 'discountAllowed': False, 'wallet': 'UK wallet', 'rules': '<!--Football - Match Odds --><br>Predict the result of this match.<br> All bets apply to Full Time according to the match officials, plus any stoppage time. Extra-time/penalty shoot-outs are not included.<br><br></b>For further information please see <a href=http://content.betfair.com/aboutus/content.asp?sWhichKey=Rules%20and%20Regulations#undefined.do style=color:0163ad; text-decoration: underline; target=_blank>Rules & Regs<br><br>\n', 'rulesHasDate': True, 'priceLadderDescription': {'type': 'CLASSIC'}}, 'totalMatched': 12303.74, 'runners': [{'selectionId': 65778, 'runnerName': 'Young Boys', 'handicap': 0.0, 'sortPriority': 1, 'metadata': {'runnerId': '65778'}}, {'selectionId': 63908, 'runnerName': 'Aston Villa', 'handicap': 0.0, 'sortPriority': 2, 'metadata': {'runnerId': '63908'}}, {'selectionId': 58805, 'runnerName': 'The Draw', 'handicap': 0.0, 'sortPriority': 3, 'metadata': {'runnerId': '58805'}}]}"),
                              Market, "Market object not created via build from json")

        self.assertEqual(my_market.build_from_json(
            "{'marketId': '1.232498611', 'marketName': 'Match Odds', 'description': {'persistenceEnabled': True, 'bspMarket': True, 'marketTime': '2024-09-17T16:45:00.000Z', 'suspendTime': '2024-09-17T16:45:00.000Z', 'bettingType': 'ODDS', 'turnInPlayEnabled': True, 'marketType': 'MATCH_ODDS', 'regulator': 'MALTA LOTTERIES AND GAMBLING AUTHORITY', 'marketBaseRate': 5.0, 'discountAllowed': False, 'wallet': 'UK wallet', 'rules': '<!--Football - Match Odds --><br>Predict the result of this match.<br> All bets apply to Full Time according to the match officials, plus any stoppage time. Extra-time/penalty shoot-outs are not included.<br><br></b>For further information please see <a href=http://content.betfair.com/aboutus/content.asp?sWhichKey=Rules%20and%20Regulations#undefined.do style=color:0163ad; text-decoration: underline; target=_blank>Rules & Regs<br><br>\n', 'rulesHasDate': True, 'priceLadderDescription': {'type': 'CLASSIC'}}, 'totalMatched': 12303.74, 'runners': [{'selectionId': 65778, 'runnerName': 'Young Boys', 'handicap': 0.0, 'sortPriority': 1, 'metadata': {'runnerId': '65778'}}, {'selectionId': 63908, 'runnerName': 'Aston Villa', 'handicap': 0.0, 'sortPriority': 2, 'metadata': {'runnerId': '63908'}}, {'selectionId': 58805, 'runnerName': 'The Draw', 'handicap': 0.0, 'sortPriority': 3, 'metadata': {'runnerId': '58805'}}]}").name,
                         "Match Odds", "Market object created should be Match Odds")

    # Requires .env at project root
    def test_db_connection(self):
        bf = BFDriver(FromFileStrategy(), Output.INFO)
        self.assertIsInstance(bf, BFDriver, "BFDriver object should be created")

        db_details_string = bf.get_local_db_details()

        self.assertTrue(db_details_string.get("host") is not None, "Host should be returned as part of db connection string")
        self.assertTrue(db_details_string.get("port") is not None,
                        "Host should be returned as part of db connection string")
        self.assertTrue(db_details_string.get("db_name") is not None,
                        "Host should be returned as part of db connection string")
        db_connection = DBOutputConnection()
        self.assertIsInstance(db_connection, DBOutputConnection, "DBOutputConnection object should be created")

        db_connection.open_connection(db_details_string)

        db_connection.db_write_log("Hello")
        self.assertEqual(len(db_connection.db_read(f"select * from bf.log_file where id='{db_connection.run_id}'")[0]), 3, "Should be 3 columns returned")

        db_connection.close()

    # Requires .env at project root
    def test_db_object_ids(self):
        bf = BFDriver(FromFileStrategy(), Output.ERROR)
        my_db = DBOutputConnection()
        db_details_string = bf.get_local_db_details()

        my_db.open_connection(db_details_string)

        # Firstly make sure there is no existing unit type objects
        my_db.db_delete('bf.betfair_object_ids', "object_type='unit-test-type'")

        my_db.db_write_object_id(object_type="unit-test-type", object_name="name", object_id="12345")

        first_update = my_db.db_read(f"select * from bf.betfair_object_ids where object_type='unit-test-type'")

        self.assertTrue(len(first_update) > 0, "There should be at least one row returned")

        #print(my_db.db_read(f"select * from bf.betfair_object_ids where object_type='unit-test-type'"))
        time.sleep(1)
        # This should do nothing as the object already exists. The timestamp should be not updated
        my_db.db_write_object_id(object_type="unit-test-type", object_name="name", object_id="12345")
        second_update = my_db.db_read(f"select * from bf.betfair_object_ids where object_type='unit-test-type'")

        self.assertEqual(first_update[0][3], second_update[0][3], "Unnecessary update made for same value")

        # Now add a new id, this should run the update statement
        my_db.db_write_object_id(object_type="unit-test-type", object_name="name", object_id="54321")
        third_update = my_db.db_read(f"select * from bf.betfair_object_ids where object_type='unit-test-type'")

        self.assertEqual(third_update[0][2], 54321, "Event ID was not updated")

        my_db.close()

if __name__ == '__main__':
    unittest.main()
