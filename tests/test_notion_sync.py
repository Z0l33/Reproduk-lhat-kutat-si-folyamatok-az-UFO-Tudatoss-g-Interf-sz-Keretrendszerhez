import unittest
from pathlib import Path

from ufo_pipeline import notion_sync as ns

SHA = "a" * 64


def page(pid="p1", ds="demo-set", rec="123", doi="10.5281/zenodo.123", name="d.csv",
         claim="", personal=False, status="submitted"):
    rt = lambda s: {"rich_text": [{"plain_text": s}]}  # noqa: E731
    return {"id": pid, "properties": {
        ns.P_ID: {"title": [{"plain_text": ds}]}, ns.P_RECORD: rt(rec), ns.P_DOI: rt(doi),
        ns.P_FILE: rt(name), ns.P_CLAIM: rt(claim), ns.P_PERSONAL: {"checkbox": personal},
        ns.P_STATUS: {"select": {"name": status}}, ns.P_DECISION: {"select": {"name": "pending"}}}}


def rows(*pages):
    return [ns.parse_row(p) for p in pages]


class RowsToEntries(unittest.TestCase):
    def test_valid_row_becomes_entry_with_pin(self):
        entries, problems = ns.rows_to_entries(rows(page(claim=SHA)))
        self.assertEqual(problems, {})
        self.assertEqual(entries[0]["files"], [{"name": "d.csv", "sha256": SHA}])

    def test_no_claim_means_no_pin(self):
        entries, _ = ns.rows_to_entries(rows(page()))
        self.assertNotIn("sha256", entries[0]["files"][0])

    def test_only_submitted_rows_processed(self):
        entries, problems = ns.rows_to_entries(rows(page(status="verified"), page("p2", status="failed")))
        self.assertEqual((entries, problems), ([], {}))

    def test_hand_set_verified_is_never_trusted_or_processed(self):
        entries, _ = ns.rows_to_entries(rows(page(status="verified")))
        self.assertEqual(entries, [])

    def test_personal_data_rejected(self):
        entries, problems = ns.rows_to_entries(rows(page(personal=True)))
        self.assertEqual(entries, [])
        self.assertIn("personal data", problems["p1"][0])

    def test_sloppy_claim_not_repaired(self):
        for bad in (SHA.upper(), SHA + "\n", " " + SHA, SHA[:-1]):
            entries, problems = ns.rows_to_entries(rows(page(claim=bad)))
            self.assertEqual(entries, [], bad)
            self.assertIn("p1", problems)

    def test_concept_or_mismatched_doi_rejected(self):
        _, problems = ns.rows_to_entries(rows(page(doi="10.5281/zenodo.999")))
        self.assertIn("p1", problems)

    def test_path_in_file_name_rejected(self):
        _, problems = ns.rows_to_entries(rows(page(name="../x.csv")))
        self.assertIn("p1", problems)

    def test_two_files_one_dataset_merge(self):
        entries, problems = ns.rows_to_entries(rows(page("p1", name="a.csv"), page("p2", name="b.csv")))
        self.assertEqual(problems, {})
        self.assertEqual([f["name"] for f in entries[0]["files"]], ["a.csv", "b.csv"])

    def test_same_dataset_conflicting_record_rejected(self):
        entries, problems = ns.rows_to_entries(rows(
            page("p1", name="a.csv"), page("p2", name="b.csv", rec="124", doi="10.5281/zenodo.124")))
        self.assertIn("p2", problems)
        self.assertEqual(len(entries), 1)

    def test_duplicate_file_names_rejected(self):
        entries, problems = ns.rows_to_entries(rows(page("p1"), page("p2")))
        self.assertEqual(entries, [])
        self.assertTrue(problems)


