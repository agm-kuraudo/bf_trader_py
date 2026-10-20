"""Tests for the SP-369 exploration script's plotting-safety helper.

Regression coverage for the chart-rendering bug found on the Pi: the SP-368
dataset uses pandas *nullable* extension dtypes (``Float64`` for prices/volume,
``datetime64[ns, UTC]`` for timestamps), and matplotlib 3.8 draws a nullable
``Float64`` Series as a blank line (no exception, no points) when it comes from
a groupby slice of a Parquet-reloaded frame. ``_plot_values`` converts a column
to a plain NumPy array before plotting to side-step that; these tests lock in
that conversion.

The scope here is the thin-wrapper helper only (the pure-core maths is covered
by ``test_property_odds_behaviour.py``). The script imports matplotlib with the
headless Agg backend, so this is import-safe in CI.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from scripts.explore_odds_behaviour import _plot_values


class TestPlotValues:
    """``_plot_values`` yields plain NumPy arrays matplotlib renders reliably."""

    def test_nullable_float64_becomes_plain_float64(self):
        series = pd.Series([2.0, 1.9, 1.8], dtype="Float64")
        out = _plot_values(series)
        assert isinstance(out, np.ndarray)
        assert out.dtype == np.float64
        assert np.allclose(out, [2.0, 1.9, 1.8])

    def test_nullable_na_becomes_nan_gap(self):
        series = pd.Series([2.0, pd.NA, 1.8], dtype="Float64")
        out = _plot_values(series)
        # NA must survive as NaN (a plotted gap) rather than raising or dropping.
        assert out.dtype == np.float64
        assert np.isnan(out[1])
        assert out[0] == 2.0 and out[2] == 1.8

    def test_tz_aware_datetime_becomes_datetime64(self):
        series = pd.to_datetime(["2026-10-14T10:00", "2026-10-14T14:00", "2026-10-14T18:00"], utc=True)
        out = _plot_values(pd.Series(series))
        assert np.issubdtype(out.dtype, np.datetime64)
        assert out.shape == (3,)

    def test_plain_float_series_passthrough(self):
        series = pd.Series([0.5, 0.4, 0.3], dtype="float64")
        out = _plot_values(series)
        assert out.dtype == np.float64
        assert np.allclose(out, [0.5, 0.4, 0.3])
