"""Pure core for analysing Betfair historic stream files (SP-401 / SP-491).

Everything here is side-effect free: it works on already-decoded stream messages (``parse_market``) or on the
cached market records that ``parse_market`` produces. The I/O wrappers live in ``scripts/historic_extract.py``
(reading ``.bz2`` files into a cache) and ``scripts/historic_checks.py`` (running the checks).

Data caveats that apply to every result built on this module (they make results UPPER BOUNDS):

* The free Basic tier only carries last traded prices (``ltp``). They are not tradable bid/ask: there is no spread,
  no queue position and no bet delay.
* In-play ``ltp`` updates about once a minute and only when a bet matches. Minutes below are WALL-CLOCK minutes
  since in-play start (they include roughly 15 minutes of half-time), not football minutes.
* Commission is charged per market on net winnings only (losses pay nothing, and do not offset another market).

Two lessons are built in as guards, because both produced convincing but fake edges when first missed:

1. **No look-ahead in trade selection.** Never include or exclude a trade using something that is only known after
   entry (for example "has at least 15 in-play updates": a market whose Over wins early stops trading, so the count
   leaks the outcome). Use :func:`assert_selection_ignores_future` before running a grid.
2. **Stale last-traded prices.** After an outcome-deciding event (a goal that settles Over 2.5) nobody trades at
   about 1.01, so ``ltp`` stays at the pre-event price and a simulated "exit" on it is not available in reality.
   Run :func:`asof_calibration` first: if the as-of implied probability drifts away from the realised share, exit
   results priced off ``ltp`` for that runner are invalid (see :func:`calibration_warnings`).
"""

import math
from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

# Market types the checks understand, mapped to a short kind code.
KIND_BY_MARKET_TYPE = {"MATCH_ODDS": "MO", "OVER_UNDER_25": "OU25"}
DRAW = "The Draw"
OVER_25 = "Over 2.5 Goals"
UNDER_25 = "Under 2.5 Goals"
# Hours before the scheduled kick-off at which a pre-match reference price is recorded.
HORIZONS_H = (72, 24, 6, 1)
# In-play samples are kept for this long after in-play starts.
MAX_AFTER_S = 4 * 3600
DEFAULT_STAKE = 50.0


class LookAheadError(AssertionError):
    """A trade selection depended on information that is only known after entry."""


# --------------------------------------------------------------------------------------------- basic maths
def net_of_commission(gross: float, rate_pct: float) -> float:
    """Return ``gross`` less commission. Commission is charged on a positive net result only."""
    return gross - (gross * rate_pct / 100.0 if gross > 0 else 0.0)


def mean_ci(xs: Iterable[float]) -> tuple[float, float]:
    """Return (mean, 95% half-width) of ``xs``. The half-width is NaN for fewer than two values."""
    arr = np.asarray(list(xs), dtype=float)
    n = arr.size
    if n == 0:
        return float("nan"), float("nan")
    if n == 1:
        return float(arr[0]), float("nan")
    return float(arr.mean()), float(1.96 * arr.std(ddof=1) / math.sqrt(n))


def locked_profit_fraction(first_price: float, second_price: float, direction: str) -> float:
    """Profit, as a fraction of the first-leg stake, from opening then closing at equal outcome.

    ``LAY`` opens by laying at ``first_price`` and closes by backing at ``second_price`` with stake
    ``stake * first/second``; the result is the same whichever selection wins and equals ``1 - first/second``.
    ``BACK`` is the mirror image, ``first/second - 1``.
    """
    ratio = first_price / second_price
    return 1.0 - ratio if direction == "LAY" else ratio - 1.0


