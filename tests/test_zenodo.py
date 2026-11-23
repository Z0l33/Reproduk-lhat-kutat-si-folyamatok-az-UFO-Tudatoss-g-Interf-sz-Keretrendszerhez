"""Zenodo retrieval tests use a FAKE client and a FAKE record id ("0").

Nothing here touches the network and no real Zenodo record is claimed or implied.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ufo_pipeline.provenance import dataset_is_verified, validate_provenance
from ufo_pipeline.zenodo import ZenodoError, load_manifest, retrieve_dataset, validate_entry

FAKE_ID = "0"
FAKE_DOI = "10.5281/zenodo.0"
CONTENT = b"a,b\n1,2\n"
MD5 = "md5:" + hashlib.md5(CONTENT).hexdigest()
SHA = hashlib.sha256(CONTENT).hexdigest()


class FakeClient:
    def __init__(self, record, payloads=None, fail_json=False):
        self.record, self.payloads, self.fail_json = record, payloads or {}, fail_json
        self.downloads = []

    def get_json(self, url):
        if self.fail_json:
            raise OSError("offline")
        return self.record

    def download(self, url, dest):
        self.downloads.append(url)
        Path(dest).write_bytes(self.payloads.get(url, CONTENT))


def record(**over):
    base = {"id": 0, "doi": FAKE_DOI, "conceptdoi": "10.5281/zenodo.99999999",
            "metadata": {"title": "synthetic fake record", "version": "v0"},
            "files": [{"key": "data.csv", "size": len(CONTENT), "checksum": MD5,
                       "links": {"self": "https://zenodo.org/api/records/0/files/data.csv/content"}}]}
    base.update(over)
    return base


def entry(**over):
    base = {"dataset_id": "fake-set", "record_id": FAKE_ID, "doi": FAKE_DOI,
            "files": [{"name": "data.csv", "sha256": SHA}]}
    base.update(over)
    return base


class RetrieveTests(unittest.TestCase):
    def setUp(self):
        self.dest = Path(tempfile.mkdtemp())

    def run_it(self, ent=None, rec=None, payloads=None, **kw):
        client = FakeClient(rec or record(), payloads, **kw)
        return retrieve_dataset(ent or entry(), self.dest, client, now="2026-01-01T00:00:00+00:00"), client

    def test_verified(self):
        prov, _ = self.run_it()
        self.assertEqual(prov["status"], "verified")
        self.assertEqual(prov["files"][0]["status"], "verified")
        self.assertEqual(prov["files"][0]["sha256"], SHA)
        self.assertTrue((self.dest / "data.csv").exists())
        self.assertFalse((self.dest / "data.csv.part").exists())
        self.assertEqual(prov["first_retrieved_at"], "2026-01-01T00:00:00+00:00")

    def test_fetched_record_passes_strict_validation_and_local_rehash(self):
        prov, _ = self.run_it()
        self.assertEqual(validate_provenance(prov), [])
        self.assertTrue(dataset_is_verified(prov, self.dest))
        (self.dest / "data.csv").write_bytes(b"tampered after download")
        self.assertFalse(dataset_is_verified(prov, self.dest))

    def test_first_retrieved_at_is_preserved(self):
        client = FakeClient(record())
        prov = retrieve_dataset(entry(), self.dest, client, existing={"first_retrieved_at": "2020-05-05T00:00:00+00:00"},
                                now="2026-01-01T00:00:00+00:00")
        self.assertEqual(prov["first_retrieved_at"], "2020-05-05T00:00:00+00:00")

    def test_unpinned_is_not_verified(self):
        ent = entry(files=[{"name": "data.csv"}])
        prov, _ = self.run_it(ent)
        self.assertEqual(prov["status"], "unpinned")
        self.assertEqual(prov["files"][0]["sha256"], SHA)  # computed hash is reported for pinning

    def test_wrong_pin_fails_and_file_removed(self):
        prov, _ = self.run_it(entry(files=[{"name": "data.csv", "sha256": "0" * 64}]))
        self.assertEqual(prov["status"], "failed")
        self.assertIn("pinned_sha256_mismatch", prov["files"][0]["errors"])
        self.assertFalse((self.dest / "data.csv").exists())

    def test_corrupted_download_fails_source_checksum(self):
        url = "https://zenodo.org/api/records/0/files/data.csv/content"
        prov, _ = self.run_it(payloads={url: b"corrupted"}, ent=entry(files=[{"name": "data.csv"}]))
        self.assertEqual(prov["status"], "failed")
        self.assertIn("source_checksum_mismatch", prov["files"][0]["errors"])
        self.assertFalse((self.dest / "data.csv").exists())

    def test_missing_source_checksum_with_pin(self):
        rec = record()
        rec["files"][0].pop("checksum")
        prov, _ = self.run_it(rec=rec)
        self.assertEqual(prov["files"][0]["status"], "verified_pin_only")

    def test_missing_source_checksum_without_pin_not_verified(self):
        rec = record()
        rec["files"][0].pop("checksum")
        prov, _ = self.run_it(ent=entry(files=[{"name": "data.csv"}]), rec=rec)
        self.assertNotEqual(prov["status"], "verified")

    def test_doi_mismatch(self):
        prov, client = self.run_it(rec=record(doi="10.5281/zenodo.1234567"))
        self.assertEqual(prov["status"], "failed")
        self.assertTrue(any(e.startswith("doi_mismatch") for e in prov["errors"]))
        self.assertEqual(client.downloads, [])

    def test_concept_doi_rejected(self):
        ent = entry(doi="10.5281/zenodo.0")
        rec = record(doi="10.5281/zenodo.55", conceptdoi="10.5281/zenodo.0")
        prov, _ = self.run_it(ent, rec)
        self.assertTrue(any(e.startswith("doi_is_concept_doi") for e in prov["errors"]))

    def test_record_id_mismatch(self):
        prov, _ = self.run_it(rec=record(id=7))
        self.assertTrue(any(e.startswith("record_id_mismatch") for e in prov["errors"]))

    def test_file_not_in_record(self):
        prov, _ = self.run_it(entry(files=[{"name": "other.csv"}]))
        self.assertEqual(prov["status"], "failed")

    def test_fetch_error_is_reported(self):
        prov, _ = self.run_it(fail_json=True)
        self.assertEqual(prov["status"], "failed")
        self.assertTrue(prov["errors"][0].startswith("record_fetch_failed"))

    def test_foreign_download_host_refused(self):
        rec = record()
        rec["files"][0]["links"]["self"] = "https://evil.example/data.csv"
        prov, client = self.run_it(rec=rec)
        self.assertEqual(prov["status"], "failed")
        self.assertEqual(client.downloads, [])

    def test_manifest_checksum_drift_detected(self):
        ent = entry(files=[{"name": "data.csv", "sha256": SHA, "zenodo_checksum": "md5:" + "1" * 32}])
        prov, _ = self.run_it(ent)
        self.assertEqual(prov["status"], "failed")
        self.assertEqual(prov["files"][0]["manifest_checksum_status"], "mismatch")


class ManifestTests(unittest.TestCase):
    def test_entry_validation(self):
        self.assertEqual(validate_entry(entry()), [])
        self.assertTrue(validate_entry(entry(record_id="abc")))
        self.assertTrue(validate_entry(entry(doi="10.5281/zenodo.1")))  # != record id
        self.assertTrue(validate_entry(entry(doi="10.1234/other")))
        self.assertTrue(validate_entry(entry(files=[{"name": "../evil.csv"}])))
        self.assertTrue(validate_entry(entry(files=[{"name": "a.csv", "sha256": "xyz"}])))
        self.assertTrue(validate_entry(entry(files=[])))
        self.assertTrue(validate_entry(entry(synthetic=True)))
        self.assertTrue(validate_entry(entry(dataset_id="Bad ID")))
        # a trailing newline must not slip through any of the anchored patterns
        self.assertTrue(validate_entry(entry(record_id="0\n")))
        self.assertTrue(validate_entry(entry(doi="10.5281/zenodo.0\n")))
        self.assertTrue(validate_entry(entry(dataset_id="ok\n")))
        self.assertTrue(validate_entry(entry(files=[{"name": "a.csv", "sha256": SHA + "\n"}])))

    def test_load_manifest(self):
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "m.json"
        p.write_text(json.dumps({"schema_version": 1, "datasets": []}))
        self.assertEqual(load_manifest(p), [])
        p.write_text(json.dumps({"schema_version": 1, "datasets": [entry(), entry()]}))
        with self.assertRaises(ZenodoError):
            load_manifest(p)
        p.write_text(json.dumps({"schema_version": 1, "datasets": [entry(record_id="x")]}))
        with self.assertRaises(ZenodoError):
            load_manifest(p)
        p.write_text(json.dumps({"datasets": []}))
        with self.assertRaises(ZenodoError):
            load_manifest(p)

    def test_repository_manifest_is_valid(self):
        repo_manifest = Path(__file__).resolve().parent.parent / "data" / "manifest" / "datasets.json"
        load_manifest(repo_manifest)


if __name__ == "__main__":
    unittest.main()
