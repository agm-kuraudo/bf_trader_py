import time
from datetime import UTC, datetime, timedelta

import api.auth.auth_details as bf_auth
from api.http_methods import Methods
from api.urls import Urls
from BFDriver import BFDriver
from logic.simpleStategy import DefaultStrategy, FromFileStrategy, LoopState, decide_next_action
from output.dboutput import DBOutputConnection
from output.log import Output as Log


class MonitorServiceException(Exception):
    pass


# Sentinel distinguishing "no scalar totalMatched was supplied" from a genuine
# source value of 0. A plain default of None cannot be used because None is also
# how Betfair can report an absent runner-level totalMatched, and we must not
# write a null totalMatched into the blob (Req 2.6); a dedicated sentinel lets a
# real 0 be set (Req 2.5) while a missing figure is left unset.
_NO_TOTAL_MATCHED = object()


def build_odds_blob(ex: dict | None, total_matched=_NO_TOTAL_MATCHED) -> dict:
    """Build one runner's Odds_Blob dict from its ``ex`` dict + matched scalar.

    Pure function (no API/DB calls). Starts from the full ``ex`` object so the
    back/lay ladders — and any ``tradedVolume`` ladder carried from ``EX_TRADED``
    — flow through untouched, then conditionally sets a scalar ``totalMatched``
    from the runner-level figure, following the SP-384 matched-volume mapping
    (design "Matched-Volume Mapping" case table):

    | # | scalar       | ladder | blob result                         | Req        |
    |---|--------------|--------|-------------------------------------|------------|
    | 1 | yes (>0)     | no     | ``totalMatched = s``                | 2.1, 2.2   |
    | 2 | yes (>0)     | yes    | ``totalMatched = s`` + keep ladder  | 2.3        |
    | 3 | no           | yes    | keep ladder, no ``totalMatched``    | 2.4        |
    | 4 | yes (== 0)   | no     | ``totalMatched = 0``                | 2.5        |
    | 5 | yes (== 0)   | yes    | ``totalMatched = 0`` + keep ladder  | 2.5, 2.3   |
    | 6 | no           | no     | neither key — ladders only          | 2.6        |

    The scalar is set only when a source value is *present* — the explicit
    ``_NO_TOTAL_MATCHED`` sentinel guard means a genuine ``0`` is set (Req 2.5)
    while a missing figure is left unset (Req 2.6), so the two are distinguished
    at the source. ``tradedVolume`` is whatever ``ex`` already carried; this
    helper never fabricates an empty ``tradedVolume`` list and never writes a
    null ``totalMatched`` (Req 2.6). The result always keeps both
    ``availableToBack`` and ``availableToLay`` so the stored blob stays parseable
    by the unchanged ``logic.quality_checks.parse_odds`` (Req 2.8).

    Args:
        ex: The runner's ``ex`` dict from ``listMarketBook`` (may be ``None`` or
            empty); ``availableToBack`` / ``availableToLay`` default to empty
            ladders when absent so the blob remains parseable.
        total_matched: The runner-level ``totalMatched`` scalar. Omit (leave as
            the ``_NO_TOTAL_MATCHED`` sentinel) or pass ``None`` when the source
            carries no scalar; pass the number (including ``0``) when present.

    Returns:
        The final Odds_Blob dict to be rendered and stored.
    """
    # Copy so the caller's source dict is not mutated (pure).
    blob = dict(ex) if ex else {}

    # Guarantee both ladders are present so parse_odds accepts the blob even for
    # a runner with no/empty ex (Req 1.3, 2.8).
    blob.setdefault("availableToBack", [])
    blob.setdefault("availableToLay", [])

    # Presence guard (Req 2.5 vs 2.6): set the scalar only when a source value is
    # actually supplied. A genuine 0 is set; a missing figure (sentinel or None)
    # is left unset. The ladder, if any, is already in `blob` from `ex`.
    if total_matched is not _NO_TOTAL_MATCHED and total_matched is not None:
        blob["totalMatched"] = total_matched

    return blob


