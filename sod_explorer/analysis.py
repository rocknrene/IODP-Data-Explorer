"""Depth-registered merging and downhole statistics.

This module contains the quantitative operations of SOD Explorer. All
functions are independent of the user interface and are covered by the
test suite.

Depth-tolerance merge
    :func:`merge_by_depth` pairs each sample of dataset A with the nearest
    sample of dataset B within a depth tolerance, restricted to the same
    Hole (or the same Site on a composite depth scale), with each B sample
    used at most once.

Correlation with serially correlated samples
    :func:`correlate` reports the Pearson correlation between two downhole
    series with a significance test and confidence interval based on an
    effective sample size that accounts for lag-1 autocorrelation
    (Bretherton et al., 1999).

Depth-window smoothing
    :func:`depth_window_mean` computes a centered moving average over a
    fixed depth interval, evaluated separately for each Hole.

Sampling gaps, core tops, comment flags
    Helpers for the depth-log overlays.

References
----------
Bretherton, C. S., Widmann, M., Dymnikov, V. P., Wallace, J. M., & Bladé, I.
(1999). The effective number of spatial degrees of freedom of a
time-varying field. *Journal of Climate*, 12(7), 1990-2009.

IODP-MI (2011). *IODP Depth Scales Terminology*, version 2.0.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from .columns import (
    canonical_scale,
    depth_scale,
    depth_unit_factor,
    find_identifier_column,
    group_key,
    hole_key_columns,
    is_composite_scale,
    normalize_identifier,
)


class MergeError(ValueError):
    """Raised when two datasets cannot be merged under the requested rules."""


# ---------------------------------------------------------------------------
# Depth-tolerance merge
# ---------------------------------------------------------------------------

@dataclass
class MergeReport:
    """Summary of a depth-tolerance merge, recorded in the export provenance.

    Attributes
    ----------
    rows_a, rows_b
        Rows in each input dataset.
    rows_a_with_depth, rows_b_with_depth
        Rows with a finite depth value (rows without depth cannot be matched).
    matched
        Rows of A paired with a sample of B. Each B sample is used at most
        once, so this equals the number of B samples used.
    duplicate_matches_removed
        Candidate pairs discarded because the B sample was already paired
        with a closer A sample.
    tolerance_m
        Maximum depth separation, in meters, for a pair.
    depth_scale_a, depth_scale_b
        Depth scales inferred from the depth-column headers.
    grouping
        ``"hole"`` (pairs restricted to the same Hole), ``"site"`` (same
        Site; used when both datasets are on a composite scale), or
        ``"none"`` (identifier columns unavailable).
    median_abs_offset_m
        Median absolute depth separation of matched pairs, in meters.
    warnings
        Conditions that limit the interpretation of the result.
    """

    rows_a: int
    rows_b: int
    rows_a_with_depth: int
    rows_b_with_depth: int
    matched: int
    duplicate_matches_removed: int
    tolerance_m: float
    depth_column_a: str
    depth_column_b: str
    depth_scale_a: str | None
    depth_scale_b: str | None
    grouping: str
    merge_depth_column: str
    median_abs_offset_m: float | None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return the report as a plain dictionary for storage in provenance records."""
        return asdict(self)


