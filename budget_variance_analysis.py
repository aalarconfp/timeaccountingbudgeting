#!/usr/bin/env python3
"""Actual vs Budget for a closed (or interim) period.

Runs after ``variation_analysis.py`` (runbook H3). Actual = the post-adjustment
Final analysis of the period; budget = the versioned snapshot in
``output/Reference/Budget/`` (``budget_import.py``). Only days covered by the
budget are compared; coverage is always reported. A period without coverage
degrades cleanly (coverage file + short note, exit code 0).

Output: ``output/Integrated/Analysis/Budget/<start>_<end>/``

* Budget_Coverage_<p>.csv, Budget_Full_Period_View_<p>.csv
* Budget_Variance_Leaf_<p>.csv (Device × Category × Subcategory)
* Budget_Variance_{Device,Category,Goal,Energy,Domain,Line}_<p>.csv
* Budget_KPI_<p>.csv, Budget_Top_Deviations_<p>.csv
* Budget_Variance_Report_<p>.txt (short, recurring)
* Budget_Section_<p>.md (the "Actual vs Budget" section of the
  Monthly KPI Summary; ``--update-kpi-summary`` splices it in)

Nothing here changes Final, EDA, Variation or the Standard Report inputs.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import budget_core as core
from config import budget as cfg


ROLLUP_FILES = ("Device", "Category", "Goal", "Energy", "Domain")


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def h(minutes: float) -> str:
    return f"{minutes / 60.0:,.1f}"


def signed_h(minutes: float) -> str:
    return f"{minutes / 60.0:+,.1f}"


def pct_text(value) -> str:
    return "—" if value in ("", None) else f"{value:+.1f}%"


def kpi_value(value, unit: str) -> str:
    if value in ("", None):
        return "—"
    if unit == "leaves":
        return f"{int(value)}"
    if unit == "%":
        return f"{value:.1f}%"
    if unit == "h/night":
        return f"{value:.2f}"
    return f"{value:.1f}"


def md_table(headers: list[str], rows: list[list[str]], right: set[int]) -> list[str]:
    align = ["---:" if i in right else "---" for i in range(len(headers))]
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return out


def text_table(headers: list[str], rows: list[list[str]], right: set[int]) -> list[str]:
    widths = [
        max(len(str(x)) for x in [headers[i]] + [r[i] for r in rows])
        for i in range(len(headers))
    ]

    def fmt(row):
        return "  ".join(
            str(c).rjust(widths[i]) if i in right else str(c).ljust(widths[i])
            for i, c in enumerate(row)
        ).rstrip()

    return [fmt(headers), "  ".join("-" * w for w in widths)] + [fmt(r) for r in rows]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def output_dir(root: Path, start: date, end: date) -> Path:
    return root / core.period_label(start, end)


def out_file(directory: Path, stem: str, start: date, end: date, ext: str = "csv") -> Path:
    return directory / f"{stem}_{core.period_label(start, end)}.{ext}"


def kpi_summary_path(report_root: Path, start: date, end: date) -> Path:
    label = core.period_label(start, end)
    return report_root / label / f"Monthly_KPI_Summary_{label}.md"


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


COVERAGE_FIELDS = [
    "Period_Start", "Period_End", "Period_Days", "Covered_Days", "Coverage_pct",
    "First_Covered", "Last_Covered", "Budget_Version", "Phase_Days",
    "Budget_Total_min", "Expected_Budget_min", "Budget_Reconciliation_Diff_min",
    "Budget_Reconciles", "Actual_Covered_min", "Actual_Method", "Comparison_Basis",
]


def coverage_row(result: core.VarianceResult) -> dict:
    cov = result.coverage
    recon = result.reconciliation
    return {
        "Period_Start": cov.start.isoformat(),
        "Period_End": cov.end.isoformat(),
        "Period_Days": cov.period_days,
        "Covered_Days": cov.covered_days,
        "Coverage_pct": cov.ratio * 100.0,
        "First_Covered": cov.first_covered.isoformat() if cov.first_covered else "",
        "Last_Covered": cov.last_covered.isoformat() if cov.last_covered else "",
        "Budget_Version": result.version,
        "Phase_Days": "; ".join(f"{k}={v}" for k, v in cov.phase_days.items() if v),
        "Budget_Total_min": recon["budget_min"],
        "Expected_Budget_min": recon["expected_min"],
        "Budget_Reconciliation_Diff_min": recon["difference_min"],
        "Budget_Reconciles": "YES" if recon["ok"] else "NO",
        "Actual_Covered_min": recon.get("actual_covered_min", 0.0),
        "Actual_Method": result.actual_method,
        "Comparison_Basis": (
            "Covered days only" if cov.covered_days else "No budget coverage"
        ),
    }


def write_outputs(result: core.VarianceResult, directory: Path) -> list[Path]:
    cov = result.coverage
    s, e = cov.start, cov.end
    written = []

    path = out_file(directory, "Budget_Coverage", s, e)
    core.write_csv(path, [coverage_row(result)], COVERAGE_FIELDS)
    written.append(path)
    if not result.has_coverage:
        return written

    path = out_file(directory, "Budget_Variance_Leaf", s, e)
    core.write_csv(path, result.leaf_rows, core.LEAF_FIELDS)
    written.append(path)

    for name in ROLLUP_FILES:
        path = out_file(directory, f"Budget_Variance_{name}", s, e)
        fields = list(core.ROLLUPS[name]) + core.METRIC_FIELDS + core.ROLLUP_FIELDS_TAIL
        core.write_csv(path, result.rollups[name], fields)
        written.append(path)

    if result.line_rows:
        path = out_file(directory, "Budget_Variance_Line", s, e)
        core.write_csv(
            path, result.line_rows,
            ["Line", "Line_Class"] + core.METRIC_FIELDS
            + ["Not_Recorded_Budget_min", "Status"],
        )
        written.append(path)

    path = out_file(directory, "Budget_KPI", s, e)
    core.write_csv(
        path, result.kpis,
        ["KPI", "Actual", "Budget", "Variance", "Unit", "Floor", "Status", "Definition"],
    )
    written.append(path)

    path = out_file(directory, "Budget_Top_Deviations", s, e)
    core.write_csv(
        path,
        [{"Rank": i + 1, **row} for i, row in enumerate(result.top)],
        ["Rank"] + core.LEAF_FIELDS,
    )
    written.append(path)

    path = out_file(directory, "Budget_Full_Period_View", s, e)
    core.write_csv(
        path, result.full_period_view,
        list(result.full_period_view[0].keys()) if result.full_period_view else ["Goal"],
    )
    written.append(path)
    return written


# ---------------------------------------------------------------------------
# Markdown section (Monthly KPI Summary) and text report
# ---------------------------------------------------------------------------


def _goal_rows(result: core.VarianceResult) -> list[list[str]]:
    order = {"Investment": 0, "Maintenance": 1, "Consumption": 2}
    rows = sorted(result.rollups["Goal"], key=lambda r: order.get(r["Goal"], 9))
    return [
        [
            r["Goal"], h(r["Actual_min"]), h(r["Budget_min"]),
            signed_h(r["Variance_min"]), pct_text(r["Variance_pct"]),
            f"{r['Actual_min_per_day']:.1f}", f"{r['Budget_min_per_day']:.1f}",
            r["Assessment"],
        ]
        for r in rows
    ]


GOAL_HEADERS = [
    "Goal", "Actual h", "Budget h", "Variance h", "Variance %",
    "Actual min/day", "Budget min/day", "Assessment",
]


def _kpi_rows(result: core.VarianceResult) -> list[list[str]]:
    rows = []
    for k in result.kpis:
        rows.append([
            k["KPI"], kpi_value(k["Actual"], k["Unit"]),
            kpi_value(k["Budget"], k["Unit"]),
            "—" if k["Floor"] == "" else f"{k['Floor']:g}",
            k["Unit"], k["Status"] or "—",
        ])
    return rows


KPI_HEADERS = ["KPI", "Actual", "Budget", "Floor", "Unit", "Status"]


def _top_rows(result: core.VarianceResult) -> list[list[str]]:
    return [
        [
            str(i + 1), r["Device"], r["Category"], r["Subcategory"], r["Goal"],
            h(r["Actual_min"]), h(r["Budget_min"]), signed_h(r["Variance_min"]),
            r["Status"], r["Assessment"],
        ]
        for i, r in enumerate(result.top)
    ]


TOP_HEADERS = [
    "#", "Device", "Category", "Subcategory", "Goal",
    "Actual h", "Budget h", "Variance h", "Status", "Assessment",
]


def _alerts(result: core.VarianceResult) -> tuple[list[dict], list[dict]]:
    not_rec = sorted(
        (r for r in result.leaf_rows if r["Status"] == "Not recorded"),
        key=lambda r: -r["Budget_min"],
    )
    unb = sorted(
        (r for r in result.leaf_rows if r["Status"] == "Unbudgeted"),
        key=lambda r: -r["Actual_min"],
    )
    return not_rec, unb


def _no_coverage_text(result: core.VarianceResult, snapshot: core.BudgetSnapshot) -> str:
    return (
        f"Budget: **not available for this period** — 0 of "
        f"{result.coverage.period_days} days are covered by the approved budget "
        f"(version `{result.version}`, horizon {snapshot.horizon_start} .. "
        f"{snapshot.horizon_end}). Actuals only."
    )


def render_section(result: core.VarianceResult, snapshot: core.BudgetSnapshot) -> str:
    cov = result.coverage
    label = core.period_label(cov.start, cov.end)
    lines = [cfg.KPI_SECTION_BEGIN, "## Actual vs Budget", ""]
    if not result.has_coverage:
        lines += [_no_coverage_text(result, snapshot), "", cfg.KPI_SECTION_END]
        return "\n".join(lines) + "\n"

    recon = result.reconciliation
    lines += [
        f"Budget version `{result.version}` · {cov.describe()} "
        f"Compared on covered days only; actual = Final post-adjustment "
        f"({result.actual_method}). Budget Σ = {recon['budget_min']:,.1f} min = "
        f"{cov.covered_days} × 1,440 "
        f"({'reconciles' if recon['ok'] else 'DOES NOT RECONCILE'}).",
        "",
        "Variance = Actual − Budget. Investment: more is favorable; Consumption: "
        "less is favorable; Maintenance: no judgment (Sleep vs its floor).",
        "",
    ]
    lines += md_table(GOAL_HEADERS, _goal_rows(result), {1, 2, 3, 4, 5, 6})
    lines += ["", "### KPIs", ""]
    lines += md_table(KPI_HEADERS, _kpi_rows(result), {1, 2, 3})
    lines += ["", "### Top 10 absolute deviations (leaf)", ""]
    lines += md_table(TOP_HEADERS, _top_rows(result), {5, 6, 7})

    not_rec, unb = _alerts(result)
    lines += ["", "### Capture alerts and unbudgeted time", ""]
    if not_rec:
        lines += [
            f"- **Not recorded** (budget, no actual — check capture, not a saving): "
            + "; ".join(
                f"{r['Device']} / {r['Category']} / {r['Subcategory']} "
                f"({h(r['Budget_min'])} h budgeted)"
                for r in not_rec[:8]
            )
            + (f"; +{len(not_rec) - 8} more" if len(not_rec) > 8 else "")
        ]
    else:
        lines.append("- Not recorded: none.")
    if unb:
        lines.append(
            f"- **Unbudgeted** (actual, no budget): {len(unb)} leaves, "
            f"{h(sum(r['Actual_min'] for r in unb))} h — largest: "
            + "; ".join(
                f"{r['Device']} / {r['Subcategory']} ({h(r['Actual_min'])} h)"
                for r in unb[:5]
            )
        )
    else:
        lines.append("- Unbudgeted: none.")

    if not cov.is_full:
        lines += ["", "### Full-period view (informational)", ""]
        lines += md_table(
            ["Goal", "Actual full period min/day", "Actual covered min/day",
             "Budget covered min/day"],
            [
                [r["Goal"], f"{r['Actual_full_period_min_per_day']:.1f}",
                 f"{r['Actual_covered_min_per_day']:.1f}",
                 f"{r['Budget_covered_min_per_day']:.1f}"]
                for r in result.full_period_view
            ],
            {1, 2, 3},
        )
        lines.append(
            f"\nDays outside the budget horizon ({cov.period_days - cov.covered_days}) "
            "are excluded from the comparison above."
        )
    if result.warnings:
        lines += [""] + [f"> Warning: {w}" for w in result.warnings]
    lines += [
        "",
        f"Detail: `output/Integrated/Analysis/Budget/{label}/` "
        "(`Budget_Variance_*`, `Budget_KPI_*`, `Budget_Variance_Report_*.txt`).",
        "",
        cfg.KPI_SECTION_END,
    ]
    return "\n".join(lines) + "\n"


def render_report(result: core.VarianceResult, snapshot: core.BudgetSnapshot) -> str:
    cov = result.coverage
    rule = "=" * 78
    lines = [
        "ACTUAL vs BUDGET REPORT",
        "",
        f"Period: {cov.start} -> {cov.end}",
        f"Days: {cov.period_days}",
        f"Budget version: {result.version}",
        cov.describe(),
        "",
    ]
    if not result.has_coverage:
        lines += [
            f"No budget comparison: the budget horizon is {snapshot.horizon_start} "
            f"-> {snapshot.horizon_end}.",
            "",
            "END OF ACTUAL vs BUDGET REPORT",
        ]
        return "\n".join(lines) + "\n"

    recon = result.reconciliation
    lines += [
        "Comparison basis: covered days only.",
        f"Actual source: {result.actual_method}.",
        f"Budget total: {recon['budget_min']:,.1f} min; expected "
        f"{recon['expected_min']:,.0f} (covered days x 1440); "
        f"{'RECONCILES' if recon['ok'] else 'DOES NOT RECONCILE'}.",
        "Variance = Actual - Budget. Investment: more is favorable. Consumption:",
        "less is favorable. Maintenance: no judgment, except Sleep (vs floor).",
        "",
        rule, "1. GOAL", rule,
    ]
    lines += text_table(GOAL_HEADERS, _goal_rows(result), {1, 2, 3, 4, 5, 6})
    lines += ["", rule, "2. KPIs", rule]
    lines += text_table(KPI_HEADERS, _kpi_rows(result), {1, 2, 3})

    for number, name in ((3, "Device"), (4, "Category"), (5, "Energy"), (6, "Domain")):
        lines += ["", rule, f"{number}. {name.upper()}", rule]
        lines += text_table(
            [name, "Actual h", "Budget h", "Variance h", "Variance %", "Assessment"],
            [
                [r[name], h(r["Actual_min"]), h(r["Budget_min"]),
                 signed_h(r["Variance_min"]), pct_text(r["Variance_pct"]),
                 r["Assessment"]]
                for r in result.rollups[name]
            ],
            {1, 2, 3, 4},
        )

    lines += ["", rule, "7. TOP 10 ABSOLUTE DEVIATIONS (LEAF)", rule]
    lines += text_table(TOP_HEADERS, _top_rows(result), {5, 6, 7})

    not_rec, unb = _alerts(result)
    lines += ["", rule, "8. CAPTURE ALERTS / UNBUDGETED", rule]
    if not_rec:
        lines.append("Not recorded (budget, no actual - check capture; not a saving):")
        lines += [
            f"- {r['Device']} / {r['Category']} / {r['Subcategory']}: "
            f"{h(r['Budget_min'])} h budgeted"
            for r in not_rec
        ]
    else:
        lines.append("Not recorded: none.")
    if unb:
        lines.append(f"Unbudgeted (actual, no budget): {len(unb)} leaves, "
                     f"{h(sum(r['Actual_min'] for r in unb))} h; largest:")
        lines += [
            f"- {r['Device']} / {r['Category']} / {r['Subcategory']}: "
            f"{h(r['Actual_min'])} h"
            for r in unb[:10]
        ]
    else:
        lines.append("Unbudgeted: none.")

    if result.line_rows:
        lines += ["", rule, "9. BUDGET LINES (optional dimension)", rule]
        lines += text_table(
            ["Line", "Class", "Actual h", "Budget h", "Variance h", "Status"],
            [
                [r["Line"], r["Line_Class"], h(r["Actual_min"]), h(r["Budget_min"]),
                 signed_h(r["Variance_min"]), r["Status"]]
                for r in result.line_rows
            ],
            {2, 3, 4},
        )
        lines.append(
            "A leaf shared by several lines splits its actual by budget weight."
        )

    if not cov.is_full:
        lines += ["", rule, "10. FULL-PERIOD VIEW (informational)", rule]
        lines += text_table(
            ["Goal", "Actual full min/day", "Actual covered min/day", "Budget min/day"],
            [
                [r["Goal"], f"{r['Actual_full_period_min_per_day']:.1f}",
                 f"{r['Actual_covered_min_per_day']:.1f}",
                 f"{r['Budget_covered_min_per_day']:.1f}"]
                for r in result.full_period_view
            ],
            {1, 2, 3},
        )

    if result.warnings:
        lines += [""] + [f"WARNING: {w}" for w in result.warnings]
    lines += ["", "END OF ACTUAL vs BUDGET REPORT"]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Monthly KPI Summary splice
# ---------------------------------------------------------------------------


H2 = re.compile(r"^## (?!#)", re.MULTILINE)


def _section_bounds(text: str, title_prefix: str) -> tuple[int, int] | None:
    """Return (start, end) of the H2 section whose title starts with prefix."""
    for match in H2.finditer(text):
        if text[match.end():].startswith(title_prefix):
            nxt = H2.search(text, match.end())
            return match.start(), nxt.start() if nxt else len(text)
    return None


def splice_kpi_summary(text: str, section: str) -> str:
    """Insert or replace the budget section in a Monthly KPI Summary.

    Idempotent: an existing marked section is replaced in place. Otherwise the
    legacy "## Time budget" block is removed and the section is inserted
    right after the historical comparison (or appended).
    """
    begin = text.find(cfg.KPI_SECTION_BEGIN)
    end = text.find(cfg.KPI_SECTION_END)
    if begin != -1 and end != -1:
        end += len(cfg.KPI_SECTION_END)
        if text[end:end + 1] == "\n":
            end += 1
        return text[:begin] + section + text[end:]

    legacy = _section_bounds(text, "Time budget")
    if legacy:
        text = text[: legacy[0]] + text[legacy[1]:]

    historical = _section_bounds(text, "Historical comparison")
    if historical:
        pos = historical[1]
        before = text[:pos].rstrip("\n") + "\n\n"
        after = text[pos:]
        return before + section + ("\n" + after if after else "")
    return text.rstrip("\n") + "\n\n" + section


def update_kpi_summary(path: Path, section: str) -> str:
    if not path.exists():
        return f"Monthly KPI Summary not found ({path}); section saved separately."
    original = path.read_text(encoding="utf-8")
    updated = splice_kpi_summary(original, section)
    if updated == original:
        return f"Monthly KPI Summary already up to date: {path}"
    backup = path.with_suffix(".md.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(updated, encoding="utf-8")
    return f"Monthly KPI Summary updated: {path} (pre-budget copy: {backup.name})"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Actual vs Budget for a period.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--budget-version", help="Snapshot version (default: latest).")
    parser.add_argument("--budget-root", type=Path, default=cfg.BUDGET_REFERENCE_ROOT)
    parser.add_argument("--final-root", type=Path, default=cfg.FINAL_ROOT)
    parser.add_argument("--output-root", type=Path, default=cfg.BUDGET_ANALYSIS_ROOT)
    parser.add_argument("--report-root", type=Path, default=cfg.REPORT_ROOT)
    parser.add_argument("--update-kpi-summary", action="store_true",
                        help="Splice the section into Monthly_KPI_Summary_<period>.md.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing budget-analysis outputs.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    start = core.parse_date(args.start_date)
    end = core.parse_date(args.end_date)
    if end < start:
        print("ERROR: end date is before start date.")
        return 1

    print("=== Actual vs Budget ===")
    print(f"Period  : {start} -> {end}")
    try:
        snapshot = core.load_snapshot(args.budget_root, args.budget_version)
    except core.BudgetNotAvailable as exc:
        print(f"Budget  : NOT AVAILABLE - {exc}")
        print("RESULT: NO BUDGET SNAPSHOT; NOTHING WRITTEN.")
        return 0

    final = core.final_dir(start, end, args.final_root)
    if not final.exists():
        print(f"ERROR: Final analysis not found: {final}. Run final_analysis.py first.")
        return 1

    directory = output_dir(args.output_root, start, end)
    if directory.exists() and any(directory.iterdir()) and not args.force:
        print(f"ERROR: output already exists: {directory}. Use --force.")
        return 1

    result = core.compute_variance(snapshot, start, end, final)
    print(f"Budget  : {snapshot.version}")
    print(f"Coverage: {result.coverage.describe()}")

    directory.mkdir(parents=True, exist_ok=True)
    written = write_outputs(result, directory)
    report = out_file(directory, "Budget_Variance_Report", start, end, "txt")
    report.write_text(render_report(result, snapshot), encoding="utf-8")
    section = render_section(result, snapshot)
    section_path = out_file(directory, "Budget_Section", start, end, "md")
    section_path.write_text(section, encoding="utf-8")
    written += [report, section_path]

    if args.update_kpi_summary:
        print(update_kpi_summary(kpi_summary_path(args.report_root, start, end), section))

    if not result.has_coverage:
        print("No budget days in this period: comparison skipped (coverage file written).")
        print(f"Output  : {directory}")
        print("RESULT: NO BUDGET COVERAGE FOR PERIOD.")
        return 0

    recon = result.reconciliation
    print(f"Actual  : {result.actual_method}")
    print(f"Budget Σ: {recon['budget_min']:,.2f} min vs {recon['expected_min']:,.0f} "
          f"expected -> {'RECONCILES' if recon['ok'] else 'DOES NOT RECONCILE'}")
    for k in result.kpis:
        if k["KPI"] in ("Investment ratio", "Digital leisure min/day", "Sleep h/night"):
            print(f"  {k['KPI']:<26} actual {kpi_value(k['Actual'], k['Unit'])} vs "
                  f"budget {kpi_value(k['Budget'], k['Unit'])} {k['Status']}")
    not_rec, unb = _alerts(result)
    print(f"Not recorded leaves: {len(not_rec)}; unbudgeted leaves: {len(unb)}")
    for w in result.warnings:
        print(f"WARNING : {w}")
    print(f"Files   : {len(written)} in {directory}")
    print("RESULT: BUDGET VARIANCE ANALYSIS GENERATED." if recon["ok"]
          else "RESULT: BUDGET VARIANCE GENERATED WITH RECONCILIATION ERROR.")
    return 0 if recon["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
