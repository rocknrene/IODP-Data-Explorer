"""Clients for public scientific ocean drilling data services.

``lore``
    LIMS Online Report Environment (LORE), the IODP JOIDES Resolution
    Science Operator's report interface to the LIMS database.
``pangaea``
    PANGAEA Data Publisher for Earth & Environmental Science.
``shinylaurel``
    DSDP Data Access application for legacy Deep Sea Drilling Project data.
``routing``
    Rules that select a source from the drilling program and platform.
``catalog``
    Report types offered in the interface and their names in each source.

Every client returns a table together with a provenance ``source``
dictionary (see :mod:`sod_explorer.provenance`), and raises
:class:`~sod_explorer.sources.common.SourceError` with a user-facing
message when no data can be returned.
"""
