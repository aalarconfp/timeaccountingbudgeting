# Time Accounting & Budgeting System

> An open-source framework for personal time intelligence:
> **Tracking → Time Accounting → Time Budgeting → KPIs / decision support.**
> Tracking and time accounting are implemented; time budgeting and the KPI/decision layer are planned.

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Git](https://img.shields.io/badge/Git-Version_Control-orange)
![Status](https://img.shields.io/badge/status-active_development-green)

---

# Overview

Time Accounting & Budgeting System is a structured time-intelligence framework designed to transform fragmented activity data into a consistent model for time accounting, analysis, and future time budgeting.

The core methodology is:

```text
Track what happened
        ↓
Account for available time
        ↓
Analyze allocation
        ↓
Budget future allocation       (planned)
        ↓
Measure variance and trends    (budget variance planned)
```

The system treats time as a finite resource and provides a reproducible analytical pipeline for collection, normalization, reconciliation, classification, accounting, analysis, human review, and period close.

---

# Objectives

The project is designed to be:

- **Structured** — multiple tracking sources are transformed into a common model.
- **Reproducible** — the same inputs and processing rules should produce consistent outputs.
- **Auditable** — transformations, adjustments, validation, and period status are inspectable.
- **Efficient** — automation reduces repetitive data processing while preserving human review where needed.
- **Analytical** — time is analyzed across multiple dimensions rather than as a single total.
- **Budget-oriented** — historical accounting provides the foundation for future time budgets and variance analysis.

---

# Methodology

```text
Data Collection
      ↓
Normalization
      ↓
Source Reconciliation
      ↓
Time Accounting
      ↓
Taxonomy Classification
      ↓
Integrated Daily Model
      ↓
Analysis & KPIs
      ↓
Human Review
      ↓
Period Close
      ↓
Historical Analysis
      ↓
Time Budgeting (planned)
```

## 1. Data Collection

The system collects activity data from independent tracking sources while preserving source-level information before analytical transformations.

## 2. Normalization

Different source formats are converted into a canonical analytical structure containing concepts such as:

- Date
- Source
- Category
- Subcategory
- Duration
- Event Count
- Allocation Type
- Evidence Type
- Evidence Source

## 3. Source Reconciliation

Sources may contain overlapping, missing, duplicated, or inconsistent records. Reconciliation establishes a controlled and explainable representation of the available evidence.

The objective is not to force every source to agree, but to preserve the most useful representation of what is known.

## 4. Time Accounting

The system separates:

- Clock Capacity
- Corrected Tracked Time
- Tracked Overlap
- Unique Tracked Time
- Off-Device / Untracked Life
- Analytical Activity

The core accounting relationship is:

```text
Clock Capacity
    =
Unique Tracked Time
+
Off-Device / Untracked Life
```

Tracked overlap is handled explicitly in the accounting layer rather than silently discarded from analytical activity.

## 5. Taxonomy Classification

Activities can be analyzed across multiple dimensions:

- Category
- Subcategory
- Domain
- Energy
- Goal

This creates a consistent analytical vocabulary for allocation, trends, and budgeting.

## 6. Integrated Daily Model

Normalized sources are consolidated into a daily analytical model that becomes the foundation for:

- Reconciliation
- Detail analysis
- Exploratory analysis
- KPI reporting
- Historical comparison
- Budget development (planned)

## 7. Analysis & KPIs

The analytical layer supports:

- Category allocation
- Domain allocation
- Goal allocation
- Energy allocation
- Source and device analysis
- Daily and period trends
- Hierarchical activity analysis
- Coverage and data-quality analysis
- Historical variation analysis

## 8. Human Review

Automation is deliberately separated from human judgment.

When evidence is insufficient to identify an activity, the system can flag the period for review rather than inventing an allocation.

Manual adjustments are explicit, auditable inputs.

> **Transparent uncertainty is preferable to false precision.**

## 9. Period Close

Completed periods can be validated and documented before becoming stable historical reference periods.

A close validates:

- Source coverage
- Data integrity
- Accounting closure
- Taxonomy consistency
- Manual adjustments
- Analytical outputs
- Review items

## 10. Time Budgeting *(planned — not yet implemented)*

Time budgeting is the next planned layer, to be built on historical accounting.

The intended hierarchy is:

```text
Goal
  ↓
Domain
  ↓
Category
```

It will enable planned-versus-actual analysis and budget variance measurement.

A first analytical step exists: `historical_annual.py` produces a historical
baseline and budget *scenario templates* from the bridged history
(`docs/HISTORICAL_ANNUAL_ANALYSIS.md`). It does not implement or approve a
budget; outputs stay in the git-ignored `output/` folder.

---

# Core Architecture

```text
                    ┌─────────────────────┐
                    │   Source Systems    │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Collection &        │
                    │ Normalization       │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Reconciliation &    │
                    │ Validation          │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Time Accounting     │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Taxonomy &          │
                    │ Classification      │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Integrated Daily    │
                    │ Model               │
                    └──────────┬──────────┘
                               ↓
             ┌─────────────────┴─────────────────┐
             ↓                                   ↓
   ┌─────────────────────┐             ┌─────────────────────┐
   │ Analysis & KPIs     │             │ Human Review        │
   └──────────┬──────────┘             └──────────┬──────────┘
              └────────────────┬──────────────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Period Close        │
                    └──────────┬──────────┘
                               ↓
                    ┌─────────────────────┐
                    │ Historical Analysis │
                    │ & Budget (planned)  │
                    └─────────────────────┘
```

---

# Key Design Principles

### Accounting Before Optimization

```text
Accounting → Analysis → Budgeting
```

The system establishes what happened before attempting to determine what should happen.

### Evidence Over Assumption

Observed, inferred, estimated, and manually adjusted time are treated as different evidence states.

### Preserve the Time Universe

Analytical activity and finite clock capacity are separate concepts. This allows overlapping activity to be analyzed without implying that a period contains more time than physically exists.

### Explicit Manual Adjustments

Human knowledge can improve incomplete tracking, but corrections enter through explicit adjustment records rather than hidden source edits.

### Separation of Source and Device

Source identifies the data pipeline. Device identifies the physical or logical origin when that distinction is relevant.

### Period-Based Analysis

Defined periods provide stable baselines, comparisons, validation, and historical reference points.

### Reproducibility

A closed period should be reproducible from versioned processing logic and documented methodology.

---

# Analytical Model

The project is organized around three layers:

```text
Tracking
   ↓
Time Accounting
   ↓
Time Budgeting            (planned)
   ↓
KPIs & Decision Support   (planned)
```

### Tracking
**What happened?**

Collect and normalize activity.

### Time Accounting
**Where did available time go?**

Reconcile sources and account for the finite time universe.

### Time Budgeting *(planned)*
**Where should available time go?**

Define planned allocations and measure variance. Not implemented yet.

---

# Efficiency & Analysis

Automation is used for:

- Data ingestion
- Normalization
- Reconciliation
- Validation
- Taxonomy mapping
- Aggregation
- KPI generation
- Historical comparison
- Period validation

Human intervention is reserved for contextual judgment where automated inference would create false precision.

```text
Automation
    +
Evidence
    +
Human Judgment
    =
Reliable Time Intelligence
```

---

# Quality Controls

The pipeline validates:

- Missing dates
- Duplicate records
- Negative durations
- Schema consistency
- Source coverage
- Taxonomy completeness
- Reconciliation consistency
- Accounting closure
- Manual adjustment application
- Period integrity

The system distinguishes between:

1. Data errors
2. Evidence limitations
3. Genuine untracked time
4. Analytical uncertainty

---

# Current Capabilities

- Multi-source time collection
- Canonical normalization
- Source reconciliation
- Time-universe accounting
- Taxonomy-based classification
- Integrated daily analysis
- Category, domain, energy, and goal analysis
- Source and device analysis
- Historical period comparison
- Manual adjustment auditing
- Shared-device (secondary user) attribution
- Evidence-based review
- Period validation and close
- Variation analysis between periods
- Reproducible processing pipelines
- Automated test coverage

# Planned

- Time budgeting
- Budget hierarchy (Goal → Domain → Category)
- Planned vs. actual allocation
- Budget variance analysis
- KPI and budgeting decision layer

---

# Technology Stack

- Python
- CSV-based analytical data pipelines
- Git
- Automated tests
- Markdown documentation
- PowerShell automation where appropriate

The architecture is modular so additional sources, analytical layers, dashboards, and budgeting capabilities can be introduced without replacing the accounting foundation.

---

# Repository Structure

```text
Time Accounting & Budgeting System/
│
├── *.py
│   └── Pipeline modules (collection, normalization, integration,
│       reconciliation, analysis, reporting, period-close validation)
│
├── config/
│   └── Configuration, taxonomy, and a local-settings template
│       (devices, shared-device rule)
│
├── docs/
│   ├── System documentation
│   ├── Data collection methodology
│   └── Runbooks
│
├── scripts/
│   └── Operational automation
│
├── tests/
│   └── Automated validation and regression tests
│
├── input/            (not version-controlled)
│   └── Source data
│
├── output/           (not version-controlled)
│   └── Generated analytical outputs
│
└── README.md
```

Source data and generated outputs should remain separate from version-controlled processing logic wherever appropriate.

---

# Roadmap

## Phase 1 — Time Accounting Foundation

- Multi-source collection
- Normalization
- Reconciliation
- Time-universe accounting
- Taxonomy
- Data-quality validation
- Period close

## Phase 2 — Historical Intelligence

- Comparable historical periods
- Baseline construction
- Trend analysis
- Variation analysis
- Long-term allocation patterns

## Phase 3 — Time Budgeting

- Goal-level budgets
- Domain-level budgets
- Category-level budgets
- Planned vs. actual allocation
- Budget variance analysis
- Capacity-aware planning

## Phase 4 — KPI & Decision Layer

- Time allocation KPIs
- Coverage KPIs
- Budget variance KPIs
- Trend indicators
- Efficiency metrics
- Period-over-period decision support

## Future Extensions

- Interactive dashboards
- Automated reporting
- Additional tracking integrations
- Forecasting
- Advanced anomaly detection
- Productivity intelligence
- Optional opportunity-cost extensions

---

# Project Philosophy

The system is not designed to maximize the amount of tracked data.

It is designed to maximize the **usefulness, consistency, efficiency, and interpretability of time data**.

A reliable time-accounting system should be able to explain:

- what is known,
- what is estimated,
- what was manually adjusted,
- what remains uncertain,
- how the period reconciles,
- and how historical evidence can inform future budgets.

The goal is not perfect measurement.

The goal is:

> **Structured, auditable, decision-useful time intelligence.**

---

# Status

No release has been tagged yet. The current codebase implements the accounting, reconciliation, analysis, validation, and period-close framework. Time budgeting is the next planned layer built on top of that foundation.

---
