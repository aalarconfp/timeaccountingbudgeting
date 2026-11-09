"""Tests for Actual vs Budget: classification, signs, KPIs, outputs, splice."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import budget_core as core  # noqa: E402
import budget_import as bi  # noqa: E402
import budget_variance_analysis as bva  # noqa: E402
from config import budget as cfg  # noqa: E402
from tests import budget_fixtures as fx  # noqa: E402

WORK = ("Habit", "Productivity & Finance", "Offline Work Tracking", "Vocational", "Deep", "Investment")
SLEEP = ("Habit", "Health & Fitness", "Sleep", "Physical", "Recovery", "Maintenance")
GAMES = ("Habit", "Games", "Console Time Tracking", "Recreational", "Passive", "Consumption")
SOCIAL = ("AppleScreenTime", "Social Networking", "Social Networking", "Relational", "Shallow", "Consumption")
OFF = ("Off-Device", "Off-Device Life", "Untracked Maintenance", "Physical", "Active", "Maintenance")
AI = ("AsusLaptop", "Education", "AI Tools Learning", "Intellectual", "Deep", "Investment")

DEC = (date(2026, 12, 1), date(2026, 12, 31))
NOV = (date(2026, 11, 1), date(2026, 11, 30))
OCT = (date(2026, 10, 1), date(2026, 10, 31))


def daily(sleep=480.0):
    return {SLEEP: sleep, GAMES: 120.0, SOCIAL: 30.0, AI: 60.0,
            OFF: 1440.0 - sleep - 120.0 - 30.0 - 60.0}


class VarianceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.budget_root = self.dir / "Reference" / "Budget"
        bi.import_budget(
            fx.make_workbook(self.dir / "Time_Budget_test_v1.xlsx"),
            self.budget_root,
            taxonomy_root=fx.make_taxonomy(self.dir / "Taxonomy"),
        )
        self.snap = core.load_snapshot(self.budget_root)
        self.final_root = self.dir / "Final"

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, period=DEC, minutes=None):
        folder = fx.make_final(self.final_root, *period, minutes or daily())
        return core.compute_variance(self.snap, *period, folder)

    @staticmethod
    def leaf(result, subcategory):
        return next(r for r in result.leaf_rows if r["Subcategory"] == subcategory)


class ClassificationTests(VarianceTestCase):
    def test_status_classification(self):
        r = self.result()
        self.assertEqual(self.leaf(r, "Offline Work Tracking")["Status"], "Not recorded")
        self.assertEqual(self.leaf(r, "AI Tools Learning")["Status"], "Unbudgeted")
        self.assertEqual(self.leaf(r, "Sleep")["Status"], "Budgeted")

    def test_not_recorded_is_capture_alert_not_saving(self):
        r = self.result()
        work = self.leaf(r, "Offline Work Tracking")
        self.assertEqual(work["Actual_min"], 0.0)
        self.assertAlmostEqual(work["Budget_min"], (40 * 19 + 32 * 12) / 7 * 60)
        self.assertEqual(work["Assessment"], "Capture alert")
        goal = {g["Goal"]: g for g in r.rollups["Goal"]}
        inv = goal["Investment"]
        self.assertAlmostEqual(inv["Not_Recorded_Budget_min"], work["Budget_min"])
        self.assertEqual(inv["Not_Recorded_Leaves"], 1)
        self.assertIn("excl. not recorded", inv["Assessment"])
        not_rec = r.kpi("Not recorded leaves")
        self.assertEqual(not_rec["Actual"], 1)

    def test_immaterial_budget_is_not_an_alert(self):
        self.assertEqual(core.classify_status(0.0, cfg.MATERIAL_BUDGET_MIN - 1), "Immaterial")
        self.assertEqual(core.classify_status(0.0, cfg.MATERIAL_BUDGET_MIN), "Not recorded")
        self.assertEqual(core.classify_status(0.0, 0.0), "Inactive")
        self.assertEqual(core.classify_status(5.0, 0.0), "Unbudgeted")


class SignTests(VarianceTestCase):
    def test_variance_is_actual_minus_budget(self):
        r = self.result()
        games = self.leaf(r, "Console Time Tracking")
        budget = (7 * 19 + 14 * 12) / 7 * 60
        self.assertAlmostEqual(games["Budget_min"], budget)
        self.assertAlmostEqual(games["Actual_min"], 120 * 31)
        self.assertAlmostEqual(games["Variance_min"], 120 * 31 - budget)
        self.assertAlmostEqual(games["Variance_h"], (120 * 31 - budget) / 60)
        self.assertAlmostEqual(games["Variance_pct"], (120 * 31 - budget) / budget * 100)

    def test_goal_based_assessment(self):
        r = self.result()
        self.assertEqual(self.leaf(r, "Console Time Tracking")["Assessment"], "Unfavorable")  # more consumption
        self.assertEqual(self.leaf(r, "Social Networking")["Assessment"], "Favorable")       # less consumption
        self.assertEqual(self.leaf(r, "AI Tools Learning")["Assessment"], "Favorable")       # more investment
        self.assertEqual(self.leaf(r, "Untracked Maintenance")["Assessment"], "Neutral (residual)")
        self.assertEqual(core.assess_goal("Investment", -30), "Unfavorable")
        self.assertEqual(core.assess_goal("Consumption", 30), "Unfavorable")
        self.assertEqual(core.assess_goal("Maintenance", 500), "Neutral")
        self.assertEqual(core.assess_goal("Investment", 0.5), "On budget")

    def test_sleep_against_floor(self):
        above = self.leaf(self.result(minutes=daily(sleep=480)), "Sleep")
        self.assertEqual(above["Assessment"], "Favorable")
        below = self.leaf(self.result(minutes=daily(sleep=420)), "Sleep")
        self.assertEqual(below["Assessment"], "Unfavorable")
        self.assertIn("7.5 h/night", below["Assessment_Basis"])

    def test_goal_rollup_matches_final_classification(self):
        r = self.result()
        goal = {g["Goal"]: g for g in r.rollups["Goal"]}
        self.assertAlmostEqual(goal["Consumption"]["Actual_min"], 150 * 31)
        self.assertEqual(goal["Maintenance"]["Assessment"], "Neutral")
        self.assertAlmostEqual(sum(g["Budget_min"] for g in r.rollups["Goal"]), 31 * 1440)


class KpiAndCoverageTests(VarianceTestCase):
    def test_reconciliation_and_kpis(self):
        r = self.result()
        self.assertTrue(r.reconciliation["ok"])
        self.assertAlmostEqual(r.reconciliation["budget_min"], 31 * 1440)
        self.assertAlmostEqual(r.kpi("Discretionary consumption proxy min/day")["Actual"], 150.0)
        leisure = r.kpi("Digital leisure min/day")
        self.assertAlmostEqual(leisure["Actual"], 150.0)
        self.assertEqual(leisure["Floor"], 60.0)
        self.assertEqual(leisure["Status"], "At or above floor")
        self.assertAlmostEqual(r.kpi("Sleep h/night")["Actual"], 8.0)
        self.assertEqual(len(r.top), cfg.TOP_DEVIATIONS if len(r.leaf_rows) >= 10 else len(r.leaf_rows))
        self.assertEqual(r.top[0]["Subcategory"], "Offline Work Tracking")

    def test_partial_coverage_compares_covered_days_exactly(self):
        r = self.result(period=NOV)
        self.assertEqual(r.coverage.covered_days, 22)
        self.assertIn("Daily Final rows", r.actual_method)
        self.assertAlmostEqual(self.leaf(r, "Sleep")["Actual_min"], 480 * 22)
        self.assertAlmostEqual(r.reconciliation["budget_min"], 22 * 1440)
        total = next(v for v in r.full_period_view if v["Goal"] == "Total")
        self.assertAlmostEqual(total["Actual_full_period_min"], 1440 * 30)
        self.assertAlmostEqual(total["Actual_covered_min"], 1440 * 22)

    def test_no_coverage_degrades(self):
        r = self.result(period=OCT)
        self.assertFalse(r.has_coverage)
        self.assertEqual(r.leaf_rows, [])


class CliTests(VarianceTestCase):
    def run_cli(self, period, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = bva.main([
                "--start-date", period[0].isoformat(), "--end-date", period[1].isoformat(),
                "--budget-root", str(self.budget_root), "--final-root", str(self.final_root),
                "--output-root", str(self.dir / "Budget"), "--report-root", str(self.dir / "Report"),
                *extra,
            ])
        return code, out.getvalue()

    def test_cli_no_coverage_writes_coverage_only(self):
        fx.make_final(self.final_root, *OCT, daily())
        code, text = self.run_cli(OCT)
        self.assertEqual(code, 0)
        self.assertIn("NO BUDGET COVERAGE", text)
        files = sorted(p.name for p in (self.dir / "Budget" / "2026-10-01_2026-10-31").iterdir())
        self.assertEqual(files, [
            "Budget_Coverage_2026-10-01_2026-10-31.csv",
            "Budget_Section_2026-10-01_2026-10-31.md",
            "Budget_Variance_Report_2026-10-01_2026-10-31.txt",
        ])

    def test_cli_full_run_and_overwrite_guard(self):
        fx.make_final(self.final_root, *DEC, daily())
        code, text = self.run_cli(DEC)
        self.assertEqual(code, 0, text)
        folder = self.dir / "Budget" / "2026-12-01_2026-12-31"
        for stem in ("Budget_Variance_Leaf", "Budget_Variance_Goal", "Budget_Variance_Line",
                     "Budget_KPI", "Budget_Top_Deviations", "Budget_Coverage"):
            self.assertTrue((folder / f"{stem}_2026-12-01_2026-12-31.csv").exists(), stem)
        self.assertEqual(self.run_cli(DEC)[0], 1)          # exists, no --force
        self.assertEqual(self.run_cli(DEC, "--force")[0], 0)

    def test_cli_without_snapshot_is_clean(self):
        fx.make_final(self.final_root, *DEC, daily())
        self.budget_root = self.dir / "empty"
        code, text = self.run_cli(DEC)
        self.assertEqual(code, 0)
        self.assertIn("NOT AVAILABLE", text)


SUMMARY = """# Month — Monthly KPI Summary

