"""Reproducible data-quality and privacy screen for the GEP case CSV.

Read-only: the input file is opened for reading and its SHA-256 is recorded before and after.
Output contains counts, format patterns and category frequencies ONLY. Free-text columns
(Sachverhalt, Sichtungsort, Identifikation) are never echoed. Privacy patterns are heuristics:
a count of 0 does not prove absence, a positive count is a lead to review manually.
This is a screen, not a judgement about the research hypotheses.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .hashing import sha256_file
from .profile import _date, _decode

QA_VERSION = 1
CATEGORY_COLUMNS = ("Land", "Zeitzone", "Klassifikation_Hynek", "Klassifikation_Hendry",
                    "Klassifikation_IFO-Verifikationsschluessel", "Erstkontakt", "Tageszeit")
FREE_TEXT = ("Sachverhalt", "Sichtungsort", "Identifikation", "Identifikations_Gruppe")
RANGE_START, RANGE_END = datetime(1972, 1, 1), datetime(2026, 10, 31)
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]\Z")

PRIVACY_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "url": re.compile(r"https?://|www\.", re.I),
    "phone_like": re.compile(r"(?:\+|\b0)\d[\d /()-]{6,}\d"),
    "street_with_number": re.compile(
        r"\b[A-ZÄÖÜ][\wäöüß.-]*(?:straße|strasse|str\.|weg|gasse|platz|allee)\s*\d+"),
    "salutation_name": re.compile(r"\b(?:Herr|Frau|Hr\.|Fr\.|Dr\.)\s+[A-ZÄÖÜ][a-zäöüß]+"),
    "first_person": re.compile(r"\b(?:ich|mein|meine|meinem|meiner|mir|mich)\b", re.I),
    "age_mention": re.compile(r"\b\d{1,2}[- ]?jährig|\b\d{1,2} Jahre alt", re.I),
    "coordinates": re.compile(r"\d{1,3}[.,]\d{3,}\s*(?:°|[NSEOW]\b)"),
}
ORT_PATTERNS = {
    "has_digit": re.compile(r"\d"),
    "street_suffix": re.compile(r"(?:straße|strasse|str\.|weg|gasse|platz|allee)", re.I),
}


def _norm(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded).strip().lower()


def _pattern(value: str) -> str:
    out = re.sub(r"[0-9]", "9", value)
    out = re.sub(r"[A-Za-zÄÖÜäöüß]", "A", out)
    return re.sub(r"(.)\1{3,}", r"\1{n}", out)[:30]


def _top(counter: Counter, n: int = 15) -> Dict[str, int]:
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:n])


def _variants(values: List[str]) -> Dict[str, Any]:
    groups: Dict[str, set] = {}
    for v in values:
        groups.setdefault(_norm(v), set()).add(v)
    multi = {k: g for k, g in groups.items() if len(g) > 1}
    return {"distinct_raw": len({v for v in values}), "distinct_normalised": len(groups),
            "groups_with_spelling_variants": len(multi),
            "rows_in_variant_groups": sum(1 for v in values if _norm(v) in multi)}


_DT = r"(?:[0-9]{1,4}[-./][0-9]{1,2}[-./][0-9]{1,4})"
_TIME_SUFFIX = re.compile(r"^(" + _DT + r")[ T]+[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?\Z")
_FULL = re.compile(r"^([0-9]{1,4})[-./]([0-9]{1,2})[-./]([0-9]{1,4})\Z")
_YEAR = re.compile(r"^[0-9]{4}\Z")
_YM = re.compile(r"^(?:([0-9]{4})[-./]([0-9]{1,2})|([0-9]{1,2})[-./]([0-9]{4}))\Z")


def classify_date_value(value: str) -> str:
    """Error type of a Datum value that no known format could parse. Returns a label only."""
    v = value.strip()
    if _TIME_SUFFIX.match(v):
        return "date_with_time_unrecognised_format"
    if re.search(r"[A-Za-zÄÖÜäöüß]", v):
        return "contains_text"
    if len(re.findall(r"[0-9]{1,4}[-./][0-9]{1,2}[-./][0-9]{1,4}", v)) >= 2 or re.search(r"[0-9]\s*[-–/]\s*[0-9]{1,2}\.[0-9]{1,2}\.", v):
        return "range_or_several_dates"
    if _YEAR.match(v):
        return "year_only"
    m = _YM.match(v)
    if m:
        month = int(m.group(2) or m.group(3))
        return "year_month_only" if 1 <= month <= 12 else "year_month_invalid_month"
    m = _FULL.match(v)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if len(a) == 4:
            y, mo, d = int(a), int(b), int(c)
        elif len(c) == 4:
            y, mo, d = int(c), int(b), int(a)
        elif len(c) == 2 and len(a) <= 2:
            return "two_digit_year"
        else:
            return "full_date_shaped_ambiguous_fields"
        if not 1 <= mo <= 12:
            return "month_out_of_range"
        try:
            datetime(y, mo, d)
        except ValueError:
            return "day_invalid_for_month" if y >= 1 else "year_invalid"
        if y < 1900 or y > 2026:
            return "year_implausible"
        return "valid_date_unrecognised_format"
    return "other_shape"


def _date_error_report(raw: List[str], parsed: List[Any], gemeldet_raw: List[str]) -> Dict[str, Any]:
    bad = [i for i, (r, p) in enumerate(zip(raw, parsed)) if r and p is None]
    types = Counter(classify_date_value(raw[i]) for i in bad)
    shapes = Counter(_pattern(raw[i].strip()) for i in bad)
    return {"count": len(bad), "by_error_type": _top(types, 20), "format_shapes": _top(shapes, 20),
            "length_distribution": {str(k): v for k, v in sorted(Counter(len(raw[i].strip()) for i in bad).items())},
            "with_gemeldet_present": sum(1 for i in bad if gemeldet_raw[i]),
            "note": "shapes: digits shown as 9, letters as A; no concrete values are printed"}


def analyse(path: Union[str, Path]) -> Dict[str, Any]:
    path = Path(path)
    sha_before = sha256_file(path)
    text, encoding = _decode(path.read_bytes())
    try:
        delimiter = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    header, body = rows[0], rows[1:]
    idx = {h: i for i, h in enumerate(header)}
    missing_cols = [c for c in CATEGORY_COLUMNS + FREE_TEXT + ("Fallnummer", "Datum", "Gemeldet", "PLZ",
                    "PLZ_unvollstaendig", "Bundesland", "lokale Uhrzeit", "UTC") if c not in idx]

    def col(name: str) -> List[str]:
        i = idx.get(name)
        return [(r[i].strip() if i is not None and i < len(r) else "") for r in body]

    n = len(body)
    c = {name: col(name) for name in idx}
    report: Dict[str, Any] = {
        "qa_version": QA_VERSION, "input": {"sha256": sha_before, "bytes": path.stat().st_size,
        "encoding": encoding, "delimiter": delimiter, "data_rows": n, "columns": len(header),
        "missing_expected_columns": missing_cols},
        "notice": "Counts and patterns only. Privacy patterns are heuristics (zero != absent).",
    }
    flags: Dict[str, List[int]] = {}

    def flag(rule: str, row_index: int) -> None:
        flags.setdefault(rule, []).append(row_index)

    # --- identifiers
    fall = c.get("Fallnummer", [""] * n)
    fall_counts = Counter(v for v in fall if v)
    report["identifier"] = {
        "missing": sum(1 for v in fall if not v),
        "duplicated_values": sum(1 for v in fall_counts.values() if v > 1),
        "format_patterns": _top(Counter(_pattern(v) for v in fall if v), 10)}
    for i, v in enumerate(fall):
        if not v:
            flag("missing_fallnummer", i)

    # --- geography
    land, bund, plz, plzu = (c.get(k, [""] * n) for k in ("Land", "Bundesland", "PLZ", "PLZ_unvollstaendig"))
    plz_f = [v for v in plz if v]
    plz_len = Counter(len(v) for v in plz_f)
    report["geography"] = {
        "Land": {"missing": land.count(""), **_variants([v for v in land if v]),
                 "value_counts": _top(Counter(v for v in land if v), 80)},
        "Bundesland": {"missing": bund.count(""), **_variants([v for v in bund if v]),
                       "length_max": max((len(v) for v in bund), default=0),
                       "with_digits": sum(1 for v in bund if re.search(r"\d", v))},
        "PLZ": {"filled": len(plz_f), "digits_only": sum(1 for v in plz_f if v.isdigit()),
                "not_digits_only": sum(1 for v in plz_f if not v.isdigit()),
                "length_distribution": {str(k): v for k, v in sorted(plz_len.items())},
                "equal_to_zero": sum(1 for v in plz_f if v.isdigit() and int(v) == 0),
                "leading_zero": sum(1 for v in plz_f if v.startswith("0") and len(v) > 1),
                "format_patterns": _top(Counter(_pattern(v) for v in plz_f), 10)},
        "PLZ_unvollstaendig": {"filled": sum(1 for v in plzu if v),
                               "format_patterns": _top(Counter(_pattern(v) for v in plzu if v), 15)},
        "plz_and_plz_unvollstaendig_both_filled": sum(1 for a, b in zip(plz, plzu) if a and b),
    }
    for i, v in enumerate(land):
        if not v:
            flag("missing_land", i)
    for i, v in enumerate(plz):
        if v and (not v.isdigit() or len(v) > 5):
            flag("plz_nonnumeric_or_over_5_digits", i)
        elif v and int(v) == 0:
            flag("plz_zero", i)

    # --- dates and times
    datum = [_date(v) if v else None for v in c.get("Datum", [""] * n)]
    gemeldet = [_date(v) if v else None for v in c.get("Gemeldet", [""] * n)]
    unparsable = lambda raw, parsed: sum(1 for r, p in zip(raw, parsed) if r and p is None)  # noqa: E731
    both = [(g, d) for g, d in zip(gemeldet, datum) if g and d]
    report["dates"] = {
        "Datum": {"missing": sum(1 for v in c.get("Datum", []) if not v),
                  "unparsable": unparsable(c.get("Datum", []), datum),
                  "before_1972": sum(1 for d in datum if d and d < RANGE_START),
                  "after_2026_10_31": sum(1 for d in datum if d and d > RANGE_END),
                  "by_decade": _top(Counter(f"{d.year // 10 * 10}s" for d in datum if d), 20)},
        "Gemeldet": {"missing": sum(1 for v in c.get("Gemeldet", []) if not v),
                     "unparsable": unparsable(c.get("Gemeldet", []), gemeldet),
                     "equal_1970_01_01": sum(1 for g in gemeldet if g and g == datetime(1970, 1, 1)),
                     "after_2026_10_31": sum(1 for g in gemeldet if g and g > RANGE_END)},
        "Datum_unparsable_breakdown": _date_error_report(
            c.get("Datum", [""] * n), datum, c.get("Gemeldet", [""] * n)),
        "both_dates_present": len(both),
        "reported_before_event": sum(1 for g, d in both if g < d),
        "reported_more_than_1_year_after_event": sum(1 for g, d in both if (g - d).days > 365),
        "datum_missing_but_gemeldet_present": sum(1 for g, d in zip(gemeldet, datum) if g and not d),
    }
    for i, (g, d) in enumerate(zip(gemeldet, datum)):
        if d is None and not c.get("Datum", [""] * n)[i]:
            flag("missing_datum", i)
        if d and d < RANGE_START:
            flag("datum_before_1972", i)
        if d and d > RANGE_END:
            flag("datum_after_2026_10_31", i)
        if g and g == datetime(1970, 1, 1):
            flag("gemeldet_epoch_1970_01_01", i)
        if g and d and g < d:
            flag("reported_before_event", i)
    for name in ("lokale Uhrzeit", "UTC"):
        vals = c.get(name, [""] * n)
        filled = [v for v in vals if v]
        report["dates"][name] = {"filled": len(filled),
                                 "invalid_hh_mm": sum(1 for v in filled if not TIME_RE.match(v))}
    for i, v in enumerate(c.get("lokale Uhrzeit", [""] * n)):
        if v and not TIME_RE.match(v):
            flag("invalid_local_time", i)

    # --- classification
    cls: Dict[str, Any] = {}
    for name in ("Klassifikation_Hynek", "Klassifikation_Hendry", "Klassifikation_IFO-Verifikationsschluessel"):
        vals = [v for v in c.get(name, []) if v]
        cls[name] = {"filled": len(vals), "missing": n - len(vals), "value_counts": _top(Counter(vals), 40),
                     **_variants(vals)}
    hend = c.get("Klassifikation_Hendry", [""] * n)
    key = c.get("Klassifikation_IFO-Verifikationsschluessel", [""] * n)
    cls["hendry_x_verification_key"] = {f"{h or '(empty)'} | {k or '(empty)'}": v for (h, k), v in
                                        sorted(Counter(zip(hend, key)).items(), key=lambda kv: (-kv[1], kv[0]))[:30]}
    cls["hendry_ifo_case_variants_rows"] = sum(1 for h in hend if h.upper() == "IFO" and h != "IFO")
    cls["hendry_S_rows"] = hend.count("S")
    cls["hendry_hoax_rows"] = hend.count("Hoax")
    cls["hendry_good_ufo_rows"] = hend.count("GOOD UFO")
    report["classification"] = cls
    for i, (h, k) in enumerate(zip(hend, key)):
        if h.upper() == "IFO" and h != "IFO":
            flag("hendry_case_variant", i)
        if h == "S":
            flag("hendry_S_unclear_meaning", i)
        if k and not h:
            flag("verification_key_without_hendry", i)

    # --- privacy screen (counts only)
    sach = c.get("Sachverhalt", [""] * n)
    ort = c.get("Sichtungsort", [""] * n)
    priv: Dict[str, Any] = {"Sachverhalt": {"filled": sum(1 for v in sach if v),
                                            "length_max": max((len(v) for v in sach), default=0),
                                            "length_over_1000": sum(1 for v in sach if len(v) > 1000),
                                            "rows_matching": {}},
                            "Sichtungsort": {"filled": sum(1 for v in ort if v), "rows_matching": {}}}
    for pname, rx in PRIVACY_PATTERNS.items():
        priv["Sachverhalt"]["rows_matching"][pname] = sum(1 for v in sach if v and rx.search(v))
    for pname, rx in ORT_PATTERNS.items():
        priv["Sichtungsort"]["rows_matching"][pname] = sum(1 for v in ort if v and rx.search(v))
    priv["Sichtungsort"]["rows_matching"]["coordinates"] = sum(
        1 for v in ort if v and PRIVACY_PATTERNS["coordinates"].search(v))
    text_counts = Counter(_norm(v) for v in sach if v)
    priv["Sachverhalt"]["rows_with_identical_text_elsewhere"] = sum(v for v in text_counts.values() if v > 1)
    quasi = [(d, t, o, p) for d, t, o, p in zip(c.get("Datum", []), c.get("lokale Uhrzeit", []), ort, plz)
             if d and t and o]
    qcount = Counter(quasi)
    priv["quasi_identifier_uniqueness"] = {
        "definition": "rows with Datum, lokale Uhrzeit and Sichtungsort all filled, grouped by "
                      "(Datum, lokale Uhrzeit, Sichtungsort, PLZ)",
        "rows_considered": len(quasi), "rows_unique_combination": sum(1 for v in qcount.values() if v == 1),
        "rows_with_exact_time_place_date_and_plz": sum(1 for q in quasi if q[3])}
    priv["note"] = ("The dataset description says submitter personal data were excluded; this screen only "
                    "counts surface patterns. Manual review of flagged rows is required before any "
                    "redistribution or publication.")
    report["privacy_screen"] = priv
    for i, v in enumerate(sach):
        if v and any(PRIVACY_PATTERNS[k].search(v) for k in
                     ("email", "url", "phone_like", "street_with_number", "salutation_name", "age_mention")):
            flag("sachverhalt_direct_identifier_pattern", i)
    for i, v in enumerate(sach):
        if v and text_counts[_norm(v)] > 1:
            flag("duplicate_sachverhalt_text", i)
    for i, v in enumerate(ort):
        if v and ORT_PATTERNS["street_suffix"].search(v) and ORT_PATTERNS["has_digit"].search(v):
            flag("sichtungsort_street_with_number", i)

    # --- summary
    flagged = set().union(*flags.values()) if flags else set()
    report["summary"] = {
        "rows": n, "rows_with_at_least_one_flag": len(flagged),
        "rule_counts": dict(sorted(((k, len(v)) for k, v in flags.items()), key=lambda kv: (-kv[1], kv[0]))),
        "free_text_columns_never_echoed": list(FREE_TEXT),
    }
    report["input"]["sha256_after"] = sha256_file(path)
    report["input"]["unchanged_during_analysis"] = report["input"]["sha256_after"] == sha_before
    return report
