"""Negative tests for the existing notion_sync integration (no network, no real token, synthetic rows only).

Nothing here proves that a real NOTION_TOKEN exists or works, or that the live Notion API behaves as the fakes do.
Every test checks the safety invariant: on a bad token, missing access, API failure or malformed row, nothing is
downloaded, nothing is written to Notion and no provenance file is created; the token never appears in output.
"""
import io
import json
import socket
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from ufo_pipeline import cli
from ufo_pipeline import notion_sync as ns
from tests.test_notion_sync import page

FAKE_TOKEN = "secret_FAKE_TEST_TOKEN_0123456789"      # not a credential; used to prove it never leaks
DB = "0123456789abcdef0123456789abcdef"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(payload):
    return FakeResponse(json.dumps(payload).encode("utf-8"))


def http_error(code, msg="err"):
    return urllib.error.HTTPError(f"{ns.API}/x", code, msg, {}, io.BytesIO(b'{"message":"detail"}'))


def run_cli(monkeypatch, urlopen, capsys, *extra, token=FAKE_TOKEN):
    if token is None:
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
    else:
        monkeypatch.setenv("NOTION_TOKEN", token)
    with mock.patch("urllib.request.urlopen", urlopen):
        code = cli.main(["notion-sync", "--database-id", DB, "--dest", "raw", "--provenance-dir", "prov", *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


def nothing_persisted():
    return not Path("prov").exists() and not Path("raw").exists()


# ---- missing / invalid token ----
def test_missing_token_stops_before_any_request(monkeypatch, capsys):
    urlopen = mock.Mock()
    code, out, err = run_cli(monkeypatch, urlopen, capsys, token=None)
    assert code == 2 and "NOTION_TOKEN is not set" in err and out == ""
    urlopen.assert_not_called()
    assert nothing_persisted()


def test_empty_token_is_treated_as_missing(monkeypatch, capsys):
    urlopen = mock.Mock()
    code, _, err = run_cli(monkeypatch, urlopen, capsys, token="")
    assert code == 2 and "NOTION_TOKEN is not set" in err
    urlopen.assert_not_called()


@pytest.mark.parametrize("status,reason", [(401, "Unauthorized"), (403, "Forbidden"), (404, "Not Found")])
@pytest.mark.parametrize("apply", [False, True])
def test_auth_and_access_errors_fail_closed_and_do_not_leak_the_token(monkeypatch, capsys, status, reason, apply):
    """401 = bad token; 403/404 = integration has no access to the database (Notion answers 404 for both)."""
    urlopen = mock.Mock(side_effect=http_error(status, reason))
    code, out, err = run_cli(monkeypatch, urlopen, capsys, *(["--apply"] if apply else []))
    assert code == 2 and f"HTTP Error {status}" in err
    assert FAKE_TOKEN not in out + err and out == ""
    assert urlopen.call_count == 1                      # no retry loop
    assert nothing_persisted()


@pytest.mark.parametrize("failure", [http_error(429, "Too Many Requests"), http_error(500, "Server Error"),
                                     urllib.error.URLError("dns failure"), socket.timeout("timed out"),
                                     ConnectionResetError("reset")])
def test_transport_and_rate_limit_failures_fail_closed(monkeypatch, capsys, failure):
    urlopen = mock.Mock(side_effect=failure)
    code, out, err = run_cli(monkeypatch, urlopen, capsys, "--apply")
    assert code == 2 and out == "" and FAKE_TOKEN not in err
    assert urlopen.call_count == 1
    assert nothing_persisted()


@pytest.mark.parametrize("body", [b"", b"<html>gateway</html>", b"[1, 2", b"\xff\xfe"])
def test_unparsable_api_response_fails_closed(monkeypatch, capsys, body):
    code, out, err = run_cli(monkeypatch, mock.Mock(return_value=FakeResponse(body)), capsys, "--apply")
    assert code == 2 and out == "" and FAKE_TOKEN not in err
    assert nothing_persisted()


def test_token_is_sent_only_to_the_fixed_notion_host(monkeypatch, capsys):
    urlopen = mock.Mock(return_value=ok({"results": [], "has_more": False}))
    code, out, _ = run_cli(monkeypatch, urlopen, capsys)
    assert code == 0 and FAKE_TOKEN not in out
    req = urlopen.call_args[0][0]
    assert req.full_url.startswith("https://api.notion.com/v1/databases/") and req.get_method() == "POST"
    assert req.get_header("Authorization") == f"Bearer {FAKE_TOKEN}"
    assert json.loads(req.data)["filter"]["select"]["equals"] == "submitted"   # only submitted rows are requested


def test_empty_database_is_a_successful_empty_run_not_a_verification(monkeypatch, capsys):
    code, out, _ = run_cli(monkeypatch, mock.Mock(return_value=ok({"results": [], "has_more": False})), capsys)
    report = json.loads(out)
    assert code == 0 and report["rows"] == 0 and report["datasets"] == {} and report["applied"] is False


# ---- a reply claiming has_more without a cursor must not make the client resend the query ----
class _StopAfter(Exception):
    pass


@pytest.mark.parametrize("cursor", [None, "", 0, ["c"]])
def test_has_more_without_cursor_fails_after_one_request(cursor):
    calls = []

    def urlopen(req, timeout=None):
        calls.append(1)
        if len(calls) > 5:
            raise _StopAfter()
        return ok({"results": [], "has_more": True, "next_cursor": cursor})

    with mock.patch("urllib.request.urlopen", urlopen):
        with pytest.raises(ValueError, match="next_cursor"):
            ns.UrllibNotionClient(FAKE_TOKEN).query_submitted(DB)
    assert len(calls) == 1


def test_has_more_without_cursor_through_the_cli_writes_nothing(monkeypatch, capsys):
    urlopen = mock.Mock(return_value=ok({"results": [page()], "has_more": True}))
    code, out, err = run_cli(monkeypatch, urlopen, capsys, "--apply")
    assert code == 2 and out == "" and "next_cursor" in err and FAKE_TOKEN not in err
    assert urlopen.call_count == 1 and nothing_persisted()


def test_pagination_follows_the_cursor_and_collects_all_pages():
    replies = iter([ok({"results": [{"id": "a"}], "has_more": True, "next_cursor": "c1"}),
                    ok({"results": [{"id": "b"}], "has_more": False})])
    seen = []

    def urlopen(req, timeout=None):
        seen.append(json.loads(req.data).get("start_cursor"))
        return next(replies)

    with mock.patch("urllib.request.urlopen", urlopen):
        assert [p["id"] for p in ns.UrllibNotionClient(FAKE_TOKEN).query_submitted(DB)] == ["a", "b"]
    assert seen == [None, "c1"]


# ---- unknown fields and deliberately malformed rows ----
class Recorder:
    def __init__(self, pages):
        self.pages, self.updated, self.downloads = pages, [], []

    def query_submitted(self, database_id):
        return self.pages

    def update_page(self, page_id, properties):
        self.updated.append(page_id)

    def retriever(self, entry, dest, existing=None):
        self.downloads.append(entry["dataset_id"])
        raise AssertionError("a malformed row must never reach the retriever")


def sync_rows(pages, apply=True):
    rec = Recorder(pages)
    try:
        ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=apply)
    except AssertionError:
        raise
    except Exception:           # fail closed is acceptable; the invariant below is what matters
        pass
    return rec


def mutated(fn):
    p = page()
    fn(p)
    return p


MALFORMED = {
    "status is a bare string": lambda p: p["properties"].__setitem__(ns.P_STATUS, "submitted"),
    "status select is null": lambda p: p["properties"].__setitem__(ns.P_STATUS, {"select": None}),
    "properties is null": lambda p: p.__setitem__("properties", None),
    "title is a bare string": lambda p: p["properties"].__setitem__(ns.P_ID, "demo-set"),
    "dataset id missing": lambda p: p["properties"].pop(ns.P_ID),
    "file name missing": lambda p: p["properties"].pop(ns.P_FILE),
    "personal-data flag as text": lambda p: p["properties"].__setitem__(ns.P_PERSONAL, {"checkbox": "false"}),
    "personal-data flag is a string property": lambda p: p["properties"].__setitem__(ns.P_PERSONAL, "yes"),
    "record id wrong": lambda p: p["properties"].__setitem__(ns.P_RECORD, {"rich_text": [{"plain_text": "999"}]}),
}


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_malformed_row_never_downloads_and_never_writes_a_verified_state(name):
    page_ = mutated(MALFORMED[name])
    rec = sync_rows([page_])
    assert rec.downloads == []
    # whatever happens to a bad row, Notion is only ever told "failed" or nothing, never a verified state
    for upd in rec.updated:
        assert upd == "p1"


@pytest.mark.parametrize("name", ["dataset id missing", "file name missing", "record id wrong", "title is a bare string",
                                  "personal-data flag as text"])
def test_rejected_rows_are_reported_with_a_reason(name):
    entries, problems = ns.rows_to_entries([ns.parse_row(mutated(MALFORMED[name]))])
    assert entries == [] and problems.get("p1")


def test_unknown_extra_property_is_ignored_and_changes_nothing():
    base = ns.rows_to_entries([ns.parse_row(page())])
    extra = ns.rows_to_entries([ns.parse_row(mutated(lambda p: p["properties"].__setitem__("Surprise", {"x": 1})))])
    assert base == extra


def test_row_in_a_state_other_than_submitted_is_ignored_even_with_a_valid_claim():
    for state in ("verified", "verified_pin_only", "unpinned", "failed", None):
        p = page(status=state or "x", claim="a" * 64)
        if state is None:
            p["properties"][ns.P_STATUS] = {"select": None}
        assert ns.rows_to_entries([ns.parse_row(p)]) == ([], {})


@pytest.mark.parametrize("name", ["status is a bare string", "personal-data flag is a string property"])
def test_wrongly_typed_property_should_be_rejected_not_crash(name):
    """A property of the wrong JSON type is flagged as malformed and the row is rejected with a reason."""
    row = ns.parse_row(mutated(MALFORMED[name]))
    assert row["malformed"]
    entries, problems = ns.rows_to_entries([{**row, "status": "submitted"}])
    assert entries == [] and "unexpected type" in problems["p1"][0]


def test_non_dict_page_should_be_rejected_not_crash():
    for bad in (None, "page", 5, [], [{"id": "p"}]):
        row = ns.parse_row(bad)
        assert row["page_id"] is None and row["status"] is None and row["malformed"] == ["page"]
        entries, problems = ns.rows_to_entries([row])
        assert entries == [] and problems == {"unidentified-row-1": ["page is not an object; row rejected"]}


def test_properties_of_the_wrong_container_type_are_rejected():
    for bad in (["x"], "text", 5):      # falsy containers ([], "", 0) count as "no properties" and are ignored
        row = ns.parse_row({"id": "p1", "properties": bad})
        assert row["malformed"] == ["properties"]
        entries, problems = ns.rows_to_entries([row])
        assert entries == [] and "p1" in problems


@pytest.mark.parametrize("name", ["status is a bare string", "personal-data flag is a string property", "properties is null"])
@pytest.mark.parametrize("apply", [False, True])
def test_malformed_rows_through_sync_download_nothing_and_write_only_failed(name, apply):
    rec = Recorder([mutated(MALFORMED[name])])
    report = ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=apply)
    assert rec.downloads == [] and report["datasets"] == {} and not Path("prov").exists()
    assert report["applied"] is apply and (apply or rec.updated == [])
    assert set(rec.updated) <= {"p1"}


