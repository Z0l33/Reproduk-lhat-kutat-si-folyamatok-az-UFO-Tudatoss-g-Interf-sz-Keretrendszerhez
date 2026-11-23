"""The static site must be rebuilt from the committed report and make no live-status claims."""
import importlib.util
import re
from pathlib import Path

OBS = Path(__file__).resolve().parent.parent / "observatory"
spec = importlib.util.spec_from_file_location("site_build", OBS / "build.py")
site_build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_build)


def test_index_is_up_to_date_with_report_and_template():
    assert (OBS / "index.html").read_text(encoding="utf-8") == site_build.build(), \
        "run: python observatory/build.py"


def test_no_live_status_claims_or_forbidden_content():
    html = (OBS / "index.html").read_text(encoding="utf-8")
    assert "system online" not in html.lower()
    assert "Notion-szinkron még nincs igazolva" in html and "Notion sync is not yet verified" in html
    assert re.search(r"2026-11-23", html) and "nem frissül automatikusan" in html
    assert "Sachverhalt\"" not in html.split("const D=")[1].split(";\nconst T=")[0].replace("sachverhalt_", "")
    assert "<script src=" not in html            # no external scripts


def test_embedded_json_cannot_break_out_of_the_script_block():
    import json
    nasty = {"a": "</script><script>alert(1)</script> & <!--  "}
    out = site_build.embed(nasty)
    assert "<" not in out and ">" not in out and "&" not in out and " " not in out
    assert json.loads(out) == nasty                       # escapes decode back to the same data


def test_generated_page_has_no_raw_angle_bracket_in_data_block():
    html = (OBS / "index.html").read_text(encoding="utf-8")
    block = html.split("const D=")[1].split(";\nconst T=")[0]
    for ch in ("<", ">", "&", "\u2028", "\u2029"):
        assert ch not in block, repr(ch)


def test_fonts_are_self_hosted_and_licensed():
    html = (OBS / "index.html").read_text(encoding="utf-8")
    head = html.split("<script>")[0]                       # markup and CSS, before the inline script
    for needle in ("googleapis", "gstatic", "http://", "https://", "<link"):
        assert needle not in head, needle
    files = set(re.findall(r"url\('fonts/([^']+\.woff)'\)", html))
    assert files, "no @font-face sources found"
    for f in files:
        assert (OBS / "fonts" / f).is_file(), f
    assert (OBS / "fonts" / "Inter-LICENSE.txt").is_file()
    assert (OBS / "fonts" / "JetBrainsMono-OFL.txt").is_file()


def _make_fonts():
    spec = importlib.util.spec_from_file_location("make_fonts", OBS / "make_fonts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_woff_files_match_the_manifest():
    import json
    manifest = json.loads((OBS / "fonts" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["outputs"] and manifest["sources"]
    for rec in manifest["sources"].values():
        assert rec["version"] and rec["sha256"] and rec["upstream"] and rec["acquired_from"]
    assert _make_fonts().check() == 0


def test_a_modified_woff_file_is_detected(tmp_path, monkeypatch):
    mod = _make_fonts()
    import shutil
    shutil.copytree(OBS / "fonts", tmp_path / "fonts")
    victim = next((tmp_path / "fonts").glob("*.woff"))
    victim.write_bytes(victim.read_bytes() + b"x")
    monkeypatch.setattr(mod, "FONTS", tmp_path / "fonts")
    monkeypatch.setattr(mod, "MANIFEST", tmp_path / "fonts" / "MANIFEST.json")
    assert mod.check() == 1


def test_missing_source_fonts_give_a_clear_error(capsys):
    import pytest
    mod = _make_fonts()
    with pytest.raises(SystemExit) as e:
        mod.main(["--inter-dir", "/nonexistent-ufo", "--jetbrains-dir", "/nonexistent-ufo", "--out", "x"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "source font file(s) not found" in err and "/nonexistent-ufo/Inter-Regular.otf" in err
    assert "UFO_FONT_SRC_INTER" in err


def test_regeneration_reproduces_the_committed_files_when_sources_exist(tmp_path):
    import json
    import pytest
    pytest.importorskip("fontTools")
    mod = _make_fonts()
    try:
        mod.resolve_sources(type("A", (), {"inter_dir": None, "jetbrains_dir": None})())
    except SystemExit:
        pytest.skip("source fonts not available in this environment")
    manifest = json.loads((OBS / "fonts" / "MANIFEST.json").read_text(encoding="utf-8"))
    from fontTools import version
    if version != manifest["fonttools_version"]:
        pytest.skip("fontTools version differs from the one recorded in the manifest")
    assert mod.main(["--out", str(tmp_path)]) == 0
    new = json.loads((tmp_path / "MANIFEST.json").read_text(encoding="utf-8"))
    assert new == manifest
