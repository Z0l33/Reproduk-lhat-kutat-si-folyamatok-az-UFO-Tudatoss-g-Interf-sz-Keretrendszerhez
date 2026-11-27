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
                      ns.properties_from_provenance(p, "d.csv")[ns.P_NOTES]["rich_text"][0]["text"]["content"])


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


class SubmitterFieldsAreNeverWritten(unittest.TestCase):
    """Audit finding K3: the submitter's 'Source / note' was overwritten by pipeline errors."""

    def test_payload_builders_write_only_pipeline_fields(self):
        prov_ok = {"status": "unpinned", "retrieved_at": "t", "errors": [],
                   "files": [{"name": "d.csv", "status": "unpinned", "sha256": SHA, "size": 1,
                              "api_checksum_verified": True, "errors": []}]}
        prov_bad = {"status": "failed", "retrieved_at": "t", "errors": ["record_fetch_failed: x"], "files": []}
        payloads = [ns.properties_from_provenance(prov_ok, "d.csv", "https://run"),
                    ns.properties_from_provenance(prov_bad, "d.csv", "https://run"),
                    ns.rejection_properties(["row is flagged as containing personal data; rejected"])]
        for props in payloads:
            self.assertEqual(set(props) & ns.PROTECTED_FIELDS, set())
            self.assertLessEqual(set(props), ns.PIPELINE_FIELDS)
        self.assertIn(ns.P_NOTES, payloads[1])
        self.assertIn(ns.P_NOTES, payloads[2])

    def test_apply_keeps_the_submitter_note_for_rejected_failed_and_clean_rows(self):
        import tempfile

        def with_note(p):
            p["properties"][ns.P_SUBMITTER_NOTE] = {"rich_text": [{"plain_text": "Submitter note: source X"}]}
            return p

        class Client:
            def __init__(self, pages):
                self.pages, self.updated = pages, {}

            def query_submitted(self, database_id):
                return self.pages

            def update_page(self, page_id, properties):
                self.updated[page_id] = properties

        def retriever(entry, dest, existing=None):
            ok = entry["dataset_id"] == "clean"
            return {"status": "unpinned" if ok else "failed", "retrieved_at": "t", "errors": [],
                    "files": [{"name": "d.csv", "status": "unpinned" if ok else "failed", "sha256": None, "size": 1,
                               "api_checksum_verified": ok, "errors": [] if ok else ["source_checksum_mismatch"]}]}

        client = Client([with_note(page("rejected", ds="r", rec="101", doi="10.5281/zenodo.101", personal=True)),
                         with_note(page("failed", ds="f", rec="102", doi="10.5281/zenodo.102")),
                         with_note(page("clean", ds="clean", rec="103", doi="10.5281/zenodo.103"))])
        with tempfile.TemporaryDirectory() as d:
            ns.sync(client, "db", d, d, retriever, apply=True, blocked={})
        self.assertEqual(set(client.updated), {"rejected", "failed", "clean"})
        for pid, props in client.updated.items():
            self.assertNotIn(ns.P_SUBMITTER_NOTE, props, pid)
            self.assertLessEqual(set(props), ns.PIPELINE_FIELDS, pid)
        self.assertIn("personal data", client.updated["rejected"][ns.P_NOTES]["rich_text"][0]["text"]["content"])
        self.assertIn("source_checksum_mismatch", client.updated["failed"][ns.P_NOTES]["rich_text"][0]["text"]["content"])

    def test_the_write_guard_refuses_foreign_properties(self):
        import tempfile

        class Client:
            updated = []

            def query_submitted(self, database_id):
                return [page("p1", personal=True)]

            def update_page(self, page_id, properties):
                self.updated.append(page_id)

        original = ns.rejection_properties
        try:
            ns.rejection_properties = lambda issues: {ns.P_SUBMITTER_NOTE: ns._rich("x")}
            with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
                ns.sync(Client(), "db", d, d, lambda *a, **k: None, apply=True, blocked={})
        finally:
            ns.rejection_properties = original
        self.assertEqual(Client.updated, [])


class NotionTextDecoding(unittest.TestCase):
    def test_content_only_title_and_rich_text(self):
        p = page()
        for prop in p["properties"].values():
            for key in ("title", "rich_text"):
                if key in prop:
                    prop[key] = [{"type": "text", "text": {"content": item["plain_text"]}}
                                 for item in prop[key]]
        entries, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(problems, {})
        self.assertEqual(entries[0]["dataset_id"], "demo-set")

    def test_conflicting_title_rejected(self):
        p = page()
        p["properties"][ns.P_ID]["title"] = [
            {"type": "text", "plain_text": "demo-set", "text": {"content": "other"}}]
        entries, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(entries, [])
        self.assertIn("p1", problems)

    def test_personal_data_still_rejected(self):
        p = page(personal=True)
        p["properties"][ns.P_ID]["title"] = [
            {"type": "text", "text": {"content": "demo-set"}}]
        entries, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(entries, [])
        self.assertIn("personal data", problems["p1"][0])