def test_garbage_pages_never_cause_a_write_to_a_made_up_page_id():
    rec = Recorder([None, "page", {"properties": {ns.P_STATUS: "submitted"}}])   # no usable page id at all
    report = ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=True)
    assert rec.updated == [] and rec.downloads == [] and not Path("prov").exists()
    assert report["rows"] == 3


def test_malformed_property_on_a_non_submitted_row_is_left_alone():
    bad = page(status="verified")
    bad["properties"][ns.P_PERSONAL] = "yes"
    rec = Recorder([bad])
    ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=True)
    assert rec.updated == [] and rec.downloads == []         # a verified row is never overwritten with "failed"


def test_well_formed_rows_parse_exactly_as_before():
    row = ns.parse_row(page(claim="a" * 64))
    assert row["malformed"] == [] and row["status"] == "submitted" and row["decision"] == "pending"
    assert row["personal_data"] is False and row["claimed_sha256"] == "a" * 64


def test_valid_claim_must_be_exact_and_personal_rows_never_reach_the_retriever():
    rec = sync_rows([page("p1", personal=True), page("p2", ds="other", claim="A" * 64)])
    assert rec.downloads == [] and set(rec.updated) == {"p1", "p2"}


# ---- workflow guard rails (static) ----
def test_workflow_is_manual_dry_run_by_default_and_never_prints_the_token():
    text = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "notion-sync.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text and "pull_request" not in text and "push:" not in text and "schedule" not in text
    assert "default: false" in text and "secrets.NOTION_TOKEN" in text
    assert text.count("secrets.NOTION_TOKEN") == 1 and "echo" not in text
    assert "contents: read" in text


