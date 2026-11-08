# Historical Annual Analysis (current taxonomy) and Initial Budget Baseline

Status: implemented, pending human review (not committed).
Change class: D — new feature (`CHANGE_CONTROL.md`). Reporting / analysis layer
plus three documented mapping-rule changes (§5). No accounting rule, source
file or closed period is modified.

## 1. Purpose

Present September 2025 – September 2026 as a period-level analogue of the
monthly reports, **in the current system taxonomy**, using the legacy manual
tracking wherever a defensible mapping exists, and prepare a first,
editable Time Budgeting baseline.

| Level | Question | Where |
|---|---|---|
| Tracking | What produced records; how complete is the evidence? | Standard Report §2, `Annual_A_*` |
| Time Accounting | Recorded, overlap, unique tracked, Off-Device Life — per regime | Standard Report §1–2 |
| Time Analysis | Current categories, subcategories, domains, energy, goals, devices; patterns | Standard Report, EDA, Final Detail, Annual Analysis |
| Time Budgeting | Reference ranges and scenario templates per current-taxonomy line | Annual Analysis §13, `Annual_J_*` |

No budget functionality is implemented and no target is set (`BUDGET_DESIGN.md`).

## 2. Why bridge groups exist — and why they are not the reporting taxonomy

The historical bridge (`HISTORICAL_BRIDGE.md`) assigns every legacy and
current record an internal *bridge group* (e.g. `WORK_STUDY`,
`HOUSEHOLD_DAILY_LIVING`). Bridge groups are a **comparability device**: they
flag capture differences between regimes and drive the pipeline's
month-over-month classification. They are not the system's taxonomy.

The first annual implementation aggregated its dashboards by bridge group,
which created a parallel user-facing taxonomy. It now follows one chain:

```text
legacy source value (workbook cell, kept verbatim with provenance)
  -> mapping rules: config/historical_bridge/legacy_activity_bridge.csv + legacy_label_maps.csv
     (applied once, by historical_bridge.normalize_legacy)
  -> current canonical category / subcategory / domain / energy / goal / device class
     (validated against the current taxonomy)
  -> annual aggregation (historical_canonical.build_canonical_records)
  -> reports
```

The bridge group stays on every record as a diagnostic field and appears only
in the Mapping & Comparability report.

## 3. Authoritative current taxonomy

`historical_canonical.load_current_taxonomy()` reads the same files the
monthly pipeline uses (`final_analysis.TAXONOMY_ROOT` =
`output/Reference/Taxonomy/`):

* categories — `Dim_Category_Default.csv` (12 categories) + the synthetic
  **Off-Device Life** produced by `final_analysis.build_off_device_rows`;
* subcategories — `Dim_Activity.csv` activity names + `config/taxonomy.py`;
* domains / energies / goals — `Dim_Domain.csv`, `Dim_Energy.csv`, `Dim_Goal.csv`.

`historical_canonical.validate_bridge_against_taxonomy()` rejects any bridge
rule that would emit a category, subcategory, domain, energy or goal outside
that taxonomy. Both `historical_pipeline.py` (validation check) and
`historical_annual.py` run it. No category list is hard-coded for reporting.

## 4. How legacy values reach the current taxonomy

