# Period-Close Checklist

Status: Mandatory

Reporting period (inclusive): __________ .. __________  
Prepared by: ________________________  
Date closed: _________________________

## A. Period setup

- [ ] Start date recorded.
- [ ] End date recorded.
- [ ] Expected calendar days confirmed (inclusive range).
- [ ] Clock capacity calculated (calendar days x 24 h).
- [ ] Correct output directory confirmed (`output/Integrated/Analysis/Final/<start>_<end>/`).

## B. Source collection

- [ ] ActivityWatch laptop data collected and pipelined.
- [ ] ActivityWatch desktop package imported (`import_desktop_activitywatch.py`).
- [ ] Desktop import receipt reviewed (`output/Imports/Import_Receipt_*.json`):
      period correct, `closed_period_conflicts` empty (or override recorded),
      `empty_dates` explained.
- [ ] Apple Screen Time screenshots present for every date.
- [ ] Habit / Off-Device screenshots present and reviewed.
- [ ] No known source gaps remain unexplained.

## C. Source processing

- [ ] All source processors completed.
- [ ] Integrated daily files generated.
- [ ] Expected number of daily integrated files confirmed.
- [ ] Source/device identities confirmed.
- [ ] Duplicate ingestion checked.
- [ ] Missing dates checked.

## D. Adjustments

- [ ] All known habit overstatements reviewed.
- [ ] All attribution corrections reviewed.
- [ ] Shared-device attribution handled through the configured ActivityWatch device target.
- [ ] Apple Screen Time was not reduced for a shared-device attribution correction.
- [ ] Applied and unapplied adjustment amounts audited.
- [ ] No adjustment silently created time.

## E. Taxonomy

- [ ] Taxonomy mappings reviewed.
- [ ] `build_time_taxonomy.py` passes validation.
- [ ] `Dim_Activity.csv` checked for intended overrides.
- [ ] Category defaults checked where needed.
- [ ] No unexplained unmapped activity remains.

## F. Final analysis

- [ ] `final_analysis.py` compiles.
- [ ] `final_analysis.py` completes.
- [ ] Final CSV generated.
- [ ] Hierarchy CSV generated.
- [ ] Correction audit generated.
- [ ] Attribution audit generated.
- [ ] Source reconciliation generated.
- [ ] Daily reconciliation generated.
- [ ] Time-universe reconciliation generated.
- [ ] Final report generated.

## G. Accounting validation

- [ ] Clock capacity is correct.
- [ ] Corrected tracked time reviewed.
- [ ] Tracked overlap reviewed.
- [ ] Unique tracked time reviewed.
- [ ] Off-Device Life is present.
- [ ] Unique tracked + Off-Device Life reconciles to clock capacity.
- [ ] REVIEW days have been inspected.
- [ ] Analytical activity exceeding capacity is understood as overlapping
      observation, not automatically an error.

## H. Hierarchy validation

- [ ] Device totals reviewed.
- [ ] Category totals reviewed.
- [ ] Subcategory totals reviewed.
- [ ] Domain totals reviewed.
- [ ] Energy totals reviewed.
- [ ] Goal totals reviewed.
- [ ] Off-Device Life has Domain/Energy/Goal.
- [ ] Unexpected blank dimensions investigated.

## I. EDA

- [ ] EDA compiles.
- [ ] EDA completes.
- [ ] EDA flags reviewed.
- [ ] Top activities reviewed.
- [ ] Concentration reviewed.
- [ ] Uncategorized reviewed.
- [ ] Taxonomy candidates reviewed.
- [ ] No automatic taxonomy changes accepted without human review.

## J. Standard report

- [ ] Standard Report generated.
- [ ] Clock capacity reviewed.
- [ ] Unique tracked reviewed.
- [ ] Off-Device Life reviewed.
- [ ] Overlap reviewed.
- [ ] Investment reviewed.
- [ ] Maintenance reviewed.
- [ ] Consumption reviewed.
- [ ] Recovery reviewed.
- [ ] Major discretionary drains reviewed.
- [ ] Recommendation reviewed.

## J2. Actual vs Budget  *(only if the approved budget covers the period)*

- [ ] Budget version recorded: ____________________
- [ ] `budget_variance_analysis.py` completed; `--update-kpi-summary` applied.
- [ ] Coverage reviewed (covered days / period days); uncovered days explained.
- [ ] Period budget reconciles to covered days × 24 h.
- [ ] Every **Not recorded** leaf reviewed as a capture question (not a saving).
- [ ] Unbudgeted leaves and top 10 deviations reviewed.
- [ ] Sleep vs 7.5 h/night and digital leisure vs 60 min/day floors reviewed.
- [ ] `period_close_validator.py ... --require-budget` passed.
- [ ] Not applicable (no budget coverage) — noted.

## K. Historical integration

- [ ] Period outputs archived.
- [ ] Historical integration scope confirmed.
- [ ] Period data transformed into historical schema.
- [ ] Historical integration performed or explicitly deferred.
- [ ] Historical totals reconciled.
- [ ] Historical changes documented.

## L. Close

- [ ] All documentation updated.
- [ ] Exceptions documented.
- [ ] Git diff reviewed.
- [ ] Commit created.
- [ ] Closed period is reproducible from the runbook.

### Close decision

- [ ] CLOSED
- [ ] CLOSED WITH DOCUMENTED REVIEW ITEMS
- [ ] NOT CLOSED
