import json
from pathlib import Path

REG = Path(__file__).resolve().parents[1] / "data" / "manifest" / "external-sources.json"


def _load():
    return json.loads(REG.read_text(encoding="utf-8"))


def test_register_is_metadata_only_and_never_a_licence_pass():
    doc = _load()
    ids = [s["source_id"] for s in doc["sources"]]
    assert len(ids) == len(set(ids)) and ids
    for s in doc["sources"]:
        assert s["licence_status"] in {"unverified", "restricted"}
        assert s["retrieval"] == "not downloaded"
        assert s["sha256"] is None
        assert s["url"].startswith("https://")
        assert s["stated_licence"] and s["stated_contents"]


def test_excluded_source_not_listed():
    doc = _load()
    for name in doc["excluded_by_project_policy"]:
        assert all(name.lower() not in s["title"].lower() and name.lower() not in s["host"].lower()
                   for s in doc["sources"])


def test_nuforc_entries_are_restricted():
    for s in _load()["sources"]:
        if "nuforc" in s["source_id"]:
            assert s["licence_status"] == "restricted"
