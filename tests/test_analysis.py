"""Tests for merging and downhole statistics."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from sod_explorer import analysis
from sod_explorer.analysis import MergeError

from .conftest import ar1

DEPTH = "Depth CSF-A (m)"


class TestMergeByDepth:
    def test_match_count_counts_pairs_not_rows_of_a(self, two_hole_tables):
        a, b = two_hole_tables
        merged, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.02)
        assert len(merged) == len(a)
        assert report.matched == merged["Vp (m/s)_B"].notna().sum() == 2
        assert report.matched < len(a)

    def test_pairs_are_restricted_to_the_same_hole(self, two_hole_tables):
        a, b = two_hole_tables
        merged, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.02)
        paired = merged.dropna(subset=["Vp (m/s)_B"])
        assert (paired["Hole_A"] == paired["Hole_B"]).all()
        # Hole K at 1.00 m is within tolerance of Hole J's 1.01 m sample, but must not pair with it.
        k_top = merged[(merged["Hole_A"] == "K") & (merged[f"{DEPTH}_A"] == 1.00)]
        assert k_top["Vp (m/s)_B"].isna().all()
        assert report.grouping == "hole"

    def test_one_to_one_keeps_nearest_a_sample(self, two_hole_tables):
        a, b = two_hole_tables
        merged, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.02)
        j = merged[merged["Hole_A"] == "J"].set_index(f"{DEPTH}_A")
        # B at 1.01 m is equidistant from A at 1.00 and 1.02 m; the tie goes to the shallower sample.
        assert j.loc[1.00, "Vp (m/s)_B"] == 1500.0
        assert np.isnan(j.loc[1.02, "Vp (m/s)_B"])
        assert report.duplicate_matches_removed == 1

    def test_without_one_to_one_b_samples_are_reused(self, two_hole_tables):
        a, b = two_hole_tables
        merged, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.02, one_to_one=False)
        assert report.matched == 3 and report.duplicate_matches_removed == 0

    def test_depth_offset_column(self, two_hole_tables):
        a, b = two_hole_tables
        merged, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.02)
        offsets = merged["depth_offset_m"].dropna().round(6).tolist()
        assert sorted(offsets) == [-0.01, 0.01]
        assert report.median_abs_offset_m == pytest.approx(0.01)
        assert merged.columns[0] == "merge depth CSF-A (m)"

    def test_tolerance_is_inclusive_and_zero_is_exact(self):
        a = pd.DataFrame({DEPTH: [1.0, 2.0]})
        b = pd.DataFrame({DEPTH: [1.0, 2.05], "x": [1, 2]})
        _, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.0)
        assert report.matched == 1
        _, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, tolerance_m=0.05)
        assert report.matched == 2

    def test_different_scales_are_refused_unless_allowed(self, two_hole_tables):
        a, b = two_hole_tables
        b = b.rename(columns={DEPTH: "Depth CSF-B (m)"})
        with pytest.raises(MergeError, match="CSF-B"):
            analysis.merge_by_depth(a, b, DEPTH, "Depth CSF-B (m)", 0.02)
        _, report = analysis.merge_by_depth(a, b, DEPTH, "Depth CSF-B (m)", 0.02, allow_mixed_scales=True)
        assert any("CSF-B" in w for w in report.warnings)

    def test_legacy_mbsf_is_compatible_with_csf_a(self, two_hole_tables):
        a, b = two_hole_tables
        b = b.rename(columns={DEPTH: "depth_mbsf"})
        _, report = analysis.merge_by_depth(a, b, DEPTH, "depth_mbsf", 0.02)
        assert report.matched == 2

    def test_units_are_converted(self):
        a = pd.DataFrame({DEPTH: [1.0, 2.0]})
        b = pd.DataFrame({"Depth CSF-A (cm)": [100.5, 300.0], "x": [1, 2]})
        _, report = analysis.merge_by_depth(a, b, DEPTH, "Depth CSF-A (cm)", tolerance_m=0.01)
        assert report.matched == 1

    def test_composite_scale_pairs_across_holes_within_site(self):
        a = pd.DataFrame({"Site": ["U1"] * 2, "Hole": ["A", "A"], "Depth CCSF-A (m)": [1.0, 2.0]})
        b = pd.DataFrame({"Site": ["U1"] * 2, "Hole": ["B", "B"], "Depth CCSF-A (m)": [1.0, 2.0], "x": [5, 6]})
        _, report = analysis.merge_by_depth(a, b, "Depth CCSF-A (m)", "Depth CCSF-A (m)", 0.01)
        assert report.grouping == "site" and report.matched == 2

    def test_missing_identifiers_produce_a_warning(self):
        a = pd.DataFrame({DEPTH: [1.0]})
        b = pd.DataFrame({DEPTH: [1.0], "x": [1]})
        _, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, 0.01)
        assert report.grouping == "none"
        assert any("depth alone" in w for w in report.warnings)

    def test_rows_without_depth_are_reported(self):
        a = pd.DataFrame({DEPTH: [1.0, np.nan]})
        b = pd.DataFrame({DEPTH: [1.0], "x": [1]})
        merged, report = analysis.merge_by_depth(a, b, DEPTH, DEPTH, 0.01)
        assert report.rows_a_with_depth == 1 and len(merged) == 1

    def test_invalid_inputs(self, two_hole_tables):
        a, b = two_hole_tables
        with pytest.raises(MergeError):
            analysis.merge_by_depth(a, b, "missing", DEPTH, 0.01)
        with pytest.raises(MergeError):
            analysis.merge_by_depth(a, b, DEPTH, DEPTH, -1)


class TestCorrelation:
    def test_white_noise_effective_size_close_to_n(self):
        rng = np.random.default_rng(1)
        x, y = rng.normal(size=500), rng.normal(size=500)
        result = analysis.correlate(x, y, np.arange(500))
        assert result.n_effective == pytest.approx(500, rel=0.1)
        assert result.p_value == pytest.approx(stats.pearsonr(x, y).pvalue, rel=0.2)

    def test_autocorrelation_reduces_effective_size(self):
        rng = np.random.default_rng(2)
        x, y = ar1(400, 0.9, rng), ar1(400, 0.9, rng)
        result = analysis.correlate(x, y, np.arange(400))
        expected = 400 * (1 - 0.81) / (1 + 0.81)
        assert result.n_effective == pytest.approx(expected, rel=0.5)
        assert result.p_value > stats.pearsonr(x, y).pvalue
        assert result.ci_low <= result.r <= result.ci_high

    def test_false_positive_rate_is_controlled_for_ar1_series(self):
        """Independent AR(1) pairs should be declared significant about 5% of the time."""
        rng = np.random.default_rng(3)
        naive = adjusted = 0
        trials = 300
        for _ in range(trials):
            x, y = ar1(200, 0.85, rng), ar1(200, 0.85, rng)
            naive += stats.pearsonr(x, y).pvalue < 0.05
            adjusted += analysis.correlate(x, y, np.arange(200)).p_value < 0.05
        assert naive / trials > 0.25
        assert adjusted / trials < 0.10

    def test_results_do_not_depend_on_input_order(self):
        rng = np.random.default_rng(4)
        depth = np.arange(100.0)
        x, y = ar1(100, 0.5, rng), ar1(100, 0.5, rng)
        order = rng.permutation(100)
        first = analysis.correlate(x, y, depth)
        second = analysis.correlate(x[order], y[order], depth[order])
        assert first.n_effective == pytest.approx(second.n_effective)

    def test_lag1_within_groups_only(self):
        values = np.array([0, 1, 2, 3, 100, 101, 102, 103], dtype=float)
        groups = np.array(["a"] * 4 + ["b"] * 4)
        assert analysis.lag1_autocorrelation(values, groups) == pytest.approx(1.0)

    def test_degenerate_inputs(self):
        with pytest.raises(ValueError):
            analysis.correlate([1, 2], [1, 2], [1, 2])
        with pytest.raises(ValueError):
            analysis.correlate([1, 1, 1, 1], [1, 2, 3, 4], [1, 2, 3, 4])

    def test_summary_mentions_effective_size(self):
        rng = np.random.default_rng(5)
        text = analysis.correlate(rng.normal(size=50), rng.normal(size=50), np.arange(50)).summary()
        assert "n_eff" in text and "95% CI" in text

    def test_effective_sample_size_bounds(self):
        assert analysis.effective_sample_size(100, 0.0, 0.0) == 100
        assert analysis.effective_sample_size(100, -0.9, 0.9) == 100
        assert analysis.effective_sample_size(100, 0.999, 0.999) == 2
        assert np.isnan(analysis.effective_sample_size(100, np.nan, 0.5))


class TestDepthWindowMean:
    def test_matches_brute_force(self):
        rng = np.random.default_rng(6)
        depth = np.sort(rng.uniform(0, 50, 300))
        values = rng.normal(size=300)
        smooth = analysis.depth_window_mean(depth, values, window_m=4.0, min_count=1)
        for i in (0, 57, 150, 299):
            inside = np.abs(depth - depth[i]) <= 2.0
            assert smooth[i] == pytest.approx(values[inside].mean())

    def test_does_not_bridge_gaps_or_groups(self):
        depth = [0.0, 0.1, 0.2, 10.0, 10.1, 10.2]
        values = [1, 1, 1, 9, 9, 9]
        assert analysis.depth_window_mean(depth, values, 1.0).tolist() == [1, 1, 1, 9, 9, 9]
        grouped = analysis.depth_window_mean([0, 0.1, 0.2, 0, 0.1, 0.2], values, 1.0,
                                             groups=["a"] * 3 + ["b"] * 3)
        assert grouped.tolist() == [1, 1, 1, 9, 9, 9]

    def test_min_count_and_unsorted_input(self):
        result = analysis.depth_window_mean([5.0, 0.0, 0.1], [7, 1, 3], 1.0, min_count=2)
        assert np.isnan(result[0]) and result[1] == result[2] == 2.0


def test_sampling_gaps_are_evaluated_per_hole():
    df = pd.DataFrame({"Site": ["C0019"] * 4, "Hole": ["J", "J", "K", "K"], DEPTH: [1, 9, 1, 2]})
    assert analysis.find_sampling_gaps(df, DEPTH, 5.0) == [("C0019J", 1.0, 9.0)]
    single = pd.DataFrame({DEPTH: [0, 1, 7]})
    assert analysis.find_sampling_gaps(single, DEPTH, 5.0) == [("", 1.0, 7.0)]


def test_core_tops_from_lims_columns():
    df = pd.DataFrame({"Hole": ["E"] * 4, "Core": [1, 1, 2, 2], "Type": ["H"] * 4, DEPTH: [0.5, 3.0, 5.6, 7.1]})
    assert analysis.core_tops(df, DEPTH) == [("1H", 0.5), ("2H", 5.6)]


def test_core_tops_from_jcores_ids():
    df = pd.DataFrame({"sample_id": ["C0019J-1K-1", "C0019J-1K-2", "C0019J-2K-1"], DEPTH: [800.0, 801.0, 810.0]})
    assert analysis.core_tops(df, DEPTH) == [("1K", 800.0), ("2K", 810.0)]


def test_comment_flagged():
    df = pd.DataFrame({"Comments": ["", None, "cracked", " "]})
    assert analysis.comment_flagged(df).tolist() == [False, False, True, False]


def test_expedition_filter_on_merged_table():
    df = pd.DataFrame({"Exp_A": [362, 405], "Exp_B": [None, 405], "x": [1, 2]})
    assert analysis.expedition_values(df) == ["362", "405"]
    assert analysis.filter_expeditions(df, ["362"])["x"].tolist() == [1]
    assert analysis.filter_expeditions(df, []).empty
    assert len(analysis.filter_expeditions(df, None)) == 2


class TestDetrending:
    def test_shared_trend_leaves_significance_unassessable(self):
        rng = np.random.default_rng(8)
        depth = np.linspace(0, 100, 200)
        x = 0.01 * depth + rng.normal(0, 0.02, 200)
        y = 2.0 * depth + rng.normal(0, 5, 200)
        raw = analysis.correlate(x, y, depth)
        assert raw.r > 0.9 and raw.n_effective < 4 and np.isnan(raw.p_value)
        assert "not assessable" in raw.summary()

    def test_detrended_independent_noise_is_not_significant(self):
        rng = np.random.default_rng(9)
        depth = np.linspace(0, 100, 200)
        x = 0.01 * depth + rng.normal(0, 0.02, 200)
        y = 2.0 * depth + rng.normal(0, 5, 200)
        result = analysis.correlate(x, y, depth, detrend=True)
        assert result.detrended and abs(result.r) < 0.2 and result.p_value > 0.05

    def test_detrended_shared_signal_is_detected(self):
        rng = np.random.default_rng(10)
        depth = np.linspace(0, 100, 200)
        signal = rng.normal(0, 1, 200)
        result = analysis.correlate(depth + signal, -3 * depth + signal + rng.normal(0, 0.3, 200),
                                    depth, detrend=True)
        assert result.r > 0.8 and result.p_value < 0.001

    def test_detrend_is_per_group(self):
        depth = np.array([0, 1, 2, 0, 1, 2], dtype=float)
        values = np.array([0, 1, 2, 10, 12, 14], dtype=float)
        residuals = analysis.detrend_by_depth(values, depth, ["a"] * 3 + ["b"] * 3)
        assert np.allclose(residuals, 0)
