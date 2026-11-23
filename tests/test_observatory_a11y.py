"""Accessibility report: result categories, status derivation, honesty rules, mutation checks, metadata separation."""
import copy
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

OBS = Path(__file__).resolve().parent.parent / "observatory"
spec = importlib.util.spec_from_file_location("a11y_report", OBS / "a11y_report.py")
a11y = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a11y)

HTML = (OBS / "index.html").read_text(encoding="utf-8")
COMMITTED = json.loads((OBS / "a11y-report.json").read_text(encoding="utf-8"))


def passing(i="x"):
    return {"id": i, "title": "t", "scope": "s", "status": a11y.PASS, "evidence": {"test": "check_x", "proved": "something"}}


# 1. the three categories are listed, and only those
def test_report_lists_exactly_the_three_categories():
    assert a11y.STATUSES == ("PASS", "MANUAL REQUIRED", "NOT VERIFIED")
    content = COMMITTED["content"]
    assert set(content["counts"]) == set(a11y.STATUSES)
    assert {i["status"] for i in content["items"]} == set(a11y.STATUSES)        # each category is really used
    assert sum(content["counts"].values()) == len(content["items"])
    for i in content["items"]:
        if i["status"] == a11y.MANUAL:
            assert i["needed_check"] and i["done"] is False                      # what is needed, and not yet done
        if i["status"] == a11y.NOTVER:
            assert i["reason"]


# 2. overall status derivation
def test_overall_status_derivation():
    man = a11y.normalize_item({"id": "m", "title": "t", "scope": "s", "status": a11y.MANUAL, "needed_check": "listen", "done": False})
    nv = a11y.normalize_item({"id": "n", "title": "t", "scope": "s", "status": a11y.NOTVER, "reason": "none"})
    assert a11y.derive_overall([passing()])["status"] == a11y.PASS
    assert a11y.derive_overall([passing(), man])["status"] == a11y.MANUAL
    assert a11y.derive_overall([passing(), nv])["status"] == a11y.NOTVER
    assert a11y.derive_overall([passing(), man, nv])["status"] == a11y.NOTVER
    assert COMMITTED["content"]["overall"] == a11y.derive_overall(COMMITTED["content"]["items"])


# 3. no clean PASS while anything is open
def test_no_clean_pass_with_open_items():
    for open_status in (
        {"id": "m", "title": "t", "scope": "s", "status": a11y.MANUAL, "needed_check": "listen", "done": False},
        {"id": "n", "title": "t", "scope": "s", "status": a11y.NOTVER, "reason": "none"},
    ):
        items = [a11y.normalize_item(passing("a")), a11y.normalize_item(open_status)]
        overall = a11y.derive_overall(items)
        assert overall["status"] != "PASS" and overall["open_items"] == 1
    # a manual item marked done without a recorded result stays open
    claimed = a11y.normalize_item({"id": "m", "title": "t", "scope": "s", "status": a11y.MANUAL, "needed_check": "x", "done": True})
    assert claimed["done"] is False and a11y.derive_overall([claimed])["status"] != "PASS"
    c = COMMITTED["content"]
    assert c["overall"]["status"] != "PASS" and c["overall"]["open_items"] == c["counts"][a11y.MANUAL] + c["counts"][a11y.NOTVER]


# 4. empty or incomplete evidence is never a PASS
@pytest.mark.parametrize("evidence", [None, {}, {"test": "", "proved": "x"}, {"test": "t", "proved": "  "}, {"test": "t"}, "ok"])
def test_pass_without_evidence_becomes_not_verified(evidence):
    item = passing()
    item["evidence"] = evidence
    out = a11y.normalize_item(item)
    assert out["status"] == a11y.NOTVER and "evidence" in out["reason"]
    assert a11y.derive_overall([out])["status"] == a11y.NOTVER


def test_pass_without_scope_status_or_items_is_not_verified():
    assert a11y.normalize_item({**passing(), "scope": ""})["status"] == a11y.NOTVER
    assert a11y.normalize_item({**passing(), "status": "OK"})["status"] == a11y.NOTVER
    assert a11y.normalize_item({"id": "m", "title": "t", "scope": "s", "status": a11y.MANUAL})["status"] == a11y.NOTVER
    assert a11y.derive_overall([])["status"] == a11y.NOTVER


# 5. mutation tests: a real defect in the page must make the named check fail
def _mutate(old, new, count=1):
    assert old in HTML, f"mutation anchor missing: {old!r}"
    return HTML.replace(old, new, count)


