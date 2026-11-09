# Process Map

## High-level architecture

```text
DESKTOP PC                                LAPTOP / central
─────────────────────────────            ─────────────────────────────
Desktop Tracker Collector                 System Tracker (this repo)
  ActivityWatch (localhost:5600)
    → Raw → Fact_Time → Daily_Time
    → validate → export package
          │                                ┌─ Laptop ActivityWatch
          │  portable, versioned           │    activitywatch_laptop.py
          │  export package                │    → activitywatch_pipeline.py
          │                                │    → daily_time_builder.py
          └───────────────────────────────►│─ import_desktop_activitywatch.py
                                           │    (contract check, closed-period
                                           │     protection, import receipt)
                                           ├─ iPhone Screen Time
                                           │    screenshots → ingest → review
                                           │    → promote → builder
                                           └─ Habit / Off-Device
                                                screenshots → ingest → review
                                                → builder
                                                       │
                                                       ▼
                                            Taxonomy reference
                                            (output/Reference/Taxonomy/)
                                                       │
                                                       ▼
                                     integrated_daily_time_builder.py
                                       → Integrated_Daily_Time_<date>.csv
                                                       │
                              integration_analysis.py + integration_human_review.py
                                                       │
                              manual review → habit_manual_adjustments.py
                                                       │
                                                       ▼
                                            final_analysis.py
                                              ├── Final Analysis
                                              ├── Correction Audit
                                              ├── Daily Reconciliation
                                              ├── Source Reconciliation
                                              └── Time Universe
                                                       │
                          ┌────────────────────────────┼────────────────────────────┐
                          ▼                            ▼                            ▼
                  detail_analysis.py          exploratory_analysis.py       standard_report.py
                  (hierarchy)                 (EDA, taxonomy candidates)     (recurring KPIs)
                          └────────────────────────────┼────────────────────────────┘
                                                       ▼
                                        period_close_validator.py
                                                       ▼
                     variation_analysis.py → budget_variance_analysis.py
                     (period vs previous)     (Actual vs Budget; reads the
                                               snapshot in output/Reference/Budget/)
                                                       ▼
                              Period-Close Checklist → archive → historical → commit
```

## Executable dependency order (enforced by the code)

```
laptop ActivityWatch    +    import desktop package
        ↓  (output/Raw + output/Fact + output/Daily for each device)
daily_time_builder (as needed)  +  fact_time_validator
iPhone: ingest → review → promote → categorize residuals → builder
Habit:  ingest → review → builder
        ↓  (output/Daily/Time/<source>/Daily_Time_<date>.csv for every source)
build_time_taxonomy                       (validates output/Reference/Taxonomy/)
        ↓
integrated_daily_time_builder             → output/Integrated/Integrated_Daily_Time_*.csv
        ↓  (REQUIRED before the next step)
integration_analysis  +  integration_human_review
        ↓
manual review  →  habit_manual_adjustments  →  rebuild integrated daily
        ↓
final_analysis                            → output/Integrated/Analysis/Final/<range>/
        ↓
detail_analysis  →  exploratory_analysis
        ↓  (taxonomy loop only for a genuine mapping error:
        ↓   edit reference → build_time_taxonomy → final → detail → EDA → report)
standard_report
        ↓
period_close_validator  --require-eda --require-standard-report
        ↓
variation_analysis  →  budget_variance_analysis   (budget periods only;
        ↓              then period_close_validator --require-budget)
Period-Close Checklist → archive → historical → commit

Budget snapshot (independent, when the approved budget changes):
budget_import  →  output/Reference/Budget/Budget_*_<version>.*
```

## Machine map

| Step | Runs on | Why |
|---|---|---|
| `activitywatch_laptop.py`, `activitywatch_pipeline.py` | Laptop (`<laptop device>`) | reads the local ActivityWatch server; `activitywatch_machine.validate_machine` refuses elsewhere |
| Desktop collection (`collect_desktop.py`, `export_package.py`) | Desktop (`<desktop device>`) | separate project; reads the local ActivityWatch server |
| `import_desktop_activitywatch.py` | Laptop / central | stdlib-only; consumes the export package |
| `daily_time_builder.py`, `fact_time_validator.py`, taxonomy, integration, final, detail, EDA, report, close | Laptop / central | pure file processing over `output/` |
| iPhone / Habit ingest | Laptop / central | needs `OPENAI_API_KEY` + internet |

## Data ownership

| Layer | Responsibility |
|---|---|
| Raw source | Preserve source truth |
| Desktop Tracker Collector | Normalize + validate + package desktop ActivityWatch |
| System Tracker import | Verify contract + protect closed periods + land desktop data |
| Source processor | Normalize one source |
| Integration | Combine sources without losing device/source identity |
| Taxonomy | Define analytical meaning (reference layer only) |
| Final analysis | Apply reconciliation and corrections |
| Time universe | Reconcile elapsed clock time |
| Hierarchy | Aggregate analytical dimensions |
| EDA | Investigate patterns (no automatic taxonomy change) |
| Standard Report | Summarize recurring KPIs |
| Historical layer | Compare periods |
| Budget snapshot | Freeze the approved budget (`budget_import.py`); never read the workbook in the pipeline |
| Actual vs Budget | Compare Final with the period budget on covered days (`budget_variance_analysis.py`) |

## Key invariant

```text
Clock Capacity = Unique Tracked + Off-Device Life
```

Analytical activity is not a clock ledger — it may exceed capacity where
sources overlap.
