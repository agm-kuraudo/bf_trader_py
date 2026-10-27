"""SP-400: validate Over/Under goals market-type codes against the live Betfair API.

Read-only. No bets, no DB writes. Reuses the existing BFDriver auth plumbing.

Does two things, both authoritative sources of the real codes:

  1. listMarketTypes (filtered to Soccer + EPL/UCL) — Betfair's own enumeration of
     the marketType codes available for those competitions right now. This is the
     ground truth for "what is the Over/Under 2.5 code actually called".

  2. listMarketCatalogue for the nearest tracked fixture with NO market-type filter
     — shows the real marketName / marketType pairs on an actual event, so we can
     see e.g. "Over/Under 2.5 Goals" -> OVER_UNDER_25 and confirm the two runners.

Run from the project root with the venv Python:
    & "d:\\projects\\bf_trader_py\\.venv\\Scripts\\python.exe" scripts\\validate_market_types.py
"""

from api.http_methods import Methods
from api.urls import Urls
from BFDriver import BFDriver
from logic.simpleStategy import FromFileStrategy
from output.log import Output as Log

BF = BFDriver(FromFileStrategy(), Log.INFO)

# Register the extra read-only templates we need for validation (not part of the
# normal capture flow, so added here rather than in RequestBody).
BF.request_body_obj.set_template(
    "listMarketTypes",
    {
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketTypes",
        "params": {"filter": {"eventTypeIds": "<list_of_event_ids>", "competitionIds": "<list_of_competition_ids>"}},
        "id": 1,
    },
)
# marketCatalogue WITHOUT a market-type filter, to see every market on a real fixture.
BF.request_body_obj.set_template(
    "marketCatalogueAll",
    {
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": {
            "filter": {"eventIds": "<list_of_event_ids>"},
            "maxResults": "100",
            "marketProjection": ["MARKET_DESCRIPTION", "RUNNER_DESCRIPTION"],
            "sort": "FIRST_TO_START",
        },
        "id": 1,
    },
)


def main():
    if not BF.get_token():
        raise SystemExit("Failed to authenticate to Betfair API — check .env creds.")
    print("Authenticated OK\n")

    # Resolve Soccer event type + configured competitions (reuses BFDriver filters).
    event_types = BF.get_event_types()
    if event_types == 0:
        raise SystemExit("No matching event types (check EVENTS in strategy.yaml).")
    comps = BF.get_competition_ids()
    if comps == 0:
        raise SystemExit("No matching competitions (check COMPETITIONS in strategy.yaml).")

    event_type_ids = [et.id for et in event_types]
    competition_ids = [c.id for c in comps]

    # ---- 1) Authoritative list of market-type codes for Soccer + EPL/UCL ----
    print("=== listMarketTypes (Soccer + EPL/UCL) ===")
    resp = BF.call_obj.call(
        http_method=Methods.POST,
        url=Urls.JSON_RPC_BET,
        request_body=BF.request_body_obj.populate_template(
            "listMarketTypes",
            {"<list_of_event_ids>": event_type_ids, "<list_of_competition_ids>": competition_ids},
        ),
    )
    result = resp.json().get("result", [])
    over_under = []
    for row in sorted(result, key=lambda r: r.get("marketType", "")):
        mt = row.get("marketType", "")
        count = row.get("marketCount", "")
        print(f"  {mt}  (marketCount={count})")
        if mt.startswith("OVER_UNDER"):
            over_under.append(mt)
    print(f"\n  Over/Under codes present: {over_under or '(none found!)'}\n")

    # ---- 2) Real market names/types on the nearest fixture ----
    events = BF.get_events()
    if not events:
        print("No events within the configured day window — skipping per-fixture dump.")
        print("(listMarketTypes above is still authoritative for the codes.)")
        return

    event = events[0]
    print(f"=== listMarketCatalogue (no type filter) for: {event.name} ({event.id}) ===")
    resp = BF.call_obj.call(
        http_method=Methods.POST,
        url=Urls.JSON_RPC_BET,
        request_body=BF.request_body_obj.populate_template("marketCatalogueAll", {"<list_of_event_ids>": [event.id]}),
    )
    markets = resp.json().get("result", [])
    for m in markets:
        mt = m.get("description", {}).get("marketType", "?")
        name = m.get("marketName", "?")
        runners = ", ".join(r.get("runnerName", "?") for r in m.get("runners", []))
        flag = "  <-- OVER/UNDER" if mt.startswith("OVER_UNDER") else ""
        print(f"  {mt:18} | {name:28} | runners: {runners}{flag}")


if __name__ == "__main__":
    main()
