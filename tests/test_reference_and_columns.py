"""Tests for the reference table and column classification."""

import pandas as pd
import pytest

from sod_explorer import columns, reference


class TestReference:
    def test_table_has_expected_columns_and_programs(self):
        assert list(reference.REFERENCE.columns) == ["Exp", "Site", "Hole", "program", "vessel"]
        assert set(reference.REFERENCE["program"]) == {"DSDP", "ODP", "IODP"}

    def test_natural_sort_orders_numbers_then_suffixes(self):
        values = ["10", "2", "343T", "343", "1"]
        assert sorted(values, key=reference.natural_sort_key) == ["1", "2", "10", "343", "343T"]

    def test_sites_and_holes(self):
        assert reference.sites_for_expedition("343") == ["C0019"]
        assert "E" in reference.holes_for_site("362", "U1480")
        assert reference.sites_for_expedition(None) == []

    def test_unlettered_hole_label(self):
        assert reference.hole_display_label("*") == "*"
        assert reference.hole_display_label("A") == "A"

    @pytest.mark.parametrize("number, program", [
        ("1", "DSDP"), ("96", "DSDP"), ("97", None), ("100", "ODP"), ("210", "ODP"),
        ("250", None), ("301", "IODP"), ("405", "IODP"), ("343T", None), ("", None),
    ])
    def test_program_from_number(self, number, program):
        assert reference.program_from_number(number) == program

    def test_platforms_match_sod_legacy_data_access_guide(self):
        """Chikyu and MSP expeditions as listed in the SOD Legacy Data Access Quick
        Start Guide (v1.0, Sept 2026); every other IODP expedition is JOIDES Resolution."""
        chikyu = {"314", "315", "316", "319", "322", "326", "331", "332", "333", "337",
                  "338", "343", "348", "358", "365", "370", "380", "405"}
        msp = {"302", "310", "313", "325", "347", "357", "364", "381", "386", "389"}
        table = reference.REFERENCE
        numeric = table["Exp"].str.extract(r"^(\d+)")[0]
        iodp = table[table["program"] == "IODP"]
        expected = numeric[iodp.index].map(lambda n: "Chikyu" if n in chikyu else "MSP" if n in msp
                                           else "JOIDES Resolution")
        assert (iodp["vessel"] == expected).all()
        assert chikyu <= set(numeric[table["vessel"] == "Chikyu"])
        assert msp <= set(numeric[table["vessel"] == "MSP"])

    def test_leg_ranges(self):
        numbers = reference.REFERENCE["Exp"].str.extract(r"^(\d+)")[0].astype(int)
        assert numbers[reference.REFERENCE["program"] == "DSDP"].between(1, 96).all()
        assert numbers[reference.REFERENCE["program"] == "ODP"].between(100, 210).all()
        assert (reference.REFERENCE.loc[reference.REFERENCE["program"] == "DSDP", "vessel"]
                == "Glomar Challenger").all()

    def test_expedition_without_holes_is_selectable(self):
        assert "405" in reference.ALL_EXPEDITIONS
        assert reference.platform_for_expedition("405") == "Chikyu"
        assert reference.sites_for_expedition("405") == []

    def test_platform_lookup(self):
        assert reference.platform_for_expedition("1") == "Glomar Challenger"
        assert reference.platform_for_expedition("343") == "Chikyu"
        assert reference.platform_for_expedition("347") == "MSP"
        assert reference.platform_for_expedition("no-such-leg") is None


