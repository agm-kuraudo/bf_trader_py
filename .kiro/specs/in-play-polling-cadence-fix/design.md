# In-Play Polling Cadence Fix — Bugfix Design

## Overview

The Betfair capture (`bf_trader_py`) is supposed to sample OPEN in-play markets at the `IN_PLAY` 5-second cadence tier, but in practice the effective in-play sample interval collapses to ~900s (the Rundeck re-trigger interval), producing ~180x too little in-play data. In-play odds are the most valuable, perishable data the system collects: once a match ends the data cannot be back-filled.

Tier *selection* is correct. `logic/simpleStategy.py::select_tier` already returns `IN_PLAY=5` for in-play targets, and `update_runner_odds` in `monitor_service.py` correctly writes `update_frequency=5` for a target whose `start_time` has passed. The defect is entirely in the **run-loop lifecycle** of `monitor_service.py::run()`: the loop bails out before the 5s cadence can take effect, and there is no single-instance guard to safely keep an in-play run alive across Rundeck triggers.

The fix (F') has two parts:

1. **Adaptive run-loop lifecycle** — replace the fixed `range(15 * 60)` iteration budget and the two premature bail-outs with a `while` loop bounded only by a 6-hour hard safety cap. While an OPEN target is **in-play or imminent** (see lead window below), sleep on the IN_PLAY interval (~5s) and keep looping; when no such target remains (genuinely idle), exit cheaply as today's one-off Rundeck run does.
2. **Postgres session-scoped advisory lock** — a single-instance guard acquired on the run's dedicated psycopg2 connection. Because it is session-scoped, it auto-releases when the connection closes or the container dies, which cleanly avoids the poisoned-lock failure mode that the old count-based marker scheme (see `scripts/fix_lock.py`) suffered from.

**Pre-kickoff lead window.** An earlier iteration of this design used `start_time <= now()` as the sole "game on" signal. That leaves a gap: a run that starts shortly *before* kickoff sees no in-play target, the pre-match tier says nothing is due for ~300s, so the run exits — and does not reliably resume until a Rundeck trigger lands *after* kickoff (up to ~15 min later), losing the opening minutes of the match (the most valuable perishable data). To close this gap we introduce a **20-minute pre-kickoff lead window** (`LEAD_WINDOW_SECONDS = 20 * 60 = 1200s`). An OPEN target whose `start_time` is within the next 20 minutes is treated as "game on": the run stays alive and polls at the 5s IN_PLAY cadence right through kickoff. 20 minutes is deliberately chosen to exceed the ~15-min Rundeck re-trigger interval plus margin, so a kickoff can never be missed between triggers. We deliberately chose the simpler, safest "straight to 5s" behaviour over a separate pre-match cadence — 20 min of 5s polling on a handful of runners is trivial load, and the pre-kickoff drift is itself useful data. Crucially, this is achieved purely by the **loop** choosing to poll/sleep on the IN_PLAY interval while in the imminent-or-in-play state; `select_tier` and the stored `update_frequency` are **unchanged** (see the non-conflict note in Fix Implementation).

The change is deliberately minimal and surgical: it touches the run-loop control flow in `monitor_service.py::run()` and adds a small advisory-lock helper to `output/dboutput.py`. It does **not** redesign tier selection (`logic/simpleStategy.py`, `config/strategy.yaml`) or the SP-332 detect-only data-quality logic (`logic/quality_checks.py`).

Jira ticket: **SP-343**.

## Glossary

- **Bug_Condition (C)**: The condition that triggers the bug — at least one OPEN target is in-play or imminent (status `OPEN` and `start_time <= now() + LEAD_WINDOW_SECONDS`) yet its runners are sampled at the Rundeck-bounded ~900s interval instead of the ~5s `IN_PLAY` tier.
- **Property (P)**: The desired behaviour for buggy inputs — an active-or-imminent run stays alive and samples at (or near) the `IN_PLAY` 5s interval, guarded by a single-instance lock, and exits only when no in-play/imminent target remains or the 6-hour cap is hit.
- **Preservation**: Behaviour that must remain unchanged — coarse pre-match tiers, idle cheap-exit, `select_tier` tier selection, durable run start/end logging (including on crash), and the SP-332 detect-only verification.
- **F**: The original (unfixed) `run()` loop in `monitor_service.py`.
- **F'**: The fixed `run()` loop.
- **`run()`**: The method in `monitor_service.py` that opens a DB connection, authenticates, and loops fetching/updating target odds.
- **`get_filtered_targets`**: Method in `monitor_service.py` returning `(targets_to_update, nearest_update_seconds)` — targets already due (`seconds_until_next_update_required < 0`) and the minimum time-to-next-update across OPEN targets.
- **`update_runner_odds`**: Method in `monitor_service.py` that writes odds rows to `bf.market_table` and sets each target's `update_frequency` (from the tier) and `last_updated=NOW()`.
- **`select_tier`**: Pure, TOTAL, MONOTONIC function in `logic/simpleStategy.py` mapping time-to-event-start to a polling interval. Correct today; **not changed** by this fix (it still returns `LESS_THAN_3H=300` for a pre-match target inside the lead window).
- **OPEN in-play target**: A loaded target with status `OPEN` whose `start_time <= now()` (the match has already started).
- **Active-or-imminent target**: A loaded target with status `OPEN` whose `start_time <= now() + LEAD_WINDOW_SECONDS` — i.e. in-play **or** within the pre-kickoff lead window. This (not just already-started) is the unambiguous signal for "game on" that keeps the run alive at the 5s cadence.
- **Lead window**: A fixed pre-kickoff interval, `LEAD_WINDOW_SECONDS = 20 * 60 = 1200s`, during which an OPEN target that has not yet started is nonetheless treated as game-on and polled at the 5s IN_PLAY cadence. Chosen to exceed the ~15-min Rundeck re-trigger interval (plus margin) so a kickoff is never missed between triggers.
- **Advisory lock**: A Postgres session-scoped lock (`pg_try_advisory_lock`) tied to the run's connection, used as the single-instance guard.

## Bug Details

### Bug Condition

The bug manifests when at least one OPEN target is in-play **or within the 20-minute pre-kickoff lead window** but its runners are sampled at ~900s instead of ~5s. The `run()` loop is exiting prematurely for one of three reasons: it breaks when nothing is due *at that exact instant* (`filtered_targets` empty), it breaks when the nearest upcoming update is more than `MONITOR_MAX_WAIT_SECONDS` (900s) away, or the fixed `range(15 * 60)` iteration budget is exhausted. A special case of the empty-`filtered_targets` break is the **pre-kickoff drop-out**: a run starting shortly before kickoff sees no already-started target, the pre-match tier says nothing is due for ~300s, so the run exits and does not resume at 5s until a Rundeck trigger lands after kickoff. Because each Rundeck trigger spawns a fresh `run --rm` one-off container, the effective cadence collapses to the ~15-minute Rundeck re-trigger interval.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type MonitorRunState   // loaded targets + timing at run time
  OUTPUT: boolean

  // True when at least one OPEN target is in-play OR imminent (within the lead
  // window) whose runners are being sampled at the Rundeck-bounded ~900s interval
  // instead of the ~5s IN_PLAY tier.
  RETURN EXISTS t IN X.targets
           WHERE t.status = "OPEN"
             AND t.start_time <= now() + LEAD_WINDOW_SECONDS   // in-play OR within lead window
             AND effective_sample_interval(t) >> IN_PLAY_interval(5s)
END FUNCTION
```

### Examples

- **In-play match, nothing due this instant (BUG A)**: One OPEN target, `start_time` 10 minutes ago, its next update is ~4s away. `get_filtered_targets` returns `filtered_targets == []` because nothing is due *right now*. Expected: sleep ~4s and keep sampling at 5s. Actual: `if len(filtered_targets) == 0: break` exits the run; the market is not sampled again until the next Rundeck trigger (~900s later).
- **Pre-kickoff drop-out (BUG A', lead-window case)**: One OPEN target, `start_time` 8 minutes in the future. It is not yet in-play, so the old `start_time <= now()` signal treats it as not-game-on; its stored `update_frequency` is the coarse `LESS_THAN_3H=300`, so `get_filtered_targets` reports nothing due for ~300s and `nearest_update_seconds ≈ 300`. Expected: recognise the target is within the 20-min lead window, stay alive, and poll at 5s straight through kickoff. Actual: the empty-`filtered_targets` break exits the run; the opening minutes are only captured once a Rundeck trigger lands after kickoff (up to ~15 min later).
- **In-play match, nearest update far in a mixed set (BUG B)**: OPEN in-play target due in ~5s but computation of `nearest_update_seconds` across all OPEN targets yields a value > 900s in an edge state, or a pre-match target dominates. Expected: stay alive for the in-play target. Actual: `if nearest_update_seconds > 900: break` exits.
- **Long in-play run (BUG C)**: An in-play match lasts ~50 minutes. Even at a perfect 5s cadence a single container would need ~600 iterations; combined with sleeps the fixed `range(15 * 60)` budget and the 900s wait cap terminate the run well before the match ends. Expected: keep looping until the match CLOSES or the 6h cap. Actual: run terminates early.
- **Idle, no game on (NOT a bug — must be preserved)**: No OPEN target that is in-play or within the 20-min lead window; nearest update is hours away. Expected and actual: poll at the coarse tier and exit cheaply as a Rundeck-driven one-off run.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Pre-match targets CONTINUE to select and apply coarse cadence tiers (`LESS_THAN_3H=300`, `LESS_THAN_6H=900`, `LESS_THAN_12H=3600`, `MORE_THAN_12H=14400`) via `select_tier` and `update_runner_odds` (Req 3.1).
- When no game is on, the run CONTINUES to behave as an idle Rundeck-driven one-off: poll at the coarse tier and exit cheaply rather than staying alive (Req 3.2).
- Tier selection via `select_tier` / the inline tier logic and `config/strategy.yaml` is UNCHANGED (Req 3.3).
- Durable run start/end markers and error records CONTINUE to be written to `bf.log_file`, including on crash via the existing top-level except block that writes `"Monitor Service: ERROR : Ending run with failure : ..."` (Req 3.4).
- SP-332 post-event data-quality verification (`logic/quality_checks.py`) is detect-only and UNCHANGED by this fix (Req 3.5).

**Scope:**
All inputs where `isBugCondition(X)` is false — i.e. no OPEN target is in-play or within the 20-min lead window — must be completely unaffected. This includes:
- Pre-match-only target sets where every OPEN target is more than `LEAD_WINDOW_SECONDS` (20 min) from kickoff (`start_time > now() + LEAD_WINDOW_SECONDS`): these STILL use coarse tiers and can STILL trigger idle cheap-exit (Req 3.1, 3.2).
- Idle runs with no due targets and a far-out nearest update.
- Tier-selection calls for any time-to-event (`select_tier` is unchanged; the lead-window 5s cadence is driven by the loop, not by tier selection).
- Run start/end/failure logging behaviour.

**Note:** The desired correct behaviour for buggy inputs is defined in the Correctness Properties section (Property 1). This section focuses on what must NOT change.

## Hypothesized Root Cause

The requirements and code inspection point to three concrete run-loop defects plus a missing guard:

1. **Empty-`filtered_targets` immediate break (BUG A / A')**: `run()` contains `if len(filtered_targets) == 0: break`. `get_filtered_targets` only returns targets already due (`seconds_until_next_update_required < 0`), so a target due in ~5s produces an empty list and the run exits instead of sleeping ~5s. This is the primary cause of the collapse to the Rundeck interval. Its **pre-kickoff variant (A')** is that a target within the lead window carries a coarse stored `update_frequency` (e.g. 300s), so `get_filtered_targets` never marks it due at the 5s cadence and the run drops out just before kickoff. The fix must therefore (a) treat imminent-or-in-play targets as "game on" and (b) drive their cadence from the IN_PLAY interval rather than from their stored coarse `update_frequency` (see Change 2/Change 5).

2. **`MONITOR_MAX_WAIT_SECONDS` bail-out (BUG B)**: `if nearest_update_seconds > DefaultStrategy.MONITOR_MAX_WAIT_SECONDS: break` (900s). Intended as an idle cheap-exit, but as a *game-on* bail-out it terminates in-play runs prematurely and must not gate in-play continuation.

3. **Fixed `range(15 * 60)` iteration budget (BUG C)**: caps total iterations regardless of wall-clock time, so a long in-play run cannot survive. The budget should be replaced by a wall-clock 6-hour safety cap.

4. **No single-instance guard (Req 1.4)**: the runtime code path has no lock. The old count-based `bf.log_file` marker scheme (evidenced by `scripts/fix_lock.py`) was poisoned by crashed runs leaving unbalanced markers and had to be un-poisoned manually; it has since been removed from the runtime path. Keeping an in-play run alive across Rundeck triggers makes overlapping `run --rm` containers likely, so a robust, self-releasing single-instance guard is required.

## Correctness Properties

Property 1: Bug Condition — In-play/imminent cadence restored and run kept alive under a single-instance guard

_For any_ run state where the bug condition holds (`isBugCondition` returns true — at least one OPEN target that is in-play or imminent, `start_time <= now() + LEAD_WINDOW_SECONDS`), the fixed `run()` loop SHALL keep the run alive and, when nothing is due at that instant, sleep on the IN_PLAY interval (~5s) and continue looping rather than exiting — including for a pre-match target still within the 20-min lead window whose stored `update_frequency` is coarse (e.g. 300s); SHALL exit only when no OPEN in-play-or-imminent target remains or the 6-hour hard cap is reached; and SHALL hold the single-instance advisory lock so exactly one run samples at a time. Equivalently, the pure decision function `decide_next_action(state, elapsed)` SHALL never return `"exit"` while an active-or-imminent target remains and `elapsed < 6h`; it returns `"poll"` when such a target is due and `"sleep"` (with `sleep_seconds ≈ IN_PLAY interval`) otherwise.

**Validates: Requirements 2.1, 2.2, 2.3, 2.5, 2.6, 1.4**

Property 2: Preservation — Idle cheap-exit, coarse tiers, and tier selection unchanged

_For any_ run state where the bug condition does NOT hold (`isBugCondition` returns false — no OPEN in-play target remains and the nearest update is far out), the fixed function SHALL produce the same result as the original function: `decide_next_action` returns `"exit"` (idle cheap-exit), coarse pre-match tiers are selected and applied identically, `select_tier` returns identical intervals for all time-to-event inputs, durable run start/end/failure logging to `bf.log_file` is identical (including on crash), and the SP-332 detect-only logic is untouched.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

Assuming the root-cause analysis is correct, the fix has three coordinated pieces.

#### Change 1 — Extract a pure loop-decision function

**File**: `logic/simpleStategy.py` (co-located with `select_tier`, the existing pure timing logic)

Add a pure, DB-free, API-free decision function that captures the loop's *exit vs sleep vs poll* logic so it can be property-tested in isolation (mirrors how `select_tier`, `logic/deploy_checks.py`, and `logic/quality_checks.py` are pure and tested while the services stay thin):

```
CONSTANT HARD_CAP_SECONDS   = 6 * 3600    # 6-hour safety backstop
CONSTANT LEAD_WINDOW_SECONDS = 20 * 60    # pre-kickoff lead window (1200s); see Change 3
CONSTANT IN_PLAY_INTERVAL    = 5          # IN_PLAY tier interval, from tiers["IN_PLAY"]

FUNCTION decide_next_action(state, elapsed_seconds)
  INPUT:
    state.has_due_target          : boolean  # filtered_targets non-empty (something due now)
    state.has_active_or_imminent  : boolean  # EXISTS OPEN target with start_time <= now() + LEAD_WINDOW_SECONDS
    state.nearest_update_seconds  : float    # min seconds until next update across OPEN targets
    state.in_play_interval        : float    # IN_PLAY tier interval (~5s)
    elapsed_seconds               : float    # wall-clock seconds since run start
  OUTPUT: (action, sleep_seconds)  where action IN {"poll","sleep","exit"}

  IF elapsed_seconds >= HARD_CAP_SECONDS THEN
    RETURN ("exit", 0)                        # 6h backstop (Req 2.5)

  IF state.has_due_target THEN
    RETURN ("poll", 0)                        # something due now -> update odds

  IF state.has_active_or_imminent THEN
    // Game on (in-play OR within the 20-min lead window) but nothing due this instant.
    // Sleep on the IN_PLAY interval (~5s), NOT the target's stored coarse update_frequency,
    // so a pre-match target inside the lead window is still polled at 5s. (Req 2.1, 2.2, 2.3)
    RETURN ("sleep", max(0.1, state.in_play_interval - 1))

  // No OPEN in-play-or-imminent target remains -> idle cheap-exit (preserved) (Req 2.4, 3.2)
  RETURN ("exit", 0)
END FUNCTION
```

`has_active_or_imminent` is derived unambiguously from the already-loaded targets: `any(t.status == "OPEN" and t.start_time <= now() + LEAD_WINDOW_SECONDS for t in targets)`. This is the single, explicit "game on" signal that separates in-play/imminent (stay alive at 5s) from genuinely idle (cheap exit). Note the deliberate change from the earlier `nearest_update_seconds - 1` sleep to `in_play_interval - 1`: while game-on we drive the cadence from the IN_PLAY interval directly rather than from `nearest_update_seconds`, because a lead-window pre-match target's `nearest_update_seconds` reflects its coarse 300s `update_frequency` and would otherwise under-sample the 20 minutes before kickoff. Targets outside the lead window never make `has_active_or_imminent` true, so they retain coarse-tier idle behaviour.

#### Change 2 — Rewrite the `run()` loop to use the decision function

**File**: `monitor_service.py`

**Function**: `run()`

Replace `for _i in range(15 * 60):` and the two premature breaks with a wall-clock-bounded `while` loop that delegates the exit/sleep/poll decision to `decide_next_action`:

```
run_start = time.monotonic()
lead_window = timedelta(seconds=DefaultStrategy.MONITOR_LEAD_WINDOW_SECONDS)
in_play_interval = DefaultStrategy.UPDATE_FREQUENCY_TIERS.get("IN_PLAY", 5)
reload_from_db = True
while True:
    if reload_from_db:
        raw_targets = self.get_targets()
        targets = self.process_targets(raw_targets)
        self.update_target_status(targets)
        self.fetch_odds_for_new_targets(raw_targets, targets)
    reload_from_db = False

    now = datetime.now(UTC)

    # Active-or-imminent = OPEN and within the lead window (in-play OR <= 20 min to kickoff)
    active_or_imminent = [
        t for t in targets
        if t[1] == "OPEN" and t[6] is not None and t[6] <= now + lead_window
    ]

    filtered_targets, nearest_update_seconds = self.get_filtered_targets(targets)

    # Reconcile the coarse-update_frequency problem (BUG A'): get_filtered_targets marks a
    # lead-window pre-match target due only every ~300s. While game-on we force the IN_PLAY
    # cadence by also treating any active-or-imminent target as due when >= in_play_interval
    # has elapsed since its last update, so the loop polls at 5s regardless of stored freq.
    inplay_due = [
        t for t in active_or_imminent
        if seconds_since_last_update(t) >= in_play_interval
    ]
    poll_targets = union_by_market(filtered_targets, inplay_due)

    state = LoopState(
        has_due_target=len(poll_targets) > 0,
        has_active_or_imminent=len(active_or_imminent) > 0,
        nearest_update_seconds=nearest_update_seconds,
        in_play_interval=in_play_interval,
    )
    action, sleep_seconds = decide_next_action(state, time.monotonic() - run_start)

    if action == "exit":
        break
    elif action == "poll":
        self.update_runner_odds(poll_targets)
        reload_from_db = True
    else:  # "sleep"
        time.sleep(sleep_seconds)
```

Notes:
- The processed-target tuple already carries API status at index `[1]` and event start time at index `[6]` (see `process_targets`: `(market, status, len(runner_list), runners, target[6], target[7], target[4])`). The design reads `has_active_or_imminent` from these; if a cleaner field mapping is preferred at implementation time it must preserve the same "status OPEN and `start_time <= now() + LEAD_WINDOW_SECONDS`" semantics.
- **Reconciling due-ness with the coarse `update_frequency` (BUG A').** `get_filtered_targets` computes due-ness from each target's stored `update_frequency`, which for a lead-window pre-match target is the coarse 300s — so it would not be marked due at the 5s cadence. The chosen concrete approach: the loop supplements `filtered_targets` by treating an active-or-imminent target as due whenever `in_play_interval` (~5s) has elapsed since its `last_updated`, and polls the union. This drives the 5s cadence from the imminent-or-in-play state **without** changing `select_tier` or the stored `update_frequency`. (`seconds_since_last_update`/`union_by_market` are small local helpers; the last-updated timestamp is already available on the loaded target rows.)
- `reload_from_db` semantics are preserved: reload after a poll, do not reload after a pure sleep.
- The game-on sleep interval is now the IN_PLAY interval (`max(0.1, in_play_interval - 1)`), computed inside the decision function; the earlier `nearest_update_seconds - 1` sleep would under-sample the lead window.
- `MONITOR_MAX_WAIT_SECONDS` and the `range(15 * 60)` bound are **removed as run-loop bail-out conditions** (Req 2.3). `MONITOR_MAX_WAIT_SECONDS` may remain a config constant for backward compatibility but is no longer consulted in the loop.

#### Change 3 — 6-hour hard safety cap and pre-kickoff lead window (config constants)

**File**: `logic/simpleStategy.py` (constants) and `monitor_service.py` (checks)

- Add `HARD_CAP_SECONDS = 6 * 3600` (surfaced via `DefaultStrategy` / `config/strategy.yaml` as `MONITOR_HARD_CAP_SECONDS` with a safe default, following the existing optional-key `.get()` pattern in `FromFileStrategy`).
- The cap is checked once per iteration inside `decide_next_action` against `time.monotonic() - run_start` (monotonic clock so it is immune to wall-clock adjustments). On reaching the cap the loop exits regardless of in-play state; Rundeck restarts the capture on its next trigger (Req 2.5).
- Add `LEAD_WINDOW_SECONDS = 20 * 60` (1200s), surfaced via `DefaultStrategy` / `config/strategy.yaml` as `MONITOR_LEAD_WINDOW_SECONDS` with a safe default, using the same optional-key `.get()` pattern. **Rationale to document in code and config:** the value MUST exceed the ~15-min Rundeck re-trigger interval (plus margin) so that at least one Rundeck-triggered run always starts within the lead window before any kickoff, guaranteeing a run is already alive and polling at 5s when the match starts. 20 min is the ~15-min interval plus ~5 min margin. This constant defines the imminent-or-in-play boundary used by `has_active_or_imminent`.

Both constants follow the existing `FromFileStrategy` pattern, e.g.:
```
DefaultStrategy.MONITOR_HARD_CAP_SECONDS = yaml_content.get(
    "MONITOR_HARD_CAP_SECONDS", DefaultStrategy.MONITOR_HARD_CAP_SECONDS
)
DefaultStrategy.MONITOR_LEAD_WINDOW_SECONDS = yaml_content.get(
    "MONITOR_LEAD_WINDOW_SECONDS", DefaultStrategy.MONITOR_LEAD_WINDOW_SECONDS
)
```

#### Change 3a — Critical non-conflict note: `select_tier` and `update_frequency` are UNCHANGED

The lead-window 5s cadence is achieved **entirely by the run-loop** choosing to poll/sleep on the IN_PLAY interval while a target is active-or-imminent. It is NOT achieved by changing tier selection.

- `select_tier` (`logic/simpleStategy.py`) is UNCHANGED. It still returns `LESS_THAN_3H=300` for a pre-match target that happens to be inside the 20-min lead window — this preserves Req 3.3.
- `update_runner_odds`'s write of `update_frequency` (`monitor_service.py`) is UNCHANGED. A lead-window pre-match target still has its stored `update_frequency` set to the coarse tier (e.g. 300s).
- Because of the above, `get_filtered_targets` (which derives due-ness from the stored `update_frequency`) will NOT mark a lead-window pre-match target due more often than every ~300s. The loop therefore drives the 5s cadence itself, via the "treat active-or-imminent targets as due when `in_play_interval` has elapsed since last update" reconciliation in Change 2 — never by mutating `select_tier` or the stored `update_frequency`.

**Implementer warning:** do NOT attempt to fix the lead-window cadence by making `select_tier` return 5 for pre-match times, or by writing a 5s `update_frequency` for pre-match targets. That would violate preservation Req 3.1/3.3 and corrupt the coarse-tier behaviour for targets legitimately outside the lead window. The only correct locus of change is the loop's due-ness/sleep decision.

#### Change 4 — Single-instance guard via Postgres session advisory lock

**File**: `output/dboutput.py` (new helper methods on `DBOutputConnection`) and `monitor_service.py` (acquire/release in `run()`)

Add advisory-lock helpers on `DBOutputConnection`:

```
RUN_ADVISORY_LOCK_KEY = 4310343   # fixed bigint key for the SP-343 capture run lock; document in code

def try_acquire_run_lock(self) -> bool:
    # SESSION-scoped advisory lock, tied to this connection/session.
    with self.get_cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s);", (RUN_ADVISORY_LOCK_KEY,))
        return bool(cursor.fetchone()[0])

