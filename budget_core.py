"""Time Budget core: snapshot loading, period budget, Actual vs Budget.

Pure functions over CSV files. No Excel access: the budget is read only from
the versioned snapshot in ``output/Reference/Budget/`` (``budget_import.py``).

Method (``docs/BUDGET_DESIGN.md``):

* Period budget of a leaf (Device × Category × Subcategory) =
  Σ over phases of Hours_per_week × (days of the phase inside the period) / 7.
* Only days covered by a budget phase are compared. The actual for those days
  comes from the daily Final file (``Final_Analysis_<period>.csv``) filtered to
  the covered dates; when the whole period is covered the Final hierarchy is
  used, so totals match the Standard Report exactly. No proration is needed.
* Variance = Actual − Budget. Investment: more is favorable. Consumption: less
  is favorable. Maintenance: no judgment, except Sleep, which is evaluated
  against the sleep floor (h/night).
* A leaf with budget but no actual is "Not recorded" (a capture alert, never a
  saving). A leaf with actual but no budget is "Unbudgeted".
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from config import budget as cfg


DIMENSIONS = ("Device", "Category", "Subcategory", "Domain", "Energy", "Goal")
ROLLUPS = {
    "Device": ("Device",),
    "Category": ("Category",),
    "Goal": ("Goal",),
    "Energy": ("Energy",),
    "Domain": ("Domain",),
}


class BudgetNotAvailable(Exception):
    """No budget snapshot exists (run budget_import.py)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()


def period_days(start: date, end: date) -> int:
    return (end - start).days + 1


