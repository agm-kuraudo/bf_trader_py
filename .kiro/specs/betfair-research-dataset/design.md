# Design Document

## Overview

This feature (Jira **SP-368**, Betfair Trader 0.7 "analyse the captured data")
delivers a documented, tested, reusable **Dataset_Loader** that turns the raw
odds in `bf.market_table` into a tidy multi-match **Research_Dataset** — one row
per `(match x runner x timestamp)` observation with structured odds, matched
volume, runner identity, Home/Draw/Away role, pre-match/in-play phase, and
market/event metadata.

Today the only analysis path is `analyse_service.py` (`MarketAnalyser`). It is
brittle in exactly the ways this feature removes:

- it reads a single hardcoded `market_id`;
- it parses the stored odds blob ad hoc with `ast.literal_eval` inline;
- it reads only **one depth level** — and worse, the *wrong* one:
  `odds_dict["availableToBack"][1]["price"]` takes the **second** ladder entry
  (index `1`), not the best price (index `0`). A market with a single-entry
  ladder would raise `IndexError`;
- it plots one market and nothing is reusable by downstream 0.7 work
  (exploratory analysis SP-369, green-out scan SP-370, pre-match drift SP-371).

The design follows the established **SP-328 / SP-332 house pattern** already in
this repo: **pure, I/O-free, property-testable logic in `logic/`**, with a
**thin database-reading wrapper in `scripts/`**. Crucially, the odds parse path
already exists — `logic/quality_checks.py` has `parse_odds` / `serialize_odds` /
`back_prices` / `lay_prices`, written for SP-332 and already property-tested for
the round-trip. This design **reuses that single parse path** rather than adding
a second one, which is precisely what Requirement 6.4 demands ("exactly one
odds-parsing code path").

Scope is **odds-only**: no match results/settlement data is read. The
`Match_Phase` marker (pre-match vs in-play) is derived purely from the capture
timestamp relative to the Match `start_time`, so it stays within odds-only
scope. The loader shapes what is already captured; it does **not** change the
capture pipeline, the database schema, or the stored odds format.

### Key design decisions

- **Reuse the existing odds parser (Req 6.4).** The Odds_Transform's parse /
  serialize core is the existing `logic/quality_checks.py` functions. To keep
  the research-dataset logic cohesive and avoid a sprawling module, the new
  per-side extraction (best-back / best-lay / depth), the validation of
  Req 1.8 (non-numeric / negative / out-of-range prices), and the record shaping
  live in a new `logic/research_dataset.py`, which **imports and calls**
  `parse_odds` / `serialize_odds` rather than reimplementing them. The repo
  therefore keeps exactly one `ast.literal_eval` read of the `odds` column.

- **`tradedVolume` vs `totalMatched` — Matched_Volume source (design decision).**
  Requirement 5 / the glossary name the volume signal `totalMatched`, but the
  stored blob and the existing SP-332 generators carry an optional
  **`tradedVolume`** list of `{price, size}` entries, not a scalar
  `totalMatched`. The captured `odds` string is the only source available in
  odds-only scope. **Decision:** Matched_Volume is derived from the captured
  blob's volume key. The transform reads `totalMatched` when the blob carries it
  (scalar), otherwise falls back to summing the `size` values of a
  `tradedVolume` ladder when present, otherwise null. The output column is named
  `matched_volume` per the Dataset_Schema (Req 5.6). This is noted here so the
  discrepancy between the ticket wording and the real captured shape is an
  explicit, reviewable decision rather than a silent assumption.

- **Draw detection by configured selection name (Req 2.5) — assumption + risk.**
  Outcome_Role derivation keys off a configured draw-selection name (observed as
  the literal `"The Draw"` in captured data, e.g. `58805-The Draw`). Home/Away
  are then assigned by `runner_ids` left-to-right order among the two non-draw
  runners. **Risk:** this assumes (a) the draw selection is always named exactly
  the configured string after trimming, and (b) `runner_ids` order encodes
  home-then-away. Both are reasonable for MATCH_ODDS but not guaranteed; the
  design makes the failure *safe* — any match that is not exactly three runners
  with exactly one draw yields a null Outcome_Role for the undetermined runners
  and records the match as undetermined (Req 2.7), never raising.

- **Pure core, thin wrapper, parameterised reads.** All shaping is pure
  (`logic/research_dataset.py`, no DB/filesystem/network). The wrapper
  (`scripts/build_research_dataset.py`) does the I/O with the same
  `.env` + psycopg2 approach as `verify_db.py` / `check_quality.py`, using bound
  query parameters for all filter values (Req 4.7).

## Architecture

The Dataset_Loader is two cooperating layers plus a documented schema, mirroring
the SP-328 / SP-332 split exactly:

| Layer | File | Responsibility | I/O? |
|---|---|---|---|
| **Odds_Transform** (pure logic) | `logic/research_dataset.py` | Parse/shape raw rows into Dataset_Records; derive best-back/best-lay/depth, Runner_Identity, Outcome_Role, Match_Phase; count exclusions. Delegates the raw `ast.literal_eval` parse/serialize to `logic/quality_checks.py`. | **None** |
| **Dataset_Reader** (thin wrapper) | `scripts/build_research_dataset.py` | Read `bf.market_table` + `bf.target` rows via `.env`/psycopg2, delegate all shaping to the Odds_Transform, assemble the pandas DataFrame, persist Parquet, print the run summary, set exit code. | DB, filesystem |
| **Dataset_Schema** (documentation) | `docs/research_dataset_schema.md` | The documented 12-column contract (name, type, meaning) every Dataset_Record conforms to. | n/a |

```mermaid
flowchart TD
    subgraph Store["Data_Store (Postgres schema bf)"]
        MT[(bf.market_table\ntimestamp, market_id,\nrunner_id, odds)]
        TG[(bf.target\ntarget_id, event_id, market_id,\nrunner_ids, start_time, ...)]
    end

    subgraph Reader["Dataset_Reader (scripts/build_research_dataset.py) — I/O"]
        R1[read market rows\nbound params] --> R2[read target rows\nbound params]
        R2 --> R3[group rows by market_id]
    end

    subgraph Transform["Odds_Transform (logic/research_dataset.py) — pure"]
        T1[parse_odds / serialize_odds\n(logic/quality_checks.py)]
        T2[best_back / best_lay / depth\n+ Req 1.8 validation]
        T3[parse_runner_ids]
        T4[derive Outcome_Role\nHOME/DRAW/AWAY]
        T5[derive Match_Phase\nPRE_MATCH/IN_PLAY]
        T6[build Dataset_Records\n+ exclusion/unresolved counts]
        T1 --> T2 --> T6
        T3 --> T4 --> T6
        T5 --> T6
    end

    subgraph Out["Output"]
        O1[pandas DataFrame\n12 schema columns] --> O2[(Parquet artifact)]
        O1 --> O3[run summary\nproduced / excluded counts]
    end

    MT --> R1
    TG --> R2
    R3 -->|raw rows as plain Python values| T6
    T6 -->|Dataset_Records| O1
    Schema[docs/research_dataset_schema.md]:::doc -.documents.- O1
    classDef doc fill:#eef,stroke:#88a;
```

### Boundary rules

- The Odds_Transform receives already-read rows as plain Python tuples/values
  and returns Dataset_Records plus a counts summary. It performs no DB,
  filesystem, or network access (Req 3.7). This is what makes it
  property-testable without a database.
- The Dataset_Reader owns every side effect: the connection, the parameterised
  queries, the DataFrame assembly, the Parquet write, and the operator-facing
  reporting.
- `MarketAnalyser` is refactored to call the Odds_Transform for its per-runner
  series, so the brittle inline `ast.literal_eval` path is deleted (Req 6).

## Components and Interfaces

### Odds_Transform — `logic/research_dataset.py` (pure)

Public functions (each carries a docstring stating purpose, parameters, and
return value — Req 7.5):

```python
# --- Odds parsing (reuses logic/quality_checks.py) ---

def structured_odds(raw: str) -> StructuredOdds | None:
    """Parse one stored Odds_Value into best-back/best-lay/depth/volume.

    Delegates the raw str(dict) decode to quality_checks.parse_odds (the single
    odds-reading path, Req 6.4). Returns None without raising when the value
    cannot be decoded into a mapping with both ladders (Req 1.7) or any ladder
    entry has a non-numeric / negative / out-of-range price or size (Req 1.8).
    """

def serialize_structured(odds: StructuredOdds) -> str:
    """Serialize a StructuredOdds back to its stored str(dict) form (Req 1.9).

    Round-trip partner of structured_odds via quality_checks.serialize_odds:
    for any valid stored Odds_Value v, parsing then serializing reproduces a
    value whose prices, sizes, order and volume equal the original (Req 1.10).
    """

# --- Runner identity & outcome role ---

def parse_runner_ids(runner_ids: str | None) -> list[RunnerIdentity]:
    """Parse a Target 'id-name|id-name' string into ordered RunnerIdentity
    pairs, splitting each entry on the FIRST hyphen only, preserving left-to-
    right order, dropping malformed entries, never raising (Req 2.1-2.4)."""

def assign_outcome_roles(
    runners: list[RunnerIdentity], draw_name: str
) -> OutcomeRoleAssignment:
    """Assign HOME/DRAW/AWAY to a match's runners (Req 2.5-2.7).

    DRAW = the runner whose trimmed name case-sensitively equals the trimmed
    configured draw_name. When exactly three runners with exactly one draw,
    HOME/AWAY go to the first/second non-draw runner in order. Otherwise every
    undetermined runner gets a null role and the match is flagged undetermined.
    """

# --- Phase ---

def match_phase(
    capture_ts: datetime | None, start_time: datetime | None
) -> str | None:
    """Return 'PRE_MATCH' when capture_ts strictly precedes start_time,
    'IN_PLAY' when at/after, else None when either is unknown (Req 8)."""

# --- Record assembly (the top-level pure entry point) ---

def build_records(
    market_rows: Iterable[MarketRow],
    targets_by_market: Mapping[str, TargetRow],
    draw_name: str = DEFAULT_DRAW_NAME,
) -> TransformResult:
    """Produce the full set of Dataset_Records across all supplied matches.

    Pure: uses only its inputs, no I/O (Req 3.7). Produces one record per
    distinct (market_id, runner_id, capture_ts) that parses (Req 3.1),
    populates every schema field with nulls where undetermined (Req 3.2, 3.3),
    excludes unparseable rows while counting them (Req 3.5), still emits a
    record with null identity for a runner_id absent from the Target while
    counting it (Req 3.8), and orders each runner's records by ascending
    capture_ts, breaking ties by ascending runner_id (Req 3.6).
    """
```

### Dataset_Reader — `scripts/build_research_dataset.py` (thin I/O)

Mirrors `scripts/verify_db.py` / `scripts/check_quality.py`: `.env` via
`DotenvLoader`, a direct `psycopg2.connect` with a `connect_timeout`, the
`sys.path.insert` project-root shim, and `output.log.Output` for operator
messages.

```python
def read_market_rows(cursor, market_ids=None, ts_start=None, ts_end=None) -> list:
    """Read (timestamp, market_id, runner_id, odds) from bf.market_table.

    All filter values are passed as BOUND query parameters, never string-
    interpolated (Req 4.7). No market_ids -> all markets (Req 4.1). A ts range
    with only one bound is treated as open on the other side (Req 4.4).
    """

def read_targets(cursor, market_ids=None) -> dict[str, TargetRow]:
    """Read Target metadata keyed by market_id from bf.target (Req 4.2),
    using bound parameters for any supplied market_id filter (Req 4.7)."""

def build_dataset(
    env_path=None, market_ids=None, ts_start=None, ts_end=None, out_path=None
) -> BuildResult:
    """Orchestrate a run: connect, read rows, delegate shaping to the
    Odds_Transform, assemble the schema DataFrame, optionally persist Parquet,
    and return a result carrying the DataFrame plus the run-summary counts.

    Reports the Data_Store unreachable and produces no artifact on connection
    failure (Req 4.6); warns and excludes a market_id with rows but no Target
    (Req 4.9); notices zero-match filters and still emits an empty schema
    DataFrame (Req 4.8, 5.2).
    """

def main() -> int:
    """CLI entry: parse args (--market-id, --from, --to, --out), run
    build_dataset, print the produced/excluded summary (Req 5.7), return a
    non-zero exit code when the store was unreachable or persistence failed."""
```

### MarketAnalyser refactor — `analyse_service.py`

`MarketAnalyser.get_market_data` + `process_data` stop calling
`ast.literal_eval` directly. Instead `MarketAnalyser` builds its single-market
odds-over-time frame by invoking the Odds_Transform (`build_records`) on the
rows it reads for its one `market_id`, then projecting each runner's `best_back`
/ `best_lay` series in ascending-timestamp order. This gives one odds-parsing
path (Req 6.1, 6.4), fixes the latent index-1 ladder bug, and yields the same
series the loader produces for that market and runner (Req 6.2). An empty result
with a logged "no captured odds" notice replaces an exception when the loader
produces no record for the requested market (Req 6.3).

## Data Models

### `MarketRow` / `TargetRow` (reader → transform inputs)

Plain tuples/typed structures mirroring the DB columns, passed from the reader
to the pure transform:

- `MarketRow`: `(timestamp: datetime, market_id: str, runner_id: str, odds: str)`
- `TargetRow`: `(target_id, event_id, market_id, runner_ids, start_time, status, last_updated, notes)`

### `StructuredOdds` (parsed Odds_Value)

```python
@dataclass(frozen=True)
class PriceLevel:
    price: float
    size: float

@dataclass(frozen=True)
class StructuredOdds:
    raw: str                      # original stored string, for exact round-trip
    best_back: float | None       # highest availableToBack price, None if empty
    best_lay: float | None        # lowest availableToLay price, None if empty
    back_depth: tuple[PriceLevel, ...]   # non-best back entries, best-to-worst
    lay_depth: tuple[PriceLevel, ...]    # non-best lay entries, best-to-worst
    matched_volume: float | None  # from totalMatched / summed tradedVolume, else None
```

- Best_Back = max back price; Best_Lay = min lay price (Req 1.3, 1.4). Betfair
  orders ladders best-first, but the transform computes max/min explicitly so a
  mis-ordered blob still yields the true best.
- Depth = all non-best entries for a side, each preserving price+size, ordered
  best-to-worst (Req 1.5). Empty ladder → best is null, depth is empty (Req 1.6).

### `RunnerIdentity` and `OutcomeRoleAssignment`

```python
@dataclass(frozen=True)
class RunnerIdentity:
    runner_id: str
    runner_name: str

@dataclass(frozen=True)
class OutcomeRoleAssignment:
    roles: dict[str, str | None]   # runner_id -> "HOME"|"DRAW"|"AWAY"|None
    undetermined: bool             # True when the match could not be fully resolved
```

### `Dataset_Record` and the Research_Dataset

One `Dataset_Record` is one row of the output. The Research_Dataset is a pandas
DataFrame of these records, persistable to Parquet. The **Dataset_Schema** is
exactly these 12 columns, in this order, with no additional columns (Req 5.6):

| # | Column | pandas dtype | Parquet type | Meaning | Null when |
|---|---|---|---|---|---|
| 1 | `market_id` | `string` | string | Match's Betfair market id | never (grouping key) |
| 2 | `runner_id` | `string` | string | Runner/selection id | never (grouping key) |
| 3 | `runner_name` | `string` | string | Runner name from Target `runner_ids` | runner_id absent from Target (Req 3.8) |
| 4 | `outcome_role` | `string` (categorical values HOME/DRAW/AWAY) | string | Home/Draw/Away role | role undetermined (Req 2.7, 3.8) |
| 5 | `timestamp` | `datetime64[ns, UTC]` | timestamp(us, UTC) | Capture_Timestamp of the observation | never |
| 6 | `best_back` | `Float64` (nullable) | double | Best (highest) available-to-back price | back ladder empty (Req 1.6) |
| 7 | `best_lay` | `Float64` (nullable) | double | Best (lowest) available-to-lay price | lay ladder empty (Req 1.6) |
| 8 | `matched_volume` | `Float64` (nullable) | double | Matched volume signal | no volume figure present (Req 1.2) |
| 9 | `match_phase` | `string` (PRE_MATCH/IN_PLAY) | string | Pre-match vs in-play marker | `start_time` unknown (Req 8.3) |
| 10 | `event_id` | `string` | string | Target `event_id` | absent on Target (Req 3.3) |
| 11 | `start_time` | `datetime64[ns, UTC]` | timestamp(us, UTC) | Match kick-off | absent on Target (Req 3.3) |
| 12 | `target_id` | `string` | string | Target identity | absent on Target (Req 3.3) |

Depth (Req 3.4) is retained *within* the transform's `StructuredOdds` and is
available to callers (e.g. a future depth-aware analysis) alongside best-back
and best-lay; the 12-column tabular schema itself carries the best prices, as
Req 5.6 fixes the column set. The Testing Strategy covers depth retention at the
transform boundary.

### Empty-dataset handling

When a run produces zero records, the Research_Dataset is an **empty DataFrame
that still carries all 12 columns with their declared dtypes** (Req 5.2). A
module-level `empty_dataset()` helper builds this typed-but-empty frame so both
the zero-record path and the schema documentation share one definition.

## Error Handling

The design draws a hard line between the two layers:

- **Odds_Transform (`logic/research_dataset.py`) — pure, never raises.** Every
  degenerate input is turned into a null field, a dropped entry, or a count.
  The transform returns data (records plus a counts summary); it never signals
  failure by raising and performs no operator-facing reporting.
- **Dataset_Reader (`scripts/build_research_dataset.py`) — owns side effects.**
  All operator-facing messages (errors, warnings, notices), the Parquet write,
  and the process exit code live here. The reader is the only layer that can
  fail an operation or set a non-zero exit code.

### Odds_Transform error/edge paths (return nulls + counts, never raise)

| Condition | Behaviour | Requirement |
|---|---|---|
| Odds_Value string cannot decode to a mapping with both ladders | `structured_odds` returns `None`; the row is excluded from records and added to the unparseable count | Req 1.7, 3.5 |
| Ladder entry has a non-numeric, negative, or out-of-range price or size | `structured_odds` returns `None` (treated as unparseable: excluded + counted) | Req 1.8, 3.5 |
| `runner_ids` entry is malformed (no hyphen, empty id, or empty name after first-hyphen split) | that entry is dropped from the Runner_Identity sequence; all other parseable entries retained | Req 2.3 |
| `runner_ids` is absent, empty, or has no parseable entry | `parse_runner_ids` returns an empty sequence, no raise | Req 2.4 |
| Match is not exactly three runners, or not exactly one `DRAW` | every undetermined runner gets a null Outcome_Role; `OutcomeRoleAssignment.undetermined = True`; no raise | Req 2.7 |
| Market_Row `runner_id` has no matching Runner_Identity in the Match | the Dataset_Record is still produced with null `runner_name` and null `outcome_role`; the unresolved-runner count is incremented | Req 3.8 |
| `start_time` (or capture timestamp) absent/unknown | `match_phase` returns `None`; the record carries a null `match_phase`; no raise | Req 8.3 |

The transform surfaces two counts in its `TransformResult`: the number of
Market_Rows excluded as unparseable (Req 3.5) and the number of records emitted
with an unresolved Runner_Identity (Req 3.8). These counts are what the reader
reports.

### Dataset_Reader error/edge paths (operator-facing, exit codes)

| Condition | Behaviour | Requirement |
|---|---|---|
| Data_Store unreachable (connection fails) | report an operator-facing error identifying the Data_Store as unreachable; produce **no** Research_Dataset artifact; `main` returns a non-zero exit code | Req 4.6 |
| A `market_id` has Market_Rows but no Target_Row in `bf.target` | exclude that `market_id` from the dataset; report an operator-facing warning naming the `market_id` as missing Target metadata | Req 4.9 |
| A supplied `market_id` filter or timestamp range matches zero rows | produce an empty but fully-typed 12-column schema DataFrame; report an operator-facing notice that no rows matched | Req 4.8, 5.2 |
| Parquet persistence to the supplied `--out` path fails | report an operator-facing error; leave any existing file at that path **unchanged**; retain the in-memory Research_Dataset; `main` returns a non-zero exit code | Req 5.4 |
| Run completes | always report the produced-record and unparseable-excluded counts, reporting zero where none apply | Req 5.7 |

All filter values (`market_id`, timestamp bounds) are passed to the Data_Store
as bound query parameters, never string-interpolated, which closes the injection
path (Req 4.7).

### MarketAnalyser error path

| Condition | Behaviour | Requirement |
|---|---|---|
| The loader (`build_records`) produces no Dataset_Record for the requested `market_id` | `MarketAnalyser` returns an empty odds-over-time result and logs a "no captured odds" notice for that market; no exception | Req 6.3 |

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all
valid executions of a system — essentially, a formal statement about what the
system should do. Properties serve as the bridge between human-readable
specifications and machine-verifiable correctness guarantees.*

The following properties are implemented as property-based tests with
**hypothesis** (minimum 100 generated inputs each) unless noted as example-based.
Where a property generates an Odds_Value, it reuses the existing **SP-332 odds
generators** from the `logic/quality_checks` test suite rather than defining a
second generator, so the research-dataset tests exercise the same ladder/volume
shapes already trusted for the quality-check round-trip.

Each property-based test carries the tag comment:
`# Feature: betfair-research-dataset, Property {n}: {property_text}`

### Property 1: Odds_Value round-trip

*For any* valid generated Odds_Value, parsing it with `structured_odds` and then
serializing the result with `serialize_structured` SHALL produce a string that
parses to a structure whose back/lay prices, sizes, entry order, and
Matched_Volume all equal those of the original. **(hypothesis — reuses SP-332
odds generators)**

**Validates: Requirements 1.9, 1.10, 7.1**

### Property 2: Best-price correctness

*For any* valid generated Odds_Value, `best_back` SHALL equal the maximum
`availableToBack` price and `best_lay` SHALL equal the minimum `availableToLay`
price regardless of the order entries appear in the ladder; and *for any*
Odds_Value with an empty ladder on a side, the corresponding best price SHALL be
null and that side's depth SHALL be an empty sequence. **(hypothesis — generates
shuffled and empty ladders)**

