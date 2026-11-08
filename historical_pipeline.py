# historical_pipeline.py
"""Phase 2 historical pipeline: legacy workbooks -> normalized -> bridge ->
monthly / month-over-month / period datasets + reconciliation report.

Usage (from the project root, inside the project's .venv):

    python historical_pipeline.py                 # build + validate
    python historical_pipeline.py --legacy-dir "G:/.../Historicos"
    python historical_pipeline.py --check-only    # validate inputs, write nothing

Read-only inputs:
    input/Historical/Legacy/*.xlsx                (immutable copies; SHA-256 recorded)
    output/Integrated/Analysis/Final/2026-08-01_2026-08-31/   (transition)
    output/Integrated/Analysis/Final/2026-09-01_2026-09-30/   (closed, authoritative)
    config/historical_bridge/*.csv                (version-controlled mappings)

Outputs (git-ignored):
    output/Historical/
        Historical_Normalized_Records.csv
        Historical_Bridge_Records.csv           (legacy + current, common bridge columns)
        Historical_Legacy_Daily.csv / Historical_Current_Daily.csv
        Historical_Legacy_Report_Values.csv
        Historical_Repeated_Entries.csv
        Historical_Monthly_Analysis.csv         (wide, one row per month)
        Historical_Monthly_Long.csv             (month x dimension x value)
        Historical_Monthly_Comparison.csv       (month-over-month)
        Historical_Period_Analysis.csv
        Historical_Taxonomy_Mapping.csv
        Historical_Comparability_Matrix.csv
        Historical_Reconciliation_Report.md
        Historical_Run_Manifest.json

The pipeline never writes to the legacy workbooks or to any current-period
folder; it verifies this by hashing those files before and after the run.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import warnings
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from config.historical import (
    CURRENT_REFERENCE_PERIOD,
    EXCLUDED_SHEETS,
    HISTORICAL_OUTPUT_ROOT,
    LEGACY_INPUT_ROOT,
    LEGACY_SHEETS,
    LEGACY_WORKBOOKS,
    PERIOD_LABEL,
    PROJECT_ROOT,
    REGIME_CURRENT,
    REGIME_LEGACY,
    REGIME_TRANSITION,
    TRANSITION_PERIOD,
    current_final_dir,
)
from config.settings import load_settings
from historical_bridge import (
    BridgeTables,
    bridge_current,
    current_daily,
    legacy_daily,
    normalize_legacy,
    read_current_period,
    repeated_entry_register,
)
from historical_legacy_loader import load_legacy, sha256_file
from historical_monthly import (
    ANCHOR_COMPARABILITY,
    BLOCK_COMPARABILITY,
    DEVICE_COMPARABILITY,
    GROUP_COMPARABILITY,
    METHOD_BREAKS,
    build_monthly,
    month_over_month,
    period_analysis,
)

# openpyxl warns about an unsupported data-validation extension in the
# legacy workbooks; it does not affect values.
warnings.filterwarnings("ignore", message="Data Validation extension is not supported")

PHASE1_REFERENCE = {
    # Figures quoted in the Phase 2 brief. The Phase 1 audit itself is not
    # stored in the repository, so only these headline counts can be compared.
    "days_over_24h": 27,
    "repeated_standard_entries_approx": 226,
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> str:
    """Write rows deterministically; return the SHA-256 of the bytes written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for r in rows:
            for k in r:
                if k not in fieldnames:
                    fieldnames.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    data = buf.getvalue().encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def hash_tree(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(p.relative_to(root)).replace("\\", "/"): sha256_file(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def _f(v) -> float:
    return float(v) if v not in ("", None) else 0.0


# --------------------------------------------------------------------------
# Build (pure: returns all datasets in memory)
# --------------------------------------------------------------------------

def build_all(legacy_dir: Path, project_root: Path = PROJECT_ROOT) -> dict:
    tables = BridgeTables.load()
    settings = load_settings()

    legacy = load_legacy(legacy_dir)
    records = normalize_legacy(legacy["cells"], tables)
    l_daily = legacy_daily(records, legacy["daily"])
    repeats = repeated_entry_register(records)

    final_root = project_root / "output" / "Integrated" / "Analysis" / "Final"
    current_by_regime = {}
    current_records_all = []
    current_daily_all = []
    for regime, period in ((REGIME_TRANSITION, TRANSITION_PERIOD), (REGIME_CURRENT, CURRENT_REFERENCE_PERIOD)):
        final_dir = final_root / period
        analysis, universe, status = read_current_period(final_dir, period)
        recs = bridge_current(analysis, tables, regime, period, settings.laptop_device, settings.desktop_device)
        daily = current_daily(universe, regime, status)
        current_by_regime[regime] = (recs, daily)
        current_records_all.extend(recs)
        current_daily_all.extend(daily)

    wide, long_rows = build_monthly(records, l_daily, legacy["reported"], current_by_regime)
    mom = month_over_month(long_rows, wide)
    period = period_analysis(wide, long_rows, mom)

    return {
        "tables": tables,
        "legacy": legacy,
        "records": records,
        "legacy_daily": l_daily,
        "repeats": repeats,
        "current_records": current_records_all,
        "current_daily": current_daily_all,
        "wide": wide,
        "long": long_rows,
        "mom": mom,
        "period": period,
    }


def bridge_dataset(records: list[dict], current_records: list[dict]) -> list[dict]:
    """Common bridge columns for legacy + current records (included time only)."""
    out = []
    for r in records:
        if r["accounting_status"] != "INCLUDED":
            continue
        out.append(
            {
                "record_id": r["record_id"], "regime": r["regime"], "month": r["month"], "date": r["date"],
                "source": r["source"], "device_class": r["canonical_device"],
                "bridge_group": r["bridge_group"], "bridge_status": r["bridge_status"],
                "canonical_category": r["canonical_category"], "canonical_subcategory": r["canonical_subcategory"],
                "canonical_domain": r["canonical_domain"] if r["domain_status"] == "HIGH_CONFIDENCE" else "",
                "canonical_energy": r["canonical_energy"] if r["energy_status"] == "HIGH_CONFIDENCE" else "",
                "canonical_goal": r["canonical_goal"] if r["goal_status"] == "HIGH_CONFIDENCE" else "",
                "evidence_basis": r["allocation_basis"], "duration_min": r["normalized_duration_min"],
                "provenance": f"{r['source_workbook']}!{r['source_sheet']}!{r['source_cell']}",
            }
        )
    for r in current_records:
        out.append(
            {
                "record_id": r["record_id"], "regime": r["regime"], "month": r["month"], "date": r["date"],
                "source": r["source"], "device_class": r["device_class"],
                "bridge_group": r["bridge_group"], "bridge_status": r["bridge_status"],
                "canonical_category": r["category"], "canonical_subcategory": r["subcategory"],
                "canonical_domain": r["domain"], "canonical_energy": r["energy"], "canonical_goal": r["goal"],
                "evidence_basis": r["allocation_type"], "duration_min": round(r["duration_min"], 6),
                "provenance": f"{r['source_file']}#row{r['source_row']}",
            }
        )
    return out


def taxonomy_mapping_table(records: list[dict], current_records: list[dict], tables: BridgeTables) -> list[dict]:
    out = []
    agg = defaultdict(float)
    cnt = Counter()
    meta = {}
    for r in records:
        if r["accounting_status"] != "INCLUDED":
            continue
        key = (r["legacy_category"], r["legacy_subcategory"], r["legacy_device"], r["legacy_energy"], r["legacy_goal"])
        agg[key] += r["normalized_duration_min"]
        cnt[key] += 1
        meta[key] = r
    for key in sorted(agg):
        r = meta[key]
        out.append(
            {
                "side": "LEGACY", "legacy_category": key[0], "legacy_subcategory": key[1],
                "legacy_device": key[2], "legacy_energy": key[3], "legacy_goal": key[4],
                "bridge_group": r["bridge_group"], "bridge_status": r["bridge_status"],
                "canonical_category": r["canonical_category"], "canonical_subcategory": r["canonical_subcategory"],
                "category_status": r["category_status"],
                "canonical_category_candidates": r.get("canonical_category_candidates", ""),
                "canonical_domain": r["canonical_domain"], "domain_status": r["domain_status"],
                "canonical_energy": r["canonical_energy"], "energy_status": r["energy_status"],
                "canonical_goal": r["canonical_goal"], "goal_status": r["goal_status"],
                "canonical_device": r["canonical_device"], "device_status": r["device_status"],
                "records": cnt[key], "minutes": round(agg[key], 2), "hours": round(agg[key] / 60, 2),
            }
        )
    cagg = defaultdict(float)
    cmeta = {}
    for r in current_records:
        key = (r["source"], r["category"], r["subcategory"])
        cagg[key] += r["duration_min"]
        cmeta[key] = r
    for key in sorted(cagg):
        r = cmeta[key]
        out.append(
            {
                "side": "CURRENT", "current_source": key[0], "current_category": key[1], "current_subcategory": key[2],
                "bridge_group": r["bridge_group"], "bridge_status": r["bridge_status"],
                "minutes": round(cagg[key], 2), "hours": round(cagg[key] / 60, 2),
            }
        )
    return out


def comparability_matrix() -> list[dict]:
    """Metric x regime availability and cross-regime comparability."""
    rows = []

    def add(metric, legacy, transition, current, comparable, note):
        rows.append({"metric": metric, "legacy": legacy, "transition": transition, "current": current,
                     "comparable": comparable, "note": note})

    add("calendar_capacity", "Y", "Y", "Y", "COMPARABLE", "Calendar days x 24 h (legacy report capacity NOT used: mis-entered Oct–Dec 2025 and Jul 2026).")
    add("recorded_time", "Y (manual detail)", "Y (corrected tracked; ~1,230 min double-applied Habit)", "Y (corrected tracked)", "COMPARABLE_WITH_CAVEATS", "Different evidence: manual transcription/estimates vs automatic + screenshot sources.")
    add("overlap", "Y (H-OVR-1)", "Y", "Y", "COMPARABLE_WITH_CAVEATS", "Same day-level rule in all regimes; only detectable when a day exceeds 24 h.")
    add("accounted_time / coverage_pct", "Y", "Y", "Y", "COMPARABLE_WITH_CAVEATS", "Legacy accounted includes household standard allocation (≈3–4 h/day) that the current system leaves in the residual.")
    add("specified_activity / specified_coverage_pct", "Y", "Y", "Y", "COMPARABLE_WITH_CAVEATS", "Preferred like-for-like coverage measure (excludes household + vacation fill).")
    add("unresolved_time", "Y (capacity − accounted)", "Y (Off-Device residual)", "Y (Off-Device residual)", "COMPARABLE_WITH_CAVEATS", "Compare together with HOUSEHOLD_DAILY_LIVING (DAILY_LIVING_AND_RESIDUAL).")
    add("legacy_reported_total", "Y", "N", "N", "LEGACY_ONLY", "Evidence only; detail total is the analytical base (H-RPT-1).")
    legacy_only = {"HOUSEHOLD_DAILY_LIVING", "UNSPECIFIED_VACATION"}
    current_only = {"DIGITAL_SOCIAL_UNSPLIT", "UTILITIES_SYSTEM", "UNCLASSIFIED"}
    for g, (comp, note) in GROUP_COMPARABILITY.items():
        legacy = "N" if g in current_only else "Y"
        cur = "N" if g in legacy_only else "Y"
        add(f"bridge_group:{g}", legacy, cur, cur, comp, note)
    for b, (comp, note) in BLOCK_COMPARABILITY.items():
        add(f"bridge_block:{b}", "Y", "Y", "Y", comp, note)
    add("bridge_block:DAILY_LIVING_AND_RESIDUAL", "Y (household + vacation + unresolved)", "Y (residual)", "Y (residual)", "COMPARABLE_WITH_CAVEATS", "Only robust way to compare daily-living time: legacy estimates vs current inferred residual.")
    for d, (comp, note) in DEVICE_COMPARABILITY.items():
        trans = "N (unsplit)" if d in ("LAPTOP", "DESKTOP") else "Y"
        add(f"device:{d}", "Y", trans, "Y", comp if d not in ("LAPTOP", "DESKTOP") else "COMPARABLE_WITH_CAVEATS (legacy↔current only)", note)
    for a, (comp, note) in ANCHOR_COMPARABILITY.items():
        add(f"activity_anchor:{a}", "Y", "Y", "Y", comp, note)
    add("energy (Deep/Shallow/Active/Passive/Recovery)", "Partial (labels; Recovery/Rest unmappable)", "Y", "Y", "NOT_COMPARABLE", "Legacy 'Recovery / Rest' covers sleep, workout, eating, hobbies; only exact-activity rows are re-classified.")
    add("goal (Investment/Maintenance/Consumption)", "Y (label + activity re-classification)", "Y", "Y", "COMPARABLE_WITH_CAVEATS", "Use canonical_goal_specified shares (excludes household, vacation fill, residual).")
    add("domain", "Y (category/activity based; 2026 sheets carry legacy labels)", "Y", "Y", "COMPARABLE_WITH_CAVEATS", "Personal Admin / phone Health rows REVIEW_REQUIRED.")
    add("time_of_day", "Y (labels)", "N", "N", "NOT_COMPARABLE", "Labels are row defaults, not measured time of day; changed Jan 2026.")
    add("legacy subcategory (device rows)", "N (XLOOKUP example text)", "-", "-", "NOT_COMPARABLE", "Device-row 'Subcategory' is a lookup of the category's example text, not a recorded subcategory.")
    add("budget / target", "Y (legacy Report targets)", "N", "N", "NOT_COMPARABLE", "Legacy report targets are not actuals and are not used.")
    return rows


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def validate(result: dict, project_root: Path) -> list[dict]:
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    cells = result["legacy"]["cells"]
    mism = [c for c in cells if c["cache_check"] == "MISMATCH"]
    check("Legacy cached values equal evaluated entries", not mism, f"{len(mism)} mismatches out of {len(cells)} cells")
    non_num = [c for c in cells if c["value_basis"] == "NON_NUMERIC"]
    check("No non-numeric legacy day cells", not non_num, f"{len(non_num)} non-numeric")
    spill = sum(_f(c["raw_duration_min"]) for c in cells if c["date_in_sheet_month"] == "N")
    check("Spill-over (next-month) columns carry no time", spill == 0, f"{spill:.2f} min in spill-over columns")
    bad_day = [d for d in result["legacy_daily"] if d["sheet_total_check"] != "MATCH"]
    check("Recomputed daily totals equal each sheet's 'Total Hours' row", not bad_day, f"{len(bad_day)} day mismatches / {len(result['legacy_daily'])} days")

    # Report vs detail explained exactly by rows without a total formula.
    unexplained = []
    for w in result["wide"]:
        if w["regime"] != REGIME_LEGACY:
            continue
        m = w["month"]
        excluded = sum(r["normalized_duration_min"] for r in result["records"]
                       if r["month"] == m and r["accounting_status"] == "INCLUDED" and r["in_legacy_report"] == "N") / 60
        diff = float(w["legacy_reported_total_h"]) - w["recorded_h"]
        if abs(diff + excluded) > 0.01:
            unexplained.append(f"{m}: diff {diff:.2f} h, explained {-excluded:.2f} h")
    check("Legacy report − detail fully explained by rows without a total formula", not unexplained, "; ".join(unexplained) or "all 11 months reconcile to 0.00 h")

    # Current-period reconciliation to published Final totals.
    for regime in (REGIME_TRANSITION, REGIME_CURRENT):
        recs = [r for r in result["current_records"] if r["regime"] == regime]
        days = [d for d in result["current_daily"] if d["regime"] == regime]
        non_resid = sum(r["duration_min"] for r in recs if r["bridge_group"] != "RESIDUAL")
        corrected = sum(d["recorded_min"] for d in days)
        check(f"{regime}: bridged source time equals Corrected Tracked Time", abs(non_resid - corrected) < 0.05,
              f"bridged {non_resid:.3f} min vs corrected {corrected:.3f} min")
        unm = sum(r["duration_min"] for r in recs if r["bridge_group"] == "UNMAPPED")
        check(f"{regime}: no unmapped current time", unm == 0, f"{unm:.1f} min unmapped")
    sep = next(w for w in result["wide"] if w["month"] == "2026-09")
    check("September 2026 equals published Final (720h00 / 641h01 / 78h58 / overlap 0)",
          abs(sep["calendar_capacity_h"] - 720) < 1e-6 and abs(sep["accounted_h"] * 60 - 38461.779) < 0.01
          and abs(sep["unresolved_h"] * 60 - 4738.222) < 0.01 and sep["overlap_h"] == 0,
          f"capacity {sep['calendar_capacity_h']} h, accounted {sep['accounted_h']} h, unresolved {sep['unresolved_h']} h, overlap {sep['overlap_h']} h")

    # Phase 1 headline comparisons.
    over = sum(1 for d in result["legacy_daily"] if "OVER_CAPACITY" in d["day_flags"])
    check("Days > 24 h equal Phase 1 count (27)", over == PHASE1_REFERENCE["days_over_24h"], f"{over} days")
    std = sum(1 for r in result["records"] if r["allocation_basis"] == "STANDARD_DAILY_ALLOCATION")
    checks.append({"check": "Repeated standard entries vs Phase 1 (≈226)", "result": "INFO",
                   "detail": f"{std} cells under rule H-STD-1; Phase 1 definition not stored in the repo — see reconciliation report for alternative counts."})

    # Canonical values must exist in the current taxonomy (same Dim_* files as the monthly reports).
    try:
        from historical_canonical import load_current_taxonomy, validate_bridge_against_taxonomy
        taxonomy = load_current_taxonomy()
        tables = result["tables"]
        errors = validate_bridge_against_taxonomy(tables.legacy_rules, tables.label_maps.values(), taxonomy)
        check("Bridge canonical values exist in the current taxonomy (no invented category / subcategory / domain / energy / goal)",
              not errors, "; ".join(errors) or f"{len(tables.legacy_rules)} legacy rules checked against {taxonomy.source}")
    except FileNotFoundError as exc:
        checks.append({"check": "Bridge canonical values exist in the current taxonomy", "result": "INFO",
                       "detail": f"Current taxonomy files not available: {exc}"})

    # Git hygiene: outputs and inputs live under git-ignored roots.
    gitignore = (project_root / ".gitignore")
    patterns = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    ok = "input/" in patterns and "output/" in patterns
    check(".gitignore excludes input/ and output/ (no private data in Git)", ok, "patterns present" if ok else "missing pattern")
    return checks


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v) for v in r) + " |")
    return "\n".join(out)


