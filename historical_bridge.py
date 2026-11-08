# historical_bridge.py
"""Historical normalization + canonical bridge (Phase 2).

Architecture (Option C, see docs/HISTORICAL_BRIDGE.md):

1. Historical normalized layer – one record per legacy timesheet cell, with
   all legacy values preserved verbatim and full provenance.
2. Canonical bridge layer – the same records annotated with the canonical
   (current-taxonomy) values only where the mapping is defensible, plus a
   'bridge group' that both legacy and current records map into so that a
   comparable monthly analysis can be built without forcing either side to
   look like the other.

Current-system periods (August 2026 transition, September 2026 closed) are
read from their Final outputs and mapped into the same bridge groups. They
are never modified.

Deterministic accounting rules implemented here (IDs are referenced in the
datasets and in the reconciliation report):

H-PER-1  Only cells whose date falls inside the sheet's month are counted.
         Spill-over day columns (next month's dates inside the sheet) are
         kept with accounting_status=EXCLUDED_OUT_OF_PERIOD.
H-STD-1  Household / Chores cells whose exact entry repeats >= 5 times in the
         same row and month are STANDARD_DAILY_ALLOCATION (template value).
         They are kept in recorded time and reported separately.
H-REP-1  Other off-device rows with an entry repeated >= 5 times in the month
         are flagged REPEATED_ROUTINE_VALUE (informational only).
H-VAC-1  'Vacations' cells are BALANCING_FILL (unspecified vacation time).
H-OVR-1  Day overlap = max(0, recorded day minutes - 1440). Mirrors the
         current system's 'Tracked Overlap' (Final_Daily_Reconciliation).
         No record is deleted or capped; overlap is a day-level adjustment.
H-DAY-1  Day status NORMAL_TOLERANCE when unresolved <= 120 min (current
         system tolerance), REVIEW otherwise; OVER_CAPACITY when recorded
         > 1440; INCOMPLETE_OFF_DEVICE when no off-device core entry > 0.
H-RPT-1  Legacy report totals are evidence, not ground truth: rows without a
         row-total formula are excluded by the legacy report but included in
         the detail total.
"""

from __future__ import annotations

import csv
import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from config.historical import (
    BRIDGE_CONFIG_ROOT,
    REGIME_CURRENT,
    REGIME_LEGACY,
    REGIME_TRANSITION,
)

DAY_CAPACITY_MIN = 1440.0
DAY_TOLERANCE_MIN = 120.0  # same tolerance as final_analysis.py daily reconciliation
STANDARD_REPEAT_THRESHOLD = 5

HIGH = "HIGH_CONFIDENCE"
REVIEW = "REVIEW_REQUIRED"
UNMAPPED = "UNMAPPED"
# The legacy record belongs to one of several current categories that the
# legacy row cannot distinguish (candidate_categories); no single current
# category is asserted.
MULTI_CATEGORY = "MULTI_CATEGORY"
_STATUS_RANK = {HIGH: 0, "LABEL": 0, REVIEW: 1, MULTI_CATEGORY: 1, UNMAPPED: 2, "NOT_COMPARABLE": 2, "": 0}


