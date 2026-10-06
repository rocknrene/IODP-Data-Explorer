"""SOD Explorer: retrieval, depth-registered merging, and visualization of
scientific ocean drilling data from the DSDP, ODP, and IODP programs.

The package is organized so that data access, parsing, and analysis can be
used and tested independently of the web interface:

``reference``
    Static Expedition/Site/Hole reference table and program/platform lookup.
``columns``
    Classification of table columns (depth, identifier, measurement) and
    inference of the depth scale from column headers.
``parsing``
    Readers for user-supplied CSV, TSV, Excel, LAS, and ZIP files.
``analysis``
    Depth-tolerance merging, autocorrelation-corrected correlation
    statistics, depth-window smoothing, and sampling-gap detection.
``provenance``
    Machine-readable provenance records attached to every dataset and export.
``sources``
    Clients for the LIMS Online Report Environment (LORE), PANGAEA, and the
    DSDP data-access application, plus the program-to-source routing rules.
``plotting``, ``layout``, ``callbacks``
    The Dash user interface.
"""

__version__ = "2.0.0"
