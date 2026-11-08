# historical_proxy_allocation.py
"""Historical proxy allocation for Time Budgeting: worksheet, validation, apply.

Legacy records whose current category cannot be determined from the source
(unsplit / unmapped / review-required groups) can be allocated to current
categories with explicit, user-approved proxy percentages. Proxy allocations
are ESTIMATES, recorded in a separate layer; they never modify the legacy
workbooks, the normalized records, the bridge mapping tables, closed periods
or the authoritative annual outputs (which stay direct-mapped).

    python historical_proxy_allocation.py generate   # worksheet (CSV + XLSX) + Markdown summary
    python historical_proxy_allocation.py validate   # check percentages / approvals, compute allocated hours
    python historical_proxy_allocation.py apply      # approved allocations -> separate proxy-adjusted layer + baseline report

Outputs (git-ignored): output/Historical/Proxy_Allocation/
`generate` keeps user entries of an existing worksheet (matched by row_id)
and backs the previous file up before rewriting it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import historical_canonical as hc
import historical_monthly as hm
from config.historical import (
    ANNUAL_BASELINE_END_MONTH,
    ANNUAL_BASELINE_START_MONTH,
    BRIDGE_CONFIG_ROOT,
    HISTORICAL_OUTPUT_ROOT,
    LEGACY_INPUT_ROOT,
    LEGACY_SHEETS,
    LEGACY_WORKBOOKS,
    REGIME_CURRENT,
    REGIME_LEGACY,
)
from historical_legacy_loader import sha256_file

PROXY_ROOT = HISTORICAL_OUTPUT_ROOT / "Proxy_Allocation"
WORKSHEET_CSV = "Historical_Proxy_Allocation_Worksheet.csv"
WORKSHEET_XLSX = "Historical_Proxy_Allocation_Worksheet.xlsx"
SUMMARY_MD = "Historical_Proxy_Allocation_Summary.md"
EVIDENCE_CSV = "Historical_Proxy_Allocation_Evidence.csv"
VALIDATION_CSV = "Historical_Proxy_Allocation_Validation.csv"
PREVIEW_CSV = "Historical_Proxy_Allocation_Preview.csv"
APPLIED_DIR = "applied"

KEEP_UNMAPPED = "(keep unmapped)"
BASES = ("DIRECT_MAPPING", "USER_RECOLLECTION", "ANALYTICAL_PROXY")
STATUSES = ("PENDING", "APPROVED", "REJECTED")
CONFIDENCES = ("HIGH", "MEDIUM", "LOW")
USER_COLUMNS = ("user_pct", "user_rationale", "user_confidence", "allocation_basis", "approval_status")
PCT_TOLERANCE = 0.01
HEAVY_PROXY_SHARE = 0.30

COLUMNS = (
    "row_id", "decision_id", "decision_title", "group_scope", "legacy_category", "legacy_subcategory", "legacy_device",
    "legacy_energy_label", "legacy_goal_label", "evidence_class", "months_covered", "records", "historical_hours",
    "current_mapping", "target_dimension", "target_category", "target_subcategory", "target_value",
    "suggested_pct", "suggestion_basis", "suggestion_confidence", "legacy_definition", "suggestion_evidence", "source_cell_comments",
    "reference_current_same_device_pct",
    "user_pct", "user_rationale", "user_confidence", "allocation_basis", "approval_status",
    "allocated_hours", "group_user_pct_total", "validation_note", "user_confirmed_statement",
    "mapping_rule_id", "provenance", "record_filter",
)

# Categories-sheet definitions (legacy workbooks) and the source comments on
# the category labels; quoted as evidence, never as allocations.
LEGACY_DEFINITION = {
    "Work / Study": "Projects, meetings, research (2025); + side income business (2026). Name says work AND study.",
    "Learning / Skill-building": "Language apps, online courses, books.",
    "Personal Admin": "Email, banking, personal finance (& investing, 2026), shopping, habit trackers.",
    "Entertainment": "Streaming, gaming, browsing, social media.",
    "Communication / Social": "Messaging.",
    "Health & Wellness": "Sleep, Fitness, meditation (and prayer, vacations — 2026).",
    "Hobbies / Creative": "Guitar, Motorcycle, Nature.",
    "Relationships / Social": "Family Dinner, Gatherings, Friends.",
    "Household / Chores": "Cleaning, Cooking, Showering, Eating.",
}


# --------------------------------------------------------------------------
# Decision groups (exclusive filters over legacy records, first match wins)
# --------------------------------------------------------------------------

def T(category="", subcategory="", value="", dimension="category"):
    return {"target_dimension": dimension, "target_category": category, "target_subcategory": subcategory, "target_value": value}


DIG_LEISURE = [T("Entertainment"), T("Games"), T("Social Networking"), T("Information & Reading")]
ADMIN = [T("Productivity & Finance"), T("Shopping & Food"), T("Utilities")]
PF_EDU = [T("Productivity & Finance"), T("Education")]
ENERGY = [T(value=e, dimension="energy") for e in ("Recovery", "Active", "Passive", "Shallow", "Deep")]


def F(cat, sub=None, device=None, energy=None, goal=None, basis=None, comment=None):
    f = {"legacy_category": cat}
    if sub is not None:
        f["legacy_subcategory"] = sub
    if device is not None:
        f["legacy_device"] = device
    if energy is not None:
        f["legacy_energy"] = energy
    if goal is not None:
        f["legacy_goal"] = goal
    if basis is not None:
        f["allocation_basis_in"] = basis
    if comment is not None:
        f["comment_contains"] = comment
    return f


TEMPLATE = ["STANDARD_DAILY_ALLOCATION"]
NON_TEMPLATE = ["ESTIMATE_NON_TEMPLATE"]
HH_NO_NAP = "Eating, bathroom, shower"
HH_NAP = "Eating, bathroom, shower, nap"

USER_STATEMENTS = {
    "WORK_STUDY": "User (2026-10-13): Work / Study was a mixture of work and study; the percentage split is not yet known.",
    "ENTERTAINMENT": "User (2026-10-13): device Entertainment has no reliable split yet.",
    "ADMIN": "User (2026-10-13): Personal Admin has no reliable category split yet.",
    "HOUSEHOLD": "User (2026-10-13): Household template data was a mixture of estimates and actual time.",
    "RECOVERY": "User (2026-10-13): Recovery / Rest mostly referred to sleep, but not necessarily entirely.",
    "FRIENDS_PC": "User (2026-10-13): Friends on PC involved both gaming and social interaction.",
    "VACATION": "User (2026-10-13): Vacation entries represented actual vacation / leisure time, subject to verification of their evidence basis.",
    "VIDEOS": "User (2026-10-13): Videos meant creating videos; map to Creativity.",
}


def decision_specs() -> list[dict]:
    """Ordered decision groups. Record-level groups come before the broad ones."""
    S = []

    def add(did, title, flt, targets, statement="", suggested=None, basis="", conf="", evidence="", approved=None):
        S.append({"decision_id": did, "title": title, "filter": flt, "targets": targets, "statement": statement,
                  "suggested": suggested or {}, "suggestion_basis": basis, "suggestion_confidence": conf,
                  "suggestion_evidence": evidence, "approved": approved or {}})

    add("P16", "Personal Admin (off-device) cells annotated 'Bad Sleep'", F("Personal Admin", device="Off-Device", comment="Bad Sleep"),
        [T("Utilities", "Personal Time Tracking"), T("Health & Fitness", "Sleep")], USER_STATEMENTS["ADMIN"],
        evidence="Two cells carry the source comment 'Bad Sleep' although they sit in the off-device Personal Admin row.")
    add("P01", "Work / Study — Laptop, Deep Work / High-Value rows", F("Work / Study", device="Laptop", energy="Deep Work"), PF_EDU, USER_STATEMENTS["WORK_STUDY"])
    add("P02", "Work / Study — Laptop, Shallow Work / Maintenance rows", F("Work / Study", device="Laptop", energy="Shallow Work"), PF_EDU, USER_STATEMENTS["WORK_STUDY"])
    add("P03", "Work / Study — PC Desktop, Deep Work / High-Value rows", F("Work / Study", device="PC Desktop", energy="Deep Work"), PF_EDU, USER_STATEMENTS["WORK_STUDY"])
    add("P04", "Work / Study — PC Desktop, Shallow Work / Maintenance rows", F("Work / Study", device="PC Desktop", energy="Shallow Work"), PF_EDU, USER_STATEMENTS["WORK_STUDY"])
    add("P05", "Work / Study — Phone", F("Work / Study", device="Phone"), PF_EDU, USER_STATEMENTS["WORK_STUDY"])
    add("P06", "Entertainment — Phone", F("Entertainment", device="Phone"), DIG_LEISURE, USER_STATEMENTS["ENTERTAINMENT"],
        evidence="Source comment on the legacy category label: 'Phone: Information & Reading, Social Media, Entertainment' (Apple Screen Time buckets transcribed into this row).")
    add("P07", "Entertainment — Laptop, Passive / Low-Value rows", F("Entertainment", device="Laptop", energy="Passive"), DIG_LEISURE, USER_STATEMENTS["ENTERTAINMENT"])
    add("P08", "Entertainment — Laptop, Shallow Work / Maintenance rows", F("Entertainment", device="Laptop", energy="Shallow Work"), DIG_LEISURE, USER_STATEMENTS["ENTERTAINMENT"],
        evidence="Separate legacy row labelled Shallow Work / Maintenance (not Passive / Low-Value) — its meaning may differ from the main Entertainment row.")
    add("P09", "Entertainment — PC Desktop, Passive / Low-Value rows", F("Entertainment", device="PC Desktop", energy="Passive"), DIG_LEISURE, USER_STATEMENTS["ENTERTAINMENT"])
    add("P10", "Entertainment — PC Desktop, Shallow Work / Maintenance rows", F("Entertainment", device="PC Desktop", energy="Shallow Work"), DIG_LEISURE, USER_STATEMENTS["ENTERTAINMENT"],
        evidence="Separate legacy row labelled Shallow Work / Maintenance (not Passive / Low-Value).")
    add("P11", "Entertainment — TV (console gaming share?)", F("Entertainment", device="TV"),
        [T("Entertainment", "TV Time tracking"), T("Games", "Console Time Tracking")],
        evidence="Currently mapped exactly to Entertainment › TV Time tracking. Source cell comments on the TV row mention 'Xbox', so part of the time may be console gaming (current: Games › Console Time Tracking).")
    add("P12", "Personal Admin — Phone", F("Personal Admin", device="Phone"),
        [T("Productivity & Finance"), T("Utilities"), T("Travel"), T("Creativity"), T("Shopping & Food")], USER_STATEMENTS["ADMIN"],
        evidence="Source comment on the legacy category label: 'Phone: Productivity & finance, Travel, Utilities, Creativity' (Apple Screen Time buckets transcribed into this row); the definition also lists shopping.")
    add("P13", "Personal Admin — Laptop", F("Personal Admin", device="Laptop"), ADMIN, USER_STATEMENTS["ADMIN"])
    add("P14", "Personal Admin — PC Desktop", F("Personal Admin", device="PC Desktop"), ADMIN, USER_STATEMENTS["ADMIN"])
    add("P15", "Personal Admin — Off-Device (errands)", F("Personal Admin", device="Off-Device"),
        [T("Utilities", "Personal Time Tracking"), T("Productivity & Finance"), T("Shopping & Food")], USER_STATEMENTS["ADMIN"],
        suggested={0: 100}, basis="DIRECT_MAPPING", conf="MEDIUM",
        evidence="Existing mapping: the current Habit tracker for off-device admin / errands is Utilities › Personal Time Tracking. Cell comments describe errands (fine, notary, purchases).")
    add("P17", "Communication / Social — Phone", F("Communication / Social", device="Phone"),
        [T("Social Networking", "Messaging"), T("Social Networking", "Social Feeds")],
        suggested={0: 100}, basis="ANALYTICAL_PROXY", conf="MEDIUM",
        evidence="Source comment on the legacy category label: 'Phone: WhatsApp' — the phone row transcribed messaging.")
    add("P18", "Communication / Social — Laptop", F("Communication / Social", device="Laptop"),
        [T("Social Networking", "Messaging"), T("Social Networking", "Social Feeds"), T("Social Networking", "Meetings")])
    add("P19", "Communication / Social — PC Desktop", F("Communication / Social", device="PC Desktop"),
        [T("Social Networking", "Messaging"), T("Social Networking", "Social Feeds"), T("Social Networking", "Meetings")])
    add("P20", "Household / Chores — template entries, 'Eating, bathroom, shower' (Sep–Oct 2025)", F("Household / Chores", HH_NO_NAP, basis=TEMPLATE),
        [T(hc.OFF_DEVICE_CATEGORY, hc.OFF_DEVICE_SUBCATEGORY)], USER_STATEMENTS["HOUSEHOLD"],
        suggested={0: 100}, basis="DIRECT_MAPPING", conf="MEDIUM",
        evidence="Template value repeated ≥ 5 times in the month (rule H-STD-1). No nap in the label: only Off-Device Life applies. Stays flagged as an estimate.")
    add("P21", "Household / Chores — template entries, 'Eating, bathroom, shower, nap' (Nov 2025 →)", F("Household / Chores", HH_NAP, basis=TEMPLATE),
        [T(hc.OFF_DEVICE_CATEGORY, hc.OFF_DEVICE_SUBCATEGORY), T("Health & Fitness", "Sleep")], USER_STATEMENTS["HOUSEHOLD"],
        evidence="Template value; from Nov 2025 the row label includes naps. Enter the share of these minutes that was napping (→ Health & Fitness › Sleep); the rest stays Off-Device Life. Remains an estimate either way.")
    add("P22", "Household / Chores — non-template entries, 'Eating, bathroom, shower'", F("Household / Chores", HH_NO_NAP, basis=NON_TEMPLATE),
        [T(hc.OFF_DEVICE_CATEGORY, hc.OFF_DEVICE_SUBCATEGORY)], USER_STATEMENTS["HOUSEHOLD"],
        suggested={0: 100}, basis="DIRECT_MAPPING", conf="MEDIUM",
        evidence="Values that do not repeat the template (more likely actual entries). No nap in the label.")
    add("P23", "Household / Chores — non-template entries, 'Eating, bathroom, shower, nap'", F("Household / Chores", HH_NAP, basis=NON_TEMPLATE),
        [T(hc.OFF_DEVICE_CATEGORY, hc.OFF_DEVICE_SUBCATEGORY), T("Health & Fitness", "Sleep")], USER_STATEMENTS["HOUSEHOLD"],
        evidence="Values that do not repeat the template (more likely actual entries, e.g. 90–200 min). Enter the nap share.")
    add("P24", "Recovery / Rest energy — Learning / Skill-building on Phone", F("Learning / Skill-building", sub="", device="Phone"),
        ENERGY, USER_STATEMENTS["RECOVERY"],
        evidence="Legacy energy label 'Recovery / Rest' on phone learning apps; no exact activity equivalent fixes the energy. (Category Education — see P30.)")
    add("P25", "Recovery / Rest energy — Health & Wellness on Phone", F("Health & Wellness", sub="", device="Phone"),
        ENERGY, USER_STATEMENTS["RECOVERY"],
        evidence="Legacy energy label 'Recovery / Rest' on phone health apps; category is Health & Fitness (exact). Only the energy is open.")
    add("P26", "Relationships / Social — Friends on PC Desktop", F("Relationships / Social", "Friends", "PC Desktop"),
        [T("Games"), T("Social Networking")], USER_STATEMENTS["FRIENDS_PC"],
        evidence="Source cell comments name co-op games ('Lethal Company', 'Rivals'); Report 1 comment: 'Friends & Social gaming'. Concurrent gaming + talking is ONE block of time: a split attributes it, it does not add time. The social nature is also kept in the legacy domain (Relational).")
    add("P27", "Reading Books / Journaling (combined legacy row)", F("Learning / Skill-building", "Reading Books/Journaling", "Off-Device"),
        [T("Information & Reading", "Read a book"), T("Information & Reading", "Journaling")],
        evidence="Category is exact (Information & Reading); the row combines two current subcategories (Read a book = Deep, Journaling = Shallow).")
    add("P28", "Vacation entries ('Vacations' row)", F("Health & Wellness", "Vacations", "Off-Device"),
        [T(hc.OFF_DEVICE_CATEGORY, hc.OFF_DEVICE_SUBCATEGORY), T("Travel"), T(KEEP_UNMAPPED)], USER_STATEMENTS["VACATION"],
        evidence="")  # evidence filled from the data (round values, day totals) at generation
    add("P29", "Hobbies / Creative — Videos", F("Hobbies / Creative", "Videos", "Off-Device"),
        [T("Creativity")], USER_STATEMENTS["VIDEOS"],
        evidence="User confirmed the meaning (creating videos). Creativity exists in the current taxonomy; no current subcategory for off-device video creation, so the subcategory is left blank ('Media Editing' is an app-level subcategory).",
        approved={0: 100})
    add("P30", "Learning / Skill-building — Phone (category)", F("Learning / Skill-building", sub="", device="Phone"),
        [T("Education"), T("Information & Reading")], suggested={0: 100}, basis="DIRECT_MAPPING", conf="HIGH",
        evidence="Source comment on the phone Learning row: 'Phone category: Education' (Apple Screen Time Education bucket).")
    add("P31", "Learning / Skill-building — Laptop / PC Desktop (comment 'Hobbie')", F("Learning / Skill-building", sub=""),
        [T("Education"), T("Creativity"), T("Information & Reading")],
        evidence="Source comments on these rows say 'Hobbie' (hobby learning). Very small amount of time.")
    return S


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, rows: list[dict], fields=COLUMNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def load_comments(legacy_dir: Path) -> dict[str, str]:
    """record_id -> source cell comment (read-only; workbooks are hashed before/after by the caller)."""
    import warnings

    import openpyxl
    warnings.filterwarnings("ignore")
    code = {s.workbook: s.workbook_code for s in LEGACY_SHEETS}
    out = {}
    for name in LEGACY_WORKBOOKS:
        path = legacy_dir / name
        if not path.exists():
            continue
        wb = openpyxl.load_workbook(path, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for c in row:
                    if c.comment:
                        raw = c.comment.text
                        if raw.lstrip().startswith("[Threaded comment]") and "Comment:" in raw:
                            raw = raw.split("Comment:", 1)[1]  # drop Excel's threaded-comment boilerplate
                        text = " ".join(raw.replace("USUARIO:", "").replace("User:", "").split())
                        out[f"{code[name]}|{ws.title}|{c.coordinate}"] = text[:160]
        wb.close()
    return out


def matches(rec: dict, flt: dict, comments: dict) -> bool:
    for k, v in flt.items():
        if k == "record_ids":
            if rec["record_id"] not in v:
                return False
        elif k == "allocation_basis_in":
            if rec["allocation_basis"] not in v:
                return False
        elif k == "comment_contains":
            if v.lower() not in comments.get(rec["record_id"], "").lower():
                return False
        elif rec.get(k) != v:
            return False
    return True


def spec_dimension(spec: dict) -> str:
    return spec["targets"][0]["target_dimension"]


def assign_groups(records: list[dict], specs: list[dict], comments: dict) -> dict[str, dict[str, str]]:
    """dimension ('category' / 'energy') -> record_id -> decision_id.

    Within a dimension the first matching spec wins, so each record belongs to
    at most one category decision and at most one energy decision."""
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for r in records:
        if r["accounting_status"] != "INCLUDED":
            continue
        for s in specs:
            dim = spec_dimension(s)
            if r["record_id"] not in out[dim] and matches(r, s["filter"], comments):
                out[dim][r["record_id"]] = s["decision_id"]
    return out


# --------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------

def reference_split(bridge: list[dict], device: str, targets: list[dict]) -> str:
    dev = {"Phone": "PHONE", "Laptop": "LAPTOP", "PC Desktop": "DESKTOP"}.get(device)
    if not dev or targets[0]["target_dimension"] != "category":
        return ""
    use_sub = any(t["target_subcategory"] for t in targets)
    tot = defaultdict(float)
    for r in bridge:
        if r["month"] != "2026-09" or r["device_class"] != dev or r["canonical_subcategory"] == "Uncategorized":
            continue
        for t in targets:
            if r["canonical_category"] == t["target_category"] and (not use_sub or r["canonical_subcategory"] == t["target_subcategory"]):
                tot[(t["target_category"], t["target_subcategory"])] += float(r["duration_min"])
    s = sum(tot.values())
    if not s:
        return ""
    parts = ", ".join(f"{c}{' › ' + sub if sub else ''} {100 * v / s:.0f}%" for (c, sub), v in sorted(tot.items(), key=lambda x: -x[1]))
    note = " (phone 'Social Networking' bucket also contains messaging)" if dev == "PHONE" and any(t["target_category"] == "Social Networking" for t in targets) else ""
    return f"Sep 2026, same device, among these targets: {parts}{note}. Reference only — current capture and behaviour may differ."


def generate(hist_dir: Path = HISTORICAL_OUTPUT_ROOT, out_dir: Path = PROXY_ROOT, legacy_dir: Path = LEGACY_INPUT_ROOT) -> dict:
    taxonomy = hc.load_current_taxonomy()
    norm = _read_csv(hist_dir / "Historical_Normalized_Records.csv")
    bridge = _read_csv(hist_dir / "Historical_Bridge_Records.csv")
    rules = {(r["legacy_category"], r["legacy_subcategory"], r["legacy_device"]): r for r in _read_csv(BRIDGE_CONFIG_ROOT / "legacy_activity_bridge.csv")}
    before = {w: sha256_file(legacy_dir / w) for w in LEGACY_WORKBOOKS if (legacy_dir / w).exists()}
    comments = load_comments(legacy_dir)
    after = {w: sha256_file(legacy_dir / w) for w in LEGACY_WORKBOOKS if (legacy_dir / w).exists()}
    if before != after:
        raise RuntimeError("Legacy workbooks changed while reading comments — aborting.")
    specs = decision_specs()
    groups = assign_groups(norm, specs, comments)
    by_id = {r["record_id"]: r for r in norm}
    members = defaultdict(list)
    for dim_groups in groups.values():
        for rid, did in dim_groups.items():
            members[did].append(by_id[rid])

    # Vacation evidence from the data.
    daily = defaultdict(float)
    for r in norm:
        if r["accounting_status"] == "INCLUDED":
            daily[r["date"]] += float(r["normalized_duration_min"])
    vac = members.get("P28", [])
    if vac:
        vals = sorted({int(float(r["normalized_duration_min"])) for r in vac})
        tots = [daily[r["date"]] for r in vac]
        vac_ev = (f"{len(vac)} entries on {', '.join(sorted({r['date'] for r in vac}))}; values {vals} min (round numbers); "
                  f"day totals incl. the entry {min(tots):.0f}–{max(tots):.0f} min, not exactly 1,440 → entered amounts, not a mechanical "
                  "24-h balance. Activity within the vacation is not recorded. Re-labelling these minutes adds no time.")
        next(s for s in specs if s["decision_id"] == "P28")["suggestion_evidence"] = vac_ev

    rows, evidence_rows = [], []
    for s in specs:
        recs = members.get(s["decision_id"], [])
        if not recs:
            continue
        mins = sum(float(r["normalized_duration_min"]) for r in recs)
        months = sorted({r["month"] for r in recs})
        r0 = recs[0]
        rule_key = next((k for k in ((r0["legacy_category"], r0["legacy_subcategory"], r0["legacy_device"]), (r0["legacy_category"], r0["legacy_subcategory"], "*"),
                                     (r0["legacy_category"], "*", r0["legacy_device"]), (r0["legacy_category"], "*", "*")) if k in rules), None)
        current = (r0["canonical_category"] + (f" › {r0['canonical_subcategory']}" if r0["canonical_subcategory"] else "")
                   if r0["category_status"] in ("HIGH_CONFIDENCE", "REVIEW_REQUIRED") else
                   ("unsplit: " + r0["canonical_category_candidates"].replace("|", " / ") if r0["canonical_category_candidates"] else "unmapped"))
        current += f" [{r0['category_status']}]; energy {r0['canonical_energy'] or 'unmapped'} [{r0['energy_status']}]"
        cmts = sorted({comments[r["record_id"]] for r in recs if r["record_id"] in comments})
        labels = sorted({(r["legacy_energy"], r["legacy_goal"]) for r in recs})
        scope = f"{r0['legacy_category']} | {r0['legacy_subcategory'] or '(device row)'} | {r0['legacy_device']}"
        if len({r['legacy_device'] for r in recs}) > 1:
            scope = f"{r0['legacy_category']} | {r0['legacy_subcategory'] or '(device row)'} | {', '.join(sorted({r['legacy_device'] for r in recs}))}"
        flt = dict(s["filter"])
        if "comment_contains" in flt:
            flt = {"record_ids": sorted(r["record_id"] for r in recs)}
        ref = reference_split(bridge, r0["legacy_device"], s["targets"])
        evidence_rows.append({"decision_id": s["decision_id"], "legacy_definition": LEGACY_DEFINITION.get(r0["legacy_category"], ""),
                              "cell_comments": " ; ".join(cmts), "reference_current_same_device": ref,
                              "mapping_rationale": rules[rule_key]["rationale"] if rule_key else ""})
        for i, t in enumerate(s["targets"]):
            sug = s["suggested"].get(i, "")
            approved = s["approved"].get(i)
            rid = f"{s['decision_id']}:{t['target_dimension']}:{t['target_category'] or t['target_value']}:{t['target_subcategory']}"
            row = {
                "row_id": rid, "decision_id": s["decision_id"], "decision_title": s["title"], "group_scope": scope,
                "legacy_category": r0["legacy_category"], "legacy_subcategory": r0["legacy_subcategory"],
                "legacy_device": ", ".join(sorted({r["legacy_device"] for r in recs})),
                "legacy_energy_label": "; ".join(sorted({e for e, _ in labels})), "legacy_goal_label": "; ".join(sorted({g for _, g in labels})),
                "evidence_class": "; ".join(sorted({r["allocation_basis"] for r in recs})),
                "months_covered": f"{months[0]}..{months[-1]} ({len(months)})", "records": len(recs), "historical_hours": round(mins / 60, 2),
                "current_mapping": current, **t,
                "suggested_pct": sug if sug != "" else ("" if s["suggested"] else ""),
                "suggestion_basis": s["suggestion_basis"] if sug != "" else "",
                "suggestion_confidence": s["suggestion_confidence"] if sug != "" else "",
                "legacy_definition": LEGACY_DEFINITION.get(r0["legacy_category"], ""),
                "suggestion_evidence": s["suggestion_evidence"],
                "source_cell_comments": " ; ".join(cmts),
                "reference_current_same_device_pct": ref,
                "user_pct": approved if approved is not None else "",
                "user_rationale": s["statement"] if approved is not None else "",
                "user_confidence": "HIGH" if approved is not None else "",
                "allocation_basis": "USER_RECOLLECTION" if approved is not None else "",
                "approval_status": "APPROVED" if approved is not None else "PENDING",
                "user_confirmed_statement": s["statement"],
                "mapping_rule_id": f"legacy_activity_bridge:{'|'.join(rule_key)}" if rule_key else "",
                "provenance": f"Historical_Normalized_Records.csv — {len(recs)} cells, sheets {', '.join(sorted({r['source_sheet'] for r in recs}))} "
                              f"({', '.join(sorted({r['source_workbook'] for r in recs}))})",
                "record_filter": json.dumps(flt, ensure_ascii=False, sort_keys=True),
            }
            rows.append(row)

    # Coverage: every legacy minute not mapped with high confidence (category or energy) must be in a group.
    uncovered = defaultdict(float)
    for r in norm:
        if r["accounting_status"] != "INCLUDED":
            continue
        cat_open = r["category_status"] != "HIGH_CONFIDENCE" and r["record_id"] not in groups["category"]
        # Household energy follows its category decision (Off-Device Life / Sleep carry their own energy).
        en_open = (r["energy_status"] != "HIGH_CONFIDENCE" and r["legacy_category"] != "Household / Chores"
                   and r["record_id"] not in groups["energy"] and r["record_id"] not in groups["category"])
        if cat_open or en_open:
            uncovered[(r["legacy_category"], r["legacy_subcategory"], r["legacy_device"], r["category_status"], r["energy_status"])] += float(r["normalized_duration_min"])

    out_dir.mkdir(parents=True, exist_ok=True)
    existing = None
    for path in (out_dir / WORKSHEET_XLSX, out_dir / WORKSHEET_CSV):
        if path.exists():
            existing = path if existing is None or path.stat().st_mtime > existing.stat().st_mtime else existing
    carried = 0
    if existing is not None:
        old = {r["row_id"]: r for r in read_worksheet(existing)}
        backup = out_dir / "_backup" / datetime.now().strftime("%Y%m%dT%H%M%S")
        backup.mkdir(parents=True, exist_ok=True)
        for path in (out_dir / WORKSHEET_XLSX, out_dir / WORKSHEET_CSV):
            if path.exists():
                shutil.copy2(path, backup / path.name)
        for row in rows:
            o = old.get(row["row_id"])
            if o and any(str(o.get(c, "")).strip() for c in USER_COLUMNS if c != "approval_status") or (o and o.get("approval_status") not in ("", "PENDING", None)):
                for c in USER_COLUMNS:
                    row[c] = o.get(c, "")
                carried += 1
    compute(rows)
    _write_csv(out_dir / WORKSHEET_CSV, rows)
    write_xlsx(out_dir / WORKSHEET_XLSX, rows, evidence_rows)
    _write_csv(out_dir / EVIDENCE_CSV, evidence_rows, ("decision_id", "legacy_definition", "cell_comments", "reference_current_same_device", "mapping_rationale"))
    canonical_dep = category_dependency(hist_dir, rows, taxonomy)
    summary = render_summary(rows, evidence_rows, uncovered, canonical_dep, carried)
    (out_dir / SUMMARY_MD).write_text(summary, encoding="utf-8")
    manifest = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "normalized_records_sha256": sha256_file(hist_dir / "Historical_Normalized_Records.csv"),
                "legacy_workbooks_unchanged": before == after, "decisions": len({r["decision_id"] for r in rows}), "rows": len(rows),
                "user_entries_carried_over": carried, "uncovered_minutes": round(sum(uncovered.values()), 2)}
    (out_dir / "Historical_Proxy_Allocation_Manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"rows": rows, "evidence": evidence_rows, "uncovered": uncovered, "manifest": manifest, "dependency": canonical_dep}


def category_dependency(hist_dir: Path, rows: list[dict], taxonomy) -> list[dict]:
    """Legacy minutes per current category: directly mapped vs potentially affected by open decisions."""
    norm = _read_csv(hist_dir / "Historical_Normalized_Records.csv")
    direct = defaultdict(float)
    for r in norm:
        if r["accounting_status"] == "INCLUDED" and r["category_status"] in ("HIGH_CONFIDENCE", "REVIEW_REQUIRED"):
            direct[r["canonical_category"]] += float(r["normalized_duration_min"])
    exposure = defaultdict(float)
    for r in rows:
        if r["target_dimension"] != "category" or r["target_category"] == KEEP_UNMAPPED:
            continue
        if r["current_mapping"].split(" [")[0].split(" › ")[0] == r["target_category"]:
            continue  # already counted as direct legacy time of this category
        exposure[r["target_category"]] += float(r["historical_hours"]) * 60
    out = []
    for c in taxonomy.categories:
        d, e = direct.get(c, 0.0), exposure.get(c, 0.0)
        share = e / (d + e) if d + e else 0.0
        out.append({"category": c, "legacy_direct_hours": round(d / 60, 1), "hours_in_open_decisions_naming_it": round(e / 60, 1),
                    "max_proxy_share_pct": round(100 * share, 1),
                    "potential_dependence": "POTENTIALLY_HEAVY" if share >= 0.5 else ("POTENTIALLY_MODERATE" if share >= 0.2 else "LOW")})
    return out


# --------------------------------------------------------------------------
# Worksheet IO, computation, validation
# --------------------------------------------------------------------------

def read_worksheet(path: Path) -> list[dict]:
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=False)
        ws = wb["Worksheet"]
        header = [c.value for c in ws[1]]
        out = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(v not in (None, "") for v in row):
                continue
            d = {h: ("" if v is None else v) for h, v in zip(header, row)}
            for k in ("allocated_hours", "group_user_pct_total"):  # formulas: recomputed here
                d[k] = ""
            out.append({k: (str(v) if not isinstance(v, str) else v) for k, v in d.items()})
        return out
    return _read_csv(path)


def _num(v):
    v = str(v).strip().replace("%", "").replace(",", ".")
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return "ERR"


def compute(rows: list[dict]) -> list[dict]:
    """Fill allocated_hours, group totals and validation notes in place; return group results."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["decision_id"]].append(r)
    results = []
    for did, rs in groups.items():
        pcts = [_num(r["user_pct"]) for r in rs]
        errors = []
        if any(p == "ERR" for p in pcts):
            errors.append("non-numeric user_pct")
        nums = [p for p in pcts if p not in (None, "ERR")]
        if any(p < 0 or p > 100 for p in nums):
            errors.append("user_pct outside 0–100")
        total = sum(nums) if nums else None
        status = {r["approval_status"].strip().upper() or "PENDING" for r in rs}
        if len(status) > 1:
            errors.append(f"mixed approval_status {sorted(status)} — use one status per decision")
        st = sorted(status)[0] if len(status) == 1 else "MIXED"
        if st not in STATUSES and st != "MIXED":
            errors.append(f"unknown approval_status '{st}'")
        if nums and abs(total - 100) > PCT_TOLERANCE:
            errors.append(f"user_pct sums to {total:g}%, not 100%")
        if st == "APPROVED":
            if not nums:
                errors.append("APPROVED without user_pct")
            if any(p is None for p in pcts) and nums:
                pass  # blank = 0% is allowed when the others sum to 100
            if not any(str(r["user_rationale"]).strip() for r in rs):
                errors.append("APPROVED without user_rationale")
            conf = {str(r["user_confidence"]).strip().upper() for r in rs if str(r["user_pct"]).strip() not in ("", "0")}
            if not conf or not conf <= set(CONFIDENCES):
                errors.append("APPROVED needs user_confidence HIGH / MEDIUM / LOW on allocated rows")
            bases = {str(r["allocation_basis"]).strip().upper() for r in rs if str(r["user_pct"]).strip() not in ("", "0")}
            if not bases <= set(BASES):
                errors.append(f"allocation_basis must be one of {', '.join(BASES)}")
        state = ("ERROR" if errors else "APPROVED_READY" if st == "APPROVED" else
                 "REJECTED" if st == "REJECTED" else "PENDING_COMPLETE" if nums and abs(total - 100) <= PCT_TOLERANCE else "PENDING_MISSING_ALLOCATION")
        for r, p in zip(rs, pcts):
            r["allocated_hours"] = round(float(r["historical_hours"]) * p / 100, 2) if p not in (None, "ERR") else ""
            r["group_user_pct_total"] = "" if total is None else round(total, 2)
            r["validation_note"] = state + (": " + "; ".join(errors) if errors else "")
        results.append({"decision_id": did, "decision_title": rs[0]["decision_title"], "historical_hours": rs[0]["historical_hours"],
                        "approval_status": st, "user_pct_total": "" if total is None else round(total, 2), "state": state,
                        "errors": "; ".join(errors),
                        "suggested_available": "Y" if any(str(r["suggested_pct"]).strip() for r in rs) else "N"})
    return results


