# Research_Dataset Schema

> The documented **Dataset_Schema** for the Betfair Research_Dataset (SP-368).
> This is the published schema artifact required by Requirement 5.5, and it
> fixes exactly the 12 columns of Requirement 5.6. It mirrors the design
> document's "`Dataset_Record` and the Research_Dataset" table and the
> `DATASET_SCHEMA` tuple in `logic/research_dataset.py`, which is the source of
> truth for column names, dtypes, and null conditions. If any of those change,
> update all three together.

## What the Research_Dataset is

The **Research_Dataset** is the tidy, analysable output of the dataset build:
a pandas DataFrame of **Dataset_Records**, persistable to Parquet. One row is
one captured observation of a single runner in a single match at a single
capture timestamp — i.e. one row per `(market_id, runner_id, timestamp)`.

It is produced by `scripts/build_research_dataset.py` (the **Dataset_Reader**,
the thin I/O wrapper) from the captured `bf.market_table` rows joined to their
`bf.target` metadata, with all shaping done by the pure, I/O-free
`logic/research_dataset.py` **Odds_Transform** (`build_records` →
`records_to_dataframe`). The reader reads rows with bound query parameters,
delegates shaping to the transform, and persists the result as **Parquet**.

Because the transform is pure, the schema contract lives in one place —
the `DATASET_SCHEMA` tuple — and both the populated path (`records_to_dataframe`)
and the zero-record path (`empty_dataset`) build their frames from it. An empty
run therefore still carries all 12 typed columns and no extras (Req 5.2, 5.6).

## Columns

The Dataset_Schema is exactly these 12 columns, in this declared order, with no
additional columns (Req 5.6). Timestamp columns are UTC-aware; the three
price/volume columns use the pandas nullable `Float64`; every id/name/role/phase
column uses the pandas nullable `string` dtype.

| # | Column | pandas dtype | Parquet type | Meaning | Null when |
|---|---|---|---|---|---|
| 1 | `market_id` | `string` | string | The match's Betfair market id (grouping key) | never |
| 2 | `runner_id` | `string` | string | The runner/selection id (grouping key) | never |
| 3 | `runner_name` | `string` | string | Runner name from the Target `runner_ids` | `runner_id` absent from the Target (Req 3.8) |
| 4 | `outcome_role` | `string` (values `HOME`/`DRAW`/`AWAY`) | string | Home/Draw/Away role of the runner | role undetermined (Req 2.7, 3.8) |
| 5 | `timestamp` | `datetime64[ns, UTC]` | timestamp(us, UTC) | Capture_Timestamp of the observation | never |
| 6 | `best_back` | `Float64` | double | Best (highest) available-to-back price | back ladder empty (Req 1.6) |
| 7 | `best_lay` | `Float64` | double | Best (lowest) available-to-lay price | lay ladder empty (Req 1.6) |
| 8 | `matched_volume` | `Float64` | double | Matched-volume signal for the observation | no volume figure present (Req 1.2) |
| 9 | `match_phase` | `string` (values `PRE_MATCH`/`IN_PLAY`) | string | Pre-match vs in-play marker | match `start_time` unknown (Req 8.3) |
| 10 | `event_id` | `string` | string | The Target `event_id` | absent on the Target (Req 3.3) |
| 11 | `start_time` | `datetime64[ns, UTC]` | timestamp(us, UTC) | Match kick-off time | absent on the Target (Req 3.3) |
| 12 | `target_id` | `string` | string | The Target identity | absent on the Target (Req 3.3) |

## Column notes

- **`match_phase`** is derived solely from the two timestamps, using no match
  results or settlement data (Req 8.4). It is `PRE_MATCH` when the capture
  timestamp is strictly earlier than `start_time` (Req 8.1), `IN_PLAY` when it
  is equal to or later (Req 8.2), and `null` when `start_time` (or the capture
  timestamp) is unknown (Req 8.3).

- **`outcome_role`** is assigned per match from the Target `runner_ids`. The
  `DRAW` runner is the one whose trimmed name case-sensitively equals the
  configured draw-selection name (the literal `"The Draw"`); `HOME` and `AWAY`
  are then the first and second non-draw runners in `runner_ids` order. The
  role is `null` for every runner whenever the match cannot be fully resolved —
  i.e. it does not have exactly three runners with exactly one draw (Req 2.7),
  or the `runner_id` is absent from the Target (Req 3.8).

- **`matched_volume`** comes from the captured odds blob — not a separate
  settlement feed — using a single source decision: a scalar `totalMatched`
  figure when present, else the summed `size` values of a `tradedVolume`
  ladder when present, else `null` (Req 1.2). A figure that is not a usable
  number is treated as absent.

- **Depth beyond best price** (the non-best back/lay ladder entries, Req 3.4)
  is parsed and retained *within* the transform's `StructuredOdds` and is
  available to callers (e.g. a future depth-aware analysis), but it is **not**
  part of this tabular Dataset_Schema. The 12-column schema carries only the
  best back and best lay prices (`best_back`, `best_lay`); Req 5.6 fixes the
  column set.

## Loading the dataset

The Research_Dataset is persisted as Parquet, so downstream analysis
(SP-369 / SP-370 / SP-371) loads it with pandas:

```python
import pandas as pd

df = pd.read_parquet("research_dataset.parquet")
```

Reloading a persisted dataset yields a DataFrame equal to the produced
Research_Dataset in columns, types, and row count (Req 5.3). A build that
matched zero rows still writes the full 12-column typed schema with no rows, so
downstream code can rely on the columns and dtypes above being present
regardless of row count.
