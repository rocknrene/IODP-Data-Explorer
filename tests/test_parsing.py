"""Tests for file readers."""

import io
import zipfile

import pandas as pd
import pytest

from sod_explorer import parsing
from sod_explorer.parsing import ParseError


def test_lims_csv_with_preamble(fixture_bytes):
    df, meta = parsing.parse_file(fixture_bytes("lims_mad.csv"), "lims_mad.csv")
    assert meta["header_row"] == 4
    assert meta["preamble"] == {"expedition": "362", "site": "U1480", "hole": "E"}
    assert len(df) == 5 and df["Depth CSF-A (m)"].dtype.kind == "f"
    assert meta["format"] == "CSV" and len(meta["sha256"]) == 64


def test_semicolon_delimited_and_bom():
    raw = "﻿Depth CSF-A (m);Porosity (%)\n1.0;50\n2.0;51\n".encode("utf-8")
    df, meta = parsing.parse_file(raw, "x.csv")
    assert list(df.columns) == ["Depth CSF-A (m)", "Porosity (%)"]
    assert meta["delimiter"] == ";"


def test_utf16_requires_bom():
    raw = "Depth CSF-A (m)\tNGR (cps)\n1.0\t20\n".encode("utf-16")
    df, meta = parsing.parse_file(raw, "x.tsv")
    assert meta["encoding"] == "utf-16" and len(df) == 1


def test_cp1252_fallback():
    raw = "Depth CSF-A (m),Density (g/cm³)\n1.0,1.5\n".encode("cp1252")
    df, meta = parsing.parse_file(raw, "x.csv")
    assert meta["encoding"] == "cp1252" and "Density (g/cm³)" in df.columns


def test_malformed_rows_raise_instead_of_being_dropped():
    raw = b"Depth CSF-A (m),A\n1,2\n3,4,5,6\n"
    with pytest.raises(ParseError):
        parsing.parse_file(raw, "bad.csv")


def test_excel_header_detection():
    buffer = io.BytesIO()
    frame = pd.DataFrame([["Expedition 405 report", None], [None, None],
                          ["Depth CSF-A (m)", "Porosity (%)"], [1.0, 50], [2.0, 52]])
    frame.to_excel(buffer, header=False, index=False)
    df, meta = parsing.parse_file(buffer.getvalue(), "x.xlsx")
    assert meta["header_row"] == 2
    assert df["Depth CSF-A (m)"].tolist() == [1.0, 2.0]
    assert pd.api.types.is_numeric_dtype(df["Porosity (%)"])


def test_zip_prefers_bulk_member():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.csv", "a,b\n1,2\n")
        archive.writestr("export/bulk-mad.csv", "Depth CSF-A (m),Core,Sample\n1.0,1,x\n")
        archive.writestr("__MACOSX/._bulk-mad.csv", "junk")
    df, meta = parsing.parse_file(buffer.getvalue(), "export.zip")
    assert meta["zip_member"] == "export/bulk-mad.csv"
    assert meta["zip_other_members"] == ["notes.csv"]


def test_zip_without_tables():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("image.jpg", b"\x00")
    with pytest.raises(ParseError, match="No CSV/TSV"):
        parsing.parse_file(buffer.getvalue(), "x.zip")


def test_las(fixture_bytes):
    df, meta = parsing.parse_file(fixture_bytes("well.las"), "well.las")
    assert meta["index_unit"] == "M" and meta["well"] == "U1480E"
    assert df["GR"].isna().sum() == 1


def test_unsupported_extension():
    with pytest.raises(ParseError, match="Unsupported"):
        parsing.parse_file(b"x", "x.pdf")


def test_decode_upload():
    assert parsing.decode_upload("data:text/csv;base64,YSxiCg==") == b"a,b\n"
    with pytest.raises(ParseError):
        parsing.decode_upload("not-a-data-url")


