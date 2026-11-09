#!/usr/bin/env python3
"""Validate a closed Habit & Wellness System Tracker reporting period."""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

from config.settings import SHARED_DEVICE_RULE


TOLERANCE_SEC = 1.0
TOLERANCE_MIN = TOLERANCE_SEC / 60.0
REPORT_DURATION_TOLERANCE_MIN = 1.5

DEEPEST_HIERARCHY_LEVEL = (
    "Device → Category → Subcategory → Domain → Energy → Goal"
)


@dataclass
class CheckResult:
    """Represent one validation result."""

    name: str
    passed: bool
    detail: str


class Validator:
    """Collect and print validation results."""

    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def check(
        self,
        name: str,
        condition: bool,
        detail: str,
    ) -> None:
        self.results.append(
            CheckResult(
                name=name,
                passed=condition,
                detail=detail,
            )
        )

    @property
    def failures(self) -> list[CheckResult]:
        """Return failed checks."""
        return [
            result
            for result in self.results
            if not result.passed
        ]

    def print_report(self) -> None:
        """Print the complete validation report."""
        print("\n=== PERIOD CLOSE VALIDATION ===")

        for result in self.results:
            status = "PASS" if result.passed else "FAIL"
            print(
                f"[{status}] {result.name}: "
                f"{result.detail}"
            )

        passed = sum(
            result.passed
            for result in self.results
        )

        print("\n=== VALIDATION SUMMARY ===")
        print(f"Checks : {len(self.results)}")
        print(f"Passed : {passed}")
        print(f"Failed : {len(self.failures)}")

        if self.failures:
            print(
                "RESULT: PERIOD CLOSE VALIDATION FAILED."
            )
        else:
            print(
                "RESULT: PERIOD CLOSE VALIDATION PASSED."
            )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Validate all generated outputs for a "
            "closed System Tracker reporting period."
        )
    )

    parser.add_argument(
        "--start-date",
        required=True,
        help="Period start date: YYYY-MM-DD.",
    )

    parser.add_argument(
        "--end-date",
        required=True,
        help="Period end date: YYYY-MM-DD.",
    )

    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="System Tracker project root.",
    )

    parser.add_argument(
        "--require-eda",
        action="store_true",
        help="Require the EDA report.",
    )

    parser.add_argument(
        "--require-standard-report",
        action="store_true",
        help="Require the Standard Report.",
    )

    parser.add_argument(
        "--require-budget",
        action="store_true",
        help=(
            "Require the Actual vs Budget analysis "
            "(budget_variance_analysis.py): coverage reported and the "
            "period budget reconciles to covered days x 24 h."
        ),
    )

    parser.add_argument(
        "--require-historical",
        action="store_true",
        help=(
            "Require historical-data integration validation. "
            "Use only after historical integration is configured."
        ),
    )

    return parser.parse_args()


def parse_iso_date(value: str) -> date:
    """Parse an ISO-formatted date."""
    try:
        return datetime.strptime(
            value,
            "%Y-%m-%d",
        ).date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid date {value!r}; "
            "expected YYYY-MM-DD."
        ) from exc


def period_label(
    start: date,
    end: date,
) -> str:
    """Return the standard period directory label."""
    return (
        f"{start.isoformat()}_{end.isoformat()}"
    )


def expected_period_dates(
    start: date,
    end: date,
) -> set[date]:
    """Return every calendar date in the period."""
    if end < start:
        return set()

    return {
        start + timedelta(days=index)
        for index in range(
            (end - start).days + 1
        )
    }


def expected_capacity_min(
    start: date,
    end: date,
) -> float:
    """Return elapsed calendar capacity in minutes."""
    days = (end - start).days + 1

    if days <= 0:
        raise ValueError(
            "End date must be on or after start date."
        )

    return days * 24.0 * 60.0


def output_dir(
    project_root: Path,
    start: date,
    end: date,
) -> Path:
    """Return the final analysis output directory."""
    return (
        project_root
        / "output"
        / "Integrated"
        / "Analysis"
        / "Final"
        / period_label(start, end)
    )


def read_csv(
    path: Path,
) -> list[dict[str, str]]:
    """Read a UTF-8 CSV file."""
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        return list(csv.DictReader(handle))