def period_label(start: date, end: date) -> str:
    return f"{start.isoformat()}_{end.isoformat()}"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding=cfg.CSV_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(
    path: Path,
    rows: Sequence[dict],
    fieldnames: Sequence[str],
    decimals: int | None = 4,
) -> None:
    """Write rows; ``decimals=None`` keeps full float precision (snapshots)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=cfg.CSV_ENCODING, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _fmt(row.get(k, ""), decimals) for k in fieldnames})


def _fmt(value: object, decimals: int | None = 4) -> object:
    if isinstance(value, float):
        if value != value:
            return ""
        if decimals is None:
            return repr(value)
        text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
        return "0" if text in ("-0", "") else text
    return value


def number(value: object) -> float:
    try:
        return float(str(value).strip() or 0)
    except ValueError:
        return 0.0


def pct(part: float, total: float) -> float | None:
    return part / total * 100.0 if total else None


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Phase:
    name: str
    start: date
    end: date

    def days_in(self, start: date, end: date) -> int:
        lo = max(self.start, start)
        hi = min(self.end, end)
        return max(0, (hi - lo).days + 1)


@dataclass
class BudgetSnapshot:
    version: str
    phases: list[Phase]
    leaves: list[dict]   # one row per leaf × phase (Hours_per_week float)
    lines: list[dict]    # one row per budget line × leaf × phase
    manifest: dict

    @property
    def horizon_start(self) -> date:
        return min(p.start for p in self.phases)

    @property
    def horizon_end(self) -> date:
        return max(p.end for p in self.phases)

    def parameter(self, name: str, phase: str, default: float) -> float:
        values = self.manifest.get("parameters", {}).get(name, {})
        if isinstance(values, dict):
            return float(values.get(phase, default))
        return float(values if values is not None else default)

    def setting(self, name: str, default):
        return self.manifest.get("parameters", {}).get(name, default)


def snapshot_paths(root: Path, version: str) -> dict[str, Path]:
    return {
        "leaf": root / f"Budget_Leaf_{version}.csv",
        "phases": root / f"Budget_Phases_{version}.csv",
        "lines": root / f"Budget_Lines_{version}.csv",
        "manifest": root / f"Budget_Manifest_{version}.json",
    }


def list_versions(root: Path | None = None) -> list[dict]:
    """Return manifests of all snapshots, oldest import first."""
    root = root or cfg.BUDGET_REFERENCE_ROOT
    manifests = []
    for path in sorted(root.glob("Budget_Manifest_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        manifests.append(data)
    return sorted(manifests, key=lambda m: (m.get("imported_at", ""), m["version"]))


def verify_snapshot(paths: dict[str, Path], manifest: dict) -> None:
    """Fail if a snapshot file was edited after import (immutability)."""
    import hashlib

    for name, info in manifest.get("files", {}).items():
        path = paths.get(name)
        if path is None or not path.exists():
            raise BudgetNotAvailable(f"Snapshot file missing: {info.get('path')}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != info.get("sha256"):
            raise BudgetNotAvailable(
                f"Snapshot file {path.name} does not match its manifest hash. "
                "Snapshots are immutable: re-import the workbook as a new version."
            )


def load_snapshot(
    root: Path | None = None,
    version: str | None = None,
) -> BudgetSnapshot:
    """Load a snapshot (the most recently imported one by default)."""
    root = root or cfg.BUDGET_REFERENCE_ROOT
    versions = list_versions(root) if root.exists() else []
    if not versions:
        raise BudgetNotAvailable(
            f"No budget snapshot in {root}. Run budget_import.py first."
        )
    if version is None:
        version = versions[-1]["version"]
    elif version not in {m["version"] for m in versions}:
        raise BudgetNotAvailable(
            f"Budget version {version!r} not found in {root}. "
            f"Available: {', '.join(m['version'] for m in versions)}"
        )

    paths = snapshot_paths(root, version)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    verify_snapshot(paths, manifest)
    phases = [
        Phase(r["Phase"], parse_date(r["Start"]), parse_date(r["End"]))
        for r in read_csv(paths["phases"])
    ]
    leaves = read_csv(paths["leaf"])
    for row in leaves:
        row["Hours_per_week"] = number(row["Hours_per_week"])
    lines = read_csv(paths["lines"]) if paths["lines"].exists() else []
    for row in lines:
        row["Hours_per_week"] = number(row["Hours_per_week"])
    return BudgetSnapshot(version, phases, leaves, lines, manifest)


# ---------------------------------------------------------------------------
# Period budget and coverage
# ---------------------------------------------------------------------------


@dataclass
class Coverage:
    start: date
    end: date
    period_days: int
    covered_days: int
    phase_days: dict[str, int]
    covered_dates: list[date]

    @property
    def ratio(self) -> float:
        return self.covered_days / self.period_days if self.period_days else 0.0

    @property
    def first_covered(self) -> date | None:
        return self.covered_dates[0] if self.covered_dates else None

    @property
    def last_covered(self) -> date | None:
        return self.covered_dates[-1] if self.covered_dates else None

    @property
    def is_full(self) -> bool:
        return self.covered_days == self.period_days

    def describe(self) -> str:
        if not self.covered_days:
            return (
                f"No budget coverage: 0 of {self.period_days} days "
                f"({self.start} .. {self.end})."
            )
        phases = ", ".join(f"{k} {v} d" for k, v in self.phase_days.items() if v)
        return (
            f"Budget coverage: {self.covered_days} of {self.period_days} days "
            f"({self.ratio:.1%}); covered {self.first_covered} .. "
            f"{self.last_covered}; phases: {phases}."
        )


def coverage(snapshot: BudgetSnapshot, start: date, end: date) -> Coverage:
    phase_days = {p.name: p.days_in(start, end) for p in snapshot.phases}
    dates: list[date] = []
    day = start
    while day <= end:
        if any(p.start <= day <= p.end for p in snapshot.phases):
            dates.append(day)
        day += timedelta(days=1)
    return Coverage(
        start, end, period_days(start, end), len(dates), phase_days, dates
    )


def leaf_key(row: dict) -> tuple[str, str, str]:
    return tuple(str(row.get(k, "")).strip() for k in cfg.LEAF_KEY)  # type: ignore[return-value]


def period_leaf_budget(
    snapshot: BudgetSnapshot,
    start: date,
    end: date,
) -> dict[tuple[str, str, str], dict]:
    """Budget minutes per leaf for the period (covered days only)."""
    phase_days = {p.name: p.days_in(start, end) for p in snapshot.phases}
    result: dict[tuple[str, str, str], dict] = {}
    for row in snapshot.leaves:
        key = leaf_key(row)
        entry = result.setdefault(
            key,
            {
                "Domain": row["Domain"],
                "Energy": row["Energy"],
                "Goal": row["Goal"],
                "Budget_min": 0.0,
            },
        )
        days = phase_days.get(row["Phase"], 0)
        entry["Budget_min"] += row["Hours_per_week"] * 60.0 * days / 7.0
    return result


def period_line_budget(
    snapshot: BudgetSnapshot,
    start: date,
    end: date,
) -> dict[tuple[str, tuple[str, str, str]], float]:
    """Budget minutes per (line, leaf) for the period."""
    phase_days = {p.name: p.days_in(start, end) for p in snapshot.phases}
    result: dict[tuple[str, tuple[str, str, str]], float] = defaultdict(float)
    for row in snapshot.lines:
        days = phase_days.get(row["Phase"], 0)
        result[(row["Line"], leaf_key(row))] += (
            row["Hours_per_week"] * 60.0 * days / 7.0
        )
    return dict(result)


def period_parameter(
    snapshot: BudgetSnapshot,
    name: str,
    start: date,
    end: date,
    default: float,
) -> float:
    """Day-weighted average of a per-phase parameter over covered days."""
    weighted = 0.0
    days_total = 0
    for phase in snapshot.phases:
        days = phase.days_in(start, end)
        if days:
            weighted += snapshot.parameter(name, phase.name, default) * days
            days_total += days
    return weighted / days_total if days_total else default


# ---------------------------------------------------------------------------
# Actual (Final, post-adjustment)
# ---------------------------------------------------------------------------


def final_dir(start: date, end: date, root: Path | None = None) -> Path:
    return (root or cfg.FINAL_ROOT) / period_label(start, end)


def final_hierarchy_path(directory: Path, start: date, end: date) -> Path:
    return directory / (
        "Final_Analysis_By_Device_Category_Subcategory_Domain_Energy_Goal_"
        f"{period_label(start, end)}.csv"
    )


def final_daily_path(directory: Path, start: date, end: date) -> Path:
    return directory / f"Final_Analysis_{period_label(start, end)}.csv"


def load_actual_records(
    directory: Path,
    start: date,
    end: date,
    dates: Iterable[date] | None = None,
) -> tuple[list[dict], str]:
    """Actual minutes per Device × Category × Subcategory × Domain × Energy × Goal.

    Full period → Final hierarchy (deepest level). Subset of dates → daily
    Final rows filtered to those dates (exact, no proration).
    """
    wanted = None if dates is None else {d.isoformat() for d in dates}
    full = wanted is None or len(wanted) == period_days(start, end)
    totals: dict[tuple, float] = defaultdict(float)

    if full:
        path = final_hierarchy_path(directory, start, end)
        if not path.exists():
            raise FileNotFoundError(f"Missing Final hierarchy: {path}")
        for row in read_csv(path):
            if row.get("Analysis_Level") != cfg.LEAF_LEVEL:
                continue
            key = tuple(row.get(k, "").strip() for k in DIMENSIONS)
            totals[key] += number(row.get("Duration_min"))
        method = f"Final hierarchy ({path.name})"
    else:
        path = final_daily_path(directory, start, end)
        if not path.exists():
            raise FileNotFoundError(f"Missing daily Final file: {path}")
        for row in read_csv(path):
            if row.get("Date", "")[:10] not in wanted:
                continue
            key = tuple(row.get(k, "").strip() for k in DIMENSIONS)
            totals[key] += number(row.get("Duration_min"))
        method = (
            f"Daily Final rows filtered to {len(wanted)} covered dates "
            f"({path.name}); exact, not prorated"
        )

    records = [
        dict(zip(DIMENSIONS, key), Actual_min=minutes)
        for key, minutes in totals.items()
    ]
    return records, method


# ---------------------------------------------------------------------------
# Variance
# ---------------------------------------------------------------------------


def is_sleep(category: str, subcategory: str) -> bool:
    return (
        category == cfg.SLEEP_CATEGORY
        and subcategory == cfg.SLEEP_SUBCATEGORY
    )


def classify_status(actual: float, budget: float) -> str:
    has_actual = actual > cfg.ZERO_ACTUAL_MIN
    has_budget = budget > cfg.ZERO_ACTUAL_MIN
    if has_budget and has_actual:
        return "Budgeted"
    if has_budget:
        return "Not recorded"
    if has_actual:
        return "Unbudgeted"
    return "Inactive"


def assess_goal(goal: str, variance: float) -> str:
    direction = cfg.GOAL_DIRECTION.get(goal, 0)
    if direction == 0:
        return "Neutral"
    if abs(variance) <= cfg.ON_BUDGET_TOLERANCE_MIN:
        return "On budget"
    return "Favorable" if direction * variance > 0 else "Unfavorable"


def assess_leaf(
    status: str,
    device: str,
    category: str,
    subcategory: str,
    goal: str,
    variance: float,
    sleep_h_per_night: float | None,
    sleep_floor_h: float,
) -> tuple[str, str]:
    """Return (assessment, basis) for one leaf."""
    if status == "Not recorded":
        return (
            "Capture alert",
            "Budget but no recorded actual: check capture; not a saving.",
        )
    if device == cfg.OFF_DEVICE_DEVICE:
        return "Neutral (residual)", "Off-Device Life is the clock residual."
    if is_sleep(category, subcategory) and sleep_h_per_night is not None:
        ok = sleep_h_per_night + 1e-9 >= sleep_floor_h
        return (
            "Favorable" if ok else "Unfavorable",
            f"Sleep vs floor {sleep_floor_h:g} h/night "
            f"(actual {sleep_h_per_night:.2f} h/night).",
        )
    direction = cfg.GOAL_DIRECTION.get(goal, 0)
    basis = {
        1: f"Goal={goal}: more than budget is favorable.",
        -1: f"Goal={goal}: less than budget is favorable.",
        0: f"Goal={goal}: reported without judgment.",
    }[direction]
    return assess_goal(goal, variance), basis


def _metric_row(
    actual: float,
    budget: float,
    days: int,
    actual_total: float,
    budget_total: float,
) -> dict:
    variance = actual - budget
    actual_share = pct(actual, actual_total)
    budget_share = pct(budget, budget_total)
    return {
        "Actual_min": actual,
        "Budget_min": budget,
        "Variance_min": variance,
        "Actual_h": actual / 60.0,
        "Budget_h": budget / 60.0,
        "Variance_h": variance / 60.0,
        "Variance_pct": pct(variance, budget) if budget > cfg.ZERO_ACTUAL_MIN else "",
        "Actual_min_per_day": actual / days if days else 0.0,
        "Budget_min_per_day": budget / days if days else 0.0,
        "Variance_min_per_day": variance / days if days else 0.0,
        "Actual_share_pct": actual_share if actual_share is not None else "",
        "Budget_share_pct": budget_share if budget_share is not None else "",
        "Share_delta_pp": (
            (actual_share or 0.0) - (budget_share or 0.0)
        ),
    }


METRIC_FIELDS = [
    "Actual_min", "Budget_min", "Variance_min",
    "Actual_h", "Budget_h", "Variance_h", "Variance_pct",
    "Actual_min_per_day", "Budget_min_per_day", "Variance_min_per_day",
    "Actual_share_pct", "Budget_share_pct", "Share_delta_pp",
]

LEAF_FIELDS = [
    "Device", "Category", "Subcategory",
    "Domain", "Energy", "Goal",
    *METRIC_FIELDS,
    "Status", "Assessment", "Assessment_Basis",
    "Actual_Domain", "Actual_Energy", "Actual_Goal", "Taxonomy_Note",
]

ROLLUP_FIELDS_TAIL = [
    "Goal_Weighted_Variance_min", "Not_Recorded_Budget_min",
    "Not_Recorded_Leaves", "Unbudgeted_Actual_min", "Unbudgeted_Leaves",
    "Assessment",
]


@dataclass
class VarianceResult:
    version: str
    coverage: Coverage
    actual_method: str
    leaf_rows: list[dict]
    rollups: dict[str, list[dict]]
    line_rows: list[dict]
    kpis: list[dict]
    top: list[dict]
    reconciliation: dict
    full_period_view: list[dict]
    warnings: list[str] = field(default_factory=list)

    @property
    def has_coverage(self) -> bool:
        return self.coverage.covered_days > 0

    def kpi(self, name: str) -> dict:
        return next(k for k in self.kpis if k["KPI"] == name)


def build_leaf_rows(
    actual_records: list[dict],
    budget: dict[tuple[str, str, str], dict],
    days: int,
    sleep_floor_h: float,
) -> list[dict]:
    actual_by_leaf: dict[tuple, float] = defaultdict(float)
    actual_dims: dict[tuple, tuple[str, str, str, float]] = {}
    for rec in actual_records:
        key = (rec["Device"], rec["Category"], rec["Subcategory"])
        actual_by_leaf[key] += rec["Actual_min"]
        dims = (rec["Domain"], rec["Energy"], rec["Goal"])
        best = actual_dims.get(key)
        if best is None or rec["Actual_min"] > best[3]:
            actual_dims[key] = (*dims, rec["Actual_min"])

    multi_dims = defaultdict(set)
    for rec in actual_records:
        key = (rec["Device"], rec["Category"], rec["Subcategory"])
        multi_dims[key].add((rec["Domain"], rec["Energy"], rec["Goal"]))

    actual_total = sum(actual_by_leaf.values())
    budget_total = sum(b["Budget_min"] for b in budget.values())

    # Sleep is evaluated as total sleep per night over the covered days.
    sleep_actual = sum(
        v for k, v in actual_by_leaf.items() if is_sleep(k[1], k[2])
    )
    sleep_h_per_night = sleep_actual / 60.0 / days if days else None

    rows = []
    for key in sorted(set(actual_by_leaf) | set(budget)):
        actual = actual_by_leaf.get(key, 0.0)
        b = budget.get(key)
        budget_min = b["Budget_min"] if b else 0.0
        status = classify_status(actual, budget_min)
        if status == "Inactive":
            continue
        a_dims = actual_dims.get(key)
        if b:
            domain, energy, goal = b["Domain"], b["Energy"], b["Goal"]
        else:
            domain, energy, goal = a_dims[:3]
        row = {
            "Device": key[0], "Category": key[1], "Subcategory": key[2],
            "Domain": domain, "Energy": energy, "Goal": goal,
            **_metric_row(actual, budget_min, days, actual_total, budget_total),
            "Status": status,
            "Actual_Domain": a_dims[0] if a_dims else "",
            "Actual_Energy": a_dims[1] if a_dims else "",
            "Actual_Goal": a_dims[2] if a_dims else "",
        }
        notes = []
        if a_dims and b and tuple(a_dims[:3]) != (domain, energy, goal):
            notes.append("Actual Domain/Energy/Goal differs from budget")
        if len(multi_dims.get(key, ())) > 1:
            notes.append("Actual leaf has several Domain/Energy/Goal mappings")
        row["Taxonomy_Note"] = "; ".join(notes)
        assessment, basis = assess_leaf(
            status, key[0], key[1], key[2], goal, row["Variance_min"],
            sleep_h_per_night if is_sleep(key[1], key[2]) else None,
            sleep_floor_h,
        )
        row["Assessment"] = assessment
        row["Assessment_Basis"] = basis
        rows.append(row)
    return rows


def rollup(
    leaf_rows: list[dict],
    actual_records: list[dict],
    dims: tuple[str, ...],
    days: int,
) -> list[dict]:
    """Aggregate leaves by ``dims``.

    Actual is grouped by its own Final classification, budget by the budget's
    classification, so totals match the Standard Report. The goal-weighted
    variance (+Investment −Consumption, Not-recorded leaves excluded) drives
    the roll-up assessment.
    """
    actual: dict[tuple, float] = defaultdict(float)
    for rec in actual_records:
        actual[tuple(rec[d] for d in dims)] += rec["Actual_min"]

    budget: dict[tuple, float] = defaultdict(float)
    weighted: dict[tuple, float] = defaultdict(float)
    not_rec: dict[tuple, float] = defaultdict(float)
    not_rec_n: dict[tuple, int] = defaultdict(int)
    unb: dict[tuple, float] = defaultdict(float)
    unb_n: dict[tuple, int] = defaultdict(int)
    for row in leaf_rows:
        group = tuple(row[d] for d in dims)
        budget[group] += row["Budget_min"]
        if row["Status"] == "Not recorded":
            not_rec[group] += row["Budget_min"]
            not_rec_n[group] += 1
            continue
        if row["Status"] == "Unbudgeted":
            unb[group] += row["Actual_min"]
            unb_n[group] += 1
        weighted[group] += (
            cfg.GOAL_DIRECTION.get(row["Goal"], 0) * row["Variance_min"]
        )

    actual_total = sum(actual.values())
    budget_total = sum(budget.values())
    rows = []
    for group in set(actual) | set(budget):
        a = actual.get(group, 0.0)
        b = budget.get(group, 0.0)
        if a <= cfg.ZERO_ACTUAL_MIN and b <= cfg.ZERO_ACTUAL_MIN:
            continue
        w = weighted.get(group, 0.0)
        if dims == ("Goal",) and cfg.GOAL_DIRECTION.get(group[0], 0) == 0:
            assessment = "Neutral"
        elif abs(w) <= cfg.ON_BUDGET_TOLERANCE_MIN:
            has_judged = any(
                cfg.GOAL_DIRECTION.get(r["Goal"], 0)
                for r in leaf_rows
                if tuple(r[d] for d in dims) == group
            )
            assessment = "On budget" if has_judged else "Neutral"
        else:
            assessment = "Favorable" if w > 0 else "Unfavorable"
        rows.append({
            **dict(zip(dims, group)),
            **_metric_row(a, b, days, actual_total, budget_total),
            "Goal_Weighted_Variance_min": w,
            "Not_Recorded_Budget_min": not_rec.get(group, 0.0),
            "Not_Recorded_Leaves": not_rec_n.get(group, 0),
            "Unbudgeted_Actual_min": unb.get(group, 0.0),
            "Unbudgeted_Leaves": unb_n.get(group, 0),
            "Assessment": assessment,
        })
    rows.sort(key=lambda r: -max(r["Actual_min"], r["Budget_min"]))
    return rows


def line_allocation(
    snapshot: BudgetSnapshot,
    start: date,
    end: date,
) -> tuple[dict[tuple[str, tuple], float], dict[tuple, dict[str, float]]]:
    """Return (line budget, leaf → {line: weight}).

    A leaf shared by several lines (e.g. Offline Study Tracker = classes +
    study + thesis) splits its actual by each line's share of the leaf's
    period budget; if the leaf has no budget in the period, by its share over
    the whole horizon; if still zero, equally.
    """
    line_budget = period_line_budget(snapshot, start, end)
    horizon = period_line_budget(
        snapshot, snapshot.horizon_start, snapshot.horizon_end
    )
    by_leaf: dict[tuple, dict[str, float]] = defaultdict(dict)
    for (line, key), minutes in line_budget.items():
        by_leaf[key][line] = by_leaf[key].get(line, 0.0) + minutes
    weights: dict[tuple, dict[str, float]] = {}
    for key, lines in by_leaf.items():
        total = sum(lines.values())
        if total <= cfg.ZERO_ACTUAL_MIN:
            lines = {
                line: horizon.get((line, key), 0.0) for line in lines
            }
            total = sum(lines.values())
        if total <= cfg.ZERO_ACTUAL_MIN:
            weights[key] = {line: 1.0 / len(lines) for line in lines}
        else:
            weights[key] = {line: v / total for line, v in lines.items()}
    return line_budget, weights


def line_rows(
    snapshot: BudgetSnapshot,
    start: date,
    end: date,
    leaf_rows: list[dict],
    days: int,
) -> list[dict]:
    if not snapshot.lines:
        return []
    line_budget, weights = line_allocation(snapshot, start, end)
    line_class = {
        r["Line"]: r.get("Line_Class", "") for r in snapshot.lines
    }
    actual: dict[str, float] = defaultdict(float)
    budget: dict[str, float] = defaultdict(float)
    not_rec: dict[str, float] = defaultdict(float)
    for (line, _key), minutes in line_budget.items():
        budget[line] += minutes
    for row in leaf_rows:
        key = (row["Device"], row["Category"], row["Subcategory"])
        share = weights.get(key, {"Unassigned": 1.0})
        for line, w in share.items():
            actual[line] += row["Actual_min"] * w
            if row["Status"] == "Not recorded":
                not_rec[line] += row["Budget_min"] * w
    actual_total = sum(actual.values())
    budget_total = sum(budget.values())
    rows = []
    for line in sorted(set(actual) | set(budget), key=lambda l: -budget.get(l, 0.0)):
        a, b = actual.get(line, 0.0), budget.get(line, 0.0)
        if a <= cfg.ZERO_ACTUAL_MIN and b <= cfg.ZERO_ACTUAL_MIN:
            continue
        rows.append({
            "Line": line,
            "Line_Class": line_class.get(line, ""),
            **_metric_row(a, b, days, actual_total, budget_total),
            "Not_Recorded_Budget_min": not_rec.get(line, 0.0),
            "Status": classify_status(a, b),
        })
    return rows


def _goal_totals(records: Iterable[dict], value: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for rec in records:
        totals[rec["Goal"]] += rec[value]
    return totals


def discretionary(records: Iterable[dict], value: str) -> float:
    return sum(
        rec[value]
        for rec in records
        if rec["Category"] in cfg.DISCRETIONARY_FULL_CATEGORIES
        or (
            rec["Category"] == cfg.DISCRETIONARY_CONSUMPTION_CATEGORY
            and rec["Goal"] == "Consumption"
        )
    )


def investment_ratio(goals: dict[str, float]) -> float | None:
    base = sum(goals.get(g, 0.0) for g in ("Investment", "Maintenance", "Consumption"))
    return pct(goals.get("Investment", 0.0), base)


def build_kpis(
    snapshot: BudgetSnapshot,
    start: date,
    end: date,
    actual_records: list[dict],
    leaf_rows: list[dict],
    lines: list[dict],
    days: int,
) -> list[dict]:
    budget_records = [
        {"Category": r["Category"], "Goal": r["Goal"], "Budget_min": r["Budget_min"]}
        for r in leaf_rows
    ]
    a_goals = _goal_totals(actual_records, "Actual_min")
    b_goals = _goal_totals(budget_records, "Budget_min")
    a_total = sum(r["Actual_min"] for r in actual_records)
    b_total = sum(r["Budget_min"] for r in leaf_rows)

    sleep_floor = period_parameter(
        snapshot, "sleep_floor_h_per_night", start, end, cfg.DEFAULT_SLEEP_FLOOR_H
    )
    leisure_floor = period_parameter(
        snapshot, "digital_leisure_floor_min_per_day", start, end,
        cfg.DEFAULT_DIGITAL_LEISURE_FLOOR_MIN,
    )
    leisure_line = snapshot.setting(
        "digital_leisure_line", cfg.DEFAULT_DIGITAL_LEISURE_LINE
    )

    def per_day(v: float) -> float:
        return v / days if days else 0.0

    kpis: list[dict] = []

    def add(name, actual, budget, unit, definition, floor=None, status=None):
        variance = (
            actual - budget
            if isinstance(actual, (int, float)) and isinstance(budget, (int, float))
            else ""
        )
        kpis.append({
            "KPI": name, "Actual": actual, "Budget": budget,
            "Variance": variance, "Unit": unit,
            "Floor": "" if floor is None else floor,
            "Status": status or "", "Definition": definition,
        })

    a_ratio = investment_ratio(a_goals)
    b_ratio = investment_ratio(b_goals)
    add(
        "Investment ratio", a_ratio if a_ratio is not None else "",
        b_ratio if b_ratio is not None else "", "%",
        "Investment / (Investment + Maintenance + Consumption).",
        status=assess_goal("Investment", (a_ratio or 0) - (b_ratio or 0))
        if a_ratio is not None and b_ratio is not None else "",
    )
    for goal in ("Investment", "Maintenance", "Consumption"):
        a, b = per_day(a_goals.get(goal, 0.0)), per_day(b_goals.get(goal, 0.0))
        add(
            f"{goal} min/day", a, b, "min/day",
            f"Goal={goal} per covered day.",
            status=assess_goal(goal, (a - b) * days),
        )

    a_disc = discretionary(actual_records, "Actual_min")
    b_disc = discretionary(budget_records, "Budget_min")
    add(
        "Discretionary consumption proxy min/day", per_day(a_disc), per_day(b_disc),
        "min/day",
        "Games + Entertainment + Social Networking with Goal=Consumption "
        "(Standard Report definition).",
        status=assess_goal("Consumption", a_disc - b_disc),
    )
    add(
        "Discretionary consumption proxy share",
        pct(a_disc, a_total) or 0.0, pct(b_disc, b_total) or 0.0, "%",
        "Proxy as a share of analytical activity / of budget.",
    )

    leisure = next((r for r in lines if r["Line"] == leisure_line), None)
    a_leisure = per_day(leisure["Actual_min"]) if leisure else 0.0
    b_leisure = per_day(leisure["Budget_min"]) if leisure else 0.0
    add(
        "Digital leisure min/day", a_leisure, b_leisure, "min/day",
        f"Leaves of budget line '{leisure_line}' (actual split by budget "
        "weight where a leaf is shared). The floor is a minimum.",
        floor=leisure_floor,
        status="At or above floor" if a_leisure + 1e-9 >= leisure_floor else "Below floor",
    )

    a_sleep = sum(r["Actual_min"] for r in actual_records
                  if is_sleep(r["Category"], r["Subcategory"]))
    b_sleep = sum(r["Budget_min"] for r in leaf_rows
                  if is_sleep(r["Category"], r["Subcategory"]))
    a_sleep_h = per_day(a_sleep) / 60.0
    add(
        "Sleep h/night", a_sleep_h, per_day(b_sleep) / 60.0, "h/night",
        "Sleep minutes per covered day / 60; floor from the budget.",
        floor=sleep_floor,
        status="At or above floor" if a_sleep_h + 1e-9 >= sleep_floor else "Below floor",
    )

    not_rec = [r for r in leaf_rows if r["Status"] == "Not recorded"]
    unb = [r for r in leaf_rows if r["Status"] == "Unbudgeted"]
    add(
        "Not recorded leaves", len(not_rec), "", "leaves",
        "Leaves with budget but no recorded actual (capture alert).",
        status=f"{sum(r['Budget_min'] for r in not_rec) / 60.0:.1f} h budgeted"
        if not_rec else "None",
    )
    add(
        "Unbudgeted leaves", len(unb), "", "leaves",
        "Leaves with actual but no budget in the period.",
        status=f"{sum(r['Actual_min'] for r in unb) / 60.0:.1f} h actual"
        if unb else "None",
    )
    return kpis


def full_period_view(
    directory: Path,
    start: date,
    end: date,
    cov: Coverage,
    covered_records: list[dict],
    leaf_rows: list[dict],
) -> list[dict]:
    """Goal totals for the full period next to the covered-days comparison."""
    if cov.is_full:
        full_records = covered_records
    else:
        full_records, _ = load_actual_records(directory, start, end)
    full = _goal_totals(full_records, "Actual_min")
    covered = _goal_totals(covered_records, "Actual_min")
    budget = defaultdict(float)
    for row in leaf_rows:
        budget[row["Goal"]] += row["Budget_min"]
    rows = []
    for goal in ("Investment", "Maintenance", "Consumption", "Total"):
        if goal == "Total":
            f, c, b = sum(full.values()), sum(covered.values()), sum(budget.values())
        else:
            f, c, b = full.get(goal, 0.0), covered.get(goal, 0.0), budget.get(goal, 0.0)
        rows.append({
            "Goal": goal,
            "Actual_full_period_min": f,
            "Actual_full_period_min_per_day": f / cov.period_days,
            "Actual_covered_min": c,
            "Actual_covered_min_per_day": c / cov.covered_days if cov.covered_days else 0.0,
            "Budget_covered_min": b,
            "Budget_covered_min_per_day": b / cov.covered_days if cov.covered_days else 0.0,
        })
    return rows


def compute_variance(
    snapshot: BudgetSnapshot,
    start: date,
    end: date,
    directory: Path | None = None,
) -> VarianceResult:
    """Run the Actual vs Budget comparison for an inclusive period."""
    directory = directory or final_dir(start, end)
    cov = coverage(snapshot, start, end)
    if not cov.covered_days:
        return VarianceResult(
            snapshot.version, cov, "", [], {}, [], [], [],
            {"budget_min": 0.0, "expected_min": 0.0, "difference_min": 0.0, "ok": True},
            [],
        )

    records, method = load_actual_records(directory, start, end, cov.covered_dates)
    budget = period_leaf_budget(snapshot, start, end)
    days = cov.covered_days
    sleep_floor = period_parameter(
        snapshot, "sleep_floor_h_per_night", start, end, cfg.DEFAULT_SLEEP_FLOOR_H
    )
    leaves = build_leaf_rows(records, budget, days, sleep_floor)
    rollups = {
        name: rollup(leaves, records, dims, days)
        for name, dims in ROLLUPS.items()
    }
    lines = line_rows(snapshot, start, end, leaves, days)
    kpis = build_kpis(snapshot, start, end, records, leaves, lines, days)
    top = sorted(leaves, key=lambda r: -abs(r["Variance_min"]))[: cfg.TOP_DEVIATIONS]

    budget_total = sum(b["Budget_min"] for b in budget.values())
    expected = days * cfg.MINUTES_PER_DAY
    recon = {
        "budget_min": budget_total,
        "expected_min": expected,
        "difference_min": budget_total - expected,
        "ok": abs(budget_total - expected) <= cfg.PERIOD_BUDGET_TOLERANCE_MIN,
        "actual_covered_min": sum(r["Actual_min"] for r in records),
    }
    warnings = []
    if not recon["ok"]:
        warnings.append(
            f"Period budget {budget_total:.2f} min does not reconcile to "
            f"{days} covered days × 1440 = {expected:.0f} min."
        )
    mismatches = [r for r in leaves if r["Taxonomy_Note"]]
    if mismatches:
        warnings.append(
            f"{len(mismatches)} leaves have a Domain/Energy/Goal note "
            "(see Taxonomy_Note in the leaf file)."
        )
    full = full_period_view(directory, start, end, cov, records, leaves)
    return VarianceResult(
        snapshot.version, cov, method, leaves, rollups, lines, kpis, top,
        recon, full, warnings,
    )


# ---------------------------------------------------------------------------
# Brief rendering (Standard Report section)
# ---------------------------------------------------------------------------


def _h(minutes: float) -> str:
    sign = "-" if minutes < 0 else ""
    total = int(round(abs(minutes)))
    hours, mins = divmod(total, 60)
    return f"{sign}{hours}h {mins:02d}m"


def brief_lines(result: VarianceResult) -> list[str]:
    """Short plain-text budget block for the Standard Report."""
    cov = result.coverage
    lines = [
        f"Budget version:          {result.version}",
        f"Coverage:                {cov.covered_days}/{cov.period_days} days "
        f"({cov.ratio:.1%}); compared on covered days only",
    ]
    goal_rows = {r["Goal"]: r for r in result.rollups.get("Goal", [])}
    for goal in ("Investment", "Maintenance", "Consumption"):
        r = goal_rows.get(goal)
        if not r:
            continue
        lines.append(
            f"{goal + ':':<24} actual {_h(r['Actual_min'])} vs budget "
            f"{_h(r['Budget_min'])} ({_h(r['Variance_min'])}; {r['Assessment']})"
        )
    for name in (
        "Investment ratio",
        "Digital leisure min/day",
        "Sleep h/night",
    ):
        k = result.kpi(name)
        unit = "%" if k["Unit"] == "%" else f" {k['Unit']}"
        floor = f"; floor {k['Floor']:g}" if k["Floor"] != "" else ""
        status = f" [{k['Status']}]" if k["Status"] else ""
        lines.append(
            f"{name + ':':<24} {k['Actual']:.1f}{unit} vs {k['Budget']:.1f}{unit}"
            f"{floor}{status}"
        )
    not_rec = [r for r in result.leaf_rows if r["Status"] == "Not recorded"]
    if not_rec:
        biggest = sorted(not_rec, key=lambda r: -r["Budget_min"])[:3]
        lines.append(
            "Capture alerts:          "
            + "; ".join(
                f"{r['Device']}/{r['Subcategory']} ({_h(r['Budget_min'])} budgeted, none recorded)"
                for r in biggest
            )
        )
    lines.append(
        "Detail: output/Integrated/Analysis/Budget/"
        f"{period_label(cov.start, cov.end)}/ (budget_variance_analysis.py)"
    )
    return lines
