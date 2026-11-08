"""Tests for the historical proxy-allocation worksheet (historical_proxy_allocation.py).

Unit tests are synthetic. The integration test (skipped unless the private,
git-ignored historical data is present) generates, edits and applies a
worksheet entirely inside a temporary folder.
"""

from __future__ import annotations

import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import historical_proxy_allocation as hpa  # noqa: E402


def _row(did, target, hours=10.0, pct="", status="PENDING", rationale="", conf="", basis="", dim="category"):
    return {"row_id": f"{did}:{target}", "decision_id": did, "decision_title": did, "historical_hours": str(hours),
            "target_dimension": dim, "target_category": target if dim == "category" else "", "target_subcategory": "",
            "target_value": target if dim == "energy" else "", "suggested_pct": "", "user_pct": pct, "user_rationale": rationale,
            "user_confidence": conf, "allocation_basis": basis, "approval_status": status}


class ComputeTests(unittest.TestCase):
    def state(self, rows):
        return {r["decision_id"]: r for r in hpa.compute(rows)}

    def test_approved_complete_decision(self):
        rows = [_row("D1", "Productivity & Finance", 20, "60", "APPROVED", "work mostly", "MEDIUM", "USER_RECOLLECTION"),
                _row("D1", "Education", 20, "40", "APPROVED", "", "MEDIUM", "USER_RECOLLECTION")]
        res = self.state(rows)
        self.assertEqual(res["D1"]["state"], "APPROVED_READY")
        self.assertEqual([r["allocated_hours"] for r in rows], [12.0, 8.0])
        self.assertEqual(rows[0]["group_user_pct_total"], 100.0)

    def test_sum_not_100_is_an_error(self):
        rows = [_row("D1", "A", pct="60"), _row("D1", "B", pct="30")]
        self.assertIn("sums to 90%", self.state(rows)["D1"]["errors"])

    def test_approval_requires_rationale_confidence_and_basis(self):
        rows = [_row("D1", "A", pct="100", status="APPROVED")]
        err = self.state(rows)["D1"]["errors"]
        self.assertIn("rationale", err)
        self.assertIn("user_confidence", err)
        rows = [_row("D1", "A", pct="100", status="APPROVED", rationale="x", conf="HIGH", basis="GUESS")]
        self.assertIn("allocation_basis", self.state(rows)["D1"]["errors"])

    def test_missing_invalid_and_mixed(self):
        self.assertEqual(self.state([_row("D1", "A"), _row("D1", "B")])["D1"]["state"], "PENDING_MISSING_ALLOCATION")
        self.assertEqual(self.state([_row("D1", "A", pct="100"), _row("D1", "B")])["D1"]["state"], "PENDING_COMPLETE")
        self.assertIn("non-numeric", self.state([_row("D1", "A", pct="half")])["D1"]["errors"])
        self.assertIn("outside 0–100", self.state([_row("D1", "A", pct="120"), _row("D1", "B", pct="-20")])["D1"]["errors"])
        self.assertIn("mixed approval_status", self.state([_row("D1", "A", pct="50", status="APPROVED"), _row("D1", "B", pct="50")])["D1"]["errors"])

    def test_percent_formats_accepted(self):
        rows = [_row("D1", "A", pct="62,5%"), _row("D1", "B", pct="37.5")]
        self.assertEqual(self.state(rows)["D1"]["state"], "PENDING_COMPLETE")


class GroupingTests(unittest.TestCase):
    def rec(self, rid, cat, sub="", dev="Phone", energy="Shallow Work", basis="MANUAL_TRANSCRIPTION"):
        return {"record_id": rid, "accounting_status": "INCLUDED", "legacy_category": cat, "legacy_subcategory": sub,
                "legacy_device": dev, "legacy_energy": energy, "legacy_goal": "Maintenance", "allocation_basis": basis}

    def test_category_and_energy_decisions_are_independent(self):
        specs = [{"decision_id": "E", "filter": {"legacy_category": "Learning / Skill-building", "legacy_device": "Phone"}, "targets": [hpa.T(value="Deep", dimension="energy")]},
                 {"decision_id": "C", "filter": {"legacy_category": "Learning / Skill-building", "legacy_device": "Phone"}, "targets": [hpa.T("Education")]}]
        g = hpa.assign_groups([self.rec("r1", "Learning / Skill-building")], specs, {})
        self.assertEqual((g["category"]["r1"], g["energy"]["r1"]), ("C", "E"))

    def test_first_match_wins_and_record_level_overrides(self):
        specs = [{"decision_id": "CELL", "filter": {"legacy_category": "Personal Admin", "comment_contains": "bad sleep"}, "targets": [hpa.T("Health & Fitness")]},
                 {"decision_id": "ROW", "filter": {"legacy_category": "Personal Admin"}, "targets": [hpa.T("Utilities")]}]
        recs = [self.rec("a", "Personal Admin"), self.rec("b", "Personal Admin")]
        g = hpa.assign_groups(recs, specs, {"b": "Bad Sleep"})
        self.assertEqual((g["category"]["a"], g["category"]["b"]), ("ROW", "CELL"))
        self.assertTrue(hpa.matches(recs[1], {"record_ids": ["b"]}, {}))
        self.assertFalse(hpa.matches(self.rec("c", "Household / Chores", basis="ESTIMATE_NON_TEMPLATE"),
                                     {"legacy_category": "Household / Chores", "allocation_basis_in": ["STANDARD_DAILY_ALLOCATION"]}, {}))


