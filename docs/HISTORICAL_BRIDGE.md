# Historical Bridge (Phase 2) — Transformation Rules

Status: implemented, pending human review (not committed).
Change class: D — new feature (`CHANGE_CONTROL.md`). No current-system module,
input or output is modified.

## 1. Purpose

Make the legacy manual time tracking (Sep 2025 – Jul 2026) comparable with the
current System Tracker accounting (Aug 2026 transition, Sep 2026 closed) for a
monthly and period analysis **without** forcing legacy data to look like
current data. Every number must be traceable to a workbook cell or a Final
output row.

## 2. Architecture decision — Option C (both layers)

| Option | Verdict | Why |
|---|---|---|
| A. Direct transform into the current schema | Rejected | Legacy categories (9) are not ActivityWatch categories (13); legacy energy 'Recovery / Rest' and goal 'High-Value' bundle activities the current taxonomy splits; device rows have no real subcategory (column B is an XLOOKUP of example text); household time is a template estimate the current system never records. Forcing these into current columns would invent precision. |
| B. Historical schema only + ad-hoc bridge | Rejected | Preserves meaning but gives no common analytical key, so every comparison would be hand-built. |
| **C. Historical normalized layer + canonical bridge layer** | **Chosen** | Layer 1 keeps every legacy value verbatim with provenance. Layer 2 adds canonical values only where defensible (each with a status) and a shared *bridge group* that both legacy and current records map into. Comparisons are done on the bridge, with explicit comparability per metric. |

## 3. Data model and locations

| Location | Content | Git |
|---|---|---|
| `input/Historical/Legacy/*.xlsx` | Byte-identical copies of the three legacy workbooks (immutable evidence; SHA-256 in the run manifest) | ignored (`input/`) |
| `config/historical.py` | Workbook/sheet manifest, periods, paths, excluded sheets | tracked |
| `config/historical_bridge/*.csv` | Mapping tables (labels only, no personal data) | tracked |
| `historical_legacy_loader.py` | Read-only workbook parser | tracked |
| `historical_bridge.py` | Normalization, bridge, daily accounting rules | tracked |
| `historical_monthly.py` | Monthly / MoM / period aggregation, comparability | tracked |
| `historical_pipeline.py` | CLI, validation, outputs, report | tracked |
| `historical_canonical.py` | Current-taxonomy loader, bridge validation, canonical records for reporting | tracked |
| `historical_annual.py`, `historical_annual_reports.py` | Annual analysis and reports in the current taxonomy | tracked |
| `tests/test_historical_pipeline.py` | Synthetic-workbook tests + optional real-data integration test | tracked |
| `output/Historical/` | All generated datasets | ignored (`output/`) |

Run: `python historical_pipeline.py` (or `--check-only`; `--legacy-dir` to read
the workbooks from another folder). Requires `openpyxl`.

### Outputs (`output/Historical/`)

| File | Grain |
|---|---|
| `Historical_Normalized_Records.csv` | one row per legacy timesheet day-cell (incl. zero / out-of-period cells) |
| `Historical_Bridge_Records.csv` | one row per included legacy cell + one per current Final row, common bridge columns |
| `Historical_Legacy_Daily.csv`, `Historical_Current_Daily.csv` | one row per day |
| `Historical_Legacy_Report_Values.csv` | every legacy Report value used as evidence |
| `Historical_Legacy_Sheet_Layout.csv` | detected layout per sheet |
| `Historical_Repeated_Entries.csv` | repeated identical off-device entries |
| `Historical_Monthly_Analysis.csv` | one row per month (wide) |
| `Historical_Monthly_Long.csv` | month × dimension × value, with comparability |
| `Historical_Monthly_Comparison.csv` | month-over-month changes with change type |
| `Historical_Period_Analysis.csv` | period aggregates by regime segment |
| `Historical_Taxonomy_Mapping.csv` | mapping actually applied, with minutes |
| `Historical_Comparability_Matrix.csv` | Legacy / Transition / Current / Comparable? |
| `Historical_Validation.csv`, `Historical_Reconciliation_Report.md`, `Historical_Run_Manifest.json` | audit |

## 4. Normalization (layer 1)

