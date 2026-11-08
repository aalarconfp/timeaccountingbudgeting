"""Tests for the annual historical analysis in the current taxonomy
(historical_canonical.py, historical_annual.py, historical_annual_reports.py).

Unit tests use synthetic values and the version-controlled mapping tables only
(no personal data). The integration test runs against the real Phase 2
outputs (output/Historical/, git-ignored) only when they are present locally
and is skipped otherwise.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import historical_annual as ha  # noqa: E402
import historical_canonical as hc  # noqa: E402
import historical_monthly as hm  # noqa: E402
from config.historical import BRIDGE_CONFIG_ROOT, REGIME_CURRENT, REGIME_LEGACY, REGIME_TRANSITION  # noqa: E402

LEGACY = REGIME_LEGACY


def _csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def synthetic_taxonomy(root: Path) -> None:
    """Minimal Dim_* files in the shape of output/Reference/Taxonomy."""
    _write(root / "Dim_Category_Default.csv", [
        {"Canonical_Category": c, "Domain": d, "Energy": e, "Goal": g} for c, d, e, g in (
            ("Social Networking", "Relational", "Shallow", "Consumption"), ("Games", "Recreational", "Passive", "Consumption"),
            ("Entertainment", "Recreational", "Passive", "Consumption"), ("Productivity & Finance", "Vocational", "Deep", "Investment"),
            ("Education", "Intellectual", "Deep", "Investment"), ("Health & Fitness", "Physical", "Active", "Investment"))])
    _write(root / "Dim_Activity.csv", [
        {"Source": "Habit", "Activity_Name": "Sleep", "Canonical_Category": "Health & Fitness", "Domain": "Physical",
         "Energy": "Recovery", "Goal": "Maintenance", "Mapping_Type": "Override"},
        {"Source": "Habit", "Activity_Name": "Family Time Tracking", "Canonical_Category": "Social Networking", "Domain": "Relational",
         "Energy": "Active", "Goal": "Investment", "Mapping_Type": "Override"}])
    _write(root / "Dim_Domain.csv", [{"Domain": d, "Sort_Order": i} for i, d in enumerate(
        ("Vocational", "Intellectual", "Economical", "Relational", "Physical", "Recreational", "Spiritual"), 1)])
    _write(root / "Dim_Energy.csv", [{"Energy": e, "Sort_Order": i} for i, e in enumerate(("Deep", "Shallow", "Active", "Passive", "Recovery"), 1)])
    _write(root / "Dim_Goal.csv", [{"Goal": g, "Sort_Order": i} for i, g in enumerate(("Investment", "Maintenance", "Consumption"), 1)])


class TaxonomySourceTests(unittest.TestCase):
    def test_categories_come_from_dim_files_plus_off_device_life(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_taxonomy(Path(tmp))
            t = hc.load_current_taxonomy(Path(tmp))
        self.assertEqual(t.categories[:6], ["Social Networking", "Games", "Entertainment", "Productivity & Finance", "Education", "Health & Fitness"])
        self.assertEqual(t.categories[-1], hc.OFF_DEVICE_CATEGORY)
        self.assertEqual(t.goals, ["Investment", "Maintenance", "Consumption"])
        self.assertTrue(t.has_pair("Health & Fitness", "Sleep"))
        self.assertTrue(t.has_pair(hc.OFF_DEVICE_CATEGORY, hc.OFF_DEVICE_SUBCATEGORY))
        self.assertFalse(t.has_pair("Health & Fitness", "Napping"))

    def test_annual_uses_the_same_taxonomy_files_as_the_monthly_reports(self):
        import final_analysis as fa
        t = hc.load_current_taxonomy() if (fa.TAXONOMY_ROOT / "Dim_Category_Default.csv").exists() else None
        if t is None:
            self.skipTest("current taxonomy files not present")
        self.assertEqual(t.source, str(fa.TAXONOMY_ROOT))
        defaults = {m.category for (src, _), m in fa.load_taxonomy().items() if src == "CategoryDefault"}
        self.assertEqual(set(t.categories) - {hc.OFF_DEVICE_CATEGORY}, defaults)

    def test_validator_rejects_invented_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            synthetic_taxonomy(Path(tmp))
            t = hc.load_current_taxonomy(Path(tmp))
        base = {"legacy_category": "X", "legacy_subcategory": "", "legacy_device": "*", "canonical_subcategory": "",
                "canonical_domain": "", "canonical_energy": "", "canonical_goal": "", "candidate_categories": ""}
        rules = [
            {**base, "canonical_category": "Work Study", "category_status": "HIGH_CONFIDENCE"},           # invented category
            {**base, "canonical_category": "Health & Fitness", "canonical_subcategory": "Napping", "category_status": "REVIEW_REQUIRED"},
            {**base, "canonical_category": "", "category_status": "MULTI_CATEGORY", "candidate_categories": "Education"},  # < 2 candidates
            {**base, "canonical_category": "Games", "category_status": "HIGH_CONFIDENCE", "canonical_goal": "High-Value"},
        ]
        errors = hc.validate_bridge_against_taxonomy(rules, [], t)
        self.assertEqual(len(errors), 4, errors)

    def test_real_mapping_tables_only_use_current_values(self):
        import final_analysis as fa
        if not (fa.TAXONOMY_ROOT / "Dim_Category_Default.csv").exists():
            self.skipTest("current taxonomy files not present")
        rules = _csv(BRIDGE_CONFIG_ROOT / "legacy_activity_bridge.csv")
        labels = _csv(BRIDGE_CONFIG_ROOT / "legacy_label_maps.csv")
        self.assertEqual(hc.validate_bridge_against_taxonomy(rules, labels, hc.load_current_taxonomy()), [])


class LegacyMappingRuleTests(unittest.TestCase):
    """The approved legacy -> current destinations in config/historical_bridge."""

    @classmethod
    def setUpClass(cls):
        rules = _csv(BRIDGE_CONFIG_ROOT / "legacy_activity_bridge.csv")
        cls.rule = {(r["legacy_category"], r["legacy_subcategory"], r["legacy_device"]): r for r in rules}

    def dest(self, key):
        r = self.rule[key]
        return r["canonical_category"], r["canonical_subcategory"], r["category_status"], r.get("candidate_categories", "")

    def test_exact_activity_equivalents(self):
        self.assertEqual(self.dest(("Health & Wellness", "Sleep", "Off-Device"))[:3], ("Health & Fitness", "Sleep", "HIGH_CONFIDENCE"))
        self.assertEqual(self.dest(("Relationships / Social", "Family", "Off-Device"))[:3], ("Social Networking", "Family Time Tracking", "HIGH_CONFIDENCE"))
        self.assertEqual(self.dest(("Hobbies / Creative", "Motorcycle", "Off-Device"))[:3], ("Travel", "Motorcycle Time", "HIGH_CONFIDENCE"))
        self.assertEqual(self.dest(("Entertainment", "", "TV"))[:3], ("Entertainment", "TV Time tracking", "HIGH_CONFIDENCE"))

    def test_broad_legacy_labels_are_not_forced_into_one_category(self):
        self.assertEqual(self.dest(("Work / Study", "*", "*")), ("", "", "MULTI_CATEGORY", "Productivity & Finance|Education"))
        self.assertEqual(self.dest(("Entertainment", "", "*")), ("", "", "MULTI_CATEGORY", "Entertainment|Games|Social Networking"))

    def test_review_required_and_unmapped(self):
        self.assertEqual(self.dest(("Relationships / Social", "Friends", "PC Desktop"))[:3], ("Games", "", "REVIEW_REQUIRED"))
        self.assertEqual(self.dest(("Household / Chores", "*", "Off-Device"))[:3], ("Off-Device Life", "Untracked Maintenance", "REVIEW_REQUIRED"))
        self.assertEqual(self.dest(("Health & Wellness", "Vacations", "Off-Device"))[2], "UNMAPPED")
        self.assertEqual(self.dest(("Hobbies / Creative", "Videos", "Off-Device"))[2], "UNMAPPED")

    def test_recovery_rest_label_is_not_mapped_to_recovery(self):
        labels = {(r["dimension"], r["legacy_value"]): r for r in _csv(BRIDGE_CONFIG_ROOT / "legacy_label_maps.csv")}
        self.assertEqual(labels[("energy", "Recovery / Rest")]["canonical_value"], "")
        self.assertEqual(labels[("goal", "Low-Value / Drift")]["canonical_value"], "Consumption")
        self.assertEqual(labels[("device", "PC Desktop")]["canonical_value"], "DESKTOP")
        self.assertEqual(labels[("device", "Off-Device")]["canonical_value"], "OFF_DEVICE_RECORDED")  # not the inferred residual


def _norm(cell, cat, sub, status, cands="", minutes=60.0, energy="Deep", energy_status="HIGH_CONFIDENCE", goal="Investment", device="LAPTOP"):
    return {"accounting_status": "INCLUDED", "category_status": status, "canonical_category": cat, "canonical_subcategory": sub,
            "canonical_category_candidates": cands, "canonical_domain": "Vocational", "domain_status": "REVIEW_REQUIRED",
            "canonical_energy": energy, "energy_status": energy_status, "canonical_goal": goal, "goal_status": "HIGH_CONFIDENCE",
            "regime": LEGACY, "month": "2026-01", "date": "2026-01-05", "canonical_device": device, "allocation_basis": "MANUAL_TRANSCRIPTION",
            "bridge_group": "X", "legacy_category": "L", "legacy_subcategory": "", "legacy_device": "Laptop",
            "normalized_duration_min": str(minutes), "source_workbook": "wb", "source_sheet": "s", "source_cell": cell}


def _cur(cat, sub, group, minutes, goal="Consumption"):
    return {"regime": REGIME_CURRENT, "month": "2026-09", "date": "2026-09-01", "device_class": "PHONE" if group != "RESIDUAL" else "RESIDUAL",
            "canonical_category": cat, "canonical_subcategory": sub, "canonical_domain": "Physical" if group == "RESIDUAL" else "Relational",
            "canonical_energy": "Active" if group == "RESIDUAL" else "Shallow", "canonical_goal": "Maintenance" if group == "RESIDUAL" else goal,
            "evidence_basis": "Observed", "bridge_group": group, "duration_min": str(minutes), "provenance": f"Final#{cat}{sub}"}


class CanonicalRecordTests(unittest.TestCase):
    def setUp(self):
        normalized = [
            _norm("A1", "", "", "MULTI_CATEGORY", "Productivity & Finance|Education", 120),
            _norm("A2", "Health & Fitness", "Sleep", "HIGH_CONFIDENCE", minutes=480, energy="Recovery", goal="Maintenance", device="OFF_DEVICE_RECORDED"),
            _norm("A3", "", "", "UNMAPPED", minutes=30, energy="", energy_status="UNMAPPED", goal=""),
            _norm("A4", "Games", "", "REVIEW_REQUIRED", minutes=15, goal="Consumption", device="DESKTOP"),
            {**_norm("A5", "Games", "", "REVIEW_REQUIRED", minutes=999), "accounting_status": "ZERO_ENTRY"},
        ]
        daily = [{"regime": LEGACY, "month": "2026-01", "date": "2026-01-05", "unresolved_min": "100"}]
        current = [_cur("Social Networking", "Social Networking", "DIGITAL_SOCIAL_UNSPLIT", 50),
                   _cur("Off-Device Life", "Untracked Maintenance", "RESIDUAL", 70)]
        self.recs = hc.build_canonical_records(normalized, daily, current)

    def test_records_counted_once_and_excluded_cells_dropped(self):
        self.assertEqual(len(self.recs), 7)  # 4 included legacy + 1 residual + 2 current
        self.assertAlmostEqual(sum(r["minutes"] for r in self.recs), 120 + 480 + 30 + 15 + 100 + 50 + 70)

    def test_multi_category_and_unmapped_stay_outside_categories(self):
        multi = next(r for r in self.recs if r["candidate_categories"])
        self.assertEqual((multi["category"], multi["category_status"]), ("", "MULTI_CATEGORY"))
        unm = next(r for r in self.recs if r["category_status"] == "UNMAPPED")
        self.assertEqual((unm["category"], unm["energy"], unm["goal"]), ("", "", ""))

    def test_legacy_residual_uses_current_off_device_life_profile(self):
        res = next(r for r in self.recs if r["regime"] == LEGACY and r["category_status"] == hc.CAT_RESIDUAL)
        self.assertEqual((res["category"], res["subcategory"], res["domain"], res["energy"], res["goal"]),
                         ("Off-Device Life", "Untracked Maintenance", "Physical", "Active", "Maintenance"))

    def test_review_dimensions_kept_with_status(self):
        sleep = next(r for r in self.recs if r["subcategory"] == "Sleep")
        self.assertEqual((sleep["domain"], sleep["domain_status"]), ("Vocational", "REVIEW_REQUIRED"))

    def test_budget_lines_partition_every_record(self):
        lines = {hc.budget_line(r) for r in self.recs}
        self.assertTrue(lines <= set(hc.BUDGET_LINE_ORDER))
        line = {r["provenance"]: hc.budget_line(r) for r in self.recs}
        self.assertEqual(line["wb!s!A1"], "PRODUCTIVITY_FINANCE_AND_EDUCATION")
        self.assertEqual(line["wb!s!A2"], "SLEEP")
        self.assertEqual(line["wb!s!A3"], "UNMAPPED_LEGACY")
        self.assertEqual(line["wb!s!A4"], "DISCRETIONARY_CONSUMPTION")

    def test_consumption_proxy_matches_standard_report_definition(self):
        sn = {"category": "Social Networking", "subcategory": "Messaging", "goal": "Maintenance", "candidate_categories": ""}
        self.assertFalse(hc.is_discretionary(sn))
        self.assertTrue(hc.is_discretionary({**sn, "goal": "Consumption"}))
        self.assertFalse(hc.is_discretionary({**sn, "subcategory": "Family Time Tracking", "goal": "Consumption"}))
        self.assertTrue(hc.is_discretionary({"category": "Games", "subcategory": "", "goal": "Maintenance", "candidate_categories": ""}))

    def test_anchor_from_current_subcategory(self):
        self.assertEqual(hc.anchor_of({"category": "Health & Fitness", "subcategory": "Sleep", "regime": REGIME_CURRENT}), "SLEEP")
        self.assertEqual(hc.anchor_of({"category": "Information & Reading", "subcategory": "", "regime": LEGACY}), "READING_JOURNALING")
        self.assertIsNone(hc.anchor_of({"category": "Games", "subcategory": "", "regime": LEGACY}))
        self.assertEqual(set(hc.ANCHOR_SUBCATEGORIES.values()), set(hm.ANCHOR_ORDER))

    def test_residual_profile_must_be_unique(self):
        with self.assertRaises(ValueError):
            hc.residual_profile([])


class BridgeLeakTests(unittest.TestCase):
    def test_detects_bridge_groups_but_not_current_lines(self):
        self.assertEqual(ha.bridge_leaks("WORK_STUDY 3h"), ["WORK_STUDY"])
        self.assertIn("HOUSEHOLD_DAILY_LIVING", ha.bridge_leaks("x HOUSEHOLD_DAILY_LIVING"))
        self.assertEqual(ha.bridge_leaks("HEALTH_PRACTICES PRODUCTIVITY_FINANCE_AND_EDUCATION DISCRETIONARY_CONSUMPTION"), [])
        self.assertEqual(ha.bridge_leaks("Health & Fitness › Sleep"), [])


class StatsTests(unittest.TestCase):
    def test_quantile_cv_trend(self):
        self.assertEqual(ha.quantile([1, 2, 3, 4], 0.5), 2.5)
        self.assertIsNone(ha.quantile([], 0.5))
        self.assertAlmostEqual(ha.cv([10, 10, 10]), 0.0)
        self.assertEqual(ha.trend_label([60, 70, 80, 90, 100])[0], "RISING")
        self.assertEqual(ha.trend_label([2, 4, 6, 8, 10])[0], "STABLE")  # 10 min/day floor

    def test_spearman_and_ranks(self):
        self.assertAlmostEqual(ha.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertEqual(ha.ranks([5, 1, 5]), [2.5, 1.0, 2.5])

    def test_rounding_format_envelope(self):
        self.assertEqual((ha.round_down(29.9), ha.round_up(30.1)), (15, 45))
        self.assertEqual(ha.hm_fmt(125.4), "2h05")
        self.assertEqual(ha.h_fmt(3725.4), "62h 05m")
        st = {"min": 100.0, "max": 200.0}
        self.assertEqual(ha.position(79, st), "BELOW_BASELINE_ENVELOPE")
        self.assertEqual(ha.position(241, st), "ABOVE_BASELINE_ENVELOPE")
        self.assertEqual(ha.position(150, st), "WITHIN_BASELINE_ENVELOPE")


def _baseline_row(line, prot, low, high, sub="N", median=100.0, p25=90.0, mn=80.0, cur=110.0):
    return {"budget_line": line, "label": line, "is_sub_line": sub, "protection_or_flexibility": prot,
            "suggested_low_min_per_day": low, "suggested_high_min_per_day": high, "confidence": "MEDIUM",
            "baseline_median_min_per_day": median, "baseline_p25_min_per_day": p25,
            "baseline_min_min_per_day": mn, "current_2026_09_min_per_day": cur}


class BudgetScenarioTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _baseline_row("SLEEP", "PROTECT_FLOOR", 450, 480),
            _baseline_row("PRODUCTIVITY_FINANCE_AND_EDUCATION", "COMMITMENT_AND_GOAL_DEPENDENT", 300, 390),
            _baseline_row("Education", "INFORMATION_ONLY", "", "", sub="Y (not additive)"),
            _baseline_row("TRAVEL", "FLEXIBLE", 15, 45),
            _baseline_row("DISCRETIONARY_CONSUMPTION", "FLEXIBLE_CEILING", "", "", median=200.0, p25=185.0, mn=150.0),
            _baseline_row("UNMAPPED_LEGACY", "NOT_BUDGETABLE", "", ""),
        ]
        self.out = ha.budget_scenarios(self.rows)

    def rows_of(self, scen):
        return {r["budget_line"]: r for r in self.out if r["scenario"] == scen}

    def test_capacity_respected_and_gaps_not_budgeted(self):
        for scen in ("SUSTAINABLE", "GOAL_FOCUSED", "CONSERVATIVE"):
            s = self.rows_of(scen)
            self.assertEqual(s["CAPACITY_CHECK"]["status"], "FEASIBLE")
            self.assertNotIn("Education", s)            # sub-line, not additive
            self.assertNotIn("UNMAPPED_LEGACY", s)      # not budgetable
        self.assertEqual(self.rows_of("SUSTAINABLE")["CAPACITY_CHECK"]["low_min_per_day"], 825.0)  # 450+300+15+60

    def test_goal_pool_and_relative_candidate(self):
        g = self.rows_of("GOAL_FOCUSED")
        self.assertEqual(g["GOAL_POOL (allocation TO_DEFINE)"]["low_min_per_day"], 15.0)
        cand = g["GOAL_POOL_CANDIDATE from DISCRETIONARY_CONSUMPTION (relative)"]
        self.assertEqual((cand["status"], cand["low_min_per_day"], cand["high_min_per_day"]), ("TO_CONFIRM_NOT_SUMMED", 15.0, 50.0))

    def test_conservative_and_periods(self):
        c = self.rows_of("CONSERVATIVE")
        self.assertEqual(c["SLEEP"]["low_min_per_day"], 465)
        self.assertEqual(c["UNALLOCATED_RESERVE"]["low_min_per_day"], 120.0)
        self.assertEqual(self.rows_of("SUSTAINABLE")["SLEEP"]["low_h_per_week"], 52.5)

    def test_infeasible_minimums_reported(self):
        rows = [_baseline_row("A", "PROTECT_FLOOR", 900, 960), _baseline_row("B", "PROTECT_FLOOR", 600, 600)]
        chk = next(r for r in ha.budget_scenarios(rows) if r["scenario"] == "SUSTAINABLE" and r["budget_line"] == "CAPACITY_CHECK")
        self.assertEqual(chk["status"], "INFEASIBLE_MINIMUMS_EXCEED_CAPACITY")


class InputIntegrityTests(unittest.TestCase):
    def test_missing_manifest_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ha.InputError):
                ha.load_inputs(Path(tmp))

    def test_hash_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            outputs = {}
            for name in ha.INPUT_FILES:
                (d / name).write_text("a,b\n1,2\n", encoding="utf-8")
                outputs[name] = {"sha256": ha.sha256_file(d / name)}
            outputs["Historical_Bridge_Records.csv"]["sha256"] = "0" * 64
            (d / "Historical_Run_Manifest.json").write_text(json.dumps({"outputs": outputs}), encoding="utf-8")
            match = {i["file"]: i["match"] for i in ha.load_inputs(d)["integrity"]}
            self.assertEqual(match["Historical_Bridge_Records.csv"], "N")
            self.assertEqual(match["Historical_Monthly_Long.csv"], "Y")


REAL_HIST = ROOT / "output" / "Historical"
REAL_SEP = ROOT / "output" / "Integrated" / "Analysis" / "Final" / "2026-09-01_2026-09-30"
REAL_TAX = ROOT / "output" / "Reference" / "Taxonomy" / "Dim_Category_Default.csv"


@unittest.skipUnless((REAL_HIST / "Historical_Run_Manifest.json").exists() and REAL_SEP.exists() and REAL_TAX.exists(),
                     "real (git-ignored) historical outputs not present")
class RealDataIntegrationTest(unittest.TestCase):
    """Runs only on the machine that holds the private data. Writes only to a temp folder."""

    @classmethod
    def setUpClass(cls):
        import historical_annual_reports as rep
        cls.rep = rep
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "annual"
        cls.res = ha.run(REAL_HIST, cls.out, write=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_all_checks_pass(self):
        self.assertEqual([c for c in self.res["validation"] if c["result"] == "FAIL"], [])

    def test_primary_categories_are_the_current_taxonomy(self):
        cats = [r["category"] for r in self.res["B"] if r["row_type"] == "CURRENT_CATEGORY"]
        self.assertEqual(cats, hc.load_current_taxonomy().categories)
        for bridge_group in ("WORK_STUDY", "PERSONAL_ADMIN", "LEARNING", "DIGITAL_SOCIAL_UNSPLIT", "HOBBIES_CREATIVE",
                             "HOUSEHOLD_DAILY_LIVING", "UNSPECIFIED_VACATION"):
            self.assertNotIn(bridge_group, cats)
            for name in ("standard", "eda", "detail"):
                self.assertNotIn(bridge_group, self.res["reports"][name])

    def test_september_equals_closed_final(self):
        summary = {(r["Analysis_Level"], r["Value"]): float(r["Duration_min"]) for r in _csv(
            REAL_SEP / "Final_Detail_Analysis_2026-09-01_2026-09-30_Summary.csv")}
        M = self.res["ctx"]["M"]
        for (level, value), minutes in summary.items():
            dim = {"Category": "category", "Domain": "domain", "Energy": "energy", "Goal": "goal"}.get(level)
            if dim:
                self.assertAlmostEqual(M[(dim, value)]["2026-09"], minutes, places=1)
        self.assertEqual(self.res["ctx"]["regime"]["2026-08"], REGIME_TRANSITION)

    def test_category_and_subcategory_rollups(self):
        ctx = self.res["ctx"]
        for m in ctx["months"]:
            an = ctx["M"][("kpi", "ANALYTICAL_TOTAL")][m]
            cats = sum(ctx["M"][("category", c)][m] or 0 for c in ctx["taxonomy"].categories)
            gaps = sum(ctx["M"][k][m] for k in ctx["M"] if k[0] in ("unsplit", "unmapped"))
            self.assertAlmostEqual(cats + gaps, an, places=2)
            subs = sum(ctx["M"][k][m] for k in ctx["M"] if k[0] == "subcategory")
            self.assertAlmostEqual(subs, cats, places=2)

    def test_unmapped_visible_in_register_not_in_categories(self):
        reg = [r for r in self.res["I_register"] if r["dimension"] == "category" and r["status"] in ("MULTI_CATEGORY", "UNMAPPED")]
        self.assertTrue(any("Vacations" in r["legacy_source"] for r in reg))
        self.assertTrue(any("Work / Study" in r["legacy_source"] for r in reg))
        self.assertNotIn("Vacations", {r["category"] for r in self.res["B"] if r["row_type"] == "CURRENT_CATEGORY"})

    def test_devices_not_double_counted_and_shares_denominators(self):
        dev = {r["device_class"]: r for r in self.res["C"]}
        self.assertTrue(dev["LAPTOP"]["additive"].startswith("COMPONENT"))
        self.assertEqual(dev["LAPTOP"][f"{ha.SEG_TRANS}_hours"], "n/a")
        for seg in ha.SEGMENT_ORDER:
            total = sum(float(r[f"{seg}_pct_of_analytical"]) for r in self.res["B"] if r[f"{seg}_pct_of_analytical"] not in ("n/a", ""))
            self.assertAlmostEqual(total, 100.0, delta=0.1)

    def test_budget_uses_current_taxonomy_lines_and_capacity(self):
        self.assertTrue(all(b["budget_line"] in hc.BUDGET_LINE_ORDER or b["budget_line"] in hc.load_current_taxonomy().categories for b in self.res["J"]))
        self.assertTrue(all(b["user_approved_target"] == "NOT SET" for b in self.res["J"]))
        for r in self.res["K"]:
            if r["budget_line"] == "CAPACITY_CHECK":
                self.assertLessEqual(float(r["low_min_per_day"]), 1440)

    def test_outputs_written_only_to_out_dir(self):
        names = {p.name for p in self.out.iterdir()}
        for fname in self.rep.FILES.values():
            self.assertIn(fname, names)
        self.assertEqual(len(list((self.out / "charts").glob("*.svg"))), 7)
        self.assertEqual(len(list((self.out / "tables").glob("*.csv"))), len(ha.TABLES) + 1)


if __name__ == "__main__":
    unittest.main()