**Validates: Requirements 1.3, 1.4, 1.5, 1.6**

### Property 3: Runner_ids parsing

*For any* generated `runner_ids` string, `parse_runner_ids` SHALL preserve the
left-to-right order of parseable entries, split each entry on the first hyphen
only, drop every malformed entry while retaining all parseable ones, and never
raise. **(hypothesis — generates mixed well-formed/malformed entries)**

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

### Property 4: Outcome_Role assignment

*For any* set of Runner_Identity values and configured draw name, when there are
exactly three runners with exactly one whose trimmed name case-sensitively
equals the trimmed draw name, `assign_outcome_roles` SHALL assign `HOME` to the
first and `AWAY` to the second non-draw runner in order and `DRAW` to the draw
runner; otherwise every undetermined runner SHALL receive a null role and the
assignment SHALL be flagged undetermined — and the function SHALL never raise.
**(hypothesis — generates valid and degenerate runner sets)**

**Validates: Requirements 2.5, 2.6, 2.7**

### Property 5: Match_Phase classification

*For any* pair of capture timestamp and `start_time`, `match_phase` SHALL return
`PRE_MATCH` when the capture timestamp is strictly earlier than `start_time`,
`IN_PLAY` when it is equal to or later, and null when either value is unknown.
**(hypothesis — generates ordered, equal, and null timestamp pairs)**

