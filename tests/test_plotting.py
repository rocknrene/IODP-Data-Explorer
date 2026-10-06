"""Tests for figure builders."""

import numpy as np
import pandas as pd
import pytest

from sod_explorer import plotting

DEPTH = "Depth CSF-A (m)"


@pytest.fixture
def two_holes():
    rng = np.random.default_rng(0)
    depth = np.tile(np.linspace(0, 20, 40), 2)
    return pd.DataFrame({
        "Exp": ["362"] * 80, "Site": ["U1480"] * 80, "Hole": ["E"] * 40 + ["F"] * 40,
        "Core": np.repeat(np.arange(1, 5), 10).tolist() * 2, "Type": ["H"] * 80,
        "Offset (cm)": rng.uniform(0, 150, 80), DEPTH: depth,
        "Density": 1.5 + 0.01 * depth + rng.normal(0, 0.01, 80),
        "Porosity": 70 - 0.5 * depth + rng.normal(0, 1, 80),
        "Comments": [""] * 79 + ["cracked"],
    })


def test_depth_log_draws_one_segment_per_hole_and_reverses_depth(two_holes):
    fig = plotting.depth_log_figure(two_holes, DEPTH, ["Density", "Porosity"], gap_threshold_m=0.4)
    lines = [t for t in fig.data if t.mode == "lines"]
    assert len(lines) == 4  # 2 curves x 2 holes
    assert fig.layout.yaxis.autorange == "reversed"
    assert fig.layout.yaxis.title.text == DEPTH
    assert any(t.name == "Comment on sample" for t in fig.data)


def test_depth_log_warns_on_lithology_scale_mismatch(two_holes):
    lithology = pd.DataFrame({"top_depth": [0.0], "bottom_depth": [10.0], "lithology": ["clay"]})
    fig = plotting.depth_log_figure(two_holes, DEPTH, ["Density"], lithology=lithology,
                                    lithology_scale="CCSF-A")
    assert "CCSF-A" in fig.layout.title.text
    fig = plotting.depth_log_figure(two_holes, DEPTH, ["Density"], lithology=lithology,
                                    lithology_scale="mbsf")
    assert fig.layout.title.text is None


def test_heatmap_excludes_identifiers_and_depth(two_holes):
    fig = plotting.heatmap_figure(two_holes)
    assert list(fig.data[0].x) == ["Density", "Porosity"]
    assert "not tested for significance" in fig.layout.title.text


def test_heatmap_requires_two_measurements():
    fig = plotting.heatmap_figure(pd.DataFrame({DEPTH: [1.0], "Core": [1]}))
    assert "At least two" in fig.layout.annotations[0].text


def test_correlation_figure_reports_effective_sample_size(two_holes):
    fig = plotting.correlation_figure(two_holes, DEPTH, "Density", "Porosity")
    assert "n_eff" in fig.layout.title.text
    assert any(t.name == "OLS fit" for t in fig.data)


def test_correlation_figure_with_too_few_pairs():
    df = pd.DataFrame({DEPTH: [1.0, 2.0], "a": [1.0, 2.0], "b": [1.0, 2.0]})
    assert "Fewer than three" in plotting.correlation_figure(df, DEPTH, "a", "b").layout.annotations[0].text


@pytest.mark.parametrize("builder", ["tracks", "smoothed"])
def test_track_builders(two_holes, builder):
    if builder == "tracks":
        fig = plotting.tracks_figure(two_holes, DEPTH, ["Density"], ["Porosity"])
    else:
        fig = plotting.smoothed_figure(two_holes, DEPTH, ["Density"], ["Porosity"], window_m=2.0)
    assert fig.layout.yaxis.autorange == "reversed"
    assert len(fig.data) >= 4


def test_dual_axis_and_simple_builders(two_holes):
    fig = plotting.dual_axis_figure(two_holes, DEPTH, "Density", "Porosity")
    assert fig.layout.yaxis2.overlaying == "y"
    assert len(plotting.line_figure(two_holes, DEPTH, "Density").data) == 2
    scatter = plotting.scatter_figure(two_holes, "Density", DEPTH, None, invert_y=True)
    assert scatter.layout.yaxis.autorange == "reversed"
    assert plotting.histogram_figure(two_holes, "Density").data[0].type == "histogram"


def test_single_hole_is_not_split():
    df = pd.DataFrame({"Site": ["U1"] * 3, "Hole": ["A"] * 3, DEPTH: [1.0, 2.0, 3.0], "x": [1, 2, 3]})
    assert plotting.hole_labels(df) is None


def test_correlation_figure_detrended_axis_titles(two_holes):
    fig = plotting.correlation_figure(two_holes, DEPTH, "Density", "Porosity", detrend=True)
    assert "residual" in fig.layout.xaxis.title.text
    assert fig.layout.title.text.startswith("Detrended residuals")
