# Time Accounting & Budgeting System

> Personal time intelligence: **track what happened → account for every hour →
> budget where time should go → measure the variance.**

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Tests](https://img.shields.io/badge/tests-unittest-brightgreen)
![Status](https://img.shields.io/badge/status-active_development-green)

The system turns fragmented activity data (computer, phone and manual habit
logs) into one auditable model of the 24-hour day. It treats time as a finite
resource: every month closes like a set of books, and a versioned time budget
is compared with what actually happened.

---

## What it delivers

| Layer | Question | Output |
|---|---|---|
| **Time accounting** | Where did my time go? | Reconciled monthly close: every minute classified, overlaps removed, the untracked remainder made explicit |
| **Analysis** | What changed and why? | Standard Report, exploratory analysis, month-to-month variation, 13-month historical trends |
| **Time budgeting** | Where should my time go? | Versioned budget in hours/week by phase; Actual vs Budget with favorable/unfavorable assessment and capture alerts |
| **Dashboard** | What does it all look like together? | One self-contained HTML page with every closed month, the full taxonomy, history, comparisons, budget and close status |

## How it works

```text
Sources ─► Normalize ─► Reconcile ─► Classify ─► Close the month ─► Analyze ─► Budget vs Actual ─► Dashboard
              (each step is reproducible; human review only where evidence is insufficient)
```

The accounting identity behind every period:

```text
Clock Capacity (days × 24 h) = Unique Tracked Time + Off-Device Life
```

Tracked overlap is counted once. Time no source observed stays visible as
*Off-Device Life* instead of being guessed.

Every activity is classified on five dimensions: **Category › Subcategory**,
**Domain** (Physical, Vocational, Relational, …), **Energy** (Deep, Active,
Recovery, Shallow, Passive) and **Goal** (Investment, Maintenance,
Consumption).

The step-by-step procedure lives in one place:
[`docs/SYSTEM_TRACKER_RUNBOOK.md`](docs/SYSTEM_TRACKER_RUNBOOK.md).

## Time budget

An approved workbook is imported as an immutable, versioned snapshot
(Device × Category × Subcategory, hours/week per phase). Each budget period is
compared on the days it covers:

- **Variance = Actual − Budget.** More Investment and less Consumption is
  favorable; Maintenance is not judged, except Sleep against its floor.
- **Budget with no recorded actual is a capture alert**, never a saving.
- KPIs: investment ratio, discretionary consumption, digital-leisure and sleep
  floors, top deviations, coverage.

Method: [`docs/BUDGET_DESIGN.md`](docs/BUDGET_DESIGN.md).

## Dashboard

```powershell
python .\dashboard_builder.py      # → output/Dashboard/Time_Dashboard.html
```

Rebuild it after each monthly close. It discovers every period automatically
and works offline. It has seven views: **Overview, History, Taxonomy, Daily,
Compare, Budget, Quality & close.** Deep links such as
`Time_Dashboard.html#budget` or `#taxonomy/2026-09` open a view or month
directly.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy config\local_settings.example.json config\local_settings.json   # devices, shared-device rule
python -m unittest discover tests
```

Then follow the runbook for a monthly close. `input/` and `output/` hold
personal data and are not version-controlled.

## Design principles

- **Accounting before optimization:** establish what happened before deciding what should.
- **Evidence over assumption:** observed, inferred, estimated and manually adjusted time stay distinguishable.
- **Explicit adjustments:** human corrections are auditable inputs, never hidden source edits.
- **Transparent uncertainty over false precision.**
- **Reproducible periods:** a closed month can be rebuilt from versioned code and the same inputs.

## Repository

```text
*.py           pipeline modules (collection → reconciliation → analysis → close → budget → dashboard)
config/        taxonomy, budget settings, local-settings template
dashboard/     HTML template of the consolidated dashboard
docs/          runbook, methods, checklists
tests/         automated tests
input/ output/ local data (git-ignored)
```

Documentation index: [`docs/README.md`](docs/README.md).

## Status & roadmap

**Implemented:** multi-source tracking, reconciliation and time-universe
accounting; taxonomy; monthly close with validation; historical integration
(13 months); versioned time budget and Actual vs Budget; consolidated
dashboard.

**Next:** budget-variance trends across periods; KPI and decision layer;
forecasting.
