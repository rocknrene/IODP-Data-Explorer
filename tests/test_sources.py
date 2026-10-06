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
        report = catalog.LoreReport("GE", ("ge", "gaselements", "gas"))
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

    def test_restricted_dataset_is_explained(self, monkeypatch):
        """Datasets under moratorium answer HTTP 401; the message says so."""
        import requests

        class Restricted(FakeResponse):
            def raise_for_status(self):
                raise requests.HTTPError("401", response=self)

        monkeypatch.setattr(pangaea.requests, "get", lambda *a, **k: Restricted(status=401))
        with pytest.raises(SourceError, match="access-restricted"):
            pangaea.fetch_dataset("997314")

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

    def test_odp_leg_uses_ncei(self, monkeypatch):
        frame = pd.DataFrame({"x": [1]})
        monkeypatch.setattr(routing.ncei, "fetch", lambda *a: (frame, {"type": "ncei"}))
        monkeypatch.setattr(routing.lore, "fetch", lambda *a: pytest.fail("LORE must not be queried"))
        outcome = routing.fetch("mad", "204", "1244", "C")
        assert outcome.df is frame and outcome.message.startswith("NCEI")

    def test_odp_leg_falls_back_to_lore_then_explains(self, monkeypatch):
        def no_ncei(*args):
            raise SourceError("The NCEI archive has no file")
        def no_lore(*args):
            raise SourceError("LORE has no MAD data for Exp 150")
        monkeypatch.setattr(routing.ncei, "fetch", no_ncei)
        monkeypatch.setattr(routing.lore, "fetch", no_lore)
        outcome = routing.fetch("mad", "150")
        assert outcome.df is None and "NCEI" in outcome.message and "predates the LIMS" in outcome.message
        frame = pd.DataFrame({"x": [1]})
        monkeypatch.setattr(routing.lore, "fetch", lambda *a: (frame, {"type": "lore"}))
        assert routing.fetch("mad", "150").df is frame

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



class TestNcei:
    @pytest.fixture(autouse=True)
    def _no_pause(self, monkeypatch):
        from sod_explorer.sources import ncei
        monkeypatch.setattr(ncei, "PAUSE_S", 0)
        ncei._fetch_file.cache_clear()

    def _serve(self, monkeypatch, files):
        """Replace HTTP with a dict of URL -> text; other URLs answer 404."""
        from types import SimpleNamespace

        from sod_explorer.sources import ncei
        requested = []
        def get(url, **kwargs):
            requested.append(url)
            if url in files:
                return SimpleNamespace(status_code=200, text=files[url], encoding="utf-8")
            return SimpleNamespace(status_code=404, text="<html>Not Found</html>", encoding="utf-8")
        monkeypatch.setattr(ncei.requests, "get", get)
        return requested

    def test_file_url(self):
        from sod_explorer.sources import ncei
        assert ncei.file_url("carb", "204", "1244", "C").endswith("/204/1244c/carb_204_1244c.txt")

    def test_parse_carbonate_file(self, fixture_bytes):
        from sod_explorer.columns import depth_scale, find_depth_column
        from sod_explorer.sources import ncei
        df = ncei.parse_data_file(fixture_bytes("ncei_carb_204_1244c.txt").decode())
        assert len(df) == 3 and df.columns[-1] == "Hydrogen (mg HC/g)"
        assert df["Hole"].tolist() == ["C"] * 3 and df["Site"].tolist() == ["1244"] * 3
        assert pd.isna(df.loc[2, "Total_Carbon (wt %)"]) and df.loc[0, "Calcium_Carbonate (wt %)"] == 4.25
        depth = find_depth_column(df)
        assert depth == "Depth (mbsf)" and depth_scale(depth) == "mbsf"

    def test_html_is_not_a_data_file(self):
        from sod_explorer.sources import ncei
        with pytest.raises(SourceError):
            ncei.parse_data_file("<html><body>Not Found</body></html>")

    def test_fetch_one_hole(self, monkeypatch, fixture_bytes):
        from sod_explorer.sources import ncei
        url = ncei.file_url("mad", "204", "1244", "C")
        requested = self._serve(monkeypatch, {url: fixture_bytes("ncei_mad_204_1244c.txt").decode()})
        df, source = ncei.fetch("mad", "204", "1244", "c")
        assert requested == [url] and len(df) == 7 and "Porosity (%)" in df.columns
        assert source["type"] == "ncei" and source["doi"] == "10.7289/V5W37T8C"
        assert source["query"]["files"] == [{"url": url, "rows": 7}]
        ncei.fetch("mad", "204", "1244", "C")
        assert requested == [url], "a repeated request must be served from the cache"

    def test_fetch_site_requests_each_hole_of_reference_table(self, monkeypatch, fixture_bytes):
        from sod_explorer.sources import ncei
        monkeypatch.setattr(ncei, "holes_for_site", lambda leg, site: ["A", "C"])
        url = ncei.file_url("carb", "204", "1244", "C")
        requested = self._serve(monkeypatch, {url: fixture_bytes("ncei_carb_204_1244c.txt").decode()})
        df, source = ncei.fetch("carbonates", "204", "1244")
        assert len(requested) == 2 and len(df) == 3

    def test_missing_file_and_unmeasured_type(self, monkeypatch):
        from sod_explorer.sources import ncei
        self._serve(monkeypatch, {})
        with pytest.raises(SourceError, match="no Carbonates file"):
            ncei.fetch("carbonates", "204", "1244", "C")
        with pytest.raises(SourceError, match="not measured during ODP"):
            ncei.fetch("rgb", "204", "1244", "C")


