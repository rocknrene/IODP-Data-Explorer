"""Map the layout of the NOAA NCEI JOIDES Resolution archive.

Development aid, run by the ``live checks`` workflow. ODP Legs and IODP
Expeditions 301 to 312 predate the LIMS database and are archived by NOAA
NCEI at https://www.ngdc.noaa.gov/mgg/geology/data/joides_resolution/.
Before a reader for that archive can be written, its directory layout and
file formats must be known. This script

1. prints the host's ``robots.txt`` and whether it permits this client;
2. if permitted, walks a small, bounded part of the archive (the index, one
   early and one late ODP leg) and prints each directory listing;
3. prints the first lines of a few data files of each type it meets.

At most :data:`MAX_REQUESTS` requests are made, with a pause between them.
Output goes to standard output.
"""

from __future__ import annotations

import re
import time
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests

ROOT = "https://www.ngdc.noaa.gov/mgg/geology/data/joides_resolution/"
USER_AGENT = "SOD-Explorer/2.0 (archive layout mapping; github.com/rocknrene/IODP-Data-Explorer)"
MAX_REQUESTS = 60
PAUSE_S = 1.0
PREFERRED = ("175", "204", "101", "210", "301", "311")   # legs to look into first
DATA_SUFFIXES = (".txt", ".csv", ".dat", ".tab", ".tsv", ".asc", ".htm", ".html", ".xml", ".json")

_requests_made = 0


def fetch(session: requests.Session, url: str, head_bytes: int | None = None) -> requests.Response | None:
    """GET a URL within the request budget; optionally only its first bytes."""
    global _requests_made
    if _requests_made >= MAX_REQUESTS:
        return None
    _requests_made += 1
    time.sleep(PAUSE_S)
    headers = {"Range": f"bytes=0-{head_bytes - 1}"} if head_bytes else {}
    try:
        return session.get(url, timeout=40, headers=headers, stream=bool(head_bytes))
    except requests.RequestException as exc:
        print(f"   ! {url}: {exc}")
        return None


def links(html: str, base: str) -> list[str]:
    """Absolute URLs of the links on a page that stay inside the archive."""
    found = []
    for href in re.findall(r'href=["\']([^"\'#?]+)["\']', html, flags=re.IGNORECASE):
        url = urljoin(base, href)
        if url.startswith(ROOT) and url != base and url not in found and len(url) > len(base) - 1:
            found.append(url)
    return found


def show_file(session: requests.Session, url: str) -> None:
    response = fetch(session, url, head_bytes=1500)
    if response is None:
        return
    body = response.raw.read(1500, decode_content=True) if response.raw else b""
    size = response.headers.get("Content-Range", response.headers.get("Content-Length", "?"))
    print(f"   FILE {url}\n        HTTP {response.status_code} type={response.headers.get('Content-Type')} size={size}")
    for line in body.decode("latin-1", errors="replace").splitlines()[:14]:
        print(f"        | {line[:220]}")


def walk(session: requests.Session, url: str, depth: int, shown_types: dict[str, int]) -> None:
    response = fetch(session, url)
    if response is None:
        return
    print(f"\nDIR {url}  HTTP {response.status_code} ({len(response.text)} characters)")
    if not response.ok:
        print(f"   {response.text[:300]!r}")
        return
    children = links(response.text, url)
    directories = [c for c in children if c.endswith("/")]
    files = [c for c in children if not c.endswith("/")]
    print(f"   {len(directories)} directories: {[d[len(url):] for d in directories][:120]}")
    print(f"   {len(files)} files: {[f[len(url):] for f in files][:80]}")
    for file_url in files:
        name = urlparse(file_url).path.rsplit("/", 1)[-1].lower()
        kind = re.sub(r"\d+", "#", name)        # group files that differ only by numbers
        if name.endswith(DATA_SUFFIXES) and shown_types.get(kind, 0) < 1:
            shown_types[kind] = shown_types.get(kind, 0) + 1
            show_file(session, file_url)
    if depth <= 0:
        return
    ordered = sorted(directories, key=lambda d: (not any(p in d[len(url):] for p in PREFERRED), d))
    for directory in ordered[:3]:
        walk(session, directory, depth - 1, shown_types)


def main() -> None:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    robots_url = urljoin(ROOT, "/robots.txt")
    print(f"== robots.txt ({robots_url})")
    parser = robotparser.RobotFileParser()
    try:
        robots = session.get(robots_url, timeout=30)
        print(f"HTTP {robots.status_code}\n{robots.text[:3000]}")
        parser.parse(robots.text.splitlines() if robots.ok else [])
    except requests.RequestException as exc:
        print(f"could not read robots.txt: {exc}")
    allowed = parser.can_fetch(USER_AGENT, ROOT)
    print(f"\n== robots.txt permits this client to read the archive: {allowed}")
    if not allowed:
        print("Stopping: the archive is not mapped because robots.txt disallows automated access.")
        return

    print("\n== archive layout")
    walk(session, ROOT, depth=3, shown_types={})
    print(f"\n{_requests_made} requests made (limit {MAX_REQUESTS}).")


if __name__ == "__main__":
    main()
