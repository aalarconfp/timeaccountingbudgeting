"""Tests for dashboard_builder.py (period discovery and HTML rendering)."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import dashboard_builder as db

FACT_HEADER = ("Date,Source,Device,Activity,Category,Subcategory,Domain,Energy,Goal,"
               "Duration_sec,Duration_min,Allocation_Type,Taxonomy_Mapping\n")
RECON_HEADER = ("Date,Original_Recorded_min,Corrected_Recorded_min,Correction_min,"
                "Overlap_min,Uncovered_min,Tolerance_min,Status\n")


def write_period(root: Path, label: str, days: int) -> None:
    folder = root / "Integrated" / "Analysis" / "Final" / label
    folder.mkdir(parents=True)
    start = label[:10]
    fact, recon = [FACT_HEADER], [RECON_HEADER]
    for d in range(1, days + 1):
        date = f"{start[:8]}{d:02d}"
        fact.append(f"{date},Habit,Habit,Sleep,Health & Fitness,Sleep,Physical,Recovery,"
                    f"Maintenance,28800,480,Observed,Default\n")
        fact.append(f"{date},Off-Device,Off-Device,Untracked,Off-Device Life,Untracked Maintenance,"
                    f"Physical,Active,Maintenance,57600,960,Inferred Time Universe Residual,Synthetic\n")
        recon.append(f"{date},480,480,0,0,960,240,NORMAL_TOLERANCE\n")
    (folder / f"Final_Analysis_{label}.csv").write_text("".join(fact), encoding="utf-8")
    (folder / f"Final_Daily_Reconciliation_{label}.csv").write_text("".join(recon), encoding="utf-8")


class DashboardBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_month_wins_and_partial_month_is_interim(self) -> None:
        write_period(self.root, "2026-09-01_2026-09-15", 15)
        write_period(self.root, "2026-09-01_2026-09-30", 30)
        write_period(self.root, "2026-09-16_2026-09-30", 15)  # not a month start
        write_period(self.root, "2026-10-01_2026-10-15", 15)

        months = db.discover_months(self.root / "Integrated" / "Analysis" / "Final")
        self.assertEqual([m["label"] for m in months],
                         ["2026-09-01_2026-09-30", "2026-10-01_2026-10-15"])
        self.assertTrue(months[0]["full_month"])
        self.assertFalse(months[1]["full_month"])

    def test_build_and_render_embeds_data(self) -> None:
        write_period(self.root, "2026-10-01_2026-10-31", 31)
        data = db.build_data(self.root)
        month = data["months"][0]
        self.assertEqual(month["status"], "SIN CHECKLIST DE CIERRE")
        total = sum(row[-1] for row in month["fact"])
        self.assertAlmostEqual(total, 31 * 1440)
        self.assertIsNone(data["budget"])
        self.assertIsNone(data["historical"])

        html = db.render(data, db.TEMPLATE)
        self.assertNotIn(db.DATA_MARKER, html)
        payload = re.search(r"const DATA = (.*?);\n</script>", html, re.S).group(1)
        self.assertEqual(json.loads(payload)["months"][0]["key"], "2026-10")


if __name__ == "__main__":
    unittest.main()
