"""Tests for the data-source clients, using recorded or synthetic responses."""

import zipfile

import pandas as pd
import pytest

from sod_explorer.sources import catalog, common, lore, pangaea, routing, shinylaurel
from sod_explorer.sources.common import SourceError


class TestCommon:
    def test_restrict_to_request_normalizes_identifiers(self):
        df = pd.DataFrame({"Leg": [29.0, 29.0, 30.0], "Site": ["280", "281", "280"], "Hole": ["A", "a", "A"]})
        assert len(common.restrict_to_request(df, "29", "280", "")) == 1
        assert len(common.restrict_to_request(df, "29", "", "*")) == 2

    def test_validate_identifier_rejects_injection(self):
        assert common.validate_identifier(" U1480 ", "Site") == "U1480"
        with pytest.raises(SourceError):
            common.validate_identifier("1') or ('1", "Site")


class FakeLore:
    """Stand-in for LORE's three JSON services."""

    def __init__(self, fail_first=False):
        self.calls = 0
        self.fail_first = fail_first

    def __call__(self, session, path, params):
        self.calls += 1
        if self.fail_first:
            self.fail_first = False
            raise SourceError("temporary failure")
        if path.endswith("AWorkingSetGet-LORE"):
            return [101, 102]
        if path.endswith("HeaderDisplayGet-LORE"):
            return {"headers": ["Exp", "Site", "Hole", "Depth CSF-A (m)", "Bulk density (g/cm&sup3;)"]}
        return [["362", "U1480", "E", "0.5", "1.6"], ["362", "U1480", "F", "0.7", "1.7"]]


@pytest.fixture
def fake_lore(monkeypatch):
    lore._fetch_cached.cache_clear()
    fake = FakeLore()
    monkeypatch.setattr(lore, "_get_json", fake)
    monkeypatch.setattr(lore, "_session", lambda: None)
    yield fake
    lore._fetch_cached.cache_clear()


class TestLore:
    def test_fetch_filters_unescapes_and_records_source(self, fake_lore):
        df, source = lore.fetch("mad", "362", "U1480", "E")
        assert len(df) == 1 and "Bulk density (g/cm³)" in df.columns
        assert df["Depth CSF-A (m)"].dtype.kind == "f"
        assert source["type"] == "lore"
        assert source["query"]["lims_reports"] == [{"code": "MAD", "lore_report": "mad", "rows": 1}]
        assert "Proceedings" in source["citation_note"]

    def test_successes_are_cached(self, fake_lore):
        lore.fetch("mad", "362", "U1480", "E")
        calls = fake_lore.calls
        lore.fetch("mad", "362", "U1480", "E")
        assert fake_lore.calls == calls

    def test_failures_are_not_cached(self, monkeypatch):
        lore._fetch_cached.cache_clear()
        fake = FakeLore(fail_first=True)
        monkeypatch.setattr(lore, "_get_json", fake)
        monkeypatch.setattr(lore, "_session", lambda: None)
        with pytest.raises(SourceError):
            lore.fetch("mad", "362")
        df, _ = lore.fetch("mad", "362")
        assert len(df) == 2
        lore._fetch_cached.cache_clear()

    def test_unknown_report_key(self, fake_lore):
        with pytest.raises(SourceError, match="no report"):
            lore.fetch("not_a_report", "362")

    def test_candidate_names_are_probed_in_order(self, monkeypatch):
        """The first candidate that LORE's header service recognizes is used."""
        asked = []

        def header_service(session, path, params):
            asked.append(params["name"])
            if params["name"] == "gaselements":
                return {"headers": ["Exp", "Methane (ppmv)"]}
            return {"headers": []}

        monkeypatch.setattr(lore, "_get_json", header_service)
        report = catalog.REPORTS["gas_elements"].lore_reports[0]
        name, headers = lore.resolve_report_name(None, report)
        assert name == "gaselements" and asked == ["ge", "gaselements"]
        assert headers == ["Exp", "Methane (ppmv)"]

    def test_unrecognized_names_raise(self, monkeypatch):
        monkeypatch.setattr(lore, "_get_json", lambda *a: {"headers": []})
        with pytest.raises(SourceError, match="did not recognize"):
            lore.resolve_report_name(None, catalog.REPORTS["rgb"].lore_reports[0])

    def test_multi_report_type_is_combined_and_labeled(self, fake_lore):
        """Split-core P-wave velocity combines the caliper (PWC) and bayonet (PWB) reports."""
        df, source = lore.fetch("pwave_split", "362", "U1480")
        assert df["LIMS report"].value_counts().to_dict() == {"PWC": 2, "PWB": 2}
        assert [r["code"] for r in source["query"]["lims_reports"]] == ["PWC", "PWB"]

    def test_rows_to_frame_keeps_mixed_columns_as_text(self):
        df = lore.rows_to_frame([["1", "A"], ["2", "3"], ["", "4"]], ["n", "mixed"])
        assert df["n"].dtype.kind == "f" and df["mixed"].dtype.kind != "f"


