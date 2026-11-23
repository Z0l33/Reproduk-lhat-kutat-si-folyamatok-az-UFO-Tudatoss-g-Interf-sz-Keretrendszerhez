import itertools
import unittest

from ufo_pipeline.evidence import (POSITIVE_STATUSES, assess, evaluate_replication)
from ufo_pipeline.prereg import check_prereg, prereg_hash
from tests.helpers import ANALYSIS, make_prereg

RETRIEVED = "2030-01-01T00:00:00+00:00"


def problems(prereg=None, params=None, dataset_id="d", retrieved=RETRIEVED):
    return check_prereg(prereg or make_prereg("d"), params or dict(ANALYSIS), dataset_id=dataset_id,
                        dataset_first_retrieved_at=retrieved)


class PreregTests(unittest.TestCase):
    def test_conforming_is_confirmatory(self):
        self.assertEqual(problems(), [])

    def test_hash_changes_with_content(self):
        a, b = make_prereg("d"), make_prereg("d", sesoi_g=0.3)
        self.assertNotEqual(prereg_hash(a), prereg_hash(b))
        self.assertEqual(prereg_hash(a), prereg_hash(make_prereg("d")))

    def test_deviation_detected(self):
        for key, value in (("sesoi_g", 0.1), ("alpha", 0.1), ("seed", 2), ("group_b", "C"),
                           ("expected_direction", "any"), ("require_replication", False)):
            self.assertTrue(problems(params={**ANALYSIS, key: value}), key)

    def test_registered_after_retrieval(self):
        p = make_prereg("d", registered_at="2031-01-01T00:00:00+00:00")
        self.assertTrue(any("not earlier" in x for x in problems(p)))

    def test_missing_or_placeholder_registry(self):
        for rid in ("", "TEMPLATE", "REPLACE_ME"):
            self.assertTrue(problems(make_prereg("d", registry_id=rid)), rid)

    def test_naive_timestamp_and_unknown_retrieval(self):
        self.assertTrue(problems(make_prereg("d", registered_at="2020-01-01T00:00:00")))
        self.assertTrue(problems(retrieved=None))

    def test_wrong_dataset(self):
        self.assertTrue(problems(dataset_id="other"))

    def test_missing_fields(self):
        self.assertTrue(problems(prereg={"prereg_id": "x"}))


def clean(**over):
    base = dict(data_valid=True, leakage_detected=False, dataset_verified=True, synthetic=False,
                prereg_problems=[], expected_direction="a>b", require_replication=False,
                analysis={"verdict": "above_sesoi", "hedges_g": 0.8})
    base.update(over)
    return base


class EvidenceTests(unittest.TestCase):
    def test_clean_confirmatory_positive(self):
        a = assess(**clean())
        self.assertEqual(a.status, "confirmatory_positive_unreplicated")
        self.assertTrue(a.counts_as_positive_evidence)

    def test_requires_replication_by_default(self):
        a = assess(**clean(require_replication=True))
        self.assertEqual(a.status, "awaiting_independent_replication")
        self.assertFalse(a.counts_as_positive_evidence)

    def test_replication_outcomes(self):
        self.assertTrue(assess(**clean(require_replication=True), replication_outcome="replicated").counts_as_positive_evidence)
        for outcome in ("failed", "invalid"):
            a = assess(**clean(), replication_outcome=outcome)
            self.assertFalse(a.counts_as_positive_evidence, outcome)

    def test_blocking_conditions(self):
        cases = {"invalid_data": dict(data_valid=False), "invalid_leakage": dict(leakage_detected=True),
                 "synthetic_not_evidence": dict(synthetic=True), "unverified_data": dict(dataset_verified=False)}
        for status, over in cases.items():
            a = assess(**clean(**over))
            self.assertEqual(a.status, status)
            self.assertFalse(a.counts_as_positive_evidence)

    def test_exploratory_never_positive(self):
        for pp in (None, ["deviation"]):
            a = assess(**clean(prereg_problems=pp))
            self.assertEqual(a.status, "exploratory_hypothesis_generating")
            self.assertEqual(a.mode, "exploratory")
            self.assertFalse(a.counts_as_positive_evidence)

    def test_non_positive_verdicts(self):
        expected = {"equivalent_to_zero_within_sesoi": "confirmatory_null_equivalence",
                    "inconclusive": "confirmatory_inconclusive",
                    "significant_but_not_above_sesoi": "confirmatory_below_sesoi_or_uncertain"}
        for verdict, status in expected.items():
            a = assess(**clean(analysis={"verdict": verdict, "hedges_g": 0.1}))
            self.assertEqual(a.status, status)
            self.assertFalse(a.counts_as_positive_evidence)

    def test_wrong_direction(self):
        a = assess(**clean(analysis={"verdict": "above_sesoi", "hedges_g": -0.9}))
        self.assertEqual(a.status, "confirmatory_wrong_direction")
        self.assertFalse(a.counts_as_positive_evidence)
        self.assertTrue(assess(**clean(expected_direction="any",
                                       analysis={"verdict": "above_sesoi", "hedges_g": -0.9})).counts_as_positive_evidence)

    def test_exhaustive_invariant(self):
        """Positive evidence must be impossible unless EVERY condition is clean."""
        verdicts = ["above_sesoi", "equivalent_to_zero_within_sesoi", "inconclusive",
                    "significant_but_not_above_sesoi"]
        for (valid, leak, verified, synth, prereg_ok, req, repl, verdict, g) in itertools.product(
                (True, False), (True, False), (True, False), (True, False), (None, [], ["x"]),
                (True, False), (None, "replicated", "failed", "invalid"), verdicts, (0.9, -0.9)):
            a = assess(data_valid=valid, leakage_detected=leak, dataset_verified=verified, synthetic=synth,
                       prereg_problems=prereg_ok, expected_direction="a>b", require_replication=req,
                       analysis={"verdict": verdict, "hedges_g": g}, replication_outcome=repl)
            all_clean = (valid and not leak and verified and not synth and prereg_ok == []
                         and verdict == "above_sesoi" and g > 0 and repl not in ("failed", "invalid")
                         and (repl == "replicated" or not req))
            self.assertEqual(a.counts_as_positive_evidence, all_clean)
            self.assertEqual(a.counts_as_positive_evidence, a.status in POSITIVE_STATUSES)