* Sheets used: `Timesheet 9–12` (2025), `Timesheet 1–6` (2026 T1), `Timesheet 1` (2026 T2) and the matching monthly `Report` sheets. The manifest month is validated against each sheet's start-date cell `B4`.
* Day columns: from `G` to the column before the sheet's total column (the column whose row-11 formula is `=SUM(G11:…)/60`). Dates come from row 9 (cached values).
* Detail rows: rows from 11 to the `Total Hours:` row with a category in column A, excluding subtotal rows (column E ends with `:`).
* Values: Excel's cached value; the literal entry (`=60*3+15`, `45`) is kept in `raw_entry` and evaluated independently with a safe arithmetic evaluator; `cache_check` records MATCH/MISMATCH. Reference formulas are never evaluated.
* `record_id = <workbook code>|<sheet>|<cell>` — stable and unique.
* Legacy values are preserved verbatim: category, subcategory (+ whether it was a literal or the XLOOKUP example), energy, goal, device, time of day, and the Domain label where a 2026 sheet carries one.

## 5. Bridge (layer 2)

Mapping tables (`config/historical_bridge/`):

* `bridge_groups.csv` — the comparison taxonomy: WORK_STUDY, LEARNING, PERSONAL_ADMIN, COMMUNICATION, ENTERTAINMENT, DIGITAL_SOCIAL_UNSPLIT (current only), HEALTH_WELLNESS, RELATIONSHIPS_SOCIAL, HOBBIES_CREATIVE, HOUSEHOLD_DAILY_LIVING (legacy only), UNSPECIFIED_VACATION (legacy only), UTILITIES_SYSTEM / UNCLASSIFIED (current only), UNMAPPED; rolled up into blocks PRODUCTIVE, DIGITAL_LEISURE_AND_SOCIAL, HEALTH, SOCIAL_IN_PERSON, HOBBIES, DAILY_LIVING_AND_RESIDUAL, UNCLASSIFIED_AND_SYSTEM.
* `legacy_activity_bridge.csv` — key (legacy category, subcategory, device) with wildcard fallback in this order: exact → device `*` → subcategory `*` → both `*`. Gives bridge group and canonical category / subcategory / domain / energy / goal, each with `HIGH_CONFIDENCE`, `REVIEW_REQUIRED` or `UNMAPPED`. `LABEL` means "use the legacy row label via the label map".
* `legacy_label_maps.csv` — energy, goal, device and time-of-day label maps.
* `current_activity_bridge.csv` — key (current source, category, subcategory) with wildcard fallback.

Principles:

1. Where an **exact activity equivalent** exists (Sleep, Workout, Meditation, Family, Friends, Motorcycle, Guitar, Reading), the canonical energy / goal / domain are the current taxonomy's values for that activity (`Dim_Activity.csv`), and the legacy label is preserved. Example: legacy Sleep `High-Value` → canonical goal `Maintenance`.
2. Otherwise the legacy **row label** is mapped (Deep Work→Deep, Shallow Work→Shallow, Passive→Passive, High-Value→Investment, Maintenance→Maintenance, Low-Value / Drift→Consumption).
3. Legacy `Recovery / Rest` is **never** label-mapped (it covers sleep, workout, eating, hobbies, phone learning) → `REVIEW_REQUIRED`, blank canonical energy.
4. Ambiguous concepts are kept in their legacy group and flagged, never silently reassigned (Videos, PC 'Friends', Household, Vacations, Personal Admin off-device).
5. Time of day is **not comparable** (row defaults, changed Jan 2026; no current dimension).
6. Two concepts per record, never mixed: the **canonical current values**
   (`canonical_category` / `canonical_subcategory` / domain / energy / goal /
   device, each with a status) are what reports aggregate; the **bridge
   group** is an internal comparability device and is not a reporting
   taxonomy (`HISTORICAL_ANNUAL_ANALYSIS.md` §2).
7. When a legacy row spans several current categories and carries no
   subcategory, the rule uses `category_status = MULTI_CATEGORY` with
   `candidate_categories` (pipe-separated) and leaves `canonical_category`
   blank — no single current category is asserted.
8. Every canonical value must exist in the current taxonomy
   (`output/Reference/Taxonomy`, the files `final_analysis.py` uses); the
   pipeline validates this (`historical_canonical.validate_bridge_against_taxonomy`).

Mapping change log: 2026-10 — Work / Study device rows → MULTI_CATEGORY
{Productivity & Finance, Education}; Entertainment device rows (not TV) →
MULTI_CATEGORY {Entertainment, Games, Social Networking}; PC 'Friends' →
Games (REVIEW_REQUIRED). Rationale in the table; details in
`HISTORICAL_ANNUAL_ANALYSIS.md` §5.