def render_report(result: dict, checks: list[dict], manifest: dict) -> str:
    legacy = result["legacy"]
    wide = result["wide"]
    lines = [
        "# Historical Reconciliation Report — Phase 2",
        "",
        f"Period: **{PERIOD_LABEL}**. Generated {manifest['generated_utc']} by `historical_pipeline.py`.",
        "",
        "Regimes: LEGACY_MANUAL_TRACKING (2025-09 .. 2026-07) · TRANSITION_REVIEW (2026-08) · CURRENT_RECONCILED_TIME_ACCOUNTING (2026-09, read-only).",
        "",
        "## 1. Source evidence",
        "",
        _md_table(["Workbook", "Bytes", "SHA-256"], [[w["source_workbook"], w["bytes"], w["sha256"]] for w in legacy["workbooks"]]),
        "",
        "Excluded sheets (kept as evidence, not used):",
        "",
    ]
    for sheet, why in EXCLUDED_SHEETS.items():
        lines.append(f"- `{sheet}` — {why}")
    lines += ["", "Detected sheet layouts:", ""]
    lines.append(_md_table(
        ["Month", "Sheet", "Total col", "Day cols", "In-month", "Spill-over cols", "Detail rows", "Rows w/o total formula", "Legacy domain labels"],
        [[m["sheet_month"], f"{m['source_workbook'][25:-5]} / {m['source_sheet']}", m["total_column"], m["day_columns"],
          m["in_month_day_columns"], m["spillover_columns"] or "-", m["detail_rows"], m["rows_without_total_formula"] or "-",
          "yes" if m["legacy_domain_map"] else "no"] for m in legacy["meta"]]))
    lines += [
        "",
        "## 2. Legacy detail vs legacy report (H-RPT-1)",
        "",
        _md_table(
            ["Month", "Calendar cap h", "Detail h", "Reported h", "Diff h", "Diff %", "Report capacity h", "Report 'Other' h", "Explanation"],
            [[w["month"], w["calendar_capacity_h"], round(w["legacy_detail_total_h"], 2), round(float(w["legacy_reported_total_h"]), 2),
              round(w["report_minus_detail_h"], 2), round(w["report_minus_detail_pct"], 2), w["legacy_reported_capacity_h"],
              round(float(w["legacy_reported_other_h"]), 2) if w["legacy_reported_other_h"] != "" else "",
              ("Row 51 (off-device Personal Admin) has no row-total formula → excluded by report" if abs(w["report_minus_detail_h"]) > 0.01 else "Reconciles")
              + ("; report capacity mis-entered" if abs(float(w["legacy_reported_capacity_h"]) - w["calendar_capacity_h"]) > 0.01 else "")]
             for w in wide if w["regime"] == REGIME_LEGACY]),
        "",
        "Decision: the **detail total** is the analytical base. It is complete (includes row 51), reproducible from cells, and independent of the report's capacity inputs, which were mis-entered in Oct 2025 (30 h × 31 d), Nov 2025 (30 × 30), Dec 2025 (31 × 31) and Jul 2026 (24 × 27). The report values are preserved in `Historical_Legacy_Report_Values.csv`.",
        "",
        "## 3. Days exceeding 24 hours (H-OVR-1)",
        "",
    ]
    over = [d for d in result["legacy_daily"] if "OVER_CAPACITY" in d["day_flags"]]
    total_over = sum(d["overlap_min"] for d in over)
    lines.append(
        f"{len(over)} days exceed 24 h; total excess {total_over:.1f} min ({total_over/60:.2f} h) across 11 months; "
        f"median excess {sorted(d['overlap_min'] for d in over)[len(over)//2]:.1f} min; max {max(d['overlap_min'] for d in over):.1f} min."
    )
    lines += [
        "",
        _md_table(["Date", "Recorded h", "Excess min", "Standard allocation min", "Status"],
                  [[d["date"], round(d["recorded_min"] / 60, 2), round(d["overlap_min"], 1), round(d["standard_allocation_min"], 1), d["day_status"]] for d in over]),
        "",
        "Evidence: every day's recomputed total equals the sheet's own 'Total Hours' row (no parsing artefact); the excess is small relative to the estimated household allocation (90–240 min) and to transcribed phone time, both of which can run concurrently with other entries. No single record is identifiable as a duplicate, so no record is deleted or capped. Treatment mirrors the current system's Tracked Overlap: overlap = max(0, day − 1440); accounted = recorded − overlap.",
        "",
        "## 4. Repeated standard entries (H-STD-1 / H-REP-1)",
        "",
        "Register of off-device entries repeated ≥ 2 times in the same row-month (classification by rule):",
        "",
    ]
    reg = result["repeats"]
    by_class = Counter()
    by_class_cells = Counter()
    for r in reg:
        by_class[r["classification"]] += r["total_min"]
        by_class_cells[r["classification"]] += r["occurrences"]
    lines.append(_md_table(["Classification", "Cells", "Hours"],
                           [[k, by_class_cells[k], round(v / 60, 1)] for k, v in sorted(by_class.items())]))
    hh = [r for r in result["records"] if r["accounting_status"] == "INCLUDED" and r["legacy_category"] == "Household / Chores"]
    vals = Counter(r["normalized_duration_min"] for r in hh)
    modal = Counter((r["month"], r["raw_entry"]) for r in hh)
    modal_cells = sum(max(c for (m2, _), c in modal.items() if m2 == m) for m in {r["month"] for r in hh})
    lines += [
        "",
        f"Household cells: {len(hh)} non-zero. Alternative counts for comparison with Phase 1 (≈226): modal entry per month = {modal_cells}; value ∈ {{180, 240}} = {vals[180.0] + vals[240.0]}; rule H-STD-1 (≥5 identical entries in the row-month) = {by_class_cells['STANDARD_DAILY_ALLOCATION']}. The Phase 1 definition is not stored in the repository, so the difference cannot be reconciled line by line.",
        "",
        "Evidence for 'standard daily allocation': the entries reproduce fixed compositions ('=30+30+15+60+15+30' = 180 min in Sep 2025; '=30+10+10+20+20+10+60+15+20+30+15' = 240 min from Oct 2025, then the literal 240), and the same composition is the Household budget in the legacy reports (e.g. Report 1 D12 '=(30+30+15+60+15+30)/60'). They are planned/standard values entered as actuals, not measurements and not overlaps. Treatment: kept in recorded time, reported separately (`standard_allocation_h`), excluded from `specified_activity` for like-for-like comparison with the current system.",
        "",
        "## 5. August 2026 (TRANSITION / REVIEW)",
        "",
        "Read as published from `Final/2026-08-01_2026-08-31`; not repaired. Known issues kept visible:",
        "",
        "- ~1,230 min of Aug 16–31 Habit adjustments applied twice (Aug 17 +240, Aug 27 +420, Aug 29 +420, Aug 30 +150; see Sept Monthly KPI Summary) → recorded/accounted overstated by up to ~20.5 h (≈2.8% of capacity).",
        "- Analytical total 755h14m exceeds capacity 744h (overlap 11h15m).",
        "- Laptop and desktop not separated (Device = 'ActivityWatch'); only COMPUTERS_TOTAL is comparable.",
        "- Three attribution removals (shared desktop) capped at available time (300 min requested, 0 applied).",
        "",
        "## 6. September 2026 (authoritative reference)",
        "",
        "Read-only. All `Final/2026-09-01_2026-09-30` files are hashed before and after the run (see §8).",
        "",
        "## 7. Methodology transition points",
        "",
    ]
    for month, items in METHOD_BREAKS.items():
        for target, desc in items:
            lines.append(f"- **{month}** ({target}): {desc}")
    lines += ["", "## 8. Validation", "", _md_table(["Check", "Result", "Detail"], [[c["check"], c["result"], c["detail"]] for c in checks]), ""]
    lines += ["Integrity hashes (before == after run):", ""]
    for name, info in manifest["integrity"].items():
        lines.append(f"- {name}: {'UNCHANGED' if info['unchanged'] else 'CHANGED'} ({info['files']} files)")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run(legacy_dir: Path, out_dir: Path, project_root: Path = PROJECT_ROOT, write: bool = True) -> dict:
    final_root = project_root / "output" / "Integrated" / "Analysis" / "Final"
    protected = {
        "legacy_workbooks": legacy_dir,
        f"current_final_{CURRENT_REFERENCE_PERIOD}": final_root / CURRENT_REFERENCE_PERIOD,
        f"transition_final_{TRANSITION_PERIOD}": final_root / TRANSITION_PERIOD,
    }
    before = {k: hash_tree(v) if k != "legacy_workbooks" else
              {w: sha256_file(v / w) for w in LEGACY_WORKBOOKS} for k, v in protected.items()}

    result = build_all(legacy_dir, project_root)
    # Reproducibility: a second in-memory build must be identical.
    second = build_all(legacy_dir, project_root)
    reproducible = all(
        json.dumps(result[k], sort_keys=True, default=str) == json.dumps(second[k], sort_keys=True, default=str)
        for k in ("records", "legacy_daily", "current_records", "wide", "long", "mom", "period")
    )
    checks = validate(result, project_root)
    checks.append({"check": "Two consecutive builds are identical (deterministic)", "result": "PASS" if reproducible else "FAIL", "detail": "records, daily, monthly, MoM, period"})

    manifest = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": PERIOD_LABEL,
        "legacy_dir": _display_path(legacy_dir, project_root),
        "workbooks": result["legacy"]["workbooks"],
        "mapping_tables": {p.name: sha256_file(p) for p in sorted((PROJECT_ROOT / "config" / "historical_bridge").glob("*.csv"))},
        "outputs": {},
    }

    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "Historical_Normalized_Records.csv": result["records"],
            "Historical_Bridge_Records.csv": bridge_dataset(result["records"], result["current_records"]),
            "Historical_Legacy_Daily.csv": result["legacy_daily"],
            "Historical_Current_Daily.csv": result["current_daily"],
            "Historical_Legacy_Report_Values.csv": result["legacy"]["reported"],
            "Historical_Legacy_Sheet_Layout.csv": result["legacy"]["meta"],
            "Historical_Repeated_Entries.csv": result["repeats"],
            "Historical_Monthly_Analysis.csv": result["wide"],
            "Historical_Monthly_Long.csv": result["long"],
            "Historical_Monthly_Comparison.csv": result["mom"],
            "Historical_Period_Analysis.csv": result["period"],
            "Historical_Taxonomy_Mapping.csv": taxonomy_mapping_table(result["records"], result["current_records"], result["tables"]),
            "Historical_Comparability_Matrix.csv": comparability_matrix(),
            "Historical_Validation.csv": checks,
        }
        for name, rows in files.items():
            manifest["outputs"][name] = {"rows": len(rows), "sha256": write_csv(out_dir / name, rows)}

    after = {k: hash_tree(v) if k != "legacy_workbooks" else
             {w: sha256_file(v / w) for w in LEGACY_WORKBOOKS} for k, v in protected.items()}
    manifest["integrity"] = {
        k: {"files": len(before[k]), "unchanged": before[k] == after[k]} for k in protected
    }
    for k, info in manifest["integrity"].items():
        checks.append({"check": f"Protected inputs unchanged: {k}", "result": "PASS" if info["unchanged"] else "FAIL", "detail": f"{info['files']} files hashed before/after"})

    if write:
        report = render_report(result, checks, manifest)
        (out_dir / "Historical_Reconciliation_Report.md").write_text(report, encoding="utf-8")
        manifest["outputs"]["Historical_Reconciliation_Report.md"] = {"sha256": hashlib.sha256(report.encode("utf-8")).hexdigest()}
        write_csv(out_dir / "Historical_Validation.csv", checks)
        (out_dir / "Historical_Run_Manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result["checks"] = checks
    result["manifest"] = manifest
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--legacy-dir", type=Path, default=LEGACY_INPUT_ROOT,
                        help="Folder holding the legacy workbooks (default: input/Historical/Legacy).")
    parser.add_argument("--out-dir", type=Path, default=HISTORICAL_OUTPUT_ROOT)
    parser.add_argument("--check-only", action="store_true", help="Build and validate in memory; write nothing.")
    args = parser.parse_args(argv)

    result = run(args.legacy_dir, args.out_dir, write=not args.check_only)
    failed = [c for c in result["checks"] if c["result"] == "FAIL"]
    for c in result["checks"]:
        print(f"[{c['result']}] {c['check']} — {c['detail']}")
    if not args.check_only:
        print(f"\nOutputs written to {args.out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