def validate(out_dir: Path = PROXY_ROOT, worksheet: Path | None = None, hist_dir: Path = HISTORICAL_OUTPUT_ROOT) -> dict:
    taxonomy = hc.load_current_taxonomy()
    path = worksheet or newest_worksheet(out_dir)
    rows = read_worksheet(path)
    results = compute(rows)
    errors = []
    for r in rows:
        if r["target_dimension"] == "category" and r["target_category"] != KEEP_UNMAPPED and not taxonomy.has_pair(r["target_category"], r["target_subcategory"]):
            errors.append(f"{r['row_id']}: target not in the current taxonomy")
        if r["target_dimension"] == "energy" and r["target_value"] not in taxonomy.energies:
            errors.append(f"{r['row_id']}: energy not in the current taxonomy")
    manifest_path = out_dir / "Historical_Proxy_Allocation_Manifest.json"
    stale = False
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        stale = m.get("normalized_records_sha256") != sha256_file(hist_dir / "Historical_Normalized_Records.csv")
    if stale:
        errors.append("Historical normalized records changed since the worksheet was generated — run `generate` (user entries are kept).")
    _write_csv(out_dir / VALIDATION_CSV, results, ("decision_id", "decision_title", "historical_hours", "approval_status", "user_pct_total", "state", "errors", "suggested_available"))
    _write_csv(out_dir / PREVIEW_CSV, rows)
    return {"worksheet": path, "results": results, "errors": errors, "rows": rows}


