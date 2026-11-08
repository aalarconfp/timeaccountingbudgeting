# historical_annual_reports.py
"""Markdown reports and SVG charts for the annual historical analysis.

Rendering only: every number comes from the tables built by
historical_annual.py (the same rows are written to CSV). The primary reports
(Standard Report, Exploratory Analysis, Final Detail Analysis, Annual
Analysis) use the current taxonomy only. Internal bridge groups appear only in
the Mapping & Comparability report.

Structure and KPI definitions follow the monthly reports
(standard_report.py, exploratory_analysis.py) for the September 2026 close.
"""

from __future__ import annotations

import math
from collections import Counter

import historical_annual as ha
import historical_canonical as hc
import historical_monthly as hm
from config.historical import (
    ANALYSIS_END_MONTH,
    ANALYSIS_START_MONTH,
    REGIME_CURRENT,
    REGIME_LEGACY,
    REGIME_TRANSITION,
)
from historical_bridge import DAY_CAPACITY_MIN

TAG = ha.PERIOD_TAG
FILES = {
    "index": "00_INDEX.md",
    "standard": f"Annual_Standard_Report_{TAG}.md",
    "eda": f"Annual_Exploratory_Analysis_{TAG}.md",
    "detail": f"Annual_Final_Detail_Analysis_{TAG}.md",
    "analysis": f"Historical_Annual_Analysis_{TAG}.md",
    "mapping": f"Historical_Mapping_and_Comparability_Report_{TAG}.md",
}
SEGS = ha.SEGMENT_ORDER
SEG_SHORT = {ha.SEG_MIXED: "13 months (mixed)", ha.SEG_LEGACY: "Legacy (11 m)", ha.SEG_BASE: "Legacy baseline (9 m)",
             ha.SEG_TRANS: "Transition (Aug 26)", ha.SEG_CUR: "Current (Sep 26)"}
APPENDIX = "<!-- technical-appendix -->"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _t(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if v is None else str(v).replace("|", "/") for v in r) + " |")
    return "\n".join(out)


def _acc(res, seg, metric, field):
    return next((r.get(field, "") for r in res["A"] if r["segment"] == seg and r["metric"] == metric), "")


def _kpi(res, seg, name):
    return next((r["value"] for r in res["kpi"] if r["segment"] == seg and r["kpi"] == name), "")


def _ht(minutes) -> str:
    return ha.h_fmt(minutes)


def _pct(v) -> str:
    return "n/a" if v in ("", None, "n/a") else f"{float(v):.1f}%"


def _hours_pct(row, seg) -> str:
    h = row.get(f"{seg}_hours", "n/a")
    if h in ("n/a", ""):
        return "n/a"
    return f"{_ht(float(h) * 60)} ({_pct(row[f'{seg}_pct_of_analytical'])})"


def _perday(row, seg) -> str:
    v = row.get(f"{seg}_min_per_day", "n/a")
    return "n/a" if v in ("n/a", "") else ha.hm_fmt(v)


def header(res, title, purpose) -> list[str]:
    gen = res.get("generated_utc", "(build)")
    return [f"# {title}", "",
            f"Period: **{ANALYSIS_START_MONTH} → {ANALYSIS_END_MONTH}** (13 months, {sum(res['ctx']['days'].values())} calendar days). "
            f"Generated {gen} by `historical_annual.py`. {purpose}", "",
            "> **Measurement regimes.** Legacy manual tracking (2025-09 .. 2026-07) · Transition / review (2026-08, not a closed month) · "
            "Current reconciled accounting (2026-09, closed, authoritative). The 13-month total combines different measurement "
            "regimes and is a descriptive arithmetic reference, **not** a homogeneous measurement. All categories, domains, energies "
            "and goals are the current system taxonomy (`output/Reference/Taxonomy`, as in the monthly reports); legacy time that "
            "cannot be mapped defensibly is shown separately as *unsplit* or *unmapped legacy*, never inside a category.", ""]


def segment_rows(res, seg):
    """Canonical rows of a segment in the shape used by standard_report.py helpers."""
    ms = set(res["ctx"]["segments"][seg])
    return [{"Category": r["category"], "Subcategory": r["subcategory"], "Domain": r["domain"], "Energy": r["energy"],
             "Goal": r["goal"], "Duration_sec": r["minutes"] * 60} for r in res["ctx"]["recs"] if r["month"] in ms]


def recommendations(res, seg) -> list[str]:
    """standard_report.build_recommendations on the segment's canonical rows (same thresholds and wording)."""
    import standard_report as sr
    rows = segment_rows(res, seg)
    an = sum(r["Duration_sec"] for r in rows)
    cap = float(_kpi(res, seg, "clock_capacity")) * 60
    uniq = float(_kpi(res, seg, "unique_tracked")) * 60
    off = float(_kpi(res, seg, "off_device_life_residual")) * 60
    return sr.build_recommendations(rows, an, cap, uniq, off)


# --------------------------------------------------------------------------
# Standard Report
# --------------------------------------------------------------------------

