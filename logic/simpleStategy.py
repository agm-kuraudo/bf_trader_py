import os
from dataclasses import dataclass
from datetime import timedelta

import yaml

from output.log import Output as Log


class StrategyException(Exception):
    pass


# ======================================================================================
# SP-343: In-Play Polling Cadence Fix — pure run-loop decision logic.
#
# These are the PURE defaults the decision function needs. The configurable
# MONITOR_HARD_CAP_SECONDS / MONITOR_LEAD_WINDOW_SECONDS variants are wired into
# DefaultStrategy / FromFileStrategy separately (see Task 3); do not duplicate them here.
# ======================================================================================

# 6-hour hard safety cap: an in-play run exits at this wall-clock age regardless of state,
# relying on Rundeck to restart it, so a stuck run cannot live forever (Req 2.5).
HARD_CAP_SECONDS = 6 * 3600  # 21600s

# Pre-kickoff lead window: an OPEN target with start_time <= now() + LEAD_WINDOW_SECONDS is
# treated as "game on" (in-play OR imminent). Chosen to exceed the ~15-min Rundeck
# re-trigger interval plus margin so a run is always already alive at 5s before kickoff.
LEAD_WINDOW_SECONDS = 20 * 60  # 1200s

# IN_PLAY tier interval default (~5s); the loop drives the game-on cadence from this.
IN_PLAY_INTERVAL_DEFAULT = 5


@dataclass
class LoopState:
    """Snapshot of the run-loop's decision inputs at a single iteration.

    Pure data carrier so ``decide_next_action`` (and its tests) can be constructed without
    a live Postgres DB or the Betfair API.

    Attributes:
        has_due_target: True when ``filtered_targets`` is non-empty (something is due now).
        has_active_or_imminent: True when at least one OPEN target is in-play OR within the
            lead window (``start_time <= now() + LEAD_WINDOW_SECONDS``) — the "game on" signal.
        nearest_update_seconds: Min seconds until the next update across OPEN targets. Kept
            for completeness/diagnostics; the game-on sleep is driven by ``in_play_interval``,
            NOT this coarse value (see ``decide_next_action``).
        in_play_interval: The IN_PLAY tier interval (~5s) used for the game-on sleep.
    """

    has_due_target: bool
    has_active_or_imminent: bool
    nearest_update_seconds: float
    in_play_interval: float


def decide_next_action(state: LoopState, elapsed_seconds: float) -> tuple[str, float]:
    """Decide the run-loop's next action for one iteration.

    Pure, DB-free, API-free. Captures the loop's exit-vs-sleep-vs-poll logic so the
    hard-to-test lifecycle can be property-tested in isolation (mirrors how ``select_tier``
    is pure and tested while the services stay thin).

    Behaviour (SP-343 design "Change 1"):
      1. ``elapsed_seconds >= HARD_CAP_SECONDS`` -> ``("exit", 0)``   (6h safety backstop, Req 2.5)
      2. ``state.has_due_target``               -> ``("poll", 0)``   (something due now -> update odds)
      3. ``state.has_active_or_imminent``       -> ``("sleep", max(0.1, in_play_interval - 1))``
         Game on (in-play OR within the lead window) but nothing due this instant: sleep on
         the IN_PLAY interval (~5s), NOT the coarse ``nearest_update_seconds`` — so a
         pre-match target inside the lead window is still polled at 5s (Req 2.1, 2.2, 2.3).
      4. otherwise                              -> ``("exit", 0)``   (idle cheap-exit, Req 2.4)

    Args:
        state: The current :class:`LoopState`.
        elapsed_seconds: Wall-clock seconds since the run started (monotonic).

    Returns:
        ``(action, sleep_seconds)`` where ``action`` is one of ``"poll"``, ``"sleep"``,
        ``"exit"``. ``sleep_seconds`` is only meaningful for ``"sleep"`` (``0`` otherwise).
    """
    if elapsed_seconds >= HARD_CAP_SECONDS:
        return ("exit", 0)

    if state.has_due_target:
        return ("poll", 0)

    if state.has_active_or_imminent:
        # Drive the game-on cadence from the IN_PLAY interval, never the coarse
        # nearest_update_seconds (which would under-sample a lead-window pre-match target).
        return ("sleep", max(0.1, state.in_play_interval - 1))

    return ("exit", 0)


