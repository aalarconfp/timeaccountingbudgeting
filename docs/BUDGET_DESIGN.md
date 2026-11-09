# Budget Design

## Status

**Implemented** (Actual vs Budget layer). Change class D (`CHANGE_CONTROL.md`):
a separate layer on top of the closed Final analysis. It does not change Final,
EDA, Detail or Variation outputs; the Standard Report changes only for periods
that an approved budget covers.

| Piece | File |
|---|---|
| Import the approved workbook into a versioned snapshot | `budget_import.py` |
| Period budget, coverage, Actual vs Budget logic | `budget_core.py` |
| Actual vs Budget analysis and report (runbook H3) | `budget_variance_analysis.py` |
| Paths, tolerances, KPI definitions | `config/budget.py` |
| Optional section in the Standard Report | `standard_report.py` (section 7) |
| Optional close check | `period_close_validator.py --require-budget` |

Background: `historical_annual.py` produced the baseline and scenario templates
that informed the first budget (`HISTORICAL_ANNUAL_ANALYSIS.md`).

## Principle

The time system describes actual behavior. The budget describes intended
behavior. The two are **comparable without being conflated**:

- the budget never feeds Final, EDA, Variation or historical outputs;
- budget numbers are never used as observed time;
- the pipeline reads the budget only from the immutable snapshot, never from
  the planning workbook.

## Budget source and snapshot

The approved budget is a planning workbook (git-ignored, under
`output/Planning/`). Its leaf sheet holds **hours per week** for every
taxonomy leaf (Device × Category × Subcategory, with Domain / Energy / Goal)
and every budget phase. Each phase sums to 168 h/week.

`budget_import.py` reads cached formula values (`openpyxl`, `data_only=True`)
and fails with a clear error if any formula cell has no cached value (the
workbook must be recalculated and saved in Excel first). It writes to
`output/Reference/Budget/`:

| File | Content |
|---|---|
| `Budget_Leaf_<version>.csv` | Device, Category, Subcategory, Domain, Energy, Goal, Phase, Hours_per_week (full precision) |
| `Budget_Phases_<version>.csv` | Phase, Start, End (inclusive) |
| `Budget_Lines_<version>.csv` | optional "budget line" dimension (line → leaves, per phase) |
| `Budget_Manifest_<version>.json` | source file, SHA-256, import time, version, phase totals, parameters (sleep floor, digital-leisure floor), reconciliation targets, warnings, file hashes |

Workbook layout read by the importer:

| Sheet | Use |
|---|---|
| `Supuestos` A5:C9 | phases (name, start, end); parameters by label ("Piso de sueño por noche", "Piso de ocio digital", "Sueño por noche") |
| `Detalle_Tracker` | **budget source**: header row 4, leaves from row 5 to the row before "Total" |
| `Datos`, `Diagnostico_Lineas` | budget lines and their class (optional dimension; not recalculated) |
| `Presupuesto_Tracker` | control totals by Device / Goal / Energy / Domain / Category and hours per month by Category — reconciliation only |

### Versioning and immutability

- Version = the workbook name without `Time_Budget_` (e.g. `2026-11_to_2027-04_v4`).
- Re-importing an identical file (same SHA-256) is a no-op.
- A changed file with the same name becomes a new version (`..._r2`, `..._r3`).
- Existing snapshot files are never overwritten. Loading a snapshot verifies
  each file against the hash in its manifest and refuses an edited snapshot.
- The analysis uses the most recently imported version unless
  `--budget-version` is given. The version used is recorded in every output.

### Import validations

Blocking (nothing is written):

1. every phase sums to 168 h/week ± 0.01;
2. phases are contiguous (no gaps) and do not overlap;
3. no duplicate leaf keys;
4. leaf roll-ups by Device, Goal, Energy, Domain and Category equal the
   `Presupuesto_Tracker` control totals (± 0.01 h/week);
5. the phase proration of each month equals the sheet's hours per month by
   Category (± 0.01 h) — e.g. Dec-2026 = 744 h, Nov-2026 = 528 h (9–30 Nov);
6. the budget lines add back to their leaves.

Warnings (reported in the console and the manifest, **never corrected**):

- Domain / Energy / Goal of a leaf differs from `output/Reference/Taxonomy/`
  (`Dim_Activity.csv` override for the device's source and subcategory, else
  `Dim_Category_Default.csv`; Off-Device Life = Physical / Active / Maintenance);
- a value is not in `Dim_Domain`, `Dim_Energy` or `Dim_Goal`;
- a line total differs from `Diagnostico_Lineas`.

## Period budget

For any inclusive period `[start, end]` (monthly close or ~15-day interim):

```text
Budget(leaf) = Σ over phases  Hours_per_week(leaf, phase) × 60 × days(phase ∩ period) / 7   [minutes]
```

Because every phase sums to 168 h/week, the period budget always equals
`covered days × 1440 min`. The close validator checks this (± 1 min).

## Coverage (decision)

A period may start before or end after the budget horizon (e.g. November 2026:
days 1–8 are before the first phase). The method is:

1. **Coverage is always reported**: covered days / period days, first and last
   covered date, days per phase (`Budget_Coverage_<period>.csv`).