class TestCarbonateAssembly:
    def _raw(self):
        base = {"Exp": 362, "Site": "U1480", "Hole": "E", "Core": 1, "Type": "H", "Sect": 1, "A/W": "W",
                "Sample comments": None, "hydrogen_percent": None, "nitrogen_percent": None,
                "sulfur_percent": None}
        rows = [
            {**base, "Text ID": "CYL1", "Depth CSF-A (m)": 1.0, "Analysis": "COUL", "carbon_percent": 0.6},
            {**base, "Text ID": "CYL1", "Depth CSF-A (m)": 1.0, "Analysis": "CHNS", "carbon_percent": 1.5,
             "nitrogen_percent": 0.1},
            {**base, "Text ID": "CYL2", "Depth CSF-A (m)": 5.0, "Analysis": "COUL", "carbon_percent": 0.2},
            {**base, "Text ID": "CYL2", "Depth CSF-A (m)": 5.0, "Analysis": "COUL", "carbon_percent": 0.4},
            {**base, "Text ID": "CYL3", "Depth CSF-A (m)": 3.0, "Analysis": "CHNS", "carbon_percent": 2.0},
        ]
        return pd.DataFrame(rows)

    def test_one_row_per_sample_with_computed_columns(self):
        table = lore.assemble_carbonates(self._raw())
        assert table["Text ID"].tolist() == ["CYL1", "CYL3", "CYL2", "CYL2"]      # sorted by depth
        first = table.iloc[0]
        assert first["Inorganic carbon (wt%)"] == 0.6 and first["Total carbon (wt%)"] == 1.5
        assert first["Calcium carbonate (wt%)"] == pytest.approx(5.0, abs=0.001)
        assert first["Organic carbon (wt%) by difference (CHNS-COUL)"] == pytest.approx(0.9)
        assert first["Nitrogen (wt%)"] == 0.1
        assert pd.isna(table.iloc[1]["Inorganic carbon (wt%)"]) and table.iloc[1]["Total carbon (wt%)"] == 2.0

    def test_replicates_are_kept(self):
        table = lore.assemble_carbonates(self._raw())
        replicates = table[table["Text ID"] == "CYL2"]
        assert replicates["Replicate"].tolist() == [1, 2]
        assert replicates["Inorganic carbon (wt%)"].tolist() == [0.2, 0.4]

    def test_unexpected_table_is_returned_unchanged(self):
        frame = pd.DataFrame({"x": [1]})
        assert lore.assemble_carbonates(frame) is frame


class TestCompositeAssembly:
    DEFINITION = {
        "analysiscol": "9", "rowseperatorcol": ["4"],
        "formatdefinition": {"columns": [
            {"header": {"text": "Exp"}, "col": "1"}, {"header": {"text": "Site"}, "col": "2"},
            {"header": {"text": "Hole"}, "col": "3"}, {"header": {"text": "Top depth CSF-A (m)"}, "col": "4"},
            {"template": [
                {"analysis": "ICPAES",
                 "header": [{"col": "5"}, {"text": "</br>"}, {"col": "7"}, {"text": "nm </br>ICPAES"}],
                 "match": ["5", "7"], "source": "6"},
                {"analysis": "ALKALINITY", "fixedvalue": [
                    {"source": "8", "header": [{"text": "Alkalinity (mM) ALKALINITY"}]}]},
            ]},
        ]},
    }
    COLUMNS = ["sample_number", "Exp", "Site", "Hole", "Top depth CSF-A (m)", "calibrated_name",
               "concentration", "wavelength", "alkalinity", "Analysis"]

    def _raw(self):
        rows = [
            [1, 362, "U1480", "E", 7.4, "Ba (uM)", 2.5, 455.4, None, "ICPAES"],
            [2, 362, "U1480", "E", 2.9, "Ba (uM)", 1.5, 455.4, None, "ICPAES"],
            [3, 362, "U1480", "E", 2.9, "Ca (mM)", 10.2, 315.9, None, "ICPAES"],
            [4, 362, "U1480", "E", 2.9, None, None, None, 3.1, "ALKALINITY"],
            [5, 362, "U1480", "E", 2.9, None, None, None, 3.3, "ALKALINITY"],
        ]
        return pd.DataFrame(rows, columns=self.COLUMNS)

    def test_results_become_columns_one_row_per_sample(self):
        table = lore.assemble_composite(self._raw(), self.DEFINITION)
        assert list(table.columns) == ["Exp", "Site", "Hole", "Top depth CSF-A (m)", "Replicate",
                                       "Ba (uM) 455.4 nm ICPAES", "Ca (mM) 315.9 nm ICPAES",
                                       "Alkalinity (mM) ALKALINITY"]
        assert table["Top depth CSF-A (m)"].tolist() == [2.9, 2.9, 7.4]
        assert table["Ba (uM) 455.4 nm ICPAES"].tolist()[0] == 1.5 and table.iloc[2, 5] == 2.5
        assert table["Alkalinity (mM) ALKALINITY"].tolist()[:2] == [3.1, 3.3]      # replicates kept
        assert table["Replicate"].tolist() == [1, 2, 1]

    def test_table_that_does_not_fit_the_definition_is_returned_unchanged(self):
        frame = pd.DataFrame({"x": [1]})
        assert lore.assemble_composite(frame, self.DEFINITION) is frame
        assert lore.assemble_composite(self._raw(), {"formatdefinition": {}}).equals(self._raw())
