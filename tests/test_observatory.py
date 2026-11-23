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
