import copy
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_logic_fixture
from ufo_pipeline.hashing import sha256_file
from ufo_pipeline.pipeline import run_analysis
from ufo_pipeline.provenance import (IntegrityError, check_local_file, dataset_is_verified, load_json,
                                     validate_provenance, write_json)

GOOD_SHA = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"  # sha256(b"abc")


def good():
    return {"schema_version": 1, "dataset_id": "d", "source": "zenodo", "record_id": "0",
            "doi": "10.5281/zenodo.0", "synthetic": False, "status": "verified", "errors": [],
            "first_retrieved_at": "2030-01-01T00:00:00+00:00",
            "files": [{"name": "a.csv", "size": 3, "sha256": GOOD_SHA, "status": "verified"}]}


def top(**over):
    p = good()
    p.update(over)
    return p


def file_entry(**over):
    p = good()
    p["files"][0].update(over)
    return p


class ValidateProvenanceStrict(unittest.TestCase):
    def test_good_record_has_no_problems(self):
        self.assertEqual(validate_provenance(good()), [])

    def test_bad_hashes_rejected(self):
        for bad in (GOOD_SHA.upper(), GOOD_SHA[:-1], GOOD_SHA + "0", "g" * 64, "", None, 123,
                    "md5:" + "a" * 32, " " + GOOD_SHA, GOOD_SHA + "\n"):
            self.assertTrue(validate_provenance(file_entry(sha256=bad)), repr(bad))

    def test_bad_names_rejected(self):
        for bad in ("../x.csv", "a/b.csv", "", ".", "..", "a\\b.csv", None, 5):
            self.assertTrue(validate_provenance(file_entry(name=bad)), repr(bad))

    def test_duplicate_names_rejected(self):
        p = good()
        p["files"].append(copy.deepcopy(p["files"][0]))
        self.assertTrue(any("duplicate" in x for x in validate_provenance(p)))

    def test_status_vocabulary(self):
        self.assertTrue(validate_provenance(top(status="ok")))
        self.assertTrue(validate_provenance(file_entry(status="maybe")))

    def test_verified_requires_consistency(self):
        self.assertTrue(validate_provenance(top(errors=["x"])))
        self.assertTrue(validate_provenance(file_entry(status="unpinned")))
        self.assertTrue(validate_provenance(top(files=[])))
        self.assertTrue(validate_provenance(top(first_retrieved_at=None)))
        self.assertTrue(validate_provenance(top(first_retrieved_at="2030-01-01T00:00:00")))  # naive

    def test_zenodo_ids(self):
        self.assertTrue(validate_provenance(top(record_id="abc")))
        self.assertTrue(validate_provenance(top(record_id=0)))
        self.assertTrue(validate_provenance(top(doi="10.5281/zenodo.1")))
        self.assertTrue(validate_provenance(top(doi="10.1234/other")))
        self.assertTrue(validate_provenance(top(synthetic=True, status="synthetic")))

    def test_types_and_shapes(self):
        self.assertTrue(validate_provenance(top(synthetic="false")))
        self.assertTrue(validate_provenance(top(synthetic=True)))  # synthetic but status != synthetic
        self.assertTrue(validate_provenance(file_entry(size=-1)))
        self.assertTrue(validate_provenance(file_entry(size=True)))
        self.assertTrue(validate_provenance(file_entry(path="../x")))
        self.assertTrue(validate_provenance(file_entry(path="/abs")))
        self.assertTrue(validate_provenance(top(files="nope")))
        self.assertTrue(validate_provenance("not a dict"))
        self.assertTrue(validate_provenance({}))

    def test_failed_file_may_lack_hash_but_not_carry_a_bad_one(self):
        ok = top(status="failed", files=[{"name": "a.csv", "sha256": None, "status": "failed"}])
        self.assertEqual(validate_provenance(ok), [])
        bad = top(status="failed", files=[{"name": "a.csv", "sha256": "xyz", "status": "failed"}])
        self.assertTrue(validate_provenance(bad))

    def test_synthetic_record_shape(self):
        synth = {"schema_version": 1, "dataset_id": "s", "source": "synthetic-generator", "synthetic": True,
                 "status": "synthetic", "files": [{"name": "a.csv", "sha256": GOOD_SHA, "status": "synthetic"}]}
        self.assertEqual(validate_provenance(synth), [])


class CheckLocalFileIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "a.csv"
        self.f.write_bytes(b"abc")

    def test_ok(self):
        self.assertTrue(check_local_file(good(), self.f)["ok"])

    def test_missing_file(self):
        self.assertFalse(check_local_file(good(), self.tmp / "nope.csv")["ok"])

    def test_renamed_copy_with_same_content_is_refused(self):
        twin = self.tmp / "copy.csv"
        twin.write_bytes(b"abc")
        out = check_local_file(good(), twin)
        self.assertFalse(out["ok"])
        self.assertIn("renamed", out["message"])

    def test_same_name_different_content_is_refused(self):
        other = self.tmp / "sub"
        other.mkdir()
        (other / "a.csv").write_bytes(b"xyz")
        self.assertFalse(check_local_file(good(), other / "a.csv")["ok"])

    def test_ambiguous_entries_refused(self):
        p = good()
        p["files"].append(copy.deepcopy(p["files"][0]))
        out = check_local_file(p, self.f)
        self.assertFalse(out["ok"])
        self.assertIn("ambiguous", out["message"])

    def test_size_mismatch_refused(self):
        self.assertFalse(check_local_file(file_entry(size=4), self.f)["ok"])

    def test_malformed_recorded_hash_refused(self):
        out = check_local_file(file_entry(sha256=GOOD_SHA.upper()), self.f)
        self.assertFalse(out["ok"])
        self.assertIn("malformed", out["message"])

    def test_recorded_path_must_match_location(self):
        sub = self.tmp / "set1"
        sub.mkdir()
        (sub / "a.csv").write_bytes(b"abc")
        self.assertTrue(check_local_file(file_entry(path="set1/a.csv"), sub / "a.csv")["ok"])
        self.assertFalse(check_local_file(file_entry(path="set2/a.csv"), sub / "a.csv")["ok"])


class DatasetIsVerifiedHashesLocalFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "a.csv").write_bytes(b"abc")
        (self.tmp / "b.csv").write_bytes(b"hello")
        self.prov = good()
        self.prov["files"].append({"name": "b.csv", "size": 5, "status": "verified",
                                   "sha256": sha256_file(self.tmp / "b.csv")})

    def test_all_files_present_and_intact(self):
        self.assertTrue(dataset_is_verified(self.prov, self.tmp))

    def test_one_tampered_file(self):
        (self.tmp / "b.csv").write_bytes(b"HELLO")
        self.assertFalse(dataset_is_verified(self.prov, self.tmp))

    def test_one_missing_file(self):
        (self.tmp / "b.csv").unlink()
        self.assertFalse(dataset_is_verified(self.prov, self.tmp))

    def test_malformed_record_is_never_verified(self):
        self.prov["files"][0]["sha256"] = GOOD_SHA.upper()
        self.assertFalse(dataset_is_verified(self.prov, self.tmp))


class PipelineUsesLocalRehash(unittest.TestCase):
    def test_tampered_non_analysed_file_blocks_analysis(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-x")
        extra = fx["root"] / "extra.csv"
        extra.write_bytes(b"extra data")
        prov = load_json(fx["paths"]["provenance"])
        prov["files"].append({"name": "extra.csv", "size": extra.stat().st_size, "status": "verified",
                              "sha256": sha256_file(extra)})
        write_json(fx["paths"]["provenance"], prov)
        before = run_analysis(fx["config"], base_dir=fx["root"])
        self.assertEqual(before["assessment"]["status"], "awaiting_independent_replication")
        extra.write_bytes(b"tampered!!")  # the analysed file itself is untouched
        after = run_analysis(fx["config"], base_dir=fx["root"])
        self.assertEqual(after["assessment"]["status"], "unverified_data")
        self.assertIsNone(after["analysis"])
        self.assertFalse(after["assessment"]["counts_as_positive_evidence"])

    def test_malformed_recorded_hash_blocks_analysis(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-y")
        prov = load_json(fx["paths"]["provenance"])
        prov["files"][0]["sha256"] = prov["files"][0]["sha256"].upper()
        write_json(fx["paths"]["provenance"], prov)
        with self.assertRaises(IntegrityError):
            run_analysis(fx["config"], base_dir=fx["root"])

    def test_report_lists_provenance_problems(self):
        fx = make_logic_fixture("true_effect", dataset_id="fx-z", status="unpinned", file_status="unpinned")
        rep = run_analysis(fx["config"], base_dir=fx["root"])
        self.assertEqual(rep["dataset"]["provenance_problems"], [])
        self.assertEqual(rep["assessment"]["status"], "unverified_data")


if __name__ == "__main__":
    unittest.main()
