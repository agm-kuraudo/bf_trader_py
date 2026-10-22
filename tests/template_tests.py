import json
import unittest

from api.request_body import RequestBody
from output.log import Output as Log


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
        # Cert Auth

        request_handler = RequestBody()
        value = request_handler.populate_template(
            "CertAuth",
            {"<USERID>": "my_userid", "<PWD>": "my_userpwd"},
            add_quotes=True,
        )

        string_value = str(value).replace("'", '"')

        self.assertTrue(is_valid_json(string_value), "CertAuth The JSON string should be valid")
        # listEventTypes

        self.assertTrue(
            is_valid_json(request_handler.get_template("listEventTypes")),
            "listEventTypes The JSON string should be valid",
        )

        # listCompetitions

        value = request_handler.populate_template("listCompetitions", {"<list_of_event_ids>": ["1"]})
        self.assertTrue(is_valid_json(value), "listCompetitions The JSON string should be valid")

        # listEvents

        value = request_handler.populate_template(
            "listEvents",
            {
                "<list_of_event_ids>": ["1"],
                "<list_of_competition_ids>": ["10932509", "228"],
                "<list_of_market_types>": ["MATCH_ODDS"],
            },
        )

        self.assertTrue(is_valid_json(value), "listEvents The JSON string should be valid")

        # listMarketBook
        value = request_handler.populate_template("listMarketBook", {"<ListOfMarketIDs>": ["1.238784666"]})
        self.assertTrue(is_valid_json(value), "listMarketBook The JSON string should be valid")

        # listRunnerBook
        value = request_handler.populate_template(
            "listRunnerBook", {"<MarketID>": "dsfg", "<RunnerID>": "dsfg"}, add_quotes=True
        )
        self.assertTrue(is_valid_json(value), "listRunnerBook The JSON string should be valid")

        # getAccountFunds
        self.assertTrue(
            is_valid_json(request_handler.get_template("getAccountFunds")),
            "getAccountFunds The JSON string should be valid",
        )


class SP403MarketIdSubstitution(unittest.TestCase):
    """SP-403 regression: populate_template must render string substitutions as
    quoted JSON strings on the REAL production path (add_quotes=False, as
    update_runner_odds calls it), so a market id is never reparsed by json.loads
    as a float.

    The original crash: "1.263913210" (ends in 0) was spliced in unquoted and
    json.loads reparsed it as the float 1.26391321 - trailing zero dropped, a
    non-existent market - so listRunnerBook returned an empty result and the run
    crashed. The pre-existing test_templates case missed this because it called
    listRunnerBook with add_quotes=True.
    """

    Log.LOG_FILE = False

    def setUp(self):
        self.rb = RequestBody()

    def test_marketid_trailing_zero_renders_as_exact_string(self):
        """The production path (add_quotes=False) keeps marketId an exact string."""
        body = self.rb.populate_template(
            "listRunnerBook",
            {"<MarketID>": "1.263913210", "<RunnerID>": "60298"},
        )
        market_id = body["params"]["marketId"]
        self.assertIsInstance(market_id, str, "marketId must be a string, not a number")
        self.assertEqual(market_id, "1.263913210", "trailing zero must be preserved")

    def test_selectionid_renders_as_string(self):
        """selectionId renders as a string (Betfair accepts string selectionId)."""
        body = self.rb.populate_template(
            "listRunnerBook",
            {"<MarketID>": "1.263913210", "<RunnerID>": "60298"},
        )
        self.assertEqual(body["params"]["selectionId"], "60298")
        self.assertIsInstance(body["params"]["selectionId"], str)

    def test_various_trailing_zero_market_ids_round_trip(self):
        """Any decimal market id string survives substitution exactly."""
        for market_id in ["1.263913210", "1.100000000", "1.263913200", "1.5", "1.263914615"]:
            body = self.rb.populate_template(
                "listRunnerBook",
                {"<MarketID>": market_id, "<RunnerID>": "1"},
            )
            self.assertEqual(body["params"]["marketId"], market_id)
            self.assertIsInstance(body["params"]["marketId"], str)

    def test_list_substitution_still_renders_as_array(self):
        """Preservation: list values (e.g. listMarketBook) stay JSON arrays."""
        body = self.rb.populate_template("listMarketBook", {"<ListOfMarketIDs>": ["1.263913210"]})
        self.assertEqual(body["params"]["marketIds"], ["1.263913210"])
        self.assertIsInstance(body["params"]["marketIds"], list)

    def test_list_events_substitutions_preserved(self):
        """Preservation: listEvents list placeholders stay JSON arrays."""
        body = self.rb.populate_template(
            "listEvents",
            {
                "<list_of_event_ids>": ["1"],
                "<list_of_competition_ids>": ["10932509", "228"],
                "<list_of_market_types>": ["MATCH_ODDS"],
            },
        )
        f = body["params"]["filter"]
        self.assertEqual(f["eventTypeIds"], ["1"])
        self.assertEqual(f["competitionIds"], ["10932509", "228"])
        self.assertEqual(f["marketTypeCodes"], ["MATCH_ODDS"])

    def test_auth_add_quotes_still_quoted_strings(self):
        """Preservation: CertAuth with add_quotes=True still yields quoted strings."""
        body = self.rb.populate_template("CertAuth", {"<USERID>": "me@example.com", "<PWD>": "secret"}, add_quotes=True)
        self.assertEqual(body["username"], "me@example.com")
        self.assertEqual(body["password"], "secret")

    def test_special_characters_in_string_values_are_escaped(self):
        """String values with quotes/backslashes round-trip (json.dumps escaping).

        The old f'"{value}"' path would have produced malformed JSON for such
        values; the json.dumps-based substitution escapes them correctly.
        """
        tricky = 'p@ss"w\\d'  # contains a double quote and a backslash
        body = self.rb.populate_template("CertAuth", {"<USERID>": "u", "<PWD>": tricky}, add_quotes=True)
        self.assertEqual(body["password"], tricky)


if __name__ == "__main__":
    unittest.main()
