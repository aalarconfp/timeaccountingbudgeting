#!/usr/bin/env python3
"""Import the approved Time Budget workbook into an immutable CSV snapshot.

Reads the planning workbook (cached formula values, ``data_only=True``) and
writes to ``output/Reference/Budget/``:

* ``Budget_Leaf_<version>.csv``     Device, Category, Subcategory, Domain,
                                    Energy, Goal, Phase, Hours_per_week
                                    (sheet Detalle_Tracker — the budget source)
* ``Budget_Phases_<version>.csv``   Phase, Start, End (sheet Supuestos A5:C9)
* ``Budget_Lines_<version>.csv``    optional "budget line" dimension
                                    (sheets Datos + Diagnostico_Lineas)
* ``Budget_Manifest_<version>.json`` source file, SHA-256, import time,
                                    version, phase totals, parameters,
                                    Presupuesto_Tracker reconciliation targets,
                                    validation warnings, file hashes

The Excel logic is not recalculated here: values are taken as cached.
Presupuesto_Tracker is used only to reconcile the leaf roll-ups.

Re-importing an identical file is a no-op. A changed file creates a new
version (``..._r2``); an existing snapshot is never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import budget_core as core
from config import budget as cfg


DEFAULT_SOURCE = cfg.PLANNING_ROOT / "Time_Budget_2026-11_to_2027-04_v4.xlsx"

SHEET_ASSUMPTIONS = "Supuestos"
SHEET_LEAF = "Detalle_Tracker"
SHEET_LINES = "Datos"
SHEET_LINE_CLASS = "Diagnostico_Lineas"
SHEET_RECON = "Presupuesto_Tracker"
SHEETS_USED = (SHEET_ASSUMPTIONS, SHEET_LEAF, SHEET_LINES, SHEET_LINE_CLASS, SHEET_RECON)

PHASE_ROWS = range(5, 10)        # Supuestos!A5:C9
LEAF_HEADER_ROW = 4              # Detalle_Tracker
LINES_HEADER_ROW = 5             # Datos
LINE_CLASS_ROWS = range(5, 28)   # Diagnostico_Lineas rows 5-27

PARAMETER_LABELS = {
    "Sueño por noche": "sleep_target_h_per_night",
    "Piso de sueño por noche": "sleep_floor_h_per_night",
    "Piso de ocio digital": "digital_leisure_floor_min_per_day",
}

SPANISH_MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}

RECON_DIMENSIONS = ("Device", "Goal", "Energy", "Domain", "Category")

LEAF_FIELDS = [
    "Device", "Category", "Subcategory", "Domain", "Energy", "Goal",
    "Phase", "Hours_per_week",
]
PHASE_FIELDS = ["Phase", "Start", "End"]
LINE_FIELDS = [
    "Line", "Line_Class", "Leisure_Component", "Line_Type",
    "Device", "Category", "Subcategory", "Domain", "Energy", "Goal",
    "Phase", "Hours_per_week",
]


class BudgetImportError(Exception):
    """The workbook cannot be imported (blocking validation failure)."""


@dataclass
class ParsedBudget:
    phases: list[core.Phase]
    leaves: list[dict]
    lines: list[dict]
    parameters: dict
    recon_targets: dict
    monthly_targets: dict
    line_targets: dict
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Workbook reading
# ---------------------------------------------------------------------------


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value, where: str) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raise BudgetImportError(f"{where}: expected a number, found {value!r}.")


def load_workbooks(path: Path):
    """Return (values workbook, formulas workbook) after the cache check."""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise BudgetImportError("openpyxl is required (pip install openpyxl).") from exc

    values = openpyxl.load_workbook(path, data_only=True)
    formulas = openpyxl.load_workbook(path, data_only=False)
    missing = [s for s in SHEETS_USED if s not in values.sheetnames]
    if missing:
        raise BudgetImportError(f"{path.name}: missing sheets {missing}.")
    check_cached_values(values, formulas)
    return values, formulas


def check_cached_values(values, formulas) -> None:
    """Fail when a formula cell has no cached value (workbook not recalculated)."""
    empty = []
    for name in SHEETS_USED:
        ws_f = formulas[name]
        ws_v = values[name]
        for row in ws_f.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    if ws_v[cell.coordinate].value is None:
                        empty.append(f"{name}!{cell.coordinate}")
    if empty:
        shown = ", ".join(empty[:10])
        more = f" (+{len(empty) - 10} more)" if len(empty) > 10 else ""
        raise BudgetImportError(
            f"{len(empty)} formula cells have no cached value: {shown}{more}. "
            "Open the workbook in Excel, recalculate and save it, then re-import."
        )


def _as_date(value, where: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return core.parse_date(str(value))
    except ValueError as exc:
        raise BudgetImportError(f"{where}: invalid date {value!r}.") from exc


def read_phases(ws) -> list[core.Phase]:
    phases = []
    for r in PHASE_ROWS:
        name = _text(ws.cell(r, 1).value)
        if not name:
            break
        phases.append(core.Phase(
            name,
            _as_date(ws.cell(r, 2).value, f"{ws.title}!B{r}"),
            _as_date(ws.cell(r, 3).value, f"{ws.title}!C{r}"),
        ))
    if not phases:
        raise BudgetImportError(f"{ws.title}!A{PHASE_ROWS.start}: no phases found.")
    return phases


def read_parameters(ws, phase_names: list[str]) -> dict:
    """Per-phase parameters located by label in column A (header 'Parámetro')."""
    header_row = None
    for r in range(1, ws.max_row + 1):
        if _text(ws.cell(r, 1).value) == "Parámetro":
            header_row = r
            break
    if header_row is None:
        raise BudgetImportError(f"{ws.title}: 'Parámetro' header not found.")
    columns = {
        _text(ws.cell(header_row, c).value): c
        for c in range(1, ws.max_column + 1)
    }
    params: dict = {}
    for r in range(header_row + 1, ws.max_row + 1):
        key = PARAMETER_LABELS.get(_text(ws.cell(r, 1).value))
        if not key:
            continue
        params[key] = {
            p: _num(ws.cell(r, columns[p]).value, f"{ws.title} row {r} {p}")
            for p in phase_names
        }
    for key in PARAMETER_LABELS.values():
        if key not in params:
            raise BudgetImportError(f"{ws.title}: parameter {key} not found.")
    return params


def _header_map(ws, row: int) -> dict[str, int]:
    return {
        _text(ws.cell(row, c).value): c
        for c in range(1, ws.max_column + 1)
        if _text(ws.cell(row, c).value)
    }


def _require_columns(ws, header: dict[str, int], names, row: int) -> None:
    missing = [n for n in names if n not in header]
    if missing:
        raise BudgetImportError(f"{ws.title} row {row}: missing columns {missing}.")


def read_leaves(ws, phase_names: list[str]) -> list[dict]:
    header = _header_map(ws, LEAF_HEADER_ROW)
    dims = ["Device", "Category", "Subcategory", "Domain", "Energy", "Goal"]
    _require_columns(ws, header, dims + phase_names, LEAF_HEADER_ROW)

    rows = []
    r = LEAF_HEADER_ROW + 1
    found_total = False
    while r <= ws.max_row:
        first = _text(ws.cell(r, header["Device"]).value)
        if first == "Total":
            found_total = True
            break
        if first:
            base = {d: _text(ws.cell(r, header[d]).value) for d in dims}
            for p in phase_names:
                rows.append({
                    **base,
                    "Phase": p,
                    "Hours_per_week": _num(
                        ws.cell(r, header[p]).value, f"{ws.title}!row {r} {p}"
                    ),
                    "_row": r,
                })
        r += 1
    if not found_total:
        raise BudgetImportError(f"{ws.title}: 'Total' row not found.")
    return rows


def read_line_classes(ws) -> dict[str, str]:
    classes = {}
    for r in LINE_CLASS_ROWS:
        line = _text(ws.cell(r, 1).value)
        if line and line != "Total":
            classes[line] = _text(ws.cell(r, 2).value)
    return classes


def read_line_targets(ws, phase_names: list[str]) -> dict[str, dict[str, float]]:
    header = _header_map(ws, LINE_CLASS_ROWS.start - 1)
    targets = {}
    for r in LINE_CLASS_ROWS:
        line = _text(ws.cell(r, 1).value)
        if line and line != "Total":
            targets[line] = {
                p: _num(ws.cell(r, header[p]).value, f"{ws.title}!row {r}")
                for p in phase_names if p in header
            }
    return targets


def read_lines(ws, phase_names: list[str], classes: dict[str, str]) -> list[dict]:
    header = _header_map(ws, LINES_HEADER_ROW)
    _require_columns(
        ws, header,
        ["Línea", "Device", "Category", "Subcategory"] + phase_names,
        LINES_HEADER_ROW,
    )
    rows = []
    for r in range(LINES_HEADER_ROW + 1, ws.max_row + 1):
        line = _text(ws.cell(r, header["Línea"]).value)
        if line == "Total":
            break
        if not line:
            continue
        base = {
            "Line": line,
            "Line_Class": classes.get(line, ""),
            "Leisure_Component": (
                _text(ws.cell(r, header["Componente ocio"]).value)
                if "Componente ocio" in header else ""
            ),
            "Line_Type": _text(ws.cell(r, header["Tipo"]).value) if "Tipo" in header else "",
        }
        for d in ("Device", "Category", "Subcategory", "Domain", "Energy", "Goal"):
            base[d] = _text(ws.cell(r, header[d]).value) if d in header else ""
        for p in phase_names:
            rows.append({
                **base,
                "Phase": p,
                "Hours_per_week": _num(ws.cell(r, header[p]).value, f"{ws.title}!row {r} {p}"),
            })
    return rows


def _parse_month(label: str) -> tuple[int, int] | None:
    text = label.strip().lower().replace(".", "")
    if "-" not in text:
        return None
    month_text, year_text = text.rsplit("-", 1)
    month = SPANISH_MONTHS.get(month_text[:4]) or SPANISH_MONTHS.get(month_text[:3])
    if not month or not year_text.isdigit():
        return None
    year = int(year_text)
    return (2000 + year if year < 100 else year), month


def read_recon_targets(ws, phase_names: list[str]) -> tuple[dict, dict]:
    """Return ({dimension: {value: {phase: h/week}}}, {month: {category: h}})."""
    targets: dict = {}
    monthly: dict = {}
    r = 1
    while r <= ws.max_row:
        label = _text(ws.cell(r, 1).value)
        if label in RECON_DIMENSIONS:
            header = _header_map(ws, r)
            block: dict = {}
            r += 1
            while r <= ws.max_row and _text(ws.cell(r, 1).value) not in ("", "Total"):
                value = _text(ws.cell(r, 1).value)
                block[value] = {
                    p: _num(ws.cell(r, header[p]).value, f"{ws.title}!row {r}")
                    for p in phase_names
                }
                r += 1
            targets[label] = block
            continue
        if label.startswith("Category (horas por mes)"):
            header = _header_map(ws, r)
            months = {
                name: col for name, col in header.items() if _parse_month(name)
            }
            r += 1
            while r <= ws.max_row and _text(ws.cell(r, 1).value) != "":
                value = _text(ws.cell(r, 1).value)
                for name, col in months.items():
                    year, month = _parse_month(name)  # type: ignore[misc]
                    key = f"{year:04d}-{month:02d}"
                    monthly.setdefault(key, {})[value] = _num(
                        ws.cell(r, col).value, f"{ws.title}!row {r}"
                    )
                if value == "Total":
                    break
                r += 1
        r += 1
    return targets, monthly


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_phases(phases: list[core.Phase]) -> list[str]:
    errors = []
    for p in phases:
        if p.end < p.start:
            errors.append(f"Phase {p.name}: end {p.end} is before start {p.start}.")
    ordered = sorted(phases, key=lambda p: p.start)
    for a, b in zip(ordered, ordered[1:]):
        if b.start <= a.end:
            errors.append(f"Phases {a.name} and {b.name} overlap ({b.start} <= {a.end}).")
        elif b.start != a.end + timedelta(days=1):
            errors.append(
                f"Gap between {a.name} (ends {a.end}) and {b.name} (starts {b.start})."
            )
    names = [p.name for p in phases]
    if len(set(names)) != len(names):
        errors.append("Duplicate phase names.")
    return errors


def phase_totals(leaves: list[dict]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in leaves:
        totals[row["Phase"]] += row["Hours_per_week"]
    return dict(totals)


def validate_phase_totals(totals: dict[str, float]) -> list[str]:
    return [
        f"Phase {p} sums to {t:.4f} h/week; expected "
        f"{cfg.HOURS_PER_WEEK:g} ± {cfg.PHASE_TOTAL_TOLERANCE_H}."
        for p, t in totals.items()
        if abs(t - cfg.HOURS_PER_WEEK) > cfg.PHASE_TOTAL_TOLERANCE_H
    ]


def validate_unique_leaves(leaves: list[dict]) -> list[str]:
    seen: dict[tuple, int] = {}
    errors = []
    for row in leaves:
        key = (*core.leaf_key(row), row["Phase"])
        if key in seen:
            errors.append(
                f"Duplicate leaf {key[:3]} (rows {seen[key]} and {row.get('_row')})."
            )
        else:
            seen[key] = row.get("_row", 0)
    return errors


def validate_recon(leaves: list[dict], targets: dict) -> list[str]:
    errors = []
    for dim, block in targets.items():
        computed: dict[tuple[str, str], float] = defaultdict(float)
        for row in leaves:
            computed[(row[dim], row["Phase"])] += row["Hours_per_week"]
        for value, by_phase in block.items():
            for phase, target in by_phase.items():
                got = computed.get((value, phase), 0.0)
                if abs(got - target) > cfg.RECONCILIATION_TOLERANCE_H:
                    errors.append(
                        f"{SHEET_RECON} {dim}={value} {phase}: leaves sum to "
                        f"{got:.4f} h/week, sheet says {target:.4f}."
                    )
        for (value, phase), got in computed.items():
            if value not in block and abs(got) > cfg.RECONCILIATION_TOLERANCE_H:
                errors.append(
                    f"{SHEET_RECON} {dim}={value} {phase}: {got:.4f} h/week in "
                    "leaves but missing from the sheet."
                )
    return errors


def month_bounds(key: str) -> tuple[date, date]:
    year, month = int(key[:4]), int(key[5:7])
    start = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def validate_monthly(snapshot: core.BudgetSnapshot, monthly: dict) -> list[str]:
    errors = []
    for key, by_category in monthly.items():
        start, end = month_bounds(key)
        budget = core.period_leaf_budget(snapshot, start, end)
        computed: dict[str, float] = defaultdict(float)
        for (_, category, _), entry in budget.items():
            computed[category] += entry["Budget_min"] / 60.0
        computed["Total"] = sum(v for k, v in computed.items() if k != "Total")
        for category, target in by_category.items():
            got = computed.get(category, 0.0)
            if abs(got - target) > cfg.RECONCILIATION_TOLERANCE_H:
                errors.append(
                    f"{SHEET_RECON} monthly {key} {category}: phase proration gives "
                    f"{got:.4f} h, sheet says {target:.4f} h."
                )
    return errors


def validate_lines(leaves: list[dict], lines: list[dict]) -> list[str]:
    if not lines:
        return []
    by_leaf: dict[tuple, float] = defaultdict(float)
    for row in lines:
        by_leaf[(*core.leaf_key(row), row["Phase"])] += row["Hours_per_week"]
    errors = []
    for row in leaves:
        key = (*core.leaf_key(row), row["Phase"])
        got = by_leaf.pop(key, 0.0)
        if abs(got - row["Hours_per_week"]) > cfg.RECONCILIATION_TOLERANCE_H:
            errors.append(
                f"{SHEET_LINES}: lines for {key[:3]} {key[3]} sum to {got:.4f} "
                f"h/week; {SHEET_LEAF} says {row['Hours_per_week']:.4f}."
            )
    for key, got in by_leaf.items():
        if abs(got) > cfg.RECONCILIATION_TOLERANCE_H:
            errors.append(f"{SHEET_LINES}: {key[:3]} {key[3]} has no {SHEET_LEAF} leaf.")
    return errors


def line_target_warnings(lines: list[dict], targets: dict) -> list[str]:
    computed: dict[tuple[str, str], float] = defaultdict(float)
    for row in lines:
        computed[(row["Line"], row["Phase"])] += row["Hours_per_week"]
    warnings = []
    for line, by_phase in targets.items():
        for phase, target in by_phase.items():
            got = computed.get((line, phase), 0.0)
            if abs(got - target) > cfg.RECONCILIATION_TOLERANCE_H:
                warnings.append(
                    f"Line '{line}' {phase}: {SHEET_LINES} sums to {got:.4f}, "
                    f"{SHEET_LINE_CLASS} says {target:.4f} h/week."
                )
    return warnings


def load_taxonomy(root: Path) -> dict:
    def rows(name):
        path = root / name
        return core.read_csv(path) if path.exists() else []

    return {
        "activity": {
            (r["Source"], r["Activity_Name"]): (r["Domain"], r["Energy"], r["Goal"])
            for r in rows("Dim_Activity.csv")
        },
        "category": {
            r["Canonical_Category"]: (r["Domain"], r["Energy"], r["Goal"])
            for r in rows("Dim_Category_Default.csv")
        },
        "domains": {r["Domain"] for r in rows("Dim_Domain.csv")},
        "energies": {r["Energy"] for r in rows("Dim_Energy.csv")},
        "goals": {r["Goal"] for r in rows("Dim_Goal.csv")},
        "available": (root / "Dim_Category_Default.csv").exists(),
    }


def taxonomy_warnings(leaves: list[dict], taxonomy: dict) -> list[str]:
    """Compare leaf Domain/Energy/Goal with output/Reference/Taxonomy (warn only)."""
    if not taxonomy["available"]:
        return ["Taxonomy reference not found; Domain/Energy/Goal not checked."]
    warnings = []
    seen = set()
    for row in leaves:
        key = core.leaf_key(row)
        if key in seen:
            continue
        seen.add(key)
        device, category, subcategory = key
        dims = (row["Domain"], row["Energy"], row["Goal"])
        for value, allowed, label in (
            (row["Domain"], taxonomy["domains"], "Domain"),
            (row["Energy"], taxonomy["energies"], "Energy"),
            (row["Goal"], taxonomy["goals"], "Goal"),
        ):
            if allowed and value not in allowed:
                warnings.append(f"{key}: {label} {value!r} is not in Dim_{label}.csv.")
        if device == cfg.OFF_DEVICE_DEVICE or category == cfg.OFF_DEVICE_CATEGORY:
            expected, basis = cfg.OFF_DEVICE_MAPPING, "Off-Device Life mapping"
        else:
            source = cfg.DEVICE_SOURCE.get(device)
            override = taxonomy["activity"].get((source, subcategory))
            if override:
                expected, basis = override, f"Dim_Activity ({source}/{subcategory})"
            elif category in taxonomy["category"]:
                expected, basis = taxonomy["category"][category], "Dim_Category_Default"
            else:
                warnings.append(f"{key}: category {category!r} not in the taxonomy.")
                continue
        if dims != tuple(expected):
            warnings.append(
                f"{key}: budget Domain/Energy/Goal {dims} differs from {basis} "
                f"{tuple(expected)}."
            )
    return warnings


def parse_workbook(path: Path, taxonomy_root: Path | None = None) -> ParsedBudget:
    values, _ = load_workbooks(path)
    phases = read_phases(values[SHEET_ASSUMPTIONS])
    names = [p.name for p in phases]
    params = read_parameters(values[SHEET_ASSUMPTIONS], names)
    leaves = read_leaves(values[SHEET_LEAF], names)
    classes = read_line_classes(values[SHEET_LINE_CLASS])
    line_targets = read_line_targets(values[SHEET_LINE_CLASS], names)
    lines = read_lines(values[SHEET_LINES], names, classes)
    recon, monthly = read_recon_targets(values[SHEET_RECON], names)
    parsed = ParsedBudget(phases, leaves, lines, params, recon, monthly, line_targets)
    validate(parsed, taxonomy_root or cfg.TAXONOMY_ROOT)
    return parsed


def validate(parsed: ParsedBudget, taxonomy_root: Path) -> None:
    """Raise on blocking errors; collect warnings on ``parsed``."""
    errors: list[str] = []
    errors += validate_phases(parsed.phases)
    errors += validate_phase_totals(phase_totals(parsed.leaves))
    errors += validate_unique_leaves(parsed.leaves)
    errors += validate_recon(parsed.leaves, parsed.recon_targets)
    errors += validate_lines(parsed.leaves, parsed.lines)
    if not errors:
        snapshot = core.BudgetSnapshot("check", parsed.phases, parsed.leaves, [], {})
        errors += validate_monthly(snapshot, parsed.monthly_targets)
    if errors:
        raise BudgetImportError(
            "Budget validation failed:\n- " + "\n- ".join(errors)
        )
    parsed.warnings += line_target_warnings(parsed.lines, parsed.line_targets)
    parsed.warnings += taxonomy_warnings(parsed.leaves, load_taxonomy(taxonomy_root))


# ---------------------------------------------------------------------------
# Snapshot writing
# ---------------------------------------------------------------------------


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def base_version(path: Path) -> str:
    stem = path.stem
    return stem[len("Time_Budget_"):] if stem.startswith("Time_Budget_") else stem


def resolve_version(root: Path, requested: str, source_hash: str) -> tuple[str, bool]:
    """Return (version, already_imported). Never reuses a version name."""
    existing = {m["version"]: m for m in core.list_versions(root)} if root.exists() else {}
    for manifest in existing.values():
        if manifest.get("source_sha256") == source_hash and (
            manifest["version"] == requested
            or manifest["version"].startswith(requested + "_r")
        ):
            return manifest["version"], True
    if requested not in existing:
        return requested, False
    n = 2
    while f"{requested}_r{n}" in existing:
        n += 1
    return f"{requested}_r{n}", False


def relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def write_snapshot(
    parsed: ParsedBudget,
    source: Path,
    source_hash: str,
    version: str,
    root: Path,
) -> dict:
    paths = core.snapshot_paths(root, version)
    clash = [p for p in paths.values() if p.exists()]
    if clash:
        raise BudgetImportError(f"Refusing to overwrite existing snapshot files: {clash}")

    leaves = [{k: r[k] for k in LEAF_FIELDS} for r in parsed.leaves]
    core.write_csv(paths["leaf"], leaves, LEAF_FIELDS, decimals=None)
    core.write_csv(
        paths["phases"],
        [{"Phase": p.name, "Start": p.start.isoformat(), "End": p.end.isoformat()}
         for p in parsed.phases],
        PHASE_FIELDS,
    )
    core.write_csv(paths["lines"], parsed.lines, LINE_FIELDS, decimals=None)

    totals = phase_totals(parsed.leaves)
    manifest = {
        "version": version,
        "source_file": relative(source),
        "source_sha256": source_hash,
        "imported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "unit": "hours per week",
        "horizon": {
            "start": min(p.start for p in parsed.phases).isoformat(),
            "end": max(p.end for p in parsed.phases).isoformat(),
        },
        "phases": [
            {"phase": p.name, "start": p.start.isoformat(), "end": p.end.isoformat(),
             "total_h_per_week": round(totals.get(p.name, 0.0), 6)}
            for p in parsed.phases
        ],
        "leaf_count": len({core.leaf_key(r) for r in parsed.leaves}),
        "line_count": len({r["Line"] for r in parsed.lines}),
        "parameters": {
            **parsed.parameters,
            "digital_leisure_line": cfg.DEFAULT_DIGITAL_LEISURE_LINE,
        },
        "reconciliation_targets": {
            "h_per_week_by_dimension": parsed.recon_targets,
            "h_per_month_by_category": parsed.monthly_targets,
        },
        "validation": {
            "status": "PASS",
            "warnings": parsed.warnings,
        },
        "files": {
            name: {"path": p.name, "sha256": sha256(p)}
            for name, p in paths.items() if name != "manifest"
        },
    }
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def import_budget(
    source: Path,
    root: Path | None = None,
    version: str | None = None,
    taxonomy_root: Path | None = None,
    dry_run: bool = False,
) -> tuple[dict | None, ParsedBudget, str, bool]:
    """Import ``source``; return (manifest, parsed, version, already_imported)."""
    root = root or cfg.BUDGET_REFERENCE_ROOT
    if not source.exists():
        raise BudgetImportError(f"Budget workbook not found: {source}")
    parsed = parse_workbook(source, taxonomy_root)
    source_hash = sha256(source)
    resolved, already = resolve_version(root, version or base_version(source), source_hash)
    if already or dry_run:
        return None, parsed, resolved, already
    root.mkdir(parents=True, exist_ok=True)
    manifest = write_snapshot(parsed, source, source_hash, resolved, root)
    return manifest, parsed, resolved, False


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help="Planning workbook (.xlsx, recalculated).")
    parser.add_argument("--version", help="Version label (default: from the file name).")
    parser.add_argument("--output-root", type=Path, default=cfg.BUDGET_REFERENCE_ROOT)
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate only; write nothing.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    print("=== Budget import ===")
    print(f"Source  : {args.source}")
    try:
        manifest, parsed, version, already = import_budget(
            args.source, args.output_root, args.version, dry_run=args.dry_run
        )
    except BudgetImportError as exc:
        print(f"ERROR: {exc}")
        print("RESULT: BUDGET IMPORT FAILED.")
        return 1

    totals = phase_totals(parsed.leaves)
    for p in parsed.phases:
        print(f"  {p.name:<8} {p.start} .. {p.end}  {totals[p.name]:.4f} h/week")
    print(f"Leaves  : {len({core.leaf_key(r) for r in parsed.leaves})}")
    print(f"Lines   : {len({r['Line'] for r in parsed.lines})}")
    print("Checks  : 168 h/phase, contiguous phases, unique leaves, "
          "Presupuesto_Tracker roll-ups and monthly totals - PASS")
    for warning in parsed.warnings:
        print(f"WARNING : {warning}")
    if not parsed.warnings:
        print("Taxonomy: Domain/Energy/Goal consistent with output/Reference/Taxonomy.")

    if already:
        print(f"Version : {version} (identical file already imported; nothing written)")
        print("RESULT: BUDGET ALREADY IMPORTED.")
    elif args.dry_run:
        print(f"Version : {version} (dry run; nothing written)")
        print("RESULT: BUDGET VALIDATION PASSED.")
    else:
        print(f"Version : {version}")
        print(f"Output  : {args.output_root}")
        print("RESULT: BUDGET IMPORTED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