# ---- several unidentified pages arriving together: each is rejected separately and nothing is written to them ----
def id_less(**kw):
    p = page(**kw)
    p.pop("id")
    return p


def test_each_unidentified_page_gets_its_own_reason_keyed_by_position():
    pages = [None, page("good", ds="ok-set", name="a.csv"), "text", id_less(ds="no-id-set"), {"properties": {ns.P_STATUS: "submitted"}},
             page("p6", ds="x", personal=True)]
    entries, problems = ns.rows_to_entries([ns.parse_row(p) for p in pages])
    assert sorted(k for k in problems if k.startswith("unidentified-row-")) == [
        "unidentified-row-1", "unidentified-row-3", "unidentified-row-4", "unidentified-row-5"]   # 1-based positions
    assert "page is not an object" in problems["unidentified-row-1"][0]
    assert "page is not an object" in problems["unidentified-row-3"][0]
    assert problems["unidentified-row-4"] == ["page has no usable page id; row rejected"]
    assert "unexpected type in: Pipeline status" in problems["unidentified-row-5"][0]
    assert "p6" in problems and "good" not in problems and [e["dataset_id"] for e in entries] == ["ok-set"]
    assert len({tuple(v) for k, v in problems.items() if k.startswith("unidentified")}) == 3   # distinct reasons, none merged


