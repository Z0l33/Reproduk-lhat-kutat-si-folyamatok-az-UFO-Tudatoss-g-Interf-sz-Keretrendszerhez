"""Build observatory/index.html from observatory/template.html and the committed QA report.

Only aggregates from reports/*.qa.json are embedded. Run: python observatory/build.py
The status block in the template is hand-maintained and dated; it does not update itself.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "gep-ufo-uap-2026-10.qa.json"
RULES = ["missing_datum", "missing_land", "plz_format_unexpected_for_land",
         "plz_nonnumeric_or_over_5_digits", "plz_unvollstaendig_4_or_more_digits",
         "hendry_S_unclear_meaning", "duplicate_sachverhalt_text",
         "sachverhalt_direct_identifier_pattern", "sachverhalt_age_phrase_unclassified",
         "sachverhalt_person_age_strict"]


def embed(data) -> str:
    """JSON for a <script> block: no raw '<', '>', '&' or JS line separators can end the block or the string."""
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                    ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        text = text.replace(ch, esc)
    return text


def build() -> str:
    r = json.loads(REPORT.read_text(encoding="utf-8"))
    risk = r["reidentification_risk"]
    strict = risk["strict_age_cohort"]
    data = {
        "rows": r["summary"]["rows"], "flagged": r["summary"]["rows_with_at_least_one_flag"],
        "prec": r["dates"]["Datum_precision"],
        "hendry": r["classification"]["Klassifikation_Hendry"]["value_counts"],
        "levels": [[k, strict[k]["risk_indicator"], strict[k]["eligible_rows"]] for k in strict],
        "cohorts": risk["cohorts"],
        "rules": {k: r["summary"]["rule_counts"][k] for k in RULES},
        "ev": r["evidence_levels"], "sha": r["input"]["sha256"],
    }
    assert sum(data["prec"].values()) == data["rows"]
    tpl = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    return tpl.replace("/*DATA*/null", embed(data))


if __name__ == "__main__":
    (Path(__file__).parent / "index.html").write_text(build(), encoding="utf-8")