## 1. Capacity

x

## Time budget

Budget: **NOT AVAILABLE** — no formal time budget exists.

## Habit before / after manual adjustment

y

## Historical comparison (normalized)

### A vs B

z
"""


class SpliceTests(unittest.TestCase):
    SECTION = f"{cfg.KPI_SECTION_BEGIN}\n## Actual vs Budget\n\nbody\n\n{cfg.KPI_SECTION_END}\n"

    def test_replaces_not_available_and_places_after_history(self):
        out = bva.splice_kpi_summary(SUMMARY, self.SECTION)
        self.assertNotIn("NOT AVAILABLE", out)
        self.assertNotIn("## Time budget", out)
        self.assertGreater(out.index("## Actual vs Budget"), out.index("### A vs B"))
        self.assertIn("## Habit before / after manual adjustment", out)

    def test_idempotent(self):
        once = bva.splice_kpi_summary(SUMMARY, self.SECTION)
        self.assertEqual(bva.splice_kpi_summary(once, self.SECTION), once)
        changed = bva.splice_kpi_summary(once, self.SECTION.replace("body", "new"))
        self.assertIn("new", changed)
        self.assertEqual(changed.count("## Actual vs Budget"), 1)

    def test_inserted_before_section_following_history(self):
        text = SUMMARY + "\n## Notes\n\nn\n"
        out = bva.splice_kpi_summary(text, self.SECTION)
        self.assertLess(out.index("### A vs B"), out.index("## Actual vs Budget"))
        self.assertLess(out.index("## Actual vs Budget"), out.index("## Notes"))


if __name__ == "__main__":
    unittest.main()
