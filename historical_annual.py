# historical_annual.py
"""Annual consolidated historical analysis (Sep 2025 – Sep 2026) in the CURRENT
taxonomy, plus an initial Time Budgeting baseline.

Layer on top of the Phase 2 historical pipeline. It CONSUMES the datasets in
output/Historical/ (produced by historical_pipeline.py); it does not re-read
the legacy workbooks and adds no accounting rule.

Translation chain (docs/HISTORICAL_ANNUAL_ANALYSIS.md):

    legacy source value -> bridge mapping rules (config/historical_bridge/,
    historical_bridge.normalize_legacy) -> current canonical value
    (historical_canonical.build_canonical_records) -> annual aggregation -> reports

All primary reports and dashboards aggregate by the current taxonomy (the
Dim_* files used by final_analysis.py for the monthly reports). Bridge groups
are kept only as an internal diagnostic in the mapping & comparability report.

Usage (from the project root, inside the project's .venv):

    python historical_pipeline.py      # (re)build output/Historical first
    python historical_annual.py        # annual analysis + reports
    python historical_annual.py --check-only

Outputs (git-ignored, private): output/Historical/Annual_2025-09_to_2026-09/
(00_INDEX.md written there lists every file). Report rendering and charts
live in historical_annual_reports.py.

Nothing here is a dependency of the monthly close. Budget outputs are
analytical templates; no budget is implemented or approved by this script.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import historical_canonical as hc
import historical_monthly as hm
from config.historical import (
    ANALYSIS_END_MONTH,
    ANALYSIS_START_MONTH,
    ANNUAL_BASELINE_END_MONTH,
    ANNUAL_BASELINE_START_MONTH,
    ANNUAL_OUTPUT_ROOT,
    BRIDGE_CONFIG_ROOT,
    CURRENT_REFERENCE_PERIOD,
    FINAL_ANALYSIS_ROOT,
    HISTORICAL_OUTPUT_ROOT,
    LEGACY_INPUT_ROOT,
    LEGACY_WORKBOOKS,
    PROJECT_ROOT,
    REGIME_CURRENT,
    REGIME_LEGACY,
    REGIME_NO_DATA,
    REGIME_TRANSITION,
    TRANSITION_PERIOD,
)
from historical_bridge import DAY_CAPACITY_MIN, DAY_TOLERANCE_MIN, read_current_period
from historical_legacy_loader import sha256_file

PERIOD_TAG = f"{ANALYSIS_START_MONTH}_to_{ANALYSIS_END_MONTH}"

INPUT_FILES = (
    "Historical_Monthly_Analysis.csv",
    "Historical_Monthly_Long.csv",
    "Historical_Monthly_Comparison.csv",
    "Historical_Bridge_Records.csv",
    "Historical_Normalized_Records.csv",
    "Historical_Legacy_Daily.csv",
    "Historical_Current_Daily.csv",
    "Historical_Taxonomy_Mapping.csv",
    "Historical_Validation.csv",
)
# historical_pipeline.run() records this file's hash, then rewrites it with the
# integrity checks appended, so its manifest hash never matches by design.
REWRITTEN_AFTER_MANIFEST = {"Historical_Validation.csv"}

# Segments. The 13-month total mixes three measurement regimes; it is a
# descriptive arithmetic reference only.
SEG_MIXED = "MIXED_13M"
SEG_LEGACY = "LEGACY_ALL"
SEG_BASE = "LEGACY_BASELINE"
SEG_TRANS = "TRANSITION"
SEG_CUR = "CURRENT"
SEGMENT_ORDER = (SEG_MIXED, SEG_LEGACY, SEG_BASE, SEG_TRANS, SEG_CUR)
SEGMENT_LABEL = {
    SEG_MIXED: "All 13 months — mixed-method arithmetic total (descriptive reference, NOT a homogeneous measurement)",
    SEG_LEGACY: "Legacy manual tracking, all 11 months (2025-09 .. 2026-07)",
    SEG_BASE: f"Legacy baseline window ({ANNUAL_BASELINE_START_MONTH} .. {ANNUAL_BASELINE_END_MONTH})",
    SEG_TRANS: "Transition / review (2026-08) — not a closed, fully comparable month",
    SEG_CUR: "Current reconciled accounting (2026-09, closed, authoritative)",
}

EVIDENCE_CLASS = {
    "MANUAL_TRANSCRIPTION": "TRANSCRIBED_DEVICE_TIME",
    "MANUAL_ENTRY": "MANUAL_RECORDED",
    "REPEATED_ROUTINE_VALUE": "MANUAL_RECORDED",
    "STANDARD_DAILY_ALLOCATION": "ESTIMATE_OR_STANDARD",
    "ESTIMATE_NON_TEMPLATE": "ESTIMATE_OR_STANDARD",
    "BALANCING_FILL": "BALANCING_FILL",
    "Observed": "OBSERVED_AUTOMATIC",
    "Derived": "OBSERVED_AUTOMATIC",
    "Mixed": "OBSERVED_AUTOMATIC",
    "Inferred": "HABIT_SCREENSHOT_INFERRED",
    "Target_Derived": "HABIT_SCREENSHOT_INFERRED",
    "Manual Habit Addition": "MANUAL_ADJUSTMENT",
    "Manual Habit Reduction": "MANUAL_ADJUSTMENT",
    "Estimated": "ESTIMATE_OR_STANDARD",
    "Inferred Time Universe Residual": "INFERRED_RESIDUAL",
}
EVIDENCE_ORDER = (
    "OBSERVED_AUTOMATIC", "TRANSCRIBED_DEVICE_TIME", "MANUAL_RECORDED", "HABIT_SCREENSHOT_INFERRED",
    "MANUAL_ADJUSTMENT", "ESTIMATE_OR_STANDARD", "BALANCING_FILL",
)

# Bridge groups that exist only in the current regime — used internally to
# detect capture differences, never shown as categories.
CURRENT_ONLY_CAPTURE_GROUPS = {"DIGITAL_SOCIAL_UNSPLIT", "UTILITIES_SYSTEM", "UNCLASSIFIED"}
DIGITAL_DEVICES = ("PHONE", "COMPUTERS_TOTAL", "TV")
SCREEN_DEVICES = ("PHONE", "LAPTOP", "DESKTOP", "COMPUTERS_TOTAL", "TV")

# Legacy evidence status of a current category.
EV_COMPARABLE = "COMPARABLE_WITH_CAVEATS"
EV_RESERVATIONS = "COMPARABLE_WITH_RESERVATIONS"
EV_PARTIAL = "PARTIAL_LEGACY_EVIDENCE"
EV_CAPTURE = "CAPTURE_DIFFERS"
EV_NONE = "CURRENT_ONLY_NO_LEGACY_EVIDENCE"
EV_NOTE = {
    EV_COMPARABLE: "Legacy time mapped with high confidence; comparable with the usual regime caveats.",
    EV_RESERVATIONS: "Most legacy time reaches this category through review-required mappings or estimates.",
    EV_PARTIAL: "Part of the legacy time that belongs here sits in unsplit legacy records (several possible current categories); legacy values understate the category.",
    EV_CAPTURE: "Most current time comes from capture the legacy method did not transcribe (system, unclassified or unsplit phone buckets).",
    EV_NONE: "No legacy record maps here; absence in legacy is a capture / mapping gap, not evidence of zero time.",
}
EV_TO_MOM = {EV_COMPARABLE: hm.WITH_CAVEATS, EV_RESERVATIONS: hm.WITH_CAVEATS, EV_PARTIAL: hm.WITHIN_LEGACY,
             EV_CAPTURE: hm.WITHIN_LEGACY, EV_NONE: hm.CURRENT_ONLY}

LINE_COMPARABILITY = {
    "SLEEP": hm.WITH_CAVEATS, "HEALTH_PRACTICES": hm.WITH_CAVEATS, "HEALTH_OTHER": hm.WITH_CAVEATS,
    "OFF_DEVICE_LIFE": hm.WITH_CAVEATS, "IN_PERSON_RELATIONSHIPS": hm.WITH_CAVEATS,
    "PRODUCTIVITY_FINANCE_AND_EDUCATION": hm.WITH_CAVEATS, "INFORMATION_READING": hm.WITH_CAVEATS,
    "DISCRETIONARY_CONSUMPTION": hm.WITH_CAVEATS, "OTHER_SOCIAL_NETWORKING": hm.WITHIN_LEGACY,
    "TRAVEL": hm.WITH_CAVEATS, "CREATIVITY": hm.WITH_CAVEATS, "UTILITIES": hm.WITHIN_LEGACY,
    "SHOPPING_AND_NEWS": hm.CURRENT_ONLY, "UNMAPPED_LEGACY": hm.NOT_COMPARABLE,
}
LINE_PROTECTION = {
    "SLEEP": "PROTECT_FLOOR", "HEALTH_PRACTICES": "PROTECT_FLOOR", "HEALTH_OTHER": "NOT_BUDGETED_SEPARATELY",
    "OFF_DEVICE_LIFE": "PROTECT_RESERVE", "IN_PERSON_RELATIONSHIPS": "PROTECT_FLOOR",
    "PRODUCTIVITY_FINANCE_AND_EDUCATION": "COMMITMENT_AND_GOAL_DEPENDENT", "INFORMATION_READING": "GOAL_DEPENDENT",
    "DISCRETIONARY_CONSUMPTION": "FLEXIBLE_CEILING", "OTHER_SOCIAL_NETWORKING": "MAINTENANCE_ALLOWANCE",
    "TRAVEL": "FLEXIBLE", "CREATIVITY": "GOAL_DEPENDENT", "UTILITIES": "MAINTENANCE_ALLOWANCE",
    "SHOPPING_AND_NEWS": "MAINTENANCE_ALLOWANCE", "UNMAPPED_LEGACY": "NOT_BUDGETABLE",
}
CAPTURE_SENSITIVE_LINES = {
    "PRODUCTIVITY_FINANCE_AND_EDUCATION": "Legacy transcription vs automatic capture; part of current time is the 50/50 split of ActivityWatch 'Uncategorized'.",
    "DISCRETIONARY_CONSUMPTION": "Current Apple Screen Time ingests the full phone social bucket; legacy transcribed selected categories.",
    "OFF_DEVICE_LIFE": "Legacy = household template estimate + (capacity − accounted); current = inferred residual only.",
    "OTHER_SOCIAL_NETWORKING": "Phone messaging sits inside the unsplit phone social bucket in the current system.",
    "UTILITIES": "System processes and unclassified time are captured only by the current system.",
    "INFORMATION_READING": "Web reading / podcasts are captured automatically only in the current system.",
}
# historical_monthly.METHOD_BREAKS targets that affect a budget line.
LINE_BREAK_TARGETS = {
    "PRODUCTIVITY_FINANCE_AND_EDUCATION": {"PERSONAL_ADMIN", "block:PRODUCTIVE"},
    "UTILITIES": {"PERSONAL_ADMIN"},
    "OFF_DEVICE_LIFE": {"HOUSEHOLD_DAILY_LIVING", "block:DAILY_LIVING_AND_RESIDUAL"},
    "INFORMATION_READING": {"anchor:READING_JOURNALING"},
    "SLEEP": {"HOUSEHOLD_DAILY_LIVING"},  # naps folded into the household estimate from Nov 2025
}
# Workbook-wide ('*') breaks: keys plausibly affected (from the break descriptions).
WILDCARD_BREAK_SCOPE = {
    "2026-01": {"PRODUCTIVITY_FINANCE_AND_EDUCATION", "DISCRETIONARY_CONSUMPTION", "OTHER_SOCIAL_NETWORKING",
                "UNMAPPED_LEGACY", "PHONE", "DESKTOP", "COMPUTERS_TOTAL", "DIGITAL_TOTAL"},
}

OBSERVED = "OBSERVED_PATTERN"
HYPOTHESIS = "BEHAVIORAL_HYPOTHESIS"
METHOD = "POSSIBLE_METHODOLOGICAL_EFFECT"
INSUFFICIENT = "INSUFFICIENT_DATA"

MIN_MATERIAL_MIN_PER_DAY = 10.0
STABLE_CV = 0.15
VOLATILE_CV = 0.45
TREND_MIN_PCT = 0.20
ENVELOPE_TOLERANCE = 0.20
ASSOCIATION_RHO = 0.30
DAYTYPE_RATIO = 1.5
ROUND_TO_MIN = 15
MIN_BASELINE_MONTHS = 6
RESERVE_MIN_PER_DAY = {"SUSTAINABLE": DAY_TOLERANCE_MIN / 2, "GOAL_FOCUSED": DAY_TOLERANCE_MIN / 2,
                       "CONSERVATIVE": DAY_TOLERANCE_MIN}
PERIOD_DAYS = {"week": 7, "month_30d": 30, "quarter_91d": 91, "year_365d": 365}
SUB_NOT_RECORDED = "(subcategory not recorded in legacy)"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> str:
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


def hash_tree(root: Path, recursive: bool = True) -> dict[str, str]:
    if not root.exists():
        return {}
    files = root.rglob("*") if recursive else root.glob("*")
    return {str(p.relative_to(root)).replace("\\", "/"): sha256_file(p) for p in sorted(files) if p.is_file()}


def r1(x):
    return "" if x is None or x == "" else round(float(x), 1)


def r2(x):
    return "" if x is None or x == "" else round(float(x), 2)


def round_down(x: float, step: int = ROUND_TO_MIN) -> int:
    return int(math.floor(x / step) * step)


def round_up(x: float, step: int = ROUND_TO_MIN) -> int:
    return int(math.ceil(x / step) * step)


def round_near(x: float, step: int = ROUND_TO_MIN) -> int:
    return int(round(x / step) * step)


def quantile(values: list[float], q: float) -> float | None:
    """Linear-interpolation quantile (numpy default)."""
    if not values:
        return None
    v = sorted(values)
    pos = (len(v) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def cv(values: list[float]) -> float | None:
    m, s = mean(values), stdev(values)
    return s / m if m and s is not None else None


def ols_change(values: list[float]) -> float | None:
    """Fitted change from first to last point of an OLS line over equally spaced months."""
    n = len(values)
    if n < 3:
        return None
    xs = list(range(n))
    mx, my = mean(xs), mean(values)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, values)) / den * (n - 1)


def trend_label(values: list[float]) -> tuple[str, float | None]:
    m = mean(values)
    ch = ols_change(values)
    if ch is None or not m:
        return "INSUFFICIENT", ch
    if abs(ch) >= max(MIN_MATERIAL_MIN_PER_DAY, TREND_MIN_PCT * m):
        return ("RISING" if ch > 0 else "FALLING"), ch
    return "STABLE", ch


def peak_concentration(values: list[float]) -> float | None:
    total = sum(values)
    if total <= 0 or len(values) < 3:
        return None
    return sum(sorted(values, reverse=True)[:2]) / total


def ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = ranks(a), ranks(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den if den else None


def month_days(month: str) -> int:
    import calendar
    y, m = map(int, month.split("-"))
    return calendar.monthrange(y, m)[1]


def hm_fmt(minutes) -> str:
    """Minutes as 'HhMM'."""
    if minutes is None or minutes in ("", "n/a"):
        return "n/a"
    minutes = float(minutes)
    sign = "-" if minutes < 0 else ""
    m = abs(int(round(minutes)))
    return f"{sign}{m // 60}h{m % 60:02d}"


def h_fmt(minutes) -> str:
    """Minutes as 'XXXh YYm' (monthly Standard Report style)."""
    if minutes is None or minutes == "":
        return "n/a"
    m = int(round(float(minutes)))
    return f"{m // 60}h {m % 60:02d}m"


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

class InputError(RuntimeError):
    pass


def load_inputs(hist_dir: Path) -> dict:
    manifest_path = hist_dir / "Historical_Run_Manifest.json"
    if not manifest_path.exists():
        raise InputError(f"{manifest_path} not found — run historical_pipeline.py first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    integrity, data = [], {}
    for name in INPUT_FILES:
        p = hist_dir / name
        if not p.exists():
            raise InputError(f"{p} not found — run historical_pipeline.py first.")
        actual = sha256_file(p)
        expected = manifest.get("outputs", {}).get(name, {}).get("sha256")
        match = "Y" if expected == actual else "N"
        if name in REWRITTEN_AFTER_MANIFEST:
            match = "N/A (rewritten after manifest by the pipeline)"
        integrity.append({"file": name, "sha256": actual, "manifest_sha256": expected or "", "match": match})
        data[name] = _read_csv(p)
    data["manifest"] = manifest
    data["integrity"] = integrity
    data["groups_meta"] = {r["bridge_group"]: r for r in _read_csv(BRIDGE_CONFIG_ROOT / "bridge_groups.csv")}
    data["legacy_rules"] = _read_csv(BRIDGE_CONFIG_ROOT / "legacy_activity_bridge.csv")
    data["label_maps"] = _read_csv(BRIDGE_CONFIG_ROOT / "legacy_label_maps.csv")
    return data


# --------------------------------------------------------------------------
# Context: canonical records + monthly metric matrix
# --------------------------------------------------------------------------

def unsplit_label(cands: str) -> str:
    return "Unsplit legacy: " + " | ".join(cands.split("|"))


def build_context(data: dict, taxonomy: hc.CurrentTaxonomy) -> dict:
    wide = data["Historical_Monthly_Analysis.csv"]
    for w in wide:
        for k, v in list(w.items()):
            if k.endswith(("_h", "_pct")) or k.startswith(("calendar_days", "days_")):
                try:
                    w[k] = float(v) if v != "" else ""
                except ValueError:
                    pass
    months = [w["month"] for w in wide]
    regime = {w["month"]: w["regime"] for w in wide}
    days = {m: month_days(m) for m in months}
    baseline = [m for m in months if ANNUAL_BASELINE_START_MONTH <= m <= ANNUAL_BASELINE_END_MONTH]
    segments = {
        SEG_MIXED: [m for m in months if regime[m] != REGIME_NO_DATA],
        SEG_LEGACY: [m for m in months if regime[m] == REGIME_LEGACY],
        SEG_BASE: baseline,
        SEG_TRANS: [m for m in months if regime[m] == REGIME_TRANSITION],
        SEG_CUR: [m for m in months if regime[m] == REGIME_CURRENT],
    }
    W = {w["month"]: w for w in wide}

    recs = hc.build_canonical_records(data["Historical_Normalized_Records.csv"], data["Historical_Legacy_Daily.csv"],
                                      data["Historical_Bridge_Records.csv"])
    for r in recs:
        r["line"] = hc.budget_line(r)
        r["anchor"] = hc.anchor_of(r) or ""
        r["evidence_class"] = EVIDENCE_CLASS.get(r["evidence_basis"], "OTHER")

    M: dict = defaultdict(lambda: {m: 0.0 for m in months})
    for c in taxonomy.categories:
        M[("category", c)]
    for dim, values in (("domain", taxonomy.domains), ("energy", taxonomy.energies), ("goal", taxonomy.goals)):
        for v in list(values) + [hc.UNMAPPED_VALUE]:
            M[(dim, v)]
    for l in hc.BUDGET_LINE_ORDER:
        M[("line", l)]
    for a in hm.ANCHOR_ORDER:
        M[("anchor", a)]
    for k in ("ANALYTICAL_TOTAL", "CONSUMPTION_PROXY", "SOCIAL_CONSUMPTION"):
        M[("kpi", k)]

    daily = defaultdict(lambda: defaultdict(float))
    for r in recs:
        m, v = r["month"], r["minutes"]
        M[("kpi", "ANALYTICAL_TOTAL")][m] += v
        M[("mapping_status", r["category_status"])][m] += v
        if r["category"]:
            M[("category", r["category"])][m] += v
            M[("subcategory", f"{r['category']} › {r['subcategory'] or SUB_NOT_RECORDED}")][m] += v
        elif r["category_status"] == hc.CAT_MULTI:
            M[("unsplit", unsplit_label(r["candidate_categories"]))][m] += v
        else:
            M[("unmapped", r["legacy_label"])][m] += v
        for dim in ("domain", "energy", "goal"):
            M[(dim, r[dim] or hc.UNMAPPED_VALUE)][m] += v
        M[("line", r["line"])][m] += v
        M[("device_class", r["device_class"])][m] += v
        if r["anchor"]:
            M[("anchor", r["anchor"])][m] += v
        if hc.is_discretionary(r):
            M[("kpi", "CONSUMPTION_PROXY")][m] += v
        if r["category"] == "Social Networking" and r["goal"] == "Consumption":
            M[("kpi", "SOCIAL_CONSUMPTION")][m] += v
        d = r["date"]
        daily[d]["line:" + r["line"]] += v
        if r["category"]:
            daily[d]["category:" + r["category"]] += v
        if r["anchor"]:
            daily[d]["anchor:" + r["anchor"]] += v
        if r["device_class"] in SCREEN_DEVICES:
            daily[d]["DIGITAL_TOTAL"] += v
        for dim in ("energy", "goal"):
            daily[d][f"{dim}:{r[dim] or hc.UNMAPPED_VALUE}"] += v

    # Accounting, device classes and (diagnostic) bridge groups from the pipeline's monthly long table.
    for r in data["Historical_Monthly_Long.csv"]:
        if r["dimension"] in ("accounting", "device", "bridge_group", "bridge_block"):
            M[(r["dimension"], r["value"])][r["month"]] = float(r["minutes"])
    for d in hm.DEVICE_ORDER:
        M[("device", d)]
    for m in months:
        if regime[m] == REGIME_TRANSITION:  # laptop / desktop not separable in the transition month
            M[("device", "LAPTOP")][m] = None
            M[("device", "DESKTOP")][m] = None

    day_info = {}
    for d in data["Historical_Legacy_Daily.csv"] + data["Historical_Current_Daily.csv"]:
        day_info[d["date"]] = d
        daily[d["date"]]["UNRESOLVED"] = float(d["unresolved_min"])

    evidence = defaultdict(lambda: defaultdict(float))
    cat_status = defaultdict(lambda: defaultdict(float))
    cat_capture = defaultdict(float)
    off_spec = defaultdict(float)
    digital_system = defaultdict(float)
    for r in recs:
        if r["category"]:
            cat_status[(r["regime"], r["category"])][r["category_status"]] += r["minutes"]
        if r["bridge_group"] == "RESIDUAL":
            continue
        evidence[r["month"]][r["evidence_class"]] += r["minutes"]
        if r["regime"] == REGIME_CURRENT and r["bridge_group"] in CURRENT_ONLY_CAPTURE_GROUPS:
            cat_capture[r["category"]] += r["minutes"]
        if r["device_class"] == "OFF_DEVICE_RECORDED" and r["evidence_class"] not in ("ESTIMATE_OR_STANDARD", "BALANCING_FILL"):
            off_spec[r["month"]] += r["minutes"]
        if r["device_class"] in SCREEN_DEVICES and r["bridge_group"] in ("UTILITIES_SYSTEM", "UNCLASSIFIED"):
            digital_system[r["month"]] += r["minutes"]

    for m in months:
        acc = W[m]["accounted_h"] * 60
        dig = sum((M[("device", d)][m] or 0.0) for d in DIGITAL_DEVICES)
        M[("derived", "ACCOUNTED_EXCL_ESTIMATES")][m] = acc - evidence[m].get("ESTIMATE_OR_STANDARD", 0.0) - evidence[m].get("BALANCING_FILL", 0.0)
        M[("derived", "ESTIMATE_OR_STANDARD")][m] = evidence[m].get("ESTIMATE_OR_STANDARD", 0.0)
        M[("derived", "BALANCING_FILL")][m] = evidence[m].get("BALANCING_FILL", 0.0)
        M[("derived", "OFF_DEVICE_SPECIFIED")][m] = off_spec[m]
        M[("derived", "DIGITAL_TOTAL")][m] = dig
        M[("derived", "DIGITAL_EXCL_SYSTEM")][m] = dig - digital_system[m]
        M[("derived", "HABIT_EQUIVALENT_MANUAL")][m] = (M[("device", "OFF_DEVICE_RECORDED")][m] or 0.0) + (M[("device", "TV")][m] or 0.0)

    cand_with_legacy = {c for r in recs if r["regime"] == REGIME_LEGACY and r["category_status"] == hc.CAT_MULTI for c in hc.candidates(r)}
    cat_evidence = {}
    for c in taxonomy.categories:
        st = cat_status[(REGIME_LEGACY, c)]
        legacy_total = sum(st.values())
        cur_total = sum(cat_status[(REGIME_CURRENT, c)].values())
        high = st.get(hc.CAT_HIGH, 0.0) + st.get(hc.CAT_RESIDUAL, 0.0)
        if c in cand_with_legacy:
            ev = EV_PARTIAL
        elif legacy_total <= 0:
            ev = EV_NONE
        elif cur_total and cat_capture[c] / cur_total >= 0.5:
            ev = EV_CAPTURE
        elif high / legacy_total >= 0.8:
            ev = EV_COMPARABLE
        else:
            ev = EV_RESERVATIONS
        cat_evidence[c] = ev
        if ev == EV_NONE:
            for m in months:
                if regime[m] == REGIME_LEGACY:
                    M[("category", c)][m] = None

    # Planning lines with no legacy evidence: n/a (not zero) in legacy months.
    for l, comp in LINE_COMPARABILITY.items():
        if comp == hm.CURRENT_ONLY:
            for m in months:
                if regime[m] == REGIME_LEGACY:
                    M[("line", l)][m] = None

    meta = {}
    for c in taxonomy.categories:
        meta[("category", c)] = {"comparability": EV_TO_MOM[cat_evidence[c]], "note": EV_NOTE[cat_evidence[c]]}
    for dim in ("domain", "energy", "goal"):
        for k in [k for k in M if k[0] == dim]:
            meta[k] = {"comparability": hm.NOT_COMPARABLE if k[1] == hc.UNMAPPED_VALUE else hm.WITH_CAVEATS,
                       "note": "Legacy values from activity-level rules where an exact equivalent exists, else from the legacy row label."}
    for l in hc.BUDGET_LINE_ORDER:
        meta[("line", l)] = {"comparability": LINE_COMPARABILITY[l], "note": CAPTURE_SENSITIVE_LINES.get(l, "")}
    for a in hm.ANCHOR_ORDER:
        meta[("anchor", a)] = {"comparability": hm.ANCHOR_COMPARABILITY[a][0], "note": hm.ANCHOR_COMPARABILITY[a][1]}
    for d in hm.DEVICE_ORDER:
        meta[("device", d)] = {"comparability": hm.DEVICE_COMPARABILITY[d][0], "note": hm.DEVICE_COMPARABILITY[d][1]}

    return {
        "data": data, "taxonomy": taxonomy, "wide": wide, "W": W, "months": months, "regime": regime, "days": days,
        "segments": segments, "baseline": baseline, "recs": recs, "M": dict(M), "meta": meta,
        "evidence": evidence, "cat_status": cat_status, "cat_evidence": cat_evidence, "cat_capture": cat_capture,
        "daily": daily, "day_info": day_info,
    }


def per_day(ctx: dict, key: tuple[str, str], month: str) -> float | None:
    v = ctx["M"].get(key, {}).get(month)
    return None if v is None else v / ctx["days"][month]


def series(ctx: dict, key: tuple[str, str], months: list[str]) -> list[float]:
    return [per_day(ctx, key, m) for m in months if per_day(ctx, key, m) is not None]


def segment_total(ctx: dict, key: tuple[str, str], seg: str) -> tuple[float | None, int, int]:
    """(total minutes, calendar days of the available months, available months)."""
    tot, days, n = 0.0, 0, 0
    for m in ctx["segments"][seg]:
        v = ctx["M"].get(key, {}).get(m)
        if v is None:
            continue
        tot += v
        days += ctx["days"][m]
        n += 1
    return (tot if n else None), days, n


def analytical_total(ctx: dict, seg: str) -> float:
    return segment_total(ctx, ("kpi", "ANALYTICAL_TOTAL"), seg)[0] or 0.0


def baseline_stats(ctx: dict, key: tuple[str, str]) -> dict:
    vals = series(ctx, key, ctx["baseline"])
    trend, change = trend_label(vals)
    c = cv(vals)
    if trend in ("RISING", "FALLING") and (c or 0) >= VOLATILE_CV:
        trend = "VOLATILE"  # month-to-month noise dominates; an OLS trend is not reliable
    return {"n": len(vals), "median": quantile(vals, 0.5), "p25": quantile(vals, 0.25), "p75": quantile(vals, 0.75),
            "min": min(vals) if vals else None, "max": max(vals) if vals else None, "mean": mean(vals),
            "cv": c, "trend": trend, "trend_change": change, "peak2": peak_concentration(vals)}


def position(value: float | None, st: dict) -> str:
    if value is None or st["min"] is None:
        return "N/A"
    if value < st["min"] * (1 - ENVELOPE_TOLERANCE):
        return "BELOW_BASELINE_ENVELOPE"
    if value > st["max"] * (1 + ENVELOPE_TOLERANCE):
        return "ABOVE_BASELINE_ENVELOPE"
    return "WITHIN_BASELINE_ENVELOPE"


def line_breaks(key: str, months: list[str]) -> list[str]:
    """Documented methodology breaks inside `months` (excluding the first) that affect a line / device key."""
    out = []
    targets = LINE_BREAK_TARGETS.get(key, set())
    for month, items in hm.METHOD_BREAKS.items():
        if month not in months[1:] or hm_regime_month(month):
            continue
        for target, desc in items:
            if target in targets or (target == "*" and key in WILDCARD_BREAK_SCOPE.get(month, {key})):
                out.append(f"{month}: {desc}")
                break
    return out


def hm_regime_month(month: str) -> bool:
    """Regime changes (2026-08, 2026-09) are handled as METHODOLOGICAL_BREAK by month_over_month."""
    return month >= "2026-08"


# --------------------------------------------------------------------------
# A. Accounting (unchanged rules)
# --------------------------------------------------------------------------

ACCOUNTING_KEYS = (
    ("calendar_capacity", ("accounting", "calendar_capacity")),
    ("recorded", ("accounting", "recorded")),
    ("overlap", ("accounting", "overlap")),
    ("accounted_unique_tracked", ("accounting", "accounted")),
    ("off_device_life_residual", ("accounting", "unresolved")),
    ("analytical_activity", ("kpi", "ANALYTICAL_TOTAL")),
    ("specified_activity", ("accounting", "specified_activity")),
    ("accounted_excl_estimates", ("derived", "ACCOUNTED_EXCL_ESTIMATES")),
    ("estimates_and_standard_allocations", ("derived", "ESTIMATE_OR_STANDARD")),
    ("vacation_balancing_fill", ("derived", "BALANCING_FILL")),
    ("mapped_to_current_category", None),
    ("unsplit_legacy_multi_category", None),
    ("unmapped_legacy", None),
)


def _mapped_parts(ctx: dict, seg: str) -> dict[str, float]:
    ms = set(ctx["segments"][seg])
    parts = {"mapped_to_current_category": 0.0, "unsplit_legacy_multi_category": 0.0, "unmapped_legacy": 0.0}
    for r in ctx["recs"]:
        if r["month"] not in ms:
            continue
        if r["category"]:
            parts["mapped_to_current_category"] += r["minutes"]
        elif r["category_status"] == hc.CAT_MULTI:
            parts["unsplit_legacy_multi_category"] += r["minutes"]
        else:
            parts["unmapped_legacy"] += r["minutes"]
    return parts


def accounting_summary(ctx: dict) -> list[dict]:
    out = []
    W = ctx["W"]
    for seg in SEGMENT_ORDER:
        ms = ctx["segments"][seg]
        if not ms:
            continue
        cap_total, days, _ = segment_total(ctx, ("accounting", "calendar_capacity"), seg)
        parts = _mapped_parts(ctx, seg)
        hom = ("N — MIXED METHODS (descriptive reference only)" if seg == SEG_MIXED else
               "N — transition / review month" if seg == SEG_TRANS else "Y (single regime)")
        base = {"segment": seg, "segment_label": SEGMENT_LABEL[seg], "months": f"{ms[0]} .. {ms[-1]}",
                "month_count": len(ms), "calendar_days": days, "homogeneous_measurement": hom}
        for name, key in ACCOUNTING_KEYS:
            tot = parts[name] if key is None else segment_total(ctx, key, seg)[0]
            out.append({**base, "metric": name, "minutes": r2(tot), "hours": r2(tot / 60),
                        "min_per_calendar_day": r2(tot / days), "pct_of_capacity": r2(100 * tot / cap_total),
                        "count": "", "denominator": "calendar capacity of the segment (calendar days x 1440 min)"})
        for name, field in (("days_with_evidence", "days_with_data"), ("days_review_status", "days_review_status"),
                            ("days_over_capacity", "days_over_capacity"), ("days_incomplete_off_device", "days_incomplete_off_device")):
            out.append({**base, "metric": name, "minutes": "", "hours": "", "min_per_calendar_day": "", "pct_of_capacity": "",
                        "count": sum(int(W[m][field] or 0) for m in ms), "denominator": "count of calendar days"})
    return out


def monthly_accounting(ctx: dict) -> list[dict]:
    out = []
    for m in ctx["months"]:
        w = ctx["W"][m]
        cap = ctx["days"][m] * DAY_CAPACITY_MIN
        ev = ctx["evidence"][m]
        rec = w["recorded_h"] * 60
        an = ctx["M"][("kpi", "ANALYTICAL_TOTAL")][m]
        mapped = sum((ctx["M"][k][m] or 0.0) for k in ctx["M"] if k[0] == "category")
        unsplit = sum(ctx["M"][k][m] for k in ctx["M"] if k[0] == "unsplit")
        unm = sum(ctx["M"][k][m] for k in ctx["M"] if k[0] == "unmapped")
        row = {
            "month": m, "regime": w["regime"], "calendar_days": ctx["days"][m],
            "days_with_evidence": int(w["days_with_data"]), "days_analyzed": ctx["days"][m],
            "days_review_status": int(w["days_review_status"] or 0), "days_over_capacity": int(w["days_over_capacity"] or 0),
            "days_incomplete_off_device": int(w["days_incomplete_off_device"] or 0),
            "capacity_h": r2(cap / 60), "recorded_h": r2(rec / 60), "overlap_h": r2(w["overlap_h"]),
            "accounted_unique_tracked_h": r2(w["accounted_h"]), "off_device_life_residual_h": r2(w["unresolved_h"]),
            "analytical_activity_h": r2(an / 60), "mapped_to_current_category_h": r2(mapped / 60),
            "unsplit_legacy_h": r2(unsplit / 60), "unmapped_legacy_h": r2(unm / 60),
            "coverage_pct_of_capacity": r2(w["coverage_pct"]),
            "coverage_excl_estimates_pct_of_capacity": r2(100 * ctx["M"][("derived", "ACCOUNTED_EXCL_ESTIMATES")][m] / cap),
            "mapped_pct_of_analytical": r2(100 * mapped / an),
            "residual_basis": "capacity − accounted (legacy)" if w["regime"] == REGIME_LEGACY else "inferred Off-Device residual (current)",
        }
        for c in EVIDENCE_ORDER:
            row[f"evidence_{c}_h"] = r2(ev.get(c, 0.0) / 60)
            row[f"evidence_{c}_pct_of_recorded"] = r2(100 * ev.get(c, 0.0) / rec) if rec else ""
        row["methodology_status"] = w["methodology_status"]
        row["notes_flags"] = w["notes_flags"]
        out.append(row)
    return out


# --------------------------------------------------------------------------
# B. Current categories / subcategories / Final Detail hierarchy
# --------------------------------------------------------------------------

def _segment_cols(ctx: dict, key: tuple[str, str], row: dict) -> dict:
    for seg in SEGMENT_ORDER:
        tot, days, n = segment_total(ctx, key, seg)
        an = analytical_total(ctx, seg)
        row[f"{seg}_hours"] = r1(tot / 60) if tot is not None else "n/a"
        row[f"{seg}_min_per_day"] = r1(tot / days) if tot is not None else "n/a"
        row[f"{seg}_pct_of_analytical"] = r2(100 * tot / an) if tot is not None and an else "n/a"
        row[f"{seg}_months_available"] = n
    return row


def _status_mix(st: dict) -> str:
    tot = sum(st.values())
    return " / ".join(f"{k} {100 * v / tot:.0f}%" for k, v in sorted(st.items(), key=lambda x: -x[1])) if tot else ""


def category_table(ctx: dict) -> list[dict]:
    out = []
    for c in ctx["taxonomy"].categories:
        key = ("category", c)
        st = baseline_stats(ctx, key)
        ev = ctx["cat_evidence"][c]
        cur = per_day(ctx, key, "2026-09")
        cur_tot = sum(ctx["cat_status"][(REGIME_CURRENT, c)].values())
        row = _segment_cols(ctx, key, {"row_type": "CURRENT_CATEGORY", "category": c})
        row.update({
            "baseline_median_min_per_day": r1(st["median"]), "baseline_p25_min_per_day": r1(st["p25"]),
            "baseline_p75_min_per_day": r1(st["p75"]), "baseline_cv": r2(st["cv"]), "baseline_trend": st["trend"],
            "transition_2026_08_min_per_day": r1(per_day(ctx, key, "2026-08")), "current_2026_09_min_per_day": r1(cur),
            "current_vs_baseline": position(cur, st) if ev in (EV_COMPARABLE, EV_RESERVATIONS) else "NOT_COMPARED",
            "legacy_evidence": ev, "legacy_evidence_note": EV_NOTE[ev],
            "legacy_mapping_confidence": _status_mix(ctx["cat_status"][(REGIME_LEGACY, c)]),
            "current_share_from_current_only_capture_pct": r1(100 * ctx["cat_capture"][c] / cur_tot) if cur_tot else "",
            "percent_denominator": "segment analytical activity (recorded + Off-Device Life residual, as in the monthly Standard Report)",
        })
        out.append(row)
    for kind, label, note in (
            ("unsplit", "MAPPING_GAP_UNSPLIT", "Legacy records that belong to one of the listed current categories but cannot be split; kept outside every category total."),
            ("unmapped", "MAPPING_GAP_UNMAPPED", "Legacy records with no defensible current mapping; kept outside every category total.")):
        for key in sorted(k for k in ctx["M"] if k[0] == kind):
            row = _segment_cols(ctx, key, {"row_type": label, "category": key[1]})
            row.update({"legacy_evidence": "NOT_A_CURRENT_CATEGORY", "legacy_evidence_note": note,
                        "percent_denominator": "segment analytical activity"})
            out.append(row)
    return out


def subcategory_table(ctx: dict) -> list[dict]:
    out = []
    cats = ctx["taxonomy"].categories
    for key in sorted((k for k in ctx["M"] if k[0] == "subcategory"), key=lambda k: (cats.index(k[1].split(" › ")[0]), k[1])):
        cat, sub = key[1].split(" › ", 1)
        row = _segment_cols(ctx, key, {"category": cat, "subcategory": sub,
                                       "subcategory_source": "LEGACY_NOT_RECORDED" if sub == SUB_NOT_RECORDED else "CURRENT_TAXONOMY_OR_FINAL"})
        row["note"] = "Legacy record mapped to the category only; its current subcategory is not known." if sub == SUB_NOT_RECORDED else ""
        out.append(row)
    return out


def detail_hierarchy(ctx: dict) -> list[dict]:
    """Annual Final Detail Analysis: segment x device x category x subcategory x domain x energy x goal x mapping status."""
    if "_detail" in ctx:
        return ctx["_detail"]
    agg = defaultdict(float)
    for seg in (SEG_LEGACY, SEG_TRANS, SEG_CUR):
        ms = set(ctx["segments"][seg])
        for r in ctx["recs"]:
            if r["month"] not in ms:
                continue
            cat = r["category"] or (f"(unsplit) {r['candidate_categories'].replace('|', ' | ')}" if r["candidate_categories"] else "(unmapped)")
            sub = r["subcategory"] or (SUB_NOT_RECORDED if r["category"] else r["legacy_label"])
            agg[(seg, r["device_class"], cat, sub, r["domain"] or hc.UNMAPPED_VALUE, r["energy"] or hc.UNMAPPED_VALUE,
                 r["goal"] or hc.UNMAPPED_VALUE, r["category_status"])] += r["minutes"]
    out = []
    for k in sorted(agg, key=lambda k: (SEGMENT_ORDER.index(k[0]), -agg[k], k)):
        seg = k[0]
        an = analytical_total(ctx, seg)
        days = sum(ctx["days"][m] for m in ctx["segments"][seg])
        out.append({"segment": seg, "device_class": k[1], "category": k[2], "subcategory": k[3], "domain": k[4],
                    "energy": k[5], "goal": k[6], "mapping_status": k[7], "minutes": round(agg[k], 3),
                    "hours": round(agg[k] / 60, 2), "min_per_day": round(agg[k] / days, 2),
                    "pct_of_analytical": round(100 * agg[k] / an, 3),
                    "is_current_category": "N" if k[2].startswith("(") else "Y"})
    ctx["_detail"] = out
    return out


# --------------------------------------------------------------------------
# F. Domain / energy / goal + KPI
# --------------------------------------------------------------------------

def dimension_table(ctx: dict) -> list[dict]:
    out = []
    tax = ctx["taxonomy"]
    for dim, values in (("domain", tax.domains), ("energy", tax.energies), ("goal", tax.goals)):
        for v in list(values) + [hc.UNMAPPED_VALUE]:
            key = (dim, v)
            st = baseline_stats(ctx, key)
            row = _segment_cols(ctx, key, {"dimension": dim, "value": v})
            for seg in SEGMENT_ORDER:
                mapped = sum(segment_total(ctx, (dim, x), seg)[0] or 0.0 for x in values)
                tot = segment_total(ctx, key, seg)[0] or 0.0
                row[f"{seg}_pct_of_mapped_{dim}"] = r2(100 * tot / mapped) if mapped and v != hc.UNMAPPED_VALUE else ""
            conf = defaultdict(float)
            for r in ctx["recs"]:
                if r["regime"] == REGIME_LEGACY and (r[dim] or hc.UNMAPPED_VALUE) == v:
                    conf[r[f"{dim}_status"]] += r["minutes"]
            row.update({"baseline_median_min_per_day": r1(st["median"]), "baseline_p25_min_per_day": r1(st["p25"]),
                        "baseline_p75_min_per_day": r1(st["p75"]), "baseline_cv": r2(st["cv"]),
                        "current_2026_09_min_per_day": r1(per_day(ctx, key, "2026-09")),
                        "legacy_mapping_confidence": _status_mix(conf),
                        "percent_denominator": "pct_of_analytical: segment analytical activity; pct_of_mapped: excludes (Unmapped legacy)",
                        "note": ("Legacy labels without a defensible current meaning (e.g. 'Recovery / Rest' without an exact activity "
                                 "equivalent, vacation fill)." if v == hc.UNMAPPED_VALUE else "")})
            out.append(row)
    return out


def kpi_table(ctx: dict) -> list[dict]:
    """Monthly Standard Report KPIs per segment (same definitions as standard_report.py)."""
    out = []
    tax = ctx["taxonomy"]
    for seg in SEGMENT_ORDER:
        ms = ctx["segments"][seg]
        days = sum(ctx["days"][m] for m in ms)
        an = analytical_total(ctx, seg)
        g = {x: segment_total(ctx, ("goal", x), seg)[0] or 0.0 for x in tax.goals}
        e = {x: segment_total(ctx, ("energy", x), seg)[0] or 0.0 for x in tax.energies}
        games = segment_total(ctx, ("category", "Games"), seg)[0] or 0.0
        ent = segment_total(ctx, ("category", "Entertainment"), seg)[0] or 0.0
        soc = segment_total(ctx, ("kpi", "SOCIAL_CONSUMPTION"), seg)[0] or 0.0
        unsplit_dig = sum(segment_total(ctx, k, seg)[0] or 0.0 for k in ctx["M"] if k[0] == "unsplit" and "Games" in k[1])
        cap = segment_total(ctx, ("accounting", "calendar_capacity"), seg)[0]
        acc = segment_total(ctx, ("accounting", "accounted"), seg)[0]
        res = segment_total(ctx, ("accounting", "unresolved"), seg)[0]
        legacy = seg in (SEG_LEGACY, SEG_BASE, SEG_MIXED)
        rows = [
            ("clock_capacity", cap, "min", "Number of days × 24 hours."),
            ("unique_tracked", acc, "min", "Corrected tracked − tracked overlap (legacy: recorded − day-level overlap)."),
            ("off_device_life_residual", res, "min", "Capacity − unique tracked (inferred; not recorded time)."),
            ("tracked_overlap", segment_total(ctx, ("accounting", "overlap"), seg)[0], "min", "Σ max(0, day − 1440)."),
            ("analytical_activity", an, "min", "All category rows incl. Off-Device Life; can exceed capacity by the overlap."),
            ("clock_coverage_pct", 100 * (acc + res) / cap, "%", "(Unique tracked + Off-Device Life) / capacity."),
            ("unique_tracked_coverage_pct", 100 * acc / cap, "%", "Unique tracked / capacity."),
            ("investment_ratio_pct", 100 * g["Investment"] / sum(g.values()), "%", "Investment / (Investment + Maintenance + Consumption); excludes (Unmapped legacy) goal."),
            ("recovery_share_pct", 100 * e["Recovery"] / an, "%", "Recovery / analytical activity."),
            ("recovery_per_day", e["Recovery"] / days, "min/day", "Recovery / calendar days."),
            ("investment_per_day", g["Investment"] / days, "min/day", "Investment / calendar days."),
            ("maintenance_per_day", g["Maintenance"] / days, "min/day", "Maintenance / calendar days."),
            ("deep_per_day", e["Deep"] / days, "min/day", "Deep energy / calendar days."),
            ("passive_per_day", e["Passive"] / days, "min/day", "Passive energy / calendar days."),
            ("consumption_proxy_games", games, "min", "Category Games."),
            ("consumption_proxy_entertainment", ent, "min", "Category Entertainment."),
            ("consumption_proxy_social", soc, "min", "Social Networking with Goal = Consumption."),
            ("consumption_proxy_total", games + ent + soc, "min", "Games + Entertainment + Social Networking/Consumption (standard_report.py)."),
            ("consumption_proxy_pct", 100 * (games + ent + soc) / an, "%", "Consumption proxy / analytical activity."),
            ("unsplit_legacy_digital_leisure", unsplit_dig, "min", "Legacy Entertainment device rows (Entertainment, Games or Social Networking; not split)."),
        ]
        for name, val, unit, definition in rows:
            note = "Mixed-method total." if seg == SEG_MIXED else ""
            if legacy and name.startswith("consumption_proxy"):
                note = (note + " Legacy proxy is a lower bound: unsplit legacy digital leisure is reported separately.").strip()
            out.append({"segment": seg, "kpi": name, "value": r2(val), "unit": unit,
                        "per_day": r2(val / days) if unit == "min" and val is not None else "",
                        "definition": definition, "comparability_note": note})
    return out


# --------------------------------------------------------------------------
# C. Devices
# --------------------------------------------------------------------------

DEVICE_ROWS = (
    ("PHONE", ("device", "PHONE"), "Y", "AppleScreenTime", "Phone (legacy: manual transcription of Screen Time; current: Apple Screen Time ingestion)"),
    ("LAPTOP", ("device", "LAPTOP"), "COMPONENT", "ActivityWatch laptop", "Laptop (component of COMPUTERS_TOTAL)"),
    ("DESKTOP", ("device", "DESKTOP"), "COMPONENT", "ActivityWatch desktop", "Desktop (component of COMPUTERS_TOTAL)"),
    ("COMPUTERS_TOTAL", ("device", "COMPUTERS_TOTAL"), "Y", "ActivityWatch (laptop + desktop)", "Computers total = laptop + desktop (Aug 2026: unsplit)"),
    ("TV", ("device", "TV"), "Y", "Habit (TV Time tracking)", "TV (manual in both regimes; inside Habit in the current system)"),
    ("OFF_DEVICE_RECORDED", ("device", "OFF_DEVICE_RECORDED"), "Y", "Habit (other rows)", "Manually recorded off-device activity (legacy incl. household estimates and vacation fill)"),
    ("HABIT_EQUIVALENT_MANUAL", ("derived", "HABIT_EQUIVALENT_MANUAL"), "ROLLUP", "Habit", "Manual records incl. TV = the monthly report's 'Habit' device (rollup; do not add)"),
    ("OFF_DEVICE_SPECIFIED", ("derived", "OFF_DEVICE_SPECIFIED"), "SUBSET", "Habit (other rows)", "Manual off-device records excl. household estimates and vacation fill (subset)"),
    ("OFF_DEVICE_LIFE_RESIDUAL", ("accounting", "unresolved"), "OUTSIDE_RECORDED", "Off-Device", "Off-Device Life — inferred residual (capacity − unique tracked); not a device, not recorded time"),
    ("DIGITAL_TOTAL", ("derived", "DIGITAL_TOTAL"), "ROLLUP", "", "Phone + computers + TV (rollup; do not add)"),
    ("DIGITAL_EXCL_SYSTEM", ("derived", "DIGITAL_EXCL_SYSTEM"), "SUBSET", "", "Digital excluding current-only system / unclassified capture"),
)
ADDITIVITY = {"Y": "Y", "COMPONENT": "COMPONENT (inside COMPUTERS_TOTAL — do not add twice)", "SUBSET": "SUBSET (do not add)",
              "ROLLUP": "ROLLUP (do not add)", "OUTSIDE_RECORDED": "Y in analytical activity / N in recorded time"}


def device_table(ctx: dict) -> list[dict]:
    out = []
    for name, key, additive, current_name, desc in DEVICE_ROWS:
        st = baseline_stats(ctx, key)
        cur = per_day(ctx, key, "2026-09")
        row = _segment_cols(ctx, key, {"device_class": name, "monthly_report_device": current_name, "description": desc,
                                       "additive": ADDITIVITY[additive]})
        for seg in SEGMENT_ORDER:
            tot = segment_total(ctx, key, seg)[0]
            rec = segment_total(ctx, ("accounting", "recorded"), seg)[0]
            row[f"{seg}_pct_of_recorded"] = r2(100 * tot / rec) if tot is not None and rec and additive != "OUTSIDE_RECORDED" else "n/a"
        for m in ctx["months"]:
            v = per_day(ctx, key, m)
            row[f"{m}_min_per_day"] = r1(v) if v is not None else "n/a"
        row.update({"baseline_median_min_per_day": r1(st["median"]), "baseline_cv": r2(st["cv"]), "baseline_trend": st["trend"],
                    "current_vs_baseline": position(cur, st),
                    "comparability": ctx["meta"].get(key, {}).get("comparability", hm.WITH_CAVEATS),
                    "comparability_note": ctx["meta"].get(key, {}).get("note", ""),
                    "percent_denominator": "pct_of_analytical: segment analytical activity; pct_of_recorded: segment recorded time"})
        out.append(row)
    return out


# --------------------------------------------------------------------------
# D. Anchor activities (current subcategories)
# --------------------------------------------------------------------------

ANCHOR_LIMITATIONS = {
    "SLEEP": "Legacy manual decimal hours vs current Habit screenshot-inferred durations; legacy naps from Nov 2025 sit in the household estimate (Off-Device Life), not in Sleep.",
    "WORKOUT": "Manual in legacy; current Habit 'Inferred'. Intensity / quality not recorded.",
    "MEDITATION": "Fixed-length routine values in legacy; current is Target_Derived.",
    "FAMILY_TIME": "Manual in both regimes (in-person time, filed under Social Networking by the current taxonomy); event-driven.",
    "FRIENDS_TIME": "Off-device only; the legacy PC 'Friends' (online gaming) row maps to Games, not here.",
    "MOTORCYCLE": "Manual; mixes leisure riding and transport — purpose not recorded.",
    "READING_JOURNALING": "Legacy row combines Read a book and Journaling (from Oct 2025); cannot be split.",
}


def _daily_values(ctx: dict, key: str, months: list[str]) -> list[float]:
    ms = set(months)
    return [ctx["daily"][d].get(key, 0.0) for d in sorted(ctx["day_info"]) if d[:7] in ms]


def anchor_table(ctx: dict) -> list[dict]:
    out = []
    for a in hm.ANCHOR_ORDER:
        key = ("anchor", a)
        st = baseline_stats(ctx, key)
        row = {"anchor": a, "current_category_subcategory": hc.ANCHOR_LABEL[a]}
        for m in ctx["months"]:
            row[f"{m}_min_per_day"] = r1(per_day(ctx, key, m))
        for seg in (SEG_LEGACY, SEG_BASE, SEG_TRANS, SEG_CUR):
            tot, days, _ = segment_total(ctx, key, seg)
            dv = _daily_values(ctx, "anchor:" + a, ctx["segments"][seg])
            active = [v for v in dv if v > 0]
            row.update({f"{seg}_hours": r1(tot / 60), f"{seg}_min_per_day": r1(tot / days),
                        f"{seg}_days_active_pct": r1(100 * len(active) / len(dv)) if dv else "",
                        f"{seg}_min_per_active_day": r1(mean(active)) if active else "",
                        f"{seg}_daily_p10": r1(quantile(dv, 0.10)), f"{seg}_daily_p50": r1(quantile(dv, 0.50)),
                        f"{seg}_daily_p90": r1(quantile(dv, 0.90))})
        cur = per_day(ctx, key, "2026-09")
        row.update({"baseline_median_min_per_day": r1(st["median"]), "baseline_p25_min_per_day": r1(st["p25"]),
                    "baseline_p75_min_per_day": r1(st["p75"]), "baseline_cv": r2(st["cv"]), "baseline_trend": st["trend"],
                    "current_vs_baseline": position(cur, st), "comparability": hm.ANCHOR_COMPARABILITY[a][0],
                    "comparability_note": hm.ANCHOR_COMPARABILITY[a][1], "limitations": ANCHOR_LIMITATIONS[a]})
        out.append(row)
    return out


# --------------------------------------------------------------------------
# Day type, associations, EDA helpers
# --------------------------------------------------------------------------

DAYTYPE_KEYS = (
    ("line:PRODUCTIVITY_FINANCE_AND_EDUCATION", "Productivity & Finance + Education"),
    ("line:DISCRETIONARY_CONSUMPTION", "Games + Entertainment + Social Networking (consumption)"),
    ("line:IN_PERSON_RELATIONSHIPS", "Social Networking › Family + Social Time Tracking"),
    ("anchor:SLEEP", hc.ANCHOR_LABEL["SLEEP"]),
    ("anchor:WORKOUT", hc.ANCHOR_LABEL["WORKOUT"]),
    ("category:Travel", "Travel"),
    ("category:Creativity", "Creativity"),
    ("DIGITAL_TOTAL", "Digital total (phone + computers + TV)"),
    ("line:OFF_DEVICE_LIFE", "Off-Device Life"),
    ("energy:Deep", "Deep energy"),
    ("goal:Investment", "Investment"),
)


def daytype_table(ctx: dict) -> list[dict]:
    out = []
    for seg in (SEG_BASE, SEG_CUR):
        ms = set(ctx["segments"][seg])
        dates = sorted(d for d in ctx["day_info"] if d[:7] in ms)
        wk = [d for d in dates if date.fromisoformat(d).weekday() < 5]
        we = [d for d in dates if date.fromisoformat(d).weekday() >= 5]
        for key, label in DAYTYPE_KEYS:
            a = mean([ctx["daily"][d].get(key, 0.0) for d in wk])
            b = mean([ctx["daily"][d].get(key, 0.0) for d in we])
            out.append({"segment": seg, "metric": key, "label": label, "weekday_min_per_day": r1(a), "weekend_min_per_day": r1(b),
                        "weekend_to_weekday_ratio": r2(b / a) if a else "", "weekdays": len(wk), "weekend_days": len(we),
                        "note": "Mon–Fri vs Sat–Sun; public holidays and vacations are not separated."})
    return out


ASSOCIATION_PAIRS = (
    ("line:PRODUCTIVITY_FINANCE_AND_EDUCATION", "line:DISCRETIONARY_CONSUMPTION"),
    ("anchor:SLEEP", "line:PRODUCTIVITY_FINANCE_AND_EDUCATION"),
    ("anchor:SLEEP", "line:DISCRETIONARY_CONSUMPTION"),
    ("line:IN_PERSON_RELATIONSHIPS", "line:PRODUCTIVITY_FINANCE_AND_EDUCATION"),
    ("line:IN_PERSON_RELATIONSHIPS", "line:DISCRETIONARY_CONSUMPTION"),
    ("anchor:WORKOUT", "anchor:SLEEP"),
    ("line:PRODUCTIVITY_FINANCE_AND_EDUCATION", "UNRESOLVED"),
)


def association_table(ctx: dict) -> list[dict]:
    out = []
    for seg in (SEG_BASE, SEG_CUR):
        ms = set(ctx["segments"][seg])
        dates = sorted(d for d in ctx["day_info"] if d[:7] in ms)
        for a, b in ASSOCIATION_PAIRS:
            rho = spearman([ctx["daily"][d].get(a, 0.0) for d in dates], [ctx["daily"][d].get(b, 0.0) for d in dates])
            label = "INSUFFICIENT_DATA" if len(dates) < 60 or rho is None else (
                "DESCRIPTIVE_ASSOCIATION" if abs(rho) >= ASSOCIATION_RHO else "NO_MATERIAL_ASSOCIATION")
            out.append({"segment": seg, "metric_a": a, "metric_b": b, "days": len(dates), "spearman_rho": r2(rho), "label": label,
                        "caveat": "Same-day association between minutes that share a fixed 24-h day: negative values are partly mechanical. Not causal."})
    return out


def top_activities(ctx: dict, limit: int = 30) -> list[dict]:
    out = []
    for seg in (SEG_LEGACY, SEG_TRANS, SEG_CUR):
        rows = [r for r in detail_hierarchy(ctx) if r["segment"] == seg]
        for rank, r in enumerate(rows[:limit], start=1):
            out.append({**r, "rank": rank})
    return out


def concentration_table(ctx: dict) -> list[dict]:
    """Same metrics as exploratory_analysis.concentration (Top1/3/5 shares and HHI)."""
    out = []
    for seg in (SEG_MIXED, SEG_BASE, SEG_TRANS, SEG_CUR):
        for dim in ("category", "subcategory", "domain", "energy", "goal"):
            vals = sorted((segment_total(ctx, k, seg)[0] or 0.0 for k in ctx["M"] if k[0] == dim and k[1] != hc.UNMAPPED_VALUE), reverse=True)
            tot = sum(vals)
            sh = [v / tot for v in vals] if tot else []
            out.append({"segment": seg, "dimension": dim, "top1_pct": r2(100 * sum(sh[:1])), "top3_pct": r2(100 * sum(sh[:3])),
                        "top5_pct": r2(100 * sum(sh[:5])), "hhi": round(sum(s * s for s in sh), 5),
                        "denominator": "mapped values of the dimension (unsplit / unmapped legacy excluded)"})
    return out


def taxonomy_review_candidates(ctx: dict) -> list[dict]:
    """exploratory_analysis.taxonomy_candidates on the annual hierarchy + legacy mapping reasons."""
    import exploratory_analysis as ea
    out = []
    for seg in (SEG_LEGACY, SEG_CUR):
        rows = [{"Category": r["category"], "Subcategory": r["subcategory"], "Domain": r["domain"], "Energy": r["energy"],
                 "Goal": r["goal"], "Duration_sec": r["minutes"] * 60} for r in detail_hierarchy(ctx) if r["segment"] == seg]
        for c in ea.taxonomy_candidates(rows, sum(x["Duration_sec"] for x in rows)):
            reasons = c["Review_Reasons"]
            if c["Category"].startswith("(unsplit)"):
                reasons += ";LEGACY_UNSPLIT_MULTI_CATEGORY"
            elif c["Category"] == "(unmapped)":
                reasons += ";LEGACY_UNMAPPED"
            elif c["Subcategory"] == SUB_NOT_RECORDED:
                reasons += ";LEGACY_SUBCATEGORY_NOT_RECORDED"
            out.append({"segment": seg, **{k.lower(): v for k, v in c.items() if k != "Review_Reasons"}, "review_reasons": reasons})
    return out


# --------------------------------------------------------------------------
# G. Monthly trends (current taxonomy)
# --------------------------------------------------------------------------

def monthly_trends(ctx: dict) -> list[dict]:
    """Monthly values for current categories, domains, energies, goals, budget
    lines, anchors and device classes, with month-over-month classification by
    historical_monthly.month_over_month (same rules as the pipeline)."""
    long_rows = []
    for key in sorted(k for k in ctx["M"] if k[0] in ("category", "domain", "energy", "goal", "line", "anchor", "device", "accounting")):
        comp = hm.WITH_CAVEATS if key[0] == "accounting" else ctx["meta"].get(key, {}).get("comparability", hm.WITH_CAVEATS)
        dim = {"anchor": "activity_anchor"}.get(key[0], key[0])
        for m in ctx["months"]:
            v = ctx["M"][key][m]
            if v is None:
                continue
            an = ctx["M"][("kpi", "ANALYTICAL_TOTAL")][m]
            long_rows.append({"month": m, "regime": ctx["regime"][m], "dimension": dim, "value": key[1],
                              "minutes": round(v, 4), "hours": round(v / 60, 4), "min_per_day": round(v / ctx["days"][m], 4),
                              "share_pct": round(100 * v / an, 4), "comparability": comp, "_dim": key[0]})
    mom = {(r["month"], r["dimension"], r["metric"]): r for r in hm.month_over_month(long_rows, ctx["wide"])}
    out = []
    for r in long_rows:
        c = mom.get((r["month"], r["dimension"], r["value"]), {})
        ctype, reason = c.get("change_type", ""), c.get("reason", "")
        if ctype == "BEHAVIORAL_CANDIDATE" and r["_dim"] in ("line", "device"):
            brk = line_breaks(r["value"], [c.get("previous_month", ""), r["month"]])
            if brk:
                ctype, reason = "POSSIBLE_METHODOLOGICAL", brk[0]
        out.append({"month": r["month"], "regime": r["regime"], "dimension": r["_dim"], "metric": r["value"],
                    "hours": r["hours"], "min_per_day": r["min_per_day"], "share_pct_of_analytical": r["share_pct"],
                    "previous_min_per_day": c.get("previous_min_per_day", ""), "abs_change_min_per_day": c.get("abs_change_min_per_day", ""),
                    "pct_change": c.get("pct_change", ""), "material": c.get("material", ""), "change_type": ctype,
                    "change_reason": reason, "comparability": r["comparability"]})
    return out


# --------------------------------------------------------------------------
# Findings (rule-based, current taxonomy)
# --------------------------------------------------------------------------

def klabel(key: tuple[str, str]) -> str:
    dim, val = key
    if dim == "line":
        return hc.BUDGET_LINE_LABEL[val]
    if dim == "anchor":
        return hc.ANCHOR_LABEL[val]
    if dim == "device":
        return f"Device {val.replace('_', ' ').lower()}"
    if dim == "derived":
        return {"DIGITAL_TOTAL": "Digital total", "OFF_DEVICE_SPECIFIED": "Manual off-device records"}.get(val, val)
    return val


def findings(ctx: dict, daytype: list[dict], assoc: list[dict]) -> list[dict]:
    out = []
    W = ctx["W"]
    base = ctx["baseline"]
    nb = len(base)

    def add(fid, theme, desc, evidence, periods, persistence, cls, conf, method, interp, implication):
        out.append({"finding_id": fid, "theme": theme, "description": desc, "evidence": evidence,
                    "periods_supporting": periods, "persistence": persistence, "classification": cls, "confidence": conf,
                    "methodological_explanation": method, "prudent_interpretation": interp, "budget_implication": implication})

    lines = [("line", l) for l in hc.BUDGET_LINE_ORDER if l != "UNMAPPED_LEGACY"]

    def top_uses(months):
        vals = [(k, quantile(series(ctx, k, months), 0.5)) for k in lines]
        return sorted(vals, key=lambda x: -(x[1] or 0))
    tb, tc = top_uses(base), top_uses(ctx["segments"][SEG_CUR])
    same_top = [x[0] for x in tb[:3]] == [x[0] for x in tc[:3]]
    add("F01", "Capacity structure",
        "Largest uses of the 24-h day (legacy baseline, median per day): " + "; ".join(f"{klabel(k)} {hm_fmt(v)}" for k, v in tb[:4]) + ".",
        "Current 2026-09: " + "; ".join(f"{klabel(k)} {hm_fmt(v)}" for k, v in tc[:4]) + ".",
        f"{base[0]}..{base[-1]}; 2026-09", f"{nb}/{nb} baseline months + current", OBSERVED, "HIGH" if same_top else "MEDIUM",
        "Off-Device Life mixes the legacy household estimate with the inferred residual.",
        "The top three uses of the day are " + ("the same" if same_top else "not in the same order") + " in both regimes.",
        "Any budget starts from these lines; they leave a limited discretionary remainder.")

    stable_keys = [k for k in lines if k[1] != "OFF_DEVICE_LIFE"] + [("anchor", a) for a in hm.ANCHOR_ORDER if a != "SLEEP"] + \
                  [("device", d) for d in ("PHONE", "LAPTOP", "DESKTOP", "COMPUTERS_TOTAL", "TV")]
    stable, volatile = [], []
    for k in stable_keys:
        st = baseline_stats(ctx, k)
        if st["mean"] is None or st["mean"] < 15 or st["cv"] is None:
            continue
        if st["cv"] <= STABLE_CV:
            stable.append((k, st))
        elif st["cv"] >= VOLATILE_CV:
            volatile.append((k, st))
    if stable:
        add("F02", "Stable allocations",
            "Low month-to-month variability in the legacy baseline (CV ≤ 0.15, ≥ 15 min/day): "
            + ", ".join(f"{klabel(k)} (median {hm_fmt(s['median'])}, CV {s['cv']:.2f})" for k, s in stable) + ".",
            "Coefficient of variation of monthly min/day over the baseline window.", f"{base[0]}..{base[-1]}",
            f"{nb}/{nb} baseline months", OBSERVED, "HIGH",
            "Low variability in manual records can also reflect routine values; Off-Device Life is excluded because its legacy household part is a template.",
            "These allocations behaved like fixed structure of the month.", "Candidates for narrow reference ranges.")
    for i, (k, s) in enumerate(volatile, start=1):
        ms = [m for m in base if per_day(ctx, k, m) is not None]
        top = sorted(((m, per_day(ctx, k, m)) for m in ms), key=lambda x: -x[1])[:2]
        peak_driven = (s["peak2"] or 0) >= 2 * 2 / nb
        add(f"F03.{i}", "High variability",
            f"{klabel(k)} varies strongly between months (CV {s['cv']:.2f}; range {hm_fmt(s['min'])}–{hm_fmt(s['max'])} per day).",
            f"Peak months: {', '.join(f'{m} {hm_fmt(v)}' for m, v in top)}; two largest months = {100 * (s['peak2'] or 0):.0f}% of the window.",
            f"{base[0]}..{base[-1]}", "Peak-driven (1–2 atypical months)" if peak_driven else "Spread across months", OBSERVED, "MEDIUM",
            "Event-driven activities (holidays, visits, trips) are expected to vary.",
            "Real variation in the records" + (", dominated by one or two exceptional months." if peak_driven else "."),
            "Budget as a range or a quarterly allowance rather than a fixed monthly amount.")

    tid = 0
    for k in stable_keys + [("derived", "DIGITAL_TOTAL"), ("derived", "OFF_DEVICE_SPECIFIED")]:
        st = baseline_stats(ctx, k)
        if st["trend"] not in ("RISING", "FALLING") or (st["mean"] or 0) < 5:
            continue
        if k[0] == "anchor":
            brk = [f"{m}: {d}" for m, items in hm.METHOD_BREAKS.items() if m in base[1:] for t, d in items if t == f"anchor:{k[1]}"]
        else:
            brk = line_breaks(k[1], base)
        tid += 1
        first = mean([per_day(ctx, k, m) for m in base[:3]])
        last = mean([per_day(ctx, k, m) for m in base[-3:]])
        add(f"F04.{tid}", "Within-legacy trend",
            f"{klabel(k)} {st['trend'].lower()} across the legacy baseline (fitted change {st['trend_change']:+.0f} min/day; mean {hm_fmt(st['mean'])}).",
            f"Average of first 3 baseline months {hm_fmt(first)} → last 3 months {hm_fmt(last)} per day; current {hm_fmt(per_day(ctx, k, '2026-09'))}.",
            f"{base[0]}..{base[-1]}", f"OLS over {nb} months", METHOD if brk else HYPOTHESIS, "LOW" if brk else "MEDIUM",
            ("Documented methodology change(s) affecting this metric: " + "; ".join(brk)) if brk else
            "No documented break targets this metric; recording fatigue or routine values cannot be excluded.",
            "Change in recorded behaviour under one method — not proof of a behavioural change.",
            "Confirm with the next current-regime months before setting the budget above or below the historical median.")

    sid = 0
    for k in lines + [("device", d) for d in ("PHONE", "COMPUTERS_TOTAL", "TV")]:
        st = baseline_stats(ctx, k)
        cur, aug = per_day(ctx, k, "2026-09"), per_day(ctx, k, "2026-08")
        pos = position(cur, st)
        if pos not in ("ABOVE_BASELINE_ENVELOPE", "BELOW_BASELINE_ENVELOPE") or abs((cur or 0) - (st["median"] or 0)) < MIN_MATERIAL_MIN_PER_DAY:
            continue
        methodological = k[1] in CAPTURE_SENSITIVE_LINES or k[0] == "device" or ctx["meta"].get(k, {}).get("comparability") != hm.WITH_CAVEATS
        aug_same = aug is not None and position(aug, st) == pos
        sid += 1
        add(f"F05.{sid}", "Change at the regime boundary",
            f"{klabel(k)}: current 2026-09 {hm_fmt(cur)}/day is {pos.split('_')[0].lower()} the legacy baseline envelope ({hm_fmt(st['min'])}–{hm_fmt(st['max'])}, median {hm_fmt(st['median'])}).",
            f"Transition 2026-08: {hm_fmt(aug)}/day ({'same side' if aug_same else 'not on the same side'}).",
            "2026-08 (transition), 2026-09 (current)", "1 closed current month" + (" + transition" if aug_same else ""),
            METHOD if methodological else INSUFFICIENT, "LOW",
            CAPTURE_SENSITIVE_LINES.get(k[1], "Coincides with the change from manual to automatic capture."),
            "Do not read as a behavioural change until several closed current months exist.",
            "Use current-regime months as the reference for this line once 3+ months are closed; keep its range open.")

    cov = {m: W[m]["coverage_pct"] for m in ctx["segments"][SEG_LEGACY]}
    med_cov = quantile(list(cov.values()), 0.5)
    low = [m for m, c in cov.items() if c < med_cov - 5]
    if low:
        add("F06", "Tracking completeness",
            f"Legacy coverage is lower in {', '.join(low)} ({', '.join(f'{cov[m]:.1f}%' for m in low)}) than the legacy median ({med_cov:.1f}%).",
            "; ".join(f"{m}: {int(W[m]['days_review_status'])} review days, {int(W[m]['days_incomplete_off_device'] or 0)} incomplete off-device days" for m in low),
            ", ".join(low), f"{len(low)} of {len(cov)} legacy months", METHOD, "HIGH",
            "First month of the manual method (ramp-up) and the final month (off-device rows not recorded 27–31 Jul).",
            "Lower coverage reflects recording completeness, not less activity.", "Excluded from the legacy baseline window.")

    k = ("line", "OFF_DEVICE_LIFE")
    st = baseline_stats(ctx, k)
    cur = per_day(ctx, k, "2026-09")
    est, est_days, _ = segment_total(ctx, ("derived", "ESTIMATE_OR_STANDARD"), SEG_BASE)
    add("F07", "Off-Device Life constraint",
        f"Off-Device Life: legacy baseline median {hm_fmt(st['median'])}/day (range {hm_fmt(st['min'])}–{hm_fmt(st['max'])}); current {hm_fmt(cur)}/day.",
        f"Legacy Off-Device Life includes the household standard allocation / estimates ({hm_fmt(est / est_days)}/day, template values); current is the inferred residual only.",
        f"{base[0]}..{base[-1]}; 2026-09", "All months", OBSERVED, "MEDIUM", CAPTURE_SENSITIVE_LINES["OFF_DEVICE_LIFE"],
        f"Roughly {hm_fmt(min(cur, st['min']))}–{hm_fmt(max(cur, st['max']))} per day go to eating, hygiene, chores, transport and untracked time (order of magnitude, not a measurement).",
        "Reserve this time explicitly before allocating discretionary or goal time; never budget it to zero.")

    dig = baseline_stats(ctx, ("derived", "DIGITAL_TOTAL"))
    dig_ex = per_day(ctx, ("derived", "DIGITAL_EXCL_SYSTEM"), "2026-09")
    add("F08", "Digital vs non-digital",
        f"Recorded digital time (phone + computers + TV): legacy baseline median {hm_fmt(dig['median'])}/day; current {hm_fmt(per_day(ctx, ('derived', 'DIGITAL_TOTAL'), '2026-09'))}/day ({hm_fmt(dig_ex)} excluding system / unclassified capture).",
        f"Manual off-device records (excl. estimates): legacy median {hm_fmt(baseline_stats(ctx, ('derived', 'OFF_DEVICE_SPECIFIED'))['median'])}/day; current {hm_fmt(per_day(ctx, ('derived', 'OFF_DEVICE_SPECIFIED'), '2026-09'))}/day.",
        f"{base[0]}..{base[-1]}; 2026-09", "All months", OBSERVED if position(dig_ex, dig) == "WITHIN_BASELINE_ENVELOPE" else METHOD, "MEDIUM",
        "Legacy transcribed selected Screen Time / ActivityWatch categories; current ingests everything incl. system time.",
        "Screens take a large and persistent share of waking time in both regimes; the exact level depends on capture.",
        "Budget digital time by purpose (category) rather than by device.")

    gid = 0
    for c in ctx["taxonomy"].categories:
        ev = ctx["cat_evidence"][c]
        cur_c = per_day(ctx, ("category", c), "2026-09") or 0
        if ev not in (EV_NONE, EV_PARTIAL, EV_CAPTURE) or (cur_c < 5 and ev != EV_PARTIAL):
            continue
        gid += 1
        add(f"F09.{gid}", "Category comparability limit",
            f"{c}: {ev.replace('_', ' ').lower()} (current {hm_fmt(cur_c)}/day).", EV_NOTE[ev],
            "Regime-specific", "Structural", METHOD, "HIGH", EV_NOTE[ev],
            "Do not read the legacy → current difference of this category as behaviour.",
            "Budget it inside its current-taxonomy line (table J) until current months accumulate.")

    for i, key in enumerate(sorted(k for k in ctx["M"] if k[0] == "unsplit"), start=1):
        tot, days, _ = segment_total(ctx, key, SEG_LEGACY)
        add(f"F10.{i}", "Unsplit legacy records",
            f"{key[1]}: {hm_fmt(tot / days)}/day of legacy time ({tot / 60:.0f} h) cannot be assigned to a single current category.",
            "Legacy device rows without a recorded subcategory (Historical_Mapping_Review_Register.csv).",
            f"{ctx['segments'][SEG_LEGACY][0]}..{ctx['segments'][SEG_LEGACY][-1]}", "All legacy months", METHOD, "HIGH",
            "The legacy row label covers several current categories.",
            "Legacy totals of the candidate categories are understated; compare their sum across regimes.",
            "Budget the candidate categories together (table J line) until a split rule is approved.")

    did = 0
    for r in daytype:
        if r["segment"] != SEG_BASE or r["weekend_to_weekday_ratio"] in ("", None):
            continue
        a, b, ratio = r["weekday_min_per_day"], r["weekend_min_per_day"], r["weekend_to_weekday_ratio"]
        if max(a, b) < 30 or (1 / DAYTYPE_RATIO < ratio < DAYTYPE_RATIO):
            continue
        cur_r = next((x for x in daytype if x["segment"] == SEG_CUR and x["metric"] == r["metric"]), None)
        did += 1
        add(f"F11.{did}", "Weekday vs weekend",
            f"{r['label']}: weekday {hm_fmt(a)} vs weekend {hm_fmt(b)} per day (ratio {ratio:.2f}) in the legacy baseline.",
            f"Current 2026-09: weekday {hm_fmt(cur_r['weekday_min_per_day'])} vs weekend {hm_fmt(cur_r['weekend_min_per_day'])}." if cur_r else "",
            f"{base[0]}..{base[-1]}", f"{r['weekdays']} weekdays / {r['weekend_days']} weekend days", OBSERVED, "MEDIUM",
            "Holidays and vacation days are not separated from weekdays.", "The week has a different structure on weekends.",
            "A weekly execution budget should differ by day type rather than divide monthly totals by 30.")

    aid = 0
    for r in assoc:
        if r["label"] != "DESCRIPTIVE_ASSOCIATION":
            continue
        aid += 1
        add(f"F12.{aid}", "Same-day association",
            f"{r['metric_a']} and {r['metric_b']}: Spearman ρ = {r['spearman_rho']} over {r['days']} days ({r['segment']}).",
            "Annual_Daily_Associations.csv", r["segment"], f"{r['days']} days", HYPOTHESIS, "LOW",
            "Minutes in a fixed 24-h day are compositional: more of one leaves less of another.",
            "Descriptive co-movement only; no causal direction can be inferred.",
            "Treat the two lines as competing for the same hours when setting ranges.")

    w8 = W["2026-08"]
    add("F13", "Transition month",
        f"August 2026 shows overlap {w8['overlap_h']:.1f} h and {int(w8['days_over_capacity'])} days above 24 h; ~1,230 min of Habit adjustments were applied twice (published, not repaired).",
        "Historical_Reconciliation_Report.md §5", "2026-08", "1 month", METHOD, "HIGH",
        "Known double application of manual adjustments; laptop/desktop unsplit.",
        "August is a transition / review month, not a closed comparable month.",
        "Do not use August as a budget reference except as a corroborating direction.")

    falling = [a for a in hm.ANCHOR_ORDER if baseline_stats(ctx, ("anchor", a))["trend"] == "FALLING"]
    if falling:
        add("F14", "Essential activities at risk",
            "Exact-equivalent activities with a falling recorded trend in the baseline: " + ", ".join(hc.ANCHOR_LABEL[a] for a in falling) + ".",
            "; ".join(f"{hc.ANCHOR_LABEL[a]}: first 3 months {hm_fmt(mean([per_day(ctx, ('anchor', a), m) for m in base[:3]]))} → last 3 months "
                      f"{hm_fmt(mean([per_day(ctx, ('anchor', a), m) for m in base[-3:]]))}/day; current {hm_fmt(per_day(ctx, ('anchor', a), '2026-09'))}" for a in falling),
            f"{base[0]}..{base[-1]}; 2026-09", "Trend over baseline", HYPOTHESIS, "MEDIUM",
            "Manual recording in legacy; routine values for some activities.",
            "These activities may be crowded out when other lines grow.",
            "Give them explicit floors so optimisation of other lines cannot absorb them.")

    k = ("line", "DISCRETIONARY_CONSUMPTION")
    st = baseline_stats(ctx, k)
    add("F15", "Reallocation opportunity",
        f"Within the legacy baseline, months at the 25th percentile of Games + Entertainment + Social Networking (consumption) used {hm_fmt((st['median'] or 0) - (st['p25'] or 0))}/day less than the median month ({hm_fmt(st['p25'])} vs {hm_fmt(st['median'])}).",
        "Historically achieved lower level (same method); the current level is not comparable in absolute terms.",
        f"{base[0]}..{base[-1]}", f"{nb} months", HYPOTHESIS, "MEDIUM",
        "Relative difference within one method.",
        "A reduction of this size already happened in real months — plausible, not guaranteed.",
        "Candidate pool for goal-focused reallocation (Scenario B), as a relative change from the current level.")
    return out


# --------------------------------------------------------------------------
# J. Budget baseline and scenarios (current-taxonomy lines)
# --------------------------------------------------------------------------

SUB_LINES = (("Productivity & Finance", "PRODUCTIVITY_FINANCE_AND_EDUCATION"), ("Education", "PRODUCTIVITY_FINANCE_AND_EDUCATION"),
             ("Games", "DISCRETIONARY_CONSUMPTION"), ("Entertainment", "DISCRETIONARY_CONSUMPTION"))


def budget_baseline(ctx: dict) -> list[dict]:
    out = []
    specs = [(l, ("line", l), True) for l in hc.BUDGET_LINE_ORDER] + [(c, ("category", c), False) for c, _ in SUB_LINES]
    parent = dict(SUB_LINES)
    for name, key, additive in specs:
        st = baseline_stats(ctx, key)
        cur, aug = per_day(ctx, key, "2026-09"), per_day(ctx, key, "2026-08")
        if additive:
            comp, protection, label = LINE_COMPARABILITY[name], LINE_PROTECTION[name], hc.BUDGET_LINE_LABEL[name]
        else:
            comp, protection = EV_TO_MOM[ctx["cat_evidence"][name]], "INFORMATION_ONLY"
            label = f"{name} (current category inside {hc.BUDGET_LINE_LABEL[parent[name]]})"
        tot_l, days_l, _ = segment_total(ctx, key, SEG_LEGACY)
        pos = position(cur, st)
        low = high = None
        pending = []
        if name == "UNMAPPED_LEGACY":
            status, conf = "NOT_BUDGETABLE", ""
            pending.append("Unmapped legacy time (vacation fill, undocumented rows); not a planning category.")
        elif not additive:
            status, conf = "INFORMATION_ONLY", "LOW"
            pending.append("Legacy value incomplete (part of the legacy time is unsplit); plan with the parent line.")
        elif comp != hm.WITH_CAVEATS or st["n"] < MIN_BASELINE_MONTHS:
            status, conf = "NO_RANGE_REGIME_SPECIFIC", "LOW"
            pending.append("Comparable evidence exists in one regime only; needs 3+ closed current months.")
        elif pos == "WITHIN_BASELINE_ENVELOPE":
            low, high = round_down(min(st["p25"], cur)), round_up(max(st["p75"], cur))
            status = "DATA_SUPPORTED_RANGE"
            conf = "HIGH" if (st["cv"] or 1) <= 0.10 and name == "SLEEP" else ("MEDIUM" if (st["cv"] or 1) <= 0.30 else "LOW")
        elif protection == "PROTECT_RESERVE":
            low, high = round_down(min(st["p25"], cur)), round_up(max(st["p75"], cur))
            status, conf = "CROSS_REGIME_ENVELOPE", "LOW"
            pending.append("Regimes disagree (legacy estimate + residual vs current residual); range spans both. Narrow after 3+ current months.")
        else:
            status, conf = "NO_RANGE_REGIME_DIVERGENCE", "LOW"
            pending.append("Current level outside the legacy envelope (capture-method effect likely); reference current months once 3+ are closed.")
        if protection == "COMMITMENT_AND_GOAL_DEPENDENT":
            pending.append("Fixed commitments (employment / study hours) are not recorded in the system — user input.")
        if protection in ("GOAL_DEPENDENT", "COMMITMENT_AND_GOAL_DEPENDENT"):
            pending.append("Depends on future goals / projects (not yet defined).")
        out.append({
            "budget_line": name, "label": label, "is_sub_line": "N" if additive else "Y (not additive)",
            "protection_or_flexibility": protection, "comparability": comp,
            "historical_observed_avg_legacy_min_per_day": r1(tot_l / days_l) if tot_l is not None else "n/a",
            "comparable_baseline_median_min_per_day": r1(st["median"]) if comp == hm.WITH_CAVEATS and additive else "",
            "baseline_median_min_per_day": r1(st["median"]), "baseline_p25_min_per_day": r1(st["p25"]),
            "baseline_p75_min_per_day": r1(st["p75"]), "baseline_min_min_per_day": r1(st["min"]),
            "baseline_max_min_per_day": r1(st["max"]), "baseline_cv": r2(st["cv"]), "baseline_trend": st["trend"],
            "transition_2026_08_min_per_day": r1(aug), "current_2026_09_min_per_day": r1(cur), "current_vs_baseline": pos,
            "suggested_low_min_per_day": low if low is not None else "", "suggested_high_min_per_day": high if high is not None else "",
            "range_status": status, "confidence": conf, "user_approved_target": "NOT SET",
            "depends_on_future_goals": "Y" if protection in ("GOAL_DEPENDENT", "COMMITMENT_AND_GOAL_DEPENDENT", "FLEXIBLE", "FLEXIBLE_CEILING") else "N",
            "pending_information": " | ".join(pending),
            "range_rule": f"[floor{ROUND_TO_MIN}(min(P25, current)), ceil{ROUND_TO_MIN}(max(P75, current))] when current lies within [0.8×min, 1.2×max] of baseline months",
        })
    return out


def budget_scenarios(baseline_rows: list[dict]) -> list[dict]:
    out = []
    lines = [b for b in baseline_rows if b["is_sub_line"] == "N" and b.get("protection_or_flexibility") != "NOT_BUDGETABLE"]
    for scen in ("SUSTAINABLE", "GOAL_FOCUSED", "CONSERVATIVE"):
        lows = highs = mids = pool = 0.0
        to_define = []
        for b in lines:
            lo, hi = b["suggested_low_min_per_day"], b["suggested_high_min_per_day"]
            prot = b["protection_or_flexibility"]
            if lo == "" or hi == "":
                s_lo = s_hi = None
                status = "TO_DEFINE"
                to_define.append(b["budget_line"])
                legacy_ref = b["baseline_median_min_per_day"] if b["baseline_median_min_per_day"] != "" else "n/a"
                note = f"Reference only: current {b['current_2026_09_min_per_day']} min/day, legacy median {legacy_ref} min/day."
            else:
                mid = (lo + hi) / 2
                if scen == "SUSTAINABLE":
                    s_lo, s_hi, note = lo, hi, "Data-supported range."
                elif scen == "CONSERVATIVE":
                    if prot.startswith("PROTECT"):
                        s_lo, s_hi, note = round_near(mid), hi, "Protected line held in the upper half of its range."
                    elif prot in ("FLEXIBLE", "FLEXIBLE_CEILING", "GOAL_DEPENDENT"):
                        s_lo, s_hi, note = lo, round_near(mid), "Flexible line held in the lower half of its range."
                    else:
                        s_lo, s_hi, note = lo, hi, "Unchanged range."
                elif prot in ("FLEXIBLE", "FLEXIBLE_CEILING"):
                    s_lo, s_hi = lo, round_near(mid)
                    pool += round_near(mid) - lo
                    note = "Flexible line capped at range midpoint; freed minutes go to the goal pool."
                else:
                    s_lo, s_hi, note = lo, hi, "Unchanged range (protected / commitment)."
                status = "RANGE"
            if s_lo is not None:
                lows += s_lo
                highs += s_hi
                mids += (s_lo + s_hi) / 2
            row = {"scenario": scen, "budget_line": b["budget_line"], "label": b.get("label", b["budget_line"]),
                   "protection_or_flexibility": prot, "status": status,
                   "low_min_per_day": s_lo if s_lo is not None else "", "high_min_per_day": s_hi if s_hi is not None else "",
                   "confidence": b["confidence"], "note": note}
            for p, d in PERIOD_DAYS.items():
                row[f"low_h_per_{p}"] = r1(s_lo * d / 60) if s_lo is not None else ""
                row[f"high_h_per_{p}"] = r1(s_hi * d / 60) if s_hi is not None else ""
            out.append(row)
        extra = []
        if scen == "GOAL_FOCUSED":
            extra.append(("GOAL_POOL (allocation TO_DEFINE)", (pool, pool), "GOAL_POOL", "TO_ASSIGN",
                          "Minutes freed from flexible lines; assign to explicit goals / projects once defined. Not pre-assigned."))
            for b in lines:
                if b["protection_or_flexibility"] == "FLEXIBLE_CEILING" and b["suggested_low_min_per_day"] == "" and b["baseline_median_min_per_day"] != "":
                    extra.append((f"GOAL_POOL_CANDIDATE from {b['budget_line']} (relative)",
                                  (b["baseline_median_min_per_day"] - b["baseline_p25_min_per_day"], b["baseline_median_min_per_day"] - b["baseline_min_min_per_day"]),
                                  "GOAL_POOL", "TO_CONFIRM_NOT_SUMMED",
                                  "Reduction from the line's future current-regime reference: legacy median−P25 to median−min (levels already reached under one method). Not in the capacity check."))
        extra.append(("UNALLOCATED_RESERVE", (RESERVE_MIN_PER_DAY[scen],) * 2, "RESERVE", "TEMPLATE_PARAMETER",
                      f"Margin for interruptions and measurement noise (template default; system day tolerance = {DAY_TOLERANCE_MIN:.0f} min)."))
        for name, (lo, hi), prot, status, note in extra:
            if prot == "RESERVE":
                lows += lo
                highs += hi
                mids += lo
            row = {"scenario": scen, "budget_line": name, "label": name, "protection_or_flexibility": prot, "status": status,
                   "low_min_per_day": r1(lo), "high_min_per_day": r1(hi), "confidence": "LOW" if "CANDIDATE" in name else "", "note": note}
            for p, d in PERIOD_DAYS.items():
                row[f"low_h_per_{p}"] = r1(lo * d / 60)
                row[f"high_h_per_{p}"] = r1(hi * d / 60)
            out.append(row)
        out.append({
            "scenario": scen, "budget_line": "CAPACITY_CHECK", "label": "Capacity check", "protection_or_flexibility": "",
            "status": "FEASIBLE" if lows <= DAY_CAPACITY_MIN else "INFEASIBLE_MINIMUMS_EXCEED_CAPACITY",
            "low_min_per_day": r1(lows), "high_min_per_day": r1(highs), "confidence": "",
            "note": (f"Capacity 1440 min/day. Σ lower bounds incl. reserve = {lows:.0f}; upper bounds = {highs:.0f}; midpoints = {mids:.0f} → "
                     f"{DAY_CAPACITY_MIN - mids:+.0f} min/day left for TO_DEFINE lines ({', '.join(to_define) or 'none'}). Remaining minutes are NOT auto-assigned."),
        })
    return out


# --------------------------------------------------------------------------
# H/I. Mapping inventory, review register, reconciliation, diagnostics
# --------------------------------------------------------------------------

def mapping_inventory(ctx: dict) -> list[dict]:
    """One row per legacy (category, subcategory, device) key with its current destination."""
    agg = defaultdict(lambda: {"min": 0.0, "n": 0, "months": set(), "energy": Counter(), "goal": Counter()})
    rows_of = {}
    for r in ctx["data"]["Historical_Normalized_Records.csv"]:
        if r["accounting_status"] != "INCLUDED":
            continue
        k = (r["legacy_category"], r["legacy_subcategory"], r["legacy_device"])
        a = agg[k]
        v = float(r["normalized_duration_min"])
        a["min"] += v
        a["n"] += 1
        a["months"].add(r["month"])
        a["energy"][f"{r['legacy_energy']} → {r['canonical_energy'] or '(unmapped)'} [{r['energy_status']}]"] += v
        a["goal"][f"{r['legacy_goal']} → {r['canonical_goal'] or '(unmapped)'} [{r['goal_status']}]"] += v
        rows_of[k] = r
    rules = {(x["legacy_category"], x["legacy_subcategory"], x["legacy_device"]): x for x in ctx["data"]["legacy_rules"]}
    tax = ctx["taxonomy"]
    out = []
    for k in sorted(agg):
        r, a = rows_of[k], agg[k]
        rule = next((rules[c] for c in ((k[0], k[1], k[2]), (k[0], k[1], "*"), (k[0], "*", k[2]), (k[0], "*", "*")) if c in rules), {})
        dest = r["canonical_category"] if r["category_status"] in (hc.CAT_HIGH, hc.CAT_REVIEW) else (
            unsplit_label(r["canonical_category_candidates"]) if r["canonical_category_candidates"] else "(unmapped)")
        goal_check = ""
        if r["canonical_category"] in tax.category_defaults and r["canonical_goal"]:
            dg = tax.category_defaults[r["canonical_category"]][2]
            goal_check = "AGREES" if dg == r["canonical_goal"] else f"DIFFERS (current category default {dg})"
        out.append({
            "legacy_category": k[0], "legacy_subcategory": k[1] or "(device row: no subcategory recorded)", "legacy_device": k[2],
            "legacy_subcategory_source": r["legacy_subcategory_source"],
            "current_category": dest, "current_subcategory": r["canonical_subcategory"],
            "category_confidence": r["category_status"], "candidate_categories": r["canonical_category_candidates"],
            "current_domain": r["canonical_domain"], "domain_confidence": r["domain_status"],
            "energy_mapping": "; ".join(f"{e} {v / 60:.0f} h" for e, v in a["energy"].most_common()),
            "goal_mapping": "; ".join(f"{g} {v / 60:.0f} h" for g, v in a["goal"].most_common()),
            "goal_vs_current_category_default": goal_check,
            "current_device_class": r["canonical_device"], "device_confidence": r["device_status"],
            "evidence_types": r["allocation_basis"], "records": a["n"], "hours": round(a["min"] / 60, 1),
            "months": f"{min(a['months'])}..{max(a['months'])} ({len(a['months'])})",
            "internal_bridge_group": r["bridge_group"], "rationale": rule.get("rationale", ""),
        })
    for lm in ctx["data"]["label_maps"]:
        out.append({"legacy_category": f"[label map: {lm['dimension']}]", "legacy_subcategory": lm["legacy_value"], "legacy_device": "",
                    "current_category": "", "category_confidence": "",
                    "energy_mapping": lm["canonical_value"] if lm["dimension"] == "energy" else "",
                    "goal_mapping": lm["canonical_value"] if lm["dimension"] == "goal" else "",
                    "current_device_class": lm["canonical_value"] if lm["dimension"] == "device" else "",
                    "device_confidence": lm["status"], "rationale": lm["rationale"]})
    seen = {k[0] for k in agg}
    for lr in ctx["data"]["legacy_rules"]:
        if lr["legacy_category"] not in seen:
            out.append({"legacy_category": lr["legacy_category"], "legacy_subcategory": lr["legacy_subcategory"], "legacy_device": lr["legacy_device"],
                        "current_category": "(no values recorded in the source)", "category_confidence": lr["category_status"],
                        "records": 0, "hours": 0.0, "rationale": lr["rationale"]})
    return out


DECISION_OF = (("Work / Study", "D-H02"), ("Entertainment", "D-H03"), ("Household / Chores", "D-H04"),
               ("Health & Wellness / Vacations", "D-H05"), ("Hobbies / Creative / Videos", "D-H06"),
               ("Relationships / Social / Friends / PC Desktop", "D-H07"))


def review_register(ctx: dict) -> list[dict]:
    """Every legacy minute whose category, domain, energy or goal is not a high-confidence current value."""
    agg = defaultdict(float)
    for r in ctx["recs"]:
        if r["regime"] != REGIME_LEGACY or r["category_status"] == hc.CAT_RESIDUAL:
            continue
        for dim, st in (("category", r["category_status"]), ("domain", r["domain_status"]), ("energy", r["energy_status"]), ("goal", r["goal_status"])):
            if st == hc.CAT_HIGH:
                continue
            value = (r["category"] or r["candidate_categories"].replace("|", " | ") or "(none)") if dim == "category" else (r[dim] or "(none)")
            agg[(dim, st, r["legacy_label"], value, r["bridge_group"])] += r["minutes"]
    treatment = {hc.CAT_MULTI: "Several current categories possible; legacy row has no subcategory. Kept outside every category total.",
                 hc.CAT_REVIEW: "Defensible but not exact equivalence; included in totals and flagged.",
                 hc.CAT_UNMAPPED: "No defensible current value; kept outside the totals of this dimension and shown as '(Unmapped legacy)' or as a mapping gap."}
    out = []
    for (dim, st, label, value, grp), v in sorted(agg.items(), key=lambda x: (x[0][0], -x[1], x[0])):
        did = next((d for p, d in DECISION_OF if label.startswith(p)), "")
        if dim == "energy" and not did and st != hc.CAT_REVIEW:
            did = "D-H08"
        out.append({"dimension": dim, "status": st, "legacy_source": label, "current_value_or_candidates": value, "hours": round(v / 60, 2),
                    "treatment": treatment.get(st, "Review."), "decision_id": did, "internal_bridge_group": grp})
    return out


def mapping_reconciliation(ctx: dict) -> list[dict]:
    out = []
    norm_total = defaultdict(float)
    for r in ctx["data"]["Historical_Normalized_Records.csv"]:
        if r["accounting_status"] == "INCLUDED":
            norm_total[r["month"]] += float(r["normalized_duration_min"])
    for m in ctx["months"]:
        w = ctx["W"][m]
        st = {k[1]: ctx["M"][k][m] for k in ctx["M"] if k[0] == "mapping_status"}
        legacy = ctx["regime"][m] == REGIME_LEGACY
        out.append({
            "month": m, "regime": ctx["regime"][m],
            "legacy_report_total_h": w.get("legacy_reported_total_h", ""),
            "source_normalized_total_h": r2(norm_total[m] / 60) if legacy else "",
            "recorded_h": r2(w["recorded_h"]), "overlap_h": r2(w["overlap_h"]), "off_device_life_residual_h": r2(w["unresolved_h"]),
            "analytical_activity_h": r2(ctx["M"][("kpi", "ANALYTICAL_TOTAL")][m] / 60),
            "current_native_h": r2(st.get(hc.CAT_CURRENT, 0) / 60), "mapped_high_confidence_h": r2(st.get(hc.CAT_HIGH, 0) / 60),
            "mapped_review_required_h": r2(st.get(hc.CAT_REVIEW, 0) / 60), "residual_rule_h": r2(st.get(hc.CAT_RESIDUAL, 0) / 60),
            "unsplit_multi_category_h": r2(st.get(hc.CAT_MULTI, 0) / 60), "unmapped_h": r2(st.get(hc.CAT_UNMAPPED, 0) / 60),
            "identity": "analytical = recorded + residual = current-native + high + review + residual-rule + unsplit + unmapped",
            "legacy_report_minus_detail_h": w.get("report_minus_detail_h", ""),
        })
    return out


def category_comparability(ctx: dict) -> list[dict]:
    out = []
    for c in ctx["taxonomy"].categories:
        ev = ctx["cat_evidence"][c]
        sources = Counter()
        for r in ctx["recs"]:
            if r["regime"] == REGIME_LEGACY and r["category"] == c:
                sources[r["legacy_label"]] += r["minutes"]
        cur_tot = sum(ctx["cat_status"][(REGIME_CURRENT, c)].values())
        out.append({"current_category": c, "legacy_evidence": ev, "note": EV_NOTE[ev],
                    "legacy_sources": "; ".join(f"{k} {v / 60:.0f} h" for k, v in sources.most_common(6)),
                    "candidate_in_unsplit_legacy_records": "Y" if any(c in k[1] for k in ctx["M"] if k[0] == "unsplit") else "N",
                    "current_share_from_current_only_capture_pct": r1(100 * ctx["cat_capture"][c] / cur_tot) if cur_tot else "",
                    "month_over_month_comparability": EV_TO_MOM[ev]})
    return out


def bridge_diagnostics(ctx: dict) -> list[dict]:
    """Technical diagnostic: internal bridge groups by segment (NOT a reporting taxonomy)."""
    out = []
    for g in hm.GROUP_ORDER:
        key = ("bridge_group", g)
        if key not in ctx["M"]:
            continue
        row = {"internal_bridge_group": g, "display_name": ctx["data"]["groups_meta"].get(g, {}).get("display_name", g),
               "cross_regime_comparability": hm.GROUP_COMPARABILITY[g][0], "note": hm.GROUP_COMPARABILITY[g][1]}
        for seg in SEGMENT_ORDER:
            tot, days, _ = segment_total(ctx, key, seg)
            row[f"{seg}_min_per_day"] = r1(tot / days) if tot is not None and days else ""
        out.append(row)
    return out


def pending_decisions(ctx: dict, baseline_rows: list[dict]) -> list[dict]:
    out = []

    def add(group, did, decision, why, options, default):
        out.append({"decision_group": group, "decision_id": did, "decision": decision, "why_it_matters": why,
                    "options": options, "current_default_in_analysis": default, "status": "PENDING_HUMAN_VALIDATION"})

    H, B, O = "1_INTERPRET_HISTORY", "2_FIRST_BUDGET", "3_OPTIONAL_MEASUREMENT"
    unsplit = {k[1]: (segment_total(ctx, k, SEG_LEGACY)[0] or 0) / 60 for k in ctx["M"] if k[0] == "unsplit"}
    add(H, "D-H01", "Confirm the legacy baseline window", "Budget references use these months only.",
        "Keep / include Sep 2025 and Jul 2026 / last 6 legacy months", f"{ANNUAL_BASELINE_START_MONTH}..{ANNUAL_BASELINE_END_MONTH}")
    add(H, "D-H02", "Legacy 'Work / Study' device rows: split between Productivity & Finance and Education?",
        f"{next((v for k, v in unsplit.items() if 'Education' in k), 0):.0f} h of legacy time cannot be assigned to one current category; both categories' legacy totals are understated.",
        "Keep unsplit (default) / approve a documented split rule / assign all to Productivity & Finance (REVIEW_REQUIRED)",
        "Unsplit; planned as 'Productivity & Finance + Education'")
    add(H, "D-H03", "Legacy 'Entertainment' device rows: split between Entertainment, Games and Social Networking?",
        f"{next((v for k, v in unsplit.items() if 'Games' in k), 0):.0f} h of legacy time; the monthly consumption proxy is a lower bound for legacy months.",
        "Keep unsplit (default) / split by device (desktop → Games, laptop → Entertainment, phone → Social Networking) with REVIEW_REQUIRED",
        "Unsplit; planned in the Games + Entertainment + Social Networking (consumption) line")
    add(H, "D-H04", "Legacy 'Household / Chores' estimate shown in Off-Device Life (REVIEW_REQUIRED)",
        "The current system keeps meals / hygiene / chores in the Off-Device Life residual; legacy entered them as a template (naps included from Nov 2025).",
        "Keep in Off-Device Life (default) / treat as unmapped legacy-only", "Off-Device Life › Untracked Maintenance, REVIEW_REQUIRED")
    add(H, "D-H05", "Legacy 'Vacations' balancing fill", "Filler used to reach 24 h on vacation days; activity unknown.",
        "Keep unmapped (default) / treat as Off-Device Life", "Unmapped legacy")
    add(H, "D-H06", "Legacy Hobbies 'Videos' row", "Undocumented: watching (Entertainment) or producing (Creativity) videos.",
        "Keep unmapped (default) / Entertainment / Creativity", "Unmapped legacy")
    add(H, "D-H07", "Legacy PC 'Friends' row mapped to Games (REVIEW_REQUIRED)", "Legacy report comment 'Friends & Social gaming'.",
        "Keep Games / Social Networking › Social Time Tracking / unmapped", "Games (REVIEW_REQUIRED)")
    add(H, "D-H08", "Legacy energy labels without a defensible current value ('Recovery / Rest' without an exact activity)",
        "Kept as '(Unmapped legacy)' energy.", "Keep unmapped / use the current category default energy", "Unmapped")
    add(H, "D-H09", "August 2026 double-applied Habit adjustments (~1,230 min)", "August overstates recorded time; read as published.",
        "Leave as published / re-run the August close (separate change)", "As published, flagged")
    add(B, "D-B01", "Define fixed commitments (work / study hours, classes, obligations)",
        "Productivity & Finance + Education cannot be planned from history alone.", "User input", "Not defined — TO_DEFINE")
    add(B, "D-B02", "Define goals / projects and their time needs", "The goal pool of Scenario B is unassigned.", "User input", "Not defined")
    add(B, "D-B03", "Budget period convention", "Year / quarter / month boundaries.", "Calendar 2027 / Oct 2026–Sep 2027 / rolling",
        "Per-day values; conversions use 7/30/91/365 days")
    add(B, "D-B04", "Confirm protection classes per line", "Floors vs ceilings.", "Accept / adjust table J", "Analytical proposal")
    add(B, "D-B05", "Set the unallocated reserve", "Margin for interruptions and noise.", "Template 60 / 60 / 120 min/day", "Template defaults")
    no_range = [b["budget_line"] for b in baseline_rows if b["suggested_low_min_per_day"] == "" and b["is_sub_line"] == "N" and b["range_status"] != "NOT_BUDGETABLE"]
    add(B, "D-B06", "Lines without a data-supported range", "History cannot set these ranges yet.",
        "Wait for 3 closed current months / set a provisional ceiling", "TO_DEFINE: " + ", ".join(no_range))
    add(B, "D-B07", "Deviation reasons at budget review", "Deviations from illness, events or method changes must not be penalised.",
        "Reason vocabulary recorded at each review", "Not implemented")
    add(B, "D-B08", "Legacy monthly targets", "Legacy Report sheets hold targets for the old categories.",
        "Ignore (default) / review as inspiration only", "Not used; no current target or budget is configured")
    add(O, "D-O01", "Split the Apple Screen Time 'Social Networking' bucket", "Messaging vs feeds cannot be separated.",
        "App-level ingestion / keep", "Unsplit")
    add(O, "D-O02", "Record daily living explicitly (meals, chores, transport)", "Off-Device Life mixes daily living and untracked time.",
        "Habit rows / keep residual", "Residual")
    add(O, "D-O03", "Windows console encoding in historical_pipeline.py", "UnicodeEncodeError on cp1252 consoles after outputs are written.",
        "Set PYTHONIOENCODING=utf-8 / reconfigure stdout", "Workaround documented")
    return out


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

# Internal bridge identifiers that must not appear in primary reports / tables.
BRIDGE_ONLY_NAMES = tuple(sorted((set(hm.GROUP_ORDER) | set(hm.BLOCK_ORDER) | {"DAILY_LIVING_AND_RESIDUAL"}) - {"UNMAPPED"}))
PRIMARY_REPORTS = ("standard", "eda", "detail", "analysis")
PRIMARY_TABLES = ("B", "B_sub", "C", "D", "F_dims", "kpi", "G_trends", "detail", "top", "J", "K", "P")
_TOKEN = {n: re.compile(rf"(?<![A-Za-z_]){n}(?![A-Za-z_])") for n in BRIDGE_ONLY_NAMES}


def bridge_leaks(text: str) -> list[str]:
    return [n for n, pat in _TOKEN.items() if pat.search(text)]


def validate(res: dict, project_root: Path) -> list[dict]:
    ctx = res["ctx"]
    tax = ctx["taxonomy"]
    checks = []

    def check(name, ok, detail):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    bad = [i["file"] for i in res["inputs"] if i["match"] == "N"]
    check("Inputs match the historical pipeline manifest", not bad, ", ".join(bad) or f"{len(res['inputs'])} files")
    pv = ctx["data"]["Historical_Validation.csv"]
    fails = [r["check"] for r in pv if r["result"] == "FAIL"]
    check("Historical pipeline validation has no FAIL", not fails, "; ".join(fails) or f"{len(pv)} checks, 0 FAIL")
    errs = hc.validate_bridge_against_taxonomy(ctx["data"]["legacy_rules"], ctx["data"]["label_maps"], tax)
    check("Bridge canonical values exist in the current taxonomy", not errs, "; ".join(errs) or tax.source)

    cats = [r["category"] for r in res["B"] if r["row_type"] == "CURRENT_CATEGORY"]
    outside = sorted({r["category"] for r in ctx["recs"] if r["category"]} - set(tax.categories))
    check("Primary annual categories = current taxonomy categories (no invented category)",
          cats == tax.categories and not outside, f"{len(cats)} categories from {tax.source}; outside taxonomy: {outside or 'none'}")
    bad_sub = sorted({(r["category"], r["subcategory"]) for r in ctx["recs"] if r["regime"] == REGIME_LEGACY and r["subcategory"]
                      and not tax.has_pair(r["category"], r["subcategory"])})
    check("Legacy records use only current subcategories (no invented subcategory)", not bad_sub, str(bad_sub[:5]) if bad_sub else "ok")
    dims_bad = sorted({(d, r[d]) for r in ctx["recs"] for d, allowed in (("domain", tax.domains), ("energy", tax.energies), ("goal", tax.goals))
                       if r[d] and r[d] not in allowed})
    check("Domains / energies / goals come from the current taxonomy", not dims_bad, str(dims_bad[:5]) if dims_bad else "ok")
    leaks = []
    for t in PRIMARY_TABLES:
        for row in res[t]:
            for k, v in row.items():
                if isinstance(v, str) and bridge_leaks(v):
                    leaks.append(f"{t}.{k}:{','.join(bridge_leaks(v))}")
    for name in PRIMARY_REPORTS:
        main = res["reports"][name].split("<!-- technical-appendix -->")[0]
        leaks += [f"{name}:{n}" for n in bridge_leaks(main)]
    leaks += [f"chart {n}" for n, svg in res["charts"].items() if bridge_leaks(svg)]
    check("Bridge-only groups do not appear in primary tables, reports or charts", not leaks, ", ".join(sorted(set(leaks))[:8]) or "none")

    final_dir = FINAL_ANALYSIS_ROOT if project_root == PROJECT_ROOT else project_root / "output" / "Integrated" / "Analysis" / "Final"
    sep_dir = final_dir / CURRENT_REFERENCE_PERIOD
    summary = sep_dir / f"Final_Detail_Analysis_{CURRENT_REFERENCE_PERIOD}_Summary.csv"
    if summary.exists():
        diffs, n = [], 0
        for r in _read_csv(summary):
            dim = {"Category": "category", "Domain": "domain", "Energy": "energy", "Goal": "goal"}.get(r["Analysis_Level"])
            if dim:
                n += 1
                if abs(float(r["Duration_min"]) - (ctx["M"].get((dim, r["Value"]), {}).get("2026-09") or 0.0)) > 0.01:
                    diffs.append(f"{dim}:{r['Value']}")
        check("September 2026 categories / domains / energies / goals equal the closed Final Detail Analysis", not diffs and n == 28,
              ", ".join(diffs) or f"{n} values match (±0.01 min)")
        _, universe, _ = read_current_period(sep_dir, CURRENT_REFERENCE_PERIOD)
        w = ctx["W"]["2026-09"]
        uniq = sum(float(r["Unique_Tracked_min"]) for r in universe)
        resid = sum(float(r["Analytical_Off_Device_min"]) for r in universe)
        check("September 2026 time universe equals the closed Final",
              abs(uniq - w["accounted_h"] * 60) < 0.05 and abs(resid - w["unresolved_h"] * 60) < 0.05 and abs(ctx["M"][("kpi", "ANALYTICAL_TOTAL")]["2026-09"] - 43200) < 0.05,
              f"unique {uniq / 60:.2f} h, Off-Device Life {resid / 60:.2f} h, analytical {ctx['M'][('kpi', 'ANALYTICAL_TOTAL')]['2026-09'] / 60:.2f} h")
    else:
        check("September 2026 equals the closed Final", False, f"{summary} not found")
    check("August 2026 labelled TRANSITION_REVIEW (not closed)", ctx["regime"].get("2026-08") == REGIME_TRANSITION, ctx["regime"].get("2026-08", ""))
    check("All 13 months present", ctx["months"] == hm.month_range() and REGIME_NO_DATA not in ctx["regime"].values(), f"{len(ctx['months'])} months")
    nodays = [m for m in ctx["months"] if int(ctx["W"][m]["days_with_data"]) != ctx["days"][m]]
    check("Calendar days = days with evidence = days analysed", not nodays, f"missing: {nodays or 'none'}")

    errs = []
    for m in ctx["months"]:
        an = ctx["M"][("kpi", "ANALYTICAL_TOTAL")][m]
        rec, resid_m = ctx["W"][m]["recorded_h"] * 60, ctx["W"][m]["unresolved_h"] * 60
        if abs(an - rec - resid_m) > 0.5:
            errs.append(f"{m} analytical ≠ recorded + residual")
        cat = sum(ctx["M"][("category", c)][m] or 0.0 for c in tax.categories)
        gaps = sum(ctx["M"][k][m] for k in ctx["M"] if k[0] in ("unsplit", "unmapped"))
        if abs(cat + gaps - an) > 0.5:
            errs.append(f"{m} categories + gaps ≠ analytical")
        for dim, vals in (("domain", tax.domains), ("energy", tax.energies), ("goal", tax.goals)):
            if abs(sum(ctx["M"][(dim, v)][m] for v in list(vals) + [hc.UNMAPPED_VALUE]) - an) > 0.5:
                errs.append(f"{m} {dim} ≠ analytical")
        if abs(sum(ctx["M"][("line", l)][m] or 0.0 for l in hc.BUDGET_LINE_ORDER) - an) > 0.5:
            errs.append(f"{m} budget lines ≠ analytical")
        for c in tax.categories:
            cval = ctx["M"][("category", c)][m]
            subs = sum(ctx["M"][k][m] for k in ctx["M"] if k[0] == "subcategory" and k[1].startswith(c + " › "))
            if cval is not None and abs(cval - subs) > 0.5:
                errs.append(f"{m} {c} subcategories ≠ category")
        if abs(sum((ctx["M"][("device", x)][m] or 0.0) for x in ("PHONE", "COMPUTERS_TOTAL", "TV", "OFF_DEVICE_RECORDED")) - rec) > 0.5:
            errs.append(f"{m} devices ≠ recorded")
        if ctx["regime"][m] != REGIME_TRANSITION and abs((ctx["M"][("device", "LAPTOP")][m] or 0) + (ctx["M"][("device", "DESKTOP")][m] or 0)
                                                          - (ctx["M"][("device", "COMPUTERS_TOTAL")][m] or 0)) > 0.5:
            errs.append(f"{m} laptop + desktop ≠ computers")
        acc = ctx["W"][m]["accounted_h"] * 60
        if abs(rec - ctx["W"][m]["overlap_h"] * 60 - acc) > 0.5 or abs(ctx["days"][m] * DAY_CAPACITY_MIN - acc - resid_m) > 0.5:
            errs.append(f"{m} accounting identity")
    check("Universe identities: analytical = recorded + residual = Σ categories + unsplit + unmapped = Σ domain = Σ energy = Σ goal = Σ budget lines; "
          "subcategories roll up; devices sum to recorded; laptop + desktop = computers; accounted = recorded − overlap", not errs,
          "; ".join(errs[:6]) or "13 months reconcile (±0.5 min)")
    keys = Counter((r["provenance"], r["category"], r["candidate_categories"]) for r in ctx["recs"])
    norm = sum(float(r["normalized_duration_min"]) for r in ctx["data"]["Historical_Normalized_Records.csv"] if r["accounting_status"] == "INCLUDED")
    leg = sum(r["minutes"] for r in ctx["recs"] if r["regime"] == REGIME_LEGACY and r["category_status"] != hc.CAT_RESIDUAL)
    dup = [k for k, n in keys.items() if n > 1 and not k[0].startswith("Final/")]
    check("Each legacy source cell appears once (no duplicated aggregation); legacy canonical total = normalized total",
          not dup and abs(norm - leg) < 0.01, f"normalized {norm:.1f} min vs canonical {leg:.1f} min; duplicates {len(dup)}")
    long = {(r["month"], r["value"]): float(r["minutes"]) for r in ctx["data"]["Historical_Monthly_Long.csv"] if r["dimension"] == "activity_anchor"}
    anchor_err = [f"{m}:{a}" for m in ctx["months"] for a in hm.ANCHOR_ORDER if abs(ctx["M"][("anchor", a)][m] - long.get((m, a), 0.0)) > 0.05]
    check("Anchor activities from current subcategories equal the pipeline's anchor totals", not anchor_err, ", ".join(anchor_err[:6]) or "7 anchors x 13 months")
    inv = sum(float(r.get("hours") or 0) for r in res["H_inventory"] if not str(r["legacy_category"]).startswith("[label"))
    check("Mapping inventory covers every legacy minute", abs(inv - norm / 60) < 0.5, f"inventory {inv:.1f} h vs normalized {norm / 60:.1f} h")
    gap = sum(r["minutes"] for r in ctx["recs"] if not r["category"]) / 60
    reg = sum(r["hours"] for r in res["I_register"] if r["dimension"] == "category" and r["status"] in (hc.CAT_MULTI, hc.CAT_UNMAPPED))
    check("Unsplit and unmapped legacy time is fully listed in the review register", abs(gap - reg) < 0.1, f"{gap:.2f} h in gaps vs {reg:.2f} h registered")
    mixed = next((r for r in res["A"] if r["segment"] == SEG_MIXED), None)
    check("13-month total labelled as a mixed-method descriptive reference", bool(mixed) and mixed["homogeneous_measurement"].startswith("N — MIXED"), "")
    seg_days = {s: sum(ctx["days"][m] for m in ctx["segments"][s]) for s in SEGMENT_ORDER}
    den = [r["segment"] for r in res["A"] if r["calendar_days"] != seg_days[r["segment"]]
           or (r["metric"] == "calendar_capacity" and abs(float(r["min_per_calendar_day"]) - 1440) > 1e-6)]
    check("Per-day averages use segment calendar days; capacity = 1440 min/day", not den, str(seg_days))
    pct_err = []
    for seg in SEGMENT_ORDER:
        s = sum(float(r[f"{seg}_pct_of_analytical"]) for r in res["B"] if r[f"{seg}_pct_of_analytical"] not in ("n/a", ""))
        if abs(s - 100) > 0.1:
            pct_err.append(f"{seg} {s:.2f}%")
    check("Category + mapping-gap shares sum to 100% of analytical activity in every segment", not pct_err, ", ".join(pct_err) or "ok")
    infeas = [r["scenario"] for r in res["K"] if r["budget_line"] == "CAPACITY_CHECK" and r["status"] != "FEASIBLE"]
    check("Scenario lower bounds + reserve ≤ 1440 min/day", not infeas,
          "; ".join(f"{r['scenario']}: {r['low_min_per_day']}" for r in res["K"] if r["budget_line"] == "CAPACITY_CHECK"))
    check("Budget lines are defined by current-taxonomy values only",
          all(b["budget_line"] in hc.BUDGET_LINE_ORDER or b["budget_line"] in tax.categories for b in res["J"]), f"{len(res['J'])} lines")
    check("No target carried forward (user_approved_target = NOT SET)", all(b["user_approved_target"] == "NOT SET" for b in res["J"]), "")
    return checks


# --------------------------------------------------------------------------
# Build / run
# --------------------------------------------------------------------------

def build(hist_dir: Path = HISTORICAL_OUTPUT_ROOT, taxonomy_root: Path | None = None) -> dict:
    import historical_annual_reports as rep

    data = load_inputs(hist_dir)
    taxonomy = hc.load_current_taxonomy(taxonomy_root)
    ctx = build_context(data, taxonomy)
    res = {"ctx": ctx, "inputs": data["integrity"]}
    res["A"] = accounting_summary(ctx)
    res["A_monthly"] = monthly_accounting(ctx)
    res["B"] = category_table(ctx)
    res["B_sub"] = subcategory_table(ctx)
    res["C"] = device_table(ctx)
    res["D"] = anchor_table(ctx)
    res["F_dims"] = dimension_table(ctx)
    res["kpi"] = kpi_table(ctx)
    res["daytype"] = daytype_table(ctx)
    res["assoc"] = association_table(ctx)
    res["G_trends"] = monthly_trends(ctx)
    res["detail"] = detail_hierarchy(ctx)
    res["top"] = top_activities(ctx)
    res["concentration"] = concentration_table(ctx)
    res["review_candidates"] = taxonomy_review_candidates(ctx)
    res["P"] = findings(ctx, res["daytype"], res["assoc"])
    res["J"] = budget_baseline(ctx)
    res["K"] = budget_scenarios(res["J"])
    res["L"] = pending_decisions(ctx, res["J"])
    res["H_inventory"] = mapping_inventory(ctx)
    res["H_comparability"] = category_comparability(ctx)
    res["H_reconciliation"] = mapping_reconciliation(ctx)
    res["H_bridge_diag"] = bridge_diagnostics(ctx)
    res["I_register"] = review_register(ctx)
    res["validation"] = []
    res["reports"] = rep.render_all(res)
    res["charts"] = rep.build_charts(res)
    return res


TABLES = (
    ("Annual_A_Accounting_Summary.csv", "A"),
    ("Annual_A_Monthly_Accounting.csv", "A_monthly"),
    ("Annual_B_Current_Category.csv", "B"),
    ("Annual_B_Current_Subcategory.csv", "B_sub"),
    ("Annual_C_Device.csv", "C"),
    ("Annual_D_Anchor_Activities.csv", "D"),
    ("Annual_F_Domain_Energy_Goal.csv", "F_dims"),
    ("Annual_F_KPI.csv", "kpi"),
    ("Annual_G_Monthly_Trends.csv", "G_trends"),
    ("Annual_Final_Detail_Hierarchy.csv", "detail"),
    ("Annual_EDA_Top_Activities.csv", "top"),
    ("Annual_EDA_Concentration.csv", "concentration"),
    ("Annual_EDA_Taxonomy_Review_Candidates.csv", "review_candidates"),
    ("Annual_Daytype_Profile.csv", "daytype"),
    ("Annual_Daily_Associations.csv", "assoc"),
    ("Annual_Patterns_Findings.csv", "P"),
    ("Annual_J_Budget_Baseline.csv", "J"),
    ("Annual_J_Budget_Scenarios.csv", "K"),
    ("Annual_Pending_Decisions.csv", "L"),
    ("Historical_Mapping_Inventory.csv", "H_inventory"),
    ("Historical_Category_Comparability.csv", "H_comparability"),
    ("Historical_Mapping_Reconciliation.csv", "H_reconciliation"),
    ("Historical_Bridge_Group_Diagnostics.csv", "H_bridge_diag"),
    ("Historical_Mapping_Review_Register.csv", "I_register"),
)


def _fingerprint(res: dict) -> str:
    payload = {k: res[k] for _, k in TABLES}
    payload["charts"] = res["charts"]
    payload["reports"] = res["reports"]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def run(hist_dir: Path = HISTORICAL_OUTPUT_ROOT, out_dir: Path = ANNUAL_OUTPUT_ROOT, project_root: Path = PROJECT_ROOT,
        write: bool = True) -> dict:
    import historical_annual_reports as rep

    final_root = project_root / "output" / "Integrated" / "Analysis" / "Final"
    analysis_root = project_root / "output" / "Integrated" / "Analysis"
    legacy_dir = LEGACY_INPUT_ROOT if project_root == PROJECT_ROOT else project_root / "input" / "Historical" / "Legacy"
    protected = {
        "legacy_workbooks": lambda: {w: sha256_file(legacy_dir / w) for w in LEGACY_WORKBOOKS if (legacy_dir / w).exists()},
        f"current_final_{CURRENT_REFERENCE_PERIOD}": lambda: hash_tree(final_root / CURRENT_REFERENCE_PERIOD),
        f"transition_final_{TRANSITION_PERIOD}": lambda: hash_tree(final_root / TRANSITION_PERIOD),
        "september_standard_report_and_eda": lambda: {**hash_tree(analysis_root / "Report" / CURRENT_REFERENCE_PERIOD),
                                                      **{f"EDA/{k}": v for k, v in hash_tree(analysis_root / "EDA" / CURRENT_REFERENCE_PERIOD).items()}},
        "historical_pipeline_outputs": lambda: hash_tree(hist_dir, recursive=False),
        "current_taxonomy": lambda: hash_tree(project_root / "output" / "Reference" / "Taxonomy"),
    }
    before = {k: f() for k, f in protected.items()}
    res = build(hist_dir)
    second = build(hist_dir)
    fp1, fp2 = _fingerprint(res), _fingerprint(second)
    tax = res["ctx"]["taxonomy"]
    res["manifest"] = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scripts": ["historical_annual.py", "historical_annual_reports.py", "historical_canonical.py"],
        "period": f"{ANALYSIS_START_MONTH} .. {ANALYSIS_END_MONTH}",
        "baseline_window": f"{ANNUAL_BASELINE_START_MONTH} .. {ANNUAL_BASELINE_END_MONTH}",
        "reporting_taxonomy": {"source": tax.source, "categories": tax.categories, "domains": tax.domains,
                               "energies": tax.energies, "goals": tax.goals},
        "python": sys.version.split()[0],
        "inputs": res["inputs"],
        "historical_pipeline_manifest_generated_utc": res["ctx"]["data"]["manifest"].get("generated_utc"),
        "mapping_tables": res["ctx"]["data"]["manifest"].get("mapping_tables", {}),
        "legacy_workbooks": res["ctx"]["data"]["manifest"].get("workbooks", []),
        "parameters": {"MIN_MATERIAL_MIN_PER_DAY": MIN_MATERIAL_MIN_PER_DAY, "STABLE_CV": STABLE_CV, "VOLATILE_CV": VOLATILE_CV,
                       "TREND_MIN_PCT": TREND_MIN_PCT, "ENVELOPE_TOLERANCE": ENVELOPE_TOLERANCE, "ASSOCIATION_RHO": ASSOCIATION_RHO,
                       "DAYTYPE_RATIO": DAYTYPE_RATIO, "ROUND_TO_MIN": ROUND_TO_MIN, "MIN_BASELINE_MONTHS": MIN_BASELINE_MONTHS,
                       "RESERVE_MIN_PER_DAY": RESERVE_MIN_PER_DAY},
        "content_fingerprint": fp1,
        "deterministic_note": "Tables, charts and reports are built twice and compared; generated_utc here and in report headers is runtime metadata.",
        "outputs": {},
    }
    checks = validate(res, project_root)
    checks.append({"check": "Two consecutive builds are identical (tables, charts, reports)", "result": "PASS" if fp1 == fp2 else "FAIL", "detail": fp1[:16]})
    bad_svg = []
    for name, svg in res["charts"].items():
        try:
            ElementTree.fromstring(svg)
        except ElementTree.ParseError as exc:
            bad_svg.append(f"{name}: {exc}")
    checks.append({"check": "Charts are well-formed SVG", "result": "PASS" if not bad_svg else "FAIL", "detail": "; ".join(bad_svg) or f"{len(res['charts'])} charts"})
    res["validation"] = checks

    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        tables_dir = out_dir / "tables"
        for name, key in TABLES:
            res["manifest"]["outputs"][f"tables/{name}"] = {"rows": len(res[key]), "sha256": write_csv(tables_dir / name, res[key])}
        (out_dir / "charts").mkdir(exist_ok=True)
        for name, svg in res["charts"].items():
            (out_dir / "charts" / name).write_text(svg, encoding="utf-8")
            res["manifest"]["outputs"][f"charts/{name}"] = {"sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest()}
        rt = []
        for name, key in TABLES:
            back = _read_csv(tables_dir / name)
            if len(back) != len(res[key]) or any(any(str(a.get(k, "")) != b.get(k, "") for k in b) for a, b in zip(res[key], back)):
                rt.append(name)
        checks.append({"check": "Reports and tables consistent (reports rendered from the same rows; CSV round-trip identical)",
                       "result": "PASS" if not rt else "FAIL", "detail": ", ".join(rt) or f"{len(TABLES)} tables"})
    after = {k: f() for k, f in protected.items()}
    for k in protected:
        checks.append({"check": f"Protected files unchanged: {k}", "result": "PASS" if before[k] == after[k] else "FAIL",
                       "detail": f"{len(before[k])} files hashed before/after"})
    res["generated_utc"] = res["manifest"]["generated_utc"]
    res["reports"] = rep.render_all(res)   # final render includes the validation results
    if write:
        for fname, text in rep.report_files(res).items():
            (out_dir / fname).write_text(text, encoding="utf-8")
            res["manifest"]["outputs"][fname] = {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        res["manifest"]["validation"] = checks
        write_csv(out_dir / "tables" / "Annual_Validation.csv", checks)
        (out_dir / "Annual_Run_Manifest.json").write_text(json.dumps(res["manifest"], indent=2, ensure_ascii=False), encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--historical-dir", type=Path, default=HISTORICAL_OUTPUT_ROOT)
    parser.add_argument("--out-dir", type=Path, default=ANNUAL_OUTPUT_ROOT)
    parser.add_argument("--check-only", action="store_true", help="Build and validate in memory; write nothing.")
    args = parser.parse_args(argv)
    try:
        res = run(args.historical_dir, args.out_dir, write=not args.check_only)
    except InputError as exc:
        print(f"[FAIL] {exc}")
        return 1
    for c in res["validation"]:
        print(f"[{c['result']}] {c['check']} — {c['detail']}")
    if not args.check_only:
        print(f"\nOutputs written to {args.out_dir}")
    return 1 if any(c["result"] == "FAIL" for c in res["validation"]) else 0


if __name__ == "__main__":
    sys.exit(main())