@pytest.mark.parametrize("apply", [False, True])
def test_unidentified_pages_never_get_a_notion_write_or_a_download(apply):
    pages = [None, id_less(), id_less(ds="second"), "text", {"properties": None}]
    rec = Recorder(pages)
    report = ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=apply)
    assert rec.updated == [] and rec.downloads == [] and not Path("prov").exists()
    assert report["rows"] == 5 and report["applied"] is apply
    assert sorted(report["rejected"]) == [f"unidentified-row-{n}" for n in (1, 2, 3, 4, 5)]   # every id-less page is rejected
    json.dumps(report)                                                                      # report stays serialisable


def test_a_real_page_id_that_imitates_a_generated_key_is_treated_as_unidentified():
    pages = [page("unidentified-row-1"), page("unidentified-row-1", ds="y")]
    rec = Recorder(pages)
    report = ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=True)
    assert rec.updated == [] and rec.downloads == []
    assert sorted(report["rejected"]) == ["unidentified-row-1", "unidentified-row-2"]


def test_report_and_output_contain_no_property_values_or_token(monkeypatch, capsys):
    secret = "SECRET-VALUE-doi-10.5281-zenodo.424242"
    bad = id_less(ds="no-id", doi=secret, name=secret + ".csv", claim="b" * 64)
    bad["properties"][ns.P_PERSONAL] = secret                      # wrong type, value must not be echoed
    urlopen = mock.Mock(return_value=ok({"results": [bad, None, "x"], "has_more": False}))
    code, out, err = run_cli(monkeypatch, urlopen, capsys, "--apply")
    assert code == 1 and secret not in out + err and "b" * 64 not in out + err and FAKE_TOKEN not in out + err
    report = json.loads(out)
    assert sorted(report["rejected"]) == ["unidentified-row-1", "unidentified-row-2", "unidentified-row-3"]
    assert urlopen.call_count == 1                                 # the query only; no PATCH was sent
    assert nothing_persisted()


