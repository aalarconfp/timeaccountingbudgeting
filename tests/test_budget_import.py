"""Tests for budget_import.py: snapshot writing, versioning and validations.

Unit tests use a synthetic workbook (tests/budget_fixtures.py). The
integration test reconciles the real approved workbook against its own
Presupuesto_Tracker totals and runs only when the (git-ignored) file exists.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import budget_core as core  # noqa: E402
import budget_import as bi  # noqa: E402
from tests import budget_fixtures as fx  # noqa: E402

REAL_WORKBOOK = ROOT / "output" / "Planning" / "Time_Budget_2026-11_to_2027-04_v4.xlsx"


class ImportTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.taxonomy = fx.make_taxonomy(self.dir / "Taxonomy")
        self.out = self.dir / "Budget"

    def tearDown(self):
        self.tmp.cleanup()

    def workbook(self, name="Time_Budget_test_v1.xlsx", **kwargs) -> Path:
        return fx.make_workbook(self.dir / name, **kwargs)

    def run_import(self, path, **kwargs):
        return bi.import_budget(path, self.out, taxonomy_root=self.taxonomy, **kwargs)


class SnapshotTests(ImportTestCase):
    def test_import_writes_versioned_snapshot(self):
        manifest, parsed, version, already = self.run_import(self.workbook())
        self.assertFalse(already)
        self.assertEqual(version, "test_v1")
        for path in core.snapshot_paths(self.out, version).values():
            self.assertTrue(path.exists(), path)
        self.assertEqual(manifest["leaf_count"], 5)
        self.assertEqual([p["phase"] for p in manifest["phases"]], fx.PHASE_NAMES)
        for phase in manifest["phases"]:
            self.assertAlmostEqual(phase["total_h_per_week"], 168.0, places=6)
        self.assertEqual(len(manifest["source_sha256"]), 64)
        self.assertEqual(parsed.warnings, [])

        snap = core.load_snapshot(self.out)
        self.assertEqual(snap.version, "test_v1")
        self.assertEqual(len(snap.leaves), 5 * 3)
        self.assertEqual(snap.parameter("sleep_floor_h_per_night", "Fase A", 0), 7.5)
        self.assertEqual(
            snap.parameter("digital_leisure_floor_min_per_day", "Fase H", 0), 60.0
        )
        leaf_header = core.read_csv(core.snapshot_paths(self.out, version)["leaf"])[0]
        self.assertEqual(list(leaf_header), bi.LEAF_FIELDS)

    def test_full_precision_is_kept(self):
        leaves = [list(l) for l in fx.LEAVES]
        leaves[2][6] = (7.123456789, 14.0, 7.0)
        leaves[4][6] = (59.75 - 0.123456789, 60.75, 59.75)
        leaves = [tuple(l) for l in leaves]
        lines = [list(l) for l in fx.LINES]
        lines[2][4] = (7.123456789, 14.0, 7.0)
        lines[5][4] = (54.75 - 0.123456789, 56.75, 54.75)
        self.run_import(self.workbook(leaves=leaves, lines=[tuple(l) for l in lines]))
        snap = core.load_snapshot(self.out)
        value = next(r["Hours_per_week"] for r in snap.leaves
                     if r["Subcategory"] == "Console Time Tracking" and r["Phase"] == "Fase A")
        self.assertEqual(value, 7.123456789)

    def test_reimport_identical_is_noop(self):
        path = self.workbook()
        self.run_import(path)
        manifest, _, version, already = self.run_import(path)
        self.assertTrue(already)
        self.assertIsNone(manifest)
        self.assertEqual(version, "test_v1")
        self.assertEqual(len(core.list_versions(self.out)), 1)

    def test_changed_file_creates_new_version_without_overwrite(self):
        path = self.workbook()
        self.run_import(path)
        before = core.snapshot_paths(self.out, "test_v1")["leaf"].read_bytes()
        leaves = [list(l) for l in fx.LEAVES]
        leaves[2][6] = (8.0, 14.0, 7.0)
        leaves[4][6] = (58.75, 60.75, 59.75)
        lines = [list(l) for l in fx.LINES]
        lines[2][4] = (8.0, 14.0, 7.0)
        lines[5][4] = (53.75, 56.75, 54.75)
        fx.make_workbook(path, leaves=[tuple(l) for l in leaves], lines=[tuple(l) for l in lines])
        _, _, version, already = self.run_import(path)
        self.assertFalse(already)
        self.assertEqual(version, "test_v1_r2")
        self.assertEqual(core.snapshot_paths(self.out, "test_v1")["leaf"].read_bytes(), before)
        self.assertEqual(core.load_snapshot(self.out).version, "test_v1_r2")
        self.assertEqual(core.load_snapshot(self.out, "test_v1").version, "test_v1")

    def test_dry_run_writes_nothing(self):
        self.run_import(self.workbook(), dry_run=True)
        self.assertFalse(self.out.exists())

    def test_edited_snapshot_is_rejected(self):
        _, _, version, _ = self.run_import(self.workbook())
        leaf = core.snapshot_paths(self.out, version)["leaf"]
        leaf.write_text(leaf.read_text(encoding="utf-8-sig").replace("54.25", "60.0"),
                        encoding="utf-8-sig")
        with self.assertRaises(core.BudgetNotAvailable):
            core.load_snapshot(self.out)

    def test_missing_snapshot_raises(self):
        with self.assertRaises(core.BudgetNotAvailable):
            core.load_snapshot(self.dir / "nothing")


class ValidationTests(ImportTestCase):
    def assertImportFails(self, fragment, **kwargs):
        with self.assertRaises(bi.BudgetImportError) as ctx:
            self.run_import(self.workbook(**kwargs))
        self.assertIn(fragment, str(ctx.exception))
        self.assertFalse(self.out.exists())

    def test_phase_not_168_fails(self):
        leaves = [list(l) for l in fx.LEAVES]
        leaves[0][6] = (40.5, 32.0, 40.0)
        lines = [list(l) for l in fx.LINES]
        lines[0][4] = (40.5, 32.0, 40.0)
        self.assertImportFails(
            "Phase Fase A sums to 168.5000",
            leaves=[tuple(l) for l in leaves], lines=[tuple(l) for l in lines],
        )

    def test_overlapping_phases_fail(self):
        phases = list(fx.PHASES)
        phases[1] = ("Fase H", date(2026, 12, 19), date(2027, 1, 3))
        self.assertImportFails("overlap", phases=phases)

    def test_gap_between_phases_fails(self):
        phases = list(fx.PHASES)
        phases[1] = ("Fase H", date(2026, 12, 21), date(2027, 1, 3))
        self.assertImportFails("Gap between", phases=phases)

    def test_duplicate_leaf_fails(self):
        leaves = list(fx.LEAVES)
        half = ("Habit", "Health & Fitness", "Sleep", "Physical", "Recovery",
                "Maintenance", (27.125, 27.125, 27.125))
        leaves[1] = half
        leaves.append(half)
        self.assertImportFails("Duplicate leaf", leaves=leaves)

    def test_uncached_formula_fails(self):
        self.assertImportFails("no cached value", formula_cell="M20")

    def test_reconciliation_mismatch_fails(self):
        self.assertImportFails("Presupuesto_Tracker", recon_shift=0.5)

    def test_taxonomy_difference_is_warning_only(self):
        fx.make_taxonomy(self.taxonomy, overrides=[
            ("Habit", "Console Time Tracking", "Games", "Recreational", "Active", "Consumption", "Override"),
        ])
        manifest, parsed, _, _ = self.run_import(self.workbook())
        self.assertIsNotNone(manifest)
        self.assertEqual(len(parsed.warnings), 1)
        self.assertIn("Console Time Tracking", parsed.warnings[0])
        self.assertEqual(manifest["validation"]["warnings"], parsed.warnings)
        # Not corrected: the snapshot keeps the workbook's mapping.
        snap = core.load_snapshot(self.out)
        energy = {r["Energy"] for r in snap.leaves if r["Subcategory"] == "Console Time Tracking"}
        self.assertEqual(energy, {"Passive"})


@unittest.skipUnless(REAL_WORKBOOK.exists(), "approved budget workbook not present")
class RealWorkbookReconciliationTests(unittest.TestCase):
    """The approved v4 workbook reconciles with its own control totals."""

    @classmethod
    def setUpClass(cls):
        cls.parsed = bi.parse_workbook(REAL_WORKBOOK)
        cls.snap = core.BudgetSnapshot(
            "v4", cls.parsed.phases, cls.parsed.leaves, cls.parsed.lines,
            {"parameters": cls.parsed.parameters},
        )

    def total_h(self, start, end):
        return sum(
            e["Budget_min"] for e in core.period_leaf_budget(self.snap, start, end).values()
        ) / 60.0

    def test_horizon_and_phases(self):
        self.assertEqual(len(self.parsed.phases), 5)
        self.assertEqual(self.snap.horizon_start, date(2026, 11, 9))
        self.assertEqual(self.snap.horizon_end, date(2027, 4, 30))

    def test_monthly_totals_match_presupuesto_tracker(self):
        self.assertAlmostEqual(self.total_h(date(2026, 11, 1), date(2026, 11, 30)), 528.0, places=6)
        self.assertAlmostEqual(self.total_h(date(2026, 12, 1), date(2026, 12, 31)), 744.0, places=6)
        for key, by_category in self.parsed.monthly_targets.items():
            start, end = bi.month_bounds(key)
            budget = core.period_leaf_budget(self.snap, start, end)
            for category, target in by_category.items():
                if category == "Total":
                    got = sum(e["Budget_min"] for e in budget.values()) / 60.0
                else:
                    got = sum(e["Budget_min"] for k, e in budget.items() if k[1] == category) / 60.0
                self.assertAlmostEqual(got, target, places=6, msg=f"{key} {category}")

    def test_offline_work_is_in_habit(self):
        hours = {
            r["Phase"]: r["Hours_per_week"] for r in self.parsed.leaves
            if r["Subcategory"] == "Offline Work Tracking"
        }
        self.assertEqual(hours["Fase A"], 40)
        self.assertEqual(hours["Fase H"], 32)


if __name__ == "__main__":
    unittest.main()