def newest_worksheet(out_dir: Path) -> Path:
    cands = [p for p in (out_dir / WORKSHEET_XLSX, out_dir / WORKSHEET_CSV) if p.exists()]
    if not cands:
        raise FileNotFoundError(f"No worksheet in {out_dir}; run `generate` first.")
    return max(cands, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------
# apply (separate, non-authoritative proxy-adjusted layer)
# --------------------------------------------------------------------------

def apply(out_dir: Path = PROXY_ROOT, worksheet: Path | None = None, hist_dir: Path = HISTORICAL_OUTPUT_ROOT) -> dict:
    """Allocate APPROVED decisions only; everything else keeps its direct mapping or stays unresolved.

    Writes a separate, non-authoritative layer under <out_dir>/applied/."""
    import final_analysis as fa
    import historical_annual as ha

    v = validate(out_dir, worksheet, hist_dir)
    bad = [r for r in v["results"] if r["state"] == "ERROR" and r["approval_status"] == "APPROVED"]
    if v["errors"] or bad:
        raise RuntimeError("Worksheet does not validate: " + "; ".join(v["errors"] + [f"{r['decision_id']}: {r['errors']}" for r in bad]))
    taxonomy = hc.load_current_taxonomy()
    activity = {f"{m.category}|{act}": m for (src, act), m in fa.load_taxonomy().items() if src != "CategoryDefault"}

    # Decision definitions from the worksheet itself (worksheet order = priority).
    specs, seen = [], set()
    for r in v["rows"]:
        if r["decision_id"] not in seen:
            seen.add(r["decision_id"])
            specs.append({"decision_id": r["decision_id"], "filter": json.loads(r["record_filter"]),
                          "targets": [{"target_dimension": r["target_dimension"]}]})
    approved = defaultdict(list)
    for r in v["rows"]:
        if r["approval_status"].strip().upper() == "APPROVED" and _num(r["user_pct"]) not in (None, "ERR", 0):
            approved[r["decision_id"]].append(r)

    norm = _read_csv(hist_dir / "Historical_Normalized_Records.csv")
    legacy_daily = _read_csv(hist_dir / "Historical_Legacy_Daily.csv")
    bridge = _read_csv(hist_dir / "Historical_Bridge_Records.csv")
    canonical = hc.build_canonical_records(norm, legacy_daily, bridge)
    canon = {r["provenance"]: r for r in canonical if r["regime"] == REGIME_LEGACY and r["category_status"] != hc.CAT_RESIDUAL}
    res_dom, res_en, res_goal = hc.residual_profile([r for r in bridge if r["regime"] != REGIME_LEGACY])
    groups = assign_groups(norm, specs, {})

    layer = []
    for rec in norm:
        if rec["accounting_status"] != "INCLUDED":
            continue
        c = canon[f"{rec['source_workbook']}!{rec['source_sheet']}!{rec['source_cell']}"]
        mins = float(rec["normalized_duration_min"])
        cat_did = groups["category"].get(rec["record_id"], "")
        en_did = groups["energy"].get(rec["record_id"], "")
        base = {"record_id": rec["record_id"], "month": rec["month"], "date": rec["date"], "legacy_category": rec["legacy_category"],
                "legacy_subcategory": rec["legacy_subcategory"], "legacy_device": rec["legacy_device"], "original_minutes": mins,
                "category_decision": cat_did, "energy_decision": en_did, "evidence_class": rec["allocation_basis"]}
        # 1) category pieces
        if approved.get(cat_did):
            pieces = []
            for p in approved[cat_did]:
                cat, sub = ("", "") if p["target_category"] == KEEP_UNMAPPED else (p["target_category"], p["target_subcategory"])
                dom, en, goal = c["domain"], c["energy"], c["goal"]
                act = activity.get(f"{cat}|{sub}") if sub else None
                if cat == hc.OFF_DEVICE_CATEGORY:
                    dom, en, goal = res_dom, res_en, res_goal
                elif act is not None:
                    dom, en, goal = act.domain, act.energy, act.goal
                elif cat in taxonomy.category_defaults:
                    dom = taxonomy.category_defaults[cat][0]
                pieces.append({"target_category": cat, "target_subcategory": sub, "domain": dom, "energy": en, "goal": goal,
                               "candidate_categories": "" if cat else c["candidate_categories"], "share": _num(p["user_pct"]) / 100,
                               "allocation_basis": p["allocation_basis"].strip().upper(), "confidence": p["user_confidence"].strip().upper(),
                               "approval_status": "APPROVED", "rationale": p["user_rationale"]})
        else:
            pieces = [{"target_category": c["category"], "target_subcategory": c["subcategory"], "domain": c["domain"], "energy": c["energy"],
                       "goal": c["goal"], "candidate_categories": c["candidate_categories"], "share": 1.0,
                       "allocation_basis": "DIRECT_MAPPING" if c["category"] else "UNRESOLVED", "confidence": c["category_status"],
                       "approval_status": "PENDING" if cat_did else "N/A", "rationale": ""}]
        # 2) energy split (applies to every category piece of the record)
        if approved.get(en_did):
            split = []
            for piece in pieces:
                for p in approved[en_did]:
                    split.append({**piece, "energy": p["target_value"], "share": piece["share"] * _num(p["user_pct"]) / 100,
                                  "energy_basis": p["allocation_basis"].strip().upper()})
            pieces = split
        for piece in pieces:
            layer.append({**base, **{k: v for k, v in piece.items() if k != "share"}, "allocated_pct": round(100 * piece["share"], 6),
                          "allocated_minutes": round(mins * piece["share"], 6), "energy_basis": piece.get("energy_basis", "")})

    per_rec = defaultdict(float)
    for x in layer:
        per_rec[x["record_id"]] += x["allocated_minutes"]
    orig = {r["record_id"]: float(r["normalized_duration_min"]) for r in norm if r["accounting_status"] == "INCLUDED"}
    conservation = max(abs(per_rec[k] - orig[k]) for k in orig)

    months = hm.month_range()
    days = {m: ha.month_days(m) for m in months}
    base_months = [m for m in months if ANNUAL_BASELINE_START_MONTH <= m <= ANNUAL_BASELINE_END_MONTH]
    UNRES = "(unresolved legacy)"
    direct = defaultdict(lambda: defaultdict(float))
    adjusted = defaultdict(lambda: defaultdict(float))
    proxy = defaultdict(lambda: defaultdict(float))
    daily_adj = defaultdict(lambda: defaultdict(float))
    for rc in canonical:
        direct[rc["category"] or UNRES][rc["month"]] += rc["minutes"]
        if rc["regime"] != REGIME_LEGACY or rc["category_status"] == hc.CAT_RESIDUAL:
            adjusted[rc["category"]][rc["month"]] += rc["minutes"]
            daily_adj[rc["date"]][rc["category"]] += rc["minutes"]
    for x in layer:
        cat = x["target_category"] or UNRES
        adjusted[cat][x["month"]] += x["allocated_minutes"]
        daily_adj[x["date"]][cat] += x["allocated_minutes"]
        if x["allocation_basis"] in ("USER_RECOLLECTION", "ANALYTICAL_PROXY"):
            proxy[cat][x["month"]] += x["allocated_minutes"]
    rows = []
    legacy_dates = sorted(d for d in daily_adj if d[:7] in base_months)
    for cat in taxonomy.categories + [UNRES]:
        dvals = [direct[cat][m] / days[m] for m in base_months]
        avals = [adjusted[cat][m] / days[m] for m in base_months]
        ptot = sum(proxy[cat][m] for m in base_months)
        atot = sum(adjusted[cat][m] for m in base_months)
        share = ptot / atot if atot else 0.0
        wk = ha.mean([daily_adj[d].get(cat, 0.0) for d in legacy_dates if date.fromisoformat(d).weekday() < 5])
        we = ha.mean([daily_adj[d].get(cat, 0.0) for d in legacy_dates if date.fromisoformat(d).weekday() >= 5])
        rows.append({"category": cat, "direct_baseline_median_min_per_day": ha.r1(ha.quantile(dvals, 0.5)),
                     "adjusted_baseline_median_min_per_day": ha.r1(ha.quantile(avals, 0.5)),
                     "adjusted_p25": ha.r1(ha.quantile(avals, 0.25)), "adjusted_p75": ha.r1(ha.quantile(avals, 0.75)),
                     "adjusted_cv": ha.r2(ha.cv(avals)), "adjusted_weekday_min_per_day": ha.r1(wk), "adjusted_weekend_min_per_day": ha.r1(we),
                     "current_2026_09_min_per_day": ha.r1(adjusted[cat]["2026-09"] / days["2026-09"]),
                     "proxy_share_of_adjusted_baseline_pct": ha.r1(100 * share),
                     "proxy_dependence": "HEAVY" if share >= HEAVY_PROXY_SHARE else ("SOME" if share > 0 else "NONE"),
                     "note": "Estimate: includes user-approved proxy allocations." if share else "Direct mapping only."})
    monthly = [{"month": m, "category": cat, "direct_minutes": round(direct[cat][m], 3), "adjusted_minutes": round(adjusted[cat][m], 3),
                "proxy_minutes": round(proxy[cat][m], 3)} for m in months for cat in taxonomy.categories + [UNRES]]
    tot_err = max(abs(sum(direct[c][m] for c in list(direct)) - sum(adjusted[c][m] for c in list(adjusted))) for m in months)
    outside = sorted({x["target_category"] for x in layer if x["target_category"] and x["target_category"] not in taxonomy.categories})
    applied = out_dir / APPLIED_DIR
    applied.mkdir(parents=True, exist_ok=True)
    layer_fields = ("record_id", "month", "date", "legacy_category", "legacy_subcategory", "legacy_device", "original_minutes",
                    "category_decision", "energy_decision", "evidence_class", "target_category", "target_subcategory", "domain", "energy", "goal",
                    "candidate_categories", "allocated_pct", "allocated_minutes", "allocation_basis", "energy_basis", "confidence",
                    "approval_status", "rationale")
    _write_csv(applied / "Historical_Proxy_Allocation_Layer.csv", layer, layer_fields)
    _write_csv(applied / "Historical_Proxy_Adjusted_Category_Baseline.csv", rows, list(rows[0]))
    _write_csv(applied / "Historical_Proxy_Adjusted_Monthly.csv", monthly, list(monthly[0]))
    checks = [
        {"check": "Every legacy minute allocated exactly once (Σ pieces = source cell)", "result": "PASS" if conservation < 1e-6 else "FAIL", "detail": f"max deviation {conservation:.2e} min"},
        {"check": "Monthly totals conserved (Σ direct = Σ adjusted, incl. unresolved)", "result": "PASS" if tot_err < 0.01 else "FAIL", "detail": f"max deviation {tot_err:.4f} min"},
        {"check": "Only APPROVED decisions allocated", "result": "PASS", "detail": f"approved: {', '.join(sorted(approved)) or 'none'}"},
        {"check": "Allocation targets in the current taxonomy", "result": "PASS" if not outside else "FAIL", "detail": ", ".join(outside) or "ok"},
    ]
    _write_csv(applied / "Historical_Proxy_Apply_Validation.csv", checks, ("check", "result", "detail"))
    (applied / "Historical_Proxy_Adjusted_Baseline_Report.md").write_text(render_applied(rows, checks, approved, v["results"]), encoding="utf-8")
    return {"layer": layer, "baseline": rows, "monthly": monthly, "checks": checks}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _t(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(v).replace("|", "/").replace("\n", " ") for v in r) + " |")
    return "\n".join(out)