def render_odds_blob(blob: dict) -> str:
    """Render an Odds_Blob dict to the stored ``str(dict)`` string form.

    Pure function. The stored contract is a Python dict ``repr`` (``str(dict)``),
    NOT JSON — ``logic.quality_checks.parse_odds`` reads it with
    ``ast.literal_eval``. Rendering with ``str`` is the exact round-trip partner
    of that read (``quality_checks.serialize_odds`` is likewise ``str(parsed)``),
    so for any blob built from JSON-like scalar/list values,
    ``ast.literal_eval(render_odds_blob(blob))`` reconstructs an equal dict.

    Args:
        blob: The Odds_Blob dict (typically from :func:`build_odds_blob`).

    Returns:
        The ``str(dict)`` rendering stored in ``bf.market_table.odds``.
    """
    return str(blob)


def assemble_snapshot_rows(result, market_id, capture_ts) -> list[tuple]:
    """Assemble the coherent per-market snapshot rows from one ``listMarketBook`` result.

    Pure function (no API/DB calls). Given the already-parsed ``listMarketBook``
    ``result`` for ONE market, the ``market_id``, and ONE shared ``capture_ts``,
    returns the ordered list of ``(capture_ts, market_id, runner_id, odds_str)``
    tuples — exactly one row per returned runner, in the order Betfair returned
    them. The caller is responsible for computing the single ``capture_ts`` (one
    ``datetime.now(UTC)`` per market) and for empty/error handling *before*
    calling this helper (see design "Per-market snapshot flow"); this function
    assumes a non-empty result and only guards a runner that is missing ``ex``.

    Every row binds the SAME ``capture_ts`` object, so all N rows of the snapshot
    share one identical timestamp (Req 1.2, 1.4). One row is produced per runner
    in ``result[0]["runners"]`` using ``runner["selectionId"]`` as ``runner_id``
    — no runner omitted, no duplicate (Req 1.3). This holds trivially for N == 1
    (single-runner market → single row). A runner with no/empty ``ex`` still
    produces a row: :func:`build_odds_blob` backfills empty ladders, so the blob
    stays parseable and the row surfaces null prices downstream (Req 1.3, 2.6).

    The per-runner Odds_Blob is built by reusing the task-1 helpers unchanged:
    :func:`build_odds_blob` (from the runner's ``ex`` plus its runner-level
    ``totalMatched`` scalar, passed only when present via the ``_NO_TOTAL_MATCHED``
    presence guard) then :func:`render_odds_blob` for the stored ``str(dict)`` form.

    Args:
        result: The parsed ``listMarketBook`` result for one market — a list
            whose ``result[0]["runners"]`` is the list of runner dicts (each with
            ``selectionId``, an optional runner-level ``totalMatched``, and an
            optional ``ex``). Assumed non-empty (caller guards emptiness).
        market_id: The market id bound into every row.
        capture_ts: The single shared Capture_Timestamp bound into every row.

    Returns:
        An ordered list of ``(capture_ts, market_id, runner_id, odds_str)``
        tuples, one per returned runner.
    """
    runners = result[0]["runners"]
    rows = []
    for runner in runners:
        runner_id = runner["selectionId"]
        # Reuse the task-1 blob builder. Pass the runner-level totalMatched scalar
        # only when the key is actually present, so a genuine 0 is set (Req 2.5)
        # while a missing figure is left unset (Req 2.6) — mirroring the helper's
        # _NO_TOTAL_MATCHED presence guard. A missing/empty `ex` is handled by
        # build_odds_blob backfilling empty ladders (Req 1.3, 2.6).
        if "totalMatched" in runner:
            blob = build_odds_blob(runner.get("ex"), runner["totalMatched"])
        else:
            blob = build_odds_blob(runner.get("ex"))
        odds_str = render_odds_blob(blob)
        rows.append((capture_ts, market_id, runner_id, odds_str))
    return rows