2. **The comparison uses covered days only.** The actual for those days is
   taken **exactly** from the daily Final file (`Final_Analysis_<period>.csv`,
   post-adjustment) filtered to the covered dates. No proration of the actual
   is needed. When the whole period is covered, the Final hierarchy is used,
   so totals equal the Standard Report.
3. **Full-period view (informational)**: actual min/day for the full period
   next to the covered-day actual and budget min/day, by Goal
   (`Budget_Full_Period_View_<period>.csv`).
4. **No coverage** (e.g. October 2026): the analysis writes only the coverage
   file, a one-line report and the section text "not available for this
   period", and exits with code 0.

Per-day figures and shares use covered days as the denominator.

## Actual vs Budget

`budget_variance_analysis.py` runs **after** `variation_analysis.py`, on the
post-adjustment Final. Output: `output/Integrated/Analysis/Budget/<start>_<end>/`.

| File | Level |
|---|---|
| `Budget_Variance_Leaf_<p>.csv` | Device × Category × Subcategory (with Domain / Energy / Goal) |
| `Budget_Variance_{Device,Category,Goal,Energy,Domain}_<p>.csv` | roll-ups |
| `Budget_Variance_Line_<p>.csv` | budget lines (optional dimension) |
| `Budget_KPI_<p>.csv`, `Budget_Top_Deviations_<p>.csv` | KPIs, top 10 absolute leaf deviations |
| `Budget_Coverage_<p>.csv`, `Budget_Full_Period_View_<p>.csv` | coverage and reconciliation |
| `Budget_Variance_Report_<p>.txt` | short recurring report |
| `Budget_Section_<p>.md` | "Actual vs Budget" section of the Monthly KPI Summary |

Columns: Actual, Budget and Variance (**Actual − Budget**) in minutes and
hours, Variance %, min/day for each, share of each, share delta (pp).

### Status

| Status | Rule |
|---|---|
| Budgeted | budget > 0 and actual > 0 |
| **Not recorded** | budget ≥ 15 min in the period and no actual — a **capture alert** (e.g. offline work not logged in Habit), never a saving |
| Immaterial | budget < 15 min and no actual (rounding residue of the workbook's proportional split) |
| **Unbudgeted** | actual > 0 and no budget in the period |

### Assessment (sign by Goal)

| Goal | Favorable when |
|---|---|
| Investment | Actual > Budget |
| Consumption | Actual < Budget |
| Maintenance | no judgment ("Neutral") |
| Sleep (Maintenance) | evaluated against the sleep floor (h/night, from the budget; 7.5) |
| Off-Device Life | "Neutral (residual)" — it is the clock residual |

`|Variance| ≤ 1 min` is "On budget". Roll-ups use the goal-weighted variance
(+Investment − Consumption, Maintenance 0). Not-recorded leaves are excluded
from the weighted variance and shown separately (`Not_Recorded_Budget_min`);
when present, the roll-up label says "(excl. not recorded)".

Actual is grouped by its Final classification and budget by the budget's
classification, so roll-up actuals match the Standard Report. A leaf whose
Final Domain / Energy / Goal differs from the budget is flagged in
`Taxonomy_Note`.

### KPIs

| KPI | Definition |
|---|---|
| Investment ratio | Investment / (Investment + Maintenance + Consumption), actual vs budget |
| Investment / Maintenance / Consumption min/day | per covered day |
| Discretionary consumption proxy | Games + Entertainment + Social Networking with Goal=Consumption (Standard Report definition), min/day and share |
| Digital leisure min/day | leaves of the budget line "Ocio digital (residual)"; floor 60 min/day is a **minimum** |
| Sleep h/night | Sleep minutes per covered day / 60, vs budget and the 7.5 h floor |
| Not recorded / Unbudgeted leaves | counts and hours |
| Top 10 deviations | leaves by absolute variance |

### Budget lines (optional dimension)

Lines group leaves by habit/commitment. A leaf shared by several lines (e.g.
Offline Study Tracker = classes + study + thesis; Off-Device Life = lunch +
commute + personal maintenance) splits its actual by each line's share of that
leaf's period budget (by horizon share if the period budget is zero; equally
if both are zero). The Excel logic is not recalculated.

## Integration

- **Standard Report** — section "7. ACTUAL vs BUDGET" is added only when the
  budget covers the period. It is computed directly from the snapshot and the
  Final, so the report does not depend on `budget_variance_analysis.py` having
  run first. Reports for uncovered periods are byte-identical to before.
- **Monthly KPI Summary** — `budget_variance_analysis.py --update-kpi-summary`
  replaces the old "## Time budget / NOT AVAILABLE" block with "## Actual vs
  Budget", placed right after the historical month-over-month comparison.
  The section is delimited by markers, so re-running replaces it in place; the
  pre-budget file is kept once as `.md.bak`.
- **Period close** — `period_close_validator.py --require-budget` checks that
  the analysis exists, the snapshot hashes match, coverage is reported and
  recomputes, the period budget reconciles to covered days × 24 h and the leaf
  file adds up. It fails when the budget does not cover the period (do not use
  the flag for such periods).

## Changing the budget

Edit the workbook, recalculate and save it, re-import (new version), then
re-run `budget_variance_analysis.py --force` for the affected periods. Record
the new version in the period notes. A closed period's budget comparison keeps
the version it was closed with unless a versioned correction is recorded
(freeze rule, `CHANGE_CONTROL.md`).