def render_summary(rows, evidence_rows, uncovered, dependency, carried) -> str:
    groups = defaultdict(list)
    for r in rows:
        groups[r["decision_id"]].append(r)
    ev = {e["decision_id"]: e for e in evidence_rows}
    L = ["# Historical Proxy Allocation — review summary", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} by `historical_proxy_allocation.py generate`. "
         "Private (git-ignored). **Nothing here is applied**: the authoritative annual outputs stay direct-mapped. Proxy allocations, once "
         "approved, are estimates in a separate layer — never directly observed facts.", "",
         "## How to fill in the worksheet", "",
         f"1. Open `{WORKSHEET_XLSX}` (recommended: live totals, drop-down lists, yellow = editable) or `{WORKSHEET_CSV}`.",
         "2. Each decision has one row per candidate target. Enter `user_pct` (0–100) on the rows of a decision so that they **sum to 100**; "
         "blank rows count as 0%. `suggested_pct` is only a starting point where the evidence supports one — copy it into `user_pct` "
         "only if you agree.",
         "3. Write your recollection in `user_rationale`, choose `user_confidence` (HIGH / MEDIUM / LOW) and `allocation_basis` "
         "(USER_RECOLLECTION for your own estimate, ANALYTICAL_PROXY if you accept an evidence-based suggestion, DIRECT_MAPPING if you confirm the existing mapping).",
         "4. Set `approval_status` to APPROVED on **all rows of the decision** when you are done (or REJECTED to keep today's direct mapping / unresolved state). "
         "PENDING decisions are never applied.",
         "5. Run `python historical_proxy_allocation.py validate` — it checks sums, approvals and targets and writes the allocated hours to "
         f"`{PREVIEW_CSV}` and a per-decision status to `{VALIDATION_CSV}`.",
         "6. When you are satisfied: `python historical_proxy_allocation.py apply` (separate proxy-adjusted layer and baseline report; the authoritative annual reports are not touched).", "",
         f"Re-running `generate` keeps your entries (matched by `row_id`) and backs up the previous worksheet. User entries carried over this run: {carried}.", "",
         "## Decisions", "",
         _t(["ID", "Decision", "Hours", "Targets", "Suggestion", "Status"],
            [[did, rs[0]["decision_title"], rs[0]["historical_hours"],
              " / ".join((r["target_category"] or r["target_value"]) + (f" › {r['target_subcategory']}" if r["target_subcategory"] else "") for r in rs),
              ", ".join(f"{r['suggested_pct']}% {(r['target_category'] or r['target_value'])}{' › ' + r['target_subcategory'] if r['target_subcategory'] else ''}" for r in rs if str(r["suggested_pct"]).strip())
              or ("approved: " + ", ".join(f"{r['user_pct']}% {r['target_category']}" for r in rs if str(r["user_pct"]).strip()) if rs[0]["approval_status"] == "APPROVED" else "— (your estimate)"),
              rs[0]["validation_note"].split(":")[0]] for did, rs in groups.items()]), ""]
    for did, rs in groups.items():
        r0 = rs[0]
        e = ev.get(did, {})
        L += [f"### {did} — {r0['decision_title']}", "",
              f"- **Scope:** {r0['group_scope']} · evidence class {r0['evidence_class']} · legacy labels {r0['legacy_energy_label']} / {r0['legacy_goal_label']} · {r0['months_covered']} · {r0['records']} cells · **{r0['historical_hours']} h**.",
              f"- **Current treatment:** {r0['current_mapping']}.",
              f"- **Legacy definition:** {e.get('legacy_definition') or '—'}"]
        if r0["user_confirmed_statement"]:
            L.append(f"- **Your statement:** {r0['user_confirmed_statement']}")
        if e.get("cell_comments"):
            L.append(f"- **Source cell comments (workbook notes on these cells):** {e['cell_comments']}")
        if r0["suggestion_evidence"]:
            L.append(f"- **Evidence:** {r0['suggestion_evidence']}")
        if r0["reference_current_same_device_pct"]:
            L.append(f"- **Current-system reference (not a suggestion):** {r0['reference_current_same_device_pct']}")
        sug = [r for r in rs if str(r["suggested_pct"]).strip()]
        if sug:
            L.append(f"- **Suggested starting point:** " + ", ".join(f"{r['suggested_pct']}% {(r['target_category'] or r['target_value'])}{' › ' + r['target_subcategory'] if r['target_subcategory'] else ''}" for r in sug)
                     + f" ({sug[0]['suggestion_basis']}, {sug[0]['suggestion_confidence']} confidence) — not applied until you approve it.")
        elif r0["approval_status"] != "APPROVED":
            L.append("- **Suggested starting point:** none — the evidence does not support one; please estimate.")
        if any(str(r["user_pct"]).strip() for r in rs):
            L.append("- **Entered:** " + ", ".join(f"{r['user_pct']}% {(r['target_category'] or r['target_value'])} → {r['allocated_hours']} h" for r in rs if str(r["user_pct"]).strip())
                     + f" · status {r0['approval_status']}")
        L.append("")
    L += ["## Which current categories depend on these decisions", "",
          "Legacy hours already mapped directly to the category vs hours in open decisions that could move time INTO it (upper bound: every such hour "
          "allocated to this category; hours already mapped to it are not counted twice). POTENTIALLY_HEAVY = the legacy baseline could rest mostly on "
          "proxy assumptions. The actual proxy share is computed by `apply` from the approved percentages.", "",
          _t(["Current category", "Legacy direct h", "h that could move in", "Max proxy share", "Potential dependence"],
             [[d["category"], d["legacy_direct_hours"], d["hours_in_open_decisions_naming_it"], f"{d['max_proxy_share_pct']}%", d["potential_dependence"]] for d in dependency]), "",
          "## Coverage", ""]
    if uncovered:
        L.append(_t(["Legacy key (category / subcategory / device / category status / energy status)", "Hours"], [[" / ".join(k), round(v / 60, 1)] for k, v in sorted(uncovered.items())]))
    else:
        L.append("Every legacy record whose category or energy is not mapped with high confidence belongs to exactly one decision (household energy follows its category decision).")
    L += ["", "## Safeguards", "",
          "- The legacy workbooks are read only (SHA-256 checked before and after reading comments); normalized records, mapping tables, closed periods and the annual outputs are not modified.",
          "- Allocations re-attribute existing minutes: Σ allocated minutes per source cell = its original minutes; nothing is added or double-counted.",
          "- Proxy-adjusted results keep both the allocation-adjusted estimate and the original direct amount, with the proxy share per category.",
          "- Historical averages are references, not approved targets.", ""]
    return "\n".join(L)


