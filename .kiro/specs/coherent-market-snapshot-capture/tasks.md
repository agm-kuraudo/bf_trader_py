# Implementation Plan: Coherent Market Snapshot Capture (SP-396 + SP-384)

## Overview

This plan implements the design's single-file production change: rewriting
`MonitorService.update_runner_odds` in `monitor_service.py` from a per-runner
`listRunnerBook` model to a per-market `listMarketBook` snapshot model, while
persisting matched volume in each runner's Odds_Blob.

The work is test-driven. The pure logic is factored out of `update_runner_odds`
into two small pure helpers so they can be property-tested in isolation:

- a **blob builder** — builds the Odds_Blob dict/string for one runner from its
  `ex` dict plus the runner-level `totalMatched` scalar (the matched-volume
  mapping / case table), and
- a **row assembler** — assembles the N `(capture_ts, market_id, runner_id,
  odds_str)` rows for a market from a parsed `listMarketBook` result and one
  shared timestamp.

`update_runner_odds` is then rewired to use these helpers: one `listMarketBook`
call per market, one `datetime.now(UTC)` per market bound as a parameter, a
single atomic multi-row INSERT, per-market error isolation, preserving the
`bf.target` tier-update tail.

No other production file changes: `simpleStategy.py`, `api/request_body.py`,
`output/dboutput.py`, `logic/research_dataset.py`, `logic/quality_checks.py`,
and `scripts/build_research_dataset.py` are untouched. Tests use `hypothesis`
and reuse the `valid_odds_values` / ladder generators from
`tests/test_property_research_dataset.py` and
`tests/test_property_quality_checks.py`.

Run everything through the venv Python per the environment/testing steering:
`d:\projects\bf_trader_py\.venv\Scripts\python.exe`. Do not run the suite
mid-task — the single verification gate (ruff then full pytest) is the final
local task; Pi deploy + verification is the Definition-of-Done close-out.

## Tasks

- [x] 1. Extract the pure blob-builder helper (matched-volume mapping)
  - [x] 1.1 Implement the per-runner Odds_Blob builder helper in `monitor_service.py`
    - Add a module-level (or static) pure function that takes a runner's `ex`
      dict and the runner-level `totalMatched` scalar (which may be absent) and
      returns the final Odds_Blob dict, starting from the full `ex` (preserving
      `availableToBack`, `availableToLay`, and any `tradedVolume` from
      `EX_TRADED`).
    - Set `totalMatched` only when a source scalar is present (using an explicit
      presence guard so a genuine `0` is set and a missing figure is left
      unset); never write an empty `tradedVolume` list or a null `totalMatched`.
    - Implement the full case table: scalar>0 → set scalar; scalar>0 + ladder →
      set scalar, keep ladder; ladder only → keep ladder, no scalar; scalar==0 →
      set `0`; scalar==0 + ladder → set `0`, keep ladder; neither → omit both
      keys (ladders only).
    - Provide a sibling helper (or return value) that renders the blob with
      `str(dict)` to match the stored `str(dict)` contract `parse_odds` reads.
    - Pure function only — no API or DB calls.
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 1.2 Write property test for the blob builder (matched-volume round-trip)
    - **Property 1: Matched-volume round-trip through the unchanged loader**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 3.2, 3.3**
    - In a new `tests/test_property_coherent_snapshot.py`, reuse the
      `valid_odds_values` / ladder composites from
      `tests/test_property_research_dataset.py` and
      `tests/test_property_quality_checks.py`, extended with an optional
      `totalMatched` scalar (including zero) and an optional `tradedVolume`
      ladder.
    - Build the blob via the helper; assert
      `logic.research_dataset.structured_odds(blob).matched_volume` equals the
      expected figure (scalar preferred incl. zero, else summed ladder sizes,
      else `None`) and that `best_back` (max back price) / `best_lay` (min lay
      price) match the source ladders.
    - `@settings(max_examples=...)` ≥ 100; tag with
      `# Feature: coherent-market-snapshot-capture, Property 1: ...`.

  - [ ]* 1.3 Write unit tests for the blob builder case table
    - Table-driven examples for all six rows of the mapping table, including the
      scalar==0 case (persisted, not omitted) and the neither-key case (both
      keys omitted, ladders only).
    - Assert the rendered string reconstructs an equal dict via
      `ast.literal_eval` / `parse_odds`.
    - _Requirements: 2.5, 2.6_

