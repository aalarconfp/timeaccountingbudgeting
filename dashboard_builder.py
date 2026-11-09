#!/usr/bin/env python3
"""Consolidated Time Dashboard (single self-contained HTML file).

Reads the final outputs of the pipeline and embeds them as JSON into
``dashboard/dashboard_template.html``. Nothing is recalculated against the
sources: every number comes from the closed (or interim) Final analysis, the
historical annual analysis and the versioned budget snapshot.

Inputs (all discovered automatically, nothing to configure per month):

* ``output/Integrated/Analysis/Final/<YYYY-MM-01>_<end>/``  fact table
  (``Final_Analysis_*.csv``), daily reconciliation, correction audit
* ``output/Integrated/Analysis/Report/<period>/``  Standard Report,
  Period-Close Checklist (close decision, validator result)
* ``output/Integrated/Analysis/EDA/<period>/``  taxonomy review candidates
* ``output/Integrated/Analysis/Budget/<period>/``  official Actual vs Budget
  (when it exists)
* ``output/Historical/Annual_*/tables/``  13-month historical trends,
  accounting, findings, budget baseline, pending decisions
* ``output/Reference/Budget/``  latest budget snapshot (leaf, lines, phases)

Output: ``output/Dashboard/Time_Dashboard.html`` (open it in any browser;
works offline).

Monthly update (after the period close, runbook H5)::

    python .\\dashboard_builder.py
"""

from __future__ import annotations

import argparse
import calendar
import csv
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = PROJECT_ROOT / "output"
TEMPLATE = PROJECT_ROOT / "dashboard" / "dashboard_template.html"
DEFAULT_OUT = OUTPUT_ROOT / "Dashboard" / "Time_Dashboard.html"
DATA_MARKER = "/*__DASHBOARD_DATA__*/null"

PERIOD_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})$")