**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

### Property 6: Record determinism and ordering

*For any* set of Market_Rows and Target metadata, `build_records` SHALL be
deterministic (identical input yields identical output), and within each Runner
the records SHALL be ordered by ascending Capture_Timestamp with ties broken by
ascending `runner_id`. **(hypothesis — generates multi-runner, multi-timestamp,
duplicate-timestamp inputs)**

**Validates: Requirements 3.1, 3.6**

### Property 7: Exclusion and unresolved accounting

*For any* set of Market_Rows, the number of Dataset_Records produced plus the
number of Market_Rows counted as unparseable SHALL equal the number of distinct
`(market_id, runner_id, Capture_Timestamp)` observations in the input, and the
unresolved-Runner_Identity count SHALL equal the number of produced records whose
`runner_id` had no matching Runner_Identity. **(hypothesis — generates mixes of
parseable/unparseable rows and resolved/unresolved runners)**

**Validates: Requirements 3.5, 3.8**

### Property 8: Schema invariance

*For any* run outcome, including the zero-record case, the produced
Research_Dataset DataFrame SHALL carry exactly the 12 Dataset_Schema columns in
the declared order and with the declared dtypes, and no additional columns.
**(example-based — the empty-dataset case and a populated case assert the exact
column set and dtypes)**

**Validates: Requirements 5.2, 5.6**

