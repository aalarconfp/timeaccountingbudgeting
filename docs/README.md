# Time Accounting & Budgeting System — Operational Documentation

Operational documentation for integrating, reconciling, analyzing, and
reporting personal time data across ActivityWatch (laptop + desktop), Apple
Screen Time, and Habit / Off-Device tracking. Device IDs and the
shared-device attribution rule are configured per installation (see
`DATA_COLLECTION.md` → Device configuration).

## Two projects

| Project | Repo | Responsibility |
|---|---|---|
| **System Tracker** | this repo | Import desktop data · laptop ActivityWatch · iPhone · Habit · taxonomy · integration · reconciliation · final / detail / EDA analysis · Standard Report · period close · historical integration · budget snapshot and Actual vs Budget |
| **Desktop Tracker Collector** | `../Desktop Tracker Collector/` | Desktop ActivityWatch only: collect → Raw → Fact_Time → Daily_Time → validate → portable export package |

The desktop PC does not need this repo. It produces a versioned export package
that System Tracker imports with `import_desktop_activitywatch.py`.

## Canonical workflow

See **`docs/SYSTEM_TRACKER_RUNBOOK.md`** — the single operational runbook.
Summary:

1. Prepare period + environment
2. Collect: laptop ActivityWatch · **import desktop package** · iPhone screenshots · Habit screenshots
3. Validate source coverage *(human gate)*
4. Process: Daily_Time · iPhone (ingest → review → promote → build) · Habit (ingest → review → build) · taxonomy
5. Integrate: `integrated_daily_time_builder.py` → `integration_analysis.py` + `integration_human_review.py`
6. Manual integration review *(human gate)* → apply adjustments → rebuild integrated daily
7. `final_analysis.py` → review correction audit / daily reconciliation / time universe *(human gate)*
8. `detail_analysis.py` → `exploratory_analysis.py` → taxonomy-review loop only for a genuine mapping error
9. `standard_report.py` → `period_close_validator.py`
9b. `variation_analysis.py` → `budget_variance_analysis.py` (budget periods; `--require-budget` at close)
10. Period-Close Checklist → archive → historical integration → `dashboard_builder.py` → commit

## Accounting model

```
Clock Capacity = Unique Tracked Time + Off-Device Life
Unique Tracked Time = Corrected Tracked Time - Tracked Overlap
```

Analytical activity can exceed clock capacity because multiple sources observe
the same elapsed time. Taxonomy: 12 categories, 7 domains, 5 energies, 3 goals.

## Documentation

- `SYSTEM_TRACKER_RUNBOOK.md` — canonical operating procedure
- `PERIOD_CLOSE_CHECKLIST.md` — mandatory period-close checklist
- `PROCESS_MAP.md` — architecture, dependency graph, machine map
- `DATA_COLLECTION.md` — source collection and the desktop import
- `ACTIVITYWATCH_LAPTOP.md` — laptop machine / ActivityWatch facts
- `SOURCE_PROCESSING.md` — per-source normalization
- `INTEGRATION_AND_RECONCILIATION.md` — consolidation and adjustment rules
- `TAXONOMY_AND_EDA.md` — taxonomy, hierarchy, EDA
- `REPORTING.md` — Standard Report, recurring KPI definitions and the consolidated dashboard- `INTERIM_REVIEW_PROCEDURE.md` — ~15-day operational review vs. monthly close; Notion report flow
- `HISTORICAL_DATA_INTEGRATION.md` — one-year historical integration procedure
- `HISTORICAL_BRIDGE.md` — Phase 2 legacy-to-current bridge: architecture, mapping tables, accounting rules
- `HISTORICAL_ANNUAL_ANALYSIS.md` — annual analysis in the current taxonomy (Standard Report / EDA / Final Detail analogues), legacy-to-current mapping, budget baseline, scenario templates and the proxy-allocation worksheet workflow
- `BUDGET_DESIGN.md` — budget snapshot, period proration, coverage and Actual vs Budget method
- `TROUBLESHOOTING.md` — common failures and recovery
- `CHANGE_CONTROL.md` — rules for changing the analytical system
- `GLOSSARY.md` — canonical terminology
- `PYTHON_ENVIRONMENT.md` — per-machine Python environment
- `POWER_BI_DASHBOARD_FUTURE.md` — future dashboard notes

Desktop collection is documented in the Desktop Tracker Collector's own
`docs/` (`COLLECTION_RUNBOOK.md`, `EXPORT_CONTRACT.md`, `DESKTOP_MACHINE.md`,
`TROUBLESHOOTING.md`).
