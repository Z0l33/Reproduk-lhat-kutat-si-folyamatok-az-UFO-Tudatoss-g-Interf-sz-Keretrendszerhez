"""Column allowlist between data loading and analysis. Includes the negative tests: an analysis that
references a denied column must stop with an error, and a denied column must never reach the analysis
layer, not even through an accidental config reference."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from ufo_pipeline import pipeline
from ufo_pipeline.access import (ColumnAccessError, load_csv_allowlisted, resolve_access,
                                 validate_policy)
from ufo_pipeline.cli import main
from ufo_pipeline.pipeline import run_analysis
from ufo_pipeline.provenance import write_json
from tests.helpers import add_column, make_logic_fixture

CANARY = "GEHEIM-KANARIENVOGEL-4711"
GEP = "gep-ufo-uap-2026-10"
REPO = Path(__file__).resolve().parents[1]


def gep_like(**kw):
    """A fixture shaped like the real dataset: denied free-text and place columns carrying a canary."""
    fx = make_logic_fixture("null", dataset_id=GEP, policy="repo", **kw)
    for name in ("Sachverhalt", "Sichtungsort", "PLZ", "PLZ_unvollstaendig"):
        add_column(fx["paths"], name, lambda i, n=name: f"{CANARY}-{n}-{i}")
    return fx


def run(fx):
    return run_analysis(fx["config"], base_dir=fx["root"])


class DeniedColumnNeverReachesAnalysis(unittest.TestCase):
    def test_canary_columns_are_not_loaded_and_not_in_report(self):
        fx = gep_like()
        seen = []
        real_validate = pipeline.validate_schema

        def spy(columns, rows, spec):
            seen.append((list(columns), [set(r) for r in rows[:3]]))
            return real_validate(columns, rows, spec)

        with mock.patch.object(pipeline, "validate_schema", spy):
            rep = run(fx)
        cols, row_keys = seen[0]
        denied = {"Sachverhalt", "Sichtungsort", "PLZ", "PLZ_unvollstaendig"}
        self.assertFalse(denied & set(cols))
        for keys in row_keys:
            self.assertFalse(denied & keys)
        self.assertNotIn(CANARY, json.dumps(rep))
        self.assertEqual(rep["data_access"]["columns_not_loaded"], 4)
        self.assertEqual(rep["data_access"]["policy_denied_columns_present_in_file"],
                         ["PLZ", "PLZ_unvollstaendig", "Sachverhalt", "Sichtungsort"])

    def test_leakage_detection_also_gets_only_allowed_columns(self):
        fx = gep_like()
        captured = {}
        real = pipeline.detect_leakage

        def spy(rows, **kw):
            captured["keys"] = set().union(*[set(r) for r in rows[:5]])
            return real(rows, **kw)

        with mock.patch.object(pipeline, "detect_leakage", spy):
            run(fx)
        self.assertFalse({"Sachverhalt", "Sichtungsort", "PLZ", "PLZ_unvollstaendig"} & captured["keys"])


class NegativeTestsDeniedReferenceStopsTheAnalysis(unittest.TestCase):
    """The data file must not even be opened when the config is refused."""

    def refuse(self, mutate, expect):
        fx = gep_like()
        mutate(fx["config"])
        with mock.patch.object(pipeline, "load_csv_allowlisted",
                               side_effect=AssertionError("data file was read")) as loader:
            with self.assertRaises(ColumnAccessError) as ctx:
                run(fx)
        loader.assert_not_called()
        self.assertIn(expect, str(ctx.exception))
        self.assertNotIn(CANARY, str(ctx.exception))

    def test_denied_column_as_feature(self):
        def m(c):
            c["leakage"]["feature_columns"].append("Sachverhalt")
            c["data_access"]["allowed_columns"].append("Sachverhalt")
        self.refuse(m, "denied")

    def test_denied_column_as_outcome_without_allowlisting_it(self):
        def m(c):
            c["analysis"]["outcome_column"] = "PLZ"
        self.refuse(m, "denied column 'PLZ'")

    def test_denied_column_as_group_column(self):
        def m(c):
            c["analysis"]["group_column"] = "Sichtungsort"
        self.refuse(m, "denied column 'Sichtungsort'")

    def test_denied_column_in_validation_spec(self):
        def m(c):
            c["validation"]["columns"]["PLZ_unvollstaendig"] = {"type": "str"}
        self.refuse(m, "denied column 'PLZ_unvollstaendig'")

    def test_denied_column_in_allowlist(self):
        def m(c):
            c["data_access"]["allowed_columns"].append("Sachverhalt")
        self.refuse(m, "cannot be allowed")

    def test_case_and_whitespace_variants_are_still_denied(self):
        def m(c):
            c["leakage"]["feature_columns"] = ["  sachverhalt "]
            c["data_access"]["allowed_columns"].append("  sachverhalt ")
        self.refuse(m, "denied")

    def test_denied_name_hidden_in_unexpected_config_place(self):
        def m(c):
            c["analysis"]["note"] = "Sachverhalt"
        self.refuse(m, "mentions denied column")

    def test_unknown_top_level_section_is_refused(self):
        def m(c):
            c["extra_features"] = ["Sachverhalt"]
        self.refuse(m, "unknown config section")

    def test_referenced_column_missing_from_allowlist(self):
        def m(c):
            c["data_access"]["allowed_columns"].remove("covariate")
        self.refuse(m, "not in the allowlist")

    def test_policy_location_cannot_be_overridden(self):
        def m(c):
            c["data_access"]["policy_path"] = "/tmp/permissive.json"
        self.refuse(m, "cannot be overridden")

    def test_global_pattern_denies_even_unlisted_names(self):
        def m(c):
            c["leakage"]["feature_columns"].append("reporter_email")
            c["data_access"]["allowed_columns"].append("reporter_email")
        self.refuse(m, "denied")


class FailClosed(unittest.TestCase):
    def test_real_dataset_without_allowlist_is_refused(self):
        fx = make_logic_fixture("null", dataset_id="x-real", explicit_allowlist=False)
        with self.assertRaises(ColumnAccessError) as ctx:
            run(fx)
        self.assertIn("allowed_columns is required", str(ctx.exception))

    def test_real_dataset_without_policy_file_is_refused(self):
        fx = make_logic_fixture("null", dataset_id="x-real", policy=None)
        with self.assertRaises(ColumnAccessError) as ctx:
            run(fx)
        self.assertIn("policies/sensitive_columns.json", str(ctx.exception))

    def test_real_dataset_without_policy_entry_is_refused(self):
        fx = make_logic_fixture("null", dataset_id="x-real",
                                policy={"schema_version": 1, "datasets": {"other": {"denied_columns": []}}})
        with self.assertRaises(ColumnAccessError) as ctx:
            run(fx)
        self.assertIn("explicit access decision", str(ctx.exception))

    def test_invalid_policy_is_refused(self):
        fx = make_logic_fixture("null", dataset_id="x-real", policy={"schema_version": 1, "datasets": []})
        with self.assertRaises(ColumnAccessError):
            run(fx)

    def test_allowed_column_missing_in_file_is_refused(self):
        fx = make_logic_fixture("null", dataset_id="x-real")
        fx["config"]["data_access"]["allowed_columns"].append("not_in_file")
        with self.assertRaises(ColumnAccessError):
            run(fx)

    def test_synthetic_default_allowlist_still_works(self):
        from ufo_pipeline.synthetic import write_dataset
        root = Path(tempfile.mkdtemp())
        paths = write_dataset("null", root, n_per_group=40, seed=1)
        from tests.helpers import ANALYSIS, VALIDATION
        cfg = {"analysis_id": "syn", "dataset_provenance": paths["provenance"].name,
               "data_file": paths["csv"].name, "validation": copy.deepcopy(VALIDATION),
               "analysis": copy.deepcopy(ANALYSIS)}
        rep = run_analysis(cfg, base_dir=root)
        self.assertEqual(rep["data_access"]["mode"], "synthetic_default_allowlist")


class CliAndPolicyFile(unittest.TestCase):
    def test_cli_exits_nonzero_and_writes_no_report_for_denied_reference(self):
        fx = gep_like()
        fx["config"]["analysis"]["outcome_column"] = "PLZ"
        cfg_path = fx["root"] / "cfg.json"
        write_json(cfg_path, fx["config"])
        out = fx["root"] / "out"
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(["analyze", "--config", str(cfg_path), "--out", str(out),
                         "--base-dir", str(fx["root"])])
        self.assertEqual(code, 2)
        self.assertIn("COLUMN ACCESS DENIED", err.getvalue())
        self.assertFalse(out.exists())
        self.assertNotIn(CANARY, err.getvalue())

    def test_repo_policy_is_valid_and_denies_gep_sensitive_columns(self):
        policy = json.loads((REPO / "policies" / "sensitive_columns.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_policy(policy), [])
        denied = set(policy["datasets"][GEP]["denied_columns"])
        self.assertEqual(denied, {"Sachverhalt", "Sichtungsort", "PLZ", "PLZ_unvollstaendig"})

    def test_loader_drops_unlisted_columns_at_parse_time(self):
        d = Path(tempfile.mkdtemp())
        (d / "x.csv").write_text(f"a,secret,b\n1,{CANARY},2\n3,{CANARY},4\n", encoding="utf-8")
        cols, rows, info = load_csv_allowlisted(d / "x.csv", ["a", "b"])
        self.assertEqual(cols, ["a", "b"])
        self.assertNotIn(CANARY, json.dumps(rows))
        self.assertEqual(info["columns_not_loaded"], 1)


if __name__ == "__main__":
    unittest.main()
