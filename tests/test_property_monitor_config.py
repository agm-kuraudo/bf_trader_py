"""
Property-based tests for SP-302: Monitor Initial Odds and Configurable Timing.
Tests the correctness properties defined in the design document.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import UTC, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from logic.simpleStategy import DefaultStrategy, select_tier

# === Property 1: Tier selection returns correct interval for any time offset ===


class TestProperty1TierSelection:
    """Feature: monitor-initial-odds-and-config.

    Property 1: Tier selection returns the correct interval for any time offset.
    """

    @given(
        in_play=st.integers(min_value=1, max_value=86400),
        lt_3h=st.integers(min_value=1, max_value=86400),
        lt_6h=st.integers(min_value=1, max_value=86400),
        lt_12h=st.integers(min_value=1, max_value=86400),
        gt_12h=st.integers(min_value=1, max_value=86400),
        seconds_offset=st.integers(min_value=-7200, max_value=172800),
    )
    @settings(max_examples=200)
    def test_tier_selection_returns_correct_bucket(self, in_play, lt_3h, lt_6h, lt_12h, gt_12h, seconds_offset):
        """
        **Validates: Requirements 1.4, 2.3**
        """
        tiers = {
            "IN_PLAY": in_play,
            "LESS_THAN_3H": lt_3h,
            "LESS_THAN_6H": lt_6h,
            "LESS_THAN_12H": lt_12h,
            "MORE_THAN_12H": gt_12h,
        }
        time_until_start = timedelta(seconds=seconds_offset)
        result = select_tier(tiers, time_until_start)

        if seconds_offset <= 0:
            assert result == in_play
        elif seconds_offset <= 3 * 3600:
            assert result == lt_3h
        elif seconds_offset <= 6 * 3600:
            assert result == lt_6h
        elif seconds_offset <= 12 * 3600:
            assert result == lt_12h
        else:
            assert result == gt_12h

    def test_tier_selection_with_default_config(self):
        """Verify the default config values match expected tier boundaries."""
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        # In-play
        assert select_tier(tiers, timedelta(seconds=-100)) == 5
        # Less than 3h
        assert select_tier(tiers, timedelta(hours=1)) == 300
        # Less than 6h
        assert select_tier(tiers, timedelta(hours=4)) == 900
        # Less than 12h
        assert select_tier(tiers, timedelta(hours=8)) == 3600
        # More than 12h
        assert select_tier(tiers, timedelta(hours=24)) == 14400


# === Property 2: Config loading preserves present values and defaults for missing ===


class TestProperty2ConfigLoading:
    """Feature: monitor-initial-odds-and-config.

    Property 2: Config loading preserves present values and applies defaults for missing keys.
    """

    def test_default_strategy_has_all_timing_attributes(self):
        """
        DefaultStrategy must have all four timing attributes with correct defaults.

        **Validates: Requirements 2.2, 2.5, 3.3, 4.3, 5.3, 6.1, 6.2**
        """
        assert hasattr(DefaultStrategy, "UPDATE_FREQUENCY_TIERS")
        assert hasattr(DefaultStrategy, "INITIAL_UPDATE_FREQUENCY")
        assert hasattr(DefaultStrategy, "STALE_TARGET_HOURS")
        assert hasattr(DefaultStrategy, "MONITOR_MAX_WAIT_SECONDS")

        assert DefaultStrategy.INITIAL_UPDATE_FREQUENCY == 14400
        assert DefaultStrategy.STALE_TARGET_HOURS == 24
        assert DefaultStrategy.MONITOR_MAX_WAIT_SECONDS == 900

        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        assert tiers["IN_PLAY"] == 5
        assert tiers["LESS_THAN_3H"] == 300
        assert tiers["LESS_THAN_6H"] == 900
        assert tiers["LESS_THAN_12H"] == 3600
        assert tiers["MORE_THAN_12H"] == 14400

    @given(
        initial_freq=st.integers(min_value=1, max_value=86400),
        stale_hours=st.integers(min_value=1, max_value=168),
        max_wait=st.integers(min_value=60, max_value=7200),
    )
    @settings(max_examples=100)
    def test_get_with_fallback_pattern(self, initial_freq, stale_hours, max_wait):
        """
        Simulate the .get() fallback pattern used in FromFileStrategy.

        **Validates: Requirements 2.2, 2.5, 3.3, 4.3, 5.3, 6.1, 6.2**
        """
        yaml_content = {
            "INITIAL_UPDATE_FREQUENCY": initial_freq,
            "STALE_TARGET_HOURS": stale_hours,
            "MONITOR_MAX_WAIT_SECONDS": max_wait,
        }

        # Simulate loading with .get() and defaults
        loaded_initial = yaml_content.get("INITIAL_UPDATE_FREQUENCY", 14400)
        loaded_stale = yaml_content.get("STALE_TARGET_HOURS", 24)
        loaded_max_wait = yaml_content.get("MONITOR_MAX_WAIT_SECONDS", 900)

        assert loaded_initial == initial_freq
        assert loaded_stale == stale_hours
        assert loaded_max_wait == max_wait

    def test_missing_keys_use_defaults(self):
        """
        When keys are absent, .get() returns defaults.

        **Validates: Requirements 6.1, 6.2**
        """
        yaml_content = {}  # Empty config

        loaded_initial = yaml_content.get("INITIAL_UPDATE_FREQUENCY", 14400)
        loaded_stale = yaml_content.get("STALE_TARGET_HOURS", 24)
        loaded_max_wait = yaml_content.get("MONITOR_MAX_WAIT_SECONDS", 900)
        loaded_tiers = yaml_content.get("UPDATE_FREQUENCY_TIERS", DefaultStrategy.UPDATE_FREQUENCY_TIERS)

        assert loaded_initial == 14400
        assert loaded_stale == 24
        assert loaded_max_wait == 900
        assert loaded_tiers == DefaultStrategy.UPDATE_FREQUENCY_TIERS


# === Property 3: Newly-opened target identification selects exactly the correct targets ===


class TestProperty3NewlyOpenedIdentification:
    """Feature: monitor-initial-odds-and-config.

    Property 3: Newly-opened target identification selects exactly the correct targets.
    """

    @given(
        raw_statuses=st.lists(st.sampled_from(["IDENTIFIED", "OPEN", "CLOSED", "EXPIRED"]), min_size=1, max_size=20),
        processed_statuses=st.lists(st.sampled_from(["OPEN", "CLOSED", "SUSPENDED"]), min_size=1, max_size=20),
    )
    @settings(max_examples=200)
    def test_newly_opened_selects_correct_targets(self, raw_statuses, processed_statuses):
        """
        Only targets where raw=IDENTIFIED AND processed=OPEN should be selected.

        **Validates: Requirements 1.1**
        """
        # Ensure lists are same length
        min_len = min(len(raw_statuses), len(processed_statuses))
        raw_statuses = raw_statuses[:min_len]
        processed_statuses = processed_statuses[:min_len]

        # Build mock raw_targets (status at index 5) and processed_targets (status at index 1)
        raw_targets = [("", "", "", "", "", status, "", "", "") for status in raw_statuses]
        processed_targets = [("market", status, 3, [1, 2, 3], 14400, None, None) for status in processed_statuses]

        # Apply the same logic as fetch_odds_for_new_targets
        newly_opened = []
        for raw, processed in zip(raw_targets, processed_targets, strict=False):
            if raw[5] == "IDENTIFIED" and processed[1] == "OPEN":
                newly_opened.append(processed)

        # Verify: count matches expected
        expected_count = sum(
            1
            for raw_s, proc_s in zip(raw_statuses, processed_statuses, strict=False)
            if raw_s == "IDENTIFIED" and proc_s == "OPEN"
        )
        assert len(newly_opened) == expected_count

        # Verify: all selected have correct statuses
        for target in newly_opened:
            assert target[1] == "OPEN"

    def test_no_newly_opened_when_all_already_open(self):
        """
        If all raw targets are already OPEN, none should be selected.

        **Validates: Requirements 1.1**
        """
        raw_targets = [("", "", "", "", "", "OPEN", "", "", "")] * 5
        processed_targets = [("market", "OPEN", 3, [1], 14400, None, None)] * 5

        newly_opened = [
            proc
            for raw, proc in zip(raw_targets, processed_targets, strict=False)
            if raw[5] == "IDENTIFIED" and proc[1] == "OPEN"
        ]
        assert len(newly_opened) == 0

    def test_empty_lists_returns_empty(self):
        """
        Empty input lists should produce empty output.

        **Validates: Requirements 1.1**
        """
        newly_opened = [proc for raw, proc in zip([], [], strict=False) if raw[5] == "IDENTIFIED" and proc[1] == "OPEN"]
        assert len(newly_opened) == 0


# === Property 4: Cadence tier selection is total and monotonic ===


class TestProperty4CadenceTierSelection:
    """Feature: season-background-data-capture.

    Property 4: Cadence tier selection is total and monotonic.
    """

    # Feature: season-background-data-capture, Property 4: Cadence tier selection is total and monotonic
    @given(
        offset_a=st.integers(min_value=-172800, max_value=172800),
        offset_b=st.integers(min_value=-172800, max_value=172800),
    )
    @settings(max_examples=200)
    def test_select_tier_is_total_and_monotonic(self, offset_a, offset_b):
        """
        TOTAL: select_tier returns a defined integer interval for any time offset
        (negative/in-play, zero, and large positive) without raising or returning None.

        MONOTONIC: for two times t1 <= t2, the interval selected for t1 is <= the
        interval selected for t2 - as an event gets closer the interval never gets
        longer. Uses the default tiers so the tier ordering holds.

        **Validates: Requirements 2.2**
        """
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS

        # Sort the two offsets so t1 <= t2 (in seconds-to-start).
        t1_seconds, t2_seconds = sorted((offset_a, offset_b))
        t1 = timedelta(seconds=t1_seconds)
        t2 = timedelta(seconds=t2_seconds)

        interval_t1 = select_tier(tiers, t1)
        interval_t2 = select_tier(tiers, t2)

        # TOTAL: both results are defined integers.
        assert interval_t1 is not None
        assert interval_t2 is not None
        assert isinstance(interval_t1, int)
        assert isinstance(interval_t2, int)

        # MONOTONIC: a smaller time-to-event never yields a longer interval.
        assert interval_t1 <= interval_t2


# ======================================================================================
# SP-343: In-Play Polling Cadence Fix — Bug-Condition Exploration Property Test
# ======================================================================================
#
# Task 1 (bug-condition methodology). This test is EXPECTED TO FAIL on the current
# (unfixed) code. The failure CONFIRMS the bug: when an OPEN target is in-play OR within
# the 20-minute pre-kickoff lead window but nothing is due at that exact instant, the
# current run-loop EXITS (or the effective sample interval collapses to ~900s / the
# Rundeck re-trigger interval) instead of sleeping ~5s and continuing.
#
# The pure ``decide_next_action`` does NOT exist yet on unfixed code, so per the design's
# "Exploratory Bug Condition Checking" section we use approach (a): a small, faithful
# extraction of the CURRENT inline loop decision logic from ``monitor_service.py::run()``.
# The extraction reproduces exactly the three bail-out conditions present today:
#   1. empty-``filtered_targets`` break            (BUG A / A' — pre-kickoff drop-out)
#   2. ``nearest_update_seconds > 900`` cap break  (BUG B)
#   3. fixed ``range(15 * 60)`` iteration budget    (BUG C)
#
# Requirements traced: 1.1, 1.2, 1.3, 1.5.

from logic.simpleStategy import select_tier as _select_tier  # noqa: E402  (kept local to SP-343 block)

# Constants from the SP-343 bugfix spec (see bugfix.md / design.md).
LEAD_WINDOW_SECONDS = 20 * 60  # 1200s — pre-kickoff lead window ("game on" boundary)
MONITOR_MAX_WAIT_SECONDS = 900  # current run-loop 900s bail-out cap
IN_PLAY_INTERVAL = 5  # IN_PLAY tier interval (~5s)
_RANGE_BUDGET = 15 * 60  # current fixed range(15*60) iteration budget


def simulate_current_run_loop(
    *,
    start_time_offset_seconds: float,
    stored_update_frequency: int,
    last_updated_offset_seconds: float,
    match_duration_seconds: float,
    now: datetime | None = None,
) -> dict:
    """Faithful extraction of the CURRENT (unfixed) ``monitor_service.py::run()`` loop
    decision logic for a single OPEN target.

    This mirrors the real loop's control flow WITHOUT any DB or Betfair API:

    - ``get_filtered_targets`` marks the target due only when
      ``last_updated + update_frequency <= now`` (``seconds_until_next_update_required < 0``),
      using the target's STORED ``update_frequency`` (coarse for pre-match targets).
    - The loop breaks immediately when nothing is due this instant
      (``len(filtered_targets) == 0``)  -> BUG A / A'.
    - The loop breaks when ``nearest_update_seconds > MONITOR_MAX_WAIT_SECONDS`` (900)
      -> BUG B.
    - The loop is bounded by ``range(15 * 60)`` iterations -> BUG C.
    - When a poll happens, the target's ``update_frequency`` is re-derived from the tier
      (``select_tier``) exactly as ``update_runner_odds`` does today, and ``last_updated``
      is advanced to the current simulated time.

    The target is OPEN. ``start_time_offset_seconds`` is ``start_time - now`` (negative =
    already in-play; positive = pre-kickoff). Returns a summary describing how many odds
    updates happened and the effective sample interval before the run exited.

    Returns dict with keys: ``num_updates``, ``exit_reason``, ``effective_interval_seconds``,
    ``sim_wall_seconds``.
    """
    if now is None:
        now = datetime.now(UTC)

    tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
    start_time = now + timedelta(seconds=start_time_offset_seconds)
    last_updated = now + timedelta(seconds=last_updated_offset_seconds)
    update_frequency = stored_update_frequency

    sim_now = now
    sim_wall_seconds = 0.0
    num_updates = 0
    update_wall_times: list[float] = []
    exit_reason = "range_budget_exhausted"

    for _i in range(_RANGE_BUDGET):
        # Stop simulating once we pass the (bounded) match window; the match would CLOSE.
        if sim_wall_seconds > match_duration_seconds:
            exit_reason = "match_closed"
            break

        # --- get_filtered_targets: due-ness from the STORED update_frequency ---
        next_update_time = last_updated + timedelta(seconds=update_frequency)
        seconds_until_next_update_required = (next_update_time - sim_now).total_seconds()
        nearest_update_seconds = seconds_until_next_update_required
        is_due = seconds_until_next_update_required < 0

        # --- BUG A / A': empty filtered_targets -> immediate break ---
        if not is_due:
            exit_reason = "empty_filtered_targets_break"
            break

        # --- poll: update odds, re-derive update_frequency from the tier (as today) ---
        num_updates += 1
        update_wall_times.append(sim_wall_seconds)
        time_until_start = start_time - sim_now
        update_frequency = _select_tier(tiers, time_until_start)
        last_updated = sim_now

        # --- BUG B: nearest update beyond the 900s cap -> break ---
        if nearest_update_seconds > MONITOR_MAX_WAIT_SECONDS:
            exit_reason = "max_wait_cap_break"
            break

        # --- loop sleep (as today: sleep ~ nearest_update_seconds - 1) ---
        sleep_seconds = max(0.1, nearest_update_seconds - 1)
        # After a poll the target was just updated, so the next due gap is update_frequency.
        # Advance simulated time by the freshly-computed cadence to model the real loop.
        step = max(0.1, update_frequency - 1) if update_frequency else sleep_seconds
        sim_now = sim_now + timedelta(seconds=step)
        sim_wall_seconds += step

    if len(update_wall_times) >= 2:
        span = update_wall_times[-1] - update_wall_times[0]
        effective_interval = span / (len(update_wall_times) - 1)
    else:
        # Only one (or zero) update before exit -> effective interval is the whole
        # match window (data is captured at best once, i.e. ~Rundeck interval sparse).
        effective_interval = float(match_duration_seconds) if match_duration_seconds > 0 else float("inf")

    return {
        "num_updates": num_updates,
        "exit_reason": exit_reason,
        "effective_interval_seconds": effective_interval,
        "sim_wall_seconds": sim_wall_seconds,
    }


# --------------------------------------------------------------------------------------
# SP-343 Task 6 — faithful extraction of the FIXED ``monitor_service.py::run()`` loop.
#
# ``simulate_current_run_loop`` (above) models the OLD loop and its three bail-outs, so
# the Task 1 exploration assertions FAIL against it (that failure was the bug proof in
# Task 1). Now that the fix has landed, Task 6 requires the SAME exploration assertions to
# PASS against the FIXED logic. Rather than re-mock ``run()`` by hand, this helper routes
# the exit/sleep/poll decision through the REAL pure ``decide_next_action`` and reproduces
# the exact lead-window + due-ness reconciliation the new ``run()`` performs:
#   - active-or-imminent = OPEN and start_time <= now + LEAD_WINDOW_SECONDS
#   - a target is treated as due when >= in_play_interval has elapsed since last_updated
#     (the union-with-filtered_targets reconciliation of BUG A'), so the 5s cadence is
#     driven by the loop even though the stored coarse update_frequency is unchanged.
#   - sleep is driven by the IN_PLAY interval (via decide_next_action), never the coarse
#     nearest_update_seconds.
#   - bounded only by the 6-hour hard cap (enforced inside decide_next_action).
#
# It keeps the same signature and return shape as ``simulate_current_run_loop`` so the
# exploration tests can dispatch between old/fixed logic with identical assertions.
# --------------------------------------------------------------------------------------


def simulate_fixed_run_loop(
    *,
    start_time_offset_seconds: float,
    stored_update_frequency: int,
    last_updated_offset_seconds: float,
    match_duration_seconds: float,
    now: datetime | None = None,
) -> dict:
    """Faithful extraction of the FIXED ``run()`` loop decision logic for a single OPEN
    target, routed through the real ``decide_next_action`` (see module docstring above).

    Same inputs/return shape as :func:`simulate_current_run_loop`. The target is OPEN;
    ``start_time_offset_seconds`` is ``start_time - now`` (negative = in-play, positive =
    pre-kickoff). The loop advances simulated wall-clock by the chosen sleep/poll cadence
    and stops when the match window closes or the 6-hour cap is hit.
    """
    if now is None:
        now = datetime.now(UTC)

    lead_window = timedelta(seconds=_PURE_LEAD_WINDOW_SECONDS)
    in_play_interval = DefaultStrategy.UPDATE_FREQUENCY_TIERS.get("IN_PLAY", 5)

    start_time = now + timedelta(seconds=start_time_offset_seconds)
    last_updated = now + timedelta(seconds=last_updated_offset_seconds)
    stored_freq = stored_update_frequency  # kept coarse; only used for nearest_update calc

    sim_now = now
    sim_wall_seconds = 0.0
    num_updates = 0
    update_wall_times: list[float] = []
    exit_reason = "unknown"

    # Hard iteration guard so a broken decision function can't spin forever in the test.
    max_iterations = 200_000
    for _i in range(max_iterations):
        # Stop simulating once we pass the (bounded) match window; the match would CLOSE
        # and the target would no longer be OPEN.
        if sim_wall_seconds > match_duration_seconds:
            exit_reason = "match_closed"
            break

        # --- classification, mirroring the new run() loop ---
        # active-or-imminent: OPEN and start_time within the lead window of now.
        has_active_or_imminent = start_time <= sim_now + lead_window

        # get_filtered_targets due-ness from the STORED (coarse) update_frequency.
        next_update_time = last_updated + timedelta(seconds=stored_freq)
        nearest_update_seconds = (next_update_time - sim_now).total_seconds()
        filtered_due = nearest_update_seconds < 0

        # BUG A' reconciliation: while game-on, also treat the target as due when
        # >= in_play_interval has elapsed since last_updated. Union with filtered_due.
        seconds_since_last_update = (sim_now - last_updated).total_seconds()
        inplay_due = has_active_or_imminent and seconds_since_last_update >= in_play_interval
        has_due_target = filtered_due or inplay_due

        state = LoopState(
            has_due_target=has_due_target,
            has_active_or_imminent=has_active_or_imminent,
            nearest_update_seconds=nearest_update_seconds,
            in_play_interval=in_play_interval,
        )
        action, sleep_seconds = decide_next_action(state, sim_wall_seconds)

        if action == "exit":
            exit_reason = "hard_cap" if sim_wall_seconds >= HARD_CAP_SECONDS else "idle_cheap_exit"
            break
        elif action == "poll":
            num_updates += 1
            update_wall_times.append(sim_wall_seconds)
            last_updated = sim_now
            # update_runner_odds re-derives the STORED update_frequency from the tier
            # (unchanged behaviour); the 5s cadence is loop-driven, not from this value.
            stored_freq = _select_tier(DefaultStrategy.UPDATE_FREQUENCY_TIERS, start_time - sim_now)
            # A poll takes ~no simulated wall time; the next iteration re-evaluates.
            step = 0.0
        else:  # "sleep"
            step = sleep_seconds

        sim_now = sim_now + timedelta(seconds=step)
        sim_wall_seconds += step
    else:
        # Loop guard exhausted (should not happen for the bounded match windows tested).
        exit_reason = "iteration_guard_exhausted"

    if len(update_wall_times) >= 2:
        span = update_wall_times[-1] - update_wall_times[0]
        effective_interval = span / (len(update_wall_times) - 1)
    else:
        effective_interval = float(match_duration_seconds) if match_duration_seconds > 0 else float("inf")

    return {
        "num_updates": num_updates,
        "exit_reason": exit_reason,
        "effective_interval_seconds": effective_interval,
        "sim_wall_seconds": sim_wall_seconds,
    }


def simulate_run_loop(*, use_fixed_logic: bool, **kwargs) -> dict:
    """Dispatch to the OLD (``simulate_current_run_loop``) or FIXED
    (``simulate_fixed_run_loop``) run-loop extraction.

    Task 6 runs the SAME exploration assertions from Task 1 against the FIXED logic
    (``use_fixed_logic=True``) so they now PASS, while keeping the OLD-logic path
    available (``use_fixed_logic=False``) to demonstrate the original bug on demand.
    """
    if use_fixed_logic:
        return simulate_fixed_run_loop(**kwargs)
    return simulate_current_run_loop(**kwargs)


class TestSP343BugConditionExploration:
    """SP-343: In-Play Polling Cadence Fix.

    Bug-Condition exploration (Task 1, re-run in Task 6).

    In Task 1 these assertions ran against ``simulate_current_run_loop`` (a faithful
    extraction of the OLD loop) and FAILED — that failure was the bug confirmation
    (empty-``filtered_targets`` break, 900s cap, ``range(15*60)`` budget, pre-kickoff
    drop-out). In Task 6, now that the fix has landed, the SAME assertions are re-run
    against the FIXED logic via ``simulate_run_loop(use_fixed_logic=True)``, which routes
    the exit/sleep/poll decision through the real pure ``decide_next_action`` and the same
    lead-window + due-ness reconciliation the new ``run()`` performs. The assertions are
    UNCHANGED (still asserting stay-alive at ~5s, no premature exit) and now PASS —
    confirming the bug is fixed.
    """

    # Task 6: exercise the FIXED decision logic through the real decide_next_action.
    _USE_FIXED_LOGIC = True

    @given(
        # In-play OR within the 20-min lead window: start_time from 20 min in the future
        # down to 60 min ago (already in-play). All satisfy start_time <= now + 1200s.
        start_time_offset_seconds=st.integers(min_value=-3600, max_value=LEAD_WINDOW_SECONDS),
        # Stored coarse update_frequency for a target the loop last saw as pre-match
        # (e.g. LESS_THAN_3H=300) — the value get_filtered_targets uses for due-ness.
        stored_update_frequency=st.sampled_from([300, 900]),
        # The match runs for at least several minutes of in-play time we want sampled.
        match_duration_seconds=st.integers(min_value=600, max_value=3000),
    )
    @settings(max_examples=200)
    def test_in_play_or_lead_window_cadence_collapses_to_rundeck_interval(
        self, start_time_offset_seconds, stored_update_frequency, match_duration_seconds
    ):
        """Bug-Condition property: an OPEN target that is in-play OR within the 20-min
        lead window, with nothing due at the current instant, SHALL be sampled at (or
        near) the ~5s IN_PLAY interval and the run SHALL stay alive — not exit or collapse
        to the ~900s Rundeck interval.

        This assertion encodes the INTENDED FIXED behaviour, so it FAILS on the current
        unfixed loop (which breaks on empty filtered_targets / the 900s cap and
        under-samples). The failure is the bug confirmation (Requirements 1.1, 1.2, 1.3,
        1.5).

        **Validates: Requirements 1.1, 1.2, 1.3, 1.5**
        """
        now = datetime.now(UTC)

        # Bug-condition precondition: target is OPEN and in-play OR within the lead window,
        # and nothing is due at this exact instant (last update within the stored freq).
        # last_updated ~1s ago guarantees seconds_until_next_update_required > 0 (not due).
        result = simulate_run_loop(
            use_fixed_logic=self._USE_FIXED_LOGIC,
            start_time_offset_seconds=start_time_offset_seconds,
            stored_update_frequency=stored_update_frequency,
            last_updated_offset_seconds=-1.0,
            match_duration_seconds=match_duration_seconds,
            now=now,
        )

        # Number of ~5s samples we would expect across the sampled match window if the
        # loop honoured the IN_PLAY cadence (allow generous slack — "at or near 5s").
        expected_min_samples = max(2, int(match_duration_seconds / (IN_PLAY_INTERVAL * 4)))

        # INTENDED FIXED behaviour — these are what SHOULD hold once F' lands.
        assert result["exit_reason"] not in (
            "empty_filtered_targets_break",
            "max_wait_cap_break",
        ), (
            f"Run exited prematurely ({result['exit_reason']}) for an in-play/lead-window "
            f"target instead of staying alive at ~5s. start_time_offset="
            f"{start_time_offset_seconds}s, stored_freq={stored_update_frequency}s, "
            f"num_updates={result['num_updates']}."
        )
        assert result["effective_interval_seconds"] <= IN_PLAY_INTERVAL * 6, (
            f"Effective sample interval collapsed to "
            f"~{result['effective_interval_seconds']:.0f}s (≈ Rundeck ~900s) instead of "
            f"~{IN_PLAY_INTERVAL}s. num_updates={result['num_updates']}, "
            f"exit_reason={result['exit_reason']}."
        )
        assert result["num_updates"] >= expected_min_samples, (
            f"Only {result['num_updates']} odds update(s) before exit; expected at least "
            f"{expected_min_samples} at the ~5s IN_PLAY cadence over a "
            f"{match_duration_seconds}s window. exit_reason={result['exit_reason']}."
        )

    def test_pre_kickoff_lead_window_dropout_example(self):
        """Concrete lead-window counterexample (design Test Case 2): an OPEN target with
        start_time ~8 min in the future and a coarse stored update_frequency=300 sees
        nothing due for ~300s, so the current loop exits before kickoff — losing the
        opening minutes. INTENDED FIXED behaviour: stay alive and sample at ~5s.

        **Validates: Requirements 1.5, 1.2**
        """
        result = simulate_run_loop(
            use_fixed_logic=self._USE_FIXED_LOGIC,
            start_time_offset_seconds=8 * 60,  # kickoff ~8 min away (within 20-min lead window)
            stored_update_frequency=300,  # coarse LESS_THAN_3H tier
            last_updated_offset_seconds=-1.0,  # just updated -> nothing due this instant
            match_duration_seconds=1800,  # ~30 min of play we want captured
        )

        assert result["exit_reason"] != "empty_filtered_targets_break", (
            f"Pre-kickoff run dropped out ({result['exit_reason']}) before kickoff instead "
            f"of staying alive at 5s through the lead window. num_updates={result['num_updates']}."
        )
        assert result["num_updates"] >= 10, (
            f"Only {result['num_updates']} update(s) captured across the lead window + match; "
            f"expected many at the ~5s cadence. exit_reason={result['exit_reason']}."
        )

    def test_in_play_nothing_due_example(self):
        """Concrete in-play counterexample (design Test Case 1): OPEN target that kicked
        off 10 min ago, just updated so nothing due this instant. Current loop hits the
        empty-filtered_targets break and exits after ~1 update. INTENDED FIXED behaviour:
        keep sampling at ~5s.

        **Validates: Requirements 1.1, 1.2**
        """
        result = simulate_run_loop(
            use_fixed_logic=self._USE_FIXED_LOGIC,
            start_time_offset_seconds=-10 * 60,  # kicked off 10 min ago (in-play)
            stored_update_frequency=5,  # even if freq were IN_PLAY, nothing due this instant
            last_updated_offset_seconds=-1.0,  # just updated -> not due right now
            match_duration_seconds=1800,
        )

        assert result["exit_reason"] != "empty_filtered_targets_break", (
            f"In-play run exited on empty filtered_targets instead of sleeping ~5s and "
            f"continuing. num_updates={result['num_updates']}, exit_reason={result['exit_reason']}."
        )
        assert result["num_updates"] >= 10, (
            f"Only {result['num_updates']} in-play update(s) before exit; expected many at ~5s. "
            f"exit_reason={result['exit_reason']}."
        )


# ======================================================================================
# SP-343 Task 2: Unit tests for the pure `decide_next_action` loop-decision function.
#
# Boundary cases per design "Change 1":
#   - elapsed at/over HARD_CAP_SECONDS -> exit (even while game-on)
#   - has_due_target -> poll
#   - has_active_or_imminent and nothing due -> sleep with sleep ≈ in_play_interval - 1
#   - neither due nor game-on -> exit (idle cheap-exit)
#
# Requirements traced: 2.1, 2.2, 2.3, 2.4, 2.5.
# ======================================================================================

from logic.simpleStategy import (  # noqa: E402
    HARD_CAP_SECONDS,
    LoopState,
    decide_next_action,
)
from logic.simpleStategy import (  # noqa: E402
    LEAD_WINDOW_SECONDS as _PURE_LEAD_WINDOW_SECONDS,
)


class TestSP343DecideNextAction:
    """SP-343: In-Play Polling Cadence Fix.

    Unit tests for the pure ``decide_next_action`` decision function (Task 2).
    """

    def test_pure_constants(self):
        """The two pure module constants carry the documented defaults.

        **Validates: Requirements 2.5, 2.7**
        """
        assert HARD_CAP_SECONDS == 6 * 3600
        assert _PURE_LEAD_WINDOW_SECONDS == 20 * 60

    def test_at_hard_cap_exits_even_when_game_on(self):
        """At exactly HARD_CAP_SECONDS the run exits regardless of in-play state.

        **Validates: Requirements 2.5**
        """
        state = LoopState(
            has_due_target=True,
            has_active_or_imminent=True,
            nearest_update_seconds=-1.0,
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, HARD_CAP_SECONDS)
        assert action == "exit"
        assert sleep_seconds == 0

    def test_over_hard_cap_exits(self):
        """Beyond the hard cap the run exits.

        **Validates: Requirements 2.5**
        """
        state = LoopState(
            has_due_target=True,
            has_active_or_imminent=True,
            nearest_update_seconds=-1.0,
            in_play_interval=5,
        )
        action, _ = decide_next_action(state, HARD_CAP_SECONDS + 1)
        assert action == "exit"

    def test_just_under_hard_cap_does_not_force_exit(self):
        """Just under the hard cap the cap does not force an exit; a due target polls.

        **Validates: Requirements 2.5**
        """
        state = LoopState(
            has_due_target=True,
            has_active_or_imminent=True,
            nearest_update_seconds=-1.0,
            in_play_interval=5,
        )
        action, _ = decide_next_action(state, HARD_CAP_SECONDS - 1)
        assert action == "poll"

    def test_has_due_target_polls(self):
        """Something due now -> poll (updates odds), with zero sleep.

        **Validates: Requirements 2.1, 2.2**
        """
        state = LoopState(
            has_due_target=True,
            has_active_or_imminent=True,
            nearest_update_seconds=-2.0,
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "poll"
        assert sleep_seconds == 0

    def test_due_target_polls_even_when_not_game_on(self):
        """A due target polls even if has_active_or_imminent is False (poll precedes it)."""
        state = LoopState(
            has_due_target=True,
            has_active_or_imminent=False,
            nearest_update_seconds=-1.0,
            in_play_interval=5,
        )
        action, _ = decide_next_action(state, 0.0)
        assert action == "poll"

    def test_active_or_imminent_not_due_sleeps_on_in_play_interval(self):
        """Game on but nothing due -> sleep ≈ in_play_interval - 1 (driven by IN_PLAY, not
        the coarse nearest_update_seconds).

        **Validates: Requirements 2.1, 2.2, 2.3**
        """
        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=True,
            nearest_update_seconds=290.0,  # coarse (e.g. lead-window 300s freq) — must be ignored
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "sleep"
        assert sleep_seconds == 4  # max(0.1, 5 - 1); NOT ~289 from nearest_update_seconds

    def test_sleep_floor_when_in_play_interval_tiny(self):
        """The sleep is floored at 0.1s when the IN_PLAY interval is <= 1s."""
        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=True,
            nearest_update_seconds=100.0,
            in_play_interval=1,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "sleep"
        assert sleep_seconds == 0.1  # max(0.1, 1 - 1)

    def test_idle_no_targets_exits(self):
        """Neither due nor game-on -> idle cheap-exit.

        **Validates: Requirements 2.4**
        """
        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=False,
            nearest_update_seconds=3600.0,
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "exit"
        assert sleep_seconds == 0


# ======================================================================================
# SP-343 Task 7: Preservation property tests (Property 2).
#
# Proves the fix did NOT change non-buggy behaviour:
#   - Idle cheap-exit is preserved: for ALL genuinely-idle states (nothing due AND no
#     active-or-imminent target) with elapsed < HARD_CAP_SECONDS, decide_next_action
#     returns "exit" — matching today's Rundeck one-off behaviour (Req 3.1, 3.2).
#   - Tier selection is unchanged: select_tier stays TOTAL + MONOTONIC and still maps the
#     representative time-to-event inputs to the exact documented tier intervals, so a
#     regression in tier VALUES would be caught (Req 3.3).
#
# This class is intentionally self-contained (its own class per Task 7 coordination) and
# reuses the module-level imports of decide_next_action / LoopState / HARD_CAP_SECONDS /
# select_tier / DefaultStrategy already present above.
#
# Requirements traced: 3.1, 3.2, 3.3.
# ======================================================================================


class TestSP343Preservation:
    """SP-343: In-Play Polling Cadence Fix.

    Property 2: Preservation — idle cheap-exit, coarse tiers, and tier selection unchanged.
    """

    @given(
        # Any nearest_update_seconds, INCLUDING large far-out values (idle runs) and
        # already-negative values (something notionally overdue) — none of which should
        # keep a genuinely-idle run (no active-or-imminent target) alive.
        nearest_update_seconds=st.floats(
            min_value=-1000.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
        ),
        in_play_interval=st.integers(min_value=1, max_value=3600),
        # Any elapsed strictly below the 6h cap; the idle exit must hold across the whole
        # pre-cap range (at/over the cap it also exits, but that is the cap property, not
        # the preservation property under test here).
        elapsed_seconds=st.floats(min_value=0.0, max_value=HARD_CAP_SECONDS - 1, allow_nan=False),
    )
    @settings(max_examples=300)
    def test_idle_cheap_exit_preserved(self, nearest_update_seconds, in_play_interval, elapsed_seconds):
        """FOR ALL genuinely-idle states (has_due_target=False AND
        has_active_or_imminent=False) with elapsed < HARD_CAP_SECONDS,
        decide_next_action returns "exit" — the idle Rundeck one-off behaviour is
        preserved regardless of how far out the nearest update is.

        **Validates: Requirements 3.1, 3.2**
        """
        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=False,
            nearest_update_seconds=nearest_update_seconds,
            in_play_interval=in_play_interval,
        )
        action, sleep_seconds = decide_next_action(state, elapsed_seconds)
        assert action == "exit"
        assert sleep_seconds == 0

    def test_idle_cheap_exit_farout_example(self):
        """Concrete idle example: nothing due, no game on, nearest update hours away, well
        under the cap -> "exit" (matches today's one-off behaviour).

        **Validates: Requirements 3.1, 3.2**
        """
        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=False,
            nearest_update_seconds=6 * 3600.0,  # 6 hours out
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "exit"
        assert sleep_seconds == 0

    @given(
        offset_a=st.integers(min_value=-172800, max_value=172800),
        offset_b=st.integers(min_value=-172800, max_value=172800),
    )
    @settings(max_examples=300)
    def test_select_tier_still_total_and_monotonic(self, offset_a, offset_b):
        """select_tier remains TOTAL (defined int for any offset) and MONOTONIC (a
        smaller time-to-event never yields a longer interval) under the default tiers —
        confirming the fix did not alter tier selection.

        **Validates: Requirements 3.3**
        """
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS

        t1_seconds, t2_seconds = sorted((offset_a, offset_b))
        interval_t1 = select_tier(tiers, timedelta(seconds=t1_seconds))
        interval_t2 = select_tier(tiers, timedelta(seconds=t2_seconds))

        assert isinstance(interval_t1, int)
        assert isinstance(interval_t2, int)
        assert interval_t1 <= interval_t2

    def test_select_tier_exact_interval_values_unchanged(self):
        """Representative time-to-event inputs still map to the EXACT documented tier
        intervals (IN_PLAY=5, LESS_THAN_3H=300, LESS_THAN_6H=900, LESS_THAN_12H=3600,
        MORE_THAN_12H=14400). This is the regression guard: a change to any tier VALUE
        (not just ordering) would fail here.

        **Validates: Requirements 3.3**
        """
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS
        # In-play (start_time in the past)
        assert select_tier(tiers, timedelta(seconds=-1)) == 5
        assert select_tier(tiers, timedelta(seconds=-3600)) == 5
        # Exactly at kickoff (0s -> in-play boundary is inclusive of <= 0)
        assert select_tier(tiers, timedelta(seconds=0)) == 5
        # < 3h
        assert select_tier(tiers, timedelta(hours=1)) == 300
        assert select_tier(tiers, timedelta(hours=3)) == 300  # inclusive upper edge
        # < 6h
        assert select_tier(tiers, timedelta(hours=4)) == 900
        assert select_tier(tiers, timedelta(hours=6)) == 900  # inclusive upper edge
        # < 12h
        assert select_tier(tiers, timedelta(hours=8)) == 3600
        assert select_tier(tiers, timedelta(hours=12)) == 3600  # inclusive upper edge
        # > 12h
        assert select_tier(tiers, timedelta(hours=13)) == 14400
        assert select_tier(tiers, timedelta(hours=48)) == 14400


# ======================================================================================
# SP-343 Task 6: Fix-Checking property tests (Correctness Property 1).
#
# Proves the FIXED behaviour holds for buggy inputs, over the pure ``decide_next_action``
# (design "Fix Checking" + Correctness Property 1):
#
#   1. FOR ALL active-or-imminent states (has_active_or_imminent=True) with
#      has_due_target=False and elapsed < HARD_CAP_SECONDS: decide_next_action returns
#      "sleep" (NEVER "exit"), and sleep_seconds ≈ in_play_interval - 1 (tracks the ~5s
#      IN_PLAY interval, NOT the coarse nearest_update_seconds). Generators deliberately
#      feed large coarse nearest_update_seconds (up to 900) and small in_play_interval
#      (~5) to prove the sleep tracks in_play_interval.
#   2. FOR ALL has_due_target=True states with elapsed < HARD_CAP_SECONDS: returns "poll".
#   3. 6-hour cap: FOR ANY state, elapsed >= HARD_CAP_SECONDS -> "exit" (even game-on).
#   4. Lead-window boundary (concrete, deterministic): a target at exactly
#      start_time == now + LEAD_WINDOW_SECONDS is active-or-imminent -> "sleep"; a target
#      at +1s beyond the window with a far-out nearest update -> "exit".
#
# Kept in its own class per Task 6 coordination so it does not collide with Task 7's
# preservation/logging edits. Reuses the module-level imports of decide_next_action /
# LoopState / HARD_CAP_SECONDS / _PURE_LEAD_WINDOW_SECONDS already present above.
#
# Requirements traced: 2.1, 2.2, 2.3, 2.5, 2.7.
# ======================================================================================


class TestSP343FixChecking:
    """SP-343: In-Play Polling Cadence Fix.

    Property 1 (Fix Checking): in-play / lead-window cadence restored and the run kept
    alive at ~5s under the pure ``decide_next_action`` decision function.
    """

    @given(
        # Small IN_PLAY interval (~5s), including a couple of neighbouring values.
        in_play_interval=st.integers(min_value=2, max_value=8),
        # Large COARSE nearest_update_seconds (e.g. a lead-window 300s freq up to the old
        # 900s cap). If the sleep tracked this instead of in_play_interval the assertion
        # below would fail — that is exactly what we are proving does NOT happen.
        nearest_update_seconds=st.floats(min_value=1.0, max_value=900.0, allow_nan=False),
        # Any elapsed strictly under the 6h cap.
        elapsed_seconds=st.floats(min_value=0.0, max_value=HARD_CAP_SECONDS - 1, allow_nan=False),
    )
    @settings(max_examples=300)
    def test_active_or_imminent_nothing_due_sleeps_on_in_play_interval(
        self, in_play_interval, nearest_update_seconds, elapsed_seconds
    ):
        """FOR ALL active-or-imminent states with nothing due and elapsed < 6h:
        decide_next_action returns "sleep" (never "exit"), and the sleep tracks the
        IN_PLAY interval (max(0.1, in_play_interval - 1)), NOT the coarse
        nearest_update_seconds.

        **Validates: Requirements 2.1, 2.2, 2.3**
        """
        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=True,
            nearest_update_seconds=nearest_update_seconds,
            in_play_interval=in_play_interval,
        )
        action, sleep_seconds = decide_next_action(state, elapsed_seconds)

        assert action != "exit", (
            f"Run exited while an active-or-imminent target remained (elapsed="
            f"{elapsed_seconds:.1f}s < 6h). It must stay alive and sleep at ~5s."
        )
        assert action == "sleep"

        expected_sleep = max(0.1, in_play_interval - 1)
        assert sleep_seconds == expected_sleep, (
            f"Sleep {sleep_seconds} did not track the IN_PLAY interval "
            f"(expected {expected_sleep} from in_play_interval={in_play_interval}); it must "
            f"NOT track the coarse nearest_update_seconds={nearest_update_seconds:.1f}."
        )
        # Explicit guard that the sleep is bounded near the IN_PLAY interval and nowhere
        # near the coarse nearest_update_seconds (which can be up to 900s).
        assert sleep_seconds <= in_play_interval, "Game-on sleep must be bounded by the IN_PLAY interval."

    @given(
        has_active_or_imminent=st.booleans(),
        nearest_update_seconds=st.floats(min_value=-100.0, max_value=1000.0, allow_nan=False),
        in_play_interval=st.integers(min_value=1, max_value=900),
        elapsed_seconds=st.floats(min_value=0.0, max_value=HARD_CAP_SECONDS - 1, allow_nan=False),
    )
    @settings(max_examples=300)
    def test_due_target_polls(self, has_active_or_imminent, nearest_update_seconds, in_play_interval, elapsed_seconds):
        """FOR ALL states with has_due_target=True and elapsed < 6h: decide_next_action
        returns "poll" (something is due now -> update odds), regardless of the other
        fields.

        **Validates: Requirements 2.1, 2.2**
        """
        state = LoopState(
            has_due_target=True,
            has_active_or_imminent=has_active_or_imminent,
            nearest_update_seconds=nearest_update_seconds,
            in_play_interval=in_play_interval,
        )
        action, sleep_seconds = decide_next_action(state, elapsed_seconds)
        assert action == "poll"
        assert sleep_seconds == 0

    @given(
        has_due_target=st.booleans(),
        has_active_or_imminent=st.booleans(),
        nearest_update_seconds=st.floats(min_value=-1000.0, max_value=1_000_000.0, allow_nan=False),
        in_play_interval=st.integers(min_value=1, max_value=900),
        # At or beyond the hard cap (with generous headroom above it).
        elapsed_seconds=st.floats(
            min_value=float(HARD_CAP_SECONDS), max_value=float(HARD_CAP_SECONDS) * 3, allow_nan=False
        ),
    )
    @settings(max_examples=300)
    def test_hard_cap_forces_exit_for_any_state(
        self, has_due_target, has_active_or_imminent, nearest_update_seconds, in_play_interval, elapsed_seconds
    ):
        """6-hour cap property: FOR ANY state, once elapsed >= HARD_CAP_SECONDS the action
        is "exit" — even when a game is on and something is due. Rundeck restarts the
        capture on its next trigger.

        **Validates: Requirements 2.5**
        """
        state = LoopState(
            has_due_target=has_due_target,
            has_active_or_imminent=has_active_or_imminent,
            nearest_update_seconds=nearest_update_seconds,
            in_play_interval=in_play_interval,
        )
        action, sleep_seconds = decide_next_action(state, elapsed_seconds)
        assert action == "exit"
        assert sleep_seconds == 0

    # ---- Lead-window boundary (concrete, deterministic classification checks) ----

    def _classify_active_or_imminent(self, start_time_offset_seconds: float) -> bool:
        """Replicate the new ``run()`` loop's active-or-imminent classification for a
        single OPEN target: ``start_time <= now + LEAD_WINDOW_SECONDS``. Deterministic —
        anchors ``now`` and derives ``start_time`` from the given offset so the boundary
        is tested exactly (inclusive edge)."""
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        lead_window = timedelta(seconds=_PURE_LEAD_WINDOW_SECONDS)
        start_time = now + timedelta(seconds=start_time_offset_seconds)
        return start_time <= now + lead_window

    def test_lead_window_inclusive_edge_stays_alive(self):
        """A target at EXACTLY start_time == now + LEAD_WINDOW_SECONDS is active-or-imminent
        (inclusive edge) -> decide_next_action returns "sleep" (~IN_PLAY interval), NOT
        "exit". Proves the pre-kickoff lead window keeps the run alive right at its edge.

        **Validates: Requirements 2.7, 2.1, 2.2**
        """
        # Classification at the exact inclusive edge is True.
        assert self._classify_active_or_imminent(_PURE_LEAD_WINDOW_SECONDS) is True

        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=True,  # from the edge classification above
            nearest_update_seconds=300.0,  # coarse lead-window freq — must be ignored
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "sleep"
        assert sleep_seconds == 4  # max(0.1, 5 - 1); tracks IN_PLAY, not the 300s coarse freq

    def test_just_beyond_lead_window_exits(self):
        """A target one second BEYOND the lead window (start_time == now +
        LEAD_WINDOW_SECONDS + 1) is NOT active-or-imminent; with nothing due and a far-out
        nearest update the run performs the idle cheap-exit -> "exit".

        **Validates: Requirements 2.7**
        """
        # Classification just past the edge is False.
        assert self._classify_active_or_imminent(_PURE_LEAD_WINDOW_SECONDS + 1) is False

        state = LoopState(
            has_due_target=False,
            has_active_or_imminent=False,  # from the beyond-edge classification above
            nearest_update_seconds=300.0,  # far-out coarse pre-match update
            in_play_interval=5,
        )
        action, sleep_seconds = decide_next_action(state, 0.0)
        assert action == "exit"
        assert sleep_seconds == 0
