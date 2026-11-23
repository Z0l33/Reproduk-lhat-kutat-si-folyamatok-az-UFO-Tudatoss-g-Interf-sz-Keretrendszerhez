import tempfile
import unittest
from pathlib import Path

from ufo_pipeline.hashing import digests_equal, hash_file, parse_checksum
from ufo_pipeline.provenance import (IntegrityError, check_local_file, dataset_is_verified,
                                     load_json, require_local_file_integrity, validate_provenance,
                                     write_json)

SHA256_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
MD5_ABC = "900150983cd24fb0d6963f7d28e17f72"


class HashingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "abc.txt"
        self.f.write_bytes(b"abc")

    def test_known_vectors(self):
        h = hash_file(self.f, ("sha256", "md5"))
        self.assertEqual(h["sha256"], SHA256_ABC)
        self.assertEqual(h["md5"], MD5_ABC)

    def test_parse_checksum_forms(self):
        self.assertEqual(parse_checksum(f"md5:{MD5_ABC}"), ("md5", MD5_ABC))
        self.assertEqual(parse_checksum(MD5_ABC.upper()), ("md5", MD5_ABC))
        self.assertEqual(parse_checksum(SHA256_ABC)[0], "sha256")

    def test_parse_checksum_rejects_garbage(self):
        for bad in ("", "md5:xyz", "crc32:abcd", "abc"):
            with self.assertRaises(ValueError):
                parse_checksum(bad)

    def test_digests_equal(self):
        self.assertTrue(digests_equal(SHA256_ABC, SHA256_ABC.upper()))
        self.assertFalse(digests_equal(SHA256_ABC, "0" * 64))

    def test_unsupported_algorithm(self):
        with self.assertRaises(ValueError):
            hash_file(self.f, ("crc32",))


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.f = self.tmp / "abc.txt"
        self.f.write_bytes(b"abc")
        self.prov = {"schema_version": 1, "dataset_id": "x", "source": "zenodo", "record_id": "0",
                     "doi": "10.5281/zenodo.0", "synthetic": False, "status": "verified", "errors": [],
                     "files": [{"name": "abc.txt", "sha256": SHA256_ABC, "status": "verified"}]}

    def test_roundtrip_and_validate(self):
        p = self.tmp / "p.json"
        write_json(p, self.prov)
        self.assertEqual(load_json(p), self.prov)
        self.assertEqual(validate_provenance(self.prov), [])

    def test_validate_reports_missing_keys(self):
        self.assertTrue(validate_provenance({"schema_version": 1}))

    def test_local_file_ok_and_tampered(self):
        self.assertTrue(check_local_file(self.prov, self.f)["ok"])
        self.f.write_bytes(b"abd")
        self.assertFalse(check_local_file(self.prov, self.f)["ok"])
        with self.assertRaises(IntegrityError):
            require_local_file_integrity(self.prov, self.f)

    def test_unlisted_file_fails(self):
        other = self.tmp / "other.txt"
        other.write_bytes(b"abc")
        self.assertFalse(check_local_file(self.prov, other)["ok"])

    def test_dataset_is_verified_rules(self):
        self.assertTrue(dataset_is_verified(self.prov))
        self.assertFalse(dataset_is_verified({**self.prov, "status": "unpinned"}))
        self.assertFalse(dataset_is_verified({**self.prov, "errors": ["boom"]}))
        bad = {**self.prov, "files": [{"name": "a", "sha256": "x", "status": "unpinned"}]}
        self.assertFalse(dataset_is_verified(bad))
        self.assertFalse(dataset_is_verified({**self.prov, "files": []}))


if __name__ == "__main__":
    unittest.main()