# Fact columns kept from Final_Analysis_<period>.csv (dictionary-encoded).
FACT_DIMS = [
    "Source", "Device", "Activity", "Category", "Subcategory",
    "Domain", "Energy", "Goal", "Allocation_Type", "Taxonomy_Mapping",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_table(path: Path, limit: int | None = None) -> dict | None:
    """Generic {headers, rows} table for display-only CSVs."""
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        headers = next(reader, [])
        rows = [row for row in reader]
    if limit is not None:
        rows = rows[:limit]
    return {"headers": headers, "rows": rows}


def num(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "", "n/a") else default
    except ValueError:
        return default


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


class Dictionary:
    """Per-field string dictionary to keep the embedded JSON small."""

    def __init__(self) -> None:
        self.values: dict[str, list[str]] = {d: [] for d in FACT_DIMS}
        self._index: dict[str, dict[str, int]] = {d: {} for d in FACT_DIMS}

    def code(self, dim: str, value: str) -> int:
        index = self._index[dim]
        if value not in index:
            index[value] = len(self.values[dim])
            self.values[dim].append(value)
        return index[value]


# ---------------------------------------------------------------------------
# Period discovery
# ---------------------------------------------------------------------------


def discover_months(final_root: Path) -> list[dict]:
    """One entry per calendar month with a Final folder starting on day 1.

    A folder covering the full month wins (closed month); otherwise the
    longest folder starting on day 1 is used and flagged provisional.
    """
    candidates: dict[str, list[tuple[date, date, Path]]] = {}
    if not final_root.exists():
        return []
    for folder in final_root.iterdir():
        match = PERIOD_RE.match(folder.name)
        if not folder.is_dir() or not match:
            continue
        start = date.fromisoformat(match.group(1))
        end = date.fromisoformat(match.group(2))
        if start.day != 1 or end.month != start.month or end.year != start.year:
            continue
        if not (folder / f"Final_Analysis_{folder.name}.csv").exists():
            continue
        candidates.setdefault(start.strftime("%Y-%m"), []).append((start, end, folder))

    months = []
    for key in sorted(candidates):
        start, end, folder = max(candidates[key], key=lambda c: c[1])
        last_day = calendar.monthrange(start.year, start.month)[1]
        months.append({
            "key": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": folder.name,
            "days": (end - start).days + 1,
            "month_days": last_day,
            "full_month": end.day == last_day,
            "folder": folder,
        })
    return months


# ---------------------------------------------------------------------------
# Month loaders
# ---------------------------------------------------------------------------


def load_fact(month: dict, dictionary: Dictionary) -> list[list]:
    rows = read_csv(month["folder"] / f"Final_Analysis_{month['label']}.csv")
    fact = []
    for row in rows:
        minutes = num(row.get("Duration_min"))
        if minutes == 0:
            continue
        day = int(row["Date"][8:10])
        fact.append([day] + [dictionary.code(d, (row.get(d) or "").strip()) for d in FACT_DIMS]
                    + [round(minutes, 3)])
    return fact


def load_reconciliation(month: dict) -> list[list]:
    rows = read_csv(month["folder"] / f"Final_Daily_Reconciliation_{month['label']}.csv")
    out = []
    for r in rows:
        out.append([
            int(r["Date"][8:10]),
            round(num(r.get("Original_Recorded_min")), 1),
            round(num(r.get("Corrected_Recorded_min")), 1),
            round(num(r.get("Correction_min")), 1),
            round(num(r.get("Overlap_min")), 1),
            round(num(r.get("Uncovered_min")), 1),
            round(num(r.get("Tolerance_min")), 1),
            r.get("Status", ""),
        ])
    return out


def load_corrections(month: dict) -> list[dict]:
    rows = read_csv(month["folder"] / f"Final_Correction_Audit_{month['label']}.csv")
    keep = ["Date", "Correction_Type", "Source", "Category", "Subcategory",
            "Before_min", "Applied_min", "Unapplied_min", "After_min", "Direction",
            "Status", "Reason"]
    return [{k: r.get(k, "") for k in keep} for r in sorted(rows, key=lambda r: r.get("Date", ""))]


def load_report_info(analysis_root: Path, month: dict) -> dict:
    label = month["label"]
    report_dir = analysis_root / "Report" / label
    checklist = read_text(report_dir / f"Period_Close_Checklist_{label}.md")
    standard = read_text(report_dir / f"Standard_Report_{label}.txt")

    decision = ""
    match = re.search(r"Close decision:\s*\**\s*([^*\n]+)", checklist)
    if match:
        decision = match.group(1).strip()
    validator = ""
    match = re.search(r"(\d+/\d+)\s+checks passed", checklist)
    if match:
        validator = match.group(1)
    closed_on = ""
    match = re.search(r"Date closed:\s*(\d{4}-\d{2}-\d{2})", checklist)
    if match:
        closed_on = match.group(1)

    attention = []
    section = re.search(r"5\. WHAT DESERVES ATTENTION\?\s*=+\s*(.*?)\n=+", standard, re.S)
    if section:
        attention = [line[2:].strip() for line in section.group(1).splitlines()
                     if line.startswith("- ")]

    if decision:
        status = decision
    elif month["full_month"]:
        status = "SIN CHECKLIST DE CIERRE"
    else:
        status = "INTERINO / PROVISIONAL"

    return {
        "status": status,
        "validator": validator,
        "closed_on": closed_on,
        "attention": attention,
        "has_standard_report": bool(standard),
    }


def load_eda(analysis_root: Path, month: dict) -> dict | None:
    return read_table(analysis_root / "EDA" / month["label"]
                      / "EDA_Taxonomy_Review_Candidates.csv", limit=40)


def load_official_budget(analysis_root: Path, month: dict) -> dict | None:
    directory = analysis_root / "Budget" / month["label"]
    if not directory.exists():
        return None
    out = {}
    for stem in ("Budget_KPI", "Budget_Variance_Goal", "Budget_Variance_Category",
                 "Budget_Variance_Line", "Budget_Top_Deviations", "Budget_Coverage"):
        table = read_table(directory / f"{stem}_{month['label']}.csv")
        if table:
            out[stem] = table
    return out or None


# ---------------------------------------------------------------------------
# Historical
# ---------------------------------------------------------------------------


def latest_annual_dir(historical_root: Path) -> Path | None:
    dirs = sorted(p for p in historical_root.glob("Annual_*") if p.is_dir())
    return dirs[-1] if dirs else None


def load_historical(historical_root: Path) -> dict | None:
    annual = latest_annual_dir(historical_root)
    if annual is None:
        return None
    tables = annual / "tables"

    trends = []
    for r in read_csv(tables / "Annual_G_Monthly_Trends.csv"):
        trends.append([r["month"], r["regime"], r["dimension"], r["metric"],
                       round(num(r.get("min_per_day")), 2),
                       round(num(r.get("share_pct_of_analytical")), 2)])

    accounting = []
    for r in read_csv(tables / "Annual_A_Monthly_Accounting.csv"):
        accounting.append({
            "month": r.get("month"),
            "regime": r.get("regime"),
            "days": num(r.get("calendar_days")),
            "capacity_h": num(r.get("capacity_h")),
            "recorded_h": num(r.get("recorded_h")),
            "unique_h": num(r.get("accounted_unique_tracked_h")),
            "off_device_h": num(r.get("off_device_life_residual_h")),
            "coverage_pct": num(r.get("coverage_pct_of_capacity")),
            "review_days": num(r.get("days_review_status")),
            "methodology": r.get("methodology_status", ""),
        })

    findings = [{k: r.get(k, "") for k in ("finding_id", "theme", "description",
                                           "classification", "confidence",
                                           "budget_implication")}
                for r in read_csv(tables / "Annual_Patterns_Findings.csv")]
    decisions = [{k: r.get(k, "") for k in ("decision_id", "decision", "why_it_matters",
                                            "current_default_in_analysis", "status")}
                 for r in read_csv(tables / "Annual_Pending_Decisions.csv")]
    baseline = [{k: r.get(k, "") for k in ("budget_line", "label", "protection_or_flexibility",
                                           "baseline_median_min_per_day",
                                           "baseline_p25_min_per_day",
                                           "baseline_p75_min_per_day",
                                           "current_2026_09_min_per_day",
                                           "suggested_low_min_per_day",
                                           "suggested_high_min_per_day",
                                           "confidence")}
                for r in read_csv(tables / "Annual_J_Budget_Baseline.csv")]
    return {
        "folder": annual.name,
        "trends": trends,
        "accounting": accounting,
        "findings": findings,
        "decisions": decisions,
        "baseline": baseline,
    }


# ---------------------------------------------------------------------------
# Budget snapshot
# ---------------------------------------------------------------------------


def load_budget(reference_root: Path) -> dict | None:
    manifests = []
    for path in reference_root.glob("Budget_Manifest_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        manifests.append((data.get("imported_at", ""), path, data))
    if not manifests:
        return None
    _, path, manifest = max(manifests, key=lambda m: m[0])
    version = manifest.get("version") or path.stem.replace("Budget_Manifest_", "")

    phases = [{"phase": r["Phase"], "start": r["Start"], "end": r["End"]}
              for r in read_csv(reference_root / f"Budget_Phases_{version}.csv")]
    leaf = []
    for r in read_csv(reference_root / f"Budget_Leaf_{version}.csv"):
        hpw = num(r.get("Hours_per_week"))
        if hpw:
            leaf.append([r["Device"], r["Category"], r["Subcategory"], r["Domain"],
                         r["Energy"], r["Goal"], r["Phase"], round(hpw, 4)])
    lines = []
    for r in read_csv(reference_root / f"Budget_Lines_{version}.csv"):
        hpw = num(r.get("Hours_per_week"))
        if hpw:
            lines.append([r["Line"], r.get("Line_Class", ""), r.get("Leisure_Component", ""),
                          r["Device"], r["Category"], r["Subcategory"], r["Goal"],
                          r["Phase"], round(hpw, 4)])
    return {
        "version": version,
        "imported_at": manifest.get("imported_at", ""),
        "horizon": manifest.get("horizon", {}),
        "parameters": manifest.get("parameters", {}),
        "phases": phases,
        "leaf": leaf,
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_data(output_root: Path) -> dict:
    analysis_root = output_root / "Integrated" / "Analysis"
    dictionary = Dictionary()
    months = []
    for month in discover_months(analysis_root / "Final"):
        entry = {k: v for k, v in month.items() if k != "folder"}
        entry["fact"] = load_fact(month, dictionary)
        entry["recon"] = load_reconciliation(month)
        entry["corrections"] = load_corrections(month)
        entry.update(load_report_info(analysis_root, month))
        entry["eda_review"] = load_eda(analysis_root, month)
        entry["official_budget"] = load_official_budget(analysis_root, month)
        months.append(entry)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "fact_dims": FACT_DIMS,
        "dict": dictionary.values,
        "months": months,
        "historical": load_historical(output_root / "Historical"),
        "budget": load_budget(output_root / "Reference" / "Budget"),
    }


def render(data: dict, template: Path) -> str:
    html = template.read_text(encoding="utf-8")
    if DATA_MARKER not in html:
        raise SystemExit(f"Template marker {DATA_MARKER!r} not found in {template}")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")  # never close the <script> early
    return html.replace(DATA_MARKER, payload)


def summarize(data: dict) -> list[str]:
    lines = []
    for m in data["months"]:
        total = sum(r[-1] for r in m["fact"])
        overlap = sum(r[4] for r in m["recon"])
        capacity = m["days"] * 1440
        # Analytical activity = capacity + tracked overlap (overlap is counted
        # once in the accounting, but kept in the analytical activity).
        flag = "" if abs(total - overlap - capacity) < 1 else (
            f"  (WARNING: analytical {total:,.1f} - overlap {overlap:,.1f} != capacity {capacity:,.0f})")
        lines.append(f"  {m['key']}  {m['label']}  {m['days']:>2} d  "
                     f"{total / 60:8.1f} h  rows={len(m['fact']):>5}  {m['status']}{flag}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT,
                        help="Pipeline output root (default: ./output).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="Dashboard HTML path (default: output/Dashboard/Time_Dashboard.html).")
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    args = parser.parse_args(argv)

    data = build_data(args.output_root)
    if not data["months"]:
        print("No Final periods found under output/Integrated/Analysis/Final.", file=sys.stderr)
        return 1

    html = render(data, args.template)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")

    print("TIME DASHBOARD")
    print("Months:")
    for line in summarize(data):
        print(line)
    hist = data["historical"]
    print(f"Historical: {hist['folder'] if hist else 'not found'}")
    budget = data["budget"]
    print(f"Budget snapshot: {budget['version'] if budget else 'not found'}")
    print(f"Output: {args.out}  ({args.out.stat().st_size / 1024:,.0f} KB)")
    print("RESULT: DASHBOARD BUILT.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
