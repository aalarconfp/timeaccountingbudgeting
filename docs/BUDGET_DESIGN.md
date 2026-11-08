# Budget Design

## Status

Future feature.

Preparatory analysis available: `historical_annual.py` builds a historical
baseline, data-supported reference ranges per budget line and three scenario
templates (Sustainable, Goal-focused, Conservative), plus a register of
decisions needed before a first budget (`HISTORICAL_ANNUAL_ANALYSIS.md`).
These are analytical templates, not an implemented or approved budget.

The budget should be constructed from current goals and the analytical
taxonomy rather than imposed independently of the time model.

## Principle

The time system describes actual behavior.

The budget should describe intended behavior.

The two should be comparable without being conflated.

## Proposed hierarchy

```text
Goal
  ↓
Domain
  ↓
Category
  ↓
Subcategory
```

Potential budget dimensions include:

- weekly minutes/hours;
- reporting-period target;
- minimum recovery;
- minimum investment;
- maximum discretionary consumption;
- maintenance allowance.

## Current goals

The existing taxonomy defines:

- Investment
- Maintenance
- Consumption

These are the natural starting dimensions for a future budget.

## Future budget workflow

1. Review multiple completed periods.
2. Establish realistic baselines.
3. Define current goals.
4. Convert goals into target ranges.
5. Define minimums and maximums where appropriate.
6. Compare actual vs target.
7. Avoid rigid optimization of healthy maintenance activities.
8. Review budget deviations qualitatively.
9. Adjust goals intentionally.

## Important constraint

Do not build the budget before the historical baseline is sufficiently stable.

The budget should be based on observed current behavior and current goals,
not on arbitrary numbers.