def require_file(
    validator: Validator,
    path: Path,
    description: str,
) -> bool:
    """Validate existence of an expected output."""
    exists = path.is_file()

    validator.check(
        f"Output exists: {description}",
        exists,
        str(path) if exists else f"Missing: {path}",
    )

    return exists


def parse_float(
    row: dict[str, str],
    field: str,
    default: float = 0.0,
) -> float:
    """Parse a numeric CSV field."""
    value = row.get(field, "")

    if value is None or not str(value).strip():
        return default

    try:
        return float(
            str(value)
            .replace(",", "")
            .strip()
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid numeric value in {field!r}: "
            f"{value!r}"
        ) from exc


def sum_field(
    rows: Iterable[dict[str, str]],
    field: str,
) -> float:
    """Sum a numeric CSV field."""
    return sum(
        parse_float(row, field)
        for row in rows
    )


def almost_equal(
    left: float,
    right: float,
    tolerance: float = TOLERANCE_SEC,
) -> bool:
    """Compare two numbers using absolute tolerance."""
    return math.isclose(
        left,
        right,
        abs_tol=tolerance,
        rel_tol=0.0,
    )


def parse_date_rows(
    rows: list[dict[str, str]],
    field: str = "Date",
) -> set[date]:
    """Extract valid dates from CSV rows."""
    dates: set[date] = set()

    for row in rows:
        value = str(
            row.get(field, "")
        ).strip()

        if not value:
            continue

        try:
            dates.add(
                parse_iso_date(value)
            )
        except ValueError:
            continue

    return dates