def render_applied(rows, checks, approved, results) -> str:
    L = ["# Proxy-adjusted historical baseline (estimate)", "",
         f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} by `historical_proxy_allocation.py apply`. "
         "**Estimate layer** — includes user-approved proxy allocations; the authoritative annual outputs remain direct-mapped. "
         f"Approved decisions: {', '.join(sorted(approved)) or 'none'}. Legacy baseline window {ANNUAL_BASELINE_START_MONTH}..{ANNUAL_BASELINE_END_MONTH}; min per calendar day.", "",
         _t(["Current category", "Direct median", "Adjusted median", "Adjusted P25–P75", "CV", "Weekday", "Weekend", "Sep 26", "Proxy share", "Dependence"],
            [[r["category"], r["direct_baseline_median_min_per_day"], r["adjusted_baseline_median_min_per_day"], f"{r['adjusted_p25']}–{r['adjusted_p75']}",
              r["adjusted_cv"], r["adjusted_weekday_min_per_day"], r["adjusted_weekend_min_per_day"], r["current_2026_09_min_per_day"],
              f"{r['proxy_share_of_adjusted_baseline_pct']}%", r["proxy_dependence"]] for r in rows]), "",
         "Pending decisions (not applied): " + (", ".join(r["decision_id"] for r in results if r["approval_status"] != "APPROVED") or "none"), "",
         _t(["Check", "Result", "Detail"], [[c["check"], c["result"], c["detail"]] for c in checks]), ""]
    return "\n".join(L)


