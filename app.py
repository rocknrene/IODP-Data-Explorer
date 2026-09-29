# ─────────────────────────────────────────────────────────────────────────────
# WHERE EACH LEG'S DATA COMES FROM
#
#   DSDP (Legs 1-96, Glomar Challenger)  -> shinylaurel.com ONLY.
#                                           Never NGDC, never PANGAEA.
#   ODP  (Legs 100-210, JOIDES Resolution)
#   IODP (Expeditions 301+, JOIDES Resolution)
#                                        -> NGDC JOIDES Resolution archive first,
#                                           LIMS/LORE as the backup.
#
# NGDC's archive covers ODP and IODP Phase 1 (Expeditions 301-346). For
# later expeditions (e.g. 400, 402) NGDC has nothing, so LORE answers.
#
# Non-JOIDES Resolution IODP expeditions (Chikyu, Mission Specific
# Platforms) are not in either source and keep their existing handling.
# ─────────────────────────────────────────────────────────────────────────────

NGDC_BASE = "https://www.ngdc.noaa.gov/mgg/geology/data/joides_resolution/"


def drilling_program(leg):
    """Which drilling program a Leg/Expedition number belongs to."""
    n = int(str(leg).strip())
    if n <= 96:
        return "DSDP"
    if 100 <= n <= 210:
        return "ODP"
    if n >= 301:
        return "IODP"
    return None  # 97-99 and 211-300 were never used


def fetch_by_program(report, exp, site="", hole="", vessel=None):
    """Fetch one report for one Leg, from the right source(s) for its program.

    Returns (DataFrame or None, status message).
    Relies on fetch_dsdp_shinylaurel, fetch_ngdc and fetch_lore, which
    live elsewhere in app.py.
    """
    exp = str(exp).strip()
    try:
        program = drilling_program(exp)
    except ValueError:
        return None, f"'{exp}' is not a Leg/Expedition number"
    if program is None:
        return None, f"No drilling program used Leg/Expedition number {exp}"

    # DSDP: shinylaurel.com only. If it doesn't have it, say so and stop.
    if program == "DSDP":
        dsdp_type = SHINYLAUREL_DSDP_TYPES.get(report)
        if not dsdp_type:
            return None, (f"shinylaurel.com's DSDP archive has no "
                          f"{LORE_REPORTS.get(report, report)} data type")
        df, err = fetch_dsdp_shinylaurel(dsdp_type, exp, site, hole)
        if df is not None and not df.empty:
            return df, f"✓ DSDP (shinylaurel.com)  {dsdp_type}  Leg {exp}  ({len(df):,} rows)"
        return None, f"No {dsdp_type} data on shinylaurel.com for DSDP Leg {exp}"

    # IODP expeditions not drilled by the JOIDES Resolution aren't in
    # NGDC's JR archive or LORE; leave them to the existing Chikyu/MSP path.
    if program == "IODP" and vessel and vessel != "JOIDES Resolution":
        return None, f"Expedition {exp} was not a JOIDES Resolution expedition"

    # ODP and IODP (JR): NGDC first, then LORE.
    label = "Leg" if program == "ODP" else "Exp"
    reasons = []

    df, err = fetch_ngdc(report, exp, site, hole)
    if df is not None and not df.empty:
        return df, f"✓ NGDC  {LORE_REPORTS.get(report, report)}  {label} {exp}  ({len(df):,} rows)"
    reasons.append(f"NGDC: {err or 'no data'}")

    df, err = fetch_lore(report, exp, site, hole)
    if df is not None and not df.empty:
        return df, f"✓ LIMS/LORE  {LORE_REPORTS.get(report, report)}  {label} {exp}  ({len(df):,} rows)"
    reasons.append(f"LORE: {err or 'no data'}")

    return None, f"No data found for {program} {label} {exp}. " + "; ".join(reasons)
