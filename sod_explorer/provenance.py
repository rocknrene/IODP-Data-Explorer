"""Machine-readable provenance for datasets and exports.

Every dataset loaded into SOD Explorer carries a provenance record: a
JSON-serializable dictionary that states where the data came from, how it
was requested, when it was retrieved, how it should be cited, and which
processing steps were applied. Merged datasets carry the records of both
inputs together with the merge parameters and summary counts.

Exports are ZIP archives containing

``data.csv``
    The exported table.
``provenance.json``
    The full provenance record (schema ``sod-explorer-provenance/1``).
``CITATION.txt``
    Plain-text citations for every source dataset and for this software.

PANGAEA distributes its datasets under licenses that require citation of
the dataset DOI; ``CITATION.txt`` lists these citations for inclusion in
any publication that uses the exported data.
"""

from __future__ import annotations

import copy
import io
import json
import math
import zipfile
from datetime import datetime, timezone

import pandas as pd

from . import __version__
from .columns import depth_scale, find_depth_column

SCHEMA = "sod-explorer-provenance/1"
SOFTWARE_NAME = "SOD Explorer"
SOFTWARE_URL = "https://huggingface.co/spaces/rocknrene/SOD-Explorer"
SOFTWARE_CITATION = (
    f"Castillo, R. ({datetime.now(timezone.utc).year}). SOD Explorer (version {__version__}) "
    f"[Computer software]. {SOFTWARE_URL}"
)


def utc_now() -> str:
    """Current UTC time in ISO 8601 format with second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _describe_table(df: pd.DataFrame) -> dict:
    """Row count, column names, and detected depth column and scale of a table."""
    depth_column = find_depth_column(df)
    return {
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "depth_column": depth_column,
        "depth_scale": depth_scale(depth_column) if depth_column else None,
    }


def new_record(source: dict, df: pd.DataFrame, label: str | None = None) -> dict:
    """Create a provenance record for a dataset obtained from one source.

    Parameters
    ----------
    source
        Description of the source. Required key: ``type`` (``"upload"``,
        ``"lore"``, ``"pangaea"``, or ``"dsdp_shinylaurel"``). Conventional
        keys: ``name``, ``url``, ``query``, ``retrieved_utc``,
        ``citation``, ``doi``, ``license``, ``file``.
    df
        The dataset as loaded.
    label
        Short human-readable name of the dataset.
    """
    if "type" not in source:
        raise ValueError("A provenance source must declare its 'type'")
    return _json_safe({
        "schema": SCHEMA,
        "software": {"name": SOFTWARE_NAME, "version": __version__, "url": SOFTWARE_URL},
        "created_utc": utc_now(),
        "label": label or source.get("name", source["type"]),
        "source": source,
        "table": _describe_table(df),
        "processing": [],
    })


def upload_record(meta: dict, df: pd.DataFrame) -> dict:
    """Provenance record for a user-uploaded file."""
    file_info = {k: meta[k] for k in ("filename", "format", "sha256", "encoding",
                                       "header_row", "delimiter", "zip_member", "preamble",
                                       "identifiers_parsed_from")
                 if k in meta}
    source = {"type": "upload", "name": meta.get("filename", "uploaded file"),
              "retrieved_utc": utc_now(), "file": file_info}
    return new_record(source, df, label=meta.get("filename"))


def add_step(record: dict | None, step: str, **parameters) -> dict | None:
    """Return a copy of ``record`` with one processing step appended."""
    if record is None:
        return None
    updated = copy.deepcopy(record)
    updated["processing"].append({"step": step, "utc": utc_now(),
                                  "parameters": _json_safe(parameters)})
    return updated


def merge_record(record_a: dict | None, record_b: dict | None, report: dict,
                 merged: pd.DataFrame) -> dict:
    """Provenance record for a depth-tolerance merge of two datasets."""
    record = new_record({"type": "merge", "name": "Depth-tolerance merge of datasets A and B"},
                        merged, label="Merged A + B")
    record["inputs"] = {"A": record_a, "B": record_b}
    record["merge"] = _json_safe(report)
    return record


def _json_safe(node):
    """Replace non-finite floats with ``None`` so the output is strict JSON."""
    if isinstance(node, dict):
        return {str(k): _json_safe(v) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return [_json_safe(v) for v in node]
    if isinstance(node, float):
        return node if math.isfinite(node) else None
    if node is None or isinstance(node, (str, int, bool)):
        return node
    try:  # NumPy scalars
        return _json_safe(node.item())
    except AttributeError:
        return str(node)


def collect_citations(record: dict | None) -> tuple[list[str], list[str]]:
    """Dataset citations and citation guidance in a record and its inputs.

    Returns
    -------
    citations : list of str
        Formal citations supplied by the source (for example, PANGAEA
        dataset citations with DOI), without duplicates.
    notes : list of str
        Citation guidance for sources that do not issue a formal citation
        (for example, LORE, whose data are cited through the expedition
        Proceedings volume), without duplicates.
    """
    citations: list[str] = []
    notes: list[str] = []

    def visit(node):
        """Collect the citation and citation note of one record, then of its inputs."""
        if not isinstance(node, dict):
            return
        source = node.get("source", {})
        citation = source.get("citation")
        if citation:
            doi = source.get("doi")
            text = citation if (not doi or doi in citation) else f"{citation} https://doi.org/{doi}"
            if text not in citations:
                citations.append(text)
        note = source.get("citation_note")
        if note and note not in notes:
            notes.append(note)
        for child in (node.get("inputs") or {}).values():
            visit(child)

    visit(record)
    return citations, notes


def export_archive(df: pd.DataFrame, record: dict | None, basename: str = "sod_export") -> bytes:
    """Build a ZIP archive with the data, its provenance, and citations.

    Parameters
    ----------
    df
        Table to export.
    record
        Provenance record of ``df`` (``None`` produces a record stating that
        provenance is unavailable).
    basename
        Folder name inside the archive.
    """
    record = copy.deepcopy(record) if record else {
        "schema": SCHEMA, "source": {"type": "unknown"}, "processing": [],
    }
    record["exported_utc"] = utc_now()
    record["exported_table"] = _describe_table(df)

    citations, notes = collect_citations(record)
    citation_lines = ["Cite the following when using these data:", ""]
    if citations:
        citation_lines += ["Source datasets:"] + [f"  - {c}" for c in citations] + [""]
    if notes:
        citation_lines += ["Citation guidance:"] + [f"  - {n}" for n in notes] + [""]
    if not citations and not notes:
        citation_lines += ["Source datasets: no citation was provided by the source; "
                           "see provenance.json for the source description.", ""]
    citation_lines += ["Software:", f"  - {SOFTWARE_CITATION}", ""]

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{basename}/data.csv", df.to_csv(index=False))
        archive.writestr(f"{basename}/provenance.json",
                         json.dumps(_json_safe(record), indent=2, allow_nan=False))
        archive.writestr(f"{basename}/CITATION.txt", "\n".join(citation_lines))
    return buffer.getvalue()
