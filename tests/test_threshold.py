"""Threshold analysis regression tests (draft, to fold into tests/)."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from voucher_cutoff.domain import explore
from voucher_cutoff.service import Workbench
from voucher_cutoff.threshold import alternatives, estimate, prepare

ROOT = Path(__file__).resolve().parents[1]


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "fixtures" / "data.json").read_text())

    def test_result_serializes_without_nan(self):
        result = estimate(self.data, {"year": "2026", "window": 90})
        json.dumps(result, ensure_ascii=False, allow_nan=False)
        self.assertTrue(result["estimates"])
        self.assertTrue(result["rows"])

    def test_vintage_age_used_instead_of_latest_correction(self):
        prepared = prepare(self.data, {"year": "2026", "window": 90})
        record = next(row for row in prepared["records"] if row["application"] == "2026-209")
        self.assertEqual(record["age_days"], 1003)
        self.assertEqual(record["latest_age_days"], 988)
        self.assertFalse(record["eligible"])  # 1003 >= 1000，资料截面时点不具资格
        # 之后的订正会使该条变为具资格；分析必须保留截面状态
        self.assertTrue(record["latest_age_days"] < 1000)
        self.assertEqual(prepared["exclusions"]["vintage_age_differs_from_latest"], 22)

    def test_unmatured_records_excluded_not_zeroed(self):
        prepared = prepare(self.data, {"year": "2026", "window": 90})
        immature = [row for row in prepared["in_window"] if row["outcome"] is None]
        self.assertTrue(immature)
        self.assertTrue(all(not row["included"] for row in immature))
        self.assertTrue(all(row["observed_days"] < 60 for row in immature))

    def test_rule_equality_and_window_bounds(self):
        prepared = prepare(self.data, {"year": "2025", "window": 90})
        boundary = [row for row in prepared["records"] if row["distance"] == 0]
        self.assertTrue(boundary)
        self.assertFalse(any(row["eligible"] for row in boundary))
        with self.assertRaises(ValueError):
            prepare(self.data, {"year": "2025", "window": 90, "donut": 90})
        with self.assertRaises(ValueError):
            prepare(self.data, {"year": "2025", "window": 90, "followup": 30})
        with self.assertRaises(ValueError):
            prepare(self.data, {"year": "2025", "window": 90, "kernel": "epanechnikov"})

    def test_insufficient_support_does_not_emit_effect(self):
        result = estimate(self.data, {"year": "2026", "window": 5})
        self.assertIn(result["status"], {"insufficient_support", "no_support"})
        self.assertTrue(all(item["estimate"] is None for item in result["estimates"]))

    def test_weak_first_stage_reports_itt_but_not_late(self):
        result = estimate(self.data, {"year": "2025", "window": 90})
        self.assertEqual(result["status"], "weak_first_stage")
        late = next(item for item in result["estimates"] if item["id"] == "late_voucher")
        self.assertIsNone(late["estimate"])
        itt = next(item for item in result["estimates"] if item["id"] == "itt_eligibility")
        self.assertIsNotNone(itt["estimate"])

    def test_identified_year_labels_uncertainty(self):
        result = estimate(self.data, {"year": "2026", "window": 90})
        self.assertEqual(result["status"], "identified")
        late = next(item for item in result["estimates"] if item["id"] == "late_voucher")
        self.assertIsNotNone(late["estimate"])
        self.assertIn(late["direction"], {"positive", "negative", "undetermined"})
        self.assertTrue(result["notes"])

    def test_density_discontinuity_suppresses_effects(self):
        data = deepcopy(self.data)
        policy = next(row for row in data["policies"] if row["year"] == "2026")
        for index in range(60):
            age = policy["cutoff_days"] + index % 5
            application = "pile-" + str(index)
            data["applications"].append({
                "application": application, "year": "2026", "applied_day": 150,
                "age_days": age, "voucher_used": False, "visit_60d": 0,
                "observed_days": 70, "income_band": 1, "center": "C0", "recorded_at": 200,
            })
            data["age_history"].append({"change": application + "-0", "application": application,
                                        "visible_at": 10, "age_days": age})
        result = estimate(data, {"year": "2026", "window": 90})
        self.assertEqual(result["status"], "density_discontinuity")
        self.assertEqual(result["diagnostics"]["density"]["p_value"], 0.0)
        effect = next(item for item in result["estimates"] if item["id"] == "late_voucher")
        self.assertIsNone(effect["estimate"])
        self.assertTrue(any("仍可回答" in note for note in result["notes"]))

    def test_zero_first_stage_does_not_become_effect(self):
        data = deepcopy(self.data)
        for row in data["applications"]:
            if row["year"] == "2026":
                row["voucher_used"] = False
        result = estimate(data, {"year": "2026", "window": 90})
        first = next(item for item in result["estimates"] if item["id"] == "first_stage_voucher")
        self.assertLess(abs(first["estimate"]), 0.05)
        self.assertIsNone(result["diagnostics"]["late"]["estimate"])
        self.assertIn("核销", result["conclusion"])

    def test_comparison_lists_alternatives(self):
        variants = alternatives(self.data, {"year": "2026", "window": 90})
        names = [item["name"] for item in variants]
        self.assertIn("基准", names)
        self.assertGreaterEqual(len(variants), 5)
        self.assertTrue(all("status" in item for item in variants))

    def test_original_description_unchanged(self):
        before = deepcopy(self.data)
        estimate(self.data, {"year": "2026", "window": 90})
        self.assertEqual(before, self.data)
        self.assertTrue(explore(self.data, {"year": "2026", "window": "90"})["rows"])

    def test_boundary_equality_rule_respected(self):
        data = deepcopy(self.data)
        policy = next(row for row in data["policies"] if row["year"] == "2026")
        policy["eligibility"] = "age_at_application <= cutoff_days"
        prepared = prepare(data, {"year": "2026", "window": 90})
        boundary = [row for row in prepared["records"] if row["distance"] == 0]
        self.assertTrue(boundary)
        self.assertTrue(all(row["eligible"] for row in boundary))
        self.assertEqual(prepared["rule"]["shift"], -0.5)


class ThresholdServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workbench = Workbench(ROOT, self.temporary.name)

    def test_threshold_result_is_saved_and_reopened_frozen(self):
        body = {"title": "门槛复核", "analysis": "threshold",
                "selection": {"year": "2026", "window": 120}}
        saved = self.workbench.save(body)
        self.assertEqual(saved["result"]["analysis"], "threshold")
        reopened = Workbench(ROOT, self.temporary.name).store.notebook(saved["id"])
        self.assertEqual(saved, reopened)

    def test_comparison_result_is_saved(self):
        saved = self.workbench.save({"title": "规格对照", "analysis": "comparison",
                                     "selection": {"year": "2025", "window": 90}})
        self.assertEqual(saved["result"]["analysis"], "comparison")
        self.assertTrue(saved["result"]["comparison"]["scenarios"])

    def test_bad_threshold_selection_rejected(self):
        with self.assertRaises(ValueError):
            self.workbench.threshold({"selection": {"year": "2026", "window": 90, "donut": 90}})

    def test_defaults_are_resolved_into_saved_selection(self):
        result = self.workbench.threshold({"selection": {"year": "2026"}})
        self.assertEqual(result["selection"]["window"], 90)
        self.assertEqual(result["selection"]["followup"], 60)


if __name__ == "__main__":
    unittest.main()
