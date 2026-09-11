# Bugfix Requirements Document

## Introduction

The Betfair capture (`bf_trader_py`) is meant to sample in-play markets at the `IN_PLAY` 5-second cadence tier. In practice, the observed in-play sampling collapses to a ~900-second median gap (~24 in-play rows per runner versus the ~1,300–1,400 expected at 5s — roughly 180x too sparse). In-play odds movement is the most valuable, fast-moving data the system collects, and it is perishable: once a match is over the data cannot be back-filled. This defect therefore causes permanent loss of high-value data on every in-play match.

The monitor is **not** a long-lived daemon. It is triggered by Rundeck every ~15 minutes via `docker compose ... run --rm bf_capture ...`, so every trigger spawns a fresh, independent one-off container. The root cause is in `monitor_service.py::run()`: the loop exits as soon as no target is *due at that exact instant* (`get_filtered_targets` only returns targets where `seconds_until_next_update_required < 0`), and additionally bails out via the fixed `range(15 * 60)` bound and the `nearest_update_seconds > MONITOR_MAX_WAIT_SECONDS` (900s) check. As a result the effective in-play cadence collapses to the Rundeck re-trigger interval (~900s) rather than the 5s `IN_PLAY` tier interval. Tier *selection* itself is correct (`select_tier` / inline tier logic in `logic/simpleStategy.py` and the tiers in `config/strategy.yaml`); the defect is purely in the run-loop lifecycle and the absence of a single-instance guard needed to safely keep an in-play run alive across triggers.

Jira ticket: **SP-343**. Related: SP-328 (established the capture + cadence tiers) and SP-332 (detect-only data-quality verification, which correctly flags matches captured under this defect — SP-332 logic must not be changed here).

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN an OPEN in-play target exists (status OPEN and `start_time` has passed) THEN the system samples its runners at an effective interval of ~900s (bounded by the Rundeck re-trigger interval) instead of the ~5s `IN_PLAY` tier interval.

1.2 WHEN a run tick completes and no target's next update is due at that exact instant (`get_filtered_targets` returns an empty `filtered_targets`) THEN the system breaks out of the loop and exits the run, even though an in-play target is only ~5s away from being due.

1.3 WHEN the nearest upcoming update is more than `MONITOR_MAX_WAIT_SECONDS` (900s) away, OR the fixed `range(15 * 60)` bound is reached THEN the system exits the run, so an in-play run cannot survive long enough to keep sampling at 5s across the 15-minute Rundeck cycle.

1.4 WHEN Rundeck spawns a new one-off `run --rm` container while a prior run is still active THEN the system provides no single-instance guard, so overlapping concurrent runs can write odds simultaneously (there is no lock in the runtime code path).

1.5 WHEN a run starts shortly before kickoff (an OPEN target whose `start_time` is a few minutes in the future, not yet in-play) THEN the system sees nothing due at the coarse pre-match interval (`get_filtered_targets` reports the next update ~300s away for a `LESS_THAN_3H` target) and exits the run, so it does not resume 5s sampling until a Rundeck trigger lands after kickoff (up to ~15 min later), losing the opening minutes of the match.

### Expected Behavior (Correct)

2.1 WHEN an OPEN target is active-or-imminent (status OPEN and `start_time <= now() + LEAD_WINDOW_SECONDS`, i.e. in-play OR within the 20-minute pre-kickoff lead window) THEN the system SHALL sample its runners at or near the `IN_PLAY` tier interval (~5s), restoring in-play cadence to approximately the configured 5s (at or near 5s is acceptable; no hard sub-tolerance required).

2.2 WHEN a run tick completes and no target's next update is due at that exact instant, but an OPEN active-or-imminent target remains (in-play OR within the lead window) THEN the system SHALL sleep on the `IN_PLAY` interval (~5s) and continue the loop, rather than exiting.

2.3 WHEN an OPEN active-or-imminent target remains (in-play OR within the lead window) THEN the system SHALL stay alive and keep looping at the 5s cadence rather than bailing out at the 900s / 15-minute budget; the `MONITOR_MAX_WAIT_SECONDS` and fixed `range(15 * 60)` game-on limiters SHALL be removed as run-loop bail-out conditions.