# --------------------------------------------------------------------------------------------- parsing
def _iso_to_epoch_ms(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000.0


def parse_market(
    messages: Iterable[dict],
    market_id: str,
    horizons_h: Sequence[int] = HORIZONS_H,
    max_after_s: float = MAX_AFTER_S,
) -> dict | None:
    """Reduce one market's stream messages to a compact cache record, or ``None`` if it is not of interest.

    The record holds, per runner (keyed by name): final ``status`` (``WINNER``/``LOSER``/...), ``sort`` priority,
    ``entry`` (last ``ltp`` before in-play started, ``None`` if none), ``prices`` (last ``ltp`` at or before
    kick-off minus each horizon, plus ``close`` = last ``ltp`` at or before the scheduled kick-off) and ``series``
    (in-play ``(seconds_since_in_play_start, ltp)`` samples).
    """
    series: dict[int, list[tuple[float, float]]] = {}
    md_first = md_last = None
    inplay_pt = None
    for msg in messages:
        pt = msg["pt"]
        for mc in msg.get("mc", []):
            md = mc.get("marketDefinition")
            if md:
                md_first = md_first or md
                md_last = md
                if inplay_pt is None and md.get("inPlay"):
                    inplay_pt = pt
            for rc in mc.get("rc", []):
                if "ltp" in rc:
                    series.setdefault(rc["id"], []).append((pt, rc["ltp"]))
    if md_first is None or md_first.get("marketType") not in KIND_BY_MARKET_TYPE:
        return None
    kickoff_ms = _iso_to_epoch_ms(md_first["marketTime"])
    runners: dict[str, dict] = {}
    for r in md_last["runners"]:
        samples = series.get(r["id"], [])
        prices = {}
        for h in horizons_h:
            ref = kickoff_ms - h * 3600 * 1000
            before = [p for pt, p in samples if pt <= ref]
            prices[str(h)] = before[-1] if before else None
        pre_ko = [p for pt, p in samples if pt <= kickoff_ms]
        prices["close"] = pre_ko[-1] if pre_ko else None
        if inplay_pt is None:
            entry, post = (pre_ko[-1] if pre_ko else None), []
        else:
            before_ip = [p for pt, p in samples if pt < inplay_pt]
            entry = before_ip[-1] if before_ip else None
            post = [
                ((pt - inplay_pt) / 1000.0, p) for pt, p in samples if inplay_pt <= pt <= inplay_pt + max_after_s * 1000
            ]
        runners[r["name"]] = {
            "status": r["status"],
            "sort": r.get("sortPriority"),
            "entry": entry,
            "prices": prices,
            "series": post,
        }
    return {
        "kind": KIND_BY_MARKET_TYPE[md_first["marketType"]],
        "market_id": market_id,
        "event": md_first.get("eventName"),
        "ko": md_first["marketTime"],
        "rate": float(md_first.get("marketBaseRate", 5.0)),
        "inplay": inplay_pt is not None,
        "runners": runners,
    }


def single_winner(market: dict) -> bool:
    """True when exactly one runner won (excludes voided/abandoned markets)."""
    return sum(r["status"] == "WINNER" for r in market["runners"].values()) == 1


# --------------------------------------------------------------------------------------------- flat-stake checks
def settle_bet(side: str, price: float, won: bool, rate_pct: float, stake: float = DEFAULT_STAKE):
    """Settle one bet held to the result. Returns ``(gross, net_after_commission, amount_at_risk)``.

    For a LAY, ``stake`` is the backer's stake you win if the selection loses; your risk is the liability.
    """
    if side == "BACK":
        gross = stake * (price - 1.0) if won else -stake
        risk = stake
    else:
        gross = -stake * (price - 1.0) if won else stake
        risk = stake * (price - 1.0)
    return gross, net_of_commission(gross, rate_pct), risk


def prices_complete(market: dict, source: str) -> bool:
    """True for a three-runner Match Odds market with a single winner and a price at ``source`` for every runner."""
    rs = list(market["runners"].values())
    if len(rs) != 3 or DRAW not in market["runners"] or not single_winner(market):
        return False
    return all(r["prices"].get(source) for r in rs)


def pick_runner(market: dict, source: str, which: str) -> tuple[str, dict]:
    """Pick ``favourite``/``outsider``/``draw``/``home``/``away`` from a Match Odds market at ``source`` prices."""
    runners = market["runners"]
    draw = runners[DRAW]
    teams = sorted(((n, r) for n, r in runners.items() if n != DRAW), key=lambda nr: nr[1]["sort"] or 0)
    ranked = sorted(runners.items(), key=lambda nr: nr[1]["prices"][source])
    choices = {
        "favourite": ranked[0],
        "outsider": ranked[-1],
        "draw": (DRAW, draw),
        "home": teams[0],
        "away": teams[1],
    }
    return choices[which]


def flat_stake_row(markets: Iterable[dict], source: str, side: str, which: str, stake: float = DEFAULT_STAKE) -> dict:
    """Aggregate a hold-to-result flat-stake strategy over the usable markets."""
    gross_total = net_total = risk_total = 0.0
    nets = []
    for m in markets:
        if not prices_complete(m, source):
            continue
        _, runner = pick_runner(m, source, which)
        gross, net, risk = settle_bet(side, runner["prices"][source], runner["status"] == "WINNER", m["rate"], stake)
        gross_total += gross
        net_total += net
        risk_total += risk
        nets.append(net)
    mean, ci = mean_ci(nets)
    return {
        "side": side,
        "which": which,
        "n": len(nets),
        "gross": gross_total,
        "net": net_total,
        "roi_on_risk": net_total / risk_total if risk_total else float("nan"),
        "net_per_bet": mean,
        "ci": ci,
    }


def lay_early_back_late(markets: Iterable[dict], horizon_h: int, stake: float = DEFAULT_STAKE) -> dict:
    """Lay the favourite at T-``horizon_h`` and back it at the close with equal outcome (the 'sentiment drift' trade).

    Favourite = lowest price at the early time. Profit is locked at ``stake * (1 - early/close)``.
    """
    key = str(horizon_h)
    gross, net, moves = [], [], []
    for m in markets:
        if not (prices_complete(m, key) and all(r["prices"].get("close") for r in m["runners"].values())):
            continue
        _, fav = pick_runner(m, key, "favourite")
        early, close = fav["prices"][key], fav["prices"]["close"]
        profit = stake * locked_profit_fraction(early, close, "LAY")
        gross.append(profit)
        net.append(net_of_commission(profit, m["rate"]))
        moves.append(close / early - 1.0)
    mean, ci = mean_ci(gross)
    return {
        "n": len(gross),
        "median_move": float(np.median(moves)) if moves else float("nan"),
        "share_drifted": float(np.mean(np.array(moves) > 0)) if moves else float("nan"),
        "mean_gross": mean,
        "ci": ci,
        "mean_net": float(np.mean(net)) if net else float("nan"),
        "total_net": float(np.sum(net)),
    }


def longshot_lay(markets: Iterable[dict], min_price: float, liability: float = DEFAULT_STAKE) -> dict:
    """Lay every non-draw Match Odds runner priced at least ``min_price`` at the close, with fixed liability."""
    pnl, wins, implied = [], 0, 0.0
    for m in markets:
        if not single_winner(m):
            continue
        for name, r in m["runners"].items():
            price = r["prices"].get("close")
            if not price or price < min_price or name == DRAW:
                continue
            stake = liability / (price - 1.0)
            gross = -liability if r["status"] == "WINNER" else stake
            pnl.append(net_of_commission(gross, m["rate"]))
            wins += r["status"] == "WINNER"
            implied += 1.0 / price
    n = len(pnl)
    mean, ci = mean_ci(pnl)
    return {
        "min_price": min_price,
        "n": n,
        "win_rate": wins / n if n else float("nan"),
        "implied": implied / n if n else float("nan"),
        "net_per_bet": mean,
        "ci": ci,
        "roi_on_liability": mean / liability if n else float("nan"),
    }


# --------------------------------------------------------------------------------------------- in-play trades
@dataclass(frozen=True)
class Trade:
    """One in-play trade opportunity on a runner, entered at the last pre-in-play price ``p0``."""

    market_id: str
    month: str
    t: np.ndarray  # seconds since in-play start, ascending
    p: np.ndarray  # last traded price at each time
    p0: float
    won: bool
    rate: float


def build_trade(market: dict, runner_name: str) -> Trade | None:
    """Build a :class:`Trade` using ONLY information known at entry (plus a settled single winner).

    Deliberately takes no filter on the number or content of in-play samples: that is future information.
    """
    if not market["inplay"]:
        return None
    runner = market["runners"].get(runner_name)
    if runner is None or runner["entry"] is None or not single_winner(market):
        return None
    series = runner["series"]
    return Trade(
        market_id=market["market_id"],
        month=market["ko"][:7],
        t=np.array([a for a, _ in series], dtype=float),
        p=np.array([b for _, b in series], dtype=float),
        p0=float(runner["entry"]),
        won=runner["status"] == "WINNER",
        rate=float(market["rate"]),
    )


def assert_selection_ignores_future(
    markets: Sequence[dict],
    builder: Callable[[dict], object | None],
    sample: int = 2000,
) -> int:
    """Raise :class:`LookAheadError` if ``builder``'s include/exclude decision depends on the in-play series.

    Each market is rebuilt with every in-play series emptied; a look-ahead-free builder selects exactly the same
    markets. Returns the number of markets checked.
    """
    checked = 0
    for market in markets[:sample]:
        stripped = dict(market)
        stripped["runners"] = {n: {**r, "series": []} for n, r in market["runners"].items()}
        if (builder(market) is None) != (builder(stripped) is None):
            raise LookAheadError(
                f"market {market['market_id']} is included or excluded depending on its in-play series; "
                "that information is not known at entry"
            )
        checked += 1
    return checked


def settle_hold(trade: Trade, direction: str, stake: float = DEFAULT_STAKE) -> float:
    """Net result of entering and holding to the result."""
    return settle_bet(direction, trade.p0, trade.won, trade.rate, stake)[1]


def _profit_path(trade: Trade, direction: str, stake: float, limit_s: float | None) -> np.ndarray:
    p = trade.p if limit_s is None else trade.p[: int(np.searchsorted(trade.t, limit_s, side="right"))]
    if direction == "LAY":
        return stake * (1.0 - trade.p0 / p)
    return stake * (trade.p0 / p - 1.0)


def run_exit(
    trade: Trade,
    direction: str,
    target: float,
    stop: float | None,
    minutes: float | None,
    stake: float = DEFAULT_STAKE,
) -> float:
    """Reference (slow, obviously-correct) simulation of one trade with a profit target, stop loss and time exit.

    Exits at the first in-play sample whose open profit reaches ``target`` or falls to ``-stop`` (at that sample's
    price, not clipped to the threshold, because samples are about a minute apart). Otherwise exits at the last
    sample within ``minutes`` wall-clock minutes, or holds to the result when ``minutes`` is ``None``.
    """
    pr = _profit_path(trade, direction, stake, None if minutes is None else minutes * 60.0)
    if pr.size == 0:
        return settle_hold(trade, direction, stake)
    hit = pr >= target
    if stop is not None:
        hit = hit | (pr <= -stop)
    idx = np.flatnonzero(hit)
    if idx.size:
        return net_of_commission(float(pr[idx[0]]), trade.rate)
    if minutes is not None:
        return net_of_commission(float(pr[-1]), trade.rate)
    return settle_hold(trade, direction, stake)


def evaluate_grid(
    trades: Sequence[Trade],
    direction: str,
    targets: Sequence[float],
    stops: Sequence[float | None],
    minutes: Sequence[float | None],
    stake: float = DEFAULT_STAKE,
) -> tuple[list[tuple], np.ndarray]:
    """Evaluate every (target, stop, minutes) cell on every trade; equivalent to :func:`run_exit`, but fast.

    Uses running maxima/minima of the open-profit path so each cell needs two binary searches instead of a scan.
    Returns ``(keys, values)`` where ``values[i, j]`` is the net result of trade ``j`` under ``keys[i]``.
    """
    keys = [(tg, st, mi) for mi in minutes for tg in targets for st in stops]
    column = {key: i for i, key in enumerate(keys)}
    values = np.empty((len(keys), len(trades)))
    for j, trade in enumerate(trades):
        hold = settle_hold(trade, direction, stake)
        for mi in minutes:
            pr = _profit_path(trade, direction, stake, None if mi is None else mi * 60.0)
            n = pr.size
            if n == 0:
                for tg in targets:
                    for st in stops:
                        values[column[(tg, st, mi)], j] = hold
                continue
            run_max = np.maximum.accumulate(pr)
            neg_run_min = -np.minimum.accumulate(pr)
            fallback = hold if mi is None else net_of_commission(float(pr[-1]), trade.rate)
            stop_idx = {st: (n if st is None else int(np.searchsorted(neg_run_min, st, side="left"))) for st in stops}
            for tg in targets:
                target_idx = int(np.searchsorted(run_max, tg, side="left"))
                for st in stops:
                    i = min(target_idx, stop_idx[st])
                    values[column[(tg, st, mi)], j] = net_of_commission(float(pr[i]), trade.rate) if i < n else fallback
    return keys, values


# --------------------------------------------------------------------------------------------- calibration guard
def asof_calibration(
    markets: Iterable[dict],
    kind: str,
    runner_name: str,
    times_min: Sequence[float] = (0, 15, 30, 45, 60, 75, 90, 100),
) -> list[dict]:
    """Compare the as-of implied probability (1 / last traded price) with the realised win share, by time.

    Uses every market of ``kind`` with an entry price and a single winner; no filter on the future. Under fair,
    fresh prices the two stay level at every time. A widening gap means the last traded price has gone stale or
    off-market (the Over 2.5 failure mode), so exits priced off it are not tradable.
    """
    prepared = []
    for m in markets:
        if m["kind"] != kind or not m["inplay"]:
            continue
        r = m["runners"].get(runner_name)
        if r is None or r["entry"] is None or not single_winner(m):
            continue
        prepared.append(
            (
                float(r["entry"]),
                [a for a, _ in r["series"]],
                [b for _, b in r["series"]],
                1.0 if r["status"] == "WINNER" else 0.0,
            )
        )
    rows = []
    for tm in times_min:
        implied, outcome, no_sample, stale = [], [], 0, 0
        for entry, ts, ps, won in prepared:
            i = bisect_right(ts, tm * 60.0) - 1
            if i < 0:
                price = entry
                no_sample += 1
            else:
                price = ps[i]
                stale += (tm * 60.0 - ts[i]) > 600
            implied.append(1.0 / price)
            outcome.append(won)
        n = len(implied)
        if n == 0:
            continue
        gap = np.array(outcome) - np.array(implied)
        rows.append(
            {
                "minute": tm,
                "n": n,
                "implied": float(np.mean(implied)),
                "realised": float(np.mean(outcome)),
                "gap": float(gap.mean()),
                "ci": float(1.96 * gap.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan"),
                "no_sample_share": no_sample / n,
                "stale_share": stale / n,
            }
        )
    return rows


def calibration_warnings(rows: Sequence[dict], max_abs_gap: float = 0.03) -> list[str]:
    """Return a message for each time where the as-of implied probability is off by more than ``max_abs_gap``."""
    return [
        f"minute {r['minute']}: implied {r['implied']:.3f} vs realised {r['realised']:.3f} "
        f"(gap {r['gap']:+.3f}) - last traded price is stale or off-market, so exits priced off it are invalid"
        for r in rows
        if abs(r["gap"]) > max_abs_gap
    ]


# --------------------------------------------------------------------------------------------- post-jump events
def lengthens(min_ratio: float) -> Callable[[float, float], bool]:
    """Detector: the price rises by at least ``min_ratio`` (for example 1.2 = +20%) between consecutive samples."""
    return lambda previous, current: current / previous >= min_ratio


def shortens(max_ratio: float) -> Callable[[float, float], bool]:
    """Detector: the price falls to at most ``max_ratio`` of the previous sample (for example 0.85)."""
    return lambda previous, current: current / previous <= max_ratio


def post_jump_events(
    markets: Iterable[dict],
    kind: str,
    runner_name: str,
    detector: Callable[[float, float], bool],
    delay_s: float,
    horizon_min: float,
    window_s: tuple[float, float] = (60.0, 5400.0),
    price_bounds: tuple[float, float] = (1.1, 60.0),
) -> list[tuple[float, float, str, float]]:
    """Find the first sudden price jump in each market and record entry/exit prices after it.

    Prices outside ``price_bounds`` are ignored (end-of-match convergence to 1.01 or 1000 would otherwise swamp
    the result). Entry is the first sample at least ``delay_s`` after the jump; exit is the first sample at least
    ``horizon_min`` after entry. Returns ``(entry_price, exit_price, month, commission_rate)`` per event.
    """
    lo, hi = price_bounds
    t_min, t_max = window_s
    events = []
    for m in markets:
        if m["kind"] != kind:
            continue
        r = m["runners"].get(runner_name)
        if r is None or len(r["series"]) < 2:
            continue
        kept = [(a, b) for a, b in r["series"] if t_min <= a <= t_max + 900 and lo <= b <= hi]
        ts = [a for a, _ in kept]
        ps = [b for _, b in kept]
        k = next((i for i in range(1, len(ps)) if ts[i] <= t_max and detector(ps[i - 1], ps[i])), None)
        if k is None:
            continue
        e = next((i for i in range(k, len(ps)) if ts[i] >= ts[k] + delay_s), None)
        if e is None:
            continue
        j = next((i for i in range(e + 1, len(ps)) if ts[i] >= ts[e] + horizon_min * 60.0), None)
        if j is None:
            continue
        events.append((ps[e], ps[j], m["ko"][:7], float(m["rate"])))
    return events


def jump_profits(
    events: Sequence[tuple[float, float, str, float]],
    slippage: float,
    direction: str,
    stake: float = DEFAULT_STAKE,
) -> np.ndarray:
    """Net result per event for ``BACK_THEN_LAY`` or ``LAY_THEN_BACK`` with ``slippage`` lost on each leg.

    Slippage is a fraction of price (0.01 = 1%): the opening leg gets a worse price and so does the closing leg.
    """
    out = []
    for entry, exit_, _month, rate in events:
        if direction == "BACK_THEN_LAY":
            gross = stake * (entry * (1 - slippage) / (exit_ * (1 + slippage)) - 1.0)
        else:
            gross = stake * (1.0 - entry * (1 + slippage) / (exit_ * (1 - slippage)))
        out.append(net_of_commission(gross, rate))
    return np.array(out)
