"""Extract Betfair historic stream files into a compact cache (SP-401 / SP-491).

Reads every ``.bz2`` market file under ``<root>/<TIER>`` (the layout the Betfair historic data site produces once
the tar is extracted: ``TIER/year/Mon/day/eventId/marketId.bz2``), keeps MATCH_ODDS and OVER_UNDER_25 markets, and
writes one pickle holding, per market, pre-match reference prices and the in-play last-traded-price series. All
parsing lives in the pure ``logic/historic_data.py``; this wrapper only does file I/O and parallelism.

Usage (from the repo root)::

    python scripts/historic_extract.py --root data/historic --tier BASIC --out data/historic/cache_basic.pkl

The cache is a pickle: only load caches you generated yourself. ``data/`` is gitignored.
"""

import argparse
import bz2
import json
import multiprocessing as mp
import os
import pickle
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from logic.historic_data import parse_market  # noqa: E402


def read_messages(path: Path):
    """Yield the decoded JSON messages of one compressed market file."""
    with bz2.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            yield json.loads(line)


def process_file(path: Path):
    """Parse one file. Returns a market record, ``None`` (not of interest) or ``("error", path, message)``."""
    try:
        return parse_market(read_messages(path), market_id=path.stem)
    except Exception as exc:  # a truncated or half-extracted file must not stop a long run
        return ("error", str(path), str(exc))


def extract(root: Path, tier: str, workers: int) -> tuple[list[dict], list[tuple]]:
    """Parse every file under ``root/tier`` in parallel and return ``(markets, errors)``."""
    files = list((root / tier).rglob("*.bz2"))
    print(f"{len(files)} files under {root / tier}", flush=True)
    markets, errors = [], []
    with mp.Pool(workers) as pool:
        for done, result in enumerate(pool.imap_unordered(process_file, files, chunksize=50), start=1):
            if done % 10000 == 0:
                print(f"  {done}/{len(files)} read, {len(markets)} markets kept", flush=True)
            if result is None:
                continue
            if isinstance(result, tuple):
                errors.append(result)
            else:
                markets.append(result)
    return markets, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path("data/historic"), help="folder holding the tier folders")
    parser.add_argument("--tier", default="BASIC", help="tier folder to read (BASIC, ADVANCED or PRO)")
    parser.add_argument("--out", type=Path, default=None, help="cache file to write (default <root>/cache_<tier>.pkl)")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = parser.parse_args(argv)

    out = args.out or args.root / f"cache_{args.tier.lower()}.pkl"
    markets, errors = extract(args.root, args.tier, args.workers)
    with open(out, "wb") as handle:
        pickle.dump(markets, handle, protocol=pickle.HIGHEST_PROTOCOL)
    kinds = Counter((m["kind"], m["inplay"]) for m in markets)
    print(f"wrote {out}: {len(markets)} markets {dict(kinds)}; {len(errors)} unreadable files")
    for error in errors[:5]:
        print("  ", error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