| Legacy value | Treatment |
|---|---|
| Exact activity equivalents (Sleep, Workout, Meditation, Family, Friends off-device, Motorcycle, Guitar, Reading, TV) | Current category › subcategory, Domain / Energy / Goal of that current activity (`Dim_Activity`). HIGH_CONFIDENCE. |
| Broad device rows with a defensible single destination (Personal Admin → Productivity & Finance; Communication → Social Networking › Messaging; Learning → Education; Health apps → Health & Fitness) | Current category, subcategory only where known. REVIEW_REQUIRED, included in totals and flagged. |
| Broad device rows spanning several current categories (Work / Study; Entertainment except TV) | `MULTI_CATEGORY` with `candidate_categories`. **No single category is asserted**; shown as *unsplit legacy* rows, outside every category total, and budgeted in the planning line that contains all candidates. |
| Household / Chores template | Off-Device Life › Untracked Maintenance (REVIEW_REQUIRED): the current system keeps meals, hygiene and chores in Off-Device Life. Reported as an estimate, never as observed activity. |
| Legacy unresolved time (capacity − accounted) | Off-Device Life › Untracked Maintenance with the residual rule and the Domain / Energy / Goal of the current synthetic rows (read from the current data). |
| Vacation balancing fill, Hobbies 'Videos' | UNMAPPED — kept as *unmapped legacy*, outside every category and dimension total. |
| Legacy energy / goal labels | Activity-level values where an exact equivalent exists; otherwise the label map (Deep Work → Deep, Shallow Work → Shallow, Passive → Passive, High-Value → Investment, Maintenance → Maintenance, Low-Value / Drift → Consumption). 'Recovery / Rest' is **never** label-mapped; unmappable values appear as `(Unmapped legacy)`. |
| Legacy devices | Phone → PHONE, Laptop → LAPTOP, PC Desktop → DESKTOP, TV → TV, Off-Device → manually recorded off-device (not the inferred Off-Device Life residual). COMPUTERS_TOTAL = laptop + desktop, never added twice; August 2026 has no split. |
| Legacy 'Other' | Not a timesheet row: only the legacy Report's capacity − recorded with mis-entered capacities. Not mapped; its time is the Off-Device Life residual. |
| Legacy Report targets | Not used. No current target is configured. |

Mapping confidence in reports: categories, domains, energies and goals
include HIGH_CONFIDENCE and REVIEW_REQUIRED values and show the legacy
confidence mix next to each value. Each current category also carries a
**legacy evidence** status: `COMPARABLE_WITH_CAVEATS`,
`COMPARABLE_WITH_RESERVATIONS`, `PARTIAL_LEGACY_EVIDENCE` (a candidate of an
unsplit legacy record set), `CAPTURE_DIFFERS` (mostly current-only capture) or
`CURRENT_ONLY_NO_LEGACY_EVIDENCE` (legacy values shown as n/a, never as zero).

## 5. Mapping changes (2026-10, documented in the mapping table)

| Legacy key | Before | After | Evidence |
|---|---|---|---|
| Work / Study (device rows) | Productivity & Finance, REVIEW_REQUIRED | MULTI_CATEGORY {Productivity & Finance, Education}; domain REVIEW_REQUIRED | Legacy definition includes formal study; the legacy laptop Learning row holds negligible time, so laptop study was recorded here. |
| Entertainment (device rows, not TV) | Entertainment, REVIEW_REQUIRED | MULTI_CATEGORY {Entertainment, Games, Social Networking}; domain REVIEW_REQUIRED | Legacy definition: streaming, gaming, browsing, social media. |
| Relationships / Social · Friends · PC Desktop | UNMAPPED | Games, REVIEW_REQUIRED | Legacy report comment 'Friends & Social gaming'; the current system records PC gaming as Games. |

Effect on the pipeline: bridge groups, accounting, devices and anchors are
unchanged; only the domain-mapped share in `Historical_Monthly_*` changes
(Work / Study and Entertainment domains are now review-required).

## 6. How to read unmapped historical time

* *Unsplit legacy* and *unmapped legacy* rows are mapping gaps, not
  categories. They stay visible in the accounting (`mapped / unsplit /
  unmapped` lines), the category table, the Final Detail Analysis and
  `Historical_Mapping_Review_Register.csv`.
* Identity per month: analytical activity = recorded + Off-Device Life
  residual = Σ current categories + unsplit + unmapped.
* Categories that are candidates of unsplit records are understated in legacy
  months; compare their planning line (e.g. *Productivity & Finance +
  Education*) across regimes instead.

## 7. Relation to the monthly reports