def report(dataset_id, status, g, *, mode="confirmatory", synthetic=False, record="r", sha="a", prereg_sha="P",
           replicates=None):
    return {"dataset": {"dataset_id": dataset_id, "record_id": record, "synthetic": synthetic,
                        "file_sha256": {"f.csv": sha}},
            "preregistration": {"sha256": prereg_sha, "replicates_prereg_sha256": replicates},
            "analysis": {"hedges_g": g}, "assessment": {"status": status, "mode": mode},
            "data_access": {"mode": "explicit_allowlist"}}


class ReplicationRequiresAccessControlledReports(unittest.TestCase):
    def test_report_without_data_access_is_not_a_valid_replication(self):
        orig = report("o", "awaiting_independent_replication", 0.8, record="r1", sha="a", prereg_sha="P1")
        rep = report("r", "confirmatory_positive_unreplicated", 0.7, record="r2", sha="b", prereg_sha="P2",
                     replicates="P1")
        del rep["data_access"]
        out = evaluate_replication(orig, rep)
        self.assertEqual(out["outcome"], "invalid")
        self.assertTrue(any("data_access" in r for r in out["reasons"]))
        del orig["data_access"]
        self.assertEqual(evaluate_replication(orig, rep)["outcome"], "invalid")


class ReplicationTests(unittest.TestCase):
    orig = report("o", "awaiting_independent_replication", 0.8, record="r1", sha="a", prereg_sha="P1")

    def rep(self, status, g, **kw):
        base = dict(record="r2", sha="b", prereg_sha="P2", replicates="P1")
        base.update(kw)
        return report("r", status, g, **base)

    def test_success(self):
        self.assertEqual(evaluate_replication(self.orig, self.rep("confirmatory_positive_unreplicated", 0.7))["outcome"], "replicated")

    def test_failures_are_failures(self):
        for status in ("confirmatory_null_equivalence", "confirmatory_inconclusive",
                       "confirmatory_below_sesoi_or_uncertain", "confirmatory_wrong_direction"):
            self.assertEqual(evaluate_replication(self.orig, self.rep(status, 0.0))["outcome"], "failed", status)

    def test_opposite_sign_is_failure(self):
        self.assertEqual(evaluate_replication(self.orig, self.rep("confirmatory_positive_unreplicated", -0.7))["outcome"], "failed")

    def test_not_independent_is_invalid(self):
        for kw in (dict(record="r1"), dict(sha="a"), dict(replicates="WRONG"), dict(replicates=None),
                   dict(mode="exploratory"), dict(synthetic=True)):
            out = evaluate_replication(self.orig, self.rep("confirmatory_positive_unreplicated", 0.7, **kw))
            self.assertEqual(out["outcome"], "invalid", kw)

    def test_same_dataset_id_is_invalid(self):
        r = self.rep("confirmatory_positive_unreplicated", 0.7)
        r["dataset"]["dataset_id"] = "o"
        self.assertEqual(evaluate_replication(self.orig, r)["outcome"], "invalid")

    def test_unusable_replication_statuses_are_invalid(self):
        for status in ("invalid_data", "invalid_leakage", "unverified_data", "exploratory_hypothesis_generating"):
            self.assertEqual(evaluate_replication(self.orig, self.rep(status, 0.7))["outcome"], "invalid", status)

    def test_original_must_be_positive_candidate(self):
        bad = report("o", "confirmatory_null_equivalence", 0.0, record="r1", sha="a", prereg_sha="P1")
        self.assertEqual(evaluate_replication(bad, self.rep("confirmatory_positive_unreplicated", 0.7))["outcome"], "invalid")


if __name__ == "__main__":
    unittest.main()