## Testing Strategy

Testing mirrors the SP-328 / SP-332 house pattern: the pure transform carries
the bulk of the coverage (hypothesis property tests plus a few targeted example
tests), and the thin reader is exercised with fakes/fixtures rather than a live
database.

**Dual approach:**

- **Property tests (hypothesis)** cover the universal invariants — Properties 1
  through 7 above — each configured for a minimum of 100 generated inputs and
  tagged with its design property number. Odds_Value generation reuses the
  existing SP-332 generators so there is one source of truth for valid ladder
  shapes.
- **Example-based unit tests** cover the specific observable outcomes Req 7.2
  names: a Runner_Identity correctly parsed from a Market_Row, the expected
  Outcome_Role assigned, a Dataset_Record produced for each parseable Market_Row,
  and each unparseable Market_Row omitted without raising. Property 8 (schema
  invariance) is also example-based, asserting the exact 12-column set and dtypes
  for both the empty and populated cases.

**Odds_Transform (`logic/research_dataset.py`) — pure, no DB.** All of the above
run with no database, filesystem, or network access (Req 3.7), placed under the
existing `tests/` directory alongside the SP-332 logic tests. A dedicated
**depth-retention test at the transform boundary** asserts that when a Market_Row
Odds_Value carries ladder entries beyond the best price, the resulting
`StructuredOdds` retains those non-best entries (price + size, best-to-worst) for
each side alongside `best_back` / `best_lay` (Req 3.4).

