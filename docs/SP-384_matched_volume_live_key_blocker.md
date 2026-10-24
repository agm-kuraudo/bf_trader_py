# SP-384 matched-volume: capture mechanism complete, blocked on live App Key

_Status: the capture/transform **mechanism is complete and deployed** (via the
SP-396 coherent-snapshot work). Real per-runner matched volume is **not
available** on the current Betfair data tier. This is a **commercial / data-tier
blocker, not a code defect.**_

## What SP-384 wanted

Persist a per-runner matched-volume figure into each captured odds blob so the
SP-368 research dataset surfaces a non-null `matched_volume` (see SP-384 and the
SP-368 loader `logic/research_dataset.py` `_matched_volume`).

## What was built (and is correct)

Delivered as part of the combined SP-396 feature
(`.kiro/specs/coherent-market-snapshot-capture/`):

- `monitor_service.build_odds_blob(ex, total_matched=...)` persists a
  `totalMatched` scalar (and preserves any `ex.tradedVolume` ladder) into the
  stored blob in exactly the shape the unchanged SP-368 Odds_Transform reads.
- The writer reads runner-level `totalMatched` / `ex.tradedVolume` from a single
  `listMarketBook` call (`api/request_body.py` already requests
  `EX_BEST_OFFERS, EX_ALL_OFFERS, EX_TRADED`).
- Property tests cover the full matched-volume mapping (scalar wins incl. zero,
  else summed ladder, else null) and round-trip through the real loader.

The request is well-formed and `EX_TRADED` is present (verified by dumping the
serialised JSON-RPC body on the Pi).

## Root cause of null/zero volume (MEASURED on the Pi, 2026-10-24)

The capture runs on the account's **delayed** Betfair App Key. On that tier,
per-runner traded data is suppressed:

- In-use App Key (`.env` `BF_AppKey`, suffix `...CwWm`) = the `1.0-DELAY`
  version, `delayData: True` (confirmed via `getDeveloperAppKeys`).
- On an in-play MATCH_ODDS market the delayed key returns:
  - market-level `totalMatched` ~ **639,000** (present), BUT
  - runner-level `totalMatched` = **0.0** for every runner, and
  - `ex.tradedVolume` = **[]** (empty) for every runner,
  - while `lastPriceTraded` is populated (so the selection HAS traded).
- The account's **live** key (suffix `...QSmZ`, `delayData: False`) returns
  **`INVALID_APP_KEY` (ANGX-0007)** — it is not activated.

So the data we need exists on Betfair's side but is only served to an activated
**live** App Key.

Market-level `totalMatched` is whole-market liquidity, NOT per-outcome volume,
and is deliberately **not** persisted as a substitute — it would be a
misleading `matched_volume` column (decision: 2026-10-24).

## What unblocks it

Activating the live App Key costs **£499** (one-off) plus Betfair's live data
charges. Decision (2026-10-24): **not worth it until there is a worthwhile
strategy that needs volume.** Deferred.

When a live key is activated, the fix is **config-only, no code change**:

1. Set `BF_AppKey` in the Pi `.env` to the live key (suffix `...QSmZ`).
2. Redeploy / restart the capture container.
3. Re-verify with one `listMarketBook` call that runner-level `totalMatched` /
   `ex.tradedVolume` now populate, then confirm the research dataset shows a
   non-null, non-zero `matched_volume`.

## SP-396 is unaffected

The coherent per-poll snapshot (all runners at one shared timestamp) is verified
working on the Pi on the delayed key — 3 runner rows per poll share one
timestamp, replacing the old one-outcome-per-instant forward-fill. That is the
shippable value of this feature; the volume half simply carries zeros until the
live key is in place.
