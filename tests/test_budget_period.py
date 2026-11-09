"""Tests for the period budget: phase proration and coverage (budget_core)."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import budget_core as core  # noqa: E402
from tests import budget_fixtures as fx  # noqa: E402


def snapshot() -> core.BudgetSnapshot:
    phases = [core.Phase(*p) for p in fx.PHASES]
    leaves = [
        {"Device": l[0], "Category": l[1], "Subcategory": l[2], "Domain": l[3],
         "Energy": l[4], "Goal": l[5], "Phase": p, "Hours_per_week": h}
        for l in fx.LEAVES for p, h in zip(fx.PHASE_NAMES, l[6])
    ]
    lines = [
        {"Line": line, "Line_Class": cls, "Device": fx.LEAVES[i][0],
         "Category": fx.LEAVES[i][1], "Subcategory": fx.LEAVES[i][2],
         "Phase": p, "Hours_per_week": h}
        for line, cls, _c, i, hours in fx.LINES for p, h in zip(fx.PHASE_NAMES, hours)
    ]
    params = {"sleep_floor_h_per_night": {"Fase A": 7.5, "Fase H": 7.0, "Fase B1": 7.5}}
    return core.BudgetSnapshot("t", phases, leaves, lines, {"parameters": params})


def total_min(snap, start, end):
    return sum(e["Budget_min"] for e in core.period_leaf_budget(snap, start, end).values())


class ProrationTests(unittest.TestCase):
    def setUp(self):
        self.snap = snapshot()

    def test_month_inside_one_phase_plus_partial(self):
        # December: 19 days of Fase A + 12 days of Fase H.
        budget = core.period_leaf_budget(self.snap, date(2026, 12, 1), date(2026, 12, 31))
        work = budget[("Habit", "Productivity & Finance", "Offline Work Tracking")]
        self.assertAlmostEqual(work["Budget_min"], (40 * 19 + 32 * 12) / 7 * 60)
        self.assertAlmostEqual(total_min(self.snap, date(2026, 12, 1), date(2026, 12, 31)), 744 * 60)

    def test_period_crossing_three_phases(self):
        start, end = date(2026, 12, 15), date(2027, 1, 15)
        cov = core.coverage(self.snap, start, end)
        self.assertEqual(cov.phase_days, {"Fase A": 5, "Fase H": 15, "Fase B1": 12})
        self.assertTrue(cov.is_full)
        budget = core.period_leaf_budget(self.snap, start, end)
        games = budget[("Habit", "Games", "Console Time Tracking")]["Budget_min"]
        self.assertAlmostEqual(games, (7 * 5 + 14 * 15 + 7 * 12) / 7 * 60)
        self.assertAlmostEqual(total_min(self.snap, start, end), 32 * 1440)

    def test_partial_coverage_november(self):
        start, end = date(2026, 11, 1), date(2026, 11, 30)
        cov = core.coverage(self.snap, start, end)
        self.assertEqual((cov.covered_days, cov.period_days), (22, 30))
        self.assertAlmostEqual(cov.ratio, 22 / 30)
        self.assertEqual(cov.first_covered, date(2026, 11, 9))
        self.assertEqual(cov.last_covered, date(2026, 11, 30))
        self.assertFalse(cov.is_full)
        self.assertAlmostEqual(total_min(self.snap, start, end), 528 * 60)

    def test_interim_fifteen_days(self):
        start, end = date(2026, 11, 1), date(2026, 11, 15)
        cov = core.coverage(self.snap, start, end)
        self.assertEqual(cov.covered_days, 7)
        self.assertAlmostEqual(total_min(self.snap, start, end), 7 * 1440)

    def test_period_outside_horizon_has_no_coverage(self):
        start, end = date(2026, 10, 1), date(2026, 10, 31)
        cov = core.coverage(self.snap, start, end)
        self.assertEqual(cov.covered_days, 0)
        self.assertIn("No budget coverage", cov.describe())
        self.assertEqual(total_min(self.snap, start, end), 0.0)

    def test_period_after_horizon_end(self):
        cov = core.coverage(self.snap, date(2027, 1, 25), date(2027, 2, 10))
        self.assertEqual(cov.covered_days, 7)

    def test_parameter_is_day_weighted(self):
        floor = core.period_parameter(
            self.snap, "sleep_floor_h_per_night", date(2026, 12, 13), date(2026, 12, 26), 0
        )
        self.assertAlmostEqual(floor, (7.5 * 7 + 7.0 * 7) / 14)

    def test_line_budget_sums_to_leaf_budget(self):
        start, end = date(2026, 12, 1), date(2026, 12, 31)
        lines = core.period_line_budget(self.snap, start, end)
        leaves = core.period_leaf_budget(self.snap, start, end)
        off = ("Off-Device", "Off-Device Life", "Untracked Maintenance")
        shared = sum(v for (line, key), v in lines.items() if key == off)
        self.assertAlmostEqual(shared, leaves[off]["Budget_min"])

    def test_shared_leaf_actual_split_by_budget_weight(self):
        _, weights = core.line_allocation(self.snap, date(2026, 12, 1), date(2026, 12, 31))
        off = ("Off-Device", "Off-Device Life", "Untracked Maintenance")
        lunch = (5 * 19 + 4 * 12) / (59.75 * 19 + 60.75 * 12)
        self.assertAlmostEqual(weights[off]["Almuerzo laboral"], lunch)
        self.assertAlmostEqual(sum(weights[off].values()), 1.0)


if __name__ == "__main__":
    unittest.main()