def render_standard(res) -> str:
    ctx = res["ctx"]
    cats = [r for r in res["B"] if r["row_type"] == "CURRENT_CATEGORY"]
    gaps = [r for r in res["B"] if r["row_type"] != "CURRENT_CATEGORY"]
    dims = res["F_dims"]
    L = header(res, f"Annual Standard Time / Habit & Wellness Report — {ANALYSIS_START_MONTH} to {ANALYSIS_END_MONTH}",
               "Period-level analogue of the monthly Standard Report (`standard_report.py`), same KPI definitions.")
    a = L.append
    a("## 1. Executive Summary")
    a("")
    a(_t(["Metric"] + [SEG_SHORT[s] for s in SEGS], [
        ["Clock capacity"] + [_ht(_kpi(res, s, "clock_capacity")) for s in SEGS],
        ["Unique tracked time"] + [_ht(_kpi(res, s, "unique_tracked")) for s in SEGS],
        ["Off-Device Life (residual)"] + [_ht(_kpi(res, s, "off_device_life_residual")) for s in SEGS],
        ["Tracked overlap"] + [_ht(_kpi(res, s, "tracked_overlap")) for s in SEGS],
        ["Analytical activity"] + [_ht(_kpi(res, s, "analytical_activity")) for s in SEGS],
        ["Clock coverage"] + [_pct(_kpi(res, s, "clock_coverage_pct")) for s in SEGS],
        ["Mapped to a current category (% of analytical)"] + [_pct(_acc(res, s, "mapped_to_current_category", "minutes") and
                                                               100 * float(_acc(res, s, "mapped_to_current_category", "minutes")) / float(_kpi(res, s, "analytical_activity"))) for s in SEGS],
        ["Investment ratio"] + [_pct(_kpi(res, s, "investment_ratio_pct")) for s in SEGS],
        ["Recovery share"] + [_pct(_kpi(res, s, "recovery_share_pct")) for s in SEGS],
    ]))
    a("")
    a("Analytical activity can exceed clock capacity because multiple sources observe overlapping time. Clock accounting therefore uses "
      "unique tracked time + Off-Device Life, not the raw analytical total. In legacy months Off-Device Life is capacity − accounted "
      "(same rule as the current residual); the legacy household estimate is shown inside the Off-Device Life *category* (§4) with "
      "review-required confidence.")
    a("")
    a("## 2. Time Universe / Accounting")
    a("")
    metrics = [("calendar_capacity", "Clock capacity"), ("recorded", "Recorded (corrected tracked)"), ("overlap", "Tracked overlap"),
               ("accounted_unique_tracked", "Unique tracked"), ("off_device_life_residual", "Off-Device Life residual"),
               ("analytical_activity", "Analytical activity"), ("estimates_and_standard_allocations", "Estimates & standard allocations"),
               ("vacation_balancing_fill", "Vacation balancing fill"), ("accounted_excl_estimates", "Unique tracked excl. estimates"),
               ("mapped_to_current_category", "Mapped to a current category"), ("unsplit_legacy_multi_category", "Unsplit legacy (several categories)"),
               ("unmapped_legacy", "Unmapped legacy")]
    a(_t(["Metric (hours, % of capacity)"] + [SEG_SHORT[s] for s in SEGS],
         [[label] + [f"{_ht(_acc(res, s, m, 'minutes'))} ({_pct(_acc(res, s, m, 'pct_of_capacity'))})" for s in SEGS] for m, label in metrics]))
    a("")
    a(_t(["Days", ] + [SEG_SHORT[s] for s in SEGS], [
        [lab] + [_acc(res, s, m, "count") for s in SEGS] for m, lab in
        (("days_with_evidence", "Days with evidence"), ("days_review_status", "Days in REVIEW (> 120 min uncovered)"),
         ("days_over_capacity", "Days > 24 h"), ("days_incomplete_off_device", "Days with incomplete off-device records"))]))
    a("")
    a("![Monthly accounting](charts/01_accounting_coverage_monthly.svg)")
    a("")
    a("## 3. Where Did My Time Go?")
    a("")
    for seg in (ha.SEG_BASE, ha.SEG_CUR):
        top = sorted([r for r in cats if r[f"{seg}_hours"] not in ("n/a", "")], key=lambda r: -float(r[f"{seg}_hours"]))[:5]
        a(f"**Top categories — {SEG_SHORT[seg]}:** " + "; ".join(f"{r['category']}: {_hours_pct(r, seg)}" for r in top))
        a("")
        topd = sorted([r for r in dims if r["dimension"] == "domain" and r["value"] != hc.UNMAPPED_VALUE], key=lambda r: -float(r[f"{seg}_hours"]))[:5]
        a(f"**Top domains — {SEG_SHORT[seg]}:** " + "; ".join(f"{r['value']}: {_hours_pct(r, seg)}" for r in topd))
        a("")
        devs = [r for r in res["C"] if r["device_class"] in ("HABIT_EQUIVALENT_MANUAL", "LAPTOP", "DESKTOP", "COMPUTERS_TOTAL", "PHONE", "OFF_DEVICE_LIFE_RESIDUAL")
                and r[f"{seg}_hours"] not in ("n/a", "")]
        a(f"**By device — {SEG_SHORT[seg]}:** " + "; ".join(f"{r['monthly_report_device'] or r['device_class']}: {_hours_pct(r, seg)}" for r in devs))
        a("")
    a("Legacy categories marked *partial* understate their legacy time because part of it sits in unsplit legacy records (§4, §12).")
    a("")
    a("![Where did my time go](charts/02_where_did_my_time_go.svg)")
    a("")
    a("## 4. Current Categories")
    a("")
    a("Hours (share of analytical activity). Table B — `tables/Annual_B_Current_Category.csv`; subcategories in `tables/Annual_B_Current_Subcategory.csv`.")
    a("")
    a(_t(["Current category", SEG_SHORT[ha.SEG_LEGACY], SEG_SHORT[ha.SEG_TRANS], SEG_SHORT[ha.SEG_CUR], SEG_SHORT[ha.SEG_MIXED], "Legacy evidence"],
         [[r["category"], _hours_pct(r, ha.SEG_LEGACY), _hours_pct(r, ha.SEG_TRANS), _hours_pct(r, ha.SEG_CUR), _hours_pct(r, ha.SEG_MIXED),
           r["legacy_evidence"].replace("_", " ").lower()] for r in cats]
         + [[f"*{r['category']}*", _hours_pct(r, ha.SEG_LEGACY), _hours_pct(r, ha.SEG_TRANS), _hours_pct(r, ha.SEG_CUR), _hours_pct(r, ha.SEG_MIXED),
             "mapping gap — not a category"] for r in gaps]))
    a("")
    a("![Current categories by month](charts/03_current_categories_monthly.svg)")
    a("")
    for title, dim, key in (("## 5. Current Domains", "domain", None), ("## 6. Investment vs Maintenance vs Consumption", "goal", None),
                            ("## 7. Energy / Recovery", "energy", None)):
        a(title)
        a("")
        rows = [r for r in dims if r["dimension"] == dim]
        a(_t([dim.title(), SEG_SHORT[ha.SEG_LEGACY], SEG_SHORT[ha.SEG_TRANS], SEG_SHORT[ha.SEG_CUR], SEG_SHORT[ha.SEG_MIXED], "Legacy mapping confidence"],
             [[r["value"], _hours_pct(r, ha.SEG_LEGACY), _hours_pct(r, ha.SEG_TRANS), _hours_pct(r, ha.SEG_CUR), _hours_pct(r, ha.SEG_MIXED),
               r["legacy_mapping_confidence"] or "-"] for r in rows]))
        a("")
        if dim == "goal":
            a(_t(["KPI"] + [SEG_SHORT[s] for s in SEGS], [
                ["Investment ratio"] + [_pct(_kpi(res, s, "investment_ratio_pct")) for s in SEGS],
                ["Consumption proxy — Games"] + [_ht(_kpi(res, s, "consumption_proxy_games")) for s in SEGS],
                ["Consumption proxy — Entertainment"] + [_ht(_kpi(res, s, "consumption_proxy_entertainment")) for s in SEGS],
                ["Consumption proxy — Social consumption"] + [_ht(_kpi(res, s, "consumption_proxy_social")) for s in SEGS],
                ["Consumption proxy — Combined"] + [f"{_ht(_kpi(res, s, 'consumption_proxy_total'))} ({_pct(_kpi(res, s, 'consumption_proxy_pct'))})" for s in SEGS],
                ["Unsplit legacy digital leisure (not in the proxy)"] + [_ht(_kpi(res, s, "unsplit_legacy_digital_leisure")) for s in SEGS],
            ]))
            a("")
            a("For legacy months the proxy is a lower bound: legacy Entertainment device rows could be Entertainment, Games or Social Networking "
              "and are reported on the last line instead of being forced into one category.")
            a("")
        if dim == "energy":
            a(_t(["Per day"] + [SEG_SHORT[s] for s in SEGS], [
                [lab] + [f"{float(_kpi(res, s, k)):.1f} min/day" for s in SEGS] for k, lab in
                (("recovery_per_day", "Recovery/day"), ("investment_per_day", "Investment/day"), ("maintenance_per_day", "Maintenance/day"),
                 ("deep_per_day", "Deep/day"), ("passive_per_day", "Passive/day"))]))
            a("")
            a("Legacy 'Recovery / Rest' is mapped only through exact activity equivalents (e.g. Sleep → Recovery); the rest stays "
              f"'{hc.UNMAPPED_VALUE}'.")
            a("")
        if dim == "domain":
            a("![Domains, energy and goals](charts/04_domain_energy_goal_by_regime.svg)")
            a("")
    a("## 8. Device Distribution")
    a("")
    a("Device classes follow the monthly report's devices (column 'Monthly report device'). COMPUTERS_TOTAL = laptop + desktop and is never added "
      "to its components; August 2026 has no laptop / desktop split. Off-Device Life is the inferred residual, not a device.")
    a("")
    a(_t(["Device class", "Monthly report device", "Additive?", SEG_SHORT[ha.SEG_LEGACY], SEG_SHORT[ha.SEG_TRANS], SEG_SHORT[ha.SEG_CUR]],
         [[r["device_class"], r["monthly_report_device"] or "-", r["additive"].split(" ")[0], _hours_pct(r, ha.SEG_LEGACY),
           _hours_pct(r, ha.SEG_TRANS), _hours_pct(r, ha.SEG_CUR)] for r in res["C"]]))
    a("")
    a("![Devices by month](charts/05_devices_monthly.svg)")
    a("")
    a("## 9. Daily and Monthly Averages")
    a("")
    a("Per calendar day (minutes ÷ calendar days of the segment):")
    a("")
    a(_t(["Current category"] + [SEG_SHORT[s] for s in SEGS], [[r["category"]] + [_perday(r, s) for s in SEGS] for r in cats]))
    a("")
    ms = ctx["months"]
    a("Monthly evolution (h:mm per day; n/a = no comparable legacy evidence):")
    a("")
    a(_t(["Current category"] + [m[2:] for m in ms],
         [[r["category"]] + [ha.hm_fmt(ha.per_day(ctx, ("category", r["category"]), m)) for m in ms] for r in cats]
         + [[f"*{r['category']}*"] + [ha.hm_fmt(ha.per_day(ctx, (('unsplit' if r['row_type'].endswith('UNSPLIT') else 'unmapped'), r['category']), m)) for m in ms] for r in gaps]))
    a("")
    a("Weekday vs weekend (Mon–Fri vs Sat–Sun):")
    a("")
    a(_t(["Metric", "Legacy baseline weekday", "weekend", "Current weekday", "weekend"],
         [[r["label"], ha.hm_fmt(r["weekday_min_per_day"]), ha.hm_fmt(r["weekend_min_per_day"]),
           ha.hm_fmt(next(x["weekday_min_per_day"] for x in res["daytype"] if x["segment"] == ha.SEG_CUR and x["metric"] == r["metric"])),
           ha.hm_fmt(next(x["weekend_min_per_day"] for x in res["daytype"] if x["segment"] == ha.SEG_CUR and x["metric"] == r["metric"]))]
          for r in res["daytype"] if r["segment"] == ha.SEG_BASE]))
    a("")
    a("## 10. Important Patterns and Attention Areas")
    a("")
    for seg in (ha.SEG_BASE, ha.SEG_CUR):
        a(f"**{SEG_SHORT[seg]}** (same rules as the monthly Standard Report):")
        a("")
        for rec in recommendations(res, seg):
            a(f"- {rec}")
        if seg == ha.SEG_BASE:
            a("- Legacy caveat: the consumption proxy excludes the unsplit legacy digital leisure (§6), so the first recommendation may understate it.")
        a("")
    a("Annual patterns (rule-based; full matrix in `tables/Annual_Patterns_Findings.csv` and the Annual Analysis report):")
    a("")
    for f in res["P"]:
        if f["classification"] == ha.OBSERVED and f["confidence"] in ("HIGH", "MEDIUM") and f["finding_id"] in ("F01", "F02", "F07") or f["finding_id"].startswith("F14"):
            a(f"- {f['description']} *({f['classification'].replace('_', ' ').lower()}, {f['confidence'].lower()} confidence)*")
    for f in [x for x in res["P"] if x["finding_id"].startswith(("F05", "F10"))][:5]:
        a(f"- Caution: {f['description']} *({f['classification'].replace('_', ' ').lower()})*")
    a("")
    a("## 11. KPI Definitions")
    a("")
    for line in ("Clock capacity = number of days × 24 hours.",
                 "Unique tracked = corrected tracked time − tracked overlap (legacy: recorded − Σ max(0, day − 24 h)).",
                 "Off-Device Life = remaining clock capacity represented as human-life maintenance and other offline activity (capacity − unique tracked).",
                 "Analytical activity = all category rows including Off-Device Life; can exceed capacity by the overlap.",
                 "Investment ratio = Investment / (Investment + Maintenance + Consumption).",
                 "Recovery share = Recovery / analytical activity time.",
                 "Consumption proxy = Games + Entertainment + Social Networking time classified with Goal = Consumption.",
                 "Per-day values = segment minutes ÷ segment calendar days. Shares = % of the segment's analytical activity unless stated.",
                 "Annual adaptation: every segment total is a sum over its months; the 13-month (mixed) column adds different regimes."):
        a(f"- {line}")
    a("")
    a("## 12. Data Quality and Comparability Notes")
    a("")
    a(_t(["Current category", "Legacy evidence", "Note"], [[r["category"], r["legacy_evidence"], r["legacy_evidence_note"]] for r in cats]))
    a("")
    for line in (
        "Legacy time is mapped to current categories through documented rules (Mapping & Comparability report). Review-required mappings are included and flagged; unsplit and unmapped legacy time stays outside every category.",
        "Legacy Off-Device Life = household template estimate (review-required) + capacity − accounted; current Off-Device Life = inferred residual only. The two are not the same measurement.",
        "Current-only capture (system processes, unclassified ActivityWatch time, the unsplit phone social bucket) has no legacy equivalent.",
        "August 2026 is transition / review: ~1,230 min of Habit adjustments applied twice; laptop / desktop unsplit.",
        "September 2026 equals the closed Final outputs exactly (validated). Only one closed current month exists.",
        "No budget or target is configured in the current system; legacy targets are not carried forward.",
    ):
        a(f"- {line}")
    a("")
    v = res.get("validation") or []
    if v:
        a(f"Validation: {sum(1 for c in v if c['result'] == 'PASS')} PASS / {sum(1 for c in v if c['result'] == 'FAIL')} FAIL (`tables/Annual_Validation.csv`).")
        a("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Exploratory Analysis
# --------------------------------------------------------------------------

def render_eda(res) -> str:
    ctx = res["ctx"]
    L = header(res, f"Annual Exploratory Data Analysis — {ANALYSIS_START_MONTH} to {ANALYSIS_END_MONTH}",
               "Diagnostic layer, structured like the monthly `exploratory_analysis.py` report. It does not modify any dataset.")
    a = L.append
    a("## 1. Time Universe")
    a("")
    a(_t(["Metric"] + [SEG_SHORT[s] for s in SEGS], [
        [lab] + [(_ht(_kpi(res, s, k)) if not k.endswith("pct") else _pct(_kpi(res, s, k))) for s in SEGS]
        for k, lab in (("clock_capacity", "Capacity"), ("analytical_activity", "Analytical total"), ("unique_tracked", "Tracked unique"),
                       ("off_device_life_residual", "Off-device life"), ("tracked_overlap", "Tracked overlap"), ("clock_coverage_pct", "Analytical coverage"))]))
    a("")
    a("## 2. Primary Signals")
    a("")
    def dimrow(dim, v, seg):
        r = next(x for x in res["F_dims"] if x["dimension"] == dim and x["value"] == v)
        return _hours_pct(r, seg)
    a(_t(["Signal"] + [SEG_SHORT[s] for s in SEGS], [[lab] + [dimrow(d, v, s) for s in SEGS] for d, v, lab in
         (("goal", "Maintenance", "Maintenance"), ("energy", "Recovery", "Recovery"), ("energy", "Deep", "Deep energy"), ("energy", "Passive", "Passive energy"))]))
    a("")
    a("## 3. Daily Average Check")
    a("")
    a(_t(["Per day"] + [SEG_SHORT[s] for s in SEGS], [[lab] + [f"{float(_kpi(res, s, k)):.1f} min/day" for s in SEGS] for k, lab in
         (("maintenance_per_day", "Maintenance/day"), ("recovery_per_day", "Recovery/day"), ("deep_per_day", "Deep energy/day"), ("passive_per_day", "Passive energy/day"))]))
    a("")
    a("## 4. Device")
    a("")
    a(_t(["Device class", SEG_SHORT[ha.SEG_LEGACY], SEG_SHORT[ha.SEG_CUR], "Baseline trend", "Comparability"],
         [[r["device_class"], _hours_pct(r, ha.SEG_LEGACY), _hours_pct(r, ha.SEG_CUR), r["baseline_trend"], r["comparability"]] for r in res["C"]]))
    a("")
    a("## 5. Category")
    a("")
    cats = [r for r in res["B"] if r["row_type"] == "CURRENT_CATEGORY"]
    a(_t(["Category", "Legacy baseline median/day", "P25–P75", "CV", "Trend", "Sep 26/day", "Sep vs baseline", "Legacy mapping confidence", "Current-only capture %"],
         [[r["category"], ha.hm_fmt(r["baseline_median_min_per_day"]), f"{ha.hm_fmt(r['baseline_p25_min_per_day'])}–{ha.hm_fmt(r['baseline_p75_min_per_day'])}",
           r["baseline_cv"], r["baseline_trend"], ha.hm_fmt(r["current_2026_09_min_per_day"]), r["current_vs_baseline"].replace("_BASELINE_ENVELOPE", ""),
           r["legacy_mapping_confidence"] or "-", r["current_share_from_current_only_capture_pct"]] for r in cats]))
    a("")
    a("Subcategories (top 25 by 13-month time):")
    a("")
    subs = sorted(res["B_sub"], key=lambda r: -float(r[f"{ha.SEG_MIXED}_hours"]))[:25]
    a(_t(["Category › Subcategory", SEG_SHORT[ha.SEG_LEGACY], SEG_SHORT[ha.SEG_CUR]],
         [[f"{r['category']} › {r['subcategory']}", _hours_pct(r, ha.SEG_LEGACY), _hours_pct(r, ha.SEG_CUR)] for r in subs]))
    a("")
    for n, dim in ((6, "domain"), (7, "energy"), (8, "goal")):
        a(f"## {n}. {dim.title()}")
        a("")
        rows = [r for r in res["F_dims"] if r["dimension"] == dim]
        a(_t([dim.title(), SEG_SHORT[ha.SEG_LEGACY], SEG_SHORT[ha.SEG_TRANS], SEG_SHORT[ha.SEG_CUR], "Legacy baseline median/day", "Legacy confidence"],
             [[r["value"], _hours_pct(r, ha.SEG_LEGACY), _hours_pct(r, ha.SEG_TRANS), _hours_pct(r, ha.SEG_CUR),
               ha.hm_fmt(r["baseline_median_min_per_day"]), r["legacy_mapping_confidence"] or "-"] for r in rows]))
        a("")
    a("## 9. Top Analytical Activities")
    a("")
    for seg in (ha.SEG_LEGACY, ha.SEG_CUR):
        a(f"**{SEG_SHORT[seg]}**")
        a("")
        a(_t(["#", "Device class", "Category", "Subcategory", "Domain", "Energy", "Goal", "Hours", "%", "Mapping"],
             [[r["rank"], r["device_class"], r["category"], r["subcategory"], r["domain"], r["energy"], r["goal"], r["hours"],
               r["pct_of_analytical"], r["mapping_status"]] for r in res["top"] if r["segment"] == seg][:20]))
        a("")
    a("## 10. Concentration")
    a("")
    a(_t(["Segment", "Dimension", "Top1 %", "Top3 %", "Top5 %", "HHI"],
         [[SEG_SHORT[r["segment"]], r["dimension"], r["top1_pct"], r["top3_pct"], r["top5_pct"], r["hhi"]] for r in res["concentration"]]))
    a("")
    a("## 11. Taxonomy Review Candidates")
    a("")
    a("`exploratory_analysis.taxonomy_candidates` applied to the annual hierarchy, plus legacy mapping reasons.")
    a("")
    a(_t(["Segment", "Category", "Subcategory", "Domain / Energy / Goal", "Minutes", "%", "Reasons"],
         [[SEG_SHORT[r["segment"]], r["category"], r["subcategory"], f"{r['domain']} / {r['energy']} / {r['goal']}", r["duration_min"],
           r["percent_of_total"], r["review_reasons"]] for r in res["review_candidates"]][:40]))
    a("")
    a("Monthly trend classification (current categories, domains, energies, goals; `tables/Annual_G_Monthly_Trends.csv`):")
    a("")
    ct = Counter((r["dimension"], r["change_type"]) for r in res["G_trends"] if r["change_type"])
    a(_t(["Dimension", "Behavioral candidate", "Possible methodological", "Methodological break", "Not comparable"],
         [[d] + [ct.get((d, t), 0) for t in ("BEHAVIORAL_CANDIDATE", "POSSIBLE_METHODOLOGICAL", "METHODOLOGICAL_BREAK", "NOT_COMPARABLE")]
          for d in ("category", "domain", "energy", "goal", "line", "anchor", "device")]))
    a("")
    a("Same-day associations (Spearman; compositional caveat):")
    a("")
    a(_t(["Segment", "A", "B", "Days", "ρ", "Label"], [[r["segment"], r["metric_a"], r["metric_b"], r["days"], r["spearman_rho"], r["label"]] for r in res["assoc"]]))
    a("")
    a("## 12. Interpretation Guidance")
    a("")
    for i, line in enumerate((
        "Review large nodes before changing their taxonomy.",
        "Separate taxonomy problems from genuinely large behaviors.",
        "Treat Off-Device Life as meaningful human-life maintenance, not as discarded or missing time.",
        "Investigate Uncategorized and generic buckets separately.",
        "Use repeated periods before interpreting burnout or workload trends.",
        "Do not read legacy → current differences of categories marked partial / capture-differs / current-only as behaviour.",
        "Unsplit and unmapped legacy time is a mapping gap, not a category; see the review register.",
    ), start=1):
        a(f"{i}. {line}")
    a("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Final Detail Analysis
# --------------------------------------------------------------------------

def render_detail(res) -> str:
    L = header(res, f"Annual Final Detail Analysis — {ANALYSIS_START_MONTH} to {ANALYSIS_END_MONTH}",
               "Hierarchy Device → Category → Subcategory → Domain → Energy → Goal (+ mapping status) in the current taxonomy.")
    a = L.append
    a("Full table: `tables/Annual_Final_Detail_Hierarchy.csv` (one row per segment × device class × category × subcategory × domain × energy × goal × mapping status).")
    a("")
    a("## 1. Mapped activity vs unmapped historical evidence")
    a("")
    a(_t(["Month", "Regime", "Analytical h", "Current-native h", "Mapped high-confidence h", "Mapped review-required h", "Off-Device Life rule h", "Unsplit h", "Unmapped h"],
         [[r["month"], r["regime"].split("_")[0], r["analytical_activity_h"], r["current_native_h"], r["mapped_high_confidence_h"],
           r["mapped_review_required_h"], r["residual_rule_h"], r["unsplit_multi_category_h"], r["unmapped_h"]] for r in res["H_reconciliation"]]))
    a("")
    a("Identity per month: analytical activity = recorded + Off-Device Life residual = current-native + mapped (high + review) + residual rule + unsplit + unmapped.")
    a("")
    for seg in (ha.SEG_LEGACY, ha.SEG_TRANS, ha.SEG_CUR):
        a(f"## {SEG_SHORT[seg]} — category › subcategory")
        a("")
        agg = Counter()
        st = {}
        for r in res["detail"]:
            if r["segment"] == seg:
                k = (r["category"], r["subcategory"])
                agg[k] += r["minutes"]
                st.setdefault(k, Counter())[r["mapping_status"]] += r["minutes"]
        an = sum(agg.values())
        a(_t(["Category", "Subcategory", "Hours", "% of analytical", "Mapping status"],
             [[c, s, f"{v / 60:.1f}", f"{100 * v / an:.2f}", ", ".join(k for k, _ in st[(c, s)].most_common())]
              for (c, s), v in sorted(agg.items(), key=lambda x: -x[1])]))
        a("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Annual Analysis (patterns + budgeting)
# --------------------------------------------------------------------------

def render_analysis(res) -> str:
    ctx = res["ctx"]
    P, J, K, D = res["P"], res["J"], res["K"], res["L"]
    b0, b1 = ctx["baseline"][0], ctx["baseline"][-1]
    cats = [r for r in res["B"] if r["row_type"] == "CURRENT_CATEGORY"]
    L = header(res, f"Historical Annual Analysis — {ANALYSIS_START_MONTH} to {ANALYSIS_END_MONTH}",
               "Patterns, hypotheses and the initial Time Budgeting framework (current taxonomy). Not an approved budget, not a period close.")
    a = L.append
    obs = [f for f in P if f["classification"] == ha.OBSERVED]
    hyp = [f for f in P if f["classification"] == ha.HYPOTHESIS]
    meth = [f for f in P if f["classification"] == ha.METHOD]
    ins = [f for f in P if f["classification"] == ha.INSUFFICIENT]
    a("## 1. Executive Summary")
    a("")
    a(f"- **Accounting:** unique tracked time covers {_pct(_kpi(res, ha.SEG_LEGACY, 'unique_tracked_coverage_pct'))} of capacity in Legacy "
      f"(incl. household template estimates), {_pct(_kpi(res, ha.SEG_TRANS, 'unique_tracked_coverage_pct'))} in Transition and "
      f"{_pct(_kpi(res, ha.SEG_CUR, 'unique_tracked_coverage_pct'))} in Current; excluding estimates: "
      f"{_pct(_acc(res, ha.SEG_LEGACY, 'accounted_excl_estimates', 'pct_of_capacity'))} / {_pct(_acc(res, ha.SEG_TRANS, 'accounted_excl_estimates', 'pct_of_capacity'))} / "
      f"{_pct(_acc(res, ha.SEG_CUR, 'accounted_excl_estimates', 'pct_of_capacity'))}. September 2026 equals the closed Final outputs.")
    an_l = float(_kpi(res, ha.SEG_LEGACY, "analytical_activity"))
    a(f"- **Mapping:** {100 * float(_acc(res, ha.SEG_LEGACY, 'mapped_to_current_category', 'minutes')) / an_l:.1f}% of legacy analytical activity maps to a current category; "
      f"{100 * float(_acc(res, ha.SEG_LEGACY, 'unsplit_legacy_multi_category', 'minutes')) / an_l:.1f}% is unsplit (several possible categories) and "
      f"{100 * float(_acc(res, ha.SEG_LEGACY, 'unmapped_legacy', 'minutes')) / an_l:.1f}% unmapped.")
    a(f"- **Findings:** {len(P)} rule-based — {len(obs)} observed patterns, {len(hyp)} behavioural hypotheses, {len(meth)} possible methodological effects, {len(ins)} insufficient data.")
    for f in [x for x in P if x["finding_id"] in ("F01", "F02", "F07")]:
        a(f"  - {f['description']}")
    rng = [b for b in J if b["suggested_low_min_per_day"] != "" and b["is_sub_line"] == "N"]
    nor = [b["budget_line"] for b in J if b["suggested_low_min_per_day"] == "" and b["is_sub_line"] == "N" and b["range_status"] != "NOT_BUDGETABLE"]
    a(f"- **Budgeting:** {len(rng)} current-taxonomy lines have a data-supported (or explicit cross-regime) range; no range yet for: {', '.join(nor)}. "
      "Three scenario templates are provided; none is an approved budget and no target is set.")
    a("")
    a("## 2. Scope and Data Sources")
    a("")
    a(_t(["Month", "Regime", "Calendar days", "Days with evidence"], [[m, ctx["regime"][m], ctx["days"][m], int(ctx["W"][m]["days_with_data"])] for m in ctx["months"]]))
    a("")
    a(_t(["Input", "SHA-256 (12)", "Matches pipeline manifest"], [[i["file"], i["sha256"][:12], i["match"]] for i in res["inputs"]]))
    a("")
    a(f"Reporting taxonomy: `{ctx['taxonomy'].source}` — categories {', '.join(ctx['taxonomy'].categories)}; domains {', '.join(ctx['taxonomy'].domains)}; "
      f"energies {', '.join(ctx['taxonomy'].energies)}; goals {', '.join(ctx['taxonomy'].goals)}.")
    a("")
    a("## 3. Methodology and Regime Changes")
    a("")
    a("Translation chain: legacy source value → documented mapping rules → current canonical value → annual aggregation → report. "
      "Legacy values and provenance are preserved in `output/Historical/Historical_Normalized_Records.csv`; the mapping is documented in the Mapping & Comparability report.")
    a("")
    a(f"- Legacy baseline window: {b0} .. {b1} (excludes the ramp-up month and the month with incomplete off-device rows).")
    a("- Statistics on monthly min/day series: median, P25–P75, CV, OLS trend (RISING/FALLING when ≥ max(10 min/day, 20% of mean); VOLATILE when CV ≥ 0.45).")
    a("- Current vs baseline: WITHIN when inside [0.8× min, 1.2× max] of baseline months; compared only for categories with comparable legacy evidence.")
    a("")
    for month, items in hm.METHOD_BREAKS.items():
        for target, desc in items:
            if target == "*" or target.startswith("anchor:"):
                a(f"- **{month}**: {desc}")
    a("")
    a("## 4. Accounting and Coverage")
    a("")
    a(_t(["Month", "Regime", "Recorded h", "Overlap h", "Unique tracked h", "Off-Device Life h", "Coverage %", "Excl. estimates %", "Mapped % of analytical", "Review days"],
         [[r["month"], r["regime"].split("_")[0], r["recorded_h"], r["overlap_h"], r["accounted_unique_tracked_h"], r["off_device_life_residual_h"],
           r["coverage_pct_of_capacity"], r["coverage_excl_estimates_pct_of_capacity"], r["mapped_pct_of_analytical"], r["days_review_status"]] for r in res["A_monthly"]]))
    a("")
    a("## 5. Annual Category Distribution")
    a("")
    a(_t(["Current category", "Legacy baseline median/day", "P25–P75", "CV", "Trend", "Aug 26/day", "Sep 26/day", "Sep vs baseline", "Legacy evidence"],
         [[r["category"], ha.hm_fmt(r["baseline_median_min_per_day"]), f"{ha.hm_fmt(r['baseline_p25_min_per_day'])}–{ha.hm_fmt(r['baseline_p75_min_per_day'])}",
           r["baseline_cv"], r["baseline_trend"], ha.hm_fmt(r["transition_2026_08_min_per_day"]), ha.hm_fmt(r["current_2026_09_min_per_day"]),
           r["current_vs_baseline"].replace("_BASELINE_ENVELOPE", ""), r["legacy_evidence"]] for r in cats]))
    a("")
    a("## 6. Device Distribution")
    a("")
    a(_t(["Device class", "Legacy baseline median/day", "Sep 26/day", "Trend", "Comparability"],
         [[r["device_class"], ha.hm_fmt(r["baseline_median_min_per_day"]), ha.hm_fmt(r[f"{ha.SEG_CUR}_min_per_day"]), r["baseline_trend"], r["comparability"]] for r in res["C"]]))
    a("")
    a("## 7. Anchor Activities")
    a("")
    a(_t(["Activity (current category › subcategory)", "Baseline median/day", "CV", "Trend", "Days active (baseline)", "Sep 26/day", "Days active Sep 26"],
         [[r["current_category_subcategory"], ha.hm_fmt(r["baseline_median_min_per_day"]), r["baseline_cv"], r["baseline_trend"],
           f"{r[f'{ha.SEG_BASE}_days_active_pct']}%", ha.hm_fmt(r[f"{ha.SEG_CUR}_min_per_day"]), f"{r[f'{ha.SEG_CUR}_days_active_pct']}%"] for r in res["D"]]))
    a("")
    a("![Anchor activities](charts/06_anchor_activities_monthly.svg)")
    a("")
    a("## 8. Goals and Domains")
    a("")
    a("Goal = the current taxonomy class (Investment / Maintenance / Consumption) assigned to each activity; personal objectives are not defined in the system. "
      "Time allocated to a class is not evidence of progress.")
    a("")
    a(_t(["Dimension", "Value", SEG_SHORT[ha.SEG_BASE], SEG_SHORT[ha.SEG_CUR]],
         [[r["dimension"], r["value"], _hours_pct(r, ha.SEG_BASE), _hours_pct(r, ha.SEG_CUR)] for r in res["F_dims"] if r["dimension"] in ("goal", "domain")]))
    a("")
    a("## 9. Monthly Trends")
    a("")
    lines = [l for l in hc.BUDGET_LINE_ORDER]
    a(_t(["Planning line (per day)"] + [m[2:] for m in ctx["months"]],
         [[hc.BUDGET_LINE_LABEL[l]] + [ha.hm_fmt(ha.per_day(ctx, ("line", l), m)) for m in ctx["months"]] for l in lines]))
    a("")

    def fl(fs):
        return _t(["ID", "Finding", "Evidence", "Persistence", "Confidence", "Budget implication"],
                  [[f["finding_id"], f["description"], f["evidence"], f["persistence"], f["confidence"], f["budget_implication"]] for f in fs]) if fs else "None."
    a("## 10. Observed Patterns")
    a("")
    a(fl(obs))
    a("")
    a("## 11. Hypotheses and Uncertainties")
    a("")
    a("**Behavioural hypotheses** (recorded under one method; not proof of behaviour change; no causal claims):")
    a("")
    a(fl(hyp))
    a("")
    a("**Possible methodological effects:**")
    a("")
    a(fl(meth))
    a("")
    a("**Insufficient data:**")
    a("")
    a(fl(ins))
    a("")
    a("## 12. Implications for Time Budgeting")
    a("")
    for i, line in enumerate((
        "Capacity is the hard constraint: Sleep and Off-Device Life take a large, stable part of every day before anything else is planned.",
        "Protect first: Health & Fitness › Sleep, Workout + Meditation, Off-Device Life and in-person relationships (Social Networking › Family / Social Time Tracking).",
        "Plan Productivity & Finance + Education together until the legacy Work / Study split is decided (D-H02); commitments must come from the user.",
        "Games + Entertainment + Social Networking (consumption) is the main flexible line; its absolute level depends on capture method.",
        "Use ranges and quarterly allowances for event-driven lines (family, friends, travel).",
        "Keep an explicit unallocated reserve; never assign every minute.",
        "Wait for 3 closed current-regime months before fixing ranges that show regime divergence.",
    ), start=1):
        a(f"{i}. {line}")
    a("")
    a("## 13. Initial Budgeting Framework")
    a("")
    a("Method: start from calendar capacity → reserve fundamental needs (floors) → enter fixed commitments (user) → allocate goal time to explicit goals (user) → "
      "set ceilings for flexible lines → keep the reserve → check Σ lower bounds + reserve ≤ capacity → at period end compare planned vs recorded vs "
      "accounted and record the deviation reason before judging it.")
    a("")
    a("Table J — baseline (`tables/Annual_J_Budget_Baseline.csv`), min per day. *Observed average* = legacy segment total ÷ days; *comparable baseline* = "
      "legacy baseline median where the line is comparable; *planning range* only where data support it; *user target* is never set by this analysis.")
    a("")
    a(_t(["Line (current taxonomy)", "Protection", "Observed avg (legacy)", "Comparable baseline", "P25–P75", "Sep 26", "Planning range", "Status", "Confidence", "User target", "Pending"],
         [[("↳ " if b["is_sub_line"] != "N" else "") + b["label"], b["protection_or_flexibility"], b["historical_observed_avg_legacy_min_per_day"],
           b["comparable_baseline_median_min_per_day"] or "—", f"{b['baseline_p25_min_per_day']}–{b['baseline_p75_min_per_day']}", b["current_2026_09_min_per_day"],
           f"{b['suggested_low_min_per_day']}–{b['suggested_high_min_per_day']}" if b["suggested_low_min_per_day"] != "" else "—",
           b["range_status"], b["confidence"], b["user_approved_target"], b["pending_information"] or "-"] for b in J]))
    a("")
    a("![Budget reference](charts/07_budget_reference.svg)")
    a("")
    a("Table J (scenarios) — `tables/Annual_J_Budget_Scenarios.csv` (h/week, h/30-day month, h/91-day quarter, h/365-day year in the CSV). **Templates only.**")
    a("")
    for s in ("SUSTAINABLE", "GOAL_FOCUSED", "CONSERVATIVE"):
        a(f"**{s.replace('_', ' ').title()}**")
        a("")
        a(_t(["Line", "Status", "min/day", "h/week", "h/month (30 d)", "Note"],
             [[r["label"], r["status"], f"{r['low_min_per_day']}–{r['high_min_per_day']}" if r["low_min_per_day"] != "" else "TO_DEFINE",
               f"{r.get('low_h_per_week', '')}–{r.get('high_h_per_week', '')}" if r.get("low_h_per_week", "") != "" else "",
               f"{r.get('low_h_per_month_30d', '')}–{r.get('high_h_per_month_30d', '')}" if r.get("low_h_per_month_30d", "") != "" else "", r["note"]]
              for r in K if r["scenario"] == s]))
        a("")
    a("## 14. Recommended Next Decisions")
    a("")
    a(_t(["ID", "Group", "Decision", "Default in this analysis"], [[d["decision_id"], d["decision_group"], d["decision"], d["current_default_in_analysis"]] for d in D]))
    a("")
    a("## 15. Data Quality and Limitations")
    a("")
    for line in ("Mixed methods: legacy = manual transcription + manual entries + templates; current = automatic capture + screenshots + inferred residual.",
                 "One closed current month: current-regime variability is unknown.",
                 "Unsplit legacy records (Work / Study, Entertainment device rows) understate several current categories in legacy months.",
                 "Legacy Off-Device Life includes a household template estimate; the current one is a residual.",
                 "The unsplit phone social bucket hides messaging vs feeds in the current system.",
                 "Holidays and vacations are not separated in the weekday / weekend profile.",
                 "Records show where time was recorded, not outcomes, quality or progress towards goals."):
        a(f"- {line}")
    a("")
    a("## 16. Appendix — Definitions, Validation and Sources")
    a("")
    a("Definitions as in the Standard Report §11. Mapping rules: Mapping & Comparability report.")
    a("")
    a(_t(["Check", "Result", "Detail"], [[c["check"], c["result"], c["detail"]] for c in res.get("validation", [])]))
    a("")
    a("Regenerate:")
    a("")
    a("```powershell")
    a("$env:PYTHONIOENCODING = 'utf-8'")
    a(".\\.venv\\Scripts\\python.exe .\\historical_pipeline.py")
    a(".\\.venv\\Scripts\\python.exe .\\historical_annual.py")
    a("```")
    a("")
    return "\n".join(L)


# --------------------------------------------------------------------------
# Mapping & comparability (technical; may use bridge terminology)
# --------------------------------------------------------------------------

def render_mapping(res) -> str:
    ctx = res["ctx"]
    L = header(res, f"Historical Mapping and Comparability Report — {ANALYSIS_START_MONTH} to {ANALYSIS_END_MONTH}",
               "Technical report: how legacy values reach the current taxonomy, with confidence and comparability. May use internal bridge terminology.")
    a = L.append
    a("## 1. Translation chain")
    a("")
    a("legacy source value (workbook cell, preserved verbatim) → mapping rules `config/historical_bridge/legacy_activity_bridge.csv` + "
      "`legacy_label_maps.csv` (applied by `historical_bridge.normalize_legacy`) → current canonical category / subcategory / domain / energy / goal / "
      "device class (validated against the current taxonomy by `historical_canonical.validate_bridge_against_taxonomy`) → annual aggregation "
      "(`historical_canonical.build_canonical_records`) → reports.")
    a("")
    a("The internal *bridge group* is a comparability device (e.g. to detect capture differences); it is kept as a diagnostic field only and is not a reporting taxonomy.")
    a("")
    a("Mapping changes in this revision (rationale stored in the mapping table):")
    a("")
    a("- `Work / Study` device rows: Productivity & Finance (REVIEW_REQUIRED) → **MULTI_CATEGORY {Productivity & Finance, Education}** (unsplit; domain REVIEW_REQUIRED).")
    a("- `Entertainment` device rows (not TV): Entertainment (REVIEW_REQUIRED) → **MULTI_CATEGORY {Entertainment, Games, Social Networking}** (unsplit; domain REVIEW_REQUIRED).")
    a("- `Relationships / Social · Friends · PC Desktop`: UNMAPPED → **Games (REVIEW_REQUIRED)**.")
    a("")
    a("## 2. Inventory — every legacy category / subcategory / device")
    a("")
    inv = [r for r in res["H_inventory"] if not str(r["legacy_category"]).startswith("[label")]
    a(_t(["Legacy category", "Legacy subcategory", "Device", "Current category", "Current subcategory", "Confidence", "Domain (conf.)", "Device class", "Hours", "Evidence", "Rationale"],
         [[r["legacy_category"], r["legacy_subcategory"], r["legacy_device"], r["current_category"], r.get("current_subcategory", ""), r["category_confidence"],
           f"{r.get('current_domain', '')} ({r.get('domain_confidence', '')})", r.get("current_device_class", ""), r.get("hours", ""), r.get("evidence_types", ""),
           r.get("rationale", "")] for r in inv]))
    a("")
    a("Energy and goal per legacy key (legacy label → current value [confidence], hours):")
    a("")
    a(_t(["Legacy key", "Energy mapping", "Goal mapping", "Goal vs current category default"],
         [[f"{r['legacy_category']} / {r['legacy_subcategory']} / {r['legacy_device']}", r.get("energy_mapping", ""), r.get("goal_mapping", ""),
           r.get("goal_vs_current_category_default", "")] for r in inv if r.get("records")]))
    a("")
    a("Label maps (used only where no exact activity equivalent exists):")
    a("")
    a(_t(["Dimension / legacy label", "Current value", "Status", "Rationale"],
         [[f"{lm['dimension']}: {lm['legacy_value']}", lm["canonical_value"] or "(none)", lm["status"], lm["rationale"]] for lm in ctx["data"]["label_maps"]]))
    a("")
    a("Legacy 'Other' is not a timesheet row: it exists only in the legacy Report sheets as capacity − recorded (with mis-entered capacities) and is "
      "therefore not mapped; its time is the Off-Device Life residual here. Legacy targets in the Report sheets are not used.")
    a("")
    a("## 3. Comparability of each current category")
    a("")
    a(_t(["Current category", "Legacy evidence", "Legacy sources", "In unsplit legacy records?", "Current-only capture %", "MoM comparability"],
         [[r["current_category"], r["legacy_evidence"], r["legacy_sources"] or "-", r["candidate_in_unsplit_legacy_records"],
           r["current_share_from_current_only_capture_pct"], r["month_over_month_comparability"]] for r in res["H_comparability"]]))
    a("")
    a("## 4. Reconciliation (source → normalized → mapped)")
    a("")
    a(_t(["Month", "Legacy report h", "Normalized source h", "Recorded h", "Off-Device Life h", "Analytical h", "Native h", "High h", "Review h", "Residual-rule h", "Unsplit h", "Unmapped h"],
         [[r["month"], r["legacy_report_total_h"], r["source_normalized_total_h"], r["recorded_h"], r["off_device_life_residual_h"], r["analytical_activity_h"],
           r["current_native_h"], r["mapped_high_confidence_h"], r["mapped_review_required_h"], r["residual_rule_h"], r["unsplit_multi_category_h"], r["unmapped_h"]]
          for r in res["H_reconciliation"]]))
    a("")
    a("Legacy report − detail differences are explained by rows without a total formula (pipeline check H-RPT-1); the detail total is the analytical base.")
    a("")
    a("## 5. Review register (summary)")
    a("")
    reg = res["I_register"]
    agg = Counter()
    for r in reg:
        agg[(r["dimension"], r["status"])] += r["hours"]
    a(_t(["Dimension", "Status", "Hours"], [[d, s, f"{v:.1f}"] for (d, s), v in sorted(agg.items())]))
    a("")
    a("Category-level gaps and review items (full list: `tables/Historical_Mapping_Review_Register.csv`):")
    a("")
    a(_t(["Status", "Legacy source", "Current value / candidates", "Hours", "Treatment", "Decision"],
         [[r["status"], r["legacy_source"], r["current_value_or_candidates"], r["hours"], r["treatment"], r["decision_id"]] for r in reg if r["dimension"] == "category"]))
    a("")
    a("## 6. Internal bridge-group diagnostics (technical)")
    a("")
    a(_t(["Internal bridge group", "Cross-regime comparability", SEG_SHORT[ha.SEG_BASE] + " /day", SEG_SHORT[ha.SEG_CUR] + " /day", "Note"],
         [[r["internal_bridge_group"], r["cross_regime_comparability"], ha.hm_fmt(r[f"{ha.SEG_BASE}_min_per_day"]), ha.hm_fmt(r[f"{ha.SEG_CUR}_min_per_day"]), r["note"]]
          for r in res["H_bridge_diag"]]))
    a("")
    return "\n".join(L)


def render_index(res) -> str:
    L = [f"# Annual historical analysis {ANALYSIS_START_MONTH} → {ANALYSIS_END_MONTH} — index", "",
         f"Generated {res.get('generated_utc', '(build)')} by `historical_annual.py`. Private output (git-ignored). "
         "Primary reports use the current system taxonomy; the mapping report is the only place where internal bridge groups appear.", "",
         _t(["File", "Role"], [
             [FILES["standard"], "A. Annual Standard Report (structure and KPIs of the monthly Standard Report)"],
             [FILES["eda"], "B. Annual Exploratory Analysis (structure of the monthly EDA)"],
             [FILES["detail"], "C. Annual Final Detail Analysis (hierarchy in the current taxonomy; mapped vs unmapped)"],
             [FILES["analysis"], "Patterns, hypotheses and the Time Budgeting baseline / scenarios (J)"],
             [FILES["mapping"], "H. Mapping & comparability report (technical)"],
             ["tables/Annual_B_Current_Category.csv, Annual_B_Current_Subcategory.csv", "D. Category / subcategory analysis"],
             ["tables/Annual_C_Device.csv", "E. Device analysis"],
             ["tables/Annual_F_Domain_Energy_Goal.csv, Annual_F_KPI.csv", "F. Domain, energy, goal and KPI analysis"],
             ["tables/Annual_G_Monthly_Trends.csv", "G. Monthly trends (current taxonomy) with change classification"],
             ["tables/Historical_Mapping_Inventory.csv, Historical_Category_Comparability.csv, Historical_Mapping_Reconciliation.csv, Historical_Bridge_Group_Diagnostics.csv", "H. Mapping & comparability data"],
             ["tables/Historical_Mapping_Review_Register.csv", "I. Unresolved mapping / review register"],
             ["tables/Annual_J_Budget_Baseline.csv, Annual_J_Budget_Scenarios.csv, Annual_Pending_Decisions.csv", "J. Time Budgeting baseline, scenarios, decisions"],
             ["tables/Annual_Final_Detail_Hierarchy.csv, Annual_EDA_*.csv, Annual_D_Anchor_Activities.csv, Annual_Patterns_Findings.csv, Annual_Daytype_Profile.csv, Annual_Daily_Associations.csv", "Supporting tables"],
             ["tables/Annual_Validation.csv, Annual_Run_Manifest.json", "Validation and provenance"],
             ["charts/*.svg", "Charts linked from the reports"],
         ]), "",
         "Superseded outputs from the earlier bridge-group version are kept under `output/Historical/_superseded/` and are not authoritative.", ""]
    return "\n".join(L)


def render_all(res) -> dict[str, str]:
    return {"standard": render_standard(res), "eda": render_eda(res), "detail": render_detail(res),
            "analysis": render_analysis(res), "mapping": render_mapping(res), "index": render_index(res)}


def report_files(res) -> dict[str, str]:
    return {FILES[k]: v for k, v in res["reports"].items()}


# --------------------------------------------------------------------------
# Charts (stdlib SVG, light surface, native <title> tooltips)
# --------------------------------------------------------------------------

PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948")
SURFACE, INK, INK2, MUTED, GRID, WARN = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1", "#b4232a"
REGIME_FILL = {REGIME_LEGACY: "#ffffff", REGIME_TRANSITION: "#fdf3e0", REGIME_CURRENT: "#e8f1fb"}


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _svg(width, height, body, title) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'font-family="Segoe UI, Helvetica, Arial, sans-serif" role="img" aria-label="{_esc(title)}">'
            f'<title>{_esc(title)}</title><rect width="{width}" height="{height}" fill="{SURFACE}"/>' + body + "</svg>")


def chart_accounting(res) -> str:
    ctx = res["ctx"]
    w, h, x0, top, ph = 880, 400, 60, 66, 250
    step = (w - x0 - 20) / len(ctx["months"])
    max_ovl = max(100 * ctx["W"][m]["overlap_h"] * 60 / (ctx["days"][m] * DAY_CAPACITY_MIN) for m in ctx["months"])
    body = [f'<text x="{x0}" y="22" font-size="15" fill="{INK}" font-weight="600">Time universe by month: unique tracked (excl. estimates), estimates / fill, Off-Device Life (% of capacity)</text>',
            f'<text x="{x0}" y="38" font-size="11" fill="{INK2}">Regimes measure differently — dashed line marks the regime change. Overlap not shown (≤ {max_ovl:.1f}% of capacity in any month).</text>']
    i_tr = ctx["months"].index("2026-08")
    xb = x0 + i_tr * step
    body.append(f'<line x1="{xb:.1f}" x2="{xb:.1f}" y1="{top - 14}" y2="{top + ph}" stroke="{INK2}" stroke-dasharray="4 3"/>'
                f'<text x="{xb - 4:.1f}" y="{top - 4}" font-size="10" fill="{INK2}" text-anchor="end">◀ LEGACY (manual tracking)</text>'
                f'<text x="{xb + 4:.1f}" y="{top - 4}" font-size="10" fill="{INK2}">TRANSITION · CURRENT ▶</text>')
    for t in (0, 25, 50, 75, 100):
        y = top + ph - ph * t / 100
        body.append(f'<line x1="{x0}" x2="{w - 20}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/><text x="{x0 - 6}" y="{y + 3:.1f}" font-size="10" fill="{INK2}" text-anchor="end">{t}%</text>')
    cols = (("Unique tracked excl. estimates", PALETTE[0]), ("Estimates / standard / vacation fill", PALETTE[3]), ("Off-Device Life (residual)", "#b9b8b2"))
    for i, m in enumerate(ctx["months"]):
        Wm = ctx["W"][m]
        cap = ctx["days"][m] * DAY_CAPACITY_MIN
        spec = ctx["M"][("derived", "ACCOUNTED_EXCL_ESTIMATES")][m]
        vals = (spec, Wm["accounted_h"] * 60 - spec, Wm["unresolved_h"] * 60)
        y = top + ph
        for (label, color), v in zip(cols, vals):
            hh = ph * v / cap
            y -= hh
            body.append(f'<rect x="{x0 + i * step + 4:.1f}" y="{y:.1f}" width="{step - 8:.1f}" height="{max(hh - 2, 0):.1f}" fill="{color}" rx="2">'
                        f'<title>{m} {_esc(label)}: {v / 60:.1f} h ({100 * v / cap:.1f}% of capacity)</title></rect>')
        body.append(f'<text x="{x0 + (i + 0.5) * step:.1f}" y="{top + ph + 14}" font-size="10" fill="{INK2}" text-anchor="middle">{m[2:]}</text>'
                    f'<text x="{x0 + (i + 0.5) * step:.1f}" y="{top + ph + 28}" font-size="10" fill="{INK}" text-anchor="middle">{Wm["coverage_pct"]:.0f}%</text>')
    body.append(f'<text x="{x0 - 6}" y="{top + ph + 28}" font-size="9" fill="{INK2}" text-anchor="end">cov.</text>')
    lx = x0
    for label, color in cols:
        body.append(f'<rect x="{lx}" y="{h - 30}" width="10" height="10" fill="{color}" rx="2"/><text x="{lx + 14}" y="{h - 21}" font-size="11" fill="{INK}">{_esc(label)}</text>')
        lx += 14 + 7 * len(label) + 20
    body.append(f'<text x="{x0}" y="{h - 8}" font-size="10" fill="{INK2}">cov. = unique tracked / capacity. Legacy estimates = household template; current = Apple Screen Time calibrated estimates.</text>')
    return _svg(w, h, "".join(body), "Time universe by month")


def chart_grouped(title, subtitle, rows, segs, vmax, note, fname_title, unit="min") -> str:
    """rows: [(label, [value or None per seg], flag_text)] ; grouped horizontal bars."""
    w, x0, bar = 900, 300, 9
    rowh = len(segs) * (bar + 2) + 14
    h = 90 + len(rows) * rowh + 50
    pw = w - x0 - 70
    body = [f'<text x="20" y="22" font-size="15" fill="{INK}" font-weight="600">{_esc(title)}</text>',
            f'<text x="20" y="40" font-size="11" fill="{WARN}">{_esc(subtitle)}</text>']
    ticks = 5
    for t in range(ticks + 1):
        v = vmax * t / ticks
        x = x0 + pw * t / ticks
        lab = ha.hm_fmt(v) if unit == "min" else f"{v:.0f}%"
        body.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="60" y2="{h - 50}" stroke="{GRID}"/><text x="{x:.1f}" y="56" font-size="9" fill="{INK2}" text-anchor="middle">{lab}</text>')
    for r, (label, vals, flag) in enumerate(rows):
        y = 66 + r * rowh
        body.append(f'<text x="{x0 - 8}" y="{y + 14}" font-size="11" fill="{INK}" text-anchor="end">{_esc(label)}</text>')
        if flag:
            body.append(f'<text x="{x0 - 8}" y="{y + 26}" font-size="9" fill="{WARN}" text-anchor="end">{_esc(flag)}</text>')
        for j, ((sname, color), v) in enumerate(zip(segs, vals)):
            yy = y + j * (bar + 2)
            if v is None:
                body.append(f'<text x="{x0 + 4}" y="{yy + bar - 1}" font-size="9" fill="{MUTED}">n/a — {_esc(sname)}</text>')
                continue
            ww = max(pw * min(v, vmax) / vmax, 1)
            lab = ha.hm_fmt(v) if unit == "min" else f"{v:.1f}%"
            body.append(f'<rect x="{x0}" y="{yy}" width="{ww:.1f}" height="{bar}" fill="{color}" rx="2"><title>{_esc(label)} — {_esc(sname)}: {lab}</title></rect>'
                        f'<text x="{x0 + ww + 4:.1f}" y="{yy + bar - 1}" font-size="9" fill="{INK2}">{lab}</text>')
    lx = x0
    for sname, color in segs:
        body.append(f'<rect x="{lx}" y="{h - 34}" width="10" height="10" fill="{color}" rx="2"/><text x="{lx + 14}" y="{h - 25}" font-size="11" fill="{INK}">{_esc(sname)}</text>')
        lx += 14 + 7 * len(sname) + 24
    body.append(f'<text x="20" y="{h - 10}" font-size="10" fill="{INK2}">{_esc(note)}</text>')
    return _svg(w, h, "".join(body), fname_title)


SEG_SERIES = (("Legacy baseline (per day)", PALETTE[0]), ("Transition Aug 2026", PALETTE[3]), ("Current Sep 2026", PALETTE[1]))


def chart_where(res) -> str:
    rows = []
    for r in res["B"]:
        def v(seg):
            x = r.get(f"{seg}_min_per_day", "n/a")
            return None if x in ("n/a", "", None) else float(x)
        flag = {"PARTIAL_LEGACY_EVIDENCE": "partial legacy evidence", "CURRENT_ONLY_NO_LEGACY_EVIDENCE": "no legacy evidence",
                "CAPTURE_DIFFERS": "capture differs by regime", "NOT_A_CURRENT_CATEGORY": "mapping gap — not a category"}.get(r["legacy_evidence"], "")
        rows.append((r["category"], [v(ha.SEG_BASE), v(ha.SEG_TRANS), v(ha.SEG_CUR)], flag))
    vmax = max(x for _, vals, _ in rows for x in vals if x is not None)
    vmax = math.ceil(vmax / 120) * 120
    return chart_grouped("Where did my time go? Current categories (min per calendar day)",
                         "Regimes use different capture methods — differences are not automatically behavioural.",
                         rows, SEG_SERIES, vmax, "Legacy values are averages over the baseline window. Unsplit / unmapped legacy rows are mapping gaps shown for transparency.",
                         "Where did my time go")


def chart_dims(res) -> str:
    rows = []
    for dim in ("goal", "energy", "domain"):
        for r in res["F_dims"]:
            if r["dimension"] != dim:
                continue
            vals = [None if r[f"{s}_pct_of_analytical"] in ("n/a", "") else float(r[f"{s}_pct_of_analytical"]) for s in (ha.SEG_BASE, ha.SEG_TRANS, ha.SEG_CUR)]
            if all((x or 0) == 0 for x in vals):
                continue
            rows.append((f"{dim.title()}: {r['value']}", vals, "legacy label not mappable" if r["value"] == hc.UNMAPPED_VALUE else ""))
    vmax = math.ceil(max(x for _, vals, _ in rows for x in vals if x is not None) / 10) * 10
    return chart_grouped("Goals, energy and domains by regime (% of analytical activity)",
                         "Regimes use different capture methods — compare shares with care.", rows, SEG_SERIES, vmax,
                         "Share of each segment's analytical activity (incl. Off-Device Life), as in the monthly Standard Report.",
                         "Goals energy domains by regime", unit="pct")


def chart_small_multiples(res, keys, title, subtitle, fname_title, ncols=3) -> str:
    ctx = res["ctx"]
    cw, chh, x0 = 280, 150, 40
    rows_n = math.ceil(len(keys) / ncols)
    w, h = 20 + ncols * cw, 60 + rows_n * (chh + 40)
    body = [f'<text x="20" y="22" font-size="15" fill="{INK}" font-weight="600">{_esc(title)}</text>',
            f'<text x="20" y="40" font-size="11" fill="{INK2}">{_esc(subtitle)}</text>']
    months = ctx["months"]
    for idx, (key, label, flag) in enumerate(keys):
        cx = 20 + (idx % ncols) * cw
        cy = 60 + (idx // ncols) * (chh + 40)
        pw, ph = cw - x0 - 20, chh - 30
        px0, ptop = cx + x0, cy + 18
        vals = [ha.per_day(ctx, key, m) for m in months]
        vmax = max([v for v in vals if v is not None] + [1]) * 1.15
        step = pw / len(months)
        body.append(f'<text x="{cx}" y="{cy + 10}" font-size="12" fill="{INK}" font-weight="600">{_esc(label)}</text>')
        if flag:
            body.append(f'<text x="{cx + cw - 30}" y="{cy + 10}" font-size="9" fill="{WARN}" text-anchor="end">{_esc(flag)}</text>')
        for i, m in enumerate(months):
            if ctx["regime"][m] != REGIME_LEGACY:
                body.append(f'<rect x="{px0 + i * step:.1f}" y="{ptop}" width="{step:.1f}" height="{ph}" fill="{REGIME_FILL[ctx["regime"][m]]}"/>')
        for t in (0, 0.5, 1):
            y = ptop + ph - ph * t
            body.append(f'<line x1="{px0}" x2="{px0 + pw}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}"/><text x="{px0 - 4}" y="{y + 3:.1f}" font-size="9" fill="{INK2}" text-anchor="end">{ha.hm_fmt(vmax * t)}</text>')
        st = ha.baseline_stats(ctx, key)
        if st["p25"] is not None:
            y1, y2 = ptop + ph - ph * st["p75"] / vmax, ptop + ph - ph * st["p25"] / vmax
            i0, i1 = months.index(ctx["baseline"][0]), months.index(ctx["baseline"][-1]) + 1
            body.append(f'<rect x="{px0 + i0 * step:.1f}" y="{y1:.1f}" width="{(i1 - i0) * step:.1f}" height="{max(y2 - y1, 1):.1f}" fill="{PALETTE[0]}" opacity="0.12">'
                        f'<title>Legacy baseline P25–P75: {ha.hm_fmt(st["p25"])}–{ha.hm_fmt(st["p75"])}/day</title></rect>')
        pts = []
        for i, (m, v) in enumerate(zip(months, vals)):
            if v is None or (m == "2026-08" and pts):
                if pts:
                    body.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{PALETTE[0]}" stroke-width="2"/>')
                pts = []
                if v is None:
                    continue
            x = px0 + (i + 0.5) * step
            y = ptop + ph - ph * v / vmax
            pts.append(f"{x:.1f},{y:.1f}")
            color = PALETTE[0] if ctx["regime"][m] == REGIME_LEGACY else PALETTE[1]
            body.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{color}" stroke="{SURFACE}" stroke-width="1.5"><title>{_esc(label)} {m}: {ha.hm_fmt(v)}/day</title></circle>')
        if pts:
            body.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{PALETTE[1]}" stroke-width="2"/>')
        for i in (0, months.index("2026-01"), len(months) - 1):
            body.append(f'<text x="{px0 + (i + 0.5) * step:.1f}" y="{ptop + ph + 12}" font-size="9" fill="{INK2}" text-anchor="middle">{months[i][2:]}</text>')
    body.append(f'<text x="20" y="{h - 8}" font-size="10" fill="{INK2}">Blue = legacy manual months; orange = transition (Aug 2026) / current (Sep 2026); line broken at the regime change; gaps = no comparable legacy evidence. Band = legacy baseline P25–P75.</text>')
    return _svg(w, h + 6, "".join(body), fname_title)


def chart_budget(res) -> str:
    rows = [b for b in res["J"] if b["range_status"] != "NOT_BUDGETABLE"]
    w, x0, rowh = 920, 390, 26
    h = 90 + len(rows) * rowh + 50
    vmax = 600
    pw = w - x0 - 60
    X = lambda v: x0 + pw * min(float(v), vmax) / vmax
    body = [f'<text x="20" y="22" font-size="15" fill="{INK}" font-weight="600">Historical reference for the future budget (current taxonomy, min per day)</text>',
            f'<text x="20" y="40" font-size="11" fill="{INK2}">Bar = planning range (only where data support it) · grey whisker = legacy baseline min–max · ● legacy median · ◆ current Sep 2026</text>']
    for t in range(0, vmax + 1, 120):
        x = x0 + pw * t / vmax
        body.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="60" y2="{h - 50}" stroke="{GRID}"/><text x="{x:.1f}" y="56" font-size="9" fill="{INK2}" text-anchor="middle">{ha.hm_fmt(t)}</text>')
    for r, b in enumerate(rows):
        y = 66 + r * rowh
        sub = b["is_sub_line"] != "N"
        label = ("↳ " if sub else "") + (b["budget_line"] if sub else hc.BUDGET_LINE_LABEL[b["budget_line"]])
        if len(label) > 58:
            label = label[:56] + "…"
        body.append(f'<text x="{x0 - 8}" y="{y + 12}" font-size="10.5" fill="{INK2 if sub else INK}" text-anchor="end">{_esc(label)}</text>')
        if b["baseline_min_min_per_day"] not in ("", None):
            body.append(f'<line x1="{X(b["baseline_min_min_per_day"]):.1f}" x2="{X(b["baseline_max_min_per_day"]):.1f}" y1="{y + 8}" y2="{y + 8}" stroke="#b9b8b2" stroke-width="2"/>')
        if b["suggested_low_min_per_day"] != "":
            x1, x2 = X(b["suggested_low_min_per_day"]), X(b["suggested_high_min_per_day"])
            body.append(f'<rect x="{x1:.1f}" y="{y + 2}" width="{max(x2 - x1, 2):.1f}" height="12" fill="{PALETTE[0]}" opacity="0.35" rx="2">'
                        f'<title>{_esc(label)}: {b["suggested_low_min_per_day"]}–{b["suggested_high_min_per_day"]} min/day ({b["range_status"]}, {b["confidence"]})</title></rect>')
        else:
            body.append(f'<text x="{w - 56}" y="{y + 12}" font-size="9" fill="{WARN}" text-anchor="end">no range — {b["range_status"].replace("_", " ").lower()}</text>')
        if b["baseline_median_min_per_day"] not in ("", None):
            body.append(f'<circle cx="{X(b["baseline_median_min_per_day"]):.1f}" cy="{y + 8}" r="4" fill="{PALETTE[0]}" stroke="{SURFACE}" stroke-width="1.5"><title>Legacy median {b["baseline_median_min_per_day"]} min/day</title></circle>')
        if b["current_2026_09_min_per_day"] not in ("", None):
            cxp = X(b["current_2026_09_min_per_day"])
            body.append(f'<path d="M{cxp:.1f},{y + 3} l5,5 l-5,5 l-5,-5 z" fill="{PALETTE[1]}" stroke="{SURFACE}" stroke-width="1"><title>Current Sep 2026 {b["current_2026_09_min_per_day"]} min/day</title></path>')
    body.append(f'<text x="20" y="{h - 28}" font-size="10" fill="{INK2}">Ranges rounded to {ha.ROUND_TO_MIN} min. ↳ = current category inside its line (information only, not additive). Values above 10 h/day are clipped.</text>')
    body.append(f'<text x="20" y="{h - 12}" font-size="10" fill="{INK2}">Template reference only — not an approved budget; no target is set.</text>')
    return _svg(w, h, "".join(body), "Budget reference")


def build_charts(res) -> dict[str, str]:
    ctx = res["ctx"]
    flag = {"PARTIAL_LEGACY_EVIDENCE": "partial legacy", "CURRENT_ONLY_NO_LEGACY_EVIDENCE": "current only", "CAPTURE_DIFFERS": "capture differs"}
    cat_keys = [(("category", c), c, flag.get(ctx["cat_evidence"][c], "")) for c in ctx["taxonomy"].categories]
    cat_keys += [(k, k[1].replace("Unsplit legacy: ", "Unsplit: "), "mapping gap") for k in sorted(k for k in ctx["M"] if k[0] == "unsplit")]
    return {
        "01_accounting_coverage_monthly.svg": chart_accounting(res),
        "02_where_did_my_time_go.svg": chart_where(res),
        "03_current_categories_monthly.svg": chart_small_multiples(res, cat_keys, "Current categories by month (min per calendar day)",
                                                                   "Current taxonomy. Partial = part of the legacy time sits in unsplit legacy records (shown as their own panels).",
                                                                   "Current categories by month", ncols=3),
        "04_domain_energy_goal_by_regime.svg": chart_dims(res),
        "05_devices_monthly.svg": chart_small_multiples(res, [
            (("device", "PHONE"), "Phone", ""), (("device", "COMPUTERS_TOTAL"), "Computers total", ""),
            (("device", "LAPTOP"), "Laptop", "n/a Aug 2026"), (("device", "DESKTOP"), "Desktop", "n/a Aug 2026"),
            (("device", "TV"), "TV", ""), (("derived", "OFF_DEVICE_SPECIFIED"), "Manual off-device (excl. estimates)", "")],
            "Device classes by month (min per calendar day)",
            "Computers total = laptop + desktop (never added twice). Phone capture differs by regime.", "Devices by month"),
        "06_anchor_activities_monthly.svg": chart_small_multiples(res, [(("anchor", a), hc.ANCHOR_LABEL[a].replace("Information & Reading", "Info & Reading"), "") for a in hm.ANCHOR_ORDER],
                                                                  "Exact-equivalent activities by month (current category › subcategory, min per day)",
                                                                  "Comparable with caveats across regimes.", "Anchor activities by month"),
        "07_budget_reference.svg": chart_budget(res),
    }