class DecisionSpecTests(unittest.TestCase):
    def test_required_decisions_and_user_statements(self):
        specs = {s["decision_id"]: s for s in hpa.decision_specs()}
        titles = " ".join(s["title"] for s in specs.values())
        for needed in ("Work / Study", "Entertainment — Phone", "Entertainment — Laptop", "Entertainment — PC Desktop", "Personal Admin",
                       "Communication / Social", "Household / Chores", "Recovery / Rest", "Friends on PC", "Vacation", "Videos"):
            self.assertIn(needed, titles)
        videos = specs["P29"]
        self.assertEqual((videos["targets"][0]["target_category"], videos["approved"]), ("Creativity", {0: 100}))
        approved = [d for d, s in specs.items() if s["approved"]]
        self.assertEqual(approved, ["P29"])  # only the decision the user fully confirmed is pre-approved
        self.assertFalse(specs["P01"]["suggested"])  # no invented Work / Study split

    def test_targets_exist_in_current_taxonomy(self):
        import historical_canonical as hc
        if not (ROOT / "output" / "Reference" / "Taxonomy" / "Dim_Category_Default.csv").exists():
            self.skipTest("current taxonomy not present")
        tax = hc.load_current_taxonomy()
        for s in hpa.decision_specs():
            for t in s["targets"]:
                if t["target_dimension"] == "energy":
                    self.assertIn(t["target_value"], tax.energies)
                elif t["target_category"] != hpa.KEEP_UNMAPPED:
                    self.assertTrue(tax.has_pair(t["target_category"], t["target_subcategory"]), t)


def _sha(paths):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()}


REAL = ROOT / "output" / "Historical" / "Historical_Normalized_Records.csv"


@unittest.skipUnless(REAL.exists() and any((ROOT / "input" / "Historical" / "Legacy").glob("*.xlsx")),
                     "real (git-ignored) historical data not present")
class RealDataWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        protected = list((ROOT / "input" / "Historical" / "Legacy").glob("*.xlsx")) + list((ROOT / "output" / "Historical").glob("Historical_*.csv")) \
            + list((ROOT / "config" / "historical_bridge").glob("*.csv")) + list((ROOT / "output" / "Historical" / "Annual_2025-09_to_2026-09" / "tables").glob("*.csv"))
        before = _sha(protected)
        cls.gen = hpa.generate(out_dir=cls.out)
        ws = cls.out / hpa.WORKSHEET_CSV
        rows = list(csv.DictReader(ws.open(encoding="utf-8")))
        for r in rows:  # approve one synthetic test decision (temp copy only)
            if r["decision_id"] == "P02":
                r.update(user_pct="50", user_rationale="test", user_confidence="LOW", allocation_basis="USER_RECOLLECTION", approval_status="APPROVED")
        with ws.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        cls.applied = hpa.apply(out_dir=cls.out, worksheet=ws)
        cls.unchanged = before == _sha(protected)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_full_coverage_and_hours(self):
        self.assertEqual(dict(self.gen["uncovered"]), {})
        self.assertEqual(len({r["decision_id"] for r in self.gen["rows"]}), 31)

    def test_apply_conserves_minutes_and_only_uses_approved(self):
        self.assertTrue(all(c["result"] == "PASS" for c in self.applied["checks"]), self.applied["checks"])
        approved_decisions = {x["category_decision"] for x in self.applied["layer"] if x["approval_status"] == "APPROVED"}
        self.assertEqual(approved_decisions, {"P02", "P29"})
        p02 = [x for x in self.applied["layer"] if x["category_decision"] == "P02"]
        self.assertEqual({x["target_category"] for x in p02}, {"Productivity & Finance", "Education"})

    def test_adjusted_keeps_direct_reference_and_proxy_share(self):
        edu = next(r for r in self.applied["baseline"] if r["category"] == "Education")
        self.assertGreater(edu["adjusted_baseline_median_min_per_day"], edu["direct_baseline_median_min_per_day"])
        self.assertGreater(edu["proxy_share_of_adjusted_baseline_pct"], 0)

    def test_sources_and_authoritative_outputs_untouched(self):
        self.assertTrue(self.unchanged)


if __name__ == "__main__":
    unittest.main()