# --------------------------------------------------------------------------
# Mapping tables
# --------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@dataclass
class BridgeTables:
    groups: dict[str, dict]
    legacy_rules: list[dict]
    label_maps: dict[tuple[str, str], dict]
    current_rules: list[dict]

    @classmethod
    def load(cls, root: Path = BRIDGE_CONFIG_ROOT) -> "BridgeTables":
        groups = {g["bridge_group"]: g for g in _read_csv(root / "bridge_groups.csv")}
        groups.setdefault(
            "RESIDUAL",
            {
                "bridge_group": "RESIDUAL",
                "display_name": "Unresolved / Off-Device residual",
                "l0_block": "DAILY_LIVING_AND_RESIDUAL",
                "sort_order": "99",
                "cross_regime_comparability": "COMPARABLE_WITH_CAVEATS",
                "description": "Capacity minus accounted time.",
            },
        )
        legacy_rules = _read_csv(root / "legacy_activity_bridge.csv")
        label_maps = {
            (r["dimension"], r["legacy_value"]): r
            for r in _read_csv(root / "legacy_label_maps.csv")
        }
        current_rules = _read_csv(root / "current_activity_bridge.csv")
        for rule in legacy_rules + current_rules:
            group = rule["bridge_group"]
            if group not in groups:
                raise ValueError(f"Unknown bridge group in mapping table: {group}")
        return cls(groups, legacy_rules, label_maps, current_rules)

    # ---- legacy -----------------------------------------------------------
    def legacy_rule(self, category: str, subcategory: str, device: str) -> dict | None:
        candidates = [
            (category, subcategory, device),
            (category, subcategory, "*"),
            (category, "*", device),
            (category, "*", "*"),
        ]
        for cat, sub, dev in candidates:
            for rule in self.legacy_rules:
                if (
                    rule["legacy_category"] == cat
                    and rule["legacy_subcategory"] == sub
                    and rule["legacy_device"] == dev
                ):
                    return rule
        return None

    def label(self, dimension: str, value: str) -> tuple[str, str]:
        row = self.label_maps.get((dimension, value))
        if row is None:
            return "", UNMAPPED
        return row["canonical_value"], row["status"]

    # ---- current ----------------------------------------------------------
    def current_rule(self, source: str, category: str, subcategory: str) -> dict | None:
        candidates = [
            (source, category, subcategory),
            ("*", category, subcategory),
            (source, category, "*"),
            ("*", category, "*"),
        ]
        for src, cat, sub in candidates:
            for rule in self.current_rules:
                if (
                    rule["current_source"] == src
                    and rule["current_category"] == cat
                    and rule["current_subcategory"] == sub
                ):
                    return rule
        return None


def worst_status(*statuses: str) -> str:
    worst = max(statuses, key=lambda s: _STATUS_RANK.get(s, 2))
    return HIGH if worst in ("LABEL", "") else worst


# --------------------------------------------------------------------------
# Legacy normalization
# --------------------------------------------------------------------------

def _f(value) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def classify_repeats(cells: list[dict]) -> dict[str, str]:
    """Return record_id -> allocation basis for repeated-value rules."""
    counts = Counter(
        (c["sheet_month"], c["source_row"], c["raw_entry"])
        for c in cells
        if c["date_in_sheet_month"] == "Y" and _f(c["raw_duration_min"]) > 0
    )
    result: dict[str, str] = {}
    for c in cells:
        if c["date_in_sheet_month"] != "Y" or _f(c["raw_duration_min"]) <= 0:
            continue
        repeated = counts[(c["sheet_month"], c["source_row"], c["raw_entry"])] >= STANDARD_REPEAT_THRESHOLD
        if c["legacy_category"] == "Household / Chores":
            result[c["record_id"]] = "STANDARD_DAILY_ALLOCATION" if repeated else "ESTIMATE_NON_TEMPLATE"
        elif c["legacy_subcategory"] == "Vacations":
            result[c["record_id"]] = "BALANCING_FILL"
        elif repeated and c["row_block"] != "DEVICE_ROWS":
            result[c["record_id"]] = "REPEATED_ROUTINE_VALUE"
    return result


