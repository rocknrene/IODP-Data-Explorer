"""Find the LORE report names that are still unknown.

Development aid, run by the ``live checks`` workflow. It (1) probes a wide
list of candidate names against LORE's header service and (2) downloads the
LORE page and the scripts it loads and prints every line that mentions a
report name, so that names not guessed here can be read from LORE's own
menu definition. Output goes to standard output.
"""

from __future__ import annotations

import re
import sys
import time
from urllib.parse import urljoin

import requests

HOST = "https://web.iodp.tamu.edu"
PAGE = f"{HOST}/LORE/"
HEADERS = {"User-Agent": "SOD-Explorer/2.0 (report-name discovery; github.com/rocknrene/IODP-Data-Explorer)",
           "Referer": PAGE, "X-Requested-With": "XMLHttpRequest"}

CANDIDATES = {
    "CARB": ["carbonates", "carbonate", "coulometer", "coul", "chns", "carbon", "caco3", "ic",
             "inorganiccarbon", "carbchns", "carb_chns", "carbreport", "elemental", "carbsummary", "coulchns",
             "carbs", "chnscoul", "toc", "tc", "carbonatereport", "CARB", "Carbonates"],
    "GE": ["gaselements", "gaselement", "gc3", "gcfid", "nga", "ngafid", "gassafety", "gasmonitoring",
           "headspace", "hs", "vac", "gas_elements", "gasreport", "gassummary", "ge_report", "gasanalysis"],
    "IW": ["iwreport", "interstitialwater", "porewater", "iwsummary", "iwchem", "icp", "icpaes",
           "alkalinity", "alk", "iwmain", "water", "iw_report", "iws", "iwall", "iwdata", "iwcombined",
           "spec", "titration", "salinity"],
    "SRA": ["sourcerock", "sourcerockanalysis", "rockeval", "pyrolysis", "srasummary", "sra_report",
            "srareport", "sraanalysis"],
    "PEN": ["penetrometer", "penstrength", "pp", "ppen", "pocketpen", "compstrength", "strength",
            "pen_report", "penreport", "handpen", "pentest", "penet", "PEN", "compressionalstrength"],
}
ENDPOINT_GUESSES = [
    "/reference/ReportsGet-LORE", "/reference/ReportListGet-LORE", "/reference/MenuGet-LORE",
    "/reference/AccordionGet-LORE", "/reference/ReportMenuGet-LORE", "/reference/ReportGet-LORE",
    "/LORE/reports.json", "/LORE/menu.json", "/LORE/config.json",
]
KEYWORDS = re.compile(r"carb|interstitial|\biw\b|gas ?elements|source ?rock|\bsra\b|penetrometer|\bpen\b"
                      r"|HeaderDisplayGet|reportName|report_name|AWorkingSetGet", re.IGNORECASE)


def get(session: requests.Session, url: str, **kwargs):
    time.sleep(0.4)
    return session.get(url, timeout=40, **kwargs)


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("== 1. Candidate names accepted by the header service")
    for code, names in CANDIDATES.items():
        found = []
        for name in names:
            try:
                response = get(session, f"{HOST}/reference/HeaderDisplayGet-LORE",
                               params={"name": name, "scaleid": "11331", "splice": "test"})
                if response.ok and response.json().get("headers"):
                    body = response.json()
                    found.append(f"{name} -> title={body.get('title')!r} columns={len(body['headers'])}")
            except (requests.RequestException, ValueError):
                continue
        print(f"{code}: {found or 'none of the candidates'}")

    print("\n== 2. Guessed menu endpoints")
    for path in ENDPOINT_GUESSES:
        try:
            response = get(session, HOST + path)
            print(f"{path}: HTTP {response.status_code} {response.text[:300]!r}")
        except requests.RequestException as exc:
            print(f"{path}: {exc}")

    print("\n== 3. LORE page and scripts")
    try:
        page = get(session, PAGE).text
    except requests.RequestException as exc:
        print(f"could not load page: {exc}")
        return
    scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', page, flags=re.IGNORECASE)
    print(f"page length {len(page)}; scripts: {scripts}")
    for line in page.splitlines():
        if KEYWORDS.search(line):
            print(f"PAGE: {line.strip()[:400]}")
    budget = 400
    for src in scripts:
        url = urljoin(PAGE, src)
        if "jquery" in url.lower() or not url.startswith(HOST):
            continue
        try:
            text = get(session, url).text
        except requests.RequestException as exc:
            print(f"-- {url}: {exc}")
            continue
        print(f"-- {url} ({len(text)} characters)")
        # Minified scripts have very long lines; split on statement ends first.
        for piece in re.split(r"[;\n]", text):
            if KEYWORDS.search(piece) and budget > 0:
                print(f"   {piece.strip()[:500]}")
                budget -= 1
        # Other service endpoints named in the script.
        endpoints = sorted(set(re.findall(r"[\w/]*(?:Get|List)-LORE", text)))
        print(f"   endpoints: {endpoints}")


if __name__ == "__main__":
    main()
    sys.exit(0)
