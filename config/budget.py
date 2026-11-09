"""Time Budget configuration: paths, tolerances and KPI definitions.

The budget is a separate layer on top of the closed Final analysis
(``docs/BUDGET_DESIGN.md``). Nothing here changes Final, EDA or Variation.

The pipeline reads the budget only from the versioned CSV snapshot in
``output/Reference/Budget/`` written by ``budget_import.py``; it never reads
the planning workbook directly.
"""

from __future__ import annotations

from pathlib import Path

from config.settings import DESKTOP_DEVICE, LAPTOP_DEVICE


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "output"

BUDGET_REFERENCE_ROOT = OUTPUT_ROOT / "Reference" / "Budget"
TAXONOMY_ROOT = OUTPUT_ROOT / "Reference" / "Taxonomy"
PLANNING_ROOT = OUTPUT_ROOT / "Planning"

ANALYSIS_ROOT = OUTPUT_ROOT / "Integrated" / "Analysis"
FINAL_ROOT = ANALYSIS_ROOT / "Final"
REPORT_ROOT = ANALYSIS_ROOT / "Report"
BUDGET_ANALYSIS_ROOT = ANALYSIS_ROOT / "Budget"

CSV_ENCODING = "utf-8-sig"

# --- Import validation -----------------------------------------------------

HOURS_PER_WEEK = 168.0
PHASE_TOTAL_TOLERANCE_H = 0.01      # each phase must sum to 168 h ± this
RECONCILIATION_TOLERANCE_H = 0.01   # leaf roll-ups vs Presupuesto_Tracker

# --- Period budget ---------------------------------------------------------

MINUTES_PER_DAY = 1440.0
# Σ period budget must equal covered days × 1440 min within this tolerance.
PERIOD_BUDGET_TOLERANCE_MIN = 1.0
# |Actual − Budget| at or below this many minutes is reported as "On budget".
ON_BUDGET_TOLERANCE_MIN = 1.0
# Actual at or below this many minutes counts as "not recorded".
ZERO_ACTUAL_MIN = 0.01
# A leaf with budget but no actual is a capture alert ("Not recorded") only
# when its period budget is at least this many minutes; smaller budgets are
# "Immaterial" (rounding residue of the workbook's proportional split).
MATERIAL_BUDGET_MIN = 15.0

# --- Taxonomy -------------------------------------------------------------

LEAF_KEY = ("Device", "Category", "Subcategory")
LEAF_LEVEL = "Device → Category → Subcategory → Domain → Energy → Goal"

OFF_DEVICE_DEVICE = "Off-Device"
OFF_DEVICE_CATEGORY = "Off-Device Life"
# Documented in docs/TAXONOMY_AND_EDA.md (not part of Dim_Category_Default).
OFF_DEVICE_MAPPING = ("Physical", "Active", "Maintenance")

# Device in the Final hierarchy → Source column in Dim_Activity.csv.
DEVICE_SOURCE = {
    LAPTOP_DEVICE: "ActivityWatch",
    DESKTOP_DEVICE: "ActivityWatch",
    "AppleScreenTime": "AppleScreenTime",
    "Habit": "Habit",
}

# --- Goal-based assessment -------------------------------------------------

# +1: more is favorable; −1: less is favorable; 0: reported without judgment.
GOAL_DIRECTION = {
    "Investment": 1,
    "Consumption": -1,
    "Maintenance": 0,
}

SLEEP_CATEGORY = "Health & Fitness"
SLEEP_SUBCATEGORY = "Sleep"

# --- KPI definitions -------------------------------------------------------

# Same definition as standard_report.py (docs/REPORTING.md): Games +
# Entertainment + Social Networking classified as Goal=Consumption.
DISCRETIONARY_FULL_CATEGORIES = ("Games", "Entertainment")
DISCRETIONARY_CONSUMPTION_CATEGORY = "Social Networking"

# Fallbacks only; the import reads the real values from the Supuestos sheet.
DEFAULT_SLEEP_FLOOR_H = 7.5
DEFAULT_DIGITAL_LEISURE_FLOOR_MIN = 60.0
DEFAULT_DIGITAL_LEISURE_LINE = "Ocio digital (residual)"

TOP_DEVIATIONS = 10

# Markers used to splice the budget section into Monthly_KPI_Summary_*.md.
KPI_SECTION_BEGIN = "<!-- BUDGET-SECTION:BEGIN -->"
KPI_SECTION_END = "<!-- BUDGET-SECTION:END -->"
