# FILE: final_analysis.py

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from config.settings import SHARED_DEVICE_RULE


PROJECT_ROOT = Path(__file__).resolve().parent

INTEGRATED_ROOT = (
    PROJECT_ROOT
    / "output"
    / "Integrated"
)

TAXONOMY_ROOT = (
    PROJECT_ROOT
    / "output"
    / "Reference"
    / "Taxonomy"
)

HABIT_ADJUSTMENTS_FILE = (
    PROJECT_ROOT
    / "input"
    / "Integrated"
    / "Habit_Manual_Adjustments.csv"
)

CLASSIFICATION_REMOVALS_FILE = (
    INTEGRATED_ROOT
    / "Analysis"
    / "Human_Review"
    / "Integration_Classification_Removals.csv"
)

ACTIVITY_TAXONOMY_FILE = (
    TAXONOMY_ROOT
    / "Dim_Activity.csv"
)

CATEGORY_TAXONOMY_FILE = (
    TAXONOMY_ROOT
    / "Dim_Category_Default.csv"
)

DAY_MINUTES = 1440.0
DAY_SECONDS = DAY_MINUTES * 60.0

# A day is flagged REVIEW only when uncovered (or overlap) time exceeds 4 h.
RECONCILIATION_TOLERANCE_MIN = 240.0

UNCATEGORIZED_CATEGORY = (
    "Uncategorized"
)

ACTIVITYWATCH_SOURCE = (
    "ActivityWatch"
)

APPLE_SCREEN_TIME_SOURCE = (
    "AppleScreenTime"
)

HABIT_SOURCE = "Habit"


@dataclass(frozen=True)
class Record:
    date: date
    source: str
    device: str
    activity: str
    category: str
    subcategory: str
    duration_sec: float
    allocation_type: str


@dataclass(frozen=True)
class HabitAdjustment:
    date: date
    category: str
    adjustment_sec: float
    reason: str


@dataclass(frozen=True)
class ClassificationRemoval:
    date: date
    habit_category: str
    analytical_category: str
    analytical_subcategory: str
    adjustment_sec: float
    removal_sec: float
    reason: str


@dataclass(frozen=True)
class TaxonomyMapping:
    category: str
    domain: str
    energy: str
    goal: str
    mapping_type: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the final corrected consolidated "
            "analysis for the selected date range."
        )
    )

    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date in YYYY-MM-DD format.",
    )

    parser.add_argument(
        "--end-date",
        required=True,
        help="End date in YYYY-MM-DD format.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing final outputs.",
    )

    return parser.parse_args()


def parse_date(value: str) -> date:
    value = str(value).strip()

    if not value:
        raise ValueError(
            "Date cannot be empty."
        )

    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt,
            ).date()
        except ValueError:
            continue

    raise ValueError(
        f"Invalid date: {value!r}"
    )


def clean_value(
    value: object,
) -> str:
    if value is None:
        return ""

    return str(value).strip()


def clean_row(
    row: dict[str, object],
) -> dict[str, str]:
    return {
        clean_value(key): clean_value(value)
        for key, value in row.items()
        if key is not None
    }


def is_comment_row(
    row: dict[str, str],
) -> bool:
    for value in row.values():
        if value.lstrip().startswith("#"):
            return True

    return False


def read_csv(
    path: Path,
) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)

        return [
            clean_row(row)
            for row in reader
        ]


def require_columns(
    path: Path,
    rows: list[dict[str, str]],
    required: set[str],
) -> None:
    if not rows:
        return

    available = set(rows[0])

    missing = required - available

    if missing:
        raise ValueError(
            f"{path} is missing columns: "
            f"{', '.join(sorted(missing))}"
        )


def parse_float(
    value: str,
    field_name: str,
    context: str,
) -> float:
    try:
        return float(
            value.strip()
        )
    except ValueError as exc:
        raise ValueError(
            f"Invalid {field_name}={value!r} "
            f"{context}"
        ) from exc


def discover_integrated_files(
    start_date: date,
    end_date: date,
) -> list[Path]:
    files: list[Path] = []

    current = start_date

    while current <= end_date:
        path = (
            INTEGRATED_ROOT
            / (
                "Integrated_Daily_Time_"
                f"{current.isoformat()}.csv"
            )
        )

        if not path.exists():
            raise FileNotFoundError(
                f"Missing integrated daily file: {path}"
            )

        files.append(path)
        current += timedelta(days=1)

    return files


def load_integrated_records(
    files: list[Path],
    start_date: date,
    end_date: date,
) -> list[Record]:
    records: list[Record] = []

    for path in files:
        rows = read_csv(path)

        for line_number, row in enumerate(
            rows,
            start=2,
        ):
            if is_comment_row(row):
                continue

            raw_date = row.get("Date", "")

            if not raw_date:
                continue

            row_date = parse_date(
                raw_date
            )

            if not (
                start_date
                <= row_date
                <= end_date
            ):
                continue

            raw_duration = (
                row.get("Duration_sec")
                or row.get("Duration")
                or "0"
            )

            duration_sec = parse_float(
                raw_duration,
                "Duration_sec",
                (
                    f"in {path}, "
                    f"row {line_number}"
                ),
            )

            if duration_sec < 0:
                raise ValueError(
                    f"Negative duration in "
                    f"{path}, row {line_number}."
                )

            source = (
                row.get("Source")
                or row.get("Source_Name")
                or ""
            )

            # Integrated rows carry the physical device in Source_Device;
            # only ActivityWatch uses it so laptop and desktop stay distinct.
            device = (
                row.get("Device")
                or row.get("Machine")
                or (
                    row.get("Source_Device")
                    if source.lower()
                    == ACTIVITYWATCH_SOURCE.lower()
                    else ""
                )
                or source
            )

            category = (
                row.get("Category")
                or row.get("Canonical_Category")
                or ""
            )

            subcategory = (
                row.get("Subcategory")
                or row.get("Activity_Name")
                or category
            )

            activity = (
                row.get("Activity_Name")
                or row.get("App_or_Activity")
                or subcategory
                or category
            )

            allocation_type = (
                row.get("Allocation_Type")
                or "Observed"
            )

            records.append(
                Record(
                    date=row_date,
                    source=source,
                    device=device,
                    activity=activity,
                    category=category,
                    subcategory=subcategory,
                    duration_sec=duration_sec,
                    allocation_type=allocation_type,
                )
            )

    return records


