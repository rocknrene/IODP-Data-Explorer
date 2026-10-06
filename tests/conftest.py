"""Shared fixtures for the SOD Explorer test suite."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    """Return the raw bytes of a file in ``tests/fixtures``."""
    return lambda name: (FIXTURES / name).read_bytes()


def ar1(n: int, phi: float, rng: np.random.Generator) -> np.ndarray:
    """First-order autoregressive series with unit innovation variance."""
    noise = rng.normal(size=n)
    series = np.empty(n)
    series[0] = noise[0] / np.sqrt(1 - phi**2)
    for i in range(1, n):
        series[i] = phi * series[i - 1] + noise[i]
    return series


@pytest.fixture
def two_hole_tables():
    """Datasets A and B from two holes at one site, on CSF-A."""
    a = pd.DataFrame({
        "Exp": ["405"] * 6, "Site": ["C0019"] * 6, "Hole": ["J", "J", "J", "K", "K", "K"],
        "Core": [1, 1, 2, 1, 1, 2],
        "Depth CSF-A (m)": [1.00, 1.02, 2.00, 1.00, 1.50, 2.00],
        "Bulk density (g/cm3)": [1.50, 1.60, 1.70, 1.40, 1.50, 1.60],
    })
    b = pd.DataFrame({
        "Exp": ["405"] * 3, "Site": ["C0019"] * 3, "Hole": ["J", "J", "K"],
        "Depth CSF-A (m)": [1.01, 2.30, 1.49],
        "Vp (m/s)": [1500.0, 1510.0, 1520.0],
    })
    return a, b
