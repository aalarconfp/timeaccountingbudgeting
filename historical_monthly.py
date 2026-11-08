# historical_monthly.py
"""Monthly, month-over-month and period analysis across methodology regimes.

Input: bridged legacy records + daily accounting (historical_bridge.py) and
bridged current-system records + daily accounting (August 2026 transition,
September 2026 closed accounting).

Nothing here changes a number in the source layers. Every metric carries a
comparability status and every month-over-month change is classified as
BEHAVIORAL_CANDIDATE, POSSIBLE_METHODOLOGICAL, METHODOLOGICAL_BREAK or
NOT_COMPARABLE (rules in docs/HISTORICAL_BRIDGE.md, section 'Change
classification').
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from config.historical import (
    ANALYSIS_END_MONTH,
    ANALYSIS_START_MONTH,
    PERIOD_LABEL,
    REGIME_CURRENT,
    REGIME_LEGACY,
    REGIME_NO_DATA,
    REGIME_TRANSITION,
)
from historical_bridge import DAY_CAPACITY_MIN, calendar_capacity_min

COMPARABLE = "COMPARABLE"
WITH_CAVEATS = "COMPARABLE_WITH_CAVEATS"
LEGACY_ONLY = "LEGACY_ONLY"
CURRENT_ONLY = "CURRENT_ONLY"
NOT_COMPARABLE = "NOT_COMPARABLE"
WITHIN_LEGACY = "COMPARABLE_WITHIN_LEGACY_ONLY"

GROUP_ORDER = (
    "WORK_STUDY", "LEARNING", "PERSONAL_ADMIN", "COMMUNICATION", "ENTERTAINMENT",
    "DIGITAL_SOCIAL_UNSPLIT", "HEALTH_WELLNESS", "RELATIONSHIPS_SOCIAL",
    "HOBBIES_CREATIVE", "HOUSEHOLD_DAILY_LIVING", "UNSPECIFIED_VACATION",
    "UTILITIES_SYSTEM", "UNCLASSIFIED", "UNMAPPED",
)
BLOCK_OF = {
    "WORK_STUDY": "PRODUCTIVE", "LEARNING": "PRODUCTIVE", "PERSONAL_ADMIN": "PRODUCTIVE",
    "COMMUNICATION": "DIGITAL_LEISURE_AND_SOCIAL", "ENTERTAINMENT": "DIGITAL_LEISURE_AND_SOCIAL",
    "DIGITAL_SOCIAL_UNSPLIT": "DIGITAL_LEISURE_AND_SOCIAL",
    "HEALTH_WELLNESS": "HEALTH", "RELATIONSHIPS_SOCIAL": "SOCIAL_IN_PERSON",
    "HOBBIES_CREATIVE": "HOBBIES", "HOUSEHOLD_DAILY_LIVING": "DAILY_LIVING_AND_RESIDUAL",
    "UNSPECIFIED_VACATION": "DAILY_LIVING_AND_RESIDUAL", "UTILITIES_SYSTEM": "UNCLASSIFIED_AND_SYSTEM",
    "UNCLASSIFIED": "UNCLASSIFIED_AND_SYSTEM", "UNMAPPED": "UNCLASSIFIED_AND_SYSTEM",
}
BLOCK_ORDER = (
    "PRODUCTIVE", "DIGITAL_LEISURE_AND_SOCIAL", "HEALTH", "SOCIAL_IN_PERSON", "HOBBIES",
    "UNCLASSIFIED_AND_SYSTEM",
)
DEVICE_ORDER = ("PHONE", "LAPTOP", "DESKTOP", "COMPUTERS_TOTAL", "TV", "OFF_DEVICE_RECORDED")

# Exact activity equivalents ("anchors") present in both regimes.
LEGACY_ANCHORS = {
    ("Health & Wellness", "Sleep"): "SLEEP",
    ("Health & Wellness", "Workout"): "WORKOUT",
    ("Health & Wellness", "Meditation"): "MEDITATION",
    ("Relationships / Social", "Family"): "FAMILY_TIME",
    ("Hobbies / Creative", "Motorcycle"): "MOTORCYCLE",
    ("Learning / Skill-building", "Reading Books"): "READING_JOURNALING",
    ("Learning / Skill-building", "Reading Books/Journaling"): "READING_JOURNALING",
}
CURRENT_ANCHORS = {
    ("Health & Fitness", "Sleep"): "SLEEP",
    ("Health & Fitness", "Workout"): "WORKOUT",
    ("Health & Fitness", "Meditation"): "MEDITATION",
    ("Social Networking", "Family Time Tracking"): "FAMILY_TIME",
    ("Travel", "Motorcycle Time"): "MOTORCYCLE",
    ("Information & Reading", "Read a book"): "READING_JOURNALING",
    ("Information & Reading", "Journaling"): "READING_JOURNALING",
}
ANCHOR_ORDER = ("SLEEP", "WORKOUT", "MEDITATION", "FAMILY_TIME", "FRIENDS_TIME", "MOTORCYCLE", "READING_JOURNALING")

# Cross-regime comparability by metric (legacy <-> current). See the
# comparability matrix for the full Legacy / Transition / Current view.
GROUP_COMPARABILITY = {
    "WORK_STUDY": (WITH_CAVEATS, "Split vs LEARNING is judgement-based in the current taxonomy; current excludes ActivityWatch 'Uncategorized' and system time (separate groups)."),
    "LEARNING": (WITH_CAVEATS, "Split vs WORK_STUDY is judgement-based; compare PRODUCTIVE block for robust trends."),
    "PERSONAL_ADMIN": (WITH_CAVEATS, "Current phone 'Productivity & Finance' / 'Shopping & Food' buckets mapped with REVIEW_REQUIRED."),
    "COMMUNICATION": (WITHIN_LEGACY, "Current phone social bucket cannot be split between messaging and feeds; compare DIGITAL_LEISURE_AND_SOCIAL across regimes."),
    "ENTERTAINMENT": (WITHIN_LEGACY, "Current phone social bucket cannot be split between messaging and feeds; compare DIGITAL_LEISURE_AND_SOCIAL across regimes."),
    "DIGITAL_SOCIAL_UNSPLIT": (CURRENT_ONLY, "Exists only in the current Apple Screen Time ingestion."),
    "HEALTH_WELLNESS": (WITH_CAVEATS, "Dominated by sleep; sleep evidence differs (legacy manual hours vs current Habit screenshots)."),
    "RELATIONSHIPS_SOCIAL": (WITH_CAVEATS, "Manual in both regimes; Jan 2026 legacy added a PC 'Friends' (online) row."),
    "HOBBIES_CREATIVE": (WITH_CAVEATS, "Motorcycle and guitar are exact equivalents; 'Videos' and current creative apps are REVIEW_REQUIRED."),
    "HOUSEHOLD_DAILY_LIVING": (LEGACY_ONLY, "Legacy standard allocation; current equivalent sits in the inferred residual."),
    "UNSPECIFIED_VACATION": (NOT_COMPARABLE, "Legacy balancing fill on vacation days — a filler, not a behavior measure (legacy only)."),
    "UTILITIES_SYSTEM": (CURRENT_ONLY, "No legacy equivalent."),
    "UNCLASSIFIED": (CURRENT_ONLY, "ActivityWatch Uncategorized time."),
    "UNMAPPED": (NOT_COMPARABLE, "No mapping."),
}
BLOCK_COMPARABILITY = {
    "PRODUCTIVE": (WITH_CAVEATS, "Most robust productive measure; current excludes Uncategorized/system time."),
    "DIGITAL_LEISURE_AND_SOCIAL": (WITH_CAVEATS, "Robust cross-regime leisure/social-digital measure."),
    "HEALTH": (WITH_CAVEATS, "Dominated by sleep."),
    "SOCIAL_IN_PERSON": (WITH_CAVEATS, "Manual in both regimes."),
    "HOBBIES": (WITH_CAVEATS, "See HOBBIES_CREATIVE."),
    "UNCLASSIFIED_AND_SYSTEM": (NOT_COMPARABLE, "Current-only buckets."),
}
DEVICE_COMPARABILITY = {
    "PHONE": (WITH_CAVEATS, "Legacy: manual transcription of selected Screen Time categories; current: full Screen Time ingestion incl. utilities."),
    "LAPTOP": (WITH_CAVEATS, "Not available as a separate value in August 2026 (unsplit)."),
    "DESKTOP": (WITH_CAVEATS, "Not available as a separate value in August 2026 (unsplit)."),
    "COMPUTERS_TOTAL": (WITH_CAVEATS, "Laptop + desktop combined; the only computer measure available for August 2026."),
    "TV": (WITH_CAVEATS, "Manual in both regimes."),
    "OFF_DEVICE_RECORDED": (NOT_COMPARABLE, "Legacy includes household standard allocation and vacation fill; current Habit rows do not."),
}
ANCHOR_COMPARABILITY = {
    "SLEEP": (WITH_CAVEATS, "Exact activity equivalent; evidence differs (legacy manual decimal hours vs current Habit)."),
    "WORKOUT": (WITH_CAVEATS, "Exact activity equivalent."),
    "MEDITATION": (WITH_CAVEATS, "Exact activity equivalent."),
    "FAMILY_TIME": (WITH_CAVEATS, "Exact activity equivalent."),
    "FRIENDS_TIME": (WITH_CAVEATS, "Exact activity equivalent (off-device). Legacy PC 'Friends' row excluded from the anchor."),
    "MOTORCYCLE": (WITH_CAVEATS, "Exact activity equivalent."),
    "READING_JOURNALING": (WITH_CAVEATS, "Legacy row combines reading and journaling; current sums both Habit rows."),
}

# Documented methodology breaks (month -> metrics affected, description).
# '*' affects every metric.
METHOD_BREAKS: dict[str, list[tuple[str, str]]] = {
    "2025-10": [
        ("HOUSEHOLD_DAILY_LIVING", "Household template changed from '=30+30+15+60+15+30' (180 min) to an 11-block composition (240 min) / literal 200."),
        ("block:DAILY_LIVING_AND_RESIDUAL", "Household template change (see HOUSEHOLD_DAILY_LIVING)."),
        ("anchor:READING_JOURNALING", "Row relabelled 'Reading Books' -> 'Reading Books/Journaling' (journaling added)."),
        ("accounting:recorded", "Household template change raises recorded time."),
        ("accounting:accounted", "Household template change raises accounted time."),
        ("accounting:unresolved", "Household template change lowers unresolved time."),
    ],
    "2025-11": [
        ("HOUSEHOLD_DAILY_LIVING", "'nap' added to the household row label (naps folded into household)."),
        ("PERSONAL_ADMIN", "New off-device Personal Admin row (row 51) — excluded from the legacy report totals in Nov/Dec."),
        ("block:PRODUCTIVE", "New off-device Personal Admin row."),
        ("accounting:recorded", "Nap folded into household + new off-device Personal Admin row."),
        ("accounting:accounted", "Nap folded into household + new off-device Personal Admin row."),
        ("accounting:unresolved", "Nap folded into household + new off-device Personal Admin row."),
        ("accounting:specified_activity", "New off-device Personal Admin row."),
    ],
    "2026-01": [
        ("*", "New workbook (2026 T1): phone Personal Admin / Entertainment rows consolidated (time-of-day split removed), 'Vacations' row and PC 'Friends' row added, off-device Personal Admin row now included in report totals."),
    ],
    "2026-07": [
        ("*", "Off-device rows not recorded on 2026-07-27..31 (device-only tracking; legacy report capacity set to 27 days)."),
    ],
    "2026-08": [
        ("*", "Regime change: legacy manual tracking -> System Tracker (automatic ActivityWatch, Apple Screen Time ingestion, Habit screenshots, inferred Off-Device residual). August is TRANSITION / REVIEW."),
    ],
    "2026-09": [
        ("*", "Regime change: transition -> closed current reconciled accounting (device split restored; August Habit double-application not present)."),
    ],
}


def month_range(start: str = ANALYSIS_START_MONTH, end: str = ANALYSIS_END_MONTH) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _pct(num: float, den: float) -> float | str:
    return round(100.0 * num / den, 4) if den else ""


def _h(minutes: float) -> float:
    return round(minutes / 60.0, 4)


# --------------------------------------------------------------------------
# Monthly aggregation
# --------------------------------------------------------------------------

def aggregate_month(
    month: str,
    regime: str,
    daily: list[dict],
    legacy_records: list[dict] | None = None,
    current_records: list[dict] | None = None,
    reported: list[dict] | None = None,
) -> tuple[dict, list[dict]]:
    """Return (wide monthly row, long rows) for one month."""
    cap = calendar_capacity_min(month)
    days = [d for d in daily if d["month"] == month]
    recorded = sum(d["recorded_min"] for d in days)
    overlap = sum(d["overlap_min"] for d in days)
    accounted = sum(d["accounted_min"] for d in days)
    missing_days = int(round(cap / DAY_CAPACITY_MIN)) - len(days)
    unresolved = cap - accounted  # days with no data at all count as unresolved
    std = sum(d["standard_allocation_min"] for d in days)
    fill = sum(d["balancing_fill_min"] for d in days)

    groups = defaultdict(float)
    devices = defaultdict(float)
    anchors = defaultdict(float)
    energy = defaultdict(float)
    goal = defaultdict(float)
    domain = defaultdict(float)
    goal_specified = defaultdict(float)
    mapped = {"energy": 0.0, "goal": 0.0, "domain": 0.0}
    specified_total = 0.0

    if legacy_records is not None:
        for r in legacy_records:
            if r["month"] != month or r["accounting_status"] != "INCLUDED":
                continue
            v = r["normalized_duration_min"]
            groups[r["bridge_group"]] += v
            dev = r["canonical_device"] or "UNMAPPED"
            devices[dev] += v
            anchor = LEGACY_ANCHORS.get((r["legacy_category"], r["legacy_subcategory"]))
            if anchor is None and r["legacy_category"] == "Relationships / Social" and r["legacy_subcategory"] == "Friends" and r["legacy_device"] == "Off-Device":
                anchor = "FRIENDS_TIME"
            if anchor:
                anchors[anchor] += v
            for dim, store in (("energy", energy), ("goal", goal), ("domain", domain)):
                val = r[f"canonical_{dim}"]
                status = r[f"{dim}_status"]
                if val and status == "HIGH_CONFIDENCE":
                    store[val] += v
                    mapped[dim] += v
                else:
                    store["UNMAPPED_OR_REVIEW"] += v
            if r["bridge_group"] not in ("HOUSEHOLD_DAILY_LIVING", "UNSPECIFIED_VACATION"):
                specified_total += v
                if r["canonical_goal"] and r["goal_status"] == "HIGH_CONFIDENCE":
                    goal_specified[r["canonical_goal"]] += v
                else:
                    goal_specified["UNMAPPED_OR_REVIEW"] += v
    if current_records is not None:
        for r in current_records:
            if r["month"] != month or r["bridge_group"] == "RESIDUAL":
                continue
            v = r["duration_min"]
            groups[r["bridge_group"]] += v
            devices[r["device_class"]] += v
            anchor = CURRENT_ANCHORS.get((r["category"], r["subcategory"]))
            if anchor is None and (r["category"], r["subcategory"]) == ("Social Networking", "Social Time Tracking"):
                anchor = "FRIENDS_TIME"
            if anchor:
                anchors[anchor] += v
            energy[r["energy"]] += v
            goal[r["goal"]] += v
            domain[r["domain"]] += v
            for dim in mapped:
                mapped[dim] += v
            specified_total += v
            goal_specified[r["goal"]] += v

    blocks = defaultdict(float)
    for g, v in groups.items():
        blocks[BLOCK_OF.get(g, "UNCLASSIFIED_AND_SYSTEM")] += v
    if devices.get("LAPTOP") or devices.get("DESKTOP"):
        devices["COMPUTERS_TOTAL"] += devices.get("LAPTOP", 0.0) + devices.get("DESKTOP", 0.0)

    report = {}
    if reported is not None:
        for r in reported:
            if r["sheet_month"] == month:
                report[(r["report_block"], r["label"])] = r
    rep_total = report.get(("CATEGORY", "Total"), {}).get("reported_hours", "")
    rep_cap = report.get(("CAPACITY", "Reported capacity (used by 'Other')"), {}).get("reported_hours", "")
    rep_other = report.get(("CATEGORY", "Other"), {}).get("reported_hours", "")

    day_count = len(days)
    status_counts = defaultdict(int)
    flag_counts = defaultdict(int)
    for d in days:
        status_counts[d["day_status"]] += 1
        for f in filter(None, d["day_flags"].split(";")):
            flag_counts[f] += 1

    # specified_accounted = accounted minus every HOUSEHOLD / VACATION minute
    # (standard and non-template household estimates + vacation fill).
    specified_accounted = accounted - groups.get("HOUSEHOLD_DAILY_LIVING", 0.0) - groups.get("UNSPECIFIED_VACATION", 0.0)

    methodology = {
        REGIME_LEGACY: "Legacy manual timesheet (Excel). Device rows transcribed from Screen Time iOS / ActivityWatch; off-device rows entered manually; household time from a standard template.",
        REGIME_TRANSITION: "System Tracker Final (Aug 2026) as published: automatic ActivityWatch (laptop+desktop unsplit), Apple Screen Time ingestion, Habit screenshots + manual adjustments, inferred Off-Device residual. Known issue: ~1,230 min Aug 16–31 Habit adjustments applied twice.",
        REGIME_CURRENT: "System Tracker Final (Sep 2026) closed accounting, read-only reference.",
    }[regime]
    confidence = {
        REGIME_LEGACY: "LEGACY_MANUAL — comparable with caveats at bridge-group / block level",
        REGIME_TRANSITION: "TRANSITION_REVIEW — totals usable with known overstatement; device split not comparable",
        REGIME_CURRENT: "AUTHORITATIVE_CURRENT",
    }[regime]

    notes = []
    if month in METHOD_BREAKS:
        notes.extend(desc for _, desc in METHOD_BREAKS[month])
    if missing_days:
        notes.append(f"{missing_days} calendar day(s) without any data counted as unresolved.")
    if rep_total != "" and abs(float(rep_total) * 60 - recorded) > 0.01:
        notes.append(
            f"Legacy report total ({float(rep_total):.2f} h) differs from detail ({recorded/60:.2f} h); see reconciliation."
        )
    if rep_cap != "" and abs(float(rep_cap) * 60 - cap) > 0.01:
        notes.append(
            f"Legacy report capacity mis-entered ({float(rep_cap):.0f} h vs calendar {cap/60:.0f} h); report 'Other' not used."
        )

    wide = {
        "month": month,
        "regime": regime,
        "methodology_status": confidence,
        "source_methodology": methodology,
        "calendar_days": int(round(cap / DAY_CAPACITY_MIN)),
        "days_with_data": day_count,
        "calendar_capacity_h": _h(cap),
        "recorded_h": _h(recorded),
        "overlap_h": _h(overlap),
        "accounted_h": _h(accounted),
        "unresolved_h": _h(unresolved),
        "coverage_pct": _pct(accounted, cap),
        "specified_activity_h": _h(specified_accounted),
        "specified_coverage_pct": _pct(specified_accounted, cap),
        "standard_allocation_h": _h(std),
        "balancing_fill_h": _h(fill),
        "legacy_detail_total_h": _h(recorded) if regime == REGIME_LEGACY else "",
        "legacy_reported_total_h": rep_total,
        "report_minus_detail_h": round(float(rep_total) - recorded / 60, 4) if rep_total != "" else "",
        "report_minus_detail_pct": round(100 * (float(rep_total) * 60 - recorded) / recorded, 4) if rep_total != "" and recorded else "",
        "legacy_reported_capacity_h": rep_cap,
        "legacy_reported_other_h": rep_other,
        "days_over_capacity": flag_counts.get("OVER_CAPACITY", 0),
        "days_incomplete_off_device": flag_counts.get("INCOMPLETE_OFF_DEVICE", 0),
        "days_review_status": status_counts.get("REVIEW", 0),
    }
    for g in GROUP_ORDER:
        wide[f"grp_{g}_h"] = _h(groups.get(g, 0.0))
    for b in BLOCK_ORDER:
        wide[f"blk_{b}_h"] = _h(blocks.get(b, 0.0))
    for d in DEVICE_ORDER:
        wide[f"dev_{d}_h"] = _h(devices.get(d, 0.0))
    for a in ANCHOR_ORDER:
        wide[f"act_{a}_h"] = _h(anchors.get(a, 0.0))
    wide["energy_mapped_pct_of_recorded"] = _pct(mapped["energy"], recorded)
    wide["goal_mapped_pct_of_recorded"] = _pct(mapped["goal"], recorded)
    wide["domain_mapped_pct_of_recorded"] = _pct(mapped["domain"], recorded)
    wide["notes_flags"] = " | ".join(notes)

    long_rows: list[dict] = []

    denominators = {"recorded": recorded, "capacity": cap, "specified_activity": specified_total}

    def add(dimension: str, value: str, minutes: float, comparability: tuple[str, str], basis: str):
        denominator = denominators[basis]
        long_rows.append(
            {
                "month": month,
                "regime": regime,
                "dimension": dimension,
                "value": value,
                "minutes": round(minutes, 4),
                "hours": _h(minutes),
                "min_per_day": round(minutes / (cap / DAY_CAPACITY_MIN), 4),
                "share_pct": _pct(minutes, denominator),
                "share_basis": basis,
                "comparability": comparability[0],
                "comparability_note": comparability[1],
            }
        )

    add("accounting", "calendar_capacity", cap, (COMPARABLE, "Calendar days x 24 h."), "capacity")
    add("accounting", "recorded", recorded, (WITH_CAVEATS, "Legacy = manual detail incl. estimates; current = corrected tracked source time."), "capacity")
    add("accounting", "overlap", overlap, (WITH_CAVEATS, "Same day-level rule (max(0, day - 24 h)) in both regimes."), "capacity")
    add("accounting", "accounted", accounted, (WITH_CAVEATS, "Recorded minus overlap (= Unique Tracked Time)."), "capacity")
    add("accounting", "unresolved", unresolved, (WITH_CAVEATS, "Capacity minus accounted (= Off-Device residual in current). Legacy accounted includes household estimates; use specified_activity for like-for-like."), "capacity")
    add("accounting", "specified_activity", specified_accounted, (WITH_CAVEATS, "Accounted minus household standard allocation and vacation fill (like-for-like with current, which never records household time)."), "capacity")
    for g in GROUP_ORDER:
        if groups.get(g):
            add("bridge_group", g, groups[g], GROUP_COMPARABILITY[g], "recorded")
    for b in BLOCK_ORDER:
        if blocks.get(b):
            add("bridge_block", b, blocks[b], BLOCK_COMPARABILITY[b], "recorded")
    for d in DEVICE_ORDER:
        if devices.get(d):
            note = DEVICE_COMPARABILITY[d]
            if regime == REGIME_TRANSITION and d == "COMPUTERS_TOTAL":
                note = (WITH_CAVEATS, "August 2026: laptop and desktop are not separated in the Final outputs.")
            add("device", d, devices[d], note, "recorded")
    for a in ANCHOR_ORDER:
        if anchors.get(a):
            add("activity_anchor", a, anchors[a], ANCHOR_COMPARABILITY[a], "capacity")
    for val, v in sorted(energy.items()):
        add("canonical_energy", val, v, (NOT_COMPARABLE, "Legacy 'Recovery / Rest' and household/learning energies are not mappable; mapped share shown in energy_mapped_pct_of_recorded."), "recorded")
    for val, v in sorted(goal_specified.items()):
        add("canonical_goal_specified", val, v, (WITH_CAVEATS, "Goal share of specified activity (excludes household, vacation fill and residual). Legacy goal re-classified by activity where an exact equivalent exists, else by row label."), "specified_activity")
    for val, v in sorted(domain.items()):
        add("canonical_domain", val, v, (WITH_CAVEATS, "Legacy domain mapped by category/activity; Personal Admin and phone Health rows REVIEW_REQUIRED."), "recorded")
    return wide, long_rows


def build_monthly(
    legacy_records: list[dict],
    legacy_daily: list[dict],
    reported: list[dict],
    current_by_regime: dict[str, tuple[list[dict], list[dict]]],
) -> tuple[list[dict], list[dict]]:
    """Build the wide and long monthly datasets for the whole analysis window."""
    legacy_months = {r["month"] for r in legacy_daily}
    current_months = {}
    for regime, (records, daily) in current_by_regime.items():
        for d in daily:
            current_months[d["month"]] = regime
    wide_rows, long_rows = [], []
    for month in month_range():
        if month in current_months:
            regime = current_months[month]
            records, daily = current_by_regime[regime]
            wide, long_ = aggregate_month(month, regime, daily, current_records=records)
        elif month in legacy_months:
            wide, long_ = aggregate_month(month, REGIME_LEGACY, legacy_daily, legacy_records=legacy_records, reported=reported)
        else:
            wide = {"month": month, "regime": REGIME_NO_DATA, "notes_flags": "No source data."}
            long_ = []
        wide_rows.append(wide)
        long_rows.extend(long_)
    return wide_rows, long_rows


# --------------------------------------------------------------------------
# Month-over-month
# --------------------------------------------------------------------------

def _affected_by_break(month: str, dimension: str, value: str) -> str | None:
    for target, desc in METHOD_BREAKS.get(month, []):
        if target == "*":
            return desc
        if target == value and dimension == "bridge_group":
            return desc
        if target == f"block:{value}" and dimension == "bridge_block":
            return desc
        if target == f"anchor:{value}" and dimension == "activity_anchor":
            return desc
        if target == f"accounting:{value}" and dimension == "accounting":
            return desc
    return None


VACATION_SENSITIVE = {"recorded", "accounted", "unresolved"}


def month_over_month(long_rows: list[dict], wide_rows: list[dict]) -> list[dict]:
    """Absolute / % change vs previous month with change-type classification.

    Values are compared as min/day so months of different length are
    comparable. Classification:
      NOT_COMPARABLE        metric not comparable (or not present) in either month
      METHODOLOGICAL_BREAK  months belong to different regimes
      POSSIBLE_METHODOLOGICAL  documented within-regime break affects the metric
      BEHAVIORAL_CANDIDATE  same regime, no documented break, comparable metric
    """
    regime_of = {w["month"]: w["regime"] for w in wide_rows}
    fill_of = {w["month"]: float(w.get("balancing_fill_h") or 0.0) for w in wide_rows}
    index = {(r["month"], r["dimension"], r["value"]): r for r in long_rows}
    months = [w["month"] for w in wide_rows]
    keys = sorted({(r["dimension"], r["value"]) for r in long_rows})
    out = []
    for prev, cur in zip(months, months[1:]):
        for dim, val in keys:
            a = index.get((prev, dim, val))
            b = index.get((cur, dim, val))
            if a is None and b is None:
                continue
            va = a["min_per_day"] if a else 0.0
            vb = b["min_per_day"] if b else 0.0
            comp_a = a["comparability"] if a else ""
            comp_b = b["comparability"] if b else ""
            comps = {c for c in (comp_a, comp_b) if c}
            same_regime = regime_of.get(prev) == regime_of.get(cur)
            reason = ""
            if NOT_COMPARABLE in comps or not comps:
                change_type, reason = "NOT_COMPARABLE", "Metric not comparable."
            elif not same_regime:
                if comps & {LEGACY_ONLY, CURRENT_ONLY, WITHIN_LEGACY}:
                    change_type, reason = "NOT_COMPARABLE", "Metric exists / is comparable in one regime only."
                else:
                    change_type = "METHODOLOGICAL_BREAK"
                    reason = f"Regime change {regime_of.get(prev)} -> {regime_of.get(cur)}."
            else:
                brk = _affected_by_break(cur, dim, val)
                if not brk and dim == "accounting" and val in VACATION_SENSITIVE and (fill_of.get(prev) or fill_of.get(cur)):
                    brk = "Vacation balancing fill (H-VAC-1) present in one of the two months; compare specified_activity instead."
                if brk:
                    change_type, reason = "POSSIBLE_METHODOLOGICAL", brk
                elif comps & {CURRENT_ONLY} and regime_of.get(cur) == REGIME_LEGACY:
                    change_type, reason = "NOT_COMPARABLE", "Current-only metric."
                else:
                    change_type, reason = "BEHAVIORAL_CANDIDATE", "Same regime, no documented methodology change."
            abs_change = round(vb - va, 4)
            pct = round(100 * (vb - va) / va, 4) if va else ""
            material = (
                "Y" if pct != "" and abs(pct) >= 10 and abs(abs_change) * 30 >= 300 else "N"
            )
            out.append(
                {
                    "month": cur,
                    "previous_month": prev,
                    "regime": regime_of.get(cur),
                    "previous_regime": regime_of.get(prev),
                    "dimension": dim,
                    "metric": val,
                    "previous_min_per_day": va,
                    "min_per_day": vb,
                    "abs_change_min_per_day": abs_change,
                    "pct_change": pct,
                    "abs_change_h_month_equiv": round(abs_change * 30 / 60, 4),
                    "material": material,
                    "comparability": " / ".join(sorted(comps)),
                    "change_type": change_type,
                    "reason": reason,
                }
            )
    return out


# --------------------------------------------------------------------------
# Period (annual) analysis
# --------------------------------------------------------------------------

def period_analysis(wide_rows: list[dict], long_rows: list[dict], mom: list[dict]) -> list[dict]:
    """Aggregate by regime segment and for the whole window."""
    segments = {
        "LEGACY Sep 2025 – Jul 2026": [w for w in wide_rows if w["regime"] == REGIME_LEGACY],
        "TRANSITION Aug 2026": [w for w in wide_rows if w["regime"] == REGIME_TRANSITION],
        "CURRENT Sep 2026": [w for w in wide_rows if w["regime"] == REGIME_CURRENT],
        f"ALL {PERIOD_LABEL} (mixed methodology — use with care)": [w for w in wide_rows if w["regime"] != REGIME_NO_DATA],
    }
    out = []

    def row(segment, metric, value, unit, note=""):
        out.append({"segment": segment, "metric": metric, "value": value, "unit": unit, "note": note})

    for seg, rows in segments.items():
        if not rows:
            continue
        months = [w["month"] for w in rows]
        cap = sum(w["calendar_capacity_h"] for w in rows)
        rec = sum(w["recorded_h"] for w in rows)
        acc = sum(w["accounted_h"] for w in rows)
        spec = sum(w["specified_activity_h"] for w in rows)
        row(seg, "months", len(rows), "count", f"{months[0]} .. {months[-1]}")
        row(seg, "calendar_days", sum(w["calendar_days"] for w in rows), "days")
        row(seg, "total_capacity", round(cap, 2), "h")
        row(seg, "total_recorded", round(rec, 2), "h", "Gross recorded/observed time (before overlap).")
        row(seg, "total_overlap", round(sum(w["overlap_h"] for w in rows), 2), "h")
        row(seg, "total_accounted", round(acc, 2), "h")
        row(seg, "total_unresolved", round(sum(w["unresolved_h"] for w in rows), 2), "h")
        row(seg, "coverage_weighted", round(100 * acc / cap, 2), "%", "Accounted / capacity over the segment.")
        row(seg, "coverage_monthly_average", round(sum(w["coverage_pct"] for w in rows) / len(rows), 2), "%", "Simple average of monthly coverage.")
        row(seg, "specified_coverage_weighted", round(100 * spec / cap, 2), "%", "Excludes household standard allocation and vacation fill.")
        row(seg, "standard_allocation_total", round(sum(w["standard_allocation_h"] for w in rows), 2), "h")
        best = max(rows, key=lambda w: w["coverage_pct"])
        worst = min(rows, key=lambda w: w["coverage_pct"])
        row(seg, "best_coverage_month", best["month"], "month", f"{best['coverage_pct']:.2f}%")
        row(seg, "worst_coverage_month", worst["month"], "month", f"{worst['coverage_pct']:.2f}%")
        days = sum(w["calendar_days"] for w in rows)
        for prefix, dim in (("grp_", "bridge_group"), ("blk_", "bridge_block"), ("dev_", "device"), ("act_", "activity_anchor")):
            keys = [k for k in rows[0] if k.startswith(prefix)]
            for k in keys:
                total = sum(w.get(k, 0.0) for w in rows)
                if not total:
                    continue
                name = k[len(prefix):-2]
                row(seg, f"{dim}:{name}:total", round(total, 2), "h")
                row(seg, f"{dim}:{name}:share_of_recorded", round(100 * total / rec, 2) if rec else "", "%")
                row(seg, f"{dim}:{name}:monthly_average", round(total / len(rows), 2), "h/month")
                row(seg, f"{dim}:{name}:per_day", round(60 * total / days, 2), "min/day")

    comparable_changes = [
        m for m in mom
        if m["change_type"] == "BEHAVIORAL_CANDIDATE" and m["material"] == "Y"
        and m["dimension"] in ("bridge_group", "bridge_block", "activity_anchor", "accounting", "device")
    ]
    for m in sorted(comparable_changes, key=lambda x: -abs(x["abs_change_min_per_day"]))[:10]:
        row(
            "LARGEST BEHAVIORAL-CANDIDATE CHANGES (within legacy)",
            f"{m['month']}:{m['dimension']}:{m['metric']}",
            m["abs_change_min_per_day"],
            "min/day",
            f"{m['previous_min_per_day']:.1f} -> {m['min_per_day']:.1f} ({m['pct_change']}%)",
        )
    for month, items in METHOD_BREAKS.items():
        for target, desc in items:
            row("METHODOLOGY TRANSITION POINTS", month, target, "affected", desc)
    return out