def load_habit_adjustments(
    start_date: date,
    end_date: date,
) -> list[HabitAdjustment]:
    if not HABIT_ADJUSTMENTS_FILE.exists():
        return []

    rows = read_csv(
        HABIT_ADJUSTMENTS_FILE
    )

    require_columns(
        HABIT_ADJUSTMENTS_FILE,
        rows,
        {
            "Date",
            "Category",
            "Adjustment_min",
            "Reason",
        },
    )

    adjustments: list[
        HabitAdjustment
    ] = []

    for line_number, row in enumerate(
        rows,
        start=2,
    ):
        if is_comment_row(row):
            continue

        raw_date = row.get("Date", "")

        if not raw_date:
            continue

        row_date = parse_date(
            raw_date
        )

        if not (
            start_date
            <= row_date
            <= end_date
        ):
            continue

        category = (
            row.get("Category")
            or ""
        )

        raw_adjustment = (
            row.get("Adjustment_min")
            or ""
        )

        if not category:
            raise ValueError(
                "Missing Category on "
                f"row {line_number}."
            )

        if not raw_adjustment:
            raise ValueError(
                "Missing Adjustment_min on "
                f"row {line_number}."
            )

        adjustment_min = parse_float(
            raw_adjustment,
            "Adjustment_min",
            f"on row {line_number}",
        )

        reason = (
            row.get("Reason")
            or ""
        )

        adjustments.append(
            HabitAdjustment(
                date=row_date,
                category=category,
                adjustment_sec=(
                    adjustment_min * 60.0
                ),
                reason=reason,
            )
        )

    return adjustments


def load_classification_removals(
    start_date: date,
    end_date: date,
) -> list[ClassificationRemoval]:
    """
    Load the generated classification-removal audit.

    These rows are retained for audit visibility.

    They are NOT automatically applied as corrections because
    the Habit manual-adjustment pipeline already applied the
    corresponding correction to the analytical records.

    Shared-device attribution rows are therefore never double-applied.
    """
    if not CLASSIFICATION_REMOVALS_FILE.exists():
        return []

    rows = read_csv(
        CLASSIFICATION_REMOVALS_FILE
    )

    require_columns(
        CLASSIFICATION_REMOVALS_FILE,
        rows,
        {
            "Date",
            "Habit_Category",
            "Analytical_Category",
            "Analytical_Subcategory",
            "Adjustment_min",
            "Removal_min",
            "Reason",
        },
    )

    removals: list[
        ClassificationRemoval
    ] = []

    for line_number, row in enumerate(
        rows,
        start=2,
    ):
        if is_comment_row(row):
            continue

        raw_date = row.get("Date", "")

        if not raw_date:
            continue

        row_date = parse_date(
            raw_date
        )

        if not (
            start_date
            <= row_date
            <= end_date
        ):
            continue

        adjustment_min = parse_float(
            row.get(
                "Adjustment_min",
                "0",
            ),
            "Adjustment_min",
            f"on row {line_number}",
        )

        removal_min = parse_float(
            row.get(
                "Removal_min",
                "0",
            ),
            "Removal_min",
            f"on row {line_number}",
        )

        removals.append(
            ClassificationRemoval(
                date=row_date,
                habit_category=(
                    row.get(
                        "Habit_Category",
                        "",
                    )
                ),
                analytical_category=(
                    row.get(
                        "Analytical_Category",
                        "",
                    )
                ),
                analytical_subcategory=(
                    row.get(
                        "Analytical_Subcategory",
                        "",
                    )
                ),
                adjustment_sec=(
                    adjustment_min * 60.0
                ),
                removal_sec=(
                    abs(removal_min)
                    * 60.0
                ),
                reason=(
                    row.get(
                        "Reason",
                        "",
                    )
                ),
            )
        )

    return removals


def is_shared_device_adjustment(
    adjustment: HabitAdjustment,
) -> bool:
    """True when a negative Habit adjustment is a shared-device rule hit."""
    return (
        adjustment.category.strip().lower()
        == SHARED_DEVICE_RULE.habit_category.lower()
        and adjustment.adjustment_sec < 0
        and SHARED_DEVICE_RULE.matches_reason(
            adjustment.reason
        )
    )