def _suffix_columns(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Append a suffix to every column name."""
    return df.rename(columns={c: f"{c}{suffix}" for c in df.columns})


def merge_by_depth(
    dfa: pd.DataFrame,
    dfb: pd.DataFrame,
    depth_column_a: str,
    depth_column_b: str,
    tolerance_m: float,
    allow_mixed_scales: bool = False,
    one_to_one: bool = True,
    across_holes: bool = False,
) -> tuple[pd.DataFrame, MergeReport]:
    """Pair samples of two datasets by depth within a tolerance.

    Each row of ``dfa`` is paired with the row of ``dfb`` whose depth is
    nearest, provided the separation does not exceed ``tolerance_m``. The
    procedure is as follows.

    1. **Depth scale check.** The depth scale of each depth column is
       inferred from its header. If both scales are recognized and differ
       (after mapping legacy "mbsf" to CSF-A and "mcd" to CCSF-A), the merge
       is refused unless ``allow_mixed_scales`` is true, because depths on
       different scales are not directly comparable.
    2. **Unit conversion.** Depths are converted to meters using the unit
       stated in each header; meters are assumed if no unit is stated.
    3. **Grouping.** If both datasets have Site and Hole columns, pairs are
       restricted to the same Hole. If both depth columns are on a
       composite scale (CCSF, mcd), pairs are instead restricted to the same
       Site, since a composite scale registers all holes at a site to a
       common depth. If identifiers are unavailable, rows are paired on
       depth alone and a warning is recorded. With ``across_holes``, the
       restriction is lifted on request: rows are paired on depth alone
       even though they come from different Holes or Sites (for example,
       to compare depth trends between a DSDP Site and an IODP Site), and
       a warning states that paired samples are not from the same location.
    4. **Nearest-neighbor pairing** (:func:`pandas.merge_asof`,
       ``direction="nearest"``).
    5. **One-to-one constraint.** When ``one_to_one`` is true, a B sample
       paired with several A samples is retained only for the A sample
       nearest in depth (ties go to the shallower A sample); the other
       pairs are discarded. Without this constraint a single B measurement
       could be counted many times, inflating the sample size of any
       subsequent statistic.

    Parameters
    ----------
    dfa, dfb
        Input datasets.
    depth_column_a, depth_column_b
        Names of the depth columns in each dataset.
    tolerance_m
        Maximum depth separation of a pair, in meters. Must be non-negative.
    allow_mixed_scales
        Permit merging when the two depth scales differ.
    one_to_one
        Enforce that each B sample is paired with at most one A sample.
    across_holes
        Pair on depth alone, ignoring Site and Hole.

    Returns
    -------
    merged : pandas.DataFrame
        One row per row of A with a valid depth. Columns of A carry the
        suffix ``_A``; columns of B carry ``_B`` and are empty where no pair
        was found. The first two columns are the merge depth (A's depth in
        meters) and ``depth_offset_m`` (B depth minus A depth).
    report : MergeReport
        Counts, parameters, and warnings for the merge.

    Raises
    ------
    MergeError
        If a depth column is missing, the tolerance is negative, or the
        depth scales differ and ``allow_mixed_scales`` is false.
    """
    for df, column, label in ((dfa, depth_column_a, "A"), (dfb, depth_column_b, "B")):
        if column not in df.columns:
            raise MergeError(f"Depth column '{column}' not found in dataset {label}")
    if tolerance_m is None or not np.isfinite(tolerance_m) or tolerance_m < 0:
        raise MergeError("Depth tolerance must be a non-negative number")

    warnings: list[str] = []

    # 1. Depth scales.
    scale_a, scale_b = depth_scale(depth_column_a), depth_scale(depth_column_b)
    canon_a, canon_b = canonical_scale(scale_a), canonical_scale(scale_b)
    if canon_a and canon_b and canon_a != canon_b:
        message = (f"Dataset A depths are on {scale_a} and dataset B depths are on {scale_b}. "
                   "Depths on different scales are not directly comparable.")
        if not allow_mixed_scales:
            raise MergeError(message + " Select depth columns on the same scale, "
                                       "or explicitly allow mixed scales.")
        warnings.append(message + " The merge was performed because mixed scales were allowed.")
    for scale, label, column in ((scale_a, "A", depth_column_a), (scale_b, "B", depth_column_b)):
        if scale is None:
            warnings.append(f"The depth scale of dataset {label} ('{column}') could not be "
                            "determined from its header; confirm both datasets use the same scale.")

    # 2. Units.
    factor_a, assumed_a = depth_unit_factor(depth_column_a)
    factor_b, assumed_b = depth_unit_factor(depth_column_b)
    for assumed, label, column in ((assumed_a, "A", depth_column_a), (assumed_b, "B", depth_column_b)):
        if assumed:
            warnings.append(f"No depth unit is stated in '{column}' (dataset {label}); meters were assumed.")

    a = dfa.copy()
    b = dfb.copy()
    a["_depth_m"] = pd.to_numeric(a[depth_column_a], errors="coerce") * factor_a
    b["_depth_m"] = pd.to_numeric(b[depth_column_b], errors="coerce") * factor_b

    # 3. Grouping.
    composite = is_composite_scale(scale_a) and is_composite_scale(scale_b)
    level = "site" if composite else "hole"
    keys_a, keys_b = hole_key_columns(dfa), hole_key_columns(dfb)
    include_exp = keys_a.expedition is not None and keys_b.expedition is not None
    group_a = group_key(dfa, level, include_expedition=include_exp)
    group_b = group_key(dfb, level, include_expedition=include_exp)
    if across_holes:
        grouping = "none"
        warnings.append(
            "Rows were paired on depth below seafloor alone, across different Sites or Holes, "
            "as requested. Paired samples are not from the same location; the result compares "
            "depth trends and does not represent co-located measurements.")
    elif group_a is not None and group_b is not None:
        grouping = level
        a["_group"] = group_a.values
        b["_group"] = group_b.values
        if not set(a["_group"].dropna()) & set(b["_group"].dropna()):
            warnings.append(f"The two datasets share no {level}; no pairs are possible. To compare "
                            "depth trends between different Sites, select 'Pair across different "
                            "Sites/Holes'.")
    else:
        grouping = "none"
        lacking = [label for label, g in (("A", group_a), ("B", group_b)) if g is None]
        warnings.append(
            f"Site/Hole identifier columns are missing from dataset {' and '.join(lacking)}; "
            "rows were paired on depth alone. This is valid only if both datasets describe "
            "the same hole or share a composite depth scale.")

    # Rows without a finite depth (or group) cannot be positioned.
    required = ["_depth_m"] + (["_group"] if grouping != "none" else [])
    a_valid = a.dropna(subset=required)
    b_valid = b.dropna(subset=required)
    a_valid = a_valid[np.isfinite(a_valid["_depth_m"])]
    b_valid = b_valid[np.isfinite(b_valid["_depth_m"])]
    if len(a_valid) < len(a):
        warnings.append(f"{len(a) - len(a_valid)} rows of dataset A have no valid depth and were excluded.")

    # 4. Nearest-neighbor pairing.
    helper = ["_depth_m"] + (["_group"] if grouping != "none" else [])
    left = _suffix_columns(a_valid.drop(columns=helper), "_A")
    left[helper] = a_valid[helper].values
    left["_row_a"] = np.arange(len(left))
    right = _suffix_columns(b_valid.drop(columns=helper), "_B")
    right[helper] = b_valid[helper].values
    right["_row_b"] = np.arange(len(right))
    right["_depth_b_m"] = right["_depth_m"]
    left = left.sort_values("_depth_m", kind="mergesort")
    right = right.sort_values("_depth_m", kind="mergesort")
    left["_depth_m"] = left["_depth_m"].astype(float)
    right["_depth_m"] = right["_depth_m"].astype(float)

    merged = pd.merge_asof(
        left, right, on="_depth_m",
        by="_group" if grouping != "none" else None,
        tolerance=float(tolerance_m), direction="nearest",
    )

    # 5. One-to-one constraint.
    b_columns = [c for c in right.columns if c not in helper]
    matched = merged["_row_b"].notna()
    # Offsets are rounded to 1 nm so that separations equal up to
    # floating-point error are treated as ties (resolved by shallower depth).
    merged["_offset"] = (merged["_depth_b_m"] - merged["_depth_m"]).abs().round(9)
    duplicates_removed = 0
    if one_to_one and matched.any():
        candidates = merged.loc[matched].sort_values(["_row_b", "_offset", "_depth_m"], kind="mergesort")
        losers = candidates.index[candidates.duplicated("_row_b", keep="first")]
        duplicates_removed = int(len(losers))
        merged.loc[losers, b_columns] = np.nan
        merged.loc[losers, "_offset"] = np.nan
        matched = merged["_row_b"].notna()

    merge_depth_name = f"merge depth {scale_a or 'unknown scale'} (m)"
    merged.insert(0, "depth_offset_m", merged["_depth_b_m"] - merged["_depth_m"])
    merged.insert(0, merge_depth_name, merged["_depth_m"])
    sort_keys = (["_group"] if grouping != "none" else []) + ["_depth_m"]
    merged = merged.sort_values(sort_keys, kind="mergesort")
    median_offset = float(merged.loc[matched, "_offset"].median()) if matched.any() else None
    merged = merged.drop(columns=[c for c in merged.columns if c.startswith("_")]).reset_index(drop=True)

    report = MergeReport(
        rows_a=int(len(dfa)), rows_b=int(len(dfb)),
        rows_a_with_depth=int(len(a_valid)), rows_b_with_depth=int(len(b_valid)),
        matched=int(matched.sum()), duplicate_matches_removed=duplicates_removed,
        tolerance_m=float(tolerance_m),
        depth_column_a=depth_column_a, depth_column_b=depth_column_b,
        depth_scale_a=scale_a, depth_scale_b=scale_b,
        grouping=grouping, merge_depth_column=merge_depth_name,
        median_abs_offset_m=median_offset, warnings=warnings,
    )
    return merged, report


# ---------------------------------------------------------------------------
# Correlation with an effective sample size
# ---------------------------------------------------------------------------

@dataclass
class CorrelationResult:
    """Pearson correlation with an autocorrelation-adjusted significance test.

    Attributes
    ----------
    r, r_squared
        Pearson correlation coefficient and its square.
    n
        Number of complete (x, y) pairs.
    lag1_x, lag1_y
        Lag-1 autocorrelation of each series, ordered by depth within
        groups. ``nan`` if fewer than three consecutive pairs exist.
    n_effective
        Effective sample size (see :func:`effective_sample_size`).
    p_value
        Two-sided p-value of the t-test for r = 0 with ``n_effective - 2``
        degrees of freedom. ``nan`` if ``n_effective`` is 3 or less.
    ci_low, ci_high
        95% confidence interval for r from the Fisher z-transformation
        with standard error ``1 / sqrt(n_effective - 3)``.
    slope, intercept
        Ordinary least-squares regression of y on x. The regression is
        not symmetric in x and y and assumes x is measured without error.
    detrended
        True if a linear depth trend was removed from each variable (within
        each group) before the statistics were computed; the statistics then
        refer to the residuals.
    """

    r: float
    r_squared: float
    n: int
    lag1_x: float
    lag1_y: float
    n_effective: float
    p_value: float
    ci_low: float
    ci_high: float
    slope: float
    intercept: float
    detrended: bool = False

    def summary(self) -> str:
        """One-line summary for figure titles."""
        text = ("Detrended residuals: " if self.detrended else "") + \
            f"r = {self.r:.3f}, r² = {self.r_squared:.3f}, n = {self.n}"
        if np.isfinite(self.n_effective):
            text += f", n_eff = {self.n_effective:.1f}"
        if np.isfinite(self.p_value):
            text += f", p = {self.p_value:.2g}, 95% CI [{self.ci_low:.2f}, {self.ci_high:.2f}]"
        else:
            text += " (significance not assessable: n_eff ≤ 3)"
        return text


def lag1_autocorrelation(values: np.ndarray, groups: np.ndarray | None = None) -> float:
    """Lag-1 autocorrelation of an ordered series.

    Computed as the Pearson correlation between consecutive values. When
    ``groups`` is given, only consecutive values within the same group
    contribute, so that the last sample of one hole is not paired with the
    first sample of the next.

    Parameters
    ----------
    values
        Series ordered by depth (within groups).
    groups
        Group label per value, or ``None`` for a single group.

    Returns
    -------
    float
        The lag-1 autocorrelation, or ``nan`` if fewer than three
        consecutive pairs exist or either member of the pairs is constant.
    """
    values = np.asarray(values, dtype=float)
    if len(values) < 4:
        return float("nan")
    lead, lag = values[:-1], values[1:]
    if groups is not None:
        groups = np.asarray(groups)
        same = groups[:-1] == groups[1:]
        lead, lag = lead[same], lag[same]
    if len(lead) < 3 or np.std(lead) == 0 or np.std(lag) == 0:
        return float("nan")
    return float(np.corrcoef(lead, lag)[0, 1])


def effective_sample_size(n: int, lag1_x: float, lag1_y: float) -> float:
    """Effective number of independent pairs for correlating two AR(1) series.

    Uses the approximation of Bretherton et al. (1999)::

        n_eff = n * (1 - r1x * r1y) / (1 + r1x * r1y)

    where r1x and r1y are the lag-1 autocorrelations. The result is
    bounded to [2, n]. The approximation assumes each series is a
    first-order autoregressive process sampled at approximately uniform
    spacing; irregular downhole sampling makes it approximate.

    Returns ``nan`` if either autocorrelation is undefined.
    """
    if not (np.isfinite(lag1_x) and np.isfinite(lag1_y)):
        return float("nan")
    product = lag1_x * lag1_y
    n_eff = n * (1.0 - product) / (1.0 + product)
    return float(min(max(n_eff, 2.0), n))


def detrend_by_depth(values, depth, groups=None) -> np.ndarray:
    """Residuals of a least-squares linear fit of values on depth.

    The fit is made separately for each group with at least three samples;
    values in smaller groups are returned as ``nan``.
    """
    values = np.asarray(values, dtype=float)
    depth = np.asarray(depth, dtype=float)
    labels = np.asarray(groups) if groups is not None else np.zeros(len(values))
    residuals = np.full(len(values), np.nan)
    for label in pd.unique(labels):
        idx = np.flatnonzero(labels == label)
        if idx.size >= 3 and np.ptp(depth[idx]) > 0:
            slope, intercept = np.polyfit(depth[idx], values[idx], 1)
            residuals[idx] = values[idx] - (slope * depth[idx] + intercept)
    return residuals


def correlate(x, y, depth, groups=None, detrend: bool = False) -> CorrelationResult:
    """Correlate two downhole series with an autocorrelation-adjusted test.

    Downhole measurements are serially correlated: adjacent samples are not
    independent. A conventional Pearson test treats all ``n`` pairs as
    independent and therefore understates the p-value. This function
    orders the samples by depth (within groups), estimates the lag-1
    autocorrelation of each series, and tests r against a t-distribution
    with ``n_eff - 2`` degrees of freedom, where ``n_eff`` is the
    effective sample size of :func:`effective_sample_size`.

    Parameters
    ----------
    x, y
        Values of the two variables.
    depth
        Depth of each pair, used to order the samples.
    groups
        Optional Hole (or Site) label per pair.
    detrend
        Remove a linear depth trend from each variable within each group
        (:func:`detrend_by_depth`) before computing the statistics.

    Notes
    -----
    Two properties that both change monotonically with depth (for example,
    through compaction) are strongly correlated through the shared trend,
    and their lag-1 autocorrelations approach 1, so ``n_eff`` approaches its
    lower bound of 2 and no significance can be assessed. Detrending tests
    instead whether deviations from the two trends co-vary.

    Returns
    -------
    CorrelationResult

    Raises
    ------
    ValueError
        If fewer than three complete pairs are available or either variable
        is constant.
    """
    frame = pd.DataFrame({"x": np.asarray(x, dtype=float), "y": np.asarray(y, dtype=float),
                          "depth": np.asarray(depth, dtype=float)})
    frame["group"] = np.asarray(groups) if groups is not None else "all"
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["x", "y", "depth"])
    frame = frame.sort_values(["group", "depth"], kind="mergesort")
    if detrend:
        frame["x"] = detrend_by_depth(frame["x"], frame["depth"], frame["group"])
        frame["y"] = detrend_by_depth(frame["y"], frame["depth"], frame["group"])
        frame = frame.dropna(subset=["x", "y"])
    n = len(frame)
    if n < 3:
        raise ValueError("At least three complete pairs are required")
    if frame["x"].std() == 0 or frame["y"].std() == 0:
        raise ValueError("Correlation is undefined for a constant variable")

    r = float(np.corrcoef(frame["x"], frame["y"])[0, 1])
    slope, intercept = np.polyfit(frame["x"], frame["y"], 1)
    group_values = frame["group"].to_numpy()
    lag_x = lag1_autocorrelation(frame["x"].to_numpy(), group_values)
    lag_y = lag1_autocorrelation(frame["y"].to_numpy(), group_values)
    n_eff = effective_sample_size(n, lag_x, lag_y)

    p_value = ci_low = ci_high = float("nan")
    if np.isfinite(n_eff) and n_eff > 3:
        if abs(r) >= 1.0:
            p_value = 0.0
        else:
            t_stat = r * math.sqrt((n_eff - 2.0) / (1.0 - r * r))
            p_value = float(2.0 * stats.t.sf(abs(t_stat), df=n_eff - 2.0))
        z = math.atanh(max(min(r, 0.999999), -0.999999))
        half_width = stats.norm.ppf(0.975) / math.sqrt(n_eff - 3.0)
        ci_low, ci_high = math.tanh(z - half_width), math.tanh(z + half_width)

    return CorrelationResult(
        r=r, r_squared=r * r, n=n, lag1_x=lag_x, lag1_y=lag_y, n_effective=n_eff,
        p_value=p_value, ci_low=ci_low, ci_high=ci_high,
        slope=float(slope), intercept=float(intercept), detrended=detrend,
    )


def correlation_matrix(df: pd.DataFrame, columns: list[str], min_pairs: int = 10) -> pd.DataFrame:
    """Pairwise Pearson correlation matrix of measurement columns.

    Each coefficient uses the rows complete for that pair of columns. Pairs
    with fewer than ``min_pairs`` complete rows are reported as missing.
    No significance test is applied (see :func:`correlate`).
    """
    return df[columns].corr(method="pearson", min_periods=min_pairs)


# ---------------------------------------------------------------------------
# Depth-window smoothing
# ---------------------------------------------------------------------------

def depth_window_mean(depth, values, window_m: float, min_count: int = 3,
                      groups=None) -> np.ndarray:
    """Centered moving average over a fixed depth interval.

    For each sample at depth ``d``, returns the mean of all samples in the
    same group with depths in ``[d - window_m / 2, d + window_m / 2]``.
    Because the window is defined in depth rather than in number of rows,
    its physical length does not vary with sampling density, and it does
    not extend across a sampling gap wider than the window.

    Parameters
    ----------
    depth, values
        Depth and value of each sample.
    window_m
        Full window length, in depth units (meters).
    min_count
        Minimum number of samples in a window; windows with fewer samples
        return ``nan``.
    groups
        Optional Hole label per sample. Windows never include samples from
        another group.

    Returns
    -------
    numpy.ndarray
        Smoothed values aligned with the input order.
    """
    depth = np.asarray(depth, dtype=float)
    values = np.asarray(values, dtype=float)
    if window_m <= 0:
        raise ValueError("Window length must be positive")
    labels = np.asarray(groups) if groups is not None else np.zeros(len(depth))
    result = np.full(len(depth), np.nan)
    half = window_m / 2.0
    for label in pd.unique(labels):
        idx = np.flatnonzero((labels == label) & np.isfinite(depth) & np.isfinite(values))
        if idx.size == 0:
            continue
        order = idx[np.argsort(depth[idx], kind="mergesort")]
        d_sorted, v_sorted = depth[order], values[order]
        cumulative = np.concatenate(([0.0], np.cumsum(v_sorted)))
        lo = np.searchsorted(d_sorted, d_sorted - half, side="left")
        hi = np.searchsorted(d_sorted, d_sorted + half, side="right")
        counts = hi - lo
        means = (cumulative[hi] - cumulative[lo]) / np.maximum(counts, 1)
        means[counts < min_count] = np.nan
        result[order] = means
    return result


# ---------------------------------------------------------------------------
# Depth-log overlays
# ---------------------------------------------------------------------------

def find_sampling_gaps(df: pd.DataFrame, depth_column: str, threshold_m: float,
                       by_hole: bool = True) -> list[tuple[str, float, float]]:
    """Depth intervals with no samples wider than a threshold.

    A sampling gap is an interval between two consecutive sampled depths
    whose separation exceeds ``threshold_m``. It indicates where the
    dataset has no measurements; it is not a measure of core recovery,
    which requires the cored and curated intervals of each core.

    When ``by_hole`` is true and Site/Hole columns exist, consecutive depths
    are evaluated within each Hole, so that samples from one Hole do not
    conceal a gap in another.

    Returns
    -------
    list of (hole, top, bottom)
        Hole label (empty when holes are not distinguished) and the gap
        interval in the depth units of ``depth_column``, sorted by top depth.
    """
    if depth_column not in df.columns or threshold_m is None or threshold_m <= 0:
        return []
    factor, _ = depth_unit_factor(depth_column)
    keys = hole_key_columns(df)
    grouped = by_hole and keys.complete and df[[keys.site, keys.hole]].drop_duplicates().shape[0] > 1
    frame = pd.DataFrame({"depth": pd.to_numeric(df[depth_column], errors="coerce")})
    frame["hole"] = (df[keys.site].astype(str).str.strip() + df[keys.hole].astype(str).str.strip()
                     if grouped else "")
    frame = frame.dropna()
    gaps: list[tuple[str, float, float]] = []
    for hole, part in frame.groupby("hole", sort=False):
        depths = np.sort(part["depth"].unique())
        spacing = np.diff(depths) * factor
        for i in np.flatnonzero(spacing > threshold_m):
            gaps.append((str(hole), float(depths[i]), float(depths[i + 1])))
    return sorted(gaps, key=lambda g: (g[1], g[0]))


def core_tops(df: pd.DataFrame, depth_column: str) -> list[tuple[str, float]]:
    """Shallowest sampled depth in each core.

    Cores are identified by the Core column (grouped by Hole when Site/Hole
    columns exist) or, failing that, by J-CORES sample identifiers of the
    form ``<site+hole>-<core>-...``. The returned depth is the shallowest
    *measured* sample of each core, which approximates but does not equal
    the curated core top.

    Returns
    -------
    list of (label, depth)
        Core label (``"4H"``, or ``"B-4H"`` when several holes are present)
        and depth, sorted by depth.
    """
    if depth_column not in df.columns:
        return []
    depth = pd.to_numeric(df[depth_column], errors="coerce")
    core_col = find_identifier_column(df, "core")
    if core_col is not None:
        keys = hole_key_columns(df)
        type_col = next((c for c in df.columns if str(c).strip().lower() == "type"), None)
        core_label = df[core_col].map(normalize_identifier).str.upper()
        if type_col is not None:
            core_label = core_label + df[type_col].fillna("").astype(str).str.strip().str.upper()
        multi_hole = keys.hole is not None and df[keys.hole].nunique() > 1
        if multi_hole:
            core_label = df[keys.hole].astype(str).str.strip() + "-" + core_label
        frame = pd.DataFrame({"label": core_label, "depth": depth}).dropna()
    else:
        id_col = next((c for c in df.columns
                       if str(c).lower() in ("jcores_sampleid", "sampleid", "sample_id")), None)
        if id_col is None:
            return []
        parts = df[id_col].astype(str).str.split("-")
        valid = parts.str.len() >= 3
        frame = pd.DataFrame({"label": parts[valid].str[1], "depth": depth[valid]}).dropna()
    tops = frame.groupby("label", sort=False)["depth"].min().sort_values()
    return [(str(label), float(value)) for label, value in tops.items()]


def comment_column(df: pd.DataFrame) -> str | None:
    """First column whose header contains "comment", if any."""
    return next((c for c in df.columns if "comment" in str(c).lower()), None)


def comment_flagged(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of rows with a non-empty comment field.

    This is a screening aid, not a quality-control classification: LIMS
    comment fields record any note an analyst attached to a result, which
    may or may not indicate a compromised measurement.
    """
    column = comment_column(df)
    if column is None:
        return pd.Series(False, index=df.index)
    text = df[column].fillna("").astype(str).str.strip()
    return text.ne("") & text.str.lower().ne("nan")


# ---------------------------------------------------------------------------
# Expedition filtering
# ---------------------------------------------------------------------------

def expedition_values(df: pd.DataFrame) -> list[str]:
    """Distinct Expedition/Leg values in a table, in natural order.

    In a merged table the Expedition columns of both datasets are
    considered.
    """
    from .reference import natural_sort_key

    values: set[str] = set()
    for column in df.columns:
        if find_identifier_column(df[[column]], "expedition") is not None:
            values.update(df[column].dropna().map(normalize_identifier).str.upper())
    return sorted(values, key=natural_sort_key)


def filter_expeditions(df: pd.DataFrame, selected: list[str] | None) -> pd.DataFrame:
    """Keep rows whose Expedition is in ``selected``.

    Rows are kept if any Expedition column of the table (both ``_A`` and
    ``_B`` columns in a merged table) matches. A table without an
    Expedition column, or a selection of ``None``, is returned unchanged;
    an empty selection returns no rows.
    """
    if selected is None:
        return df
    wanted = {normalize_identifier(v) for v in selected}
    columns = [c for c in df.columns if find_identifier_column(df[[c]], "expedition") is not None]
    if not columns:
        return df
    keep = pd.Series(False, index=df.index)
    for column in columns:
        keep |= df[column].map(normalize_identifier).isin(wanted)
    return df[keep]