MUTATIONS = [
    ("icons-read-twice (static svg)", lambda: _mutate('<svg width="0" height="0" style="position:absolute" aria-hidden="true"', '<svg width="0" height="0" style="position:absolute"')),
    ("icons-read-twice (ic helper)", lambda: _mutate('aria-hidden="true" focusable="false"><use', 'aria-label="icon" focusable="false"><use')),
    ("claim numbering broken", lambda: _mutate("['03','Metafizikai", "['05','Metafizikai")),
    ("claim name changed", lambda: _mutate("'Átélt élmény'", "'Élmény'")),
    ("mark-04 disclaimer removed", lambda: _mutate("nem bizonyít külső", "bizonyít külső")),
    ("identical shapes", lambda: _mutate('<symbol id="a03" viewBox="0 0 24 24"><path d="M12 2.500 21.500 12 12 21.500 2.500 12z"/></symbol>',
                                           '<symbol id="a03" viewBox="0 0 24 24"><path d="M12 4 21 20H3z" stroke-width="2.200"/></symbol>')),
    ("status shape uses a circle", lambda: _mutate('<rect x="5" y="5" width="14" height="14" rx="1.500" fill="currentColor" stroke="none"/>', '<circle cx="12" cy="12" r="6"/>')),
    ("low contrast text", lambda: _mutate("--mute:#9aa8c7", "--mute:#3a4560")),
    ("small touch target", lambda: _mutate("min-height:44px;min-width:44px", "min-height:20px;min-width:20px")),
    ("status label dropped", lambda: _mutate('<span class="mk mkb">${ic(BI[i])}</span><div><span class="nm">${nm}</span>', '<span class="mk mkb">${ic(BI[i])}</span><div><span class="nm"></span>')),
    ("legends mixed", lambda: _mutate("BI[i])}</span><div><span class=\"nm\">${nm}", "AI[i])}</span><div><span class=\"nm\">${nm}")),
    ("non-button interactive element", lambda: _mutate('<div class="banner" id="warn">', '<div class="banner" id="warn" onclick="x()">')),
]


@pytest.mark.parametrize("name,mutate", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutated_page_fails_a_named_check(name, mutate):
    html = mutate()
    assert html != HTML
    with pytest.raises(a11y.CheckFailed):
        a11y.build_items(html)


def test_unmutated_page_passes_all_automated_checks():
    items = a11y.build_items(HTML)
    assert [i["status"] for i in items[:len(a11y.AUTOMATED)]] == [a11y.PASS] * len(a11y.AUTOMATED)


# 6. metadata is separate from content and survives; scope and evidence are preserved
def test_metadata_is_separate_and_preserved():
    t1, t2 = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 6, 30, 12, 0, tzinfo=timezone.utc)
    r1, r2 = a11y.build_report(HTML, t1), a11y.build_report(HTML, t2)
    assert r1["content"] == r2["content"]                                   # reproducible block
    assert r1["metadata"]["generated_at"] != r2["metadata"]["generated_at"]  # run metadata differs
    assert r1["metadata"]["content_sha256"] == r2["metadata"]["content_sha256"] == a11y.content_hash(r1["content"])
    assert {"generated_at", "environment", "tool", "tool_version", "content_sha256"} <= set(r1["metadata"])
    assert "generated_at" not in json.dumps(r1["content"])                    # no run data inside the content
    # re-serialising keeps both blocks intact
    assert json.loads(a11y.serialize(r1)) == r1


def test_committed_report_matches_page_and_keeps_scope_and_evidence():
    fresh = a11y.build_content(HTML)
    assert COMMITTED["content"] == fresh, "run: python observatory/a11y_report.py"
    assert COMMITTED["metadata"]["content_sha256"] == a11y.content_hash(fresh)
    assert COMMITTED["metadata"]["generated_at"] and COMMITTED["metadata"]["environment"]["python"]
    assert COMMITTED["content"]["scope"]
    for i in COMMITTED["content"]["items"]:
        assert i["scope"]
        if i["status"] == a11y.PASS:
            assert i["evidence"]["test"] and i["evidence"]["proved"]
    # normalising an already-normal item changes nothing
    for i in COMMITTED["content"]["items"]:
        assert a11y.normalize_item(copy.deepcopy(i)) == i


def test_check_mode_compares_content_only(tmp_path, monkeypatch):
    stale = copy.deepcopy(COMMITTED)
    stale["metadata"]["generated_at"] = "2000-01-01T00:00:00Z"               # metadata may differ
    p = tmp_path / "r.json"
    p.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(a11y, "REPORT", p)
    assert a11y.main(["--check"]) == 0
    stale["content"]["items"][0]["status"] = a11y.NOTVER                      # content may not
    p.write_text(json.dumps(stale), encoding="utf-8")
    assert a11y.main(["--check"]) == 1


# rendered check (skipped where Playwright/Chromium is unavailable): mobile reflow and real target size
def test_rendered_mobile_layout_and_target_size():
    pw = pytest.importorskip("playwright.sync_api")
    try:
        with pw.sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 400, "height": 800})
            page.goto((OBS / "index.html").as_uri())
            page.wait_for_timeout(200)
            overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
            boxes = [page.locator(s).bounding_box() for s in ("#b-hu", "#b-en")]
            hidden = page.evaluate("[...document.querySelectorAll('svg')].every(s => s.getAttribute('aria-hidden') === 'true')")
            b.close()
    except Exception as exc:  # no browser installed
        pytest.skip(f"browser unavailable: {exc}")
    assert not overflow and hidden
    assert all(bx["width"] >= 44 and bx["height"] >= 44 for bx in boxes)
