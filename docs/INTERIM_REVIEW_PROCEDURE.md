# Interim (~15-day) Review Procedure

Status: Operational procedure (reporting layer only; no new analytical logic).
Change class: none. This document reuses existing scripts and outputs and
changes no accounting rule (`CHANGE_CONTROL.md`).

## 1. Two reporting levels

| | Level 1 — Operational (interim) review | Level 2 — Monthly accounting close |
|---|---|---|
| Frequency | ~every 15 days: days 1–15 and 16–end of month | Monthly |
| Purpose | Detect gaps early, investigate exceptions while evidence is fresh, record evidence-based adjustments, set next actions | Validate the full period, reconcile the time universe, record corrections, validate outputs, formally close |
| Closes the period? | **No** | **Yes** (`PERIOD_CLOSE_CHECKLIST.md`) |
| Authority | Provisional; superseded by the close | **Authoritative** |
| Repo output | `output/Integrated/Analysis/Preliminary/<start>_<end>/` | `output/Integrated/Analysis/Final/<YYYY-MM-01>_<YYYY-MM-last>/` + executed close checklist |
| Notion record | Reviews → `Time Accounting — Interim Review — <start> to <end>` (Frequency *Biweekly*) | Reviews → `<Month YYYY> — Time Accounting Review` (Frequency *Monthly*) |

Interim numbers never overwrite or replace the monthly close. When they differ,
the monthly Final outputs win.

Notion is the executive follow-up and decision layer. The repository and its
generated outputs remain the technical source of truth. Do not re-implement
accounting logic in Notion or in a new reporting script.

## 2. Flow

```text
Data collection (continuous)
    ↓
Interim review A (days 1–15)  → evidence & exception review → Notion interim report
    ↓
Interim review B (days 16–end), where applicable → Notion interim report
    ↓
Monthly accounting close (full month, SYSTEM_TRACKER_RUNBOOK.md Phases A–G)
    ↓
Final monthly Notion report (supersedes the interim reports for that month)
    ↓
Historical monthly analysis (Phase H; historical bridge per HISTORICAL_BRIDGE.md)
```

Interim B can be folded into the monthly close when the close happens within a
few days of month end.

## 3. Steps

Use the commands in `SYSTEM_TRACKER_RUNBOOK.md`, replacing the dates with the
interval (`START_DATE .. END_DATE`, inclusive, completed days only).

### Step A — Prepare
- Define the interval and its clock capacity (`days × 24 h`).
- Confirm which sources exist for the interval (runbook Phase B). Missing
  screenshots or exports are recorded as exceptions; they are not estimated.

### Step B — Validate (without closing)
- Process sources and build Integrated Daily Time for the interval (runbook
  Phases C–D1).
- Run the preliminary integration analysis **into the Preliminary folder** so
  the top-level working files are not overwritten:

```powershell
python .\integration_analysis.py --start-date "<start>" --end-date "<end>" `
    --unaccounted-target-hours 4 `
    --output-dir ".\output\Integrated\Analysis\Preliminary\<start>_<end>"
python .\integration_human_review.py --start-date "<start>" --end-date "<end>"
```

- Do **not** run `period_close_validator.py` or fill the Period-Close
  Checklist for an interval. Those belong to Level 2.
- Optional: `final_analysis.py` may be run on the interval (as was done for
  2026-08-01..15). Its `Final/<start>_<end>/` folder is interim evidence, not
  a close.

### Step B2 — Budget check (optional, budget periods)
- If `final_analysis.py` was run on the interval and the approved budget
  covers it, run `budget_variance_analysis.py --start-date "<start>"
  --end-date "<end>"` (no `--update-kpi-summary`). Output goes to
  `output/Integrated/Analysis/Budget/<start>_<end>/`; it is provisional like
  the rest of the interim and is superseded by the monthly close.
- Use it to catch capture gaps early: a **Not recorded** leaf (e.g. offline
  work with no Habit entries) is a capture alert to fix while evidence is
  fresh, not a saving.

### Step C — Review exceptions
- Inspect every day above tolerance (uncovered time > 240 min) and any overlap
  or validation failure.
- Add an entry to `input/Integrated/Habit_Manual_Adjustments.csv` only when
  there is defensible evidence. Every entry needs a reason.
- Do not create time from gaps, averages, device pauses or habitual routines.
  Unexplained time stays in Off-Device Life / uncertainty and remains flagged.
- Adjustments recorded in the interim are re-applied over the full month at
  close. `habit_manual_adjustments.py` restores each date's pre-adjustment
  snapshot before applying, which is intended to prevent double application.
  Confirm the correction audit at close.

### Step D — Report
- In Notion → Personal Operating System → Reviews, duplicate the page
  `Time Accounting — Interim Review — YYYY-MM-DD to YYYY-MM-DD`.
- Rename it, set Date to the interval and Status to *Planned*, then *Completed*.
- Fill in only the metrics actually produced for the interval. Keep the
  "INTERIM REVIEW — NOT A MONTHLY CLOSE" banner.

### Step E — Actions
- Update the relevant next actions only. Do not duplicate tasks.
- Carry unresolved exceptions forward to the next interim or to the close.

### Step F — Monthly close
- After month end, run the full runbook (Phases A–G) for the whole month and
  complete `PERIOD_CLOSE_CHECKLIST.md`.
- Create the monthly Notion report (`<Month YYYY> — Time Accounting Review`).
  Reference the month's interim reports. Mark any blank, unused weekly or
  interim review pages for the month as *Superseded* (do not delete completed
  ones).

### Step G — Preserve
- Keep source data, Preliminary/Final outputs, the correction audit and the
  close checklist unchanged after close (freeze rule, `CHANGE_CONTROL.md`).
- Each Notion report names its source output folder or file.
- `input/`, `output/` and `private/` stay out of Git (`.gitignore`).

## 4. Interim checklist

- [ ] Interval and capacity defined
- [ ] Source coverage checked; gaps recorded as exceptions
- [ ] Integrated Daily Time built for the interval
- [ ] Preliminary analysis written to `Preliminary/<start>_<end>/`
- [ ] Days above tolerance reviewed; adjustments only with evidence
- [ ] Notion interim report created from the template, banner kept
- [ ] Next actions updated; open exceptions carried forward

## 5. Scheduling

Notion's API cannot configure template repeats, so the cadence is set by hand
in the Notion UI. See §3 Step D for the template. Suggested review dates: the
16th (for days 1–15) and the 1st (for days 16–end, or fold into the close).
