# historical_legacy_loader.py
"""Read the legacy 'Personal Time Management' workbooks into provenance records.

This module only READS the workbooks (openpyxl, read-only). It never writes to
them. Every value it emits carries the workbook, sheet and cell it came from,
the literal entry typed by the user (number or formula) and Excel's cached
result, so a reviewer can always answer "where did this number come from?".

Outputs (plain Python dicts, written to CSV by historical_pipeline.py):

* detail cells   – one record per non-blank day cell of a detail row
* reported rows  – the legacy monthly Report sheet values (category / energy /
                   device / goal blocks, capacity, 'Other')
* sheet daily    – the timesheet's own per-day 'Total Hours' row
* sheet meta     – layout facts detected per sheet (total column, spill-over
                   columns, start date) used by validation

Layout facts (verified on all 11 monthly sheets):

* Row 9 holds the dates (formula; cached value used), row 10 the headers.
* Columns A..F = Category, Subcategory, Energy Level, Time of Day, Device,
  Goal Alignment. Column B is an XLOOKUP to the Categories example text on
  device rows (i.e. NOT a real subcategory) and a literal subcategory on the
  Off-Device rows.
* Day columns start at G. The monthly total column is the column whose row-11
  formula is '=SUM(G11:<last>11)/60'; it may include day columns that belong
  to the NEXT month (spill-over). Those cells are kept but flagged.
* Rows whose column E ends with ':' are subtotal rows and are not detail.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import operator
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from config.historical import LEGACY_SHEETS, LegacySheet

try:  # openpyxl is required only for the historical pipeline.
    import openpyxl
    from openpyxl.utils import column_index_from_string, get_column_letter
    from openpyxl.worksheet.formula import ArrayFormula
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "openpyxl is required for the historical pipeline: "
        "python -m pip install openpyxl"
    ) from exc


LEAVE_ROWS = frozenset({"Holiday", "Vacation", "Sick Leave", "Personal Leave"})
FIRST_DETAIL_ROW = 11
DATE_ROW = 9
FIRST_DAY_COL = column_index_from_string("G")

ROW_BLOCK_DEVICE = "DEVICE_ROWS"
ROW_BLOCK_OFF_CORE = "OFF_HOURS_CORE"
ROW_BLOCK_OFF_OTHER = "OFF_HOURS_OTHER"


# --------------------------------------------------------------------------
# Safe arithmetic evaluation of the user's literal entries ("=60*3+15").
# --------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_ARITH_RE = re.compile(r"^[0-9+\-*/().\s]+$")


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


def evaluate_entry(raw: Any) -> tuple[float | None, str]:
    """Evaluate a literal cell entry.

    Returns (value, entry_type). entry_type is one of
    NUMBER, ARITHMETIC_FORMULA, REFERENCE_FORMULA, TEXT, BLANK.
    Reference formulas (e.g. '=+G45') cannot be evaluated offline; their
    cached value is used instead.
    """
    if raw is None:
        return None, "BLANK"
    if isinstance(raw, bool):
        return None, "TEXT"
    if isinstance(raw, (int, float)):
        return float(raw), "NUMBER"
    if isinstance(raw, ArrayFormula):
        return None, "REFERENCE_FORMULA"
    text = str(raw).strip()
    if text.startswith("="):
        body = text[1:].lstrip("+")
        if body and _ARITH_RE.match(body):
            try:
                return _eval_node(ast.parse(body, mode="eval")), "ARITHMETIC_FORMULA"
            except (ValueError, SyntaxError, ZeroDivisionError):
                return None, "REFERENCE_FORMULA"
        return None, "REFERENCE_FORMULA"
    try:
        return float(text), "NUMBER"
    except ValueError:
        return None, "TEXT"


def raw_text(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, ArrayFormula):
        return str(raw.text)
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# --------------------------------------------------------------------------
# Sheet layout detection
# --------------------------------------------------------------------------

@dataclass
class SheetLayout:
    total_col: int
    day_cols: list[int]
    col_dates: dict[int, dt.date | None]
    grand_total_row: int
    device_total_row: int
    off_core_subtotal_row: int
    detail_rows: list[int]
    start_date: dt.date


def detect_layout(ws_f, ws_v, month: str) -> SheetLayout:
    """Detect the layout of one monthly timesheet (formula + value views)."""
    total_col = None
    for col in range(FIRST_DAY_COL, ws_f.max_column + 1):
        value = ws_f.cell(FIRST_DETAIL_ROW, col).value
        if isinstance(value, str) and value.replace(" ", "").upper().startswith(
            f"=SUM(G{FIRST_DETAIL_ROW}:"
        ):
            total_col = col
            break
    if total_col is None:
        raise ValueError(f"{ws_f.title}: monthly total column not found")

    start_date = _as_date(ws_v["B4"].value)
    if start_date is None or start_date.strftime("%Y-%m") != month:
        raise ValueError(
            f"{ws_f.title}: start date {start_date} does not match manifest month {month}"
        )

    day_cols = list(range(FIRST_DAY_COL, total_col))
    col_dates = {c: _as_date(ws_v.cell(DATE_ROW, c).value) for c in day_cols}

    grand_total_row = device_total_row = off_core_subtotal_row = None
    for row in range(FIRST_DETAIL_ROW, ws_f.max_row + 1):
        label = ws_f.cell(row, 5).value
        if not isinstance(label, str):
            continue
        norm = label.strip().lower()
        if norm.startswith("total devices hours"):
            device_total_row = row
        elif norm.startswith("subtotal off hours"):
            off_core_subtotal_row = row
        elif norm.startswith("total hours"):
            grand_total_row = row
            break
    if None in (grand_total_row, device_total_row, off_core_subtotal_row):
        raise ValueError(f"{ws_f.title}: subtotal rows not found")

    detail_rows = []
    for row in range(FIRST_DETAIL_ROW, grand_total_row):
        category = ws_f.cell(row, 1).value
        label_e = ws_f.cell(row, 5).value
        if category in (None, ""):
            continue
        if isinstance(label_e, str) and label_e.strip().endswith(":"):
            continue
        detail_rows.append(row)

    return SheetLayout(
        total_col=total_col,
        day_cols=day_cols,
        col_dates=col_dates,
        grand_total_row=grand_total_row,
        device_total_row=device_total_row,
        off_core_subtotal_row=off_core_subtotal_row,
        detail_rows=detail_rows,
        start_date=start_date,
    )


def extract_legacy_domains(ws_f, layout: SheetLayout) -> dict[str, str]:
    """Category -> Domain as written in the sheet's 'Summary by Category' block.

    Some 2026 sheets carry a Domain label in column B of that block (with or
    without a 'Domain' header); 2025 sheets and the 2026 sheets without
    labels return {}. The label is used only for the sheet it appears in.
    """
    domains: dict[str, str] = {}
    start = None
    for row in range(layout.grand_total_row, ws_f.max_row + 1):
        if str(ws_f.cell(row, 1).value or "").strip().lower() == "summary by category":
            start = row
            break
    if start is None:
        return domains
    for row in range(start + 1, ws_f.max_row + 1):
        cat = ws_f.cell(row, 1).value
        dom = ws_f.cell(row, 2).value
        if cat in (None, ""):
            break
        if isinstance(dom, str) and dom.strip() and not dom.startswith("="):
            domains[str(cat).strip()] = dom.strip()
    return domains


def _row_block(row: int, layout: SheetLayout) -> str:
    if row < layout.device_total_row:
        return ROW_BLOCK_DEVICE
    if row < layout.off_core_subtotal_row:
        return ROW_BLOCK_OFF_CORE
    return ROW_BLOCK_OFF_OTHER


# --------------------------------------------------------------------------
# Detail extraction
# --------------------------------------------------------------------------

def extract_sheet(
    spec: LegacySheet,
    ws_f,
    ws_v,
) -> tuple[list[dict], list[dict], dict]:
    """Return (detail_cells, sheet_daily_totals, sheet_meta) for one sheet."""
    layout = detect_layout(ws_f, ws_v, spec.month)
    month = spec.month
    cells: list[dict] = []
    domains = extract_legacy_domains(ws_f, layout)

    for row in layout.detail_rows:
        cat = ws_f.cell(row, 1).value
        sub_f = ws_f.cell(row, 2).value
        sub_v = ws_v.cell(row, 2).value
        if isinstance(sub_f, str) and sub_f.startswith("="):
            legacy_sub = ""
            sub_source = "XLOOKUP_CATEGORY_EXAMPLE"
            cat_description = "" if sub_v in (None, 0) else str(sub_v)
        else:
            legacy_sub = "" if sub_f is None else str(sub_f).strip()
            sub_source = "LITERAL" if legacy_sub else "BLANK"
            cat_description = ""
        attrs = {
            "legacy_category": str(cat).strip(),
            "legacy_subcategory": legacy_sub,
            "legacy_subcategory_source": sub_source,
            "legacy_category_description": cat_description,
            "legacy_energy": str(ws_f.cell(row, 3).value or "").strip(),
            "legacy_time_of_day": str(ws_f.cell(row, 4).value or "").strip(),
            "legacy_device": str(ws_f.cell(row, 5).value or "").strip(),
            "legacy_goal": str(ws_f.cell(row, 6).value or "").strip(),
        }
        block = _row_block(row, layout)
        total_formula = ws_f.cell(row, layout.total_col).value
        in_report = "Y" if isinstance(total_formula, str) and total_formula.startswith("=") else "N"
        for col in layout.day_cols:
            raw = ws_f.cell(row, col).value
            cached = ws_v.cell(row, col).value
            if raw is None and cached is None:
                continue
            evaluated, entry_type = evaluate_entry(raw)
            cached_num = _num(cached)
            if cached_num is not None:
                normalized = cached_num
                value_basis = "EXCEL_CACHED_VALUE"
            elif evaluated is not None:
                normalized = evaluated
                value_basis = "EVALUATED_ENTRY"
            else:
                normalized = None
                value_basis = "NON_NUMERIC"
            cache_check = "N/A"
            if cached_num is not None and evaluated is not None:
                cache_check = "MATCH" if abs(cached_num - evaluated) < 1e-6 else "MISMATCH"
            day = layout.col_dates.get(col)
            in_period = day is not None and day.strftime("%Y-%m") == month
            cell_ref = f"{get_column_letter(col)}{row}"
            cells.append(
                {
                    "record_id": f"{spec.workbook_code}|{spec.timesheet}|{cell_ref}",
                    "source_workbook": spec.workbook,
                    "source_sheet": spec.timesheet,
                    "source_row": row,
                    "source_col": get_column_letter(col),
                    "source_cell": cell_ref,
                    "sheet_month": month,
                    "date": day.isoformat() if day else "",
                    "date_in_sheet_month": "Y" if in_period else "N",
                    "row_block": block,
                    "row_total_formula_present": in_report,
                    "legacy_domain": domains.get(attrs["legacy_category"], ""),
                    **attrs,
                    "raw_entry": raw_text(raw),
                    "entry_type": entry_type,
                    "raw_cached_value": "" if cached is None else raw_text(cached),
                    "evaluated_entry_min": "" if evaluated is None else round(evaluated, 6),
                    "cache_check": cache_check,
                    "value_basis": value_basis,
                    "raw_duration_min": "" if normalized is None else round(normalized, 6),
                }
            )

    daily = []
    for col in layout.day_cols:
        day = layout.col_dates.get(col)
        cached = _num(ws_v.cell(layout.grand_total_row, col).value)
        daily.append(
            {
                "source_workbook": spec.workbook,
                "source_sheet": spec.timesheet,
                "sheet_month": month,
                "source_cell": f"{get_column_letter(col)}{layout.grand_total_row}",
                "date": day.isoformat() if day else "",
                "date_in_sheet_month": "Y" if day and day.strftime("%Y-%m") == month else "N",
                "sheet_total_hours_cached": "" if cached is None else round(cached, 6),
            }
        )

    spill_cols = [
        get_column_letter(c)
        for c in layout.day_cols
        if not (layout.col_dates.get(c) and layout.col_dates[c].strftime("%Y-%m") == month)
    ]
    meta = {
        "source_workbook": spec.workbook,
        "source_sheet": spec.timesheet,
        "report_sheet": spec.report,
        "sheet_month": month,
        "start_date": layout.start_date.isoformat(),
        "total_column": get_column_letter(layout.total_col),
        "first_day_column": get_column_letter(layout.day_cols[0]),
        "last_day_column": get_column_letter(layout.day_cols[-1]),
        "day_columns": len(layout.day_cols),
        "in_month_day_columns": len(layout.day_cols) - len(spill_cols),
        "spillover_columns": " ".join(spill_cols),
        "detail_rows": len(layout.detail_rows),
        "device_total_row": layout.device_total_row,
        "grand_total_row": layout.grand_total_row,
        "rows_without_total_formula": " ".join(
            str(r) for r in layout.detail_rows
            if not (isinstance(ws_f.cell(r, layout.total_col).value, str)
                    and ws_f.cell(r, layout.total_col).value.startswith("="))
        ),
        "legacy_domain_map": "; ".join(f"{k}={v}" for k, v in domains.items()),
    }
    return cells, daily, meta


# --------------------------------------------------------------------------
# Legacy Report sheets
# --------------------------------------------------------------------------

REPORT_BLOCK_HEADERS = {
    "category": "CATEGORY",
    "energy": "ENERGY",
    "device": "DEVICE",
    "goal-alignment": "GOAL",
}


_CELL_REF_RE = re.compile(r"^=\+?\$?([A-Z]{1,3})\$?(\d+)-")


def _report_row(spec: LegacySheet, block: str, label: str, cell: str, raw: Any, val: Any,
                label_as_written: str | None = None) -> dict:
    return {
        "source_workbook": spec.workbook,
        "source_sheet": spec.report,
        "sheet_month": spec.month,
        "report_block": block,
        "label": label,
        "label_as_written": label if label_as_written is None else label_as_written,
        "source_cell": cell,
        "raw_formula": raw_text(raw),
        "reported_hours": "" if _num(val) is None else round(_num(val), 6),
    }


def extract_report(spec: LegacySheet, ws_f, ws_v) -> list[dict]:
    """Extract the FIRST (monthly) occurrence of each Report block.

    Some report sheets (e.g. 'Report 12') also contain a quarterly roll-up
    further down that re-uses the same block headers; only the first
    occurrence of each block is the month's own report. Later occurrences
    are emitted with report_block='EXCLUDED_ROLLUP' so they stay visible in
    the audit trail without being used.

    The capacity actually used by the report is the cell referenced by the
    'Other' row formula (e.g. '=+F2-C14'); its input cells are recorded too
    because several months carry a mis-entered capacity.
    """
    rows: list[dict] = []
    block = None
    seen: set[str] = set()
    rollup = False
    other_formula = None
    for r in range(1, ws_f.max_row + 1):
        label = ws_f.cell(r, 2).value
        if not isinstance(label, str):
            continue
        key = label.strip().lower()
        if key in REPORT_BLOCK_HEADERS:
            name = REPORT_BLOCK_HEADERS[key]
            if name in seen:
                rollup = True
            seen.add(name)
            block = name
            continue
        if block is None:
            continue
        raw = ws_f.cell(r, 3).value
        val = ws_v.cell(r, 3).value
        if rollup:
            rows.append(_report_row(spec, "EXCLUDED_ROLLUP", f"{block}:{label.strip()}", f"C{r}", raw, val))
        else:
            written = label.strip()
            norm_label = written
            # The residual row is identified by its formula (capacity - total),
            # not by its label: 'Report 6' labels it 'Household / Chores'.
            if block == "CATEGORY" and _CELL_REF_RE.match(raw_text(raw).replace(" ", "")):
                norm_label = "Other"
                other_formula = raw_text(raw)
            rows.append(_report_row(spec, block, norm_label, f"C{r}", raw, val, written))
        if key == "total":
            block = None

    # Capacity used by the report = cell referenced by the 'Other' formula.
    if other_formula:
        match = _CELL_REF_RE.match(other_formula.replace(" ", ""))
        if match:
            col, row = match.group(1), int(match.group(2))
            cap_cell = f"{col}{row}"
            rows.append(
                _report_row(spec, "CAPACITY", "Reported capacity (used by 'Other')", cap_cell,
                            ws_f[cap_cell].value, ws_v[cap_cell].value)
            )
            # Record the two inputs on the same row (hours/day, days) when the
            # capacity is a product of two cells (layout '=+E2*D2').
            cap_raw = raw_text(ws_f[cap_cell].value)
            for ref in re.findall(r"([A-Z]{1,3})(\d+)", cap_raw.lstrip("=")):
                ref_cell = f"{ref[0]}{ref[1]}"
                if ref_cell == cap_cell:
                    continue
                header = ws_v.cell(int(ref[1]) + 1, column_index_from_string(ref[0])).value
                rows.append(
                    _report_row(spec, "CAPACITY_INPUT", f"{header or ref_cell}", ref_cell,
                                ws_f[ref_cell].value, ws_v[ref_cell].value)
                )
    return rows


# --------------------------------------------------------------------------
# Workbook-level entry point
# --------------------------------------------------------------------------

def load_legacy(
    legacy_root: Path,
    sheets: Iterable[LegacySheet] = LEGACY_SHEETS,
) -> dict[str, list[dict]]:
    """Load every manifest sheet. Workbooks are opened read-only."""
    sheets = list(sheets)
    out = {"cells": [], "daily": [], "meta": [], "reported": [], "workbooks": []}
    by_book: dict[str, list[LegacySheet]] = {}
    for spec in sheets:
        by_book.setdefault(spec.workbook, []).append(spec)

    for workbook, specs in by_book.items():
        path = legacy_root / workbook
        if not path.exists():
            raise FileNotFoundError(f"Legacy workbook not found: {path}")
        stat = path.stat()
        out["workbooks"].append(
            {
                "source_workbook": workbook,
                "path": str(path),
                "bytes": stat.st_size,
                "sha256": sha256_file(path),
            }
        )
        wb_f = openpyxl.load_workbook(path, data_only=False, read_only=False)
        wb_v = openpyxl.load_workbook(path, data_only=True, read_only=False)
        try:
            for spec in specs:
                cells, daily, meta = extract_sheet(spec, wb_f[spec.timesheet], wb_v[spec.timesheet])
                out["cells"].extend(cells)
                out["daily"].extend(daily)
                out["meta"].append(meta)
                out["reported"].extend(extract_report(spec, wb_f[spec.report], wb_v[spec.report]))
        finally:
            wb_f.close()
            wb_v.close()
    return out
