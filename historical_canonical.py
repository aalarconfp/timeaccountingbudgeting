# historical_canonical.py
"""Current-taxonomy (canonical) layer for historical reporting.

Translation chain (docs/HISTORICAL_ANNUAL_ANALYSIS.md §2):

    legacy source value -> mapping rules (config/historical_bridge/legacy_activity_bridge.csv,
    applied by historical_bridge.normalize_legacy) -> current canonical value -> aggregation -> report

This module does NOT hold a second mapping. It:

1. loads the authoritative current taxonomy (the same Dim_* files that
   final_analysis.py uses for the monthly reports);
2. validates that every canonical value in the bridge tables exists in that
   taxonomy (no invented category / subcategory / domain / energy / goal);
3. turns the normalized legacy records and the current Final rows into one
   record set expressed in current canonical fields, with the bridge group
   kept only as a diagnostic field.

Current records keep their own Final values unchanged. Legacy records carry
the canonical values and statuses produced by the bridge. The legacy
unresolved time (capacity − accounted) becomes Off-Device Life rows using the
same residual rule and the same Domain / Energy / Goal as the current
synthetic Off-Device Life rows (read from the current data, not hard-coded).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

OFF_DEVICE_CATEGORY = "Off-Device Life"
OFF_DEVICE_SUBCATEGORY = "Untracked Maintenance"

# Canonical status of a record's current category.
CAT_CURRENT = "CURRENT_NATIVE"          # current-system record (Final output)
CAT_HIGH = "HIGH_CONFIDENCE"
CAT_REVIEW = "REVIEW_REQUIRED"
CAT_MULTI = "MULTI_CATEGORY"            # one of several current categories; none asserted
CAT_UNMAPPED = "UNMAPPED"
CAT_RESIDUAL = "RESIDUAL_RULE"          # capacity − accounted, Off-Device Life rule
MAPPED_STATUSES = (CAT_CURRENT, CAT_HIGH, CAT_REVIEW, CAT_RESIDUAL)

UNMAPPED_VALUE = "(Unmapped legacy)"


# --------------------------------------------------------------------------
# Authoritative current taxonomy
# --------------------------------------------------------------------------

@dataclass
class CurrentTaxonomy:
    categories: list[str]                 # Dim_Category_Default order + Off-Device Life
    domains: list[str]
    energies: list[str]
    goals: list[str]
    subcategories: dict[str, set[str]] = field(default_factory=dict)
    category_defaults: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    source: str = ""

    def has_pair(self, category: str, subcategory: str) -> bool:
        return category in self.categories and (not subcategory or subcategory in self.subcategories.get(category, set()))


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sorted_dim(path: Path, column: str) -> list[str]:
    rows = _read_csv(path)
    return [r[column] for r in sorted(rows, key=lambda r: int(r.get("Sort_Order") or 0))]


def load_current_taxonomy(root: Path | None = None) -> CurrentTaxonomy:
    """Load the taxonomy used by final_analysis.py (single source of truth)."""
    import final_analysis as fa
    from config.taxonomy import CATEGORY_SUBCATEGORIES

    root = root or fa.TAXONOMY_ROOT
    default_rows = _read_csv(root / "Dim_Category_Default.csv")
    categories = [r["Canonical_Category"] for r in default_rows if r.get("Canonical_Category")]
    defaults = {r["Canonical_Category"]: (r["Domain"], r["Energy"], r["Goal"]) for r in default_rows}
    subs: dict[str, set[str]] = {c: set() for c in categories}
    for r in _read_csv(root / "Dim_Activity.csv"):
        cat = r.get("Canonical_Category", "")
        if cat and cat.upper() != "UNMAPPED":
            subs.setdefault(cat, set()).add(r["Activity_Name"])
    for cat, names in CATEGORY_SUBCATEGORIES.items():
        subs.setdefault(str(cat), set()).update(names)
    categories.append(OFF_DEVICE_CATEGORY)
    subs[OFF_DEVICE_CATEGORY] = {OFF_DEVICE_SUBCATEGORY}
    return CurrentTaxonomy(
        categories=categories,
        domains=_sorted_dim(root / "Dim_Domain.csv", "Domain"),
        energies=_sorted_dim(root / "Dim_Energy.csv", "Energy"),
        goals=_sorted_dim(root / "Dim_Goal.csv", "Goal"),
        subcategories=subs,
        category_defaults=defaults,
        source=str(root),
    )


def validate_bridge_against_taxonomy(legacy_rules: Iterable[dict], label_maps: Iterable[dict],
                                     taxonomy: CurrentTaxonomy) -> list[str]:
    """Every canonical value the bridge can emit must exist in the current taxonomy."""
    errors = []
    for r in legacy_rules:
        key = f"{r['legacy_category']}/{r['legacy_subcategory']}/{r['legacy_device']}"
        cat, sub, status = r["canonical_category"], r["canonical_subcategory"], r["category_status"]
        cands = [c for c in (r.get("candidate_categories") or "").split("|") if c]
        if cat and not taxonomy.has_pair(cat, sub):
            errors.append(f"{key}: '{cat} / {sub}' not in the current taxonomy")
        if status == CAT_MULTI:
            if cat or len(cands) < 2:
                errors.append(f"{key}: MULTI_CATEGORY needs >= 2 candidate_categories and no single category")
        elif cands:
            errors.append(f"{key}: candidate_categories only allowed with MULTI_CATEGORY")
        if status in (CAT_HIGH, CAT_REVIEW) and not cat:
            errors.append(f"{key}: {status} without a canonical category")
        for c in cands:
            if c not in taxonomy.categories:
                errors.append(f"{key}: candidate '{c}' not in the current taxonomy")
        for dim, allowed in (("domain", taxonomy.domains), ("energy", taxonomy.energies), ("goal", taxonomy.goals)):
            v = r[f"canonical_{dim}"]
            if v and v != "LABEL" and v not in allowed:
                errors.append(f"{key}: {dim} '{v}' not in the current taxonomy")
    for r in label_maps:
        allowed = {"energy": taxonomy.energies, "goal": taxonomy.goals}.get(r["dimension"])
        if allowed is not None and r["canonical_value"] and r["canonical_value"] not in allowed:
            errors.append(f"label map {r['dimension']}:{r['legacy_value']} -> '{r['canonical_value']}' not in the current taxonomy")
    return errors


# --------------------------------------------------------------------------
# Canonical records
# --------------------------------------------------------------------------

def _dim(value: str, status: str) -> tuple[str, str]:
    """Legacy dimension value usable in a current-taxonomy view."""
    if value and status in (CAT_HIGH, CAT_REVIEW):
        return value, status
    return "", status or CAT_UNMAPPED


def residual_profile(current_rows: Iterable[dict]) -> tuple[str, str, str]:
    """Domain / Energy / Goal of the current synthetic Off-Device Life rows."""
    seen = {(r["canonical_domain"], r["canonical_energy"], r["canonical_goal"])
            for r in current_rows if r["bridge_group"] == "RESIDUAL"}
    if len(seen) != 1:
        raise ValueError(f"Expected one Off-Device Life taxonomy profile in the current data, found {sorted(seen)}")
    return seen.pop()


def build_canonical_records(normalized: Iterable[dict], legacy_daily: Iterable[dict],
                            bridge_records: Iterable[dict]) -> list[dict]:
    """One record set in current canonical fields for every regime."""
    bridge_records = list(bridge_records)
    current = [r for r in bridge_records if r["regime"] != "LEGACY_MANUAL_TRACKING"]
    res_domain, res_energy, res_goal = residual_profile(current)
    out = []
    for r in normalized:
        if r["accounting_status"] != "INCLUDED":
            continue
        cat_status = r["category_status"]
        cat = r["canonical_category"] if cat_status in (CAT_HIGH, CAT_REVIEW) else ""
        sub = r["canonical_subcategory"] if cat else ""
        dom, dom_s = _dim(r["canonical_domain"], r["domain_status"])
        en, en_s = _dim(r["canonical_energy"], r["energy_status"])
        go, go_s = _dim(r["canonical_goal"], r["goal_status"])
        out.append({
            "regime": r["regime"], "month": r["month"], "date": r["date"],
            "device_class": r["canonical_device"] or "UNMAPPED",
            "category": cat, "subcategory": sub,
            "category_status": cat_status if cat_status in (CAT_HIGH, CAT_REVIEW, CAT_MULTI) else CAT_UNMAPPED,
            "candidate_categories": r.get("canonical_category_candidates", "") if cat_status == CAT_MULTI else "",
            "domain": dom, "domain_status": dom_s, "energy": en, "energy_status": en_s, "goal": go, "goal_status": go_s,
            "evidence_basis": r["allocation_basis"], "bridge_group": r["bridge_group"],
            "legacy_label": f"{r['legacy_category']} / {r['legacy_subcategory'] or '-'} / {r['legacy_device']}",
            "minutes": float(r["normalized_duration_min"]),
            "provenance": f"{r['source_workbook']}!{r['source_sheet']}!{r['source_cell']}",
        })
    for d in legacy_daily:
        v = float(d["unresolved_min"])
        if v <= 0:
            continue
        out.append({
            "regime": d["regime"], "month": d["month"], "date": d["date"], "device_class": "RESIDUAL",
            "category": OFF_DEVICE_CATEGORY, "subcategory": OFF_DEVICE_SUBCATEGORY, "category_status": CAT_RESIDUAL,
            "candidate_categories": "", "domain": res_domain, "domain_status": CAT_RESIDUAL,
            "energy": res_energy, "energy_status": CAT_RESIDUAL, "goal": res_goal, "goal_status": CAT_RESIDUAL,
            "evidence_basis": "Inferred Time Universe Residual", "bridge_group": "RESIDUAL",
            "legacy_label": "(capacity − accounted)", "minutes": v,
            "provenance": f"Historical_Legacy_Daily.csv#{d['date']}",
        })
    for r in current:
        residual = r["bridge_group"] == "RESIDUAL"
        out.append({
            "regime": r["regime"], "month": r["month"], "date": r["date"], "device_class": r["device_class"],
            "category": r["canonical_category"], "subcategory": r["canonical_subcategory"],
            "category_status": CAT_RESIDUAL if residual else CAT_CURRENT, "candidate_categories": "",
            "domain": r["canonical_domain"], "domain_status": CAT_CURRENT, "energy": r["canonical_energy"],
            "energy_status": CAT_CURRENT, "goal": r["canonical_goal"], "goal_status": CAT_CURRENT,
            "evidence_basis": r["evidence_basis"], "bridge_group": r["bridge_group"], "legacy_label": "",
            "minutes": float(r["duration_min"]), "provenance": r["provenance"],
        })
    return out


# --------------------------------------------------------------------------
# Current-taxonomy rollups and budget lines (defined only by current values)
# --------------------------------------------------------------------------

PF_EDU = ("Productivity & Finance", "Education")
DISCRETIONARY = ("Games", "Entertainment", "Social Networking")   # standard_report consumption proxy
IN_PERSON_SUBCATEGORIES = ("Family Time Tracking", "Social Time Tracking")


def candidates(rec: dict) -> tuple[str, ...]:
    return tuple(c for c in rec.get("candidate_categories", "").split("|") if c)


def is_discretionary(rec: dict) -> bool:
    """standard_report proxy: Games + Entertainment + Social Networking with Goal=Consumption.
    A MULTI_CATEGORY legacy record counts when all its candidates are proxy
    categories and its goal is Consumption."""
    cat = rec["category"]
    if cat in ("Games", "Entertainment"):
        return True
    if cat == "Social Networking":
        return rec["goal"] == "Consumption" and rec["subcategory"] not in IN_PERSON_SUBCATEGORIES
    cands = candidates(rec)
    return bool(cands) and set(cands) <= set(DISCRETIONARY) and rec["goal"] == "Consumption"


# Anchor activities expressed as current Category › Subcategory (exact equivalents).
ANCHOR_SUBCATEGORIES = {
    ("Health & Fitness", "Sleep"): "SLEEP",
    ("Health & Fitness", "Workout"): "WORKOUT",
    ("Health & Fitness", "Meditation"): "MEDITATION",
    ("Social Networking", "Family Time Tracking"): "FAMILY_TIME",
    ("Social Networking", "Social Time Tracking"): "FRIENDS_TIME",
    ("Travel", "Motorcycle Time"): "MOTORCYCLE",
    ("Information & Reading", "Read a book"): "READING_JOURNALING",
    ("Information & Reading", "Journaling"): "READING_JOURNALING",
}
ANCHOR_LABEL = {
    "SLEEP": "Health & Fitness › Sleep",
    "WORKOUT": "Health & Fitness › Workout",
    "MEDITATION": "Health & Fitness › Meditation",
    "FAMILY_TIME": "Social Networking › Family Time Tracking",
    "FRIENDS_TIME": "Social Networking › Social Time Tracking",
    "MOTORCYCLE": "Travel › Motorcycle Time",
    "READING_JOURNALING": "Information & Reading › Read a book + Journaling",
}


def anchor_of(rec: dict) -> str | None:
    a = ANCHOR_SUBCATEGORIES.get((rec["category"], rec["subcategory"]))
    if a is None and rec["regime"] == "LEGACY_MANUAL_TRACKING" and rec["category"] == "Information & Reading" \
            and not rec["subcategory"]:
        a = "READING_JOURNALING"  # legacy 'Reading Books/Journaling' row (both current subcategories)
    return a


# Budget lines: an exhaustive partition of analytical activity, first match wins.
BUDGET_LINE_ORDER = (
    "SLEEP", "HEALTH_PRACTICES", "HEALTH_OTHER", "OFF_DEVICE_LIFE", "IN_PERSON_RELATIONSHIPS",
    "PRODUCTIVITY_FINANCE_AND_EDUCATION", "INFORMATION_READING", "DISCRETIONARY_CONSUMPTION",
    "OTHER_SOCIAL_NETWORKING", "TRAVEL", "CREATIVITY", "UTILITIES", "SHOPPING_AND_NEWS", "UNMAPPED_LEGACY",
)
BUDGET_LINE_LABEL = {
    "SLEEP": "Health & Fitness › Sleep",
    "HEALTH_PRACTICES": "Health & Fitness › Workout + Meditation",
    "HEALTH_OTHER": "Health & Fitness › other subcategories",
    "OFF_DEVICE_LIFE": "Off-Device Life",
    "IN_PERSON_RELATIONSHIPS": "Social Networking › Family + Social Time Tracking (in person)",
    "PRODUCTIVITY_FINANCE_AND_EDUCATION": "Productivity & Finance + Education",
    "INFORMATION_READING": "Information & Reading",
    "DISCRETIONARY_CONSUMPTION": "Games + Entertainment + Social Networking (consumption / feeds)",
    "OTHER_SOCIAL_NETWORKING": "Social Networking › other (messaging, career networking)",
    "TRAVEL": "Travel",
    "CREATIVITY": "Creativity",
    "UTILITIES": "Utilities",
    "SHOPPING_AND_NEWS": "Shopping & Food + News",
    "UNMAPPED_LEGACY": "Unmapped legacy time (not budgetable)",
}


def budget_line(rec: dict) -> str:
    cat, sub = rec["category"], rec["subcategory"]
    cands = candidates(rec)
    if cat == "Health & Fitness":
        if sub == "Sleep":
            return "SLEEP"
        if sub in ("Workout", "Meditation"):
            return "HEALTH_PRACTICES"
        return "HEALTH_OTHER"
    if cat == OFF_DEVICE_CATEGORY:
        return "OFF_DEVICE_LIFE"
    if cat == "Social Networking" and sub in IN_PERSON_SUBCATEGORIES:
        return "IN_PERSON_RELATIONSHIPS"
    if cat in PF_EDU or (cands and set(cands) <= set(PF_EDU)):
        return "PRODUCTIVITY_FINANCE_AND_EDUCATION"
    if cat == "Information & Reading":
        return "INFORMATION_READING"
    # Unsplit legacy digital leisure belongs here whatever its legacy goal label:
    # every candidate category is part of this budget line.
    if is_discretionary(rec) or (cands and set(cands) <= set(DISCRETIONARY)):
        return "DISCRETIONARY_CONSUMPTION"
    if cat == "Social Networking":
        return "OTHER_SOCIAL_NETWORKING"
    if cat == "Travel":
        return "TRAVEL"
    if cat == "Creativity":
        return "CREATIVITY"
    if cat == "Utilities":
        return "UTILITIES"
    if cat in ("Shopping & Food", "News"):
        return "SHOPPING_AND_NEWS"
    return "UNMAPPED_LEGACY"