def write_xlsx(path: Path, rows: list[dict], evidence_rows: list[dict]) -> None:
    import openpyxl
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = openpyxl.Workbook()
    ins = wb.active
    ins.title = "Instructions"
    for line in (
        "Historical Proxy Allocation Worksheet — estimates for Time Budgeting (not observed facts).",
        "Edit only the yellow columns on the 'Worksheet' sheet: user_pct, user_rationale, user_confidence, allocation_basis, approval_status.",
        "For each decision_id, user_pct of its rows must sum to 100 (blank = 0). group_user_pct_total shows the live sum; it turns red when it is not 100.",
        "suggested_pct is a starting point only where evidence supports it; copy it to user_pct only if you agree.",
        "Set approval_status = APPROVED on all rows of a decision when final; PENDING decisions are never applied; REJECTED keeps today's treatment.",
        "Then run: python historical_proxy_allocation.py validate   (and later: apply).",
        "Do not edit row_id or record_filter — they link each row to the source records.",
    ):
        ins.append([line])
    ins.column_dimensions["A"].width = 140
    ws = wb.create_sheet("Worksheet")
    cols = list(COLUMNS)
    ws.append(cols)
    idx = {c: i + 1 for i, c in enumerate(cols)}
    L = openpyxl.utils.get_column_letter
    for n, r in enumerate(rows, start=2):
        vals = []
        for c in cols:
            v = r.get(c, "")
            if c in ("historical_hours", "records") or (c in ("suggested_pct", "user_pct") and str(v).strip() != ""):
                try:
                    v = float(v)
                except ValueError:
                    pass
            vals.append(v)
        ws.append(vals)
        h, p, d = L(idx["historical_hours"]), L(idx["user_pct"]), L(idx["decision_id"])
        ws.cell(n, idx["allocated_hours"]).value = f'=IF({p}{n}="","",ROUND({h}{n}*{p}{n}/100,2))'
        ws.cell(n, idx["group_user_pct_total"]).value = f'=SUMIF(${d}$2:${d}${len(rows) + 1},{d}{n},${p}$2:${p}${len(rows) + 1})'
    yellow = PatternFill("solid", fgColor="FFF2CC")
    for c in USER_COLUMNS:
        for n in range(2, len(rows) + 2):
            ws.cell(n, idx[c]).fill = yellow
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    end = len(rows) + 1
    dv_pct = DataValidation(type="decimal", operator="between", formula1="0", formula2="100", allow_blank=True,
                            error="Enter a percentage between 0 and 100.")
    dv_conf = DataValidation(type="list", formula1='"HIGH,MEDIUM,LOW"', allow_blank=True)
    dv_basis = DataValidation(type="list", formula1=f'"{",".join(BASES)}"', allow_blank=True)
    dv_status = DataValidation(type="list", formula1=f'"{",".join(STATUSES)}"', allow_blank=False)
    for dv, c in ((dv_pct, "user_pct"), (dv_conf, "user_confidence"), (dv_basis, "allocation_basis"), (dv_status, "approval_status")):
        ws.add_data_validation(dv)
        dv.add(f"{L(idx[c])}2:{L(idx[c])}{end}")
    g = L(idx["group_user_pct_total"])
    ws.conditional_formatting.add(f"{g}2:{g}{end}", FormulaRule(formula=[f'AND({g}2<>"",{g}2<>0,ABS({g}2-100)>0.01)'], fill=PatternFill("solid", fgColor="F4CCCC")))
    widths = {"row_id": 22, "decision_title": 44, "group_scope": 34, "current_mapping": 40, "suggestion_evidence": 70,
              "reference_current_same_device_pct": 50, "user_rationale": 40, "user_confirmed_statement": 50, "provenance": 40, "record_filter": 30}
    for c, i in idx.items():
        ws.column_dimensions[L(i)].width = widths.get(c, 14)
    ws.freeze_panes = ws.cell(2, idx["target_dimension"])
    evs = wb.create_sheet("Evidence")
    evs.append(["decision_id", "legacy_definition", "cell_comments", "reference_current_same_device", "mapping_rationale"])
    for e in evidence_rows:
        evs.append([e["decision_id"], e["legacy_definition"], e["cell_comments"], e["reference_current_same_device"], e["mapping_rationale"]])
    for col, w in zip("ABCDE", (10, 50, 70, 70, 80)):
        evs.column_dimensions[col].width = w
    wb.save(path)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("generate", "validate", "apply"))
    parser.add_argument("--worksheet", type=Path, default=None, help="Worksheet to read (.xlsx or .csv); default: the most recently modified one.")
    parser.add_argument("--out-dir", type=Path, default=PROXY_ROOT)
    args = parser.parse_args(argv)
    if args.command == "generate":
        res = generate(out_dir=args.out_dir)
        m = res["manifest"]
        print(f"Worksheet: {args.out_dir / WORKSHEET_XLSX} (+ .csv); summary: {args.out_dir / SUMMARY_MD}")
        print(f"{m['decisions']} decisions, {m['rows']} rows; user entries carried over: {m['user_entries_carried_over']}; "
              f"uncovered legacy minutes: {m['uncovered_minutes']}; workbooks unchanged: {m['legacy_workbooks_unchanged']}")
        return 0 if not res["uncovered"] else 1
    if args.command == "validate":
        res = validate(args.out_dir, args.worksheet)
        print(f"Worksheet read: {res['worksheet']}")
        for r in res["results"]:
            print(f"  {r['decision_id']:4} {r['state']:28} {r['historical_hours']:>8} h  {r['errors']}")
        for e in res["errors"]:
            print(f"[FAIL] {e}")
        return 1 if res["errors"] or any(r["state"] == "ERROR" for r in res["results"]) else 0
    res = apply(args.out_dir, args.worksheet)
    for c in res["checks"]:
        print(f"[{c['result']}] {c['check']} — {c['detail']}")
    return 1 if any(c["result"] == "FAIL" for c in res["checks"]) else 0


if __name__ == "__main__":
    sys.exit(main())