def apply_habit_adjustments(
    records: list[Record],
    adjustments: list[HabitAdjustment],
) -> tuple[
    list[Record],
    list[dict[str, object]],
    list[HabitAdjustment],
]:
    """
    Apply legitimate Habit corrections.

    Shared-device corrections are excluded from direct Habit
    subtraction because the Habit record represents time of a
    secondary user, not of the tracked subject. Those corrections
    are redirected to the configured shared device / Uncategorized.
    """
    result = list(records)

    audit: list[
        dict[str, object]
    ] = []

    attribution_adjustments: list[
        HabitAdjustment
    ] = []

    for adjustment in adjustments:
        if is_shared_device_adjustment(
            adjustment
        ):
            attribution_adjustments.append(
                adjustment
            )

            audit.append(
                {
                    "Date": (
                        adjustment.date.isoformat()
                    ),
                    "Correction_Type": (
                        "ATTRIBUTION_REDIRECT"
                    ),
                    "Source": HABIT_SOURCE,
                    "Category": adjustment.category,
                    "Subcategory": "",
                    "Before_min": "",
                    "Requested_min": round(
                        abs(
                            adjustment.adjustment_sec
                        ) / 60.0,
                        3,
                    ),
                    "Direction": "REDIRECT",
                    "Applied_min": 0.0,
                    "Unapplied_min": round(
                        abs(
                            adjustment.adjustment_sec
                        ) / 60.0,
                        3,
                    ),
                    "After_min": "",
                    "Status": (
                        "REDIRECTED_TO_ACTIVITYWATCH"
                    ),
                    "Reason": adjustment.reason,
                }
            )

            continue

        candidates = [
            index
            for index, record in enumerate(result)
            if (
                record.date == adjustment.date
                and record.source.lower()
                == HABIT_SOURCE.lower()
                and record.category.lower()
                == adjustment.category.lower()
            )
        ]

        if not candidates:
            raise ValueError(
                "Habit adjustment has no matching "
                f"Habit record: "
                f"{adjustment.date} / "
                f"{adjustment.category}"
            )

        before = sum(
            result[index].duration_sec
            for index in candidates
        )

        requested = abs(
            adjustment.adjustment_sec
        )

        if adjustment.adjustment_sec >= 0:
            index = candidates[0]

            current = result[index]

            result[index] = Record(
                date=current.date,
                source=current.source,
                device=current.device,
                activity=current.activity,
                category=current.category,
                subcategory=current.subcategory,
                duration_sec=(
                    current.duration_sec
                    + adjustment.adjustment_sec
                ),
                allocation_type=(
                    "Manual Habit Addition"
                ),
            )

            applied = requested

        else:
            remaining = requested

            for index in sorted(
                candidates,
                key=lambda item: (
                    result[item].duration_sec
                ),
                reverse=True,
            ):
                if remaining <= 0.001:
                    break

                current = result[index]

                deduction = min(
                    current.duration_sec,
                    remaining,
                )

                result[index] = Record(
                    date=current.date,
                    source=current.source,
                    device=current.device,
                    activity=current.activity,
                    category=current.category,
                    subcategory=current.subcategory,
                    duration_sec=(
                        current.duration_sec
                        - deduction
                    ),
                    allocation_type=(
                        "Manual Habit Reduction"
                    ),
                )

                remaining -= deduction

            applied = (
                requested - remaining
            )

            if remaining > 0.001:
                raise ValueError(
                    "Negative Habit adjustment exceeds "
                    "available Habit time: "
                    f"{adjustment.date} / "
                    f"{adjustment.category} / "
                    f"{before:.3f}s - "
                    f"{requested:.3f}s."
                )

        after = sum(
            result[index].duration_sec
            for index in candidates
        )

        audit.append(
            {
                "Date": (
                    adjustment.date.isoformat()
                ),
                "Correction_Type": (
                    "HABIT_ADJUSTMENT"
                ),
                "Source": HABIT_SOURCE,
                "Category": adjustment.category,
                "Subcategory": "",
                "Before_min": round(
                    before / 60.0,
                    3,
                ),
                "Requested_min": round(
                    requested / 60.0,
                    3,
                ),
                "Direction": (
                    "ADD"
                    if adjustment.adjustment_sec >= 0
                    else "REMOVE"
                ),
                "Applied_min": round(
                    applied / 60.0,
                    3,
                ),
                "Unapplied_min": round(
                    max(
                        0.0,
                        requested - applied,
                    ) / 60.0,
                    3,
                ),
                "After_min": round(
                    after / 60.0,
                    3,
                ),
                "Status": "APPLIED",
                "Reason": adjustment.reason,
            }
        )

    return (
        result,
        audit,
        attribution_adjustments,
    )


def is_shared_device_record(
    record: Record,
) -> bool:
    return (
        record.device.strip().lower()
        == SHARED_DEVICE_RULE.target_device.lower()
    )


def apply_shared_device_attribution(
    records: list[Record],
    adjustments: list[HabitAdjustment],
) -> tuple[
    list[Record],
    list[dict[str, object]],
]:
    """
    Remove secondary-user time only from ActivityWatch
    Uncategorized records on the configured shared device.

    Apple Screen Time is explicitly excluded.
    """
    result = list(records)

    audit: list[
        dict[str, object]
    ] = []

    for adjustment in adjustments:
        requested = abs(
            adjustment.adjustment_sec
        )

        candidates = [
            index
            for index, record in enumerate(result)
            if (
                record.date == adjustment.date
                and record.source.lower()
                == ACTIVITYWATCH_SOURCE.lower()
                and is_shared_device_record(record)
                and (
                    record.category.lower()
                    == UNCATEGORIZED_CATEGORY.lower()
                    or record.subcategory.lower()
                    == UNCATEGORIZED_CATEGORY.lower()
                    or record.activity.lower()
                    == UNCATEGORIZED_CATEGORY.lower()
                )
                and record.duration_sec > 0.001
            )
        ]

        available = sum(
            result[index].duration_sec
            for index in candidates
        )

        remaining = requested

        for index in sorted(
            candidates,
            key=lambda item: (
                result[item].duration_sec
            ),
            reverse=True,
        ):
            if remaining <= 0.001:
                break

            current = result[index]

            deduction = min(
                current.duration_sec,
                remaining,
            )

            result[index] = Record(
                date=current.date,
                source=current.source,
                device=current.device,
                activity=current.activity,
                category=current.category,
                subcategory=current.subcategory,
                duration_sec=(
                    current.duration_sec
                    - deduction
                ),
                allocation_type=(
                    SHARED_DEVICE_RULE.allocation_type
                ),
            )

            remaining -= deduction

        applied = (
            requested - remaining
        )

        status = (
            "APPLIED"
            if remaining <= 0.001
            else "CAPPED_AT_AVAILABLE_TIME"
        )

        audit.append(
            {
                "Date": (
                    adjustment.date.isoformat()
                ),
                "Correction_Type": (
                    "ATTRIBUTION_REMOVAL"
                ),
                "Source": ACTIVITYWATCH_SOURCE,
                "Device_Target": (
                    SHARED_DEVICE_RULE.target_device_key.title()
                ),
                "Category_Target": (
                    UNCATEGORIZED_CATEGORY
                ),
                "Requested_min": round(
                    requested / 60.0,
                    3,
                ),
                "Applied_min": round(
                    applied / 60.0,
                    3,
                ),
                "Unapplied_min": round(
                    remaining / 60.0,
                    3,
                ),
                "Available_Target_min": round(
                    available / 60.0,
                    3,
                ),
                "Status": status,
                "Reason": adjustment.reason,
            }
        )

        print(
            "  ATTRIBUTION REMOVAL | "
            f"{adjustment.date} | "
            "ActivityWatch "
            f"{SHARED_DEVICE_RULE.target_device_key.title()} "
            "/ Uncategorized | "
            f"requested="
            f"{requested / 60.0:.1f} min | "
            f"applied="
            f"{applied / 60.0:.1f} min | "
            f"unapplied="
            f"{remaining / 60.0:.1f} min | "
            f"{status}"
        )

    return (
        result,
        audit,
    )


