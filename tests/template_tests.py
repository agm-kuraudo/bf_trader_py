import unittest
from output.log import Output as Log
from api.request_body import RequestBody
import json


def is_valid_json(data):
    try:
        # Convert the dictionary to a JSON string
        json_str = json.dumps(data)
        # Attempt to parse the JSON string
        json.loads(json_str)
        return True
    except (json.JSONDecodeError, TypeError):
        return False


class MyTestCase(unittest.TestCase):
    Log.LOG_FILE = False

    def test_templates(self):

        #Cert Auth

        request_handler = RequestBody()
        value=request_handler.populate_template(
            "CertAuth",
            {"<USERID>": "my_userid", "<PWD>": "my_userpwd"},
            add_quotes=True,
        )

        string_value = str(value).replace("'", '"')

        self.assertTrue(is_valid_json(string_value), "CertAuth The JSON string should be valid")
        #listEventTypes

        self.assertTrue(is_valid_json(request_handler.get_template("listEventTypes")), "listEventTypes The JSON string should be valid")

        #listCompetitions

        value = request_handler.populate_template("listCompetitions",
                                                {
                                                    "<list_of_event_ids>":
                                                    ['1']
                                                    }
                                                )
        self.assertTrue(is_valid_json(value), "listCompetitions The JSON string should be valid")

        #listEvents

        value = request_handler.populate_template(
                                "listEvents",
                                        {'<list_of_event_ids>': ['1'], '<list_of_competition_ids>': ['10932509', '228'], '<list_of_market_types>': ['MATCH_ODDS']}
                                        )

        self.assertTrue(is_valid_json(value), "listEvents The JSON string should be valid")

        # listMarketBook
        value = request_handler.populate_template(
            "listMarketBook",
            {'<ListOfMarketIDs>': ['1.238784666']}
        )
        self.assertTrue(is_valid_json(value), "listMarketBook The JSON string should be valid")

        # listRunnerBook
        value = request_handler.populate_template(
            "listRunnerBook",
            {
                "<MarketID>": "dsfg",
                "<RunnerID>": "dsfg"
            },
            add_quotes=True
        )
        self.assertTrue(is_valid_json(value), "listRunnerBook The JSON string should be valid")

        # getAccountFunds
        self.assertTrue(is_valid_json(request_handler.get_template("getAccountFunds")),
                        "getAccountFunds The JSON string should be valid")


if __name__ == '__main__':
    unittest.main()
