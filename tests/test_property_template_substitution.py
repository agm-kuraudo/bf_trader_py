"""
Property-based tests for SP-403: RequestBody.populate_template string-substitution
correctness.

Design Property 1 (Bug Condition): a string substitution value must survive as the
exact original string in the rendered request body - it must never be reparsed by
json.loads as a number. The live outage was caused by a market id "1.263913210"
(ending in 0) being spliced in unquoted and reparsed as the float 1.26391321.

Also covers the preservation properties: list values always render as JSON arrays
regardless of add_quotes.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings
from hypothesis import strategies as st

from api.request_body import RequestBody
from output.log import Output as Log

Log.LOG_FILE = False


# Betfair market ids look like "1.<digits>"; the bug is specifically about
# decimal-looking strings (and trailing zeros). We also throw in arbitrary text
# to prove the escaping/round-trip holds for any string.
market_id_strategy = st.builds(lambda n: f"1.{n:09d}", st.integers(min_value=0, max_value=999999999))


class TestProperty1StringSubstitutionRoundTrip:
    """Property 1: string substitutions round-trip as the exact input string.

    **Validates: Requirements 2.1, 2.2**
    """

    @given(market_id=market_id_strategy, selection_id=st.integers(min_value=1, max_value=99999999))
    @settings(max_examples=300)
    def test_listrunnerbook_marketid_round_trips_exactly(self, market_id, selection_id):
        rb = RequestBody()
        # The REAL production path: add_quotes defaults to False (as update_runner_odds calls it).
        body = rb.populate_template(
            "listRunnerBook",
            {"<MarketID>": market_id, "<RunnerID>": str(selection_id)},
        )
        assert body["params"]["marketId"] == market_id
        assert isinstance(body["params"]["marketId"], str)
        assert body["params"]["selectionId"] == str(selection_id)
        assert isinstance(body["params"]["selectionId"], str)

    @given(value=st.text(min_size=0, max_size=40))
    @settings(max_examples=300)
    def test_arbitrary_string_values_round_trip_with_escaping(self, value):
        """Any string value (incl. quotes/backslashes/control chars) round-trips exactly."""
        rb = RequestBody()
        body = rb.populate_template("listRunnerBook", {"<MarketID>": value, "<RunnerID>": "1"})
        assert body["params"]["marketId"] == value
        assert isinstance(body["params"]["marketId"], str)


class TestProperty3ListSubstitutionPreserved:
    """Property 3 (preservation): list values always render as JSON arrays.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(market_ids=st.lists(market_id_strategy, min_size=1, max_size=5))
    @settings(max_examples=200)
    def test_listmarketbook_ids_render_as_array(self, market_ids):
        rb = RequestBody()
        body = rb.populate_template("listMarketBook", {"<ListOfMarketIDs>": market_ids})
        assert body["params"]["marketIds"] == market_ids
        assert isinstance(body["params"]["marketIds"], list)