def build_classification_audit(
    removals: list[ClassificationRemoval],
    habit_adjustments: list[HabitAdjustment],
) -> list[dict[str, object]]:
    """
    Preserve Integration_Classification_Removals.csv as audit
    information without applying it a second time.

    This makes the audit trail explicit.
    """
    negative_habit_keys = {
        (
            adjustment.date,
            adjustment.category.lower(),
        )
        for adjustment in habit_adjustments
        if (
            adjustment.adjustment_sec < 0
            and not is_shared_device_adjustment(
                adjustment
            )
        )
    }

    audit: list[
        dict[str, object]
    ] = []

    for removal in removals:
        key = (
            removal.date,
            removal.habit_category.lower(),
        )

        if SHARED_DEVICE_RULE.matches_reason(
            removal.reason
        ):
            status = (
                "HANDLED_BY_ATTRIBUTION_REMOVAL"
            )

        elif key in negative_habit_keys:
            status = (
                "ALREADY_APPLIED_AS_HABIT_ADJUSTMENT"
            )

        else:
            status = (
                "AUDIT_ONLY_NOT_APPLIED"
            )

        audit.append(
            {
                "Date": (
                    removal.date.isoformat()
                ),
                "Correction_Type": (
                    "INTEGRATION_CLASSIFICATION_REMOVAL"
                ),
                "Habit_Category": (
                    removal.habit_category
                ),
                "Analytical_Category": (
                    removal.analytical_category
                ),
                "Analytical_Subcategory": (
                    removal.analytical_subcategory
                ),
                "Requested_min": round(
                    removal.removal_sec / 60.0,
                    3,
                ),
                "Applied_min": 0.0,
                "Unapplied_min": 0.0,
                "Status": status,
                "Reason": removal.reason,
            }
        )

    return audit


def load_taxonomy() -> dict[
    tuple[str, str],
    TaxonomyMapping,
]:
    activity_rows = read_csv(
        ACTIVITY_TAXONOMY_FILE
    )

    category_rows = read_csv(
        CATEGORY_TAXONOMY_FILE
    )

    require_columns(
        ACTIVITY_TAXONOMY_FILE,
        activity_rows,
        {
            "Source",
            "Activity_Name",
            "Canonical_Category",
            "Domain",
            "Energy",
            "Goal",
            "Mapping_Type",
        },
    )

    require_columns(
        CATEGORY_TAXONOMY_FILE,
        category_rows,
        {
            "Canonical_Category",
            "Domain",
            "Energy",
            "Goal",
        },
    )

    taxonomy: dict[
        tuple[str, str],
        TaxonomyMapping,
    ] = {}

    for row in category_rows:
        category = (
            row.get(
                "Canonical_Category",
                "",
            )
        )

        if not category:
            continue

        taxonomy[
            (
                "CategoryDefault",
                category,
            )
        ] = TaxonomyMapping(
            category=category,
            domain=row.get(
                "Domain",
                "",
            ),
            energy=row.get(
                "Energy",
                "",
            ),
            goal=row.get(
                "Goal",
                "",
            ),
            mapping_type="Default",
        )

    for row in activity_rows:
        source = row.get(
            "Source",
            "",
        )

        activity = row.get(
            "Activity_Name",
            "",
        )

        category = row.get(
            "Canonical_Category",
            "",
        )

        if not (
            source
            and activity
            and category
        ):
            continue

        if category.upper() == "UNMAPPED":
            continue

        taxonomy[
            (
                source,
                activity,
            )
        ] = TaxonomyMapping(
            category=category,
            domain=row.get(
                "Domain",
                "",
            ),
            energy=row.get(
                "Energy",
                "",
            ),
            goal=row.get(
                "Goal",
                "",
            ),
            mapping_type=row.get(
                "Mapping_Type",
                "",
            ),
        )

    return taxonomy


def default_taxonomy(
    taxonomy: dict[
        tuple[str, str],
        TaxonomyMapping,
    ],
    category: str,
) -> TaxonomyMapping:
    mapping = taxonomy.get(
        (
            "CategoryDefault",
            category,
        )
    )

    if mapping is None:
        raise ValueError(
            "No category taxonomy mapping found "
            f"for {category!r}."
        )

    return mapping


def get_taxonomy_mappings(
    taxonomy: dict[
        tuple[str, str],
        TaxonomyMapping,
    ],
    record: Record,
) -> list[
    tuple[float, TaxonomyMapping, str]
]:
    source = record.source.strip()
    activity = record.activity.strip()
    category = record.category.strip()

    if (
        source.lower()
        == APPLE_SCREEN_TIME_SOURCE.lower()
        and (
            activity.lower() == "other"
            or category.lower() == "other"
        )
    ):
        mapping = default_taxonomy(
            taxonomy,
            "Utilities",
        )

        return [
            (
                1.0,
                mapping,
                "Other",
            )
        ]

    if (
        source.lower()
        == ACTIVITYWATCH_SOURCE.lower()
        and (
            category.lower()
            == UNCATEGORIZED_CATEGORY.lower()
            or activity.lower()
            == UNCATEGORIZED_CATEGORY.lower()
        )
    ):
        utilities = default_taxonomy(
            taxonomy,
            "Utilities",
        )

        productivity = default_taxonomy(
            taxonomy,
            "Productivity & Finance",
        )

        return [
            (
                0.5,
                utilities,
                UNCATEGORIZED_CATEGORY,
            ),
            (
                0.5,
                productivity,
                UNCATEGORIZED_CATEGORY,
            ),
        ]

    direct = taxonomy.get(
        (
            source,
            activity,
        )
    )

    if direct is not None:
        return [
            (
                1.0,
                direct,
                activity,
            )
        ]

    category_mapping = taxonomy.get(
        (
            "CategoryDefault",
            category,
        )
    )

    if category_mapping is not None:
        return [
            (
                1.0,
                category_mapping,
                activity,
            )
        ]

    raise ValueError(
        "No taxonomy mapping found for "
        f"Source={source!r}, "
        f"Activity={activity!r}, "
        f"Category={category!r}."
    )


