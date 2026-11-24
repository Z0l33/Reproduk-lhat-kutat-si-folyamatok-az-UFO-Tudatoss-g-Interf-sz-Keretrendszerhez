import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import VALIDATION, ANALYSIS, make_logic_fixture
from ufo_pipeline.cli import main
from ufo_pipeline.pipeline import evaluate_and_reassess, render_markdown, run_analysis, write_report
from ufo_pipeline.provenance import IntegrityError, load_json, write_json
from ufo_pipeline.synthetic import SCENARIOS, write_dataset


def run(fx):
    return run_analysis(fx["config"], base_dir=fx["root"])


class SyntheticAlwaysNotEvidence(unittest.TestCase):
    """Genuinely synthetic datasets (provenance synthetic=true) can never count as evidence."""

    def cfg(self, root, scenario):
        return {"analysis_id": scenario, "dataset_provenance": f"synthetic_{scenario}.provenance.json",
                "data_file": f"synthetic_{scenario}.csv", "validation": VALIDATION,
                "leakage": {"feature_columns": ["covariate"]},
                "analysis": {**ANALYSIS, "n_permutations": 500, "n_bootstrap": 200}}

    def test_every_scenario(self):
        for scenario in SCENARIOS:
            root = Path(tempfile.mkdtemp())
            write_dataset(scenario, root)
            rep = run_analysis(self.cfg(root, scenario), base_dir=root)
            self.assertFalse(rep["assessment"]["counts_as_positive_evidence"], scenario)
            expected = "invalid_leakage" if scenario in ("leaky_proxy", "id_overlap") else "synthetic_not_evidence"
            self.assertEqual(rep["assessment"]["status"], expected, scenario)
            if expected == "invalid_leakage":
                self.assertIsNone(rep["analysis"])

    def test_tampered_data_raises(self):
        root = Path(tempfile.mkdtemp())
        paths = write_dataset("null", root)
        with open(paths["csv"], "a", encoding="utf-8") as fh:
            fh.write("S99999,A,0.5,0.1\n")
        with self.assertRaises(IntegrityError):
            run_analysis(self.cfg(root, "null"), base_dir=root)