def release_run_lock(self) -> None:
    with self.get_cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s);", (RUN_ADVISORY_LOCK_KEY,))
```

Behaviour in `run()`, immediately after `self.db_connection.open_connection(...)`:

```
if not self.db_connection.try_acquire_run_lock():
    Log.log_info("Monitor Service: INFO: Another run active, exiting as no-op", force_console_log=True)
    self.db_connection.close()
    return   # exit 0, no-op
```

On normal exit call `self.db_connection.release_run_lock()` before `close()`. On crash, do not rely on explicit release: the existing top-level except block already logs the failure marker, and closing the connection (or the container dying) drops the session, which **auto-releases** the session advisory lock. This is precisely what makes the lock self-healing and avoids the poisoned-lock problem that `scripts/fix_lock.py` had to patch for the old count-based scheme.

**Critical implementation note:** use `pg_try_advisory_lock` / `pg_advisory_unlock` (SESSION-scoped), NOT `pg_advisory_xact_lock` (transaction-scoped). `DBOutputConnection` sets `conn.autocommit = True`; under autocommit a transaction-scoped lock would be released the instant its statement's implicit transaction commits, giving no protection at all. Session-scoped advisory locks are tied to the connection/session and are unaffected by `autocommit`, so they persist for the life of the run and release on session close. `pg_try_advisory_lock` returns immediately (non-blocking), so a second concurrent run gets `false` and exits as a no-op rather than blocking.

Maps to Req 2.6 and 1.4.

## Testing Strategy

### Validation Approach

Two-phase: first surface counterexamples that demonstrate the bug on the UNFIXED code, then verify the fix restores 5s in-play cadence and preserves idle/pre-match behaviour. The loop's decision logic is extracted into the pure `decide_next_action` (Change 1) so the hard-to-test lifecycle can be property-tested without a live Postgres DB or the Betfair API — mirroring the repo's existing split of pure logic (`select_tier`, `logic/deploy_checks.py`, `logic/quality_checks.py`, tested under `tests/test_property_*.py`) from thin impure services (mocked in `tests/unit_tests_monitor_resilience.py`). The advisory lock, being inherently DB-bound, is validated with an integration-style test.

Tooling: `pytest` + `hypothesis` (already used — see `.hypothesis/` and `tests/test_property_*.py`). New tests live under `tests/` following the existing `test_property_*.py` and `unit_tests_*.py` naming.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix, and confirm/refute the root-cause hypothesis. If refuted, re-hypothesize.

**Test Plan**: Drive `run()` (or a faithful extraction of its current loop) with mocked DB/driver so that an OPEN in-play target exists but nothing is due at the current instant, and assert how many odds updates occur before the run exits. On the UNFIXED code the run exits after the empty-`filtered_targets` break (BUG A) / the 900s cap (BUG B) rather than sleeping ~5s and continuing.

**Test Cases**:
1. **Empty-filtered break exits mid-play**: OPEN in-play target due in ~4s, `filtered_targets == []` this instant — UNFIXED run exits immediately (will fail on unfixed code once the fix asserts "continue").
2. **Pre-kickoff drop-out (lead window)**: OPEN target with `start_time` ~8 min in the future and coarse stored `update_frequency=300` — UNFIXED run sees nothing due and exits before kickoff (will fail on unfixed code once the fix keeps it alive at 5s within the 20-min lead window).
3. **900s cap exits mid-play**: nearest update > 900s in a mixed set with an OPEN in-play target — UNFIXED run breaks (will fail on unfixed code).
4. **Iteration budget caps a long in-play run**: simulate enough ticks that `range(15*60)` would terminate — UNFIXED run stops before the match CLOSES (will fail on unfixed code).
5. **Edge — genuinely idle exit still correct**: no OPEN target in-play or within the 20-min lead window (all `start_time > now() + LEAD_WINDOW_SECONDS`), far-out nearest update — UNFIXED run exits cheaply (should PASS on unfixed code; guards against over-fixing).

**Expected Counterexamples**:
- Under an OPEN in-play target the run performs at most one update and then exits, giving an effective interval ≈ Rundeck re-trigger (~900s) rather than ~5s.
- Confirms the root cause is the run-loop bail-outs, not tier selection.

### Fix Checking

**Goal**: For all inputs where the bug condition holds, the fixed decision produces "stay alive and sleep ~IN_PLAY" (never "exit" while an active-or-imminent target remains and elapsed < 6h).

**Pseudocode:**
```
FOR ALL state WHERE isBugCondition(state) AND elapsed < 6h DO
  action, sleep_seconds := decide_next_action(state, elapsed)
  ASSERT action != "exit"
  ASSERT (action == "poll") OR (action == "sleep" AND sleep_seconds ≈ IN_PLAY_interval)