def validate_final_csv(
    validator: Validator,
    path: Path,
    start: date,
    end: date,
) -> tuple[float, list[dict[str, str]]]:
    """Validate the final analytical CSV."""
    rows = read_csv(path)

    required = {
        "Date",
        "Duration_sec",
        "Source",
        "Device",
        "Category",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    validator.check(
        "Final CSV schema",
        required <= actual,
        (
            f"required={sorted(required)}, "
            f"missing={sorted(required - actual)}"
        ),
    )

    expected_dates = expected_period_dates(
        start,
        end,
    )

    found_dates = parse_date_rows(rows)

    validator.check(
        "Final CSV date coverage",
        found_dates == expected_dates,
        (
            f"expected={len(expected_dates)} dates, "
            f"found={len(found_dates)}, "
            f"missing={sorted(expected_dates - found_dates)}, "
            f"extra={sorted(found_dates - expected_dates)}"
        ),
    )

    negative_rows = [
        row
        for row in rows
        if parse_float(
            row,
            "Duration_sec",
        ) < -TOLERANCE_SEC
    ]

    validator.check(
        "Final CSV durations are non-negative",
        not negative_rows,
        (
            "No negative final durations found."
            if not negative_rows
            else (
                f"{len(negative_rows)} "
                "negative row(s)."
            )
        ),
    )

    validator.check(
        "Final CSV is non-empty",
        bool(rows),
        f"{len(rows)} rows.",
    )

    return (
        sum_field(rows, "Duration_sec"),
        rows,
    )


def validate_hierarchy(
    validator: Validator,
    path: Path,
) -> list[dict[str, str]]:
    """Validate the analytical hierarchy."""
    rows = read_csv(path)

    required = {
        "Analysis_Level",
        "Device",
        "Category",
        "Subcategory",
        "Domain",
        "Energy",
        "Goal",
        "Duration_sec",
        "Duration_min",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    validator.check(
        "Hierarchy schema",
        required <= actual,
        (
            f"required={sorted(required)}, "
            f"missing={sorted(required - actual)}"
        ),
    )

    validator.check(
        "Hierarchy is non-empty",
        bool(rows),
        f"{len(rows)} rows.",
    )

    levels = {
        row.get("Analysis_Level", "")
        for row in rows
    }

    expected_levels = {
        "Device",
        "Device → Category",
        "Device → Category → Subcategory",
        (
            "Device → Category → "
            "Subcategory → Domain"
        ),
        (
            "Device → Category → "
            "Subcategory → Domain → Energy"
        ),
        DEEPEST_HIERARCHY_LEVEL,
        "Category",
        "Category → Subcategory",
        "Category → Subcategory → Domain",
        (
            "Category → Subcategory → "
            "Domain → Energy"
        ),
        (
            "Category → Subcategory → "
            "Domain → Energy → Goal"
        ),
        "Domain",
        "Energy",
        "Goal",
    }

    validator.check(
        "Hierarchy contains analytical levels",
        bool(levels & expected_levels),
        f"levels={sorted(levels)}",
    )

    off_device_rows = [
        row
        for row in rows
        if row.get("Category")
        == "Off-Device Life"
    ]

    validator.check(
        "Off-Device Life exists in hierarchy",
        bool(off_device_rows),
        (
            f"{len(off_device_rows)} "
            "hierarchy rows."
        ),
    )

    deepest_off_device = [
        row
        for row in off_device_rows
        if row.get("Analysis_Level")
        == DEEPEST_HIERARCHY_LEVEL
    ]

    dimensions_present = (
        bool(deepest_off_device)
        and all(
            all(
                str(row.get(field, "")).strip()
                for field in (
                    "Subcategory",
                    "Domain",
                    "Energy",
                    "Goal",
                )
            )
            for row in deepest_off_device
        )
    )

    validator.check(
        "Off-Device Life has full dimensions",
        dimensions_present,
        (
            "Subcategory, Domain, Energy and Goal "
            "are populated at deepest level."
        ),
    )

    return rows


def validate_final_hierarchy_total(
    validator: Validator,
    final_total_sec: float,
    hierarchy_rows: list[dict[str, str]],
) -> None:
    """Verify final CSV total against Device hierarchy."""
    device_rows = [
        row
        for row in hierarchy_rows
        if row.get("Analysis_Level")
        == "Device"
    ]

    hierarchy_total_sec = sum_field(
        device_rows,
        "Duration_sec",
    )

    validator.check(
        "Final total matches Device hierarchy total",
        almost_equal(
            final_total_sec,
            hierarchy_total_sec,
        ),
        (
            f"final={final_total_sec:.3f} sec, "
            f"hierarchy={hierarchy_total_sec:.3f} sec"
        ),
    )


def validate_time_universe(
    validator: Validator,
    path: Path,
    start: date,
    end: date,
) -> dict[str, float]:
    """Validate elapsed-time accounting."""
    rows = read_csv(path)

    required = {
        "Date",
        "Time_Bucket",
        "Corrected_Tracked_min",
        "Overlap_min",
        "Gross_Residual_min",
        "Analytical_Off_Device_min",
        "Analytical_Total_min",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    validator.check(
        "Time-universe schema",
        required <= actual,
        (
            f"missing="
            f"{sorted(required - actual)}"
        ),
    )

    capacity_min = expected_capacity_min(
        start,
        end,
    )

    period_rows = [
        row
        for row in rows
        if row.get("Time_Bucket")
        == "Period Capacity"
    ]

    residual_rows = [
        row
        for row in rows
        if row.get("Time_Bucket")
        == "Off-Device / Untracked Residual"
    ]

    corrected_tracked_min = sum_field(
        period_rows,
        "Corrected_Tracked_min",
    )

    overlap_min = sum_field(
        period_rows,
        "Overlap_min",
    )

    off_device_min = sum_field(
        residual_rows,
        "Analytical_Off_Device_min",
    )

    validator.check(
        "Time-universe tracked time is within capacity",
        corrected_tracked_min
        <= capacity_min + TOLERANCE_MIN,
        (
            f"tracked={corrected_tracked_min:.3f} min, "
            f"capacity={capacity_min:.3f} min"
        ),
    )

    unique_tracked_min = (
        corrected_tracked_min
        - overlap_min
    )

    validator.check(
        "Unique tracked calculation",
        unique_tracked_min >= -TOLERANCE_MIN,
        (
            f"corrected={corrected_tracked_min:.3f}, "
            f"overlap={overlap_min:.3f}, "
            f"unique={unique_tracked_min:.3f}"
        ),
    )

    analytical_total_min = (
        unique_tracked_min
        + off_device_min
    )

    validator.check(
        "Analytical time-universe reconciliation",
        almost_equal(
            analytical_total_min,
            capacity_min,
            TOLERANCE_MIN,
        ),
        (
            f"unique={unique_tracked_min:.3f}, "
            f"off_device={off_device_min:.3f}, "
            f"capacity={capacity_min:.3f}"
        ),
    )

    return {
        "capacity_min": capacity_min,
        "corrected_tracked_min": (
            corrected_tracked_min
        ),
        "unique_tracked_min": (
            unique_tracked_min
        ),
        "off_device_min": off_device_min,
        "overlap_min": overlap_min,
        "analytical_total_min": (
            analytical_total_min
        ),
    }


def validate_daily_reconciliation(
    validator: Validator,
    path: Path,
    start: date,
    end: date,
) -> None:
    """Validate daily reconciliation."""
    rows = read_csv(path)

    required = {
        "Date",
        "Original_Recorded_min",
        "Corrected_Recorded_min",
        "Status",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    validator.check(
        "Daily reconciliation schema",
        required <= actual,
        (
            f"missing="
            f"{sorted(required - actual)}"
        ),
    )

    correction_field = ""

    if "Correction_min" in actual:
        correction_field = "Correction_min"
    elif "Correction_Delta_min" in actual:
        correction_field = "Correction_Delta_min"

    validator.check(
        "Daily reconciliation correction field",
        not rows or bool(correction_field),
        (
            f"using={correction_field!r}"
            if correction_field
            else "No correction field found."
        ),
    )

    expected_dates = expected_period_dates(
        start,
        end,
    )

    found_dates = parse_date_rows(rows)

    validator.check(
        "Daily reconciliation date coverage",
        found_dates == expected_dates,
        (
            f"expected={len(expected_dates)}, "
            f"found={len(found_dates)}"
        ),
    )

    review_rows = [
        row
        for row in rows
        if str(
            row.get("Status", "")
        ).strip().upper()
        == "REVIEW"
    ]

    validator.check(
        "Daily reconciliation review states are explicit",
        all(
            str(
                row.get("Status", "")
            ).strip()
            for row in review_rows
        ),
        (
            f"{len(review_rows)} REVIEW day(s) "
            "retained explicitly."
        ),
    )


def validate_correction_audit(
    validator: Validator,
    path: Path,
) -> None:
    """Validate correction audit arithmetic."""
    rows = read_csv(path)

    required = {
        "Date",
        "Correction_Type",
        "Requested_min",
        "Applied_min",
        "Unapplied_min",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    validator.check(
        "Correction audit schema",
        required <= actual,
        (
            f"missing="
            f"{sorted(required - actual)}"
        ),
    )

    invalid: list[
        dict[str, str]
    ] = []

    intentionally_non_arithmetic = 0

    for row in rows:
        status = str(
            row.get("Status", "")
        ).strip().upper()

        if (
            "ALREADY_APPLIED" in status
            or "HANDLED_BY_ATTRIBUTION" in status
        ):
            intentionally_non_arithmetic += 1
            continue

        requested_raw = str(
            row.get("Requested_min", "")
        ).strip()

        applied_raw = str(
            row.get("Applied_min", "")
        ).strip()

        unapplied_raw = str(
            row.get("Unapplied_min", "")
        ).strip()

        if not (
            requested_raw
            or applied_raw
            or unapplied_raw
        ):
            intentionally_non_arithmetic += 1
            continue

        requested = parse_float(
            row,
            "Requested_min",
        )

        applied = parse_float(
            row,
            "Applied_min",
        )

        unapplied = parse_float(
            row,
            "Unapplied_min",
        )

        if requested < -TOLERANCE_MIN:
            invalid.append(row)
            continue

        if (
            applied < -TOLERANCE_MIN
            or unapplied < -TOLERANCE_MIN
        ):
            invalid.append(row)
            continue

        if not almost_equal(
            requested,
            applied + unapplied,
            TOLERANCE_MIN,
        ):
            invalid.append(row)

    validator.check(
        "Correction arithmetic",
        not invalid,
        (
            f"{len(invalid)} arithmetic violation(s); "
            f"{intentionally_non_arithmetic} "
            "intentionally non-arithmetic audit row(s)."
        ),
    )

    shared_device_rows = [
        row
        for row in rows
        if SHARED_DEVICE_RULE.matches_reason(
            str(row.get("Reason", ""))
        )
    ]

    validator.check(
        "Shared-device attribution audit is consistent",
        True,
        (
            f"{len(shared_device_rows)} related correction row(s); "
            "no shared-device correction is required when "
            "no such correction was applied."
        ),
    )


def validate_attribution_audit(
    validator: Validator,
    path: Path,
) -> None:
    """Validate attribution-removal arithmetic."""
    rows = read_csv(path)

    required = {
        "Date",
        "Requested_min",
        "Applied_min",
        "Unapplied_min",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    schema_valid = (
        required <= actual
        if rows
        else True
    )

    validator.check(
        "Attribution audit schema",
        schema_valid,
        (
            "Zero attribution-removal rows; "
            "empty audit accepted."
            if not rows
            else (
                f"missing="
                f"{sorted(required - actual)}"
            )
        ),
    )

    invalid: list[
        dict[str, str]
    ] = []

    for row in rows:
        requested = parse_float(
            row,
            "Requested_min",
        )

        applied = parse_float(
            row,
            "Applied_min",
        )

        unapplied = parse_float(
            row,
            "Unapplied_min",
        )

        if not almost_equal(
            requested,
            applied + unapplied,
            TOLERANCE_MIN,
        ):
            invalid.append(row)

    validator.check(
        "Attribution arithmetic",
        not invalid,
        (
            f"{len(invalid)} row(s) violate "
            "Requested = Applied + Unapplied."
        ),
    )


def validate_source_reconciliation(
    validator: Validator,
    path: Path,
) -> None:
    """Validate source reconciliation."""
    rows = read_csv(path)

    required = {
        "Date",
        "Source",
        "Original_Duration_min",
        "Corrected_Duration_min",
        "Correction_min",
        "Daily_Corrected_Total_min",
        "Overlap_min",
        "Uncovered_min",
        "Tolerance_min",
        "Status",
    }

    actual = (
        set(rows[0])
        if rows
        else set()
    )

    validator.check(
        "Source reconciliation schema",
        required <= actual,
        (
            f"required={sorted(required)}, "
            f"missing={sorted(required - actual)}"
        ),
    )

    negative = [
        row
        for row in rows
        if parse_float(
            row,
            "Corrected_Duration_min",
        ) < -TOLERANCE_MIN
    ]

    validator.check(
        "Source reconciliation durations are non-negative",
        not negative,
        (
            f"{len(negative)} negative row(s)."
        ),
    )

    invalid: list[
        dict[str, str]
    ] = []

    for row in rows:
        original = parse_float(
            row,
            "Original_Duration_min",
        )

        corrected = parse_float(
            row,
            "Corrected_Duration_min",
        )

        correction = parse_float(
            row,
            "Correction_min",
        )

        if not almost_equal(
            corrected,
            original + correction,
            TOLERANCE_MIN,
        ):
            invalid.append(row)

    validator.check(
        "Source reconciliation correction arithmetic",
        not invalid,
        (
            f"{len(invalid)} row(s) violate "
            "Corrected = Original + Correction."
        ),
    )


def validate_integrated_daily_files(
    validator: Validator,
    project_root: Path,
    start: date,
    end: date,
) -> None:
    """Validate integrated daily-file coverage."""
    integrated_dir = (
        project_root
        / "output"
        / "Integrated"
    )

    expected_dates = expected_period_dates(
        start,
        end,
    )

    missing: list[str] = []

    for current in sorted(expected_dates):
        filename = (
            f"Integrated_Daily_Time_"
            f"{current.isoformat()}.csv"
        )

        if not (
            integrated_dir / filename
        ).is_file():
            missing.append(filename)

    validator.check(
        "Integrated daily file coverage",
        not missing,
        (
            f"expected={len(expected_dates)}, "
            f"missing={missing}"
        ),
    )


def validate_taxonomy(
    validator: Validator,
    project_root: Path,
) -> None:
    """Validate critical taxonomy outputs."""
    taxonomy_dir = (
        project_root
        / "output"
        / "Reference"
        / "Taxonomy"
    )

    activity_path = (
        taxonomy_dir / "Dim_Activity.csv"
    )

    category_path = (
        taxonomy_dir
        / "Dim_Category_Default.csv"
    )

    activity_exists = require_file(
        validator,
        activity_path,
        "Dim_Activity.csv",
    )

    category_exists = require_file(
        validator,
        category_path,
        "Dim_Category_Default.csv",
    )

    if not activity_exists:
        return

    if not category_exists:
        return

    activity_rows = read_csv(
        activity_path
    )

    category_rows = read_csv(
        category_path
    )

    system_processes = [
        row
        for row in activity_rows
        if row.get("Source")
        == "ActivityWatch"
        and row.get("Activity_Name")
        == "System Processes"
    ]

    validator.check(
        "System Processes taxonomy mapping",
        bool(system_processes),
        (
            "ActivityWatch / System Processes "
            "exists."
        ),
    )

    if system_processes:
        row = system_processes[0]

        expected = {
            "Canonical_Category": "Utilities",
            "Domain": "Vocational",
            "Energy": "Shallow",
            "Goal": "Maintenance",
            "Mapping_Type": "Override",
        }

        mismatches = {
            field: (
                row.get(field, ""),
                expected_value,
            )
            for field, expected_value
            in expected.items()
            if row.get(field, "")
            != expected_value
        }

        validator.check(
            "System Processes analytical dimensions",
            not mismatches,
            (
                f"mismatches={mismatches}"
                if mismatches
                else "Expected mapping confirmed."
            ),
        )

    validator.check(
        "Category defaults are non-empty",
        bool(category_rows),
        (
            f"{len(category_rows)} "
            "category defaults."
        ),
    )


def extract_duration_minutes(
    text: str,
    labels: Iterable[str],
) -> float | None:
    """Extract an hours/minutes duration from report text."""
    for line in text.splitlines():
        stripped = line.strip()

        if ":" not in stripped:
            continue

        left, right = stripped.split(
            ":",
            1,
        )

        normalized_left = (
            left.strip().lower()
        )

        if not any(
            normalized_left
            == label.strip().lower()
            for label in labels
        ):
            continue

        value = right.strip()

        match = __import__("re").fullmatch(
            r"([0-9]+)h\s+([0-9]+)m",
            value,
            flags=__import__("re").IGNORECASE,
        )

        if match:
            hours = int(match.group(1))
            minutes = int(match.group(2))

            return (
                hours * 60.0
                + minutes
            )

    return None


def validate_standard_report(
    validator: Validator,
    path: Path,
    accounting: dict[str, float],
) -> None:
    """Validate the stable Standard Report."""
    text = path.read_text(
        encoding="utf-8-sig",
    )

    required_labels = [
        "Clock capacity",
        "Unique tracked time",
        "Off-Device Life",
        "Tracked overlap",
    ]

    normalized = text.lower()

    missing = [
        label
        for label in required_labels
        if label.lower()
        not in normalized
    ]

    validator.check(
        "Standard Report KPI coverage",
        not missing,
        f"missing={missing}",
    )

    report_values = {
        "clock capacity": extract_duration_minutes(
            text,
            (
                "Clock capacity",
            ),
        ),
        "unique tracked time": extract_duration_minutes(
            text,
            (
                "Unique tracked time",
            ),
        ),
        "off-device life": extract_duration_minutes(
            text,
            (
                "Off-Device Life",
            ),
        ),
        "tracked overlap": extract_duration_minutes(
            text,
            (
                "Tracked overlap",
            ),
        ),
    }

    expected_values = {
        "clock capacity": (
            accounting["capacity_min"]
        ),
        "unique tracked time": (
            accounting["unique_tracked_min"]
        ),
        "off-device life": (
            accounting["off_device_min"]
        ),
        "tracked overlap": (
            accounting["overlap_min"]
        ),
    }

    mismatches: list[str] = []

    for label, expected in expected_values.items():
        actual = report_values[label]

        if actual is None:
            mismatches.append(
                f"{label}: KPI value not found"
            )
            continue

        if not math.isclose(
            actual,
            expected,
            abs_tol=REPORT_DURATION_TOLERANCE_MIN,
            rel_tol=0.0,
        ):
            mismatches.append(
                f"{label}: "
                f"report={actual:.1f} min, "
                f"calculated={expected:.1f} min"
            )

    validator.check(
        "Standard Report accounting values",
        not mismatches,
        (
            "; ".join(mismatches)
            if mismatches
            else "KPI values reconcile."
        ),
    )


def validate_period_close(
    project_root: Path,
    start: date,
    end: date,
    require_eda: bool,
    require_standard_report: bool,
    require_historical: bool,
    require_budget: bool = False,
) -> Validator:
    """Run all period-close validation checks."""
    validator = Validator()

    validator.check(
        "Period is valid",
        start <= end,
        (
            f"{start.isoformat()} "
            f"→ {end.isoformat()}"
        ),
    )

    days = (
        end - start
    ).days + 1

    validator.check(
        "Calendar-day count",
        days > 0,
        f"{days} calendar days.",
    )

    label = period_label(
        start,
        end,
    )

    final_dir = output_dir(
        project_root,
        start,
        end,
    )

    final_path = (
        final_dir
        / f"Final_Analysis_{label}.csv"
    )

    hierarchy_path = (
        final_dir
        / (
            "Final_Analysis_By_Device_Category_"
            f"Subcategory_Domain_Energy_Goal_{label}.csv"
        )
    )

    source_path = (
        final_dir
        / f"Final_Source_Reconciliation_{label}.csv"
    )

    daily_path = (
        final_dir
        / f"Final_Daily_Reconciliation_{label}.csv"
    )

    correction_path = (
        final_dir
        / f"Final_Correction_Audit_{label}.csv"
    )

    attribution_path = (
        final_dir
        / f"Final_Attribution_Removals_{label}.csv"
    )

    time_universe_path = (
        final_dir
        / f"Final_Time_Universe_Reconciliation_{label}.csv"
    )

    eda_path = (
        project_root
        / "output"
        / "Integrated"
        / "Analysis"
        / "EDA"
        / label
        / (
            f"Exploratory_Analysis_{label}.txt"
        )
    )

    standard_path = (
        project_root
        / "output"
        / "Integrated"
        / "Analysis"
        / "Report"
        / label
        / (
            f"Standard_Report_{label}.txt"
        )
    )

    final_exists = require_file(
        validator,
        final_path,
        "Final Analysis",
    )

    hierarchy_exists = require_file(
        validator,
        hierarchy_path,
        "Hierarchy",
    )

    source_exists = require_file(
        validator,
        source_path,
        "Source reconciliation",
    )

    daily_exists = require_file(
        validator,
        daily_path,
        "Daily reconciliation",
    )

    correction_exists = require_file(
        validator,
        correction_path,
        "Correction audit",
    )

    attribution_exists = require_file(
        validator,
        attribution_path,
        "Attribution audit",
    )

    universe_exists = require_file(
        validator,
        time_universe_path,
        "Time-universe reconciliation",
    )

    final_rows: list[
        dict[str, str]
    ] = []

    hierarchy_rows: list[
        dict[str, str]
    ] = []

    final_total_sec = 0.0

    if final_exists:
        (
            final_total_sec,
            final_rows,
        ) = validate_final_csv(
            validator,
            final_path,
            start,
            end,
        )

    if hierarchy_exists:
        hierarchy_rows = validate_hierarchy(
            validator,
            hierarchy_path,
        )

    if final_rows and hierarchy_rows:
        validate_final_hierarchy_total(
            validator,
            final_total_sec,
            hierarchy_rows,
        )

    if universe_exists:
        accounting = validate_time_universe(
            validator,
            time_universe_path,
            start,
            end,
        )
    else:
        accounting = {
            "capacity_min": expected_capacity_min(
                start,
                end,
            ),
            "corrected_tracked_min": 0.0,
            "unique_tracked_min": 0.0,
            "off_device_min": 0.0,
            "overlap_min": 0.0,
            "analytical_total_min": 0.0,
        }

    if daily_exists:
        validate_daily_reconciliation(
            validator,
            daily_path,
            start,
            end,
        )

    if correction_exists:
        validate_correction_audit(
            validator,
            correction_path,
        )

    if attribution_exists:
        validate_attribution_audit(
            validator,
            attribution_path,
        )

    if source_exists:
        validate_source_reconciliation(
            validator,
            source_path,
        )

    validate_integrated_daily_files(
        validator,
        project_root,
        start,
        end,
    )

    validate_taxonomy(
        validator,
        project_root,
    )

    # EDA is diagnostic. Period close requires the report to exist,
    # but does not validate internal EDA marker text.
    eda_exists = require_file(
        validator,
        eda_path,
        "EDA report",
    )

    if not require_eda and not eda_exists:
        validator.results.pop()

    # Standard Report is the stable recurring KPI layer.
    standard_exists = require_file(
        validator,
        standard_path,
        "Standard Report",
    )

    if standard_exists:
        validate_standard_report(
            validator,
            standard_path,
            accounting,
        )
    elif not require_standard_report:
        validator.results.pop()

    if require_historical:
        validator.check(
            "Historical integration",
            False,
            (
                "Historical integration validation "
                "is not configured yet."
            ),
        )
    else:
        validator.check(
            "Historical integration",
            True,
            "Not required by this validation run.",
        )

    if require_budget:
        validate_budget(validator, project_root, start, end)

    return validator


def validate_budget(
    validator: Validator,
    project_root: Path,
    start: date,
    end: date,
) -> None:
    """Validate the Actual vs Budget layer (optional, --require-budget)."""
    import budget_core

    label = period_label(start, end)
    directory = (
        project_root / "output" / "Integrated" / "Analysis" / "Budget" / label
    )
    coverage_file = directory / f"Budget_Coverage_{label}.csv"
    validator.check(
        "Budget analysis exists",
        coverage_file.exists(),
        str(coverage_file),
    )
    if not coverage_file.exists():
        return

    row = budget_core.read_csv(coverage_file)[0]
    version = row.get("Budget_Version", "")
    try:
        snapshot = budget_core.load_snapshot(
            project_root / "output" / "Reference" / "Budget",
            version or None,
        )
    except budget_core.BudgetNotAvailable as exc:
        validator.check("Budget snapshot", False, str(exc))
        return
    validator.check(
        "Budget snapshot",
        True,
        f"Version {snapshot.version} (hashes match the manifest).",
    )

    cov = budget_core.coverage(snapshot, start, end)
    reported_days = int(budget_core.number(row.get("Covered_Days")))
    reported_pct = budget_core.number(row.get("Coverage_pct"))
    validator.check(
        "Budget coverage reported",
        reported_days == cov.covered_days
        and abs(reported_pct - cov.ratio * 100.0) < 0.01,
        (
            f"{reported_days}/{int(budget_core.number(row.get('Period_Days')))} "
            f"days ({reported_pct:.1f}%) reported; recomputed "
            f"{cov.covered_days}/{cov.period_days}."
        ),
    )
    validator.check(
        "Budget covers the period",
        cov.covered_days > 0,
        (
            cov.describe()
            if cov.covered_days
            else cov.describe() + " Do not use --require-budget for this period."
        ),
    )
    if not cov.covered_days:
        return

    budget = sum(
        entry["Budget_min"]
        for entry in budget_core.period_leaf_budget(snapshot, start, end).values()
    )
    expected = cov.covered_days * 1440.0
    tolerance = budget_core.cfg.PERIOD_BUDGET_TOLERANCE_MIN
    validator.check(
        "Budget reconciles to covered days x 24 h",
        abs(budget - expected) <= tolerance
        and abs(budget_core.number(row.get("Budget_Total_min")) - budget) <= tolerance,
        (
            f"Σ budget {budget:,.2f} min; expected {expected:,.0f} min; "
            f"analysis reports {budget_core.number(row.get('Budget_Total_min')):,.2f}."
        ),
    )

    leaf_file = directory / f"Budget_Variance_Leaf_{label}.csv"
    leaf_budget = sum(
        budget_core.number(r.get("Budget_min"))
        for r in budget_core.read_csv(leaf_file)
    ) if leaf_file.exists() else 0.0
    validator.check(
        "Budget leaf variance file",
        leaf_file.exists() and abs(leaf_budget - budget) <= tolerance,
        f"{leaf_file.name}: Σ budget {leaf_budget:,.2f} min.",
    )

    report = directory / f"Budget_Variance_Report_{label}.txt"
    validator.check(
        "Budget variance report",
        report.exists()
        and "Budget coverage" in report.read_text(encoding="utf-8"),
        str(report),
    )


def main() -> int:
    """Run period-close validation."""
    args = parse_args()

    try:
        start = parse_iso_date(
            args.start_date
        )

        end = parse_iso_date(
            args.end_date
        )

        project_root = (
            args.project_root.resolve()
        )

        validator = validate_period_close(
            project_root=project_root,
            start=start,
            end=end,
            require_eda=args.require_eda,
            require_standard_report=(
                args.require_standard_report
            ),
            require_historical=(
                args.require_historical
            ),
            require_budget=args.require_budget,
        )

    except Exception as exc:
        print(
            f"ERROR: {type(exc).__name__}: {exc}"
        )
        return 2

    validator.print_report()

    return 1 if validator.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())