class TestCatalog:
    def test_menu_follows_the_sod_data_types_table(self):
        """The sixteen generic names marked for inclusion, in three categories."""
        assert len(catalog.REPORT_TYPES) == 16
        assert catalog.CATEGORIES == ("Downhole", "Geochemistry", "Physical properties")
        headings = [o for o in catalog.menu_options() if o.get("disabled")]
        assert [h["label"] for h in headings] == ["DOWNHOLE", "GEOCHEMISTRY", "PHYSICAL PROPERTIES"]
        assert len(catalog.menu_options()) == 19

    @pytest.mark.parametrize("key, dsdp_category, dsdp_pangaea", [
        ("gra", "gamma ray attenuation", None), ("mad", "density and porosity", None),
        ("pwave_split", "sonic velocity", None), ("pwave_logger", "sonic velocity", None),
        ("shear_vane", "vane shear", None), ("shear_torvane", "vane shear", None),
        ("carbonates", "carbonate and carbon", None), ("interstitial_water", "interstitial water", None),
        ("thermal_conductivity", None, "thermal conductivity"), ("source_rock", None, "Rock-Eval pyrolysis"),
        ("penetrometer", None, "penetrometer"), ("ngr", None, None), ("rgb", None, None),
    ])
    def test_dsdp_sources(self, key, dsdp_category, dsdp_pangaea):
        report = catalog.REPORTS[key]
        assert (report.dsdp_category, report.dsdp_pangaea_term) == (dsdp_category, dsdp_pangaea)

    def test_every_type_has_lims_and_odp_names(self):
        for report in catalog.REPORT_TYPES:
            assert report.lore_reports and report.odp_report and report.pangaea_term
            for lims in report.lore_reports:
                assert lims.candidates[0] == lims.candidates[0].lower()

    def test_verified_names_are_first_candidates(self):
        first = {lims.candidates[0] for r in catalog.REPORT_TYPES for lims in r.lore_reports}
        assert catalog.VERIFIED_LORE_NAMES <= first


class FakeResponse:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self.content = text.encode()
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._payload


class TestPangaea:
    def test_parse_tab_download(self, fixture_bytes):
        df, header = pangaea.parse_tab_download(fixture_bytes("pangaea.tab").decode())
        assert list(df.columns) == ["Event label", "Depth [m]", "Dens [g/cm**3]"] and len(df) == 3
        assert header["License"].startswith("Creative Commons")
        assert "10.1594/PANGAEA.123456" in header["Citation"]

    def test_fetch_dataset_records_citation(self, monkeypatch, fixture_bytes):
        text = fixture_bytes("pangaea.tab").decode()
        monkeypatch.setattr(pangaea.requests, "get", lambda *a, **k: FakeResponse(text))
        df, source = pangaea.fetch_dataset("123456")
        assert source["doi"] == "10.1594/PANGAEA.123456"
        assert source["license"].endswith("(CC-BY-3.0)") and source["citation"]

    def test_fetch_dataset_rejects_bad_identifier(self):
        with pytest.raises(SourceError):
            pangaea.fetch_dataset("../etc")

    def test_screen_results(self):
        results = [{"value": "1", "label": "1: Vane shear strength of Leg 30 Hole 290"},
                   {"value": "2", "label": "2: Vane shear strength of Hole 29-280A"}]
        ranked = pangaea.screen_results(results, "29", "shear strength")
        assert [r["value"] for r in ranked] == ["2", "1"]
        assert pangaea.screen_results(results, "29", "diatoms") == []

    @pytest.mark.parametrize("title, match", [
        ("Data of Leg 29", True), ("DSDP Legs 29 and 30", True), ("Hole 29-280A", True),
        ("Expedition 405 physical properties", True), ("Leg 290", False), ("Leg 129", False),
    ])
    def test_leg_pattern(self, title, match):
        number = "405" if "405" in title else "29"
        assert bool(pangaea.leg_pattern(number).search(title)) is match

    def test_keyword_tolerates_plural(self):
        assert pangaea.keyword_matches("Diatom stratigraphy", "diatoms")