def build_final_rows(
    records: list[Record],
    taxonomy: dict[
        tuple[str, str],
        TaxonomyMapping,
    ],
) -> list[dict[str, object]]:
    rows: list[
        dict[str, object]
    ] = []

    for record in records:
        if record.duration_sec <= 0.001:
            continue

        mappings = get_taxonomy_mappings(
            taxonomy,
            record,
        )

        for fraction, mapping, mapped_activity in mappings:
            duration_sec = (
                record.duration_sec
                * fraction
            )

            rows.append(
                {
                    "Date": (
                        record.date.isoformat()
                    ),
                    "Source": record.source,
                    "Device": record.device,
                    "Activity": mapped_activity,
                    "Category": mapping.category,
                    "Subcategory": (
                        record.subcategory
                    ),
                    "Domain": mapping.domain,
                    "Energy": mapping.energy,
                    "Goal": mapping.goal,
                    "Duration_sec": round(
                        duration_sec,
                        3,
                    ),
                    "Duration_min": round(
                        duration_sec / 60.0,
                        3,
                    ),
                    "Allocation_Type": (
                        record.allocation_type
                    ),
                    "Taxonomy_Mapping": (
                        "50/50 Uncategorized split"
                        if fraction != 1.0
                        else mapping.mapping_type
                    ),
                }
            )

    return rows


