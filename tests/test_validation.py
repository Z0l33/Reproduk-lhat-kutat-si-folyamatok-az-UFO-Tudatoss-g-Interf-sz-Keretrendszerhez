import unittest

from ufo_pipeline.validation import detect_leakage, validate_schema

COLS = ["subject_id", "group", "outcome", "x"]
SPEC = {"columns": {"subject_id": {"type": "str"}, "group": {"type": "str"},
                    "outcome": {"type": "float", "min": -10, "max": 10},
                    "x": {"type": "float", "required": False}},
        "id_column": "subject_id", "unique_id": True, "min_rows": 2}


def row(i, g, o, x="0.1", **extra):
    return {"subject_id": f"S{i}", "group": g, "outcome": str(o), "x": x, **extra}


class SchemaTests(unittest.TestCase):
    def test_valid(self):
        rep = validate_schema(COLS, [row(1, "A", 1), row(2, "B", 2)], SPEC)
        self.assertTrue(rep["valid"], rep["errors"])

    def test_missing_column(self):
        rep = validate_schema(["subject_id"], [row(1, "A", 1)], SPEC)
        self.assertFalse(rep["valid"])
        self.assertIn("missing_column:outcome", rep["errors"])

    def test_bad_type_missing_range_and_duplicates(self):
        rows = [row(1, "A", "abc"), row(2, "B", ""), row(3, "B", 99), row(3, "B", 1)]
        errors = " ".join(validate_schema(COLS, rows, SPEC)["errors"])
        for token in ("bad_type:outcome", "missing_value:outcome", "above_max:outcome", "duplicate_ids"):
            self.assertIn(token, errors)

    def test_nan_and_inf_are_bad_type(self):
        rep = validate_schema(COLS, [row(1, "A", "nan"), row(2, "B", "inf")], SPEC)
        self.assertIn("bad_type:outcome", " ".join(rep["errors"]))

    def test_optional_column_may_be_empty(self):
        rep = validate_schema(COLS, [row(1, "A", 1, x=""), row(2, "B", 2, x="")], SPEC)
        self.assertTrue(rep["valid"])

    def test_too_few_rows(self):
        self.assertFalse(validate_schema(COLS, [row(1, "A", 1)], SPEC)["valid"])


class LeakageTests(unittest.TestCase):
    kw = dict(group_column="group", outcome_column="outcome", id_column="subject_id")

    def test_clean_data(self):
        rows = [row(i, "A" if i % 2 else "B", i * 0.37 % 5, x=str((i * 7919) % 13)) for i in range(40)]
        rep = detect_leakage(rows, feature_columns=["x"], **self.kw)
        self.assertFalse(rep["leakage_detected"], rep["findings"])

    def test_subject_in_multiple_groups(self):
        rows = [row(1, "A", 1), row(1, "B", 2), row(2, "B", 3)]
        types = [f["type"] for f in detect_leakage(rows, **self.kw)["findings"]]
        self.assertIn("subject_in_multiple_groups", types)

    def test_proxy_feature(self):
        rows = [row(i, "A", i, x=str(2 * i + 1)) for i in range(20)]
        rep = detect_leakage(rows, feature_columns=["x"], **self.kw)
        self.assertEqual(rep["findings"][0]["type"], "proxy_feature")

    def test_train_test_overlap_and_duplicates(self):
        rows = [row(1, "A", 1, split="train"), row(1, "A", 1, split="test"), row(2, "B", 2, split="test")]
        rep = detect_leakage(rows, split_column="split", feature_columns=["x"], **self.kw)
        types = {f["type"] for f in rep["findings"]}
        self.assertTrue({"subject_in_train_and_test", "identical_rows_in_train_and_test"} <= types)

    def test_temporal_leak(self):
        rows = [row(1, "A", 1, split="train", t="2020-01-10"), row(2, "A", 2, split="test", t="2020-01-05")]
        rep = detect_leakage(rows, split_column="split", time_column="t", **self.kw)
        self.assertIn("temporal_overlap_test_not_after_train", [f["type"] for f in rep["findings"]])

    def test_temporal_ok(self):
        rows = [row(1, "A", 1, split="train", t="2020-01-01"), row(2, "A", 2, split="test", t="2020-02-01")]
        rep = detect_leakage(rows, split_column="split", time_column="t", **self.kw)
        self.assertFalse(rep["leakage_detected"], rep["findings"])


if __name__ == "__main__":
    unittest.main()