**Dataset_Reader (`scripts/build_research_dataset.py`) — thin I/O.** Because all
shaping is delegated to the pure transform, the reader tests stay thin:

- `read_market_rows` / `read_targets` are exercised with a **fake cursor** that
  records the SQL and the parameters it was called with. Tests assert the filter
  values arrive as **bound query parameters** (a parameter sequence), never
  interpolated into the SQL string (Req 4.7).
- `build_dataset` is driven with fake query results to assert the operator-facing
  behaviours: unreachable store → error + no artifact + non-zero exit (Req 4.6);
  rows-without-Target → excluded + warning (Req 4.9); zero-match filter → empty
  typed DataFrame + notice (Req 4.8, 5.2); produced/excluded counts reported
  (Req 5.7).
- Parquet persistence is tested with a real temp path for the success round-trip
  (reload equals produced frame in columns/dtypes/row count — Req 5.3) and with a
  forced write failure to assert the error is reported, any existing file is left
  unchanged, and the in-memory frame is retained with a non-zero exit (Req 5.4).

**MarketAnalyser equivalence.** A test builds a small set of Market_Rows for one
`market_id`, runs both `MarketAnalyser` and the loader (`build_records`) over the
same rows, and asserts that for each Runner the `best_back` and `best_lay`
series, their Capture_Timestamps, and their ascending-timestamp ordering are
equal (Req 6.2). A second test asserts the empty-result + "no captured odds"
notice path when the loader yields no record for the requested market (Req 6.3).

