# config/historical.py
"""Configuration for the historical (legacy) bridge pipeline.

Phase 2 of the historical-data integration. Everything here is read-only
configuration: workbook manifest, period labels, input/output locations and
the location of the version-controlled taxonomy bridge tables.

Design rules (see docs/HISTORICAL_BRIDGE.md):

* Legacy workbooks are immutable source evidence. The pipeline only reads
  them and records their SHA-256 in every run manifest.
* Generated datasets go to output/Historical/ (git-ignored, private data).
* Mapping tables live in config/historical_bridge/ (version-controlled; they
  contain taxonomy labels only, no personal time data).
* The current-system period outputs (August 2026 transition, September 2026
  closed accounting) are read, never written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

INPUT_ROOT = PROJECT_ROOT / "input"
OUTPUT_ROOT = PROJECT_ROOT / "output"

# Immutable copies of the legacy workbooks (git-ignored via input/).
LEGACY_INPUT_ROOT = INPUT_ROOT / "Historical" / "Legacy"

# Generated historical datasets (git-ignored via output/).
HISTORICAL_OUTPUT_ROOT = OUTPUT_ROOT / "Historical"

# Version-controlled taxonomy bridge tables.
BRIDGE_CONFIG_ROOT = Path(__file__).resolve().parent / "historical_bridge"

# Closed / transition current-system periods (read-only references).
FINAL_ANALYSIS_ROOT = OUTPUT_ROOT / "Integrated" / "Analysis" / "Final"
CURRENT_REFERENCE_PERIOD = "2026-09-01_2026-09-30"
TRANSITION_PERIOD = "2026-08-01_2026-08-31"

ANALYSIS_START_MONTH = "2025-09"
ANALYSIS_END_MONTH = "2026-09"
PERIOD_LABEL = "September 2025 – September 2026 (13 months)"

# Annual consolidated analysis (historical_annual.py). Consumes the
# output/Historical/ datasets; writes only below this folder.
ANNUAL_OUTPUT_ROOT = HISTORICAL_OUTPUT_ROOT / f"Annual_{ANALYSIS_START_MONTH}_to_{ANALYSIS_END_MONTH}"

# Legacy baseline window used for budget references. Excludes the ramp-up
# month (2025-09: most days above the 120-min tolerance) and 2026-07
# (off-device rows not recorded on 27–31). Analytical choice, listed in the
# pending-decision register; change here and re-run to test sensitivity.
ANNUAL_BASELINE_START_MONTH = "2025-10"
ANNUAL_BASELINE_END_MONTH = "2026-06"

# Methodology regimes.
REGIME_LEGACY = "LEGACY_MANUAL_TRACKING"
REGIME_TRANSITION = "TRANSITION_REVIEW"
REGIME_CURRENT = "CURRENT_RECONCILED_TIME_ACCOUNTING"
REGIME_NO_DATA = "NO_DATA"


@dataclass(frozen=True)
class LegacySheet:
    """One monthly timesheet + its legacy report sheet."""

    workbook: str
    workbook_code: str
    timesheet: str
    report: str
    month: str  # YYYY-MM


# Explicit manifest. The loader validates every entry against the sheet's own
# start-date cell (B4) so a mislabelled sheet fails loudly instead of being
# silently assigned to the wrong month.
LEGACY_SHEETS: tuple[LegacySheet, ...] = (
    LegacySheet("Personal Time Management 2025.xlsx", "PTM2025", "Timesheet 9", "Report 9", "2025-09"),
    LegacySheet("Personal Time Management 2025.xlsx", "PTM2025", "Timesheet 10", "Report 10", "2025-10"),
    LegacySheet("Personal Time Management 2025.xlsx", "PTM2025", "Timesheet 11", "Report 11", "2025-11"),
    LegacySheet("Personal Time Management 2025.xlsx", "PTM2025", "Timesheet 12", "Report 12", "2025-12"),
    LegacySheet("Personal Time Management 2026 T1.xlsx", "PTM2026T1", "Timesheet 1", "Report 1", "2026-01"),
    LegacySheet("Personal Time Management 2026 T1.xlsx", "PTM2026T1", "Timesheet 2", "Report 2", "2026-02"),
    LegacySheet("Personal Time Management 2026 T1.xlsx", "PTM2026T1", "Timesheet 3", "Report 3", "2026-03"),
    LegacySheet("Personal Time Management 2026 T1.xlsx", "PTM2026T1", "Timesheet 4", "Report 4", "2026-04"),
    LegacySheet("Personal Time Management 2026 T1.xlsx", "PTM2026T1", "Timesheet 5", "Report 5", "2026-05"),
    LegacySheet("Personal Time Management 2026 T1.xlsx", "PTM2026T1", "Timesheet 6", "Report 6", "2026-06"),
    LegacySheet("Personal Time Management 2026 T2.xlsx", "PTM2026T2", "Timesheet 1", "Report 1", "2026-07"),
)

LEGACY_WORKBOOKS: tuple[str, ...] = tuple(dict.fromkeys(s.workbook for s in LEGACY_SHEETS))

# Sheets that exist in the workbooks but are intentionally NOT used as
# evidence for monthly accounting (documented reason in the reconciliation
# report).
EXCLUDED_SHEETS: dict[str, str] = {
    "Report Q1": "Quarterly roll-up; several formulas reference fixed cells of 'Report 2' (e.g. $C$18, $D$4) instead of the matching row, so its Energy/Device/Goal/Budget figures are corrupted.",
    "Report Q2": "Quarterly roll-up built on the same template as Report Q1; not used — monthly reports are the evidence unit.",
    "Timesheet Q": "Quarterly category/device roll-up of monthly sheets; derived, not evidence.",
    "Categories": "Legacy vocabulary sheet; used only as the source of the legacy vocabulary.",
    "©": "Template licence sheet.",
}


def current_final_dir(period: str) -> Path:
    return FINAL_ANALYSIS_ROOT / period
