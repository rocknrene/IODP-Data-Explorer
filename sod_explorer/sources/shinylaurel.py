"""Client for the DSDP Data Access application.

Legacy Deep Sea Drilling Project (DSDP) data are retrieved from the DSDP
Data Access application (https://shinylaurel.com/shiny/DSDP_data_access/),
an R Shiny application that serves the DSDP database by Leg, Site, and
data category. The application has no programmatic download interface, so
this client drives it with a headless Chromium browser (Selenium).

Procedure
---------
1. Open the application and select the "Data by Site" tab.
2. Set the Leg (``#var1``), wait for the Site list (``#var2``) to be
   populated by the server, and select the requested Site (or all Sites).
3. Set the data category (``#var_data``).
4. Wait until the server enables the download link
   (``#download_site_data``), then download the file.
5. Parse the file and restrict the rows to the requested Leg, Site, and
   Hole (the application does not filter by Hole).

The selection menus are selectize.js widgets, which replace the native
``<option>`` elements with a JavaScript-managed list; values are therefore
read and set through each widget's ``selectize`` API rather than through
Selenium's ``Select`` helper.

Operational notes
-----------------
* Requires Chromium and chromedriver. Their paths are read from the
  ``CHROME_BIN`` and ``CHROMEDRIVER_PATH`` environment variables.
* The client depends on the element identifiers listed above. A change to
  the application's layout will cause requests to fail with a timeout;
  the element identifiers were last verified in 2026.
* The application is accessed by this client with the permission of its
  maintainer (L. B. Childress, Gulf Coast Repository, Texas A&M University).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile

import pandas as pd

from ..provenance import utc_now
from .catalog import get_report, report_label
from .common import SourceError, restrict_to_request, validate_identifier

APP_URL = "https://shinylaurel.com/shiny/DSDP_data_access/"
TIMEOUT_S = 45
_SITE_PLACEHOLDER = "placeholder1"

_GET_OPTIONS_JS = """
var el = document.getElementById(arguments[0]);
if (el && el.selectize) { return Object.keys(el.selectize.options); }
return el ? Array.from(el.options).map(function(o){return o.value;}) : [];
"""

_SET_VALUE_JS = """
var el = document.getElementById(arguments[0]);
var val = arguments[1];
if (el && el.selectize) {
    el.selectize.setValue(val, false);
} else {
    el.value = Array.isArray(val) ? val[0] : val;
    el.dispatchEvent(new Event('change', {bubbles: true}));
}
"""


def read_download(path: str) -> pd.DataFrame:
    """Read a file downloaded from the application.

    Accepts a delimited text file or a ZIP archive containing one; the
    delimiter is inferred by the Python CSV sniffer.
    """
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith((".csv", ".txt", ".tsv"))]
            if not members:
                raise SourceError("The downloaded archive contains no CSV/TXT/TSV file")
            with archive.open(members[0]) as handle:
                return pd.read_csv(handle, sep=None, engine="python")
    return pd.read_csv(path, sep=None, engine="python")


def _download(category: str, leg: str, site: str, timeout: float) -> pd.DataFrame:
    """Drive the DSDP Data Access application in headless Chromium and return the downloaded table."""
    try:
        from selenium import webdriver
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except ImportError as exc:
        raise SourceError("Selenium is not installed; DSDP retrieval is unavailable") from exc

    download_dir = tempfile.mkdtemp(prefix="dsdp_dl_")
    options = Options()
    for argument in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage"):
        options.add_argument(argument)
    options.binary_location = os.environ.get("CHROME_BIN", "/usr/bin/chromium")
    options.add_experimental_option("prefs", {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
    })
    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")),
            options=options)
        # Headless Chromium requires downloads to be enabled explicitly.
        driver.execute_cdp_cmd("Page.setDownloadBehavior",
                               {"behavior": "allow", "downloadPath": download_dir})
        wait = WebDriverWait(driver, timeout)

        driver.get(APP_URL)
        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-value='bysite']"))).click()
        wait.until(EC.presence_of_element_located((By.ID, "var1")))
        driver.execute_script(_SET_VALUE_JS, "var1", leg)

        def sites_loaded(d):
            """True once the server has replaced the Site menu's placeholder with the Leg's Sites."""
            values = d.execute_script(_GET_OPTIONS_JS, "var2")
            return bool(values) and values[0] != _SITE_PLACEHOLDER
        wait.until(sites_loaded)
        sites = [site] if site else driver.execute_script(_GET_OPTIONS_JS, "var2")
        driver.execute_script(_SET_VALUE_JS, "var2", sites)
        driver.execute_script(_SET_VALUE_JS, "var_data", category)

        def download_enabled(d):
            """True once the server has enabled the download link for the current selection."""
            link = d.find_element(By.ID, "download_site_data")
            return "disabled" not in (link.get_attribute("class") or "")
        wait.until(download_enabled)
        driver.find_element(By.ID, "download_site_data").click()

        deadline = time.time() + timeout
        while time.time() < deadline:
            finished = [f for f in os.listdir(download_dir) if not f.endswith(".crdownload")]
            if finished:
                return read_download(os.path.join(download_dir, finished[0]))
            time.sleep(0.5)
        raise SourceError("The DSDP download did not complete in time; the application "
                          "may be slow or its layout may have changed")
    except WebDriverException as exc:
        raise SourceError(f"DSDP Data Access request failed: {exc.msg or exc}") from exc
    finally:
        if driver is not None:
            driver.quit()
        shutil.rmtree(download_dir, ignore_errors=True)


def fetch(report_key: str, leg: str, site: str = "", hole: str = "",
          timeout: float = TIMEOUT_S) -> tuple[pd.DataFrame, dict]:
    """Retrieve a DSDP data category for a Leg (and optionally Site and Hole).

    Returns
    -------
    df : pandas.DataFrame
        Rows restricted to the requested Leg, Site, and Hole.
    source : dict
        Provenance source description.

    Raises
    ------
    SourceError
        If the report type has no DSDP equivalent, the application cannot be
        driven, or no rows match the request.
    """
    report_type = get_report(report_key)
    category = report_type.dsdp_category if report_type else None
    if category is None:
        raise SourceError(f"The DSDP database has no category equivalent to {report_label(report_key)}")
    leg = validate_identifier(str(leg), "Leg")
    site = validate_identifier(site, "Site")
    hole = (hole or "").strip()

    df = _download(category, leg, site, timeout)
    if df is None or df.empty:
        raise SourceError(f"No {category} data returned for DSDP Leg {leg}")
    n_downloaded = len(df)
    df = restrict_to_request(df, leg, site, hole)
    if df.empty:
        raise SourceError(f"Downloaded {n_downloaded:,} rows, but none match Leg {leg}"
                          + (f" Site {site}" if site else "") + (f" Hole {hole}" if hole else ""))
    source = {
        "type": "dsdp_shinylaurel",
        "name": "DSDP Data Access application",
        "url": APP_URL,
        "query": {"report": report_key, "category": category, "leg": leg, "site": site, "hole": hole},
        "retrieved_utc": utc_now(),
        "rows_downloaded": n_downloaded,
        "citation_note": (f"Deep Sea Drilling Project data for Leg {leg}, retrieved through the "
                          f"DSDP Data Access application ({APP_URL}). Cite the Initial Reports of "
                          f"the Deep Sea Drilling Project volume for Leg {leg}."),
    }
    return df, source