**Gating commands.** While iterating, run the fast suite (`pytest`, slow tests
deselected). Before marking the work done — and before any push or PR — run the
full suite `pytest -m ""` with zero failures and zero errors (Req 7.4), plus
`ruff check .` and `ruff format --check .` reporting zero issues (Req 7.3). This
feature is pure odds-shaping logic with no model training or full-pipeline runs,
so **no tests are expected to carry the `slow` marker**; if any end-to-end
fixture is added later it should be marked `slow` accordingly.

## Requirements Coverage

| Requirement | Design elements / sections that satisfy it |
|---|---|
| **1 — Parse Odds_Value into structured prices/volume** | `structured_odds` / `serialize_structured` + `StructuredOdds` data model (reusing `logic/quality_checks.parse_odds`); Error Handling (unparseable/out-of-range → null); Property 1 (round-trip), Property 2 (best-price/empty-ladder) |
| **2 — Runner_Identity and Outcome_Role** | `parse_runner_ids`, `assign_outcome_roles` + `RunnerIdentity` / `OutcomeRoleAssignment` models; Error Handling (malformed entries, undetermined roles); Property 3, Property 4 |
| **3 — Tidy per-match/runner/timestamp records** | `build_records` (pure entry point) + `Dataset_Record` / 12-column schema; Error Handling (exclusion & unresolved counts, null fields); Property 6 (determinism/ordering), Property 7 (accounting); depth-retention test (Req 3.4) |
| **4 — Read many matches from Data_Store** | Dataset_Reader `read_market_rows` / `read_targets` / `build_dataset` (bound params, filters, open-ended ranges); Error Handling (unreachable store, missing Target, zero-match); reader tests with fake cursor |
| **5 — Documented Research_Dataset artifact** | Dataset_Schema (`docs/research_dataset_schema.md`), `empty_dataset()` helper, Parquet persistence in `build_dataset`; Error Handling (persist failure, zero-match); Property 8 (schema invariance); Parquet round-trip test |
| **6 — Supersede MarketAnalyser read path** | MarketAnalyser refactor onto `build_records` (single `ast.literal_eval` path); Error Handling (no-record → empty + notice); MarketAnalyser equivalence tests (Req 6.2, 6.3) |
| **7 — Tested, documented, lint-clean delivery** | Testing Strategy (hypothesis + example tests, gating commands); public-function docstrings on every Odds_Transform / Dataset_Reader function; Property 1 (≥100 inputs); `ruff` + `pytest -m ""` gates |
| **8 — Pre-match vs in-play classification** | `match_phase` function + `match_phase` schema column; Error Handling (null `start_time` → null phase); Property 5 |