class MonitorService:
    def __init__(self, log_level=Log.INFO, strategy=None):
        if strategy is None:
            strategy = FromFileStrategy()
        self.BF = BFDriver(strategy, log_level)
        self.db_connection = None

    def authenticate_and_get_token(self):
        try:
            if not self.BF.get_token():
                self.db_connection.db_write_log("Monitor Service: ERROR : Ending Run : Failed to retrieve token")
                raise bf_auth.AuthException("Failed to authenticate to Betfair API. Check credentials in .env file.")
            # self.db_connection.db_write_log("Token retrieved")
            Log.log_info("##############    Login Token Retrieved")
        except Exception as e:
            raise MonitorServiceException(f"Authentication failed: {e}") from e

    def get_targets(self):
        try:
            raw_targets = self.db_connection.db_read(
                "SELECT target_id, event_id, market_id, runner_ids, start_time, status, update_frequency, last_updated, notes FROM bf.target WHERE status in ('IDENTIFIED', 'OPEN');"  # noqa: E501
            )
            Log.log_info(
                f"##############    Step 1 Complete - {len(raw_targets)} targets in IDENTIFIED", force_console_log=True
            )
            return raw_targets
        except Exception as e:
            self.db_connection.db_write_log(f"Monitor Service: ERROR : Ending Run : Failed to get targets: {e}")
            raise MonitorServiceException(f"Failed to get targets: {e}") from e

    def process_targets(self, raw_targets):
        try:
            targets = []
            for target in raw_targets:
                market = target[2]
                Log.log_debug(f"Looking up runners for {market}")
                json_resp = self.BF.call_obj.call(
                    http_method=Methods.POST,
                    url=Urls.JSON_RPC_BET,
                    request_body=self.BF.request_body_obj.populate_template(
                        "listMarketBook", {"<ListOfMarketIDs>": [market]}
                    ),
                )
                Log.log_debug(json_resp)
                json = json_resp.json()
                result = json.get("result")
                if not result:
                    Log.log_warning(f"Market {market} returned empty result - marking as EXPIRED")
                    self.db_connection.db_write(f"UPDATE bf.target SET status='EXPIRED' WHERE market_id='{market}';")
                    continue
                status = result[0]["status"]
                Log.log_debug(f"Market: {market}, Status: {status}")
                runner_list = result[0]["runners"]
                Log.log_debug(f"Market {market}, Runners: {len(runner_list)}")
                runners = [runner["selectionId"] for runner in runner_list]
                targets.append((market, status, len(runner_list), runners, target[6], target[7], target[4]))
            Log.log_info(f"##############    Step 2 Complete {len(targets)} processed targets:", force_console_log=True)
            Log.log_debug(targets)
            return targets
        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to update odds for targets: {e}"
            )
            raise MonitorServiceException(f"Failed to update odds for targets: {e}") from e

    def update_target_status(self, targets):
        try:
            KNOWN_MARKET_STATES = ["OPEN", "CLOSED"]
            for target in targets:
                if target[1] in KNOWN_MARKET_STATES:
                    sql_command = f"UPDATE bf.target SET status='{target[1]}' WHERE market_id='{target[0]}';"
                    success = self.db_connection.db_write(sql_command)
                    Log.log_debug(f"Setting {target[0]} as {target[1]} status: {success}")
                else:
                    self.db_connection.db_write_log(
                        f"Monitor Service: ERROR : Ending Run : Unknown market state: {target}"
                    )
                    raise MonitorServiceException(f"Unknown market state: {target}")
        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to update target status: {e}"
            )
            raise MonitorServiceException(f"Failed to update target status: {e}") from e

    def fetch_odds_for_new_targets(self, raw_targets, processed_targets):
        """
        For targets transitioning IDENTIFIED -> OPEN, immediately fetch odds
        and set update_frequency based on tier config.
        """
        try:
            newly_opened = []
            for raw, processed in zip(raw_targets, processed_targets, strict=False):
                # raw[5] is the DB status (IDENTIFIED), processed[1] is the API status (OPEN)
                if raw[5] == "IDENTIFIED" and processed[1] == "OPEN":
                    newly_opened.append(processed)

            if newly_opened:
                Log.log_info(
                    f"##############    Fetching initial odds for {len(newly_opened)} newly-opened targets",
                    force_console_log=True,
                )
                for target in newly_opened:
                    try:
                        self.update_runner_odds([target])
                    except Exception as e:
                        Log.log_warning(f"Failed to fetch initial odds for market {target[0]}: {e}")
                        continue
            else:
                Log.log_info("##############    No newly-opened targets requiring initial odds fetch")
        except Exception as e:
            self.db_connection.db_write_log(f"Monitor Service: ERROR : Failed to fetch odds for new targets: {e}")
            Log.log_warning(f"Failed to fetch odds for new targets: {e}")

    # This function is essentially deprecated as the "get_filtered_targets" now selects only open targets
    def get_open_targets(self):
        try:
            open_targets = self.db_connection.db_read(
                "SELECT target_id, event_id, market_id, runner_ids, start_time, status, notes FROM bf.target WHERE status = 'OPEN';"  # noqa: E501
            )
            if len(open_targets) == 0:
                Log.log_warning("No open targets found")
            else:
                Log.log_info(f"Active Targets: {open_targets}")
            return open_targets
        except Exception as e:
            self.db_connection.db_write_log(f"Monitor Service: ERROR : Ending Run : Failed to get open targets: {e}")
            raise MonitorServiceException(f"Failed to get open targets: {e}") from e

    def get_filtered_targets(self, open_targets):
        try:
            targets_to_update = []
            Log.log_info("##############    Step 3 get_filtered_targets")

            # Initialise the nearest update time to something far in the future
            nearest_update_time = 99999

            for target in open_targets:
                Log.log_debug(target)

                # Retrieve the current date and time
                current_time = datetime.now(UTC)
                Log.log_debug(f"Current date and time: {current_time}")

                seconds_until_next_update = target[4]
                Log.log_debug(f"Seconds until next update: {seconds_until_next_update}")

                # Assuming target[5] contains the last updated time as a datetime object
                last_update_time = target[5]
                Log.log_debug(f"Last update time: {last_update_time}")

                # Calculate the next update time
                next_update_time = last_update_time + timedelta(seconds=seconds_until_next_update)
                Log.log_debug(f"Next update time: {next_update_time}")

                # Calculate the number of seconds until the next update is required
                time_until_next_update = next_update_time - current_time
                seconds_until_next_update_required = time_until_next_update.total_seconds()
                Log.log_debug(f"Seconds until next update is required: {seconds_until_next_update_required}")

                if seconds_until_next_update_required < nearest_update_time:
                    nearest_update_time = seconds_until_next_update_required
                    Log.log_debug(f"Nearest update time updated: {nearest_update_time}")

                if target[1] == "OPEN" and seconds_until_next_update_required < 0:
                    targets_to_update.append(target)

            return (targets_to_update, nearest_update_time)
        except Exception as e:
            self.db_connection.db_write_log(
                f"Monitor Service: ERROR : Ending Run : Failed to filter targets that require update: {e}"
            )
            raise MonitorServiceException(f"Failed to filter targets that require update: {e}") from e

    def update_runner_odds(self, open_targets):
        # Outer method-level backstop (retained): captures failures OUTSIDE the
        # per-market scope (e.g. an error iterating `open_targets` itself),
        # records them to the durable run log (bf.log_file) and surfaces them as
        # MonitorServiceException, preserving the existing durable-logging
        # behaviour. Per-market failures are isolated below so one bad market
        # does not abort the rest of the run (Req 1.5, 1.6).
        try:
            for target in open_targets:
                # One target == one market; market id is target[0]. Each market's
                # call + result-read + assemble + write + tier update runs inside
                # its own try/except so a single market's failure (empty result,
                # call/parse error, or DB write error) only skips that market and
                # the loop continues to the next (Req 1.5, 1.6, 1.7).
                market_id = target[0]
                try:
                    Log.log_debug(f"Looking up odds for target {target}")

                    # Task 4.1 — ONE listMarketBook call per market, returning
                    # every runner in a single response. Same template and call
                    # pattern as process_targets (Req 1.1).
                    resp = self.BF.call_obj.call(
                        http_method=Methods.POST,
                        url=Urls.JSON_RPC_BET,
                        request_body=self.BF.request_body_obj.populate_template(
                            "listMarketBook", {"<ListOfMarketIDs>": [market_id]}
                        ),
                    )
                    Log.log_debug(resp)
                    result = resp.json().get("result")

                    # Task 4.4 — empty / missing guard (Req 1.5). A falsy result,
                    # or a result whose runner collection is empty/absent, writes
                    # NO row and continues. Unlike process_targets, the writer
                    # does NOT mark the target EXPIRED — status transitions remain
                    # process_targets' responsibility; the writer only skips.
                    if not result or not result[0].get("runners"):
                        Log.log_warning(
                            f"listMarketBook returned an empty result for market {market_id}; "
                            f"skipping this market (no rows written)"
                        )
                        continue

                    status = result[0]["status"]
                    Log.log_debug(f"Market: {market_id}, Status: {status}, Runners: {len(result[0]['runners'])}")

                    # Task 4.1 — ONE shared capture timestamp per market, computed
                    # in Python before any row is built, bound as a parameter so
                    # every runner row of this snapshot shares one identical value
                    # (Req 1.2, 1.4). datetime.now(UTC) keeps this platform
                    # independent (capture runs on the Pi; dev/tests on Windows).
                    capture_ts = datetime.now(UTC)

                    # Task 4.2 — assemble the N rows via the pure helper, then
                    # build ONE atomic multi-row INSERT. Generate N "(%s,%s,%s,%s)"
                    # value groups to match the runner count and flatten the row
                    # tuples (order: timestamp, market_id, runner_id, odds) into a
                    # single params sequence. A single INSERT is one statement and
                    # therefore one atomic autocommitted unit: all N rows land or
                    # none do (Req 1.3, 1.7). Values stay parameterized — never
                    # string-interpolated.
                    rows = assemble_snapshot_rows(result, market_id, capture_ts)
                    value_groups = ", ".join(["(%s, %s, %s, %s)"] * len(rows))
                    sql_command = (
                        f'INSERT INTO bf.market_table("timestamp", market_id, runner_id, odds) VALUES {value_groups};'
                    )
                    params = [field for row in rows for field in row]
                    success = self.db_connection.db_write(sql_command, params)
                    Log.log_info(f"Updating odds for {market_id}: {len(rows)} runner rows, status: {success}")

                    # Task 4.4 — db_write returns False on a DB error (it swallows
                    # and logs the exception). Treat a False return as a failed
                    # snapshot: the single INSERT is atomic so zero rows persisted;
                    # log identifying the market, SKIP the tier UPDATE, and
                    # continue (Req 1.6, 1.7).
                    if not success:
                        Log.log_warning(
                            f"Snapshot write failed for market {market_id} (db_write returned False); "
                            f"skipping tier update, no rows persisted"
                        )
                        continue

                    # Task 4.3 — preserve the bf.target tier-update tail UNCHANGED
                    # in behaviour. Runs only after a SUCCESSFUL snapshot write.
                    # target[6] is the event start time (Req 1.1).
                    now = datetime.now(UTC)
                    target_time = target[6]
                    next_update_time_seconds = 0

                    tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
                    if target_time < now:
                        Log.log_info(f"Target {market_id} is open")
                        next_update_time_seconds = tiers.get("IN_PLAY", 5)
                    elif target_time < now + timedelta(hours=3):
                        Log.log_info(f"Target {market_id} is less than 3 hours away")
                        next_update_time_seconds = tiers.get("LESS_THAN_3H", 300)
                    elif target_time < now + timedelta(hours=6):
                        Log.log_info(f"Target {market_id} is less than 6 hours away")
                        next_update_time_seconds = tiers.get("LESS_THAN_6H", 900)
                    elif target_time < now + timedelta(hours=12):
                        Log.log_info(f"Target {market_id} is less than 12 hours away")
                        next_update_time_seconds = tiers.get("LESS_THAN_12H", 3600)
                    else:
                        Log.log_info(f"Target {market_id} is more than 12 hours away")
                        next_update_time_seconds = tiers.get("MORE_THAN_12H", 14400)

                    # Updating the last updated time for that target
                    sql_command = "UPDATE bf.target SET last_updated=NOW(), update_frequency=%s WHERE market_id=%s;"
                    success = self.db_connection.db_write(
                        sql_command,
                        (
                            next_update_time_seconds,
                            market_id,
                        ),
                    )
                    Log.log_debug(f"Updating last updated time for {market_id} status: {success}")

                except Exception as market_error:
                    # Task 4.4 — per-market error isolation (Req 1.6). An exception
                    # during this market's call/parse/build/write is logged with
                    # the market id and the error; no row is written for it; the
                    # loop continues to the next market.
                    Log.log_warning(
                        f"Failed to capture snapshot for market {market_id}: {market_error}; "
                        f"continuing with remaining markets"
                    )
                    continue

        except Exception as e:
            self.db_connection.db_write_log(f"Monitor Service: ERROR : Ending Run : Failed to update runner odds: {e}")
            raise MonitorServiceException(f"Failed to update runner odds: {e}") from e

    def run(self):
        try:
            # reload from db needs to be true for the first iteration
            reload_from_db = True

            db_details_string = self.BF.get_local_db_details()
            self.db_connection = DBOutputConnection()
            self.db_connection.open_connection(db_details_string)

            self.db_connection.db_write_log("Monitor Service: INFO: Starting run")

            # Single-instance guard (SP-343, Req 1.4/2.6). Acquire the session-scoped
            # advisory lock BEFORE stale-target cleanup / authenticate so a second
            # concurrent Rundeck-triggered `run --rm` container does nothing. The lock
            # is session-scoped so it auto-releases on connection close / container death.
            if not self.db_connection.try_acquire_run_lock():
                Log.log_info("Monitor Service: INFO: Another run active, exiting as no-op", force_console_log=True)
                self.db_connection.close()
                return

            # Clean up stale targets whose start_time has passed by more than the configured threshold
            stale_hours = DefaultStrategy.STALE_TARGET_HOURS
            stale_cleanup_sql = f"UPDATE bf.target SET status = 'EXPIRED' WHERE status IN ('IDENTIFIED', 'OPEN') AND start_time < NOW() - INTERVAL '{stale_hours} hours';"  # noqa: E501
            self.db_connection.db_write(stale_cleanup_sql)
            Log.log_info("##############    Stale targets cleaned up", force_console_log=True)

            self.authenticate_and_get_token()

            # SP-343 adaptive run-loop lifecycle. The old fixed range(15*60) budget and
            # the two premature bail-outs (empty filtered_targets / nearest > MAX_WAIT)
            # are replaced by a while loop bounded only by the 6-hour hard cap enforced
            # inside decide_next_action. run_start uses a monotonic clock so the cap is
            # immune to wall-clock adjustments.
            run_start = time.monotonic()
            lead_window = timedelta(seconds=DefaultStrategy.MONITOR_LEAD_WINDOW_SECONDS)
            in_play_interval = DefaultStrategy.UPDATE_FREQUENCY_TIERS.get("IN_PLAY", 5)

            while True:
                if reload_from_db:
                    # Get all of our raw target data from the database
                    raw_targets = self.get_targets()

                    # Process the targets into data we can work with easily
                    targets = self.process_targets(raw_targets)

                    # Update the status of targets (for example, CLOSE any markets that have closed!)
                    self.update_target_status(targets)

                    # Fetch initial odds for targets transitioning IDENTIFIED -> OPEN
                    self.fetch_odds_for_new_targets(raw_targets, targets)

                reload_from_db = False

                now = datetime.now(UTC)

                # Active-or-imminent = OPEN and within the lead window (in-play OR <= 20 min
                # to kickoff). Processed-target tuple shape (see process_targets):
                #   (market, status, num_runners, runners, update_frequency, last_updated, event_start_time)
                # so status is at [1] and the event start time at [6].
                active_or_imminent = [
                    t for t in targets if t[1] == "OPEN" and t[6] is not None and t[6] <= now + lead_window
                ]

                # Targets already due per their (coarse) stored update_frequency.
                filtered_targets, nearest_update_seconds = self.get_filtered_targets(targets)

                # Due-ness reconciliation (BUG A'): get_filtered_targets marks a lead-window
                # pre-match target due only every ~300s. While game-on we force the IN_PLAY
                # cadence by ALSO treating any active-or-imminent target as due when >=
                # in_play_interval seconds have elapsed since its last_updated ([5]). This
                # drives the 5s cadence without changing select_tier or the stored
                # update_frequency (Change 3a). last_updated may be None for a freshly-opened
                # target that has not yet been persisted; treat that as due.
                inplay_due = []
                for t in active_or_imminent:
                    last_updated = t[5]
                    if last_updated is None:
                        inplay_due.append(t)
                    elif (now - last_updated).total_seconds() >= in_play_interval:
                        inplay_due.append(t)

                # Union by market_id ([0]) preserving the processed-target tuple shape that
                # update_runner_odds expects.
                poll_by_market = {t[0]: t for t in filtered_targets}
                for t in inplay_due:
                    poll_by_market.setdefault(t[0], t)
                poll_targets = list(poll_by_market.values())

                Log.log_info(
                    f"##############    Poll Targets Count : {len(poll_targets)}. Active/imminent: {len(active_or_imminent)}. Nearest Update Time: {nearest_update_seconds}"  # noqa: E501
                )

                state = LoopState(
                    has_due_target=len(poll_targets) > 0,
                    has_active_or_imminent=len(active_or_imminent) > 0,
                    nearest_update_seconds=nearest_update_seconds,
                    in_play_interval=in_play_interval,
                )
                action, sleep_seconds = decide_next_action(state, time.monotonic() - run_start)

                if action == "exit":
                    Log.log_info("##############    No game on / cap reached - ending run", force_console_log=True)
                    break
                elif action == "poll":
                    Log.log_info("##############    Updating Targets", force_console_log=True)
                    # Updating odds for targets
                    self.update_runner_odds(poll_targets)
                    reload_from_db = True
                else:  # "sleep"
                    time.sleep(sleep_seconds)

            # Normal exit: release the single-instance advisory lock before closing.
            # On crash we do NOT release explicitly (the except block below runs and
            # session-close auto-releases the session-scoped lock).
            self.db_connection.release_run_lock()

            self.db_connection.db_write_log("Monitor Service: INFO: Ending run successfully")
            Log.log_info("Monitor Service: INFO: Ending run successfully", force_console_log=True)

        except Exception as e:
            # Record the failure OUTCOME and REASON to the durable run log
            # (bf.log_file) so a failed run is visible with a timestamp without
            # inspecting internal state (Req 4.1, 4.2, 3.4). This also writes an
            # "Ending run" marker for a run that already logged "Starting run",
            # so a crash does not leave the single-instance lock permanently
            # unbalanced (mitigates the SP-330 poisoned-lock failure mode).
            Log.log_error(f"Failed to update targets: {e}")
            try:
                if self.db_connection is not None:
                    self.db_connection.db_write_log(f"Monitor Service: ERROR : Ending run with failure : {e}")
            except Exception as log_error:
                # Never let failure-logging mask the original exception.
                Log.log_error(f"Also failed to record run failure to bf.log_file: {log_error}")
            raise MonitorServiceException(f"Failed to update targets: {e}") from e


if __name__ == "__main__":
    service = MonitorService(log_level=Log.INFO)

    # Call your service.run() function
    service.run()