## 6. Accounting rules

| ID | Rule | Rationale |
|---|---|---|
| H-PER-1 | Only cells dated inside the sheet month are counted; spill-over columns are kept with `EXCLUDED_OUT_OF_PERIOD`. | Nov 2025, Feb 2026, Jun 2026 sheets contain next-month columns (all zero). |
| H-STD-1 | Household / Chores cells whose exact entry repeats ≥ 5 times in the row-month are `STANDARD_DAILY_ALLOCATION`; other household cells `ESTIMATE_NON_TEMPLATE`. Kept in recorded time, reported separately, excluded from `specified_activity`. | Entries reproduce fixed compositions that are also the household budget in the legacy reports → planned values entered as actuals. |
| H-REP-1 | Other off-device entries repeated ≥ 5 times in a row-month → `REPEATED_ROUTINE_VALUE` (informational). | Fixed-length routines (meditation 15 min, reading 15 min) are plausible; evidence insufficient to call them templates. |
| H-VAC-1 | 'Vacations' cells → `BALANCING_FILL`, group UNSPECIFIED_VACATION. | Used only to fill vacation days towards 24 h. |
| H-OVR-1 | Day overlap = max(0, recorded − 1440); accounted = recorded − overlap; unresolved = 1440 − accounted. No record deleted or capped. | Same rule as the current system's Tracked Overlap (Final_Daily_Reconciliation). |
| H-DAY-1 | Day status NORMAL_TOLERANCE if unresolved ≤ 120 min else REVIEW; flags OVER_CAPACITY, INCOMPLETE_OFF_DEVICE. | Historical rule keeps 120 min; `final_analysis.py` uses 240 min since 2026-10. |
| H-RPT-1 | Detail total is the analytical base; legacy Report totals are evidence. | Report excludes rows lacking a row-total formula (row 51 in Nov/Dec 2025) and uses mis-entered capacities (Oct–Dec 2025, Jul 2026). |

Current periods: Final_Analysis rows mapped to bridge groups; `RESIDUAL`
(Off-Device Life) is unresolved time, not recorded time. Recorded =
Corrected Tracked Time, overlap = Tracked Overlap, accounted = Unique Tracked
Time — read from `Final_Time_Universe_Reconciliation`. August is read as
published (known issues stay visible); September is read-only and hashed
before and after every run.

## 7. Monthly metrics

Per month: calendar capacity, recorded, overlap, accounted, unresolved,
coverage %, specified activity / specified coverage % (excludes household and
vacation fill — the like-for-like measure), standard allocation, legacy
detail vs reported totals, bridge groups, blocks, device classes, activity
anchors (exact equivalents), canonical energy / goal / domain with mapped
share, day counts by status. Values are also expressed as min/day so months of
different length compare.

## 8. Change classification (month over month)

| Type | When |
|---|---|
| NOT_COMPARABLE | metric not comparable, or exists in one regime only |
| METHODOLOGICAL_BREAK | the two months belong to different regimes |
| POSSIBLE_METHODOLOGICAL | same regime, but a documented break (section 9) affects the metric |
| BEHAVIORAL_CANDIDATE | same regime, comparable metric, no documented break |

Accounting metrics (recorded / accounted / unresolved) are also
POSSIBLE_METHODOLOGICAL when either month contains vacation balancing fill
(H-VAC-1); compare `specified_activity` for those months. UNSPECIFIED_VACATION
itself is NOT_COMPARABLE (a filler, not behavior).

`material = Y` when |Δ| ≥ 10 % and ≥ 10 min/day. A behavioral candidate is a
change in *recorded* behavior under a manual method — not proof of behavior.

## 9. Methodology breaks

Defined in `historical_monthly.METHOD_BREAKS`: Oct 2025 (household template,
reading/journaling label), Nov 2025 (nap folded into household; off-device
Personal Admin row), Jan 2026 (new workbook, phone rows consolidated,
Vacations and PC 'Friends' rows), Jul 2026 (27–31 off-device not recorded),
Aug 2026 (regime change → transition), Sep 2026 (closed current accounting).

## 10. Adding a new closed period later

Do not add current months to the legacy pipeline. Once a period passes the
Period-Close Checklist, extend `historical_pipeline.build_all` with the new
`Final/<period>` folder under `REGIME_CURRENT` and re-run; the legacy layer is
unchanged. Then re-run the annual analysis (`HISTORICAL_ANNUAL_ANALYSIS.md`),
which consumes these outputs.