class TestShinylaurel:
    def test_read_download_csv_and_zip(self, tmp_path):
        csv_path = tmp_path / "d.csv"
        csv_path.write_text("Leg,Site,Hole,Density\n29,280,A,1.5\n")
        assert len(shinylaurel.read_download(str(csv_path))) == 1
        zip_path = tmp_path / "d.zip"
        with zipfile.ZipFile(zip_path, "w") as archive:
            archive.writestr("d.txt", "Leg\tSite\n29\t280\n")
        assert list(shinylaurel.read_download(str(zip_path)).columns) == ["Leg", "Site"]

    def test_report_without_dsdp_equivalent(self):
        with pytest.raises(SourceError, match="no category"):
            shinylaurel.fetch("ngr", "29")
        with pytest.raises(SourceError, match="no category"):
            shinylaurel.fetch("thermal_conductivity", "29")

    def test_fetch_filters_by_hole(self, monkeypatch):
        frame = pd.DataFrame({"Leg": [29, 29], "Site": [280, 280], "Hole": ["A", "B"], "x": [1, 2]})
        monkeypatch.setattr(shinylaurel, "_download", lambda *a: frame)
        df, source = shinylaurel.fetch("gra", "29", "280", "A")
        assert df["x"].tolist() == [1]
        assert source["rows_downloaded"] == 2 and source["query"]["category"] == "gamma ray attenuation"


class TestRouting:
    @pytest.mark.parametrize("expedition, strategy", [
        ("29", "dsdp"), ("150", "jr"), ("362", "jr"), ("343", "chikyu"), ("347", "msp"),
        ("405", "chikyu"), ("389", "msp"), ("999", "iodp_unknown_platform"), ("unknown", "unknown"),
    ])
    def test_resolve_route(self, expedition, strategy):
        assert routing.resolve_route(expedition).strategy == strategy

    def test_jr_uses_lore(self, monkeypatch):
        frame = pd.DataFrame({"x": [1]})
        monkeypatch.setattr(routing.lore, "fetch", lambda *a: (frame, {"type": "lore"}))
        outcome = routing.fetch("mad", "362")
        assert outcome.df is frame and "LORE" in outcome.message

    def test_pre_lims_leg_explains_archive(self, monkeypatch):
        def fail(*args):
            raise SourceError("LORE has no MAD data for Exp 150")
        monkeypatch.setattr(routing.lore, "fetch", fail)
        outcome = routing.fetch("mad", "150")
        assert outcome.df is None and "predates the LIMS" in outcome.message

    def test_unlettered_hole_not_sent_to_lore(self, monkeypatch):
        seen = {}
        def capture(report, exp, site, hole):
            seen["hole"] = hole
            return pd.DataFrame({"x": [1]}), {"type": "lore"}
        monkeypatch.setattr(routing.lore, "fetch", capture)
        routing.fetch("mad", "362", "U1480", "*")
        assert seen["hole"] == ""

    def test_pangaea_single_candidate_is_loaded(self, monkeypatch):
        monkeypatch.setattr(routing.pangaea, "find_datasets", lambda *a, **k: [{"value": "9", "label": "9: x"}])
        monkeypatch.setattr(routing.pangaea, "fetch_dataset",
                            lambda pid: (pd.DataFrame({"x": [1]}), {"type": "pangaea"}))
        assert routing.fetch("gra", "347").df is not None

    def test_pangaea_several_candidates_are_offered(self, monkeypatch):
        candidates = [{"value": "1", "label": "a"}, {"value": "2", "label": "b"}]
        monkeypatch.setattr(routing.pangaea, "find_datasets", lambda *a, **k: candidates)
        outcome = routing.fetch("gra", "347")
        assert outcome.df is None and outcome.candidates == candidates

    def test_chikyu_without_match_points_to_jcores(self, monkeypatch):
        monkeypatch.setattr(routing.pangaea, "find_datasets", lambda *a, **k: [])
        assert "J-CORES" in routing.fetch("gra", "343").message

    def test_dsdp_type_on_pangaea_is_searched_by_leg(self, monkeypatch):
        seen = {}

        def capture(expedition, keyword, platform_term, unit):
            seen.update(expedition=expedition, keyword=keyword, platform=platform_term, unit=unit)
            return []
        monkeypatch.setattr(routing.pangaea, "find_datasets", capture)
        outcome = routing.fetch("thermal_conductivity", "29")
        assert seen == {"expedition": "29", "keyword": "thermal conductivity", "platform": "DSDP", "unit": "Leg"}
        assert "Leg 29" in outcome.message

    def test_type_not_measured_by_dsdp(self):
        assert "no DSDP equivalent" in routing.fetch("ngr", "29").message

    def test_dsdp_type_in_data_access_application(self, monkeypatch):
        frame = pd.DataFrame({"x": [1]})
        monkeypatch.setattr(routing.shinylaurel, "fetch",
                            lambda *a: (frame, {"query": {"category": "sonic velocity"}}))
        assert "sonic velocity" in routing.fetch("pwave_logger", "29").message


def test_user_agent_identifies_software():
    assert "SOD-Explorer" in common.USER_AGENT
    assert "Mozilla" not in common.USER_AGENT