2.4 WHEN no OPEN target remains that is in-play or within the 20-minute pre-kickoff lead window (every OPEN target's `start_time > now() + LEAD_WINDOW_SECONDS`, or all relevant markets have CLOSED) THEN the system SHALL end the run and return to idle / Rundeck-driven mode.

2.5 WHEN a run has been alive for a hard maximum duration of 6 hours THEN the system SHALL exit as a safety backstop, relying on Rundeck to restart it, so a stuck run cannot live forever.

2.6 WHEN Rundeck spawns a new one-off `run --rm` container while a prior run is already active and healthy THEN the system SHALL detect this via a lightweight single-instance guard (a Postgres advisory / row lock) and exit immediately as a no-op, and the guard SHALL release or expire cleanly on run exit or crash so it cannot poison future runs.

2.7 WHEN an OPEN target's `start_time` is within `LEAD_WINDOW_SECONDS` (20 minutes) in the future (not yet in-play) THEN the system SHALL treat it as game-on: stay alive and poll at the ~5s `IN_PLAY` cadence right through kickoff, so the opening minutes are captured rather than lost to a post-kickoff Rundeck trigger. `LEAD_WINDOW_SECONDS` (config key `MONITOR_LEAD_WINDOW_SECONDS`, default 1200s) MUST exceed the ~15-minute Rundeck re-trigger interval plus margin so a run is always already alive and polling at 5s before any kickoff. This 5s lead-window cadence SHALL be driven by the run loop, not by tier selection.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a target is pre-match and OUTSIDE the lead window (more than 20 minutes from kickoff, `start_time > now() + LEAD_WINDOW_SECONDS`) THEN the system SHALL CONTINUE TO select and apply the coarse cadence tiers correctly (LESS_THAN_3H=300s, LESS_THAN_6H=900s, LESS_THAN_12H=3600s, MORE_THAN_12H=14400s).

3.2 WHEN no game is on (no OPEN target is in-play or within the 20-minute lead window — every OPEN target is more than 20 minutes from kickoff — and the next update is far out) THEN the system SHALL CONTINUE TO behave as an idle, Rundeck-driven one-off run: poll at the coarse tier and exit cheaply rather than staying alive.

3.3 WHEN tier selection is requested for a given time-to-event THEN the system SHALL CONTINUE TO return the correct interval per `select_tier` / the inline tier logic and the `config/strategy.yaml` tier configuration (tier selection is not the defect and must not change). In particular, `select_tier` SHALL CONTINUE TO return the coarse tier (e.g. `LESS_THAN_3H=300s`) for a pre-match target even when it is inside the 20-minute lead window; the ~5s lead-window cadence is loop-driven and is achieved WITHOUT changing `select_tier` or the stored `update_frequency`.

3.4 WHEN a run exits normally or crashes THEN the system SHALL CONTINUE TO record run start/end markers and errors to the durable run log (`bf.log_file`) as it does today.

3.5 WHEN post-event data-quality verification (SP-332) evaluates captured matches THEN the system SHALL CONTINUE TO leave that detect-only logic unchanged; this fix does not alter SP-332 behaviour.

## Bug Condition Derivation

**Key definitions**
- **F**: the original (unfixed) `run()` loop in `monitor_service.py`.
- **F'**: the fixed `run()` loop.
- **LEAD_WINDOW_SECONDS**: the pre-kickoff lead window, `20 * 60 = 1200s` (config key `MONITOR_LEAD_WINDOW_SECONDS`). An OPEN target with `start_time <= now() + LEAD_WINDOW_SECONDS` is treated as active-or-imminent ("game on"). Chosen to exceed the ~15-min Rundeck re-trigger interval plus margin.

**Bug Condition** — identifies inputs that trigger the bug:

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type MonitorRunState   // set of targets + timing at run time
  OUTPUT: boolean

  // True when at least one OPEN target is in-play OR imminent (within the
  // 20-minute pre-kickoff lead window) whose runners are being sampled at the
  // Rundeck-bounded ~900s interval instead of the ~5s IN_PLAY tier interval.
  RETURN EXISTS t IN X.targets
           WHERE t.status = "OPEN"
             AND t.start_time <= now() + LEAD_WINDOW_SECONDS   // in-play OR within lead window
             AND effective_sample_interval(t) >> IN_PLAY_interval(5s)
END FUNCTION
```

**Property: Fix Checking** — desired behavior for buggy inputs:

```pascal
// Property: In-play/imminent cadence restored to the IN_PLAY tier
FOR ALL X WHERE isBugCondition(X) DO
  result ← run'(X)
  ASSERT effective_sample_interval(active_or_imminent_target(X)) ≈ IN_PLAY_interval(5s)
     AND run_stays_alive_while(active_or_imminent_target_remains AND elapsed < 6h)
     AND single_instance_guard_held_by_exactly_one_run()
     AND run_exits_when(no_active_or_imminent_target_remains OR elapsed >= 6h)
END FOR
```

**Property: Preservation Checking** — non-buggy inputs must be unchanged:

```pascal
// Property: Preservation of pre-match / idle behaviour and tier selection.
// "Not buggy" means no OPEN target is in-play or within the 20-minute lead window
// (every OPEN target's start_time > now() + LEAD_WINDOW_SECONDS).
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)   // coarse tiers, idle-mode cheap exit, tier selection,
                        // durable run logging, and SP-332 all behave identically
END FOR
```
