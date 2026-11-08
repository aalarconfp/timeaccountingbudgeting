# Historical Data Integration

## Status

Planned operational step.

Phase 2 (legacy workbooks Sep 2025 – Jul 2026 bridged to the current
accounting) is implemented as a reproducible pipeline — see
`HISTORICAL_BRIDGE.md` and `historical_pipeline.py`.

The initial system has approximately one year of historical data that was
integrated through a controlled manual process.

The historical layer should become reproducible before it is treated as a
routine period-close operation.

## Objective

Create one consistent historical dataset that allows:

- period-to-period comparisons;
- trend analysis;
- baseline construction;
- long-term KPI analysis.

## Important separation

Historical integration is NOT the same as:

- source collection;
- daily integration;
- final reconciliation;
- taxonomy correction.

First close the current period correctly. Then integrate the closed period
into history.

## Proposed workflow

1. Freeze the closed period.
2. Preserve the exact final outputs.
3. Map current-period schema to historical schema.
4. Validate column meanings.
5. Normalize dates.
6. Normalize category/domain/energy/goal values.
7. Preserve source/device identity.
8. Append the period.
9. Deduplicate by the historical dataset's defined key.
10. Reconcile historical totals.
11. Compare period totals before and after append.
12. Record any transformation.
13. Save a historical integration audit.

## One-year backfill

For the initial one-year historical integration:

- preserve the original historical file;
- work on a copy;
- document every manual transformation;
- do not silently overwrite historical data;
- create a before/after reconciliation.

## Future automation

The long-term goal is a script such as:

```text
integrate_historical_data.py
```

with explicit:

- input schema;
- output schema;
- deduplication key;
- mapping rules;
- validation rules;
- audit output.

Until that exists, treat historical integration as a controlled closeout task.
