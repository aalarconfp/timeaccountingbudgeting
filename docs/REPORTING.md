# Reporting SOP

## Reporting layers

### Final Analysis

Technical reconciled dataset.

### EDA

Deep analytical diagnostics.

### Standard Report

Recurring management summary.

## Standard Report KPI set

### Time accounting

- Clock Capacity
- Unique Tracked
- Off-Device Life
- Tracked Overlap
- Analytical Activity

### Goal

- Investment
- Maintenance
- Consumption
- Investment ratio

### Energy

- Recovery
- Deep
- Active
- Shallow
- Passive

### Discretionary consumption proxy

Current proxy:

- Games
- Entertainment
- Social Networking

when classified as Goal=Consumption.

## Standard Report philosophy

Keep it small.

The report should identify:

1. where time went;
2. investment/maintenance/consumption balance;
3. recovery;
4. discretionary drains;
5. next-period attention areas.

Use EDA for everything deeper.

## Actual vs Budget

Layer on top of the Final analysis (`BUDGET_DESIGN.md`). Runs after
`variation_analysis.py`:

```powershell
python .\budget_variance_analysis.py --start-date "<start>" --end-date "<end>" --update-kpi-summary
```

- Output: `output/Integrated/Analysis/Budget/<start>_<end>/` (leaf and
  Device / Category / Goal / Energy / Domain / line variance, KPIs, top 10
  deviations, coverage, `Budget_Variance_Report_*.txt`, `Budget_Section_*.md`).
- Compared on budget-covered days only; coverage is always reported.
- Variance = Actual − Budget. More Investment / less Consumption is
  favorable; Maintenance is not judged, except Sleep vs its 7.5 h floor.
- "Not recorded" (budget, no actual) is a capture alert, not a saving.
- Standard Report: short section 7 only when a budget covers the period.
- Monthly KPI Summary: "## Actual vs Budget" after the historical comparison.

Budget KPIs: Investment ratio, discretionary consumption proxy (definition
above), digital leisure min/day vs its 60-min floor, sleep h/night vs 7.5,
top 10 absolute deviations.

## Consolidated dashboard

`python .\dashboard_builder.py` → `output/Dashboard/Time_Dashboard.html`:
one self-contained page with every closed month (Final), the 13-month
historical trend, the full taxonomy (Category → Subcategory, Domain, Energy,
Goal, Device, Activity), daily detail, month-vs-month comparison, Actual vs
Budget and close / data-quality status. Rebuild after each close (runbook
H3b). Template: `dashboard/dashboard_template.html`.

## Reporting cadence

- **Interim review (~15 days):** operational, provisional; does not close the period.
- **Monthly close:** authoritative; `PERIOD_CLOSE_CHECKLIST.md`.

See `INTERIM_REVIEW_PROCEDURE.md`.
