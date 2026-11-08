"""Tests for the Phase 2 historical pipeline (stdlib unittest + openpyxl).

All workbooks used here are synthetic and generated in a temp folder, so the
tests contain no personal data. One integration test runs against the real
legacy workbooks / current Final outputs only when they are present locally
(they are git-ignored) and is skipped otherwise.
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

HAS_OPENPYXL = importlib.util.find_spec("openpyxl") is not None

if HAS_OPENPYXL:
    import openpyxl

    import historical_bridge as hb
    import historical_legacy_loader as hl
    import historical_monthly as hm
    from config.historical import LegacySheet, REGIME_CURRENT, REGIME_LEGACY, REGIME_TRANSITION


def _make_workbook(path: Path, month_start: dt.date, days: int, extra_cols: int = 0,
                   household_values=None, sleep_values=None, row_without_total: bool = False) -> LegacySheet:
    """Build a minimal legacy-layout workbook (literal values, no caches)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Timesheet 1"
    ws["B4"] = dt.datetime.combine(month_start, dt.time())
    ncols = days + extra_cols
    first = 7
    total_col = first + ncols
    for i in range(ncols):
        ws.cell(9, first + i, dt.datetime.combine(month_start + dt.timedelta(days=i), dt.time()))
    headers = ["Category", "Subcategory", "Energy Level", "Time of Day", "Device", "Goal Alignment"]
    for c, h in enumerate(headers, start=1):
        ws.cell(10, c, h)
    last = openpyxl.utils.get_column_letter(total_col - 1)
    rows = [
        (11, ["Work / Study", "=+_xlfn.XLOOKUP(A11,Categories!A:A,Categories!B:B,0)", "Deep Work", "Midday", "Laptop", "High-Value"]),
        (12, ["Entertainment", "=+_xlfn.XLOOKUP(A12,Categories!A:A,Categories!B:B,0)", "Passive", "Morning", "Phone", "Low-Value / Drift"]),
    ]
    for r, vals in rows:
        for c, v in enumerate(vals, start=1):
            ws.cell(r, c, v)
        ws.cell(r, total_col, f"=SUM(G{r}:{last}{r})/60")
    ws.cell(13, 5, "Total Devices Hours:")
    sleep = [("Health & Wellness", "Sleep", "Recovery / Rest", "Off Hours", "Off-Device", "High-Value")]
    for c, v in enumerate(sleep[0], start=1):
        ws.cell(14, c, v)
    ws.cell(14, total_col, f"=SUM(G14:{last}14)/60")
    hh = ("Household / Chores", "Eating, bathroom, shower", "Recovery / Rest", "Off Hours", "Off-Device", "Maintenance")
    for c, v in enumerate(hh, start=1):
        ws.cell(15, c, v)
    ws.cell(15, total_col, f"=SUM(G15:{last}15)/60")
    ws.cell(16, 5, "Subtotal Off Hours:")
    pa = ("Personal Admin", "=+_xlfn.XLOOKUP(A17,Categories!A:A,Categories!B:B,0)", "Shallow Work", "Off Hours", "Off-Device", "Maintenance")
    for c, v in enumerate(pa, start=1):
        ws.cell(17, c, v)
    if not row_without_total:
        ws.cell(17, total_col, f"=SUM(G17:{last}17)/60")
    ws.cell(18, 5, "Total Other Off Hours:")
    ws.cell(19, 5, "Total Hours:")
    household_values = household_values or ["=30+30+15+60+15+30"] * days
    sleep_values = sleep_values or ["=8*60"] * days
    for i in range(ncols):
        col = first + i
        in_month = i < days
        ws.cell(11, col, 120 if in_month else 0)
        ws.cell(12, col, "=30+15" if in_month else 0)
        ws.cell(14, col, sleep_values[i] if in_month else 0)
        ws.cell(15, col, household_values[i] if in_month else 0)
        ws.cell(17, col, 10 if in_month else 0)
    rep = wb.create_sheet("Report 1")
    rep["D2"], rep["E2"], rep["F2"] = 24, days, 24 * days
    rep["D3"], rep["E3"] = "Hours", "Days"
    rep["B3"] = "Category"
    rep["B4"], rep["C4"] = "Work / Study", 1.0
    rep["B5"], rep["C5"] = "Household / Chores", "=+F2-C6"  # mislabelled residual row
    rep["B6"], rep["C6"] = "Total", "=SUM(C4:C4)"
    rep["B9"] = "Category"  # second (roll-up) block must be ignored
    rep["B10"], rep["C10"] = "Work / Study", 99.0
    rep["B11"], rep["C11"] = "Total", 99.0
    wb.save(path)
    month = month_start.strftime("%Y-%m")
    return LegacySheet(path.name, "TEST", "Timesheet 1", "Report 1", month)


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl not installed")
class EvaluateEntryTests(unittest.TestCase):
    def test_arithmetic_and_numbers(self):
        self.assertEqual(hl.evaluate_entry("=60*3+15"), (195.0, "ARITHMETIC_FORMULA"))
        self.assertEqual(hl.evaluate_entry("=(10+20)"), (30.0, "ARITHMETIC_FORMULA"))
        self.assertEqual(hl.evaluate_entry("=+7.35*60")[0], 7.35 * 60)
        self.assertEqual(hl.evaluate_entry(45), (45.0, "NUMBER"))
        self.assertEqual(hl.evaluate_entry(None), (None, "BLANK"))

    def test_references_are_not_evaluated(self):
        self.assertEqual(hl.evaluate_entry("=+G45")[1], "REFERENCE_FORMULA")
        self.assertEqual(hl.evaluate_entry("=__import__('os')")[1], "REFERENCE_FORMULA")


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl not installed")
class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_layout_spillover_and_provenance(self):
        spec = _make_workbook(self.root / "wb.xlsx", dt.date(2026, 2, 1), 28, extra_cols=3)
        out = hl.load_legacy(self.root, [spec])
        meta = out["meta"][0]
        self.assertEqual(meta["in_month_day_columns"], 28)
        self.assertEqual(meta["spillover_columns"].split(), ["AI", "AJ", "AK"])
        spill = [c for c in out["cells"] if c["date_in_sheet_month"] == "N"]
        self.assertTrue(spill)
        self.assertTrue(all(c["date"].startswith("2026-03") for c in spill))
        cell = next(c for c in out["cells"] if c["source_cell"] == "G12")
        self.assertEqual(cell["record_id"], "TEST|Timesheet 1|G12")
        self.assertEqual(cell["raw_entry"], "=30+15")
        self.assertEqual(cell["raw_duration_min"], 45.0)
        self.assertEqual(cell["value_basis"], "EVALUATED_ENTRY")  # no Excel cache in synthetic file
        self.assertEqual(cell["legacy_subcategory"], "")
        self.assertEqual(cell["legacy_subcategory_source"], "XLOOKUP_CATEGORY_EXAMPLE")
        self.assertEqual(out["workbooks"][0]["sha256"], hl.sha256_file(self.root / "wb.xlsx"))

    def test_report_first_block_and_residual_by_formula(self):
        spec = _make_workbook(self.root / "wb.xlsx", dt.date(2026, 4, 1), 30)
        rep = hl.load_legacy(self.root, [spec])["reported"]
        labels = [(r["report_block"], r["label"]) for r in rep]
        self.assertIn(("CATEGORY", "Other"), labels)  # mislabelled 'Household / Chores' row
        other = next(r for r in rep if r["label"] == "Other")
        self.assertEqual(other["label_as_written"], "Household / Chores")
        self.assertEqual(sum(1 for b, l in labels if b == "CATEGORY" and l == "Work / Study"), 1)
        self.assertTrue(any(b == "EXCLUDED_ROLLUP" for b, _ in labels))
        cap = next(r for r in rep if r["report_block"] == "CAPACITY")
        self.assertEqual(cap["source_cell"], "F2")
        self.assertEqual(cap["reported_hours"], 720.0)

    def test_row_without_total_formula_flagged(self):
        spec = _make_workbook(self.root / "wb.xlsx", dt.date(2025, 11, 1), 30, row_without_total=True)
        out = hl.load_legacy(self.root, [spec])
        self.assertEqual(out["meta"][0]["rows_without_total_formula"], "17")
        row17 = [c for c in out["cells"] if c["source_row"] == 17]
        self.assertTrue(all(c["row_total_formula_present"] == "N" for c in row17))

    def test_manifest_month_mismatch_fails_loudly(self):
        spec = _make_workbook(self.root / "wb.xlsx", dt.date(2026, 4, 1), 30)
        wrong = LegacySheet(spec.workbook, "TEST", spec.timesheet, spec.report, "2026-05")
        with self.assertRaises(ValueError):
            hl.load_legacy(self.root, [wrong])


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl not installed")
class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tables = hb.BridgeTables.load()

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self, **kw):
        spec = _make_workbook(self.root / "wb.xlsx", dt.date(2026, 4, 1), 30, **kw)
        out = hl.load_legacy(self.root, [spec])
        return out, hb.normalize_legacy(out["cells"], self.tables)

    def test_mapping_precedence_and_activity_reclassification(self):
        _, recs = self._load()
        sleep = next(r for r in recs if r["legacy_subcategory"] == "Sleep" and r["accounting_status"] == "INCLUDED")
        self.assertEqual(sleep["bridge_group"], "HEALTH_WELLNESS")
        self.assertEqual(sleep["canonical_goal"], "Maintenance")   # activity-based, not the legacy 'High-Value' label
        self.assertEqual(sleep["legacy_goal"], "High-Value")       # legacy value preserved
        pa_off = next(r for r in recs if r["legacy_category"] == "Personal Admin" and r["legacy_device"] == "Off-Device")
        self.assertEqual(pa_off["canonical_subcategory"], "Personal Time Tracking")  # exact device rule wins over '*'
        work = next(r for r in recs if r["legacy_category"] == "Work / Study" and r["accounting_status"] == "INCLUDED")
        self.assertEqual((work["canonical_energy"], work["energy_status"]), ("Deep", "HIGH_CONFIDENCE"))
        self.assertEqual(work["canonical_device"], "LAPTOP")

    def test_recovery_rest_label_is_not_silently_mapped(self):
        canonical, status = self.tables.label("energy", "Recovery / Rest")
        self.assertEqual(canonical, "")
        self.assertEqual(status, "REVIEW_REQUIRED")

    def test_standard_allocation_rule(self):
        values = ["=30+30+15+60+15+30"] * 26 + [90, 120, 150, 200]
        _, recs = self._load(household_values=values)
        hh = [r for r in recs if r["legacy_category"] == "Household / Chores" and r["accounting_status"] == "INCLUDED"]
        std = [r for r in hh if r["allocation_basis"] == "STANDARD_DAILY_ALLOCATION"]
        self.assertEqual(len(std), 26)
        self.assertEqual(sum(1 for r in hh if r["allocation_basis"] == "ESTIMATE_NON_TEMPLATE"), 4)
        self.assertTrue(all(r["normalized_duration_min"] == 180 for r in std))  # kept, not deleted

    def test_over_24h_overlap_is_day_level_not_deletion(self):
        sleep = ["=8*60"] * 30
        sleep[0] = "=20*60"  # day 1: 120+45+1200+180+10 = 1555 min
        out, recs = self._load(sleep_values=sleep)
        daily = hb.legacy_daily(recs, out["daily"])
        d1 = next(d for d in daily if d["date"] == "2026-04-01")
        self.assertAlmostEqual(d1["recorded_min"], 1555.0)
        self.assertAlmostEqual(d1["overlap_min"], 115.0)
        self.assertAlmostEqual(d1["accounted_min"], 1440.0)
        self.assertIn("OVER_CAPACITY", d1["day_flags"])
        self.assertEqual(sum(1 for r in recs if r["date"] == "2026-04-01" and r["accounting_status"] == "INCLUDED"), 5)
        d2 = next(d for d in daily if d["date"] == "2026-04-02")
        self.assertEqual(d2["overlap_min"], 0.0)
        self.assertEqual(d2["day_status"], "REVIEW")  # 1440 - 835 > 120 min tolerance

    def test_spillover_excluded_from_accounting(self):
        spec = _make_workbook(self.root / "wb2.xlsx", dt.date(2026, 6, 1), 30, extra_cols=1)
        out = hl.load_legacy(self.root, [spec])
        recs = hb.normalize_legacy(out["cells"], self.tables)
        spill = [r for r in recs if r["date"] == "2026-07-01"]
        self.assertTrue(spill)
        self.assertTrue(all(r["accounting_status"] == "EXCLUDED_OUT_OF_PERIOD" for r in spill))

    def test_current_mapping(self):
        rule = self.tables.current_rule("AppleScreenTime", "Social Networking", "Social Networking")
        self.assertEqual(rule["bridge_group"], "DIGITAL_SOCIAL_UNSPLIT")
        rule = self.tables.current_rule("ActivityWatch", "Social Networking", "Messaging")
        self.assertEqual(rule["bridge_group"], "COMMUNICATION")
        rule = self.tables.current_rule("Habit", "Health & Fitness", "Sleep")
        self.assertEqual(rule["bridge_group"], "HEALTH_WELLNESS")
        self.assertEqual(self.tables.current_rule("Off-Device", "Off-Device Life", "Untracked Maintenance")["bridge_group"], "RESIDUAL")
        self.assertEqual(hb.current_device_class("ActivityWatch", "ActivityWatch", "x", "Laptop", "Desktop"), "COMPUTERS_TOTAL")
        self.assertEqual(hb.current_device_class("Laptop", "ActivityWatch", "x", "Laptop", "Desktop"), "LAPTOP")

    def test_unknown_bridge_group_rejected(self):
        cfg = self.root / "cfg"
        cfg.mkdir()
        src = ROOT / "config" / "historical_bridge"
        for name in ("bridge_groups.csv", "legacy_label_maps.csv", "current_activity_bridge.csv"):
            (cfg / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
        text = (src / "legacy_activity_bridge.csv").read_text(encoding="utf-8").replace("WORK_STUDY", "NOT_A_GROUP", 1)
        (cfg / "legacy_activity_bridge.csv").write_text(text, encoding="utf-8")
        with self.assertRaises(ValueError):
            hb.BridgeTables.load(cfg)


@unittest.skipUnless(HAS_OPENPYXL, "openpyxl not installed")
class MonthlyTests(unittest.TestCase):
    def _long(self, month, regime, value, comp):
        return {"month": month, "regime": regime, "dimension": "bridge_group", "value": value,
                "min_per_day": 100.0 if month.endswith(("07", "09")) else 150.0, "comparability": comp}

    def test_change_classification(self):
        wide = [{"month": "2026-06", "regime": REGIME_LEGACY}, {"month": "2026-07", "regime": REGIME_LEGACY},
                {"month": "2026-08", "regime": REGIME_TRANSITION}, {"month": "2026-09", "regime": REGIME_CURRENT}]
        long_rows = []
        for w in wide:
            long_rows.append(self._long(w["month"], w["regime"], "WORK_STUDY", hm.WITH_CAVEATS))
            long_rows.append(self._long(w["month"], w["regime"], "HOUSEHOLD_DAILY_LIVING", hm.LEGACY_ONLY))
        mom = {(m["month"], m["metric"]): m for m in hm.month_over_month(long_rows, wide)}
        self.assertEqual(mom[("2026-07", "WORK_STUDY")]["change_type"], "POSSIBLE_METHODOLOGICAL")  # Jul 27-31 break
        self.assertEqual(mom[("2026-08", "WORK_STUDY")]["change_type"], "METHODOLOGICAL_BREAK")
        self.assertEqual(mom[("2026-08", "HOUSEHOLD_DAILY_LIVING")]["change_type"], "NOT_COMPARABLE")
        self.assertAlmostEqual(mom[("2026-07", "WORK_STUDY")]["pct_change"], -33.3333, places=3)

    def test_month_range(self):
        months = hm.month_range()
        self.assertEqual(months[0], "2025-09")
        self.assertEqual(months[-1], "2026-09")
        self.assertEqual(len(months), 13)


REAL_LEGACY = ROOT / "input" / "Historical" / "Legacy"
REAL_SEP = ROOT / "output" / "Integrated" / "Analysis" / "Final" / "2026-09-01_2026-09-30"


@unittest.skipUnless(
    HAS_OPENPYXL and REAL_LEGACY.exists() and any(REAL_LEGACY.glob("*.xlsx")) and REAL_SEP.exists(),
    "real (git-ignored) historical data not present",
)
class RealDataIntegrationTest(unittest.TestCase):
    """Runs only on the machine that holds the private data."""

    @classmethod
    def setUpClass(cls):
        import historical_pipeline as hp
        cls.hp = hp
        cls.tmp = tempfile.TemporaryDirectory()
        cls.result = hp.run(REAL_LEGACY, Path(cls.tmp.name), write=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_checks_pass(self):
        failed = [c for c in self.result["checks"] if c["result"] == "FAIL"]
        self.assertEqual(failed, [])

    def test_september_unchanged_reference(self):
        sep = next(w for w in self.result["wide"] if w["month"] == "2026-09")
        self.assertAlmostEqual(sep["accounted_h"] * 60, 38461.779, places=1)
        self.assertEqual(sep["regime"], REGIME_CURRENT)

    def test_thirteen_months(self):
        self.assertEqual([w["month"] for w in self.result["wide"]], hm.month_range())


if __name__ == "__main__":
    unittest.main()