def test_submitted_row_without_an_id_is_rejected_not_downloaded():
    rec = Recorder([id_less(claim="a" * 64)])
    report = ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=True)
    assert rec.downloads == [] and rec.updated == [] and report["datasets"] == {}
    assert list(report["rejected"]) == ["unidentified-row-1"]


def test_identified_rows_keep_their_page_id_keys_and_processing():
    pages = [page("p1", ds="one", name="a.csv"), page("p2", ds="two", personal=True)]
    entries, problems = ns.rows_to_entries([ns.parse_row(p) for p in pages])
    assert [e["dataset_id"] for e in entries] == ["one"] and list(problems) == ["p2"]
    assert ns.row_keys([ns.parse_row(p) for p in pages]) == ["p1", "p2"]


# ---- retrieval blocks from the repository manifest (audit finding M1) ----
@pytest.mark.parametrize("extra", [(), ("--apply",)])
@pytest.mark.parametrize("rec_id, ds", [("23906585", "any-name"), ("18076716", "x"), ("999", "gep-ufo-uap-2026-10")])
def test_cli_rejects_rows_for_manifest_blocked_records_without_any_zenodo_call(monkeypatch, capsys, extra, rec_id, ds):
    row = page("p1", ds=ds, rec=rec_id, doi=f"10.5281/zenodo.{rec_id}", name="f.csv")
    calls = []

    def urlopen(req, timeout=None):
        url = req.full_url
        calls.append((req.get_method(), url))
        if "zenodo.org" in url:
            raise AssertionError("a blocked row must never reach Zenodo")
        if url.endswith("/query"):
            return ok({"results": [row], "has_more": False})
        return ok({})                                   # PATCH of the rejection (apply only)

    code, out, err = run_cli(monkeypatch, urlopen, capsys, *extra)
    report = json.loads(out)
    assert code == 1 and "retrieval_blocked" in report["rejected"]["p1"][0]
    assert report["datasets"] == {} and report["downloaded"] is False
    assert not any("zenodo.org" in u for _, u in calls) and nothing_persisted()
    patches = [c for c in calls if c[0] == "PATCH"]
    assert len(patches) == (1 if extra else 0)          # dry run writes nothing to Notion


def test_cli_fails_closed_when_the_manifest_is_missing(monkeypatch, capsys):
    urlopen = mock.Mock(side_effect=AssertionError("Notion must not be queried without the manifest"))
    code, out, err = run_cli(monkeypatch, urlopen, capsys, "--manifest", "no/such/manifest.json")
    assert code == 2 and urlopen.call_count == 0 and nothing_persisted()