- [x] 2. Extract the pure row-assembler helper (coherent snapshot)
  - [x] 2.1 Implement the per-market row-assembler helper in `monitor_service.py`
    - Add a pure function that takes a parsed `listMarketBook` result for one
      market, the `market_id`, and one `capture_ts`, and returns the ordered
      list of `(capture_ts, market_id, runner_id, odds_str)` tuples — exactly
      one row per returned runner, using `runner["selectionId"]` as `runner_id`
      and the blob builder from task 1 for `odds_str`.
    - Every row binds the same `capture_ts` object; no runner omitted, no
      duplicate. Handle N == 1 and a runner with no/empty `ex` (still produces a
      row with empty-ladder blob).
    - Pure function only — no API or DB calls; the caller computes `capture_ts`.
    - _Requirements: 1.2, 1.3, 1.4_

  - [x] 2.2 Write property test for the row assembler (coherent snapshot)
    - **Property 2: Coherent snapshot — N rows, one per runner, one shared timestamp**
    - **Validates: Requirements 1.2, 1.3, 1.4**
    - Generate N ≥ 1 runners with distinct `selectionId`s and arbitrary `ex`
      content; assemble rows for one `capture_ts`; assert the row `runner_id`
      multiset equals the returned `selectionId` set (one per runner, no
      omission, no duplicate) and every row's timestamp equals that one value.
    - `@settings(max_examples=...)` ≥ 100; tag with the Property 2 comment.

- [x] 3. Checkpoint - pure helpers complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Rewire `update_runner_odds` to the per-market snapshot model
  - [x] 4.1 Replace the per-runner loop with one `listMarketBook` call per market
    - For each `target` in `open_targets` (one target == one market), issue a
      single `listMarketBook` call using the existing template and the
      `process_targets` call pattern (`{"<ListOfMarketIDs>": [target[0]]}`),
      reading `result = resp.json().get("result")`.
    - Remove the inner `for individual_runner in target[3]` loop and all
      `listRunnerBook` calls.
    - Compute one shared `capture_ts = datetime.now(UTC)` per market (datetime /
      UTC already imported) and pass it into the row-assembler helper.
    - _Requirements: 1.1, 1.2, 1.4_

  - [x] 4.2 Build the single atomic multi-row INSERT and write the snapshot
    - Assemble the N rows via the task-2 helper, generate N `(%s,%s,%s,%s)`
      value groups matching the runner count, flatten the params, and call the
      unchanged `DBOutputConnection.db_write` once per market with a single
      multi-VALUES INSERT into `bf.market_table("timestamp", market_id,
      runner_id, odds)`.
    - Replace the per-INSERT SQL literal `current_timestamp` with the bound
      `capture_ts` parameter so all N rows share one identical timestamp in one
      atomic autocommitted statement.
    - _Requirements: 1.2, 1.3, 1.4, 1.7_

  - [x] 4.3 Preserve the `bf.target` tier-update tail
    - After a successful snapshot write, run the unchanged
      `DefaultStrategy.UPDATE_FREQUENCY_TIERS` selection against `target[6]`
      (event start time) and `datetime.now(UTC)`, then execute the unchanged
      `UPDATE bf.target SET last_updated=NOW(), update_frequency=%s WHERE
      market_id=%s;`.
    - Skip the tier UPDATE when the snapshot write did not succeed (db_write
      returned `False`).
    - _Requirements: 1.1_

  - [x] 4.4 Add per-market error isolation and retain the outer backstop
    - Wrap each market's call + build + write in a per-market `try/except`:
      on an empty/missing `result` (falsy `result`, or empty/absent
      `result[0]["runners"]`), log a warning identifying the market, write no
      row, and `continue`; on an exception, log an entry identifying the market
      and the error, write no row, and `continue`; on `db_write` returning
      `False`, log identifying the market, skip the tier UPDATE, and `continue`.
    - Do not mark the target EXPIRED (status transitions stay in
      `process_targets`).
    - Retain the outer method-level `try/except` as a backstop that logs to
      `bf.log_file` and raises `MonitorServiceException` for failures outside the
      per-market scope.
    - _Requirements: 1.5, 1.6, 1.7_

  - [x] 4.5 Write property test for per-market write atomicity
    - **Property 3: Per-market write atomicity**
    - **Validates: Requirements 1.7**
    - Generate N runners; with an injected failing `db_write` assert zero
      persisted rows for the poll; with a succeeding write assert exactly N rows
      all sharing one `capture_ts`, never an incomplete subset; assert the
      writer issues a single INSERT statement for the market.
    - `@settings(max_examples=...)` ≥ 100; tag with the Property 3 comment.

  - [ ]* 4.6 Write unit/example tests for empty, error, and skip branches
    - Empty/missing result (Req 1.5): `result` is `None`; `result` is `[]`;
      `result[0]["runners"]` empty/absent → zero rows written, loop continues.
    - Call raises (Req 1.6): inject an exception for one market → logged with the
      market id, zero rows for it, next market still polled.
    - Align with existing `tests/unit_tests_monitor_service.py` /
      `tests/unit_tests_monitor_resilience.py` /
      `tests/unit_tests_monitor_empty_runnerbook.py` patterns.
    - _Requirements: 1.5, 1.6_

  - [ ]* 4.7 Write integration (mock-based) wiring test
    - Assert the writer issues exactly one `listMarketBook` `populate_template`
      per market and makes **no** `listRunnerBook` call, and that the tier
      `UPDATE bf.target` still runs after a successful write.
    - _Requirements: 1.1_

