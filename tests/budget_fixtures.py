"""Synthetic budget workbook and Final folders for the budget tests.

No personal data: three phases, five leaves, numbers chosen by hand. The
expected monthly totals are computed here from explicit day counts, not
with budget_core, so the reconciliation tests are independent.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

PHASES = [
    ("Fase A", date(2026, 11, 9), date(2026, 12, 19)),
    ("Fase H", date(2026, 12, 20), date(2027, 1, 3)),
    ("Fase B1", date(2027, 1, 4), date(2027, 1, 31)),
]
PHASE_NAMES = [p[0] for p in PHASES]

# Device, Category, Subcategory, Domain, Energy, Goal, h/week per phase
LEAVES = [
    ("Habit", "Productivity & Finance", "Offline Work Tracking",
     "Vocational", "Deep", "Investment", (40.0, 32.0, 40.0)),
    ("Habit", "Health & Fitness", "Sleep",
     "Physical", "Recovery", "Maintenance", (54.25, 54.25, 54.25)),
    ("Habit", "Games", "Console Time Tracking",
     "Recreational", "Passive", "Consumption", (7.0, 14.0, 7.0)),
    ("AppleScreenTime", "Social Networking", "Social Networking",
     "Relational", "Shallow", "Consumption", (7.0, 7.0, 7.0)),
    ("Off-Device", "Off-Device Life", "Untracked Maintenance",
     "Physical", "Active", "Maintenance", (59.75, 60.75, 59.75)),
]

# Line, class, leisure component, leaf index, h/week per phase
LINES = [
    ("Trabajo Thryve (sin almuerzo)", "Compromiso", "", 0, (40.0, 32.0, 40.0)),
    ("Sueño", "Proteger", "", 1, (54.25, 54.25, 54.25)),
    ("Ocio digital (residual)", "Residual", "Juegos", 2, (7.0, 14.0, 7.0)),
    ("Ocio digital (residual)", "Residual", "Redes sociales", 3, (7.0, 7.0, 7.0)),
    ("Almuerzo laboral", "Compromiso", "", 4, (5.0, 4.0, 5.0)),
    ("Mantenimiento personal", "Proteger", "", 4, (54.75, 56.75, 54.75)),
]

# Days of each phase per month (independent of the code under test).
MONTH_PHASE_DAYS = {
    "Nov-26": (22, 0, 0),
    "Dic-26": (19, 12, 0),
    "Ene-27": (0, 3, 28),
}


def make_workbook(
    path: Path,
    phases=None,
    leaves=None,
    lines=None,
    recon_shift: float = 0.0,
    formula_cell: str | None = None,
) -> Path:
    import openpyxl

    phases = phases or PHASES
    leaves = leaves if leaves is not None else LEAVES
    lines = lines if lines is not None else LINES
    names = [p[0] for p in phases]
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws = wb.create_sheet("Supuestos")
    ws["A4"], ws["B4"], ws["C4"] = "Fase", "Inicio", "Fin"
    for i, (name, start, end) in enumerate(phases):
        ws.cell(5 + i, 1, name)
        ws.cell(5 + i, 2, start)
        ws.cell(5 + i, 3, end)
    ws.cell(11, 1, "Parámetro")
    ws.cell(11, 2, "Unidad")
    for j, name in enumerate(names):
        ws.cell(11, 3 + j, name)
    for r, (label, value) in enumerate(
        [("Sueño por noche", 7.75), ("Piso de sueño por noche", 7.5),
         ("Piso de ocio digital", 60)], start=12,
    ):
        ws.cell(r, 1, label)
        for j in range(len(names)):
            ws.cell(r, 3 + j, value)

    ws = wb.create_sheet("Detalle_Tracker")
    header = ["Device", "Category", "Subcategory", "Domain", "Energy", "Goal",
              "Oct-26 real", *names]
    for c, h in enumerate(header, start=1):
        ws.cell(4, c, h)
    for i, leaf in enumerate(leaves):
        for c, v in enumerate([*leaf[:6], 0.0, *leaf[6]], start=1):
            ws.cell(5 + i, c, v)
    ws.cell(5 + len(leaves), 1, "Total")
    if formula_cell:
        ws[formula_cell] = "=1+1"

    ws = wb.create_sheet("Datos")
    header = ["Línea", "Componente ocio", "Tipo", "Device", "Category",
              "Subcategory", "Domain", "Energy", "Goal", "Sept-26 min",
              "Oct-26 min", "Peso fijo", *names]
    for c, h in enumerate(header, start=1):
        ws.cell(5, c, h)
    for i, (line, _cls, comp, leaf_index, hours) in enumerate(lines):
        leaf = leaves[leaf_index]
        values = [line, comp or None, "Real", *leaf[:6], 0, 0, None, *hours]
        for c, v in enumerate(values, start=1):
            ws.cell(6 + i, c, v)
    ws.cell(6 + len(lines), 1, "Total")

    ws = wb.create_sheet("Diagnostico_Lineas")
    header = ["Línea", "Clase", "Mediana 13m", "Sept-26", "Oct-26", *names]
    for c, h in enumerate(header, start=1):
        ws.cell(4, c, h)
    totals: dict[str, list[float]] = {}
    classes = {}
    for line, cls, _comp, _leaf, hours in lines:
        acc = totals.setdefault(line, [0.0] * len(names))
        for j, h in enumerate(hours):
            acc[j] += h
        classes[line] = cls
    for i, (line, hours) in enumerate(totals.items()):
        for c, v in enumerate([line, classes[line], None, 0, 0, *hours], start=1):
            ws.cell(5 + i, c, v)
    ws.cell(5 + len(totals), 1, "Total")

    ws = wb.create_sheet("Presupuesto_Tracker")
    row = 4
    dims = {"Device": 0, "Goal": 5, "Energy": 4, "Domain": 3, "Category": 1}
    for dim, idx in dims.items():
        ws.cell(row, 1, dim)
        ws.cell(row, 2, "Oct-26 real")
        for j, name in enumerate(names):
            ws.cell(row, 3 + j, name)
        sums: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(names))
        for leaf in leaves:
            for j, h in enumerate(leaf[6]):
                sums[leaf[idx]][j] += h
        row += 1
        for k, (value, hours) in enumerate(sums.items()):
            ws.cell(row, 1, value)
            ws.cell(row, 2, 0)
            for j, h in enumerate(hours):
                ws.cell(row, 3 + j, h + (recon_shift if k == 0 and j == 0 else 0.0))
            row += 1
        ws.cell(row, 1, "Total")
        row += 2

    if len(names) == 3:
        ws.cell(row, 1, "Category (horas por mes)")
        months = list(MONTH_PHASE_DAYS)
        for j, m in enumerate(months):
            ws.cell(row, 2 + j, m)
        ws.cell(row, 2 + len(months), "Total")
        row += 1
        cat_month: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(months))
        for leaf in leaves:
            for j, m in enumerate(months):
                cat_month[leaf[1]][j] += sum(
                    h * d / 7.0 for h, d in zip(leaf[6], MONTH_PHASE_DAYS[m])
                )
        for value, hours in [*cat_month.items(),
                             ("Total", [sum(c[j] for c in cat_month.values())
                                        for j in range(len(months))])]:
            ws.cell(row, 1, value)
            for j, h in enumerate(hours):
                ws.cell(row, 2 + j, h)
            ws.cell(row, 2 + len(months), sum(hours))
            row += 1

    wb.save(path)
    return path


def make_taxonomy(root: Path, overrides=None) -> Path:
    """Minimal Dim files consistent with LEAVES (overrides → mismatches)."""
    root.mkdir(parents=True, exist_ok=True)

    def write(name, header, rows):
        with (root / name).open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    activity = [
        ("Habit", "Offline Work Tracking", "Productivity & Finance", "Vocational", "Deep", "Investment", "Override"),
        ("Habit", "Sleep", "Health & Fitness", "Physical", "Recovery", "Maintenance", "Override"),
        ("Habit", "Console Time Tracking", "Games", "Recreational", "Passive", "Consumption", "Override"),
    ]
    for row in overrides or []:
        activity = [a for a in activity if a[1] != row[1]] + [row]
    write("Dim_Activity.csv",
          ["Source", "Activity_Name", "Canonical_Category", "Domain", "Energy", "Goal", "Mapping_Type"],
          activity)
    write("Dim_Category_Default.csv", ["Canonical_Category", "Domain", "Energy", "Goal"], [
        ("Social Networking", "Relational", "Shallow", "Consumption"),
        ("Games", "Recreational", "Passive", "Consumption"),
        ("Productivity & Finance", "Vocational", "Deep", "Investment"),
        ("Health & Fitness", "Physical", "Active", "Investment"),
    ])
    write("Dim_Domain.csv", ["Domain", "Sort_Order"],
          [(d, i) for i, d in enumerate(["Vocational", "Intellectual", "Economical", "Relational", "Physical", "Recreational", "Spiritual"])])
    write("Dim_Energy.csv", ["Energy", "Sort_Order"],
          [(d, i) for i, d in enumerate(["Deep", "Shallow", "Active", "Passive", "Recovery"])])
    write("Dim_Goal.csv", ["Goal", "Sort_Order"],
          [(d, i) for i, d in enumerate(["Investment", "Maintenance", "Consumption"])])
    return root


LEAF_LEVEL = "Device → Category → Subcategory → Domain → Energy → Goal"
DIMS = ["Device", "Category", "Subcategory", "Domain", "Energy", "Goal"]


def make_final(root: Path, start: date, end: date, daily_minutes: dict[tuple, float]) -> Path:
    """Write a Final folder whose every day has ``daily_minutes`` per leaf."""
    label = f"{start}_{end}"
    folder = root / label
    folder.mkdir(parents=True, exist_ok=True)
    totals: dict[tuple, float] = defaultdict(float)
    with (folder / f"Final_Analysis_{label}.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Source", *DIMS, "Duration_sec", "Duration_min"])
        day = start
        while day <= end:
            for key, minutes in daily_minutes.items():
                w.writerow([day.isoformat(), key[0], *key, minutes * 60, minutes])
                totals[key] += minutes
            day += timedelta(days=1)
    with (folder / f"Final_Analysis_By_Device_Category_Subcategory_Domain_Energy_Goal_{label}.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as fh:
        w = csv.writer(fh)
        w.writerow(["Analysis_Level", *DIMS, "Duration_sec", "Duration_min"])
        for key, minutes in totals.items():
            w.writerow([LEAF_LEVEL, *key, minutes * 60, minutes])
    return folder