class ResultProperties(unittest.TestCase):
    def prov(self, status="unpinned", checked=True):
        return {"status": status, "retrieved_at": "2026-11-23T12:00:00+00:00", "errors": [],
                "files": [{"name": "d.csv", "status": status, "sha256": SHA, "size": 10,
                           "api_checksum_verified": checked, "errors": []}]}

    def test_unpinned_reported_as_unpinned(self):
        props = ns.properties_from_provenance(self.prov(), "d.csv", "https://run")
        self.assertEqual(props[ns.P_STATUS]["select"]["name"], "unpinned")
        self.assertEqual(props[ns.P_SRC]["select"]["name"], "ok")
        self.assertEqual(props[ns.P_SIZE]["number"], 10)

    def test_mismatch_and_unavailable(self):
        self.assertEqual(ns.properties_from_provenance(self.prov("failed", False), "d.csv")
                         [ns.P_SRC]["select"]["name"], "mismatch")
        self.assertEqual(ns.properties_from_provenance(self.prov(checked=None), "d.csv")
                         [ns.P_SRC]["select"]["name"], "unavailable")

    def test_missing_file_is_failed_never_verified(self):
        props = ns.properties_from_provenance(self.prov("verified"), "other.csv")
        self.assertEqual(props[ns.P_STATUS]["select"]["name"], "failed")

    def test_unknown_status_becomes_failed(self):
        p = self.prov()
        p["files"][0]["status"] = "synthetic"
        self.assertEqual(ns.properties_from_provenance(p, "d.csv")[ns.P_STATUS]["select"]["name"], "failed")

    def test_errors_surface_in_note(self):
        p = self.prov("failed")
        p["files"][0]["errors"] = ["pinned_sha256_mismatch"]
        self.assertIn("pinned_sha256_mismatch",
                      ns.properties_from_provenance(p, "d.csv")["Source / note"]["rich_text"][0]["text"]["content"])


if __name__ == "__main__":
    unittest.main()


class SyncFlow(unittest.TestCase):
    class Fake:
        def __init__(self, pages):
            self.pages, self.updated = pages, []

        def query_submitted(self, database_id):
            return self.pages

        def update_page(self, page_id, properties):
            self.updated.append((page_id, properties))

    def run_sync(self, pages, apply, blocked=None):
        import tempfile
        calls, checked = [], []

        def checker(entry):
            checked.append(entry["dataset_id"])
            return {"dataset_id": entry["dataset_id"], "status": "metadata_ok", "downloaded": False,
                    "errors": [], "files": [{"name": f["name"], "listed": True} for f in entry["files"]]}

        def retriever(entry, dest, existing=None):
            calls.append(entry["dataset_id"])
            return {"status": "unpinned", "retrieved_at": "2026-11-23T12:00:00+00:00", "errors": [],
                    "files": [{"name": f["name"], "status": "unpinned", "sha256": SHA, "size": 1,
                               "api_checksum_verified": True, "errors": []} for f in entry["files"]]}
        client = self.Fake(pages)
        with tempfile.TemporaryDirectory() as d:
            report = ns.sync(client, "db", d, d, retriever, apply=apply,
                             blocked=blocked if blocked is not None else {}, metadata_checker=checker)
            self.leftover = sorted(p.name for p in Path(d).iterdir())
        self.checked = checked
        return report, client, calls

    def test_dry_run_is_metadata_only_no_download_no_provenance_no_notion_write(self):
        report, client, calls = self.run_sync([page()], apply=False)
        self.assertEqual(client.updated, [])
        self.assertEqual(calls, [])                          # retriever (download) never called
        self.assertEqual(self.checked, ["demo-set"])         # metadata checked instead
        self.assertEqual(self.leftover, [])                  # no provenance or data file written
        self.assertFalse(report["applied"])
        self.assertFalse(report["downloaded"])
        self.assertEqual(report["mode"], "dry_run_metadata_only")
        self.assertEqual(report["datasets"], {"demo-set": "metadata_ok"})

    def test_row_for_a_blocked_record_is_rejected_in_dry_run_and_apply(self):
        for apply in (False, True):
            for key in (("record", "123"), ("dataset", "demo-set")):
                report, client, calls = self.run_sync([page("p1")], apply=apply, blocked={key: "rights pending"})
                self.assertEqual(calls, [], (apply, key))
                self.assertEqual(self.checked, [], (apply, key))
                self.assertEqual(self.leftover, [], (apply, key))
                self.assertIn("retrieval_blocked", report["rejected"]["p1"][0])
                self.assertEqual(report["datasets"], {})
                written = dict(client.updated)
                if apply:
                    self.assertEqual(written["p1"][ns.P_STATUS]["select"]["name"], "failed")
                else:
                    self.assertEqual(written, {})

    def test_apply_updates_row_and_rejects_bad_rows_without_download(self):
        report, client, calls = self.run_sync([page("p1"), page("p2", ds="bad", personal=True)], apply=True)
        self.assertEqual(calls, ["demo-set"])
        updated = dict(client.updated)
        self.assertEqual(updated["p1"][ns.P_STATUS]["select"]["name"], "unpinned")
        self.assertEqual(updated["p2"][ns.P_STATUS]["select"]["name"], "failed")