def normalize_legacy(cells: list[dict], tables: BridgeTables) -> list[dict]:
    """Build the historical normalized + bridged record set."""
    repeats = classify_repeats(cells)
    out: list[dict] = []
    for c in cells:
        rule = tables.legacy_rule(c["legacy_category"], c["legacy_subcategory"], c["legacy_device"])
        if rule is None:
            rule = {
                "bridge_group": "UNMAPPED", "bridge_status": UNMAPPED,
                "canonical_category": "", "canonical_subcategory": "", "category_status": UNMAPPED,
                "canonical_domain": "", "domain_status": UNMAPPED,
                "canonical_energy": "", "energy_status": UNMAPPED,
                "canonical_goal": "", "goal_status": UNMAPPED,
                "evidence_type": "MANUAL_ENTRY", "rationale": "No mapping rule.", "candidate_categories": "",
            }
        energy, energy_status = rule["canonical_energy"], rule["energy_status"]
        if energy == "LABEL":
            energy, energy_status = tables.label("energy", c["legacy_energy"])
        goal, goal_status = rule["canonical_goal"], rule["goal_status"]
        if goal == "LABEL":
            goal, goal_status = tables.label("goal", c["legacy_goal"])
        device, device_status = tables.label("device", c["legacy_device"])

        raw_min = _f(c["raw_duration_min"])
        numeric = c["raw_duration_min"] != ""
        if c["date_in_sheet_month"] != "Y":
            accounting_status = "EXCLUDED_OUT_OF_PERIOD"
            normalized = 0.0
        elif not numeric:
            accounting_status = "EXCLUDED_NON_NUMERIC"
            normalized = 0.0
        elif raw_min == 0:
            accounting_status = "ZERO_ENTRY"
            normalized = 0.0
        else:
            accounting_status = "INCLUDED"
            normalized = raw_min

        basis = repeats.get(c["record_id"])
        if basis is None:
            basis = "MANUAL_TRANSCRIPTION" if c["row_block"] == "DEVICE_ROWS" and c["legacy_device"] != "TV" else "MANUAL_ENTRY"
            if c["legacy_category"] == "Household / Chores":
                basis = "ESTIMATE_NON_TEMPLATE"
        mapping_status = worst_status(rule["bridge_status"])
        dims_status = worst_status(rule["category_status"], rule["domain_status"], energy_status, goal_status)

        review_reasons = []
        if mapping_status != HIGH:
            review_reasons.append("BRIDGE_GROUP_" + mapping_status)
        if dims_status != HIGH:
            review_reasons.append("DIMENSION_" + dims_status)
        if basis in ("STANDARD_DAILY_ALLOCATION", "BALANCING_FILL"):
            review_reasons.append(basis)
        if c["row_total_formula_present"] == "N":
            review_reasons.append("EXCLUDED_FROM_LEGACY_REPORT")
        if accounting_status != "INCLUDED":
            review_reasons = []  # nothing to review on zero / out-of-period cells

        out.append(
            {
                "record_id": c["record_id"],
                "regime": REGIME_LEGACY,
                "month": c["sheet_month"],
                "date": c["date"],
                "source": "LEGACY_WORKBOOK",
                "source_workbook": c["source_workbook"],
                "source_sheet": c["source_sheet"],
                "source_row": c["source_row"],
                "source_cell": c["source_cell"],
                "row_block": c["row_block"],
                "raw_entry": c["raw_entry"],
                "entry_type": c["entry_type"],
                "raw_cached_value": c["raw_cached_value"],
                "cache_check": c["cache_check"],
                "raw_duration_min": c["raw_duration_min"],
                "normalized_duration_min": round(normalized, 6),
                "legacy_category": c["legacy_category"],
                "legacy_subcategory": c["legacy_subcategory"],
                "legacy_subcategory_source": c["legacy_subcategory_source"],
                "legacy_energy": c["legacy_energy"],
                "legacy_goal": c["legacy_goal"],
                "legacy_domain": c["legacy_domain"],
                "legacy_device": c["legacy_device"],
                "legacy_time_of_day": c["legacy_time_of_day"],
                "bridge_group": rule["bridge_group"],
                "bridge_status": rule["bridge_status"],
                "canonical_category": rule["canonical_category"],
                "canonical_subcategory": rule["canonical_subcategory"],
                "category_status": rule["category_status"],
                "canonical_category_candidates": rule.get("candidate_categories") or "",
                "canonical_domain": rule["canonical_domain"],
                "domain_status": rule["domain_status"],
                "canonical_energy": energy,
                "energy_status": energy_status,
                "canonical_goal": goal,
                "goal_status": goal_status,
                "canonical_device": device,
                "device_status": device_status,
                "evidence_type": rule["evidence_type"],
                "allocation_basis": basis,
                "in_legacy_report": c["row_total_formula_present"],
                "mapping_status": mapping_status,
                "accounting_status": accounting_status,
                "review_status": "REVIEW_REQUIRED" if review_reasons else "NO_REVIEW_NEEDED",
                "review_reasons": ";".join(review_reasons),
            }
        )
    return out


# --------------------------------------------------------------------------
# Daily accounting (legacy)
# --------------------------------------------------------------------------