def build_off_device_rows(
    corrected_records: list[Record],
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    """
    Add the inferred off-device portion of the 24-hour time universe.

    Off-device life is the complement of unique tracked time within the
    24-hour universe. Tracked overlap is removed from tracked time once for
    this universe calculation; it is not removed from the observed category
    rows because the overlap cannot be assigned to a specific category here.
    """
    rows: list[dict[str, object]] = []
    current = start_date

    while current <= end_date:
        corrected_total_sec = sum(
            record.duration_sec
            for record in corrected_records
            if record.date == current
        )

        overlap_sec = max(
            0.0,
            corrected_total_sec - DAY_SECONDS,
        )

        gross_residual_sec = max(
            0.0,
            DAY_SECONDS - corrected_total_sec,
        )

        capacity_sec = DAY_SECONDS
        analytical_off_device_sec = max(
            0.0,
            capacity_sec
            - max(
                0.0,
                corrected_total_sec - overlap_sec,
            ),
        )

        if analytical_off_device_sec > 0.001:
            rows.append(
                {
                    "Date": current.isoformat(),
                    "Source": "Off-Device",
                    "Device": "Off-Device",
                    "Activity": "Untracked Maintenance",
                    "Category": "Off-Device Life",
                    "Subcategory": "Untracked Maintenance",
                    "Domain": "Physical",
                    "Energy": "Active",
                    "Goal": "Maintenance",
                    "Duration_sec": round(
                        analytical_off_device_sec,
                        3,
                    ),
                    "Duration_min": round(
                        analytical_off_device_sec / 60.0,
                        3,
                    ),
                    "Allocation_Type": "Inferred Time Universe Residual",
                    "Taxonomy_Mapping": "Synthetic Off-Device Life",
                }
            )

        current += timedelta(days=1)

    return rows


def aggregate_analysis(
    rows: list[dict[str, object]],
    dimensions: tuple[str, ...],
) -> list[dict[str, object]]:
    totals: dict[
        tuple[str, ...],
        float,
    ] = defaultdict(float)

    for row in rows:
        key = tuple(
            str(row.get(
                dimension,
                "",
            ))
            for dimension in dimensions
        )

        totals[key] += float(
            row["Duration_sec"]
        )

    output: list[
        dict[str, object]
    ] = []

    for key, duration_sec in sorted(
        totals.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        row: dict[str, object] = {
            "Analysis_Level": (
                " → ".join(dimensions)
            ),
            "Device": "",
            "Category": "",
            "Subcategory": "",
            "Domain": "",
            "Energy": "",
            "Goal": "",
            "Duration_sec": round(
                duration_sec,
                3,
            ),
            "Duration_min": round(
                duration_sec / 60.0,
                3,
            ),
        }

        for dimension, value in zip(
            dimensions,
            key,
        ):
            row[dimension] = value

        output.append(row)

    return output


def build_hierarchy_analysis(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    levels = (
        ("Device",),
        (
            "Device",
            "Category",
        ),
        (
            "Device",
            "Category",
            "Subcategory",
        ),
        (
            "Device",
            "Category",
            "Subcategory",
            "Domain",
        ),
        (
            "Device",
            "Category",
            "Subcategory",
            "Domain",
            "Energy",
        ),
        (
            "Device",
            "Category",
            "Subcategory",
            "Domain",
            "Energy",
            "Goal",
        ),
        ("Category",),
        (
            "Category",
            "Subcategory",
        ),
        (
            "Category",
            "Subcategory",
            "Domain",
        ),
        (
            "Category",
            "Subcategory",
            "Domain",
            "Energy",
        ),
        (
            "Category",
            "Subcategory",
            "Domain",
            "Energy",
            "Goal",
        ),
        ("Domain",),
        ("Energy",),
        ("Goal",),
    )

    output: list[
        dict[str, object]
    ] = []

    for dimensions in levels:
        output.extend(
            aggregate_analysis(
                rows,
                dimensions,
            )
        )

    return output


def build_device_category_analysis(
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    dimensions = (
        "Device",
        "Category",
        "Subcategory",
        "Domain",
        "Energy",
        "Goal",
    )

    return aggregate_analysis(
        rows,
        dimensions,
    )


def build_daily_reconciliation(
    original_records: list[Record],
    corrected_records: list[Record],
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    output: list[
        dict[str, object]
    ] = []

    current = start_date

    while current <= end_date:
        original_total = sum(
            record.duration_sec
            for record in original_records
            if record.date == current
        )

        corrected_total = sum(
            record.duration_sec
            for record in corrected_records
            if record.date == current
        )

        correction_delta = (
            corrected_total
            - original_total
        )

        overlap = max(
            0.0,
            corrected_total
            - DAY_SECONDS,
        )

        uncovered = max(
            0.0,
            DAY_SECONDS
            - corrected_total,
        )

        reconciliation_difference = max(
            overlap,
            uncovered,
        )

        status = (
            "NORMAL_TOLERANCE"
            if (
                reconciliation_difference
                <= (
                    RECONCILIATION_TOLERANCE_MIN
                    * 60.0
                )
            )
            else "REVIEW"
        )

        output.append(
            {
                "Date": current.isoformat(),
                "Original_Recorded_min": round(
                    original_total / 60.0,
                    3,
                ),
                "Corrected_Recorded_min": round(
                    corrected_total / 60.0,
                    3,
                ),
                "Correction_min": round(
                    correction_delta / 60.0,
                    3,
                ),
                "Overlap_min": round(
                    overlap / 60.0,
                    3,
                ),
                "Uncovered_min": round(
                    uncovered / 60.0,
                    3,
                ),
                "Tolerance_min": (
                    RECONCILIATION_TOLERANCE_MIN
                ),
                "Status": status,
            }
        )

        current += timedelta(days=1)

    return output


def build_time_universe_reconciliation(
    original_records: list[Record],
    corrected_records: list[Record],
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    """
    Reconcile the corrected tracked sources against the 24-hour universe.

    The analytical off-device time is the complement of unique tracked time:

        unique tracked = corrected tracked - overlap
        off-device     = daily capacity - unique tracked

    This reconciles the time universe without changing the observed/corrected
    category rows, which retain their original durations for auditability.
    """
    rows: list[dict[str, object]] = []
    current = start_date

    while current <= end_date:
        original_total_sec = sum(
            record.duration_sec
            for record in original_records
            if record.date == current
        )
        corrected_total_sec = sum(
            record.duration_sec
            for record in corrected_records
            if record.date == current
        )

        capacity_sec = DAY_SECONDS
        overlap_sec = max(
            0.0,
            corrected_total_sec - capacity_sec,
        )
        unique_tracked_sec = max(
            0.0,
            corrected_total_sec - overlap_sec,
        )
        gross_residual_sec = max(
            0.0,
            capacity_sec - corrected_total_sec,
        )
        analytical_off_device_sec = max(
            0.0,
            capacity_sec
            - max(
                0.0,
                corrected_total_sec - overlap_sec,
            ),
        )
        analytical_total_sec = (
            unique_tracked_sec
            + analytical_off_device_sec
        )

        difference_sec = abs(
            analytical_total_sec
            - capacity_sec
        )

        status = (
            "NORMAL_TOLERANCE"
            if difference_sec
            <= RECONCILIATION_TOLERANCE_MIN * 60.0
            else "REVIEW"
        )

        common = {
            "Date": current.isoformat(),
            "Source": "Time Universe",
            "Capacity_min": round(
                capacity_sec / 60.0,
                3,
            ),
            "Original_Tracked_min": round(
                original_total_sec / 60.0,
                3,
            ),
            "Corrected_Tracked_min": round(
                corrected_total_sec / 60.0,
                3,
            ),
            "Unique_Tracked_min": round(
                unique_tracked_sec / 60.0,
                3,
            ),
            "Overlap_min": round(
                overlap_sec / 60.0,
                3,
            ),
            "Gross_Residual_min": round(
                gross_residual_sec / 60.0,
                3,
            ),
            "Analytical_Off_Device_min": round(
                analytical_off_device_sec / 60.0,
                3,
            ),
            "Analytical_Total_min": round(
                analytical_total_sec / 60.0,
                3,
            ),
            "Status": status,
        }

        rows.append(
            {
                **common,
                "Time_Bucket": "Period Capacity",
                "Duration_sec": round(
                    capacity_sec,
                    3,
                ),
                "Duration_min": round(
                    capacity_sec / 60.0,
                    3,
                ),
                "Interpretation": (
                    "Daily 24-hour analytical capacity."
                ),
            }
        )

        if analytical_off_device_sec > 0.001:
            rows.append(
                {
                    **common,
                    "Time_Bucket": (
                        "Off-Device / Untracked Residual"
                    ),
                    "Duration_sec": round(
                        analytical_off_device_sec,
                        3,
                    ),
                    "Duration_min": round(
                        analytical_off_device_sec / 60.0,
                        3,
                    ),
                    "Interpretation": (
                        "Inferred human-life maintenance time "
                        "not represented by unique tracked "
                        "sources. Tracked overlap is excluded "
                        "once from the unique-time calculation. "
                        "May include showering, eating, commuting, "
                        "active pauses, small conversations, "
                        "getting ready, household activity, and "
                        "other off-device activities."
                    ),
                }
            )

        current += timedelta(days=1)

    return rows


def summarize_time_universe(
    rows: list[dict[str, object]],
) -> dict[str, float]:
    """Summarize the 24-hour analytical time universe."""
    capacity_sec = sum(
        float(row["Duration_sec"])
        for row in rows
        if row["Time_Bucket"] == "Period Capacity"
    )

    analytical_off_device_sec = sum(
        float(row["Analytical_Off_Device_min"]) * 60.0
        for row in rows
        if row["Time_Bucket"] == "Period Capacity"
    )

    overlap_sec = sum(
        float(row["Overlap_min"]) * 60.0
        for row in rows
        if row["Time_Bucket"] == "Period Capacity"
    )

    corrected_tracked_sec = sum(
        float(row["Corrected_Tracked_min"]) * 60.0
        for row in rows
        if row["Time_Bucket"] == "Period Capacity"
    )

    unique_tracked_sec = sum(
        float(row["Unique_Tracked_min"]) * 60.0
        for row in rows
        if row["Time_Bucket"] == "Period Capacity"
    )

    analytical_total_sec = sum(
        float(row["Analytical_Total_min"]) * 60.0
        for row in rows
        if row["Time_Bucket"] == "Period Capacity"
    )

    return {
        "capacity_sec": capacity_sec,
        "corrected_tracked_sec": corrected_tracked_sec,
        "unique_tracked_sec": unique_tracked_sec,
        "analytical_off_device_sec": analytical_off_device_sec,
        "residual_sec": analytical_off_device_sec,
        "overlap_sec": overlap_sec,
        "analytical_total_sec": analytical_total_sec,
    }


def build_source_reconciliation(
    original_records: list[Record],
    corrected_records: list[Record],
    start_date: date,
    end_date: date,
) -> list[dict[str, object]]:
    output: list[
        dict[str, object]
    ] = []

    current = start_date

    while current <= end_date:
        sources = sorted(
            {
                record.source
                for record in original_records
                if record.date == current
            }
            |
            {
                record.source
                for record in corrected_records
                if record.date == current
            }
        )

        daily_corrected = sum(
            record.duration_sec
            for record in corrected_records
            if record.date == current
        )

        overlap = max(
            0.0,
            daily_corrected
            - DAY_SECONDS,
        )

        uncovered = max(
            0.0,
            DAY_SECONDS
            - daily_corrected,
        )

        status = (
            "NORMAL_TOLERANCE"
            if max(
                overlap,
                uncovered,
            )
            <= (
                RECONCILIATION_TOLERANCE_MIN
                * 60.0
            )
            else "REVIEW"
        )

        for source in sources:
            original_total = sum(
                record.duration_sec
                for record in original_records
                if (
                    record.date == current
                    and record.source == source
                )
            )

            corrected_total = sum(
                record.duration_sec
                for record in corrected_records
                if (
                    record.date == current
                    and record.source == source
                )
            )

            output.append(
                {
                    "Date": current.isoformat(),
                    "Source": source,
                    "Original_Duration_min": round(
                        original_total / 60.0,
                        3,
                    ),
                    "Corrected_Duration_min": round(
                        corrected_total / 60.0,
                        3,
                    ),
                    "Correction_min": round(
                        (
                            corrected_total
                            - original_total
                        ) / 60.0,
                        3,
                    ),
                    "Daily_Corrected_Total_min": round(
                        daily_corrected / 60.0,
                        3,
                    ),
                    "Overlap_min": round(
                        overlap / 60.0,
                        3,
                    ),
                    "Uncovered_min": round(
                        uncovered / 60.0,
                        3,
                    ),
                    "Tolerance_min": (
                        RECONCILIATION_TOLERANCE_MIN
                    ),
                    "Status": status,
                }
            )

        current += timedelta(days=1)

    return output


def write_csv(
    path: Path,
    rows: list[dict[str, object]],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not rows:
        path.write_text(
            "",
            encoding="utf-8",
        )
        return

    fieldnames: list[str] = []

    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


def format_duration(
    seconds: float,
) -> str:
    total_seconds = max(
        0,
        int(round(seconds)),
    )

    hours, remainder = divmod(
        total_seconds,
        3600,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    if hours:
        return (
            f"{hours}h {minutes}m"
        )

    if minutes:
        return (
            f"{minutes}m {seconds}s"
        )

    return f"{seconds}s"



def build_report(
    final_rows: list[dict[str, object]],
    daily_reconciliation: list[dict[str, object]],
    correction_audit: list[dict[str, object]],
    time_universe_summary: dict[str, float],
    start_date: date,
    end_date: date,
) -> str:
    total_duration = sum(
        float(row["Duration_sec"])
        for row in final_rows
    )

    review_days = [
        row
        for row in daily_reconciliation
        if row["Status"] == "REVIEW"
    ]

    lines = [
        "=== FINAL CONSOLIDATED ANALYSIS ===",
        "",
        f"Dates : {start_date} -> {end_date}",
        (
            "Final analytical rows : "
            f"{len(final_rows)}"
        ),
        (
            "Final analytical duration : "
            f"{format_duration(total_duration)}"
        ),
        (
            "Time-universe capacity : "
            f"{format_duration(time_universe_summary['capacity_sec'])}"
        ),
        (
            "Corrected tracked time : "
            f"{format_duration(time_universe_summary['corrected_tracked_sec'])}"
        ),
        (
            "Off-device life : "
            f"{format_duration(time_universe_summary['analytical_off_device_sec'])}"
        ),
        (
            "Unique tracked time : "
            f"{format_duration(time_universe_summary['unique_tracked_sec'])}"
        ),
        (
            "Tracked overlap : "
            f"{format_duration(time_universe_summary['overlap_sec'])}"
        ),
        "",
        "=== CORRECTION AUDIT ===",
        "",
    ]

    for row in correction_audit:
        lines.append(
            f"{row.get('Date', '')} | "
            f"{row.get('Correction_Type', '')} | "
            f"requested="
            f"{row.get('Requested_min', '')} min | "
            f"applied="
            f"{row.get('Applied_min', '')} min | "
            f"unapplied="
            f"{row.get('Unapplied_min', '')} min | "
            f"status="
            f"{row.get('Status', '')} | "
            f"{row.get('Reason', '')}"
        )

    lines.extend(
        [
            "",
            "=== DAILY RECONCILIATION ===",
            "",
            (
                "Tolerance : "
                f"{RECONCILIATION_TOLERANCE_MIN} minutes"
            ),
            "",
        ]
    )

    for row in daily_reconciliation:
        lines.append(
            f"{row['Date']} | "
            f"original="
            f"{row['Original_Recorded_min']} min | "
            f"corrected="
            f"{row['Corrected_Recorded_min']} min | "
            f"delta="
            f"{row['Correction_min']} min | "
            f"overlap="
            f"{row['Overlap_min']} min | "
            f"uncovered="
            f"{row['Uncovered_min']} min | "
            f"{row['Status']}"
        )

    lines.extend(
        [
            "",
            "=== RESULT ===",
            "",
        ]
    )

    if review_days:
        lines.append(
            f"Reconciliation review days: "
            f"{len(review_days)}"
        )

        lines.append(
            "RESULT: FINAL ANALYSIS GENERATED "
            "WITH RECONCILIATION REVIEW FLAGS."
        )
    else:
        lines.append(
            "All days are within the "
            f"{RECONCILIATION_TOLERANCE_MIN / 60:g}-hour reconciliation tolerance."
        )

        lines.append(
            "RESULT: FINAL ANALYSIS PASSED."
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()

    start_date = parse_date(
        args.start_date
    )

    end_date = parse_date(
        args.end_date
    )

    if end_date < start_date:
        raise ValueError(
            "End date cannot be earlier "
            "than start date."
        )

    output_root = (
        INTEGRATED_ROOT
        / "Analysis"
        / "Final"
        / (
            f"{start_date}_"
            f"{end_date}"
        )
    )

    final_csv = (
        output_root
        / (
            "Final_Analysis_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    hierarchy_csv = (
        output_root
        / (
            "Final_Analysis_By_Device_Category_"
            "Subcategory_Domain_Energy_Goal_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    source_csv = (
        output_root
        / (
            "Final_Source_Reconciliation_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    daily_csv = (
        output_root
        / (
            "Final_Daily_Reconciliation_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    correction_csv = (
        output_root
        / (
            "Final_Correction_Audit_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    attribution_csv = (
        output_root
        / (
            "Final_Attribution_Removals_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    report_file = (
        output_root
        / (
            "Final_Analysis_Report_"
            f"{start_date}_"
            f"{end_date}.txt"
        )
    )

    time_universe_csv = (
        output_root
        / (
            "Final_Time_Universe_Reconciliation_"
            f"{start_date}_"
            f"{end_date}.csv"
        )
    )

    outputs = (
        final_csv,
        hierarchy_csv,
        source_csv,
        daily_csv,
        correction_csv,
        attribution_csv,
        time_universe_csv,
        report_file,
    )

    if (
        not args.force
        and any(
            path.exists()
            for path in outputs
        )
    ):
        raise FileExistsError(
            "Final outputs already exist. "
            "Use --force to replace them."
        )

    print(
        "=== Final Consolidated Analysis ==="
    )

    print(
        f"Dates : {start_date} -> {end_date}"
    )

    integrated_files = (
        discover_integrated_files(
            start_date,
            end_date,
        )
    )

    print(
        "Integrated CSV files : "
        f"{len(integrated_files)}"
    )

    original_records = (
        load_integrated_records(
            integrated_files,
            start_date,
            end_date,
        )
    )

    print(
        "Raw records          : "
        f"{len(original_records)}"
    )

    habit_adjustments = (
        load_habit_adjustments(
            start_date,
            end_date,
        )
    )

    classification_removals = (
        load_classification_removals(
            start_date,
            end_date,
        )
    )

    print(
        "Habit adjustments    : "
        f"{len(habit_adjustments)}"
    )

    print(
        "Integration removals : "
        f"{len(classification_removals)}"
    )

    (
        corrected_records,
        habit_audit,
        attribution_adjustments,
    ) = apply_habit_adjustments(
        original_records,
        habit_adjustments,
    )

    print(
        "Attribution removals : "
        f"{len(attribution_adjustments)}"
    )

    (
        corrected_records,
        attribution_audit,
    ) = apply_shared_device_attribution(
        corrected_records,
        attribution_adjustments,
    )

    classification_audit = (
        build_classification_audit(
            classification_removals,
            habit_adjustments,
        )
    )

    correction_audit = (
        habit_audit
        + attribution_audit
        + classification_audit
    )

    taxonomy = load_taxonomy()

    final_rows = build_final_rows(
        corrected_records,
        taxonomy,
    )

    off_device_rows = build_off_device_rows(
        corrected_records,
        start_date,
        end_date,
    )

    final_rows.extend(
        off_device_rows
    )

    hierarchy_rows = (
        build_hierarchy_analysis(
            final_rows
        )
    )

    source_reconciliation = (
        build_source_reconciliation(
            original_records,
            corrected_records,
            start_date,
            end_date,
        )
    )

    daily_reconciliation = (
        build_daily_reconciliation(
            original_records,
            corrected_records,
            start_date,
            end_date,
        )
    )

    time_universe_reconciliation = (
        build_time_universe_reconciliation(
            original_records,
            corrected_records,
            start_date,
            end_date,
        )
    )

    time_universe_summary = summarize_time_universe(
        time_universe_reconciliation,
    )

    if not args.force:
        for path in outputs:
            if path.exists():
                raise FileExistsError(
                    f"Output already exists: {path}"
                )

    write_csv(
        final_csv,
        final_rows,
    )

    write_csv(
        hierarchy_csv,
        hierarchy_rows,
    )

    write_csv(
        source_csv,
        source_reconciliation,
    )

    write_csv(
        daily_csv,
        daily_reconciliation,
    )

    write_csv(
        correction_csv,
        correction_audit,
    )

    write_csv(
        attribution_csv,
        attribution_audit,
    )

    write_csv(
        time_universe_csv,
        time_universe_reconciliation,
    )

    report = build_report(
        final_rows,
        daily_reconciliation,
        correction_audit,
        time_universe_summary,
        start_date,
        end_date,
    )

    output_root.mkdir(
        parents=True,
        exist_ok=True,
    )

    report_file.write_text(
        report,
        encoding="utf-8",
    )

    review_days = [
        row
        for row in daily_reconciliation
        if row["Status"] == "REVIEW"
    ]

    total_duration = sum(
        float(row["Duration_sec"])
        for row in final_rows
    )

    print()
    print(
        "=== FINAL ANALYSIS SUMMARY ==="
    )

    print(
        "Final rows            : "
        f"{len(final_rows)}"
    )

    print(
        "Hierarchy rows        : "
        f"{len(hierarchy_rows)}"
    )

    print(
        "Final duration        : "
        f"{format_duration(total_duration)}"
    )

    print(
        "Correction audit rows : "
        f"{len(correction_audit)}"
    )

    print(
        "Reconciliation reviews: "
        f"{len(review_days)}"
    )

    print(
        f"Final CSV             : "
        f"{final_csv}"
    )

    print(
        f"Hierarchy CSV         : "
        f"{hierarchy_csv}"
    )

    print(
        f"Source reconciliation : "
        f"{source_csv}"
    )

    print(
        f"Daily reconciliation  : "
        f"{daily_csv}"
    )

    print(
        f"Correction audit      : "
        f"{correction_csv}"
    )

    print(
        f"Attribution audit     : "
        f"{attribution_csv}"
    )

    print(
        f"Time universe         : "
        f"{time_universe_csv}"
    )

    print(
        "Time-universe capacity: "
        f"{format_duration(time_universe_summary['capacity_sec'])}"
    )

    print(
        "Corrected tracked time: "
        f"{format_duration(time_universe_summary['corrected_tracked_sec'])}"
    )

    print(
        "Unique tracked time   : "
        f"{format_duration(time_universe_summary['unique_tracked_sec'])}"
    )

    print(
        "Off-device life       : "
        f"{format_duration(time_universe_summary['analytical_off_device_sec'])}"
    )

    print(
        "Analytical total      : "
        f"{format_duration(time_universe_summary['analytical_total_sec'])}"
    )

    if time_universe_summary["overlap_sec"] > 0:
        print(
            "Tracked overlap       : "
            f"{format_duration(time_universe_summary['overlap_sec'])}"
        )

    print(
        f"Report                : "
        f"{report_file}"
    )

    print()

    if review_days:
        print(
            "RESULT: FINAL ANALYSIS GENERATED "
            "WITH RECONCILIATION REVIEW FLAGS."
        )
    else:
        print(
            "RESULT: FINAL ANALYSIS PASSED."
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )
    except Exception as exc:
        print(
            f"ERROR: {type(exc).__name__}: {exc}"
        )
        print(
            "RESULT: FINAL ANALYSIS FAILED."
        )
        raise