class NotionTextDecodingEdgeCases(unittest.TestCase):
    """Edge cases for _decode_text / parse_row. An undecodable value must never be read as empty:
    for 'Claimed SHA-256' that would silently drop the pin and turn a pinned check into 'unpinned'."""

    def assert_rejected(self, p, field):
        row = ns.parse_row(p)
        self.assertIn(field, row["malformed"])
        entries, problems = ns.rows_to_entries([row])
        self.assertEqual(entries, [])
        self.assertIn("p1", problems)
        self.assertIn(field, problems["p1"][0])

    # --- pin downgrade (the reason for fail-closed decoding) ---
    def test_conflicting_claim_is_rejected_not_unpinned(self):
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM]["rich_text"] = [
            {"type": "text", "plain_text": SHA, "text": {"content": "b" * 64}}]
        self.assert_rejected(p, ns.P_CLAIM)

    def test_undecodable_claim_segment_is_rejected_not_unpinned(self):
        for bad in (["not-an-object"], [{"type": "mention"}], [{"plain_text": None}], [{"plain_text": 5}]):
            p = page(claim=SHA)
            p["properties"][ns.P_CLAIM]["rich_text"] = bad
            self.assert_rejected(p, ns.P_CLAIM)

    def test_wrong_container_type_is_rejected(self):
        for bad in ({"rich_text": None}, {"rich_text": "abc"}, {"select": {"name": "x"}}, "abc", 7):
            p = page(claim=SHA)
            p["properties"][ns.P_CLAIM] = bad
            self.assert_rejected(p, ns.P_CLAIM)

    # --- every decoded field is covered, not just the title ---
    def test_conflict_in_each_text_field_is_rejected(self):
        for field, key in ((ns.P_ID, "title"), (ns.P_RECORD, "rich_text"), (ns.P_DOI, "rich_text"),
                           (ns.P_FILE, "rich_text")):
            p = page()
            p["properties"][field][key] = [{"type": "text", "plain_text": "x", "text": {"content": "y"}}]
            self.assert_rejected(p, field)

    def test_non_string_content_next_to_plain_text_is_rejected(self):
        p = page()
        p["properties"][ns.P_ID]["title"] = [{"type": "text", "plain_text": "demo-set", "text": {"content": 1}}]
        self.assert_rejected(p, ns.P_ID)

    def test_content_only_segment_of_other_type_is_rejected(self):
        p = page()
        p["properties"][ns.P_ID]["title"] = [{"type": "equation", "text": {"content": "demo-set"}}]
        self.assert_rejected(p, ns.P_ID)

    # --- legitimate shapes still decode ---
    def test_multi_segment_values_are_joined(self):
        p = page(claim=SHA)
        p["properties"][ns.P_ID]["title"] = [{"plain_text": "demo"}, {"plain_text": "-set"}]
        p["properties"][ns.P_CLAIM]["rich_text"] = [
            {"type": "text", "text": {"content": SHA[:32]}}, {"plain_text": SHA[32:]}]
        entries, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(problems, {})
        self.assertEqual(entries[0]["dataset_id"], "demo-set")
        self.assertEqual(entries[0]["files"][0]["sha256"], SHA)

    def test_matching_plain_text_and_content_decode(self):
        p = page()
        p["properties"][ns.P_ID]["title"] = [{"type": "text", "plain_text": "demo-set",
                                               "text": {"content": "demo-set"}}]
        entries, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(problems, {})

    def test_mention_with_plain_text_decodes(self):
        p = page()
        p["properties"][ns.P_ID]["title"] = [{"type": "mention", "plain_text": "demo-set", "mention": {}}]
        _, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(problems, {})

    def test_absent_or_empty_claim_still_means_no_pin(self):
        for claim_prop in (None, {"rich_text": []}, {"rich_text": [{"plain_text": ""}]}):
            p = page()
            if claim_prop is None:
                del p["properties"][ns.P_CLAIM]
            else:
                p["properties"][ns.P_CLAIM] = claim_prop
            entries, problems = ns.rows_to_entries(rows(p))
            self.assertEqual(problems, {}, claim_prop)
            self.assertNotIn("sha256", entries[0]["files"][0])

    def test_decoded_values_are_not_normalised(self):
        for ds in ("Demo-Set", " demo-set", "demo-set\n"):
            p = page()
            p["properties"][ns.P_ID]["title"] = [{"type": "text", "text": {"content": ds}}]
            entries, problems = ns.rows_to_entries(rows(p))
            self.assertEqual(entries, [], repr(ds))
            self.assertIn("p1", problems)

    def test_malformed_field_on_non_submitted_row_is_ignored(self):
        p = page(status="verified")
        p["properties"][ns.P_CLAIM]["rich_text"] = ["bad"]
        self.assertEqual(ns.rows_to_entries(rows(p)), ([], {}))