@pytest.mark.parametrize("line, expected", [
    ("Expedition: 405", {"expedition": "405"}),
    ('"Site","U1480"', {"site": "U1480"}),
    ("Site: 1172", {"site": "1172"}),
    ("Hole = E", {"hole": "E"}),
    ("Leg: 29", {"expedition": "29"}),
])
def test_preamble_metadata(line, expected):
    assert parsing.extract_preamble_metadata([line]) == expected


class TestLithology:
    def test_resolves_columns_and_drops_invalid_intervals(self):
        df = pd.DataFrame({"Top depth CSF-A (m)": [0, 5, 9, None],
                           "Bottom depth CSF-A (m)": [5, 9, 8, 10],
                           "Lithology": ["clay", "sand", "bad", "x"]})
        table, info = parsing.resolve_lithology_columns(df)
        assert table["lithology"].tolist() == ["clay", "sand"]
        assert info["depth_scale"] == "CSF-A" and info["intervals_removed"] == 2

    def test_missing_columns(self):
        with pytest.raises(ParseError, match="bottom_depth"):
            parsing.resolve_lithology_columns(pd.DataFrame({"Top": [0], "Lithology": ["x"]}))


def test_summarize_site(fixture_bytes, two_hole_tables):
    df, meta = parsing.parse_file(fixture_bytes("lims_mad.csv"), "lims_mad.csv")
    info = parsing.summarize_site(df, meta)
    assert info["expedition"] == "362" and info["site_hole"] == "U1480E"
    assert info["depth_scale"] == "CSF-A" and info["depth_max"] == 7.1

    a, _ = two_hole_tables
    assert parsing.summarize_site(a, {"rows": 6})["site_hole"] == "2 holes"


class TestSampleLabels:
    def test_pangaea_style_export(self, fixture_bytes):
        """A PANGAEA table (ODP Leg 177, Site 1090) exported from an earlier version of the app."""
        df, meta = parsing.parse_file(fixture_bytes("pangaea_style_export.csv"), "export.csv")
        assert meta["identifiers_parsed_from"].startswith("Sample label")
        assert df.loc[0, ["Exp", "Site", "Hole", "Core", "Type"]].tolist() == ["177", "1090", "E", 8, "H"]
        info = parsing.summarize_site(df, meta)
        assert info["site_hole"] == "1090E" and info["depth_column"] == "Depth sed [m]"
        assert info["depth_scale"] == "mbsf"

    @pytest.mark.parametrize("label, expected", [
        ("29-280A", {"Exp": "29", "Site": "280", "Hole": "A"}),
        ("29-277-1-4", {"Exp": "29", "Site": "277", "Hole": "*", "Core": 1}),
        ("362-U1480E-1H-1,50", {"Exp": "362", "Site": "U1480", "Hole": "E", "Core": 1, "Type": "H"}),
        ("C0019E-1R-1", {"Site": "C0019", "Hole": "E", "Core": 1, "Type": "R"}),
        ("280A-8H-3", {"Site": "280", "Hole": "A", "Core": 8, "Type": "H"}),
    ])
    def test_label_forms(self, label, expected):
        df, column = parsing.identifiers_from_sample_labels(pd.DataFrame({"Sample label": [label], "x": [1.0]}))
        assert column == "Sample label"
        assert {k: df.loc[0, k] for k in expected} == expected

    def test_labels_for_unknown_sites_are_not_parsed(self):
        frame = pd.DataFrame({"Sample label": ["12-9999A-1H-1", "batch-7"], "x": [1.0, 2.0]})
        df, column = parsing.identifiers_from_sample_labels(frame)
        assert column is None and list(df.columns) == ["Sample label", "x"]

    def test_existing_site_and_hole_columns_are_kept(self):
        frame = pd.DataFrame({"Site": ["U1480"], "Hole": ["E"], "Sample label": ["362-U1480F-1H-1"]})
        df, column = parsing.identifiers_from_sample_labels(frame)
        assert column is None and df["Hole"].tolist() == ["E"]
