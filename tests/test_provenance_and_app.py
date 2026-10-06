"""Tests for provenance records, exports, and application assembly."""

import io
import json
import zipfile

import numpy as np
import pandas as pd

from sod_explorer import __version__, provenance
from sod_explorer.analysis import merge_by_depth
from sod_explorer.app import create_app
from sod_explorer.callbacks import from_store, to_store

DEPTH = "Depth CSF-A (m)"


def _pangaea_record(df):
    return provenance.new_record({
        "type": "pangaea", "name": "PANGAEA dataset 1", "doi": "10.1594/PANGAEA.1",
        "citation": "Party (1975): Data. PANGAEA, https://doi.org/10.1594/PANGAEA.1",
    }, df)


def test_upload_record(two_hole_tables):
    a, _ = two_hole_tables
    record = provenance.upload_record({"filename": "a.csv", "sha256": "0" * 64, "format": "CSV"}, a)
    assert record["schema"] == provenance.SCHEMA
    assert record["software"]["version"] == __version__
    assert record["source"]["file"]["sha256"] == "0" * 64
    assert record["table"]["depth_scale"] == "CSF-A"


def test_merge_export_contains_data_provenance_and_citations(two_hole_tables):
    a, b = two_hole_tables
    merged, report = merge_by_depth(a, b, DEPTH, DEPTH, 0.02)
    lore_record = provenance.new_record({"type": "lore", "citation_note": "Cite the Proceedings."}, a)
    record = provenance.merge_record(lore_record, _pangaea_record(b), report.to_dict(), merged)
    record = provenance.add_step(record, "filter_expeditions", selected=["405"])

    archive = zipfile.ZipFile(io.BytesIO(provenance.export_archive(merged, record, "out")))
    assert sorted(archive.namelist()) == ["out/CITATION.txt", "out/data.csv", "out/provenance.json"]
    stored = json.loads(archive.read("out/provenance.json"))
    assert stored["merge"]["matched"] == 2 and stored["merge"]["tolerance_m"] == 0.02
    assert stored["inputs"]["B"]["source"]["doi"] == "10.1594/PANGAEA.1"
    assert stored["processing"][0]["step"] == "filter_expeditions"
    citations = archive.read("out/CITATION.txt").decode()
    assert "10.1594/PANGAEA.1" in citations and "Cite the Proceedings." in citations
    assert len(pd.read_csv(archive.open("out/data.csv"))) == len(merged)


def test_non_finite_values_become_null():
    record = provenance.new_record({"type": "upload", "x": float("nan")}, pd.DataFrame({"a": [1]}))
    record = provenance.add_step(record, "step", value=np.float64("inf"))
    payload = provenance.export_archive(pd.DataFrame({"a": [1]}), record)
    stored = json.loads(zipfile.ZipFile(io.BytesIO(payload)).read("sod_export/provenance.json"))
    assert stored["source"]["x"] is None and stored["processing"][0]["parameters"]["value"] is None


def test_export_without_provenance():
    payload = provenance.export_archive(pd.DataFrame({"a": [1]}), None)
    assert "sod_export/provenance.json" in zipfile.ZipFile(io.BytesIO(payload)).namelist()


def test_store_round_trip_preserves_identifiers_and_precision():
    df = pd.DataFrame({"Site": ["0019", "U1480"], "Depth CSF-A (m)": [1.123456789012, 1.503],
                       "Timestamp": ["2024-01-01", "2024-01-02"]})
    restored = from_store(to_store(df))
    assert restored["Site"].tolist() == ["0019", "U1480"]
    assert restored["Depth CSF-A (m)"].tolist() == [1.123456789012, 1.503]
    assert restored["Timestamp"].tolist() == ["2024-01-01", "2024-01-02"]


def test_app_builds_with_unique_component_ids():
    app = create_app()
    ids = []

    def walk(component):
        if getattr(component, "id", None) is not None:
            ids.append(str(component.id))
        children = getattr(component, "children", None)
        for child in children if isinstance(children, (list, tuple)) else [children]:
            if child is not None and hasattr(child, "to_plotly_json"):
                walk(child)

    walk(app.layout)
    assert len(ids) == len(set(ids))
    assert len(app.callback_map) > 30
