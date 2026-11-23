"""Invariants of every committed QA report: aggregate-only and epistemically separated."""
import json
from pathlib import Path

import pytest

REPORTS = sorted((Path(__file__).resolve().parent.parent / "reports").glob("*.qa.json"))


def _walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield path + "/" + str(k), "key", k
            yield from _walk(v, path + "/" + str(k))
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, path + "[]")
    else:
        yield path, "value", o


def test_there_is_a_committed_report():
    assert REPORTS


@pytest.mark.parametrize("path", REPORTS, ids=lambda p: p.name)
def test_report_keeps_observation_inference_and_uncertainty_apart(path):
    r = json.loads(path.read_text(encoding="utf-8"))
    ev = r["evidence_levels"]
    assert set(ev) == {"facts", "heuristic_hits", "unverified_assumptions"}
    assert all(isinstance(ev[k], list) and ev[k] for k in ev)
    assert "reidentification_risk" in " ".join(ev["heuristic_hits"])      # estimates are not facts
    assert "reidentification_risk" not in " ".join(ev["facts"])
    assert "not certainty" in json.dumps(r["reidentification_risk"]).lower() \
        or "estimate" in json.dumps(r["reidentification_risk"]).lower()


@pytest.mark.parametrize("path", REPORTS, ids=lambda p: p.name)
def test_report_is_aggregate_only_and_integrity_recorded(path):
    r = json.loads(path.read_text(encoding="utf-8"))
    assert r["input"]["unchanged_during_analysis"] is True
    assert r["input"]["sha256"] == r["input"]["sha256_after"]
    for p, kind, v in _walk(r):
        if kind == "value" and isinstance(v, str):
            assert len(v) <= 400, p                       # no free-text passages
        if kind == "key":
            assert not (str(v).isdigit() and len(str(v)) >= 4 and "plz" in p.lower()), p  # no postal codes as keys
    text = path.read_text(encoding="utf-8")
    assert "record_id" not in text.lower().replace("record_ids_withheld", "")