def legacy_daily(records: Iterable[dict], sheet_daily: Iterable[dict]) -> list[dict]:
    """Per-day legacy accounting with H-OVR-1 / H-DAY-1."""
    rec = defaultdict(float)
    core = defaultdict(float)
    std = defaultdict(float)
    fill = defaultdict(float)
    month_of = {}
    for r in records:
        if r["accounting_status"] != "INCLUDED":
            continue
        d = r["date"]
        v = r["normalized_duration_min"]
        rec[d] += v
        month_of[d] = r["month"]
        if r["row_block"] == "OFF_HOURS_CORE":
            core[d] += v
        if r["allocation_basis"] == "STANDARD_DAILY_ALLOCATION":
            std[d] += v
        if r["allocation_basis"] == "BALANCING_FILL":
            fill[d] += v
    sheet_total = {
        s["date"]: s["sheet_total_hours_cached"]
        for s in sheet_daily
        if s["date_in_sheet_month"] == "Y"
    }
    days = sorted(set(sheet_total) | set(rec))
    out = []
    for d in days:
        recorded = rec.get(d, 0.0)
        overlap = max(0.0, recorded - DAY_CAPACITY_MIN)
        accounted = recorded - overlap
        unresolved = DAY_CAPACITY_MIN - accounted
        flags = []
        if recorded > DAY_CAPACITY_MIN + 1e-9:
            flags.append("OVER_CAPACITY")
        if core.get(d, 0.0) <= 0:
            flags.append("INCOMPLETE_OFF_DEVICE")
        status = "NORMAL_TOLERANCE" if unresolved <= DAY_TOLERANCE_MIN + 1e-9 else "REVIEW"
        st = sheet_total.get(d, "")
        sheet_check = ""
        if st != "":
            sheet_check = "MATCH" if abs(float(st) * 60 - recorded) < 1e-3 else "MISMATCH"
        out.append(
            {
                "date": d,
                "month": month_of.get(d, d[:7]),
                "regime": REGIME_LEGACY,
                "capacity_min": DAY_CAPACITY_MIN,
                "recorded_min": round(recorded, 4),
                "overlap_min": round(overlap, 4),
                "accounted_min": round(accounted, 4),
                "unresolved_min": round(unresolved, 4),
                "standard_allocation_min": round(std.get(d, 0.0), 4),
                "balancing_fill_min": round(fill.get(d, 0.0), 4),
                "sheet_total_hours_cached": st,
                "sheet_total_check": sheet_check,
                "day_status": status,
                "day_flags": ";".join(flags),
            }
        )
    return out


def repeated_entry_register(records: Iterable[dict]) -> list[dict]:
    """Register of repeated identical non-zero entries (H-STD-1 / H-REP-1)."""
    groups = defaultdict(list)
    for r in records:
        if r["accounting_status"] != "INCLUDED" or r["row_block"] == "DEVICE_ROWS":
            continue
        groups[(r["month"], r["source_sheet"], r["source_workbook"], r["source_row"],
                r["legacy_category"], r["legacy_subcategory"], r["raw_entry"])].append(r)
    out = []
    for key, rows in sorted(groups.items()):
        if len(rows) < 2:
            continue
        month, sheet, book, row, cat, sub, entry = key
        out.append(
            {
                "month": month,
                "source_workbook": book,
                "source_sheet": sheet,
                "source_row": row,
                "legacy_category": cat,
                "legacy_subcategory": sub,
                "raw_entry": entry,
                "value_min": rows[0]["normalized_duration_min"],
                "occurrences": len(rows),
                "total_min": round(sum(r["normalized_duration_min"] for r in rows), 4),
                "classification": rows[0]["allocation_basis"]
                if rows[0]["allocation_basis"] in ("STANDARD_DAILY_ALLOCATION", "REPEATED_ROUTINE_VALUE", "BALANCING_FILL")
                else "REPEATED_BELOW_THRESHOLD",
                "first_cell": rows[0]["source_cell"],
                "dates": " ".join(sorted(r["date"] for r in rows)),
            }
        )
    return out


# --------------------------------------------------------------------------
# Current system (read-only)
# --------------------------------------------------------------------------