| Monthly (September 2026) | Annual |
|---|---|
| `Standard_Report_<period>.txt` (`standard_report.py`) | `Annual_Standard_Report_<window>.md` — same sections, KPI definitions and `build_recommendations` rules, per regime segment |
| `Exploratory_Analysis_<period>.txt` (`exploratory_analysis.py`) | `Annual_Exploratory_Analysis_<window>.md` — same sections; reuses `exploratory_analysis.taxonomy_candidates` |
| `Final_Detail_Analysis_*_Hierarchy.csv` | `Annual_Final_Detail_Analysis_<window>.md` + `tables/Annual_Final_Detail_Hierarchy.csv` (+ mapping status) |

The annual analysis validates that its September 2026 values equal the
closed `Final_Detail_Analysis_*_Summary.csv` (all categories, domains,
energies, goals) and time universe. August 2026 stays TRANSITION / REVIEW.

## 8. How to run

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe .\historical_pipeline.py   # (re)build output/Historical
.\.venv\Scripts\python.exe .\historical_annual.py     # annual analysis + reports
.\.venv\Scripts\python.exe .\historical_annual.py --check-only
```

Modules: `historical_canonical.py` (taxonomy + canonical records),
`historical_annual.py` (tables, findings, budget, validation),
`historical_annual_reports.py` (Markdown + SVG). Stdlib only; the pipeline
step needs `openpyxl`.

## 9. Outputs (`output/Historical/Annual_2025-09_to_2026-09/`, git-ignored)

`00_INDEX.md` lists every file. Reports: Annual Standard Report, Annual
Exploratory Analysis, Annual Final Detail Analysis, Historical Annual Analysis
(patterns + budgeting), Historical Mapping & Comparability Report. Tables in
`tables/` (A accounting, B categories / subcategories, C devices, D anchor
activities, F domain / energy / goal / KPI, G monthly trends, H mapping &
comparability, I review register, J budget baseline / scenarios / decisions,
validation). Charts in `charts/`. Superseded outputs are kept under
`output/Historical/_superseded/`.

## 10. Segments, statistics and budgeting rules

| Segment | Months |
|---|---|
| `MIXED_13M` | all 13 — arithmetic total of mixed methods, **descriptive only** |
| `LEGACY_ALL` | 2025-09 .. 2026-07 |
| `LEGACY_BASELINE` | `config.historical.ANNUAL_BASELINE_*` (default 2025-10 .. 2026-06) |
| `TRANSITION` | 2026-08 |
| `CURRENT` | 2026-09 |

Per-day = minutes ÷ calendar days; shares = % of the segment's analytical
activity unless stated. Statistics over baseline months: median, P25–P75, CV,
OLS trend (RISING / FALLING when ≥ max(10 min/day, 20 % of mean); VOLATILE
when CV ≥ 0.45). Findings: OBSERVED_PATTERN / BEHAVIORAL_HYPOTHESIS /
POSSIBLE_METHODOLOGICAL_EFFECT / INSUFFICIENT_DATA.

Planning lines are an exhaustive partition of analytical activity defined
only by current values (`historical_canonical.BUDGET_LINE_ORDER`), e.g.
*Health & Fitness › Sleep*, *Off-Device Life*, *Productivity & Finance +
Education*, *Games + Entertainment + Social Networking (consumption)*. Each
line shows: historical observed average, comparable baseline, planning range
(only when comparable, ≥ 6 baseline months and the current value lies in
[0.8 × min, 1.2 × max] of baseline months; rounded outward to 15 min), user
target (always `NOT SET`) and missing information. Scenarios: Sustainable,
Goal-focused, Conservative; reserve 60 / 60 / 120 min/day; remaining minutes
are never auto-assigned; Σ lower bounds + reserve ≤ 1,440 min/day is
validated.

## 11. Validation

Inputs match the pipeline manifest · bridge values exist in the current
taxonomy · primary categories = current taxonomy · no invented subcategory /
domain / energy / goal · no bridge-group identifier in primary reports,
tables or charts · September 2026 equals the closed Final Detail Analysis and
time universe · August = TRANSITION · 13 months / days complete · universe
identities (categories + gaps, domains, energies, goals, planning lines,
subcategory roll-up, devices, laptop + desktop) · each legacy cell counted
once · anchors equal the pipeline · inventory and review register cover every
legacy minute · denominators · scenario capacity · no target set ·
deterministic double build · SVG well-formed · protected files unchanged
(workbooks, Final folders, September Standard Report / EDA, pipeline outputs,
taxonomy). Tests: `tests/test_historical_annual.py`.

## 12. Known limitations

* One closed current month; current-regime variability is unknown.
* August 2026 is read as published (known double-applied Habit adjustments).
* Unsplit legacy records understate several current categories in legacy months.
* Legacy Off-Device Life includes a template estimate; the current one is a residual.
* The current phone social bucket does not separate messaging from feeds.
* `historical_pipeline.py` raises `UnicodeEncodeError` on a cp1252 console
  after writing its outputs; set `PYTHONIOENCODING=utf-8`.

## 13. Proxy allocations (estimates for budgeting)

Unsplit and review-required legacy groups can be allocated to current
categories with explicit, **user-approved percentages**
(`historical_proxy_allocation.py`). These are estimates, kept in a separate
layer; the workbooks, normalized records, mapping tables, closed periods and
the authoritative annual outputs above are never modified.

```powershell
.\.venv\Scripts\python.exe .\historical_proxy_allocation.py generate   # worksheet (.xlsx + .csv) + review summary
.\.venv\Scripts\python.exe .\historical_proxy_allocation.py validate   # sums = 100 %, approvals, targets, allocated hours
.\.venv\Scripts\python.exe .\historical_proxy_allocation.py apply      # APPROVED decisions only -> Proxy_Allocation/applied/
```

* **Decision groups** share one interpretation and allocation rule (legacy
  category × device × legacy energy/goal row label; household split by
  template vs non-template and nap label; record-level groups for annotated
  cells). Category and energy decisions are independent: a record belongs to
  at most one of each.
* **Worksheet** (one row per decision × candidate target): evidence
  (legacy definition, source cell comments, existing mapping, current-system
  same-device composition as reference only), optional `suggested_pct` only
  where evidence supports it, and editable `user_pct`, `user_rationale`,
  `user_confidence`, `allocation_basis` (DIRECT_MAPPING / USER_RECOLLECTION /
  ANALYTICAL_PROXY), `approval_status` (PENDING / APPROVED / REJECTED).
  Targets must exist in the current taxonomy; `(keep unmapped)` is allowed.
* **Validation:** per decision, percentages 0–100 summing to 100; APPROVED
  needs rationale, confidence and basis; the worksheet is stale if the
  normalized records changed. `generate` keeps user entries (by `row_id`)
  and backs up the previous file.
* **Apply:** only APPROVED decisions are allocated; every source minute is
  allocated exactly once (checked). Category pieces take Domain / Energy /
  Goal from the current activity (`Dim_Activity`) when a subcategory is
  given, the Off-Device Life profile for Off-Device Life, else the legacy
  label values with the target category's default domain. Outputs: record-level
  layer, monthly direct vs adjusted vs proxy minutes, and a category baseline
  with both the **direct** and the **proxy-adjusted** median, P25–P75, CV,
  weekday / weekend and the **proxy share** (HEAVY ≥ 30 %). Historical
  averages remain references, not targets.

## 14. Updating when a new month closes

1. Close the month (`PERIOD_CLOSE_CHECKLIST.md`).
2. Add the new `Final/<period>` under `REGIME_CURRENT` in the historical
   pipeline (`HISTORICAL_BRIDGE.md` §10) and widen `ANALYSIS_END_MONTH`.
3. Re-run both commands in §8. The annual folder name follows the window.
4. After 3+ closed current months, revisit lines without a planning range and
   decisions D-H01 … D-H03.