class TestDepthScales:
    @pytest.mark.parametrize("header, scale", [
        ("Depth CSF-A (m)", "CSF-A"), ("Depth CSF-B (m)", "CSF-B"),
        ("Depth CCSF-A (m)", "CCSF-A"), ("Depth CCSF-D (m)", "CCSF-D"),
        ("Depth [m] CCSF", "CCSF"), ("depth_mbsf", "mbsf"), ("Depth (mcd)", "mcd"),
        ("Depth WMSF (m)", "WMSF"), ("Depth CSF-A (m)_A", "CSF-A"), ("Depth [m]", None),
    ])
    def test_depth_scale_from_header(self, header, scale):
        assert columns.depth_scale(header) == scale

    def test_legacy_scales_map_to_iodp_equivalents(self):
        assert columns.canonical_scale("mbsf") == "CSF-A"
        assert columns.canonical_scale("mcd") == "CCSF-A"
        assert columns.canonical_scale("CSF-B") == "CSF-B"

    def test_composite_scales(self):
        assert columns.is_composite_scale("CCSF-A")
        assert columns.is_composite_scale("mcd")
        assert not columns.is_composite_scale("CSF-A")

    @pytest.mark.parametrize("header, factor, assumed", [
        ("Depth CSF-A (m)", 1.0, False), ("Offset (cm)", 0.01, False),
        ("depth_cm", 0.01, False), ("depth_mbsf", 1.0, False), ("Depth", 1.0, True),
    ])
    def test_depth_unit_factor(self, header, factor, assumed):
        assert columns.depth_unit_factor(header) == (factor, assumed)


class TestColumnClassification:
    @pytest.fixture
    def lims(self):
        return pd.DataFrame({
            "Exp": [362], "Site": ["U1480"], "Hole": ["E"], "Core": [1], "Sect": [1],
            "Offset (cm)": [50.0], "Top offset on section (cm)": [49.0],
            "Depth CSF-B (m)": [0.5], "Depth CSF-A (m)": [0.5],
            "Bulk density (g/cm3)": [1.6], "Comments": ["note"], "Timestamp (UTC)": ["x"],
        })

    def test_csf_a_preferred_over_csf_b_and_offsets_ignored(self, lims):
        assert columns.find_depth_column(lims) == "Depth CSF-A (m)"

    def test_no_depth_column_returns_none(self):
        assert columns.find_depth_column(pd.DataFrame({"a": [1.0], "b": [2.0]})) is None

    def test_top_depth_preferred_over_bottom(self):
        df = pd.DataFrame({"Bottom depth CSF-A (m)": [2.0], "Top depth CSF-A (m)": [1.0]})
        assert columns.find_depth_column(df) == "Top depth CSF-A (m)"

    def test_measurement_columns_exclude_identifiers_and_depths(self, lims):
        assert columns.measurement_columns(lims) == ["Bulk density (g/cm3)"]

    @pytest.mark.parametrize("header, kind", [
        ("Exp", "expedition"), ("Leg", "expedition"), ("Exp_A", "expedition"),
        ("Site", "site"), ("Hole_B", "hole"), ("Core", "core"), ("Sect", "section"),
        ("Offset (cm)", "other"), ("Text ID", "other"), ("depth_offset_m", "other"),
        ("Bulk density (g/cm3)", None), ("Legend", None),
    ])
    def test_identifier_kind(self, header, kind):
        assert columns.identifier_kind(header) == kind

    def test_normalize_identifier(self):
        assert columns.normalize_identifier(29.0) == "29"
        assert columns.normalize_identifier(" U1480 ") == "u1480"

    def test_group_key_optional_expedition(self, lims):
        assert columns.group_key(lims).iloc[0] == "362|u1480|e"
        assert columns.group_key(lims, include_expedition=False).iloc[0] == "u1480|e"
        assert columns.group_key(lims, level="site").iloc[0] == "362|u1480"
        assert columns.group_key(pd.DataFrame({"x": [1]})) is None


def test_dsdp_sample_depth_preferred_over_interval_and_core_depths():
    df = pd.DataFrame({"leg": [29], "site": [277], "hole": ["*"], "core": [1], "section": [4],
                       "top interval depth (cm)": [39.0], "bottom interval depth (cm)": [39.0],
                       "top of core depth (m)": [0.0], "sample depth (m)": [4.89],
                       "porosity": [63.53]})
    assert columns.find_depth_column(df) == "sample depth (m)"