- [x] 5. Checkpoint - writer rewired
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Guard the no-loader-change contract
  - [x] 6.1 Write property test: emitted blobs accepted by the unchanged loader
    - **Property 4: Emitted blobs are accepted by the unchanged loader**
    - **Validates: Requirements 2.8, 3.4**
    - For any blob the builder renders (any valid ladders with any matched-volume
      key combination from the mapping table), assert
      `logic.research_dataset.structured_odds(blob)` is non-`None` and that the
      SP-368 serialize round-trip (`serialize_structured(structured_odds(blob))`)
      reproduces an equal value — importing and exercising the **unchanged**
      `logic.research_dataset` / `logic.quality_checks` against writer-emitted
      blobs.
    - `@settings(max_examples=...)` ≥ 100; tag with the Property 4 comment.

  - [ ]* 6.2 Write loader-coherence example test
    - Feed the unchanged `build_records` three rows at one shared timestamp
      (HOME / DRAW / AWAY) → exactly one observation per `outcome_role` at that
      exact timestamp, none carried forward from an earlier runner.
    - _Requirements: 3.1, 3.5_

- [x] 7. Final verification gate (local)
  - Run, once, through the venv Python per the testing steering, as the single
    end-of-task gate — ruff check, then ruff format --check (fixing anything
    flagged), then the full pytest suite:
    - `& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff check .`
    - `& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m ruff format --check .`
    - `& "d:\projects\bf_trader_py\.venv\Scripts\python.exe" -m pytest`
  - Confirm lint/format clean and all tests green before any push/PR or marking
    the Jira task Done. Do not run the suite before this gate.
  - _Requirements: 2.8, 3.4_

- [~] 8. Deploy to the Pi and verify (Definition of Done close-out)
  - Runs on the deployment target, not locally — reach the Pi over SSH per the
    `pi-access` steering and use `scripts/deploy.sh` (do not hand-run docker).
  - Deploy (Req 4.1, 4.2): run `scripts/deploy.sh` on the Pi; confirm the
    recreated capture container is running; on a step failure confirm the script
    reports the failed step and leaves the previous container unchanged.
  - Capture-cycle spot check (Req 4.3, 4.4): within ~10 minutes, query
    `bf.market_table` and confirm a market's recent runner rows share one
    identical `timestamp` and at least one blob carries `totalMatched` or
    `tradedVolume`.
  - Research dataset (Req 4.5, 4.6): run the unchanged
    `scripts/build_research_dataset.py` against a Pi-captured market and confirm
    at least one instant where all three MATCH_ODDS outcomes (HOME/DRAW/AWAY)
    are present at one shared `timestamp`, and a non-null `matched_volume` for at
    least one runner observation.
  - No regression (Req 4.7): spot-check a sample of pre-deploy rows before/after
    to confirm stored Odds_Blob and timestamp are unchanged (INSERT-only, no
    backfill).
  - Keep the ticket in Mostly Done until deploy + verification are done; add the
    completion comment noting the deploy and the verification evidence.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

## Notes

- The four property tests (1.2, 2.2, 4.5, 6.1) are required — they are the
  executable evidence for the design's correctness properties — and task 8 (Pi
  deploy + verification) is required as the Definition of Done for this
  deployable work. The remaining `*`-marked example/unit tests (1.3, 4.6, 4.7,
  6.2) are optional and may be skipped for a faster MVP; core implementation
  tasks are never optional.
- Each task references specific requirement clauses for traceability.
- Checkpoints ensure incremental validation after the pure helpers and after the
  writer rewire.
- Property tests validate the four universal correctness properties from the
  design; unit/example tests cover the empty/error/skip branches and the
  no-loader-change guard.
- The only production file that changes is `monitor_service.py`;
  `simpleStategy.py`, `api/request_body.py`, `output/dboutput.py`,
  `logic/research_dataset.py`, `logic/quality_checks.py`, and
  `scripts/build_research_dataset.py` are not modified.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "4.1"] },
    { "id": 3, "tasks": ["4.2"] },
    { "id": 4, "tasks": ["4.3", "4.4"] },
    { "id": 5, "tasks": ["4.5", "4.6", "4.7", "6.1", "6.2"] },
    { "id": 6, "tasks": ["8"] }
  ]
}
```