def current_device_class(device: str, source: str, subcategory: str,
                         laptop_device: str, desktop_device: str) -> str:
    if source == "Off-Device" or device == "Off-Device":
        return "RESIDUAL"
    if source == "AppleScreenTime":
        return "PHONE"
    if source == "Habit":
        return "TV" if subcategory == "TV Time tracking" else "OFF_DEVICE_RECORDED"
    if device == laptop_device:
        return "LAPTOP"
    if device == desktop_device:
        return "DESKTOP"
    if source == "ActivityWatch":
        return "COMPUTERS_TOTAL"
    return "UNMAPPED"


def bridge_current(rows: Iterable[dict], tables: BridgeTables, regime: str, period: str,
                   laptop_device: str, desktop_device: str) -> list[dict]:
    """Annotate current Final_Analysis rows with bridge groups (read-only)."""
    out = []
    for i, r in enumerate(rows, start=1):
        rule = tables.current_rule(r["Source"], r["Category"], r["Subcategory"])
        group = rule["bridge_group"] if rule else "UNMAPPED"
        status = rule["bridge_status"] if rule else UNMAPPED
        out.append(
            {
                "record_id": f"CURRENT|{period}|row{i}",
                "regime": regime,
                "month": r["Date"][:7],
                "date": r["Date"],
                "source": r["Source"],
                "device": r["Device"],
                "device_class": current_device_class(
                    r["Device"], r["Source"], r["Subcategory"], laptop_device, desktop_device
                ),
                "activity": r["Activity"],
                "category": r["Category"],
                "subcategory": r["Subcategory"],
                "domain": r["Domain"],
                "energy": r["Energy"],
                "goal": r["Goal"],
                "allocation_type": r["Allocation_Type"],
                "duration_min": float(r["Duration_min"]),
                "bridge_group": group,
                "bridge_status": status,
                "source_file": f"Final/{period}/Final_Analysis_{period}.csv",
                "source_row": i + 1,
            }
        )
    return out


CURRENT_PERIOD_FILES = (
    "Final_Analysis_{p}.csv",
    "Final_Time_Universe_Reconciliation_{p}.csv",
    "Final_Daily_Reconciliation_{p}.csv",
)


def read_current_period(final_dir: Path, period: str) -> tuple[list[dict], list[dict], dict[str, str]]:
    """Read Final_Analysis, daily time-universe rows and published day status."""
    analysis = _read_csv(final_dir / f"Final_Analysis_{period}.csv")
    universe = [
        r for r in _read_csv(final_dir / f"Final_Time_Universe_Reconciliation_{period}.csv")
        if r.get("Time_Bucket") == "Period Capacity"
    ]
    status = {
        r["Date"]: r["Status"]
        for r in _read_csv(final_dir / f"Final_Daily_Reconciliation_{period}.csv")
    }
    return analysis, universe, status


def current_daily(universe: Iterable[dict], regime: str, day_status: dict[str, str] | None = None) -> list[dict]:
    day_status = day_status or {}
    out = []
    for r in universe:
        cap = float(r["Capacity_min"])
        corrected = float(r["Corrected_Tracked_min"])
        unique = float(r["Unique_Tracked_min"])
        overlap = float(r["Overlap_min"])
        residual = float(r["Analytical_Off_Device_min"])
        flags = []
        if corrected > cap + 1e-9:
            flags.append("OVER_CAPACITY")
        out.append(
            {
                "date": r["Date"],
                "month": r["Date"][:7],
                "regime": regime,
                "capacity_min": cap,
                "recorded_min": round(corrected, 4),
                "overlap_min": round(overlap, 4),
                "accounted_min": round(unique, 4),
                "unresolved_min": round(residual, 4),
                "standard_allocation_min": 0.0,
                "balancing_fill_min": 0.0,
                "sheet_total_hours_cached": "",
                "sheet_total_check": "",
                "day_status": day_status.get(r["Date"], ""),
                "day_flags": ";".join(flags),
            }
        )
    return out


def calendar_capacity_min(month: str) -> float:
    year, mon = map(int, month.split("-"))
    return calendar.monthrange(year, mon)[1] * DAY_CAPACITY_MIN


__all__ = [
    "BridgeTables",
    "normalize_legacy",
    "legacy_daily",
    "repeated_entry_register",
    "bridge_current",
    "read_current_period",
    "current_daily",
    "calendar_capacity_min",
    "REGIME_LEGACY",
    "REGIME_TRANSITION",
    "REGIME_CURRENT",
]
