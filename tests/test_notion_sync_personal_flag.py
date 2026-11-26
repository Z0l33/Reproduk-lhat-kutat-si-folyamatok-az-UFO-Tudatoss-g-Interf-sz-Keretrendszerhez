"""Regression tests: the 'Contains personal data' flag must fail closed (plan v1.4, D1-D3, A-01..A-06, V13-01..V13-03).

Synthetic rows only; no network, no real token. Written BEFORE the fix: on the unfixed code the tests marked
E in the plan must fail; after the fix every test here must pass.
"""
import json

import pytest

from ufo_pipeline import notion_sync as ns
from tests.test_notion_sync import page
from tests.test_notion_sync_negative import DB, FAKE_TOKEN, ok, run_cli, nothing_persisted

MISSING_MSG = "required Notion property missing: Contains personal data; row rejected"
TYPE_MSG = "unexpected type in Notion property: Contains personal data; row rejected"
ABSENT = object()                     # marker: remove the property key entirely

BAD_FLAGS = {                         # shape -> expected rejection message
    "key absent": (ABSENT, MISSING_MSG),
    "explicit null": (None, TYPE_MSG),                                        # A-01: present but null = malformed
    "select-typed": ({"type": "select", "select": {"name": "Yes"}}, TYPE_MSG),
    "checkbox type, no value": ({"type": "checkbox"}, TYPE_MSG),
    "checkbox value null": ({"checkbox": None}, TYPE_MSG),
    "rich_text-typed": ({"type": "rich_text", "rich_text": [{"plain_text": "yes"}]}, TYPE_MSG),
    # V13-03: falsy non-bool values and a wrong / null type next to a valid-looking checkbox value
    "checkbox value 0": ({"checkbox": 0}, TYPE_MSG),
    "checkbox value empty string": ({"checkbox": ""}, TYPE_MSG),
    "select type with checkbox false": ({"type": "select", "checkbox": False}, TYPE_MSG),
    "null type with checkbox false": ({"type": None, "checkbox": False}, TYPE_MSG),
}


def with_flag(flag, **kw):
    p = page(**kw)
    if flag is ABSENT:
        p["properties"].pop(ns.P_PERSONAL)
    else:
        p["properties"][ns.P_PERSONAL] = flag
    return p


def id_less(p):
    p.pop("id")
    return p


class Rec:
    """Fake Notion client + retriever + metadata checker that record every effect."""

    def __init__(self, pages):
        self.pages, self.updated, self.downloads, self.meta = pages, [], [], []

    def query_submitted(self, database_id):
        return self.pages

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties[ns.P_STATUS]["select"]["name"]))

    def retriever(self, entry, dest, existing=None):
        self.downloads.append(entry["dataset_id"])
        return {"status": "verified", "files": [], "retrieved_at": "2026-01-01T00:00:00Z"}

    def checker(self, entry):
        self.meta.append(entry["dataset_id"])
        return {"status": "ok"}


def run_sync(pages, apply):
    rec = Rec(pages)
    report = ns.sync(rec, DB, "raw", "prov", rec.retriever, apply=apply, blocked={}, metadata_checker=rec.checker)
    return rec, report


# ---- T1: bad flag never reaches download or metadata check; dry run writes nothing, apply writes only failed to own id
@pytest.mark.parametrize("shape", sorted(BAD_FLAGS))
@pytest.mark.parametrize("apply", [False, True])
def test_t1_bad_flag_is_rejected_in_sync(shape, apply):
    rec, report = run_sync([with_flag(BAD_FLAGS[shape][0])], apply)
    assert rec.downloads == [] and rec.meta == [] and nothing_persisted()
    assert list(report["rejected"]) == ["p1"]
    if apply:
        assert rec.updated == [("p1", "failed")]          # A-03: exactly one write, failed, own real page id
    else:
        assert rec.updated == []                          # A-03: dry run writes nothing at all


# ---- T2: exact rejection reason, value never echoed
@pytest.mark.parametrize("shape", sorted(BAD_FLAGS))
def test_t2_exact_rejection_reason(shape):
    flag, msg = BAD_FLAGS[shape]
    entries, problems = ns.rows_to_entries([ns.parse_row(with_flag(flag))], {})
    assert entries == [] and problems == {"p1": [msg]}


# ---- T3: through the CLI with --apply: no download, one PATCH with 'failed', no value or token in any output
def test_t3_cli_apply_rejects_without_leaking_values(monkeypatch, capsys):
    secret = "SECRET-FLAG-VALUE-424242"
    reply = ok({"results": [with_flag({"type": "select", "select": {"name": secret}})], "has_more": False})
    sent = []

    def urlopen(req, timeout=None):
        sent.append((req.get_method(), req.full_url, (req.data or b"").decode("utf-8")))
        return reply if len(sent) == 1 else ok({})

    code, out, err = run_cli(monkeypatch, urlopen, capsys, "--apply")
    assert code == 1 and secret not in out + err and FAKE_TOKEN not in out + err
    assert [m for m, _, _ in sent] == ["POST", "PATCH"] and sent[1][1].endswith("/pages/p1")
    body = json.loads(sent[1][2])
    assert body["properties"][ns.P_STATUS] == {"select": {"name": "failed"}} and secret not in sent[1][2]
    assert all("zenodo" not in url for _, url, _ in sent) and nothing_persisted()