class ConfirmatoryLogic(unittest.TestCase):
    """Uses re-labelled synthetic FIXTURES to exercise the logic. No real data, no evidence."""

    def test_true_effect_awaits_replication(self):
        rep = run(make_logic_fixture("true_effect", dataset_id="fx-a"))
        self.assertEqual(rep["assessment"]["mode"], "confirmatory")
        self.assertEqual(rep["assessment"]["status"], "awaiting_independent_replication")
        self.assertFalse(rep["assessment"]["counts_as_positive_evidence"])

    def test_true_effect_unreplicated_when_allowed(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-a", analysis_over={"require_replication": False},
                                prereg_over={"require_replication": False})
        rep = run(fx)
        self.assertEqual(rep["assessment"]["status"], "confirmatory_positive_unreplicated")
        self.assertTrue(rep["assessment"]["counts_as_positive_evidence"])

    def test_null_is_not_positive(self):
        rep = run(make_logic_fixture("null", dataset_id="fx-n"))
        self.assertEqual(rep["assessment"]["mode"], "confirmatory")
        self.assertIn(rep["assessment"]["status"], ("confirmatory_null_equivalence", "confirmatory_inconclusive"))
        self.assertFalse(rep["assessment"]["counts_as_positive_evidence"])

    def test_effect_below_sesoi_is_not_positive(self):
        rep = run(make_logic_fixture("below_sesoi", dataset_id="fx-b"))
        self.assertEqual(rep["assessment"]["status"], "confirmatory_null_equivalence")
        self.assertFalse(rep["assessment"]["counts_as_positive_evidence"])

    def test_leakage_is_not_positive(self):
        rep = run(make_logic_fixture("leaky_proxy", dataset_id="fx-l"))
        self.assertEqual(rep["assessment"]["status"], "invalid_leakage")
        self.assertIsNone(rep["analysis"])

    def test_unverified_dataset_is_not_analysed(self):
        rep = run(make_logic_fixture("true_effect", dataset_id="fx-u", status="unpinned", file_status="unpinned"))
        self.assertEqual(rep["assessment"]["status"], "unverified_data")
        self.assertIsNone(rep["analysis"])

    def test_no_prereg_is_exploratory(self):
        rep = run(make_logic_fixture("true_effect", dataset_id="fx-e", prereg=False))
        self.assertEqual(rep["assessment"]["status"], "exploratory_hypothesis_generating")
        self.assertFalse(rep["assessment"]["counts_as_positive_evidence"])

    def test_deviation_makes_it_exploratory(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-d", analysis_over={"sesoi_g": 0.1})
        rep = run(fx)
        self.assertEqual(rep["assessment"]["mode"], "exploratory")
        self.assertTrue(any("deviation" in p for p in rep["preregistration"]["problems"]))

    def test_late_preregistration_is_exploratory(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-t", prereg_over={"registered_at": "2031-06-01T00:00:00+00:00"})
        self.assertEqual(run(fx)["assessment"]["mode"], "exploratory")

    def test_placeholder_registry_is_exploratory(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-p", prereg_over={"registry_id": "REPLACE_ME"})
        self.assertEqual(run(fx)["assessment"]["mode"], "exploratory")

    def test_report_files_and_banner(self):
        rep = run(make_logic_fixture("null", dataset_id="fx-m"))
        out = Path(tempfile.mkdtemp())
        paths = write_report(rep, out)
        self.assertEqual(load_json(paths["json"])["assessment"], rep["assessment"])
        self.assertIn("NO", render_markdown(rep))


class ReplicationEndToEnd(unittest.TestCase):
    def pair(self, replication_scenario, **rep_kw):
        orig = run(make_logic_fixture("true_effect", dataset_id="fx-orig", seed=1))
        fx = make_logic_fixture(replication_scenario, dataset_id="fx-rep", seed=2, record_id="00",
                                prereg_over={"require_replication": False},
                                analysis_over={"require_replication": False},
                                extra_prereg={"replicates_prereg_sha256": orig["preregistration"]["sha256"]}, **rep_kw)
        return orig, run(fx)

    def test_successful_replication_counts(self):
        orig, rep = self.pair("true_effect")
        out = evaluate_and_reassess(orig, rep)
        self.assertEqual(out["replication"]["outcome"], "replicated")
        self.assertTrue(out["original_assessment_after"]["counts_as_positive_evidence"])
        self.assertEqual(out["original_assessment_after"]["status"], "confirmatory_positive_replicated")

    def test_failed_replication_never_positive(self):
        orig, rep = self.pair("null")
        out = evaluate_and_reassess(orig, rep)
        self.assertEqual(out["replication"]["outcome"], "failed")
        self.assertEqual(out["original_assessment_after"]["status"], "replication_failed")
        self.assertFalse(out["original_assessment_after"]["counts_as_positive_evidence"])

    def test_leaky_replication_is_invalid_not_a_win(self):
        orig, rep = self.pair("leaky_proxy")
        out = evaluate_and_reassess(orig, rep)
        self.assertEqual(out["replication"]["outcome"], "invalid")
        self.assertFalse(out["original_assessment_after"]["counts_as_positive_evidence"])

    def test_rerunning_same_data_is_not_replication(self):
        orig = run(make_logic_fixture("true_effect", dataset_id="fx-orig", seed=1))
        out = evaluate_and_reassess(orig, orig)
        self.assertEqual(out["replication"]["outcome"], "invalid")
        self.assertFalse(out["original_assessment_after"]["counts_as_positive_evidence"])


class FetchExitCodeTests(unittest.TestCase):
    """`fetch` is exercised with a patched retrieve_dataset: no network is used."""

    def run_fetch(self, status, *extra):
        from unittest import mock
        root = Path(tempfile.mkdtemp())
        manifest = root / "m.json"
        manifest.write_text(json.dumps({"schema_version": 1, "datasets": [{
            "dataset_id": "fake-set", "record_id": "0", "doi": "10.5281/zenodo.0",
            "files": [{"name": "data.csv"}]}]}))
        fake = {"dataset_id": "fake-set", "status": status, "files": [], "errors": []}
        with mock.patch("ufo_pipeline.cli.retrieve_dataset", return_value=fake) as patched:
            code = main(["fetch", "--manifest", str(manifest), "--dest", str(root / "raw"),
                         "--provenance-dir", str(root / "prov"), *extra])
        self.assertEqual(patched.call_count, 1)
        return code

    def test_verified_exits_zero(self):
        self.assertEqual(self.run_fetch("verified"), 0)

    def test_unpinned_exits_nonzero_by_default(self):
        self.assertEqual(self.run_fetch("unpinned"), 1)

    def test_unpinned_allowed_only_with_flag(self):
        self.assertEqual(self.run_fetch("unpinned", "--allow-unpinned"), 0)

    def test_failed_always_exits_nonzero(self):
        self.assertEqual(self.run_fetch("failed"), 1)
        self.assertEqual(self.run_fetch("failed", "--allow-unpinned"), 1)


class CliTests(unittest.TestCase):
    def test_validate_manifest_and_empty_fetch(self):
        repo = Path(__file__).resolve().parent.parent
        manifest = str(repo / "data" / "manifest" / "datasets.json")
        self.assertEqual(main(["validate-manifest", "--manifest", manifest]), 0)
        # fetch must never touch the network in tests: use an empty temporary manifest
        empty = Path(tempfile.mkdtemp()) / "empty.json"
        empty.write_text(json.dumps({"schema_version": 1, "datasets": []}))
        self.assertEqual(main(["fetch", "--manifest", str(empty)]), 0)

    def test_fetch_of_repository_manifest_is_blocked_without_network(self):
        # The exact command verify-datasets runs must download nothing while rights are open.
        from unittest import mock

        class NoNetwork:
            def __init__(self, *a, **k):
                raise AssertionError("network client must not be created for blocked entries")

        repo = Path(__file__).resolve().parent.parent
        tmp = Path(tempfile.mkdtemp())
        with mock.patch("ufo_pipeline.zenodo.UrllibClient", NoNetwork):
            rc = main(["fetch", "--manifest", str(repo / "data" / "manifest" / "datasets.json"),
                       "--allow-unpinned", "--dest", str(tmp / "raw"),
                       "--provenance-dir", str(tmp / "prov")])
        self.assertEqual(rc, 1)
        self.assertFalse((tmp / "raw").exists())
        for name in ("gep-ufo-uap-2026-10", "sddb-dreams-2025-12-28"):
            prov = load_json(tmp / "prov" / f"{name}.json")
            self.assertEqual(prov["status"], "failed")
            self.assertTrue(prov["errors"][0].startswith("retrieval_blocked:"))

    def test_bad_manifest_returns_error(self):
        tmp = Path(tempfile.mkdtemp()) / "m.json"
        tmp.write_text(json.dumps({"schema_version": 1, "datasets": [{"dataset_id": "x"}]}))
        self.assertEqual(main(["validate-manifest", "--manifest", str(tmp)]), 2)

    def test_synthetic_then_analyze(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(main(["synthetic", "--scenario", "true_effect", "--out", str(root)]), 0)
        cfg = {"analysis_id": "cli", "dataset_provenance": "synthetic_true_effect.provenance.json",
               "data_file": "synthetic_true_effect.csv", "validation": VALIDATION,
               "analysis": {**ANALYSIS, "n_permutations": 300, "n_bootstrap": 100}}
        write_json(root / "cfg.json", cfg)
        self.assertEqual(main(["analyze", "--config", str(root / "cfg.json"), "--base-dir", str(root),
                               "--out", str(root / "out")]), 0)
        self.assertEqual(load_json(root / "out" / "cli.json")["assessment"]["status"], "synthetic_not_evidence")

    def test_analyze_tampered_returns_integrity_exit_code(self):
        root = Path(tempfile.mkdtemp())
        paths = write_dataset("null", root)
        paths["csv"].write_text(paths["csv"].read_text() + "S1,A,1,1\n")
        cfg = {"analysis_id": "t", "dataset_provenance": paths["provenance"].name, "data_file": paths["csv"].name,
               "validation": VALIDATION, "analysis": ANALYSIS}
        write_json(root / "cfg.json", cfg)
        self.assertEqual(main(["analyze", "--config", str(root / "cfg.json"), "--base-dir", str(root),
                               "--out", str(root / "o")]), 2)


if __name__ == "__main__":
    unittest.main()