def test_dry_run_through_cli_reads_metadata_only_and_downloads_nothing(monkeypatch, capsys):
    row = page("p1", ds="open-set", rec="555", doi="10.5281/zenodo.555", name="f.csv")
    calls = []
    record = {"id": 555, "doi": "10.5281/zenodo.555",
              "files": [{"key": "f.csv", "size": 3, "checksum": "md5:" + "0" * 32,
                         "links": {"self": "https://zenodo.org/api/records/555/files/f.csv/content"}}]}

    def urlopen(req, timeout=None):
        url = req.full_url
        calls.append((req.get_method(), url))
        if url == "https://zenodo.org/api/records/555":
            return ok(record)
        if url.endswith("/query"):
            return ok({"results": [row], "has_more": False})
        raise AssertionError(f"unexpected request in dry run: {req.get_method()} {url}")

    code, out, err = run_cli(monkeypatch, urlopen, capsys)
    report = json.loads(out)
    assert code == 0 and report["mode"] == "dry_run_metadata_only" and report["applied"] is False
    assert report["datasets"] == {"open-set": "metadata_ok"} and report["downloaded"] is False
    assert [m for m, _ in calls] == ["POST", "GET"]     # Notion query + record metadata, nothing else
    assert nothing_persisted()


@pytest.mark.parametrize("rec_id", ["023906585", "0023906585", "018076716"])
@pytest.mark.parametrize("extra", [(), ("--apply",)])
def test_leading_zero_spellings_of_a_blocked_record_never_reach_zenodo(monkeypatch, capsys, rec_id, extra):
    row = page("p1", ds="x", rec=rec_id, doi=f"10.5281/zenodo.{rec_id}", name="f.csv")
    calls = []

    def urlopen(req, timeout=None):
        calls.append(req.full_url)
        if "zenodo.org" in req.full_url:
            raise AssertionError("a blocked record must never reach Zenodo, whatever its spelling")
        return ok({"results": [row], "has_more": False} if req.full_url.endswith("/query") else {})

    code, out, err = run_cli(monkeypatch, urlopen, capsys, *extra)
    report = json.loads(out)
    assert code == 1 and "retrieval_blocked" in report["rejected"]["p1"][0]
    assert not any("zenodo.org" in u for u in calls) and nothing_persisted()


def test_an_extra_manifest_cannot_remove_the_repository_blocks(monkeypatch, capsys, tmp_path):
    open_manifest = tmp_path / "open.json"
    open_manifest.write_text(json.dumps({"schema_version": 1, "datasets": [{
        "dataset_id": "gep-ufo-uap-2026-10", "record_id": "23906585", "doi": "10.5281/zenodo.23906585",
        "files": [{"name": "f.csv"}]}]}), encoding="utf-8")                 # same record, NOT blocked
    row = page("p1", ds="x", rec="23906585", doi="10.5281/zenodo.23906585", name="f.csv")

    def urlopen(req, timeout=None):
        if "zenodo.org" in req.full_url:
            raise AssertionError("an extra manifest must not lift a repository block")
        return ok({"results": [row], "has_more": False} if req.full_url.endswith("/query") else {})

    code, out, err = run_cli(monkeypatch, urlopen, capsys, "--manifest", str(open_manifest))
    assert code == 1 and "retrieval_blocked" in json.loads(out)["rejected"]["p1"][0]


def test_record_ids_are_compared_in_one_canonical_spelling():
    assert ns.canonical_record_id("023906585") == "23906585" and ns.canonical_record_id(" 23906585 ") == "23906585"
    assert ns.canonical_record_id("0") == "0" and ns.canonical_record_id("00") == "0"
    assert ns.canonical_record_id("２３９０６５８５") == "２３９０６５８５"   # non-ASCII digits: unchanged, fail validation later
    blocked = ns.blocked_targets([{"dataset_id": "d", "record_id": "023906585", "retrieval_blocked": True,
                                   "retrieval_blocked_reason": "r"}])
    assert ("record", "23906585") in blocked