class NotionTextDecodingAudit21(unittest.TestCase):
    """Audit findings R21-01..R21-03 (PR #21 re-review)."""

    def assert_rejected(self, p, field):
        row = ns.parse_row(p)
        self.assertIn(field, row["malformed"])
        entries, problems = ns.rows_to_entries([row])
        self.assertEqual(entries, [])
        self.assertIn(field, problems["p1"][0])

    def assert_accepted(self, p):
        entries, problems = ns.rows_to_entries(rows(p))
        self.assertEqual(problems, {})
        return entries

    # R21-01: absent optional field is allowed; present-but-null is malformed
    def test_r21_01_absent_claim_is_allowed(self):
        p = page()
        del p["properties"][ns.P_CLAIM]
        self.assertNotIn("sha256", self.assert_accepted(p)[0]["files"][0])

    def test_r21_01_null_claim_is_malformed(self):
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM] = None
        self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_01_null_in_every_text_field_is_malformed(self):
        for field in (ns.P_ID, ns.P_RECORD, ns.P_DOI, ns.P_FILE, ns.P_CLAIM):
            p = page()
            p["properties"][field] = None
            self.assert_rejected(p, field)

    # R21-02: missing text.content vs explicit null / wrong type
    def test_r21_02_null_content_next_to_plain_text_is_malformed(self):
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM]["rich_text"] = [{"type": "text", "plain_text": SHA, "text": {"content": None}}]
        self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_02_text_object_without_content_is_malformed(self):
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM]["rich_text"] = [{"type": "text", "plain_text": SHA, "text": {}}]
        self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_02_non_object_text_is_malformed(self):
        for bad in (None, "x", 1, []):
            p = page(claim=SHA)
            p["properties"][ns.P_CLAIM]["rich_text"] = [{"type": "text", "plain_text": SHA, "text": bad}]
            self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_02_plain_text_without_text_key_still_decodes(self):
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM]["rich_text"] = [{"type": "mention", "plain_text": SHA, "mention": {}}]
        self.assertEqual(self.assert_accepted(p)[0]["files"][0]["sha256"], SHA)

    # R21-03: container must be the expected one, unambiguous, and consistent with "type"
    def test_r21_03_both_containers_rejected(self):
        p = page()
        p["properties"][ns.P_ID] = {"title": [{"plain_text": "demo-set"}], "rich_text": [{"plain_text": "demo-set"}]}
        self.assert_rejected(p, ns.P_ID)
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM] = {"rich_text": [{"plain_text": SHA}], "title": []}
        self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_03_wrong_container_for_field_rejected(self):
        p = page()
        p["properties"][ns.P_ID] = {"rich_text": [{"plain_text": "demo-set"}]}
        self.assert_rejected(p, ns.P_ID)
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM] = {"title": [{"plain_text": SHA}]}
        self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_03_type_contradicting_container_rejected(self):
        p = page()
        p["properties"][ns.P_ID] = {"type": "rich_text", "title": [{"plain_text": "demo-set"}]}
        self.assert_rejected(p, ns.P_ID)
        p = page(claim=SHA)
        p["properties"][ns.P_CLAIM] = {"type": "number", "rich_text": [{"plain_text": SHA}]}
        self.assert_rejected(p, ns.P_CLAIM)

    def test_r21_03_rest_shape_with_matching_type_accepted(self):
        p = page(claim=SHA)
        p["properties"][ns.P_ID] = {"id": "title", "type": "title", "title": [
            {"type": "text", "text": {"content": "demo-set", "link": None}, "plain_text": "demo-set",
             "annotations": {}, "href": None}]}
        p["properties"][ns.P_CLAIM] = {"id": "abc", "type": "rich_text", "rich_text": [
            {"type": "text", "text": {"content": SHA, "link": None}, "plain_text": SHA}]}
        entries = self.assert_accepted(p)
        self.assertEqual((entries[0]["dataset_id"], entries[0]["files"][0]["sha256"]), ("demo-set", SHA))

    def test_r21_03_container_value_not_a_list_rejected(self):
        for bad in (None, {}, "demo-set"):
            p = page()
            p["properties"][ns.P_ID] = {"title": bad}
            self.assert_rejected(p, ns.P_ID)