def select_tier(tiers: dict | None, time_until_start: timedelta) -> int:
    """Select the polling interval (seconds) for a target given its time to event start.

    Cadence tier selection for background odds capture (Req 2.2). Given a tier config
    dict mapping tier names to intervals in seconds and a timedelta until event start,
    return the correct polling interval.

    The function is:
      - TOTAL: it returns a defined interval for any ``time_until_start`` value,
        including negative (in-play) values.
      - MONOTONIC: given the default tier ordering (IN_PLAY <= LESS_THAN_3H <=
        LESS_THAN_6H <= LESS_THAN_12H <= MORE_THAN_12H), a larger time-to-event never
        yields a shorter interval, so two callers picking a tier for the same
        time-to-event always get the same interval.

    If ``tiers`` is None, the defaults from ``DefaultStrategy.UPDATE_FREQUENCY_TIERS``
    are used. Missing individual keys fall back to the documented per-tier defaults.
    """
    if tiers is None:
        tiers = DefaultStrategy.UPDATE_FREQUENCY_TIERS

    seconds_until_start = time_until_start.total_seconds()
    if seconds_until_start <= 0:
        return tiers.get("IN_PLAY", 5)
    elif seconds_until_start <= 3 * 3600:
        return tiers.get("LESS_THAN_3H", 300)
    elif seconds_until_start <= 6 * 3600:
        return tiers.get("LESS_THAN_6H", 900)
    elif seconds_until_start <= 12 * 3600:
        return tiers.get("LESS_THAN_12H", 3600)
    else:
        return tiers.get("MORE_THAN_12H", 14400)


class DefaultStrategy:
    # Filter constants
    EVENTS = ["Soccer"]
    COMPETITIONS = ["English Premier League", "UEFA Champions League"]
    MARKET_TYPEs = ["MATCH_ODDS"]
    MAX_EVENTS = 5
    MIN_DAYS_TILL_START = 1
    MAX_DAYS_TILL_START = 5
    NEWEST_FIRST = True

    # Monitor timing configuration
    UPDATE_FREQUENCY_TIERS = {
        "IN_PLAY": 5,
        "LESS_THAN_3H": 300,
        "LESS_THAN_6H": 900,
        "LESS_THAN_12H": 3600,
        "MORE_THAN_12H": 14400,
    }
    INITIAL_UPDATE_FREQUENCY = 14400
    STALE_TARGET_HOURS = 24
    MONITOR_MAX_WAIT_SECONDS = 900
    # 6-hour wall-clock safety backstop for a single in-play run (SP-343).
    MONITOR_HARD_CAP_SECONDS = 6 * 3600
    # Pre-kickoff lead window: an OPEN target within this many seconds of its
    # start_time is treated as "game on" and polled at the 5s IN_PLAY cadence.
    # MUST exceed the ~15-min Rundeck re-trigger interval (plus margin) so a run
    # is always alive at 5s before any kickoff (SP-343).
    MONITOR_LEAD_WINDOW_SECONDS = 20 * 60


class FromFileStrategy(DefaultStrategy):
    # EVENTS = super().EVENTS
    # COMPETITIONS = super().COMPETITIONS
    # MARKET_TYPEs = super().MARKET_TYPEs
    # MAX_EVENTS = super().MAX_EVENTS

    def __init__(self):
        try:
            absolute_path = os.path.dirname(__file__)
            relative_path = "../config/strategy.yaml"
            full_path = os.path.join(absolute_path, relative_path)

            with open(full_path) as f:
                yaml_content = yaml.safe_load(f.read())
                Log.log_info(f"Selected Strategy: {yaml_content}")
                DefaultStrategy.EVENTS = yaml_content["EVENTS"]
                DefaultStrategy.COMPETITIONS = yaml_content["COMPETITIONS"]
                DefaultStrategy.MAX_EVENTS = yaml_content["MAX_EVENTS"]
                DefaultStrategy.MIN_DAYS_TILL_START = yaml_content["MIN_DAYS_TILL_START"]
                DefaultStrategy.MAX_DAYS_TILL_START = yaml_content["MAX_DAYS_TILL_START"]
                DefaultStrategy.NEWEST_FIRST = yaml_content["NEWEST_FIRST"]
                # Monitor timing configuration (optional keys with safe defaults)
                DefaultStrategy.UPDATE_FREQUENCY_TIERS = yaml_content.get(
                    "UPDATE_FREQUENCY_TIERS", DefaultStrategy.UPDATE_FREQUENCY_TIERS
                )
                DefaultStrategy.INITIAL_UPDATE_FREQUENCY = yaml_content.get(
                    "INITIAL_UPDATE_FREQUENCY", DefaultStrategy.INITIAL_UPDATE_FREQUENCY
                )
                DefaultStrategy.STALE_TARGET_HOURS = yaml_content.get(
                    "STALE_TARGET_HOURS", DefaultStrategy.STALE_TARGET_HOURS
                )
                DefaultStrategy.MONITOR_MAX_WAIT_SECONDS = yaml_content.get(
                    "MONITOR_MAX_WAIT_SECONDS", DefaultStrategy.MONITOR_MAX_WAIT_SECONDS
                )
                DefaultStrategy.MONITOR_HARD_CAP_SECONDS = yaml_content.get(
                    "MONITOR_HARD_CAP_SECONDS", DefaultStrategy.MONITOR_HARD_CAP_SECONDS
                )
                DefaultStrategy.MONITOR_LEAD_WINDOW_SECONDS = yaml_content.get(
                    "MONITOR_LEAD_WINDOW_SECONDS",
                    DefaultStrategy.MONITOR_LEAD_WINDOW_SECONDS,
                )
        except Exception as e:
            raise StrategyException("Cannot read strategy from file") from e


# test = FromFileStrategy()
# print(FromFileStrategy.EVENTS)
# print(FromFileStrategy.COMPETITIONS)