# ---- T4: the real Notion API shape keeps working
@pytest.mark.parametrize("flag,accepted", [(False, True), (True, False)])
def test_t4_real_api_shape(flag, accepted):
    real = {"id": "%3E", "type": "checkbox", "checkbox": flag}
    entries, problems = ns.rows_to_entries([ns.parse_row(with_flag(real))], {})
    if accepted:
        assert [e["dataset_id"] for e in entries] == ["demo-set"] and problems == {}
    else:
        assert entries == [] and problems == {"p1": ["row is flagged as containing personal data; rejected"]}


# ---- T5: a row that is not submitted is left alone whatever its flag looks like
@pytest.mark.parametrize("shape", ["key absent", "select-typed"])
def test_t5_non_submitted_row_is_left_alone(shape):
    rec, report = run_sync([with_flag(BAD_FLAGS[shape][0], status="verified")], apply=True)
    assert rec.updated == [] and rec.downloads == [] and report["rejected"] == {}


# ---- T6: truthy non-bool values are rejected as malformed, not by accident through bool()
@pytest.mark.parametrize("flag", [1, "false"], ids=["int-1", "string-false"])
def test_t6_non_bool_checkbox_value_is_malformed(flag):
    entries, problems = ns.rows_to_entries([ns.parse_row(with_flag({"checkbox": flag}))], {})
    assert entries == [] and problems == {"p1": [TYPE_MSG]}


# ---- T7: one bad row of a dataset is rejected on its own; the good row of the same dataset keeps the existing rule
def test_t7_one_bad_row_in_a_multi_row_dataset():
    good = page("pa", ds="multi", name="a.csv")
    bad = with_flag(ABSENT, pid="pb", ds="multi", name="b.csv")
    entries, problems = ns.rows_to_entries([ns.parse_row(good), ns.parse_row(bad)], {})
    assert problems == {"pb": [MISSING_MSG]}
    assert [[f["name"] for f in e["files"]] for e in entries] == [["a.csv"]]


# ---- T8 (A-06): precedence collisions follow plan section 2.2 exactly
def _status_bad(p):
    p["properties"][ns.P_STATUS] = "submitted"
    return p


def _decision_bad(p):
    p["properties"][ns.P_DECISION] = "pending"
    return p


COLLISIONS = {
    "unidentified + missing flag": (
        lambda: id_less(with_flag(ABSENT)), "unidentified-row-1", ["page has no usable page id; row rejected"]),
    "unidentified + malformed flag": (
        lambda: id_less(with_flag({"type": "select", "select": None})), "unidentified-row-1",
        ["page has no usable page id; unexpected type in: Contains personal data; row rejected"]),
    "page not an object": (lambda: None, "unidentified-row-1", ["page is not an object; row rejected"]),
    "malformed status + missing flag": (
        lambda: _status_bad(with_flag(ABSENT)), "p1", ["unexpected type in Notion property: Pipeline status; row rejected"]),
    "malformed decision + missing flag": (
        lambda: _decision_bad(with_flag(ABSENT)), "p1",
        ["unexpected type in Notion property: Maintainer decision; row rejected"]),
    "malformed decision + malformed flag": (
        lambda: _decision_bad(with_flag(None)), "p1",
        ["unexpected type in Notion properties: Contains personal data, Maintainer decision; row rejected"]),
    "properties not an object": (
        lambda: {"id": "p1", "properties": ["x"]}, "p1", ["unexpected type in Notion property: properties; row rejected"]),
    "not submitted + missing flag": (lambda: with_flag(ABSENT, status="verified"), None, None),
    "not submitted + malformed flag": (lambda: with_flag(None, status="verified"), None, None),
}


@pytest.mark.parametrize("name", sorted(COLLISIONS))
def test_t8_precedence(name):
    make, key, msg = COLLISIONS[name]
    entries, problems = ns.rows_to_entries([ns.parse_row(make())], {})
    assert entries == []
    assert problems == ({} if key is None else {key: msg})


def test_t8_precedence_through_sync_writes_failed_only_to_real_ids():
    pages = [id_less(with_flag(ABSENT)), None, _status_bad(with_flag(ABSENT, pid="pd")),
             _decision_bad(with_flag(ABSENT, pid="pe")), with_flag(ABSENT, pid="pg", status="verified")]
    rec, report = run_sync(pages, apply=True)
    assert sorted(rec.updated) == [("pd", "failed"), ("pe", "failed")]
    assert rec.downloads == [] and rec.meta == [] and nothing_persisted()
    assert sorted(report["rejected"]) == ["pd", "pe", "unidentified-row-1", "unidentified-row-2"]