END FOR
```

Implemented as a hypothesis property test generating target sets/timings where at least one OPEN target is in-play OR within the 20-min lead window (`start_time <= now() + LEAD_WINDOW_SECONDS`, including pre-match targets with a coarse stored `update_frequency`), asserting `decide_next_action` never returns `"exit"` (for `elapsed < 6h`) and that non-due active-or-imminent states yield `"sleep"` with `sleep_seconds` bounded near the IN_PLAY interval (~5s) rather than the coarse `nearest_update_seconds`.

### Preservation Checking

**Goal**: For all inputs where the bug condition does NOT hold, the fixed function behaves identically to the original — specifically the idle cheap-exit and tier selection.

**Pseudocode:**
```
FOR ALL state WHERE NOT isBugCondition(state) DO   # no OPEN target in-play or within lead window
  action, _ := decide_next_action(state, elapsed)
  ASSERT action == "exit"                       # idle cheap-exit unchanged
  ASSERT select_tier(tiers, tte) == original_select_tier(tiers, tte)  # tier selection unchanged
END FOR
```

**Testing Approach**: Property-based testing is preferred for preservation because it generates many states across the input domain and catches edge cases manual tests miss, giving strong confidence that non-buggy behaviour is unchanged.

**Test Plan**: Observe idle/pre-match behaviour on UNFIXED code first (idle run exits cheaply; coarse tiers applied), then encode those observations as properties over `decide_next_action` and reuse the existing `select_tier` monotonicity/totality properties in `tests/test_property_monitor_config.py`.

**Test Cases**:
1. **Idle cheap-exit preserved**: no OPEN target in-play or within the 20-min lead window (all `start_time > now() + LEAD_WINDOW_SECONDS`) + far-out nearest update -> `decide_next_action` returns `"exit"` (matches today's behaviour for the genuinely-idle case).
2. **Coarse tier selection preserved**: pre-match times map to `LESS_THAN_3H/6H/12H/MORE_THAN_12H` intervals unchanged (reuse `select_tier` properties).
3. **Run start/end/failure logging preserved**: extend `tests/unit_tests_monitor_resilience.py` to assert the start marker, success marker, and `"Ending run with failure"` marker are still written under the new loop, including that failure-logging never masks the original exception.

### Unit Tests

- `decide_next_action` boundary cases: exactly-due (`nearest_update_seconds == 0`), just-under 6h vs at/over 6h cap, in-play-with-nothing-due, no-targets.
- **Lead-window boundary**: a single OPEN target at exactly `start_time == now() + LEAD_WINDOW_SECONDS` (the inclusive edge) — `has_active_or_imminent` is true and `decide_next_action` returns `"sleep"` with `sleep_seconds ≈ IN_PLAY interval` (not `"exit"`); a target one second beyond the window (`start_time == now() + LEAD_WINDOW_SECONDS + 1`) with a far-out nearest update yields `"exit"`.
- `run()` with mocked `DBOutputConnection` and `BFDriver`: verifies poll -> `reload_from_db=True`, sleep -> no reload, and exit on idle (extends `tests/unit_tests_monitor_resilience.py`).
- Advisory-lock helper method signatures behave (returns bool; uses session-scoped SQL).

### Property-Based Tests

- Fix-Checking property: generate active-or-imminent states (an OPEN target in-play OR within the 20-min lead window, including pre-match targets with a coarse stored `update_frequency`) -> `decide_next_action` never returns `"exit"` (elapsed < 6h) and sleeps near IN_PLAY (~5s), not near the coarse `nearest_update_seconds`, when nothing is due.
- Preservation property: generate genuinely-idle states (no OPEN target in-play or within the lead window) -> `decide_next_action` returns `"exit"`; reuse `select_tier` totality/monotonicity properties (already in `tests/test_property_monitor_config.py`) to prove tier selection unchanged.
- 6-hour cap property: for any state, once `elapsed >= HARD_CAP_SECONDS` the action is `"exit"`.

### Integration Tests

- **Single-instance lock (DB-bound)**: using the repo's DB test conventions (see `tests/integration_test_verify_db.py`, `tests/unit_tests_db.py`), open two `DBOutputConnection` sessions; assert the first `try_acquire_run_lock()` returns `True`, a second concurrent attempt returns `False`, and after `release_run_lock()` (or closing the first session) the lock can be re-acquired — proving the guard is self-releasing and not poison-prone.
- Full-flow in-play smoke: with mocked Betfair responses simulating an OPEN in-play market for a bounded window, assert multiple odds writes occur at ~5s spacing and the run exits when the market CLOSES.
- Idle full-flow: no in-play or lead-window target -> run exits cheaply after a coarse poll, matching today's one-off behaviour.
- Lead-window full-flow: an OPEN target with `start_time` ~10 min in the future -> run stays alive and writes odds at ~5s spacing through kickoff (proving the pre-kickoff gap is closed), while `select_tier`/stored `update_frequency` for that target remain the coarse tier.

## Deployment & Verification (Definition of Done)

This code runs on the Raspberry Pi as the Betfair capture stack, so it is deployable work: merging is not sufficient. Per the deployment DoD, while the fix is complete but not yet deployed/verified the SP-343 ticket sits in **Mostly Done**, not Done.

- Deploy via `scripts/deploy.sh` on the Pi (syncs code, validates `.env`, rebuilds and recreates the container with `docker compose up -d --build`, runs post-deploy verification). See the `pi-access` steering for how to reach the Pi.
- **In-play cadence verification is required before Done**: against a live in-play match, confirm `bf.market_table` shows in-play rows at ~5s spacing (not ~900s), including that rows begin at ~5s spacing from within the 20-min pre-kickoff lead window (i.e. the opening minutes are captured, not lost to a post-kickoff Rundeck trigger), and confirm only one run holds the advisory lock at a time (a second Rundeck-triggered `run --rm` logs the "Another run active, exiting as no-op" marker and exits).
- The completion comment on SP-343 must note that the deploy was done and the ~5s in-play cadence (including pre-kickoff lead window) + single-instance no-op were verified on the Pi.
