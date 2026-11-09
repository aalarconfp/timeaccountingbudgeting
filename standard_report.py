# FILE: standard_report.py

"""Generate the recurring KPI report from reconciled final analysis outputs."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
FINAL_ROOT = PROJECT_ROOT / "output" / "Integrated" / "Analysis" / "Final"
REPORT_ROOT = PROJECT_ROOT / "output" / "Integrated" / "Analysis" / "Report"

CSV_ENCODING = "utf-8-sig"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the recurring standard KPI report."
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--no-budget",
        action="store_true",
        help="Omit the Actual vs Budget section even if a budget covers the period.",
    )
    return parser.parse_args()


def parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def period_days(start_date: date, end_date: date) -> int:
    return (end_date - start_date).days + 1


def final_dir(start_date: date, end_date: date) -> Path:
    return FINAL_ROOT / f"{start_date}_{end_date}"


def hierarchy_path(start_date: date, end_date: date) -> Path:
    return final_dir(start_date, end_date) / (
        "Final_Analysis_By_Device_Category_Subcategory_Domain_Energy_Goal_"
        f"{start_date}_{end_date}.csv"
    )


def universe_path(start_date: date, end_date: date) -> Path:
    return final_dir(start_date, end_date) / (
        f"Final_Time_Universe_Reconciliation_{start_date}_{end_date}.csv"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding=CSV_ENCODING, newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: object) -> float:
    try:
        return float(str(value).strip() or 0)
    except ValueError:
        return 0.0


def duration(seconds: float) -> str:
    minutes = int(round(seconds / 60.0))
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def pct(value: float, total: float) -> float:
    return (value / total * 100.0) if total else 0.0


def deepest_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    level = "Device → Category → Subcategory → Domain → Energy → Goal"
    return [row for row in rows if row.get("Analysis_Level") == level]


def aggregate(
    rows: list[dict[str, str]],
    field: str,
) -> list[tuple[str, float]]:
    totals: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        totals[row.get(field, "").strip()] += number(row["Duration_sec"])
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def load_universe(
    path: Path,
) -> tuple[float, float, float, float, float]:
    rows = read_csv(path)
    capacity = 0.0
    corrected = 0.0
    overlap = 0.0
    off_device = 0.0

    for row in rows:
        if row.get("Time_Bucket") == "Period Capacity":
            corrected += number(row.get("Corrected_Tracked_min")) * 60.0
            overlap += number(row.get("Overlap_min")) * 60.0
            capacity += 24.0 * 60.0 * 60.0

        if row.get("Time_Bucket") == "Off-Device / Untracked Residual":
            off_device += number(
                row.get("Analytical_Off_Device_min")
                or row.get("Gross_Residual_min")
            ) * 60.0

    unique = max(0.0, corrected - overlap)
    return capacity, corrected, unique, off_device, overlap


def find_time(
    rows: list[dict[str, str]],
    field: str,
    value: str,
) -> float:
    return sum(
        number(row["Duration_sec"])
        for row in rows
        if row.get(field, "").strip() == value
    )


def find_pair(
    rows: list[dict[str, str]],
    first_field: str,
    first_value: str,
    second_field: str,
    second_value: str,
) -> float:
    return sum(
        number(row["Duration_sec"])
        for row in rows
        if row.get(first_field, "").strip() == first_value
        and row.get(second_field, "").strip() == second_value
    )


def build_recommendations(
    rows: list[dict[str, str]],
    analytical_total: float,
    capacity: float,
    unique: float,
    off_device: float,
) -> list[str]:
    games = find_time(rows, "Category", "Games")
    entertainment = find_time(rows, "Category", "Entertainment")
    social_consumption = find_pair(
        rows,
        "Category",
        "Social Networking",
        "Goal",
        "Consumption",
    )
    passive = find_time(rows, "Energy", "Passive")
    deep = find_time(rows, "Energy", "Deep")
    maintenance = find_time(rows, "Goal", "Maintenance")
    recovery = find_time(rows, "Energy", "Recovery")
    investment = find_time(rows, "Goal", "Investment")

    recommendations: list[str] = []

    discretionary_consumption = games + entertainment + social_consumption
    if pct(discretionary_consumption, analytical_total) >= 20:
        recommendations.append(
            "Review discretionary consumption first: Games + Entertainment "
            f"+ Social consumption account for {duration(discretionary_consumption)} "
            f"({pct(discretionary_consumption, analytical_total):.1f}% of analytical time)."
        )
    else:
        recommendations.append(
            "Keep discretionary consumption visible, but it is not the first "
            "priority from this period's KPI mix."
        )

    if pct(deep, analytical_total) >= 20:
        recommendations.append(
            f"Protect Deep-energy blocks: {duration(deep)} "
            f"({pct(deep, analytical_total):.1f}%)."
        )
    else:
        recommendations.append(
            "Consider protecting more uninterrupted Deep-energy time if "
            "investment activities are being crowded out."
        )

    if pct(off_device, capacity) < 5:
        recommendations.append(
            "Monitor Off-Device Life across future periods. A sustained decline "
            "may indicate that maintenance, meals, commuting, conversations, "
            "and other human-life time are being compressed."
        )
    else:
        recommendations.append(
            f"Preserve Off-Device Life: {duration(off_device)} of clock capacity "
            "is explicitly represented as offline human-life maintenance."
        )

    if pct(recovery, analytical_total) < 25:
        recommendations.append(
            "Review recovery allocation because Recovery is below 25% of "
            "analytical activity time."
        )
    else:
        recommendations.append(
            f"Recovery is substantial at {duration(recovery)}; evaluate its "
            "quality and consistency rather than treating recovery as wasted time."
        )

    investment_ratio = pct(investment, investment + maintenance + find_time(rows, "Goal", "Consumption"))
    recommendations.append(
        f"Investment share is {investment_ratio:.1f}% of classified goal time; "
        "protect the highest-value investment activities before optimizing minor categories."
    )

    if analytical_total > capacity:
        recommendations.append(
            "Analytical activity exceeds clock capacity because observations overlap. "
            "Use the time-universe unique/off-device figures for clock accounting."
        )

    return recommendations[:5]


def build_report(
    start_date: date,
    end_date: date,
    rows: list[dict[str, str]],
    capacity: float,
    corrected: float,
    unique: float,
    off_device: float,
    overlap: float,
    budget_lines: list[str] | None = None,
) -> str:
    days = period_days(start_date, end_date)
    analytical_total = sum(number(row["Duration_sec"]) for row in rows)

    maintenance = find_time(rows, "Goal", "Maintenance")
    investment = find_time(rows, "Goal", "Investment")
    consumption = find_time(rows, "Goal", "Consumption")
    recovery = find_time(rows, "Energy", "Recovery")
    deep = find_time(rows, "Energy", "Deep")
    passive = find_time(rows, "Energy", "Passive")
    active = find_time(rows, "Energy", "Active")
    shallow = find_time(rows, "Energy", "Shallow")

    categories = aggregate(rows, "Category")
    domains = aggregate(rows, "Domain")
    devices = aggregate(rows, "Device")

    top_categories = categories[:5]
    top_domains = domains[:5]
    top_devices = devices[:4]

    games = find_time(rows, "Category", "Games")
    entertainment = find_time(rows, "Category", "Entertainment")
    social_consumption = find_pair(
        rows,
        "Category",
        "Social Networking",
        "Goal",
        "Consumption",
    )
    discretionary_consumption = games + entertainment + social_consumption

    investment_base = investment + maintenance + consumption
    investment_ratio = pct(investment, investment_base)

    recommendations = build_recommendations(
        rows,
        analytical_total,
        capacity,
        unique,
        off_device,
    )

    lines = [
        "STANDARD TIME / HABIT & WELLNESS REPORT",
        "",
        f"Period: {start_date} -> {end_date}",
        f"Days: {days}",
        "",
        "=" * 78,
        "1. EXECUTIVE SUMMARY",
        "=" * 78,
        f"Clock capacity:          {duration(capacity)}",
        f"Unique tracked time:     {duration(unique)}",
        f"Off-Device Life:         {duration(off_device)}",
        f"Tracked overlap:         {duration(overlap)}",
        f"Analytical activity:     {duration(analytical_total)}",
        f"Clock coverage:          {pct(unique + off_device, capacity):.1f}%",
        "",
        "Analytical activity can exceed clock capacity because multiple sources",
        "observe overlapping time. Clock accounting therefore uses unique tracked",
        "time + Off-Device Life, not the raw analytical total.",
        "",
        "=" * 78,
        "2. WHERE DID MY TIME GO?",
        "=" * 78,
        "Top categories:",
    ]

    for name, seconds in top_categories:
        lines.append(
            f"- {name}: {duration(seconds)} ({pct(seconds, analytical_total):.1f}%)"
        )

    lines.extend(
        [
            "",
            "Top domains:",
        ]
    )

    for name, seconds in top_domains:
        lines.append(
            f"- {name}: {duration(seconds)} ({pct(seconds, analytical_total):.1f}%)"
        )

    lines.extend(
        [
            "",
            "By device:",
        ]
    )

    for name, seconds in top_devices:
        lines.append(
            f"- {name}: {duration(seconds)} ({pct(seconds, analytical_total):.1f}%)"
        )

    lines.extend(
        [
            "",
            "=" * 78,
            "3. INVESTMENT vs MAINTENANCE vs CONSUMPTION",
            "=" * 78,
            f"Investment:             {duration(investment)} "
            f"({pct(investment, analytical_total):.1f}%)",
            f"Maintenance:            {duration(maintenance)} "
            f"({pct(maintenance, analytical_total):.1f}%)",
            f"Consumption:            {duration(consumption)} "
            f"({pct(consumption, analytical_total):.1f}%)",
            f"Investment ratio:       {investment_ratio:.1f}%",
            "",
            "Discretionary consumption proxy:",
            f"- Games:                 {duration(games)}",
            f"- Entertainment:         {duration(entertainment)}",
            f"- Social consumption:    {duration(social_consumption)}",
            f"- Combined:              {duration(discretionary_consumption)} "
            f"({pct(discretionary_consumption, analytical_total):.1f}%)",
            "",
            "=" * 78,
            "4. ENERGY / RECOVERY",
            "=" * 78,
            f"Recovery:               {duration(recovery)} "
            f"({pct(recovery, analytical_total):.1f}%)",
            f"Deep:                   {duration(deep)} "
            f"({pct(deep, analytical_total):.1f}%)",
            f"Active:                 {duration(active)} "
            f"({pct(active, analytical_total):.1f}%)",
            f"Shallow:                {duration(shallow)} "
            f"({pct(shallow, analytical_total):.1f}%)",
            f"Passive:                {duration(passive)} "
            f"({pct(passive, analytical_total):.1f}%)",
            "",
            f"Recovery/day:           {recovery / days / 60:.1f} min/day",
            f"Investment/day:         {investment / days / 60:.1f} min/day",
            f"Maintenance/day:        {maintenance / days / 60:.1f} min/day",
            "",
            "=" * 78,
            "5. WHAT DESERVES ATTENTION?",
            "=" * 78,
        ]
    )

    for recommendation in recommendations:
        lines.append(f"- {recommendation}")

    lines.extend(
        [
            "",
            "=" * 78,
            "6. KPI DEFINITIONS",
            "=" * 78,
            "Clock capacity = number of days × 24 hours.",
            "Unique tracked = corrected tracked time - tracked overlap.",
            "Off-Device Life = remaining clock capacity represented as human-life",
            "maintenance and other offline activity.",
            "Investment ratio = Investment / (Investment + Maintenance + Consumption).",
            "Recovery share = Recovery / analytical activity time.",
            "Consumption proxy = Games + Entertainment + Social Networking time",
            "classified with Goal=Consumption.",
            "",
            "This report is intentionally small and recurring.",
            "Use exploratory_analysis.py for deeper taxonomy and behavioral diagnostics.",
            "",
        ]
    )

    # Optional: only when an approved budget covers part of the period.
    if budget_lines:
        lines.extend(
            [
                "=" * 78,
                "7. ACTUAL vs BUDGET",
                "=" * 78,
                *budget_lines,
                "",
            ]
        )

    lines.extend(
        [
            "=" * 78,
            "END OF STANDARD REPORT",
            "=" * 78,
        ]
    )

    return "\n".join(lines) + "\n"


def budget_section(start_date: date, end_date: date) -> list[str] | None:
    """Brief budget block, or None when no budget covers the period."""
    import budget_core

    try:
        snapshot = budget_core.load_snapshot()
    except budget_core.BudgetNotAvailable:
        return None
    if not budget_core.coverage(snapshot, start_date, end_date).covered_days:
        return None
    result = budget_core.compute_variance(
        snapshot, start_date, end_date, final_dir(start_date, end_date)
    )
    return budget_core.brief_lines(result)


def main() -> int:
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)

    hierarchy_file = hierarchy_path(start_date, end_date)
    universe_file = universe_path(start_date, end_date)

    if not hierarchy_file.exists():
        raise FileNotFoundError(f"Missing hierarchy CSV: {hierarchy_file}")
    if not universe_file.exists():
        raise FileNotFoundError(f"Missing time-universe CSV: {universe_file}")

    output_dir = REPORT_ROOT / f"{start_date}_{end_date}"
    if output_dir.exists() and not args.force:
        raise FileExistsError(
            f"Report output already exists: {output_dir}. Use --force."
        )

    hierarchy = read_csv(hierarchy_file)
    rows = deepest_rows(hierarchy)
    if not rows:
        raise ValueError("No deepest hierarchy rows found.")

    capacity, corrected, unique, off_device, overlap = load_universe(
        universe_file
    )

    report = build_report(
        start_date,
        end_date,
        rows,
        capacity,
        corrected,
        unique,
        off_device,
        overlap,
        None if args.no_budget else budget_section(start_date, end_date),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    report_file = output_dir / (
        f"Standard_Report_{start_date}_{end_date}.txt"
    )
    report_file.write_text(report, encoding="utf-8")

    print("=== Standard Report ===")
    print(f"Dates              : {start_date} -> {end_date}")
    print(f"Capacity            : {duration(capacity)}")
    print(f"Unique tracked      : {duration(unique)}")
    print(f"Off-device life     : {duration(off_device)}")
    print(f"Tracked overlap     : {duration(overlap)}")
    print(f"Report              : {report_file}")
    print("RESULT: STANDARD REPORT GENERATED.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())