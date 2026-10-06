# Reference data

## `exp_site_hole.csv`

Every Leg/Expedition, Site, and Hole combination known to SOD Explorer,
with the scientific program and drilling platform for each.

| Column    | Description                                                                 |
|-----------|-----------------------------------------------------------------------------|
| `Exp`     | Leg (DSDP, ODP) or Expedition (IODP) identifier. May carry a letter suffix (e.g. `343T`, `395C`). |
| `Site`    | Site number. IODP sites carry a platform prefix (`U` = JOIDES Resolution, `C` = Chikyu, `M` = MSP). |
| `Hole`    | Hole letter. `*` marks an early DSDP hole drilled before hole letters were assigned. |
| `program` | `DSDP`, `ODP`, or `IODP`.                                                   |
| `vessel`  | `Glomar Challenger`, `JOIDES Resolution`, `Chikyu`, or `MSP`.               |

Snapshot contents: 4,249 holes (DSDP 1,064; ODP 1,818; IODP 1,367) and three
expeditions listed by platform only (see below).

### Provenance

**Program and platform assignments** (`program`, `vessel`) follow the
*Scientific Ocean Drilling Legacy Data Access: Quick Start Guide*
(Childress, v1.0, September 2026; Gulf Coast Repository and IODP Science
Operator, Texas A&M University):

- DSDP: Glomar Challenger, Legs 1 to 96.
- ODP: JOIDES Resolution. The guide gives Legs 101 to 210; the table also
  contains Leg 100 (three holes at Site 625), ODP's first leg.
- IODP, Chikyu: Expeditions 314, 315, 316, 319, 322, 326, 331, 332, 333, 337,
  338, 343, 348, 358, 365, 370, 380, 405.
- IODP, Mission-Specific Platforms: Expeditions 302, 310, 313, 325, 347, 357,
  364, 381, 386, 389.
- IODP, JOIDES Resolution: all other expeditions.

The test suite (`tests/test_reference_and_columns.py`) checks every row
against these lists.

Expeditions 380 and 405 (Chikyu) and 389 (MSP) were added from the guide
with empty Site and Hole values, because the guide lists expeditions but not
holes. They can be selected and are routed to the correct archive; their
sites and holes remain to be entered.

**Site and Hole list.** The Expedition/Site/Hole rows were provided by
L. B. Childress (Gulf Coast Repository, Texas A&M University; personal
communication, 2026). The `*` hole convention (early DSDP holes drilled
before hole letters were assigned) comes with that list.

> **Still to confirm with the provider before publication:** the database
> the list was drawn from and the date it was extracted.

### Updating

Replace the CSV, keep the five columns above, and run `pytest` to confirm
the reference-table tests still pass.
