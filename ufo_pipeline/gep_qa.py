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
    "age_phrase_broad": re.compile(r"\b\d{1,2}[- ]?jährig|\b\d{1,2} Jahre alt", re.I),
    "coordinates": re.compile(r"\d{1,3}[.,]\d{3,}\s*(?:°|[NSEOW]\b)"),
}
# Closed vocabulary of person nouns (German). Used only to decide whether an age phrase refers to a
# person; no matched text is ever stored or printed.
PERSON_NOUNS = ("mann|männer|frau|frauen|zeuge|zeugen|zeugin|zeuginnen|kind|kinder|junge|jungen|mädchen|"
                "person|personen|schüler|schülerin|jugendliche|jugendlicher|jugendlichen|rentner|rentnerin|"
                "student|studentin|beobachter|beobachterin|ehepaar|landwirt|pilot|polizist|soldat|fischer|"
                "bauer|förster|hausfrau|melder|melderin|anrufer|anruferin|herr|dame|senior|seniorin|"
                "teenager|baby|säugling|kleinkind|tochter|sohn|vater|mutter|bruder|schwester|enkel")
_P = r"(?:" + PERSON_NOUNS + r")"
AGE_FORMS = {
    # "45-jährige(r) [adjective(s)] <person noun>"
    "number_jaehrig_then_person": re.compile(
        r"\b(\d{1,2})[- ]?jährig\w*\s+(?:\w+\s+){0,2}" + _P + r"\b", re.I),
    # "<person noun> [,|(] 45 Jahre alt|J.|jährig|Jahre"
    "person_then_number_years": re.compile(
        r"\b" + _P + r"\b[\s,(]{1,3}(?:\w+\s+){0,1}(\d{1,2})\s?(?:Jahre alt|J\.|jährig|Jahre\b)", re.I),
    # "<person noun> (45)" or "(45 J.)"
    "person_then_bracket_number": re.compile(r"\b" + _P + r"\b\s*\((\d{1,2})(?:\s?(?:J\.?|Jahre))?\)", re.I),
    # "ist/war/sei 45 Jahre alt"
    "number_jahre_alt": re.compile(r"\b(\d{1,2}) Jahre alt\b", re.I),
}
STRICT_AGE_FORMS = ("number_jaehrig_then_person", "person_then_number_years", "person_then_bracket_number")


def age_findings(text: str) -> List[int]:
    """Ages (as integers) of person-bound age phrases in a text. The text itself is not returned."""
    found: Dict[int, int] = {}  # start position of the number -> age (one age per position)
    for name in STRICT_AGE_FORMS:
        for m in AGE_FORMS[name].finditer(text):
            found.setdefault(m.start(1), int(m.group(1)))
    return [found[k] for k in sorted(found)]


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


def datum_precision(raw: List[str], parsed: List[Any]) -> List[str]:
    """Per row: full | year_month | year_only | missing | other_unparsable."""
    out = []
    for r, p in zip(raw, parsed):
        if not r:
            out.append("missing")
        elif p is not None:
            out.append("full")
        else:
            kind = classify_date_value(r)
            out.append({"year_only": "year_only", "year_month_only": "year_month"}.get(kind, "other_unparsable"))
    return out


# Expected PLZ format by country (closed table, German country names as used in the file). Checked only
# for the countries listed; everything else is reported as "not checked".
PLZ_DIGITS_BY_LAND = {"Deutschland": 5, "Österreich": 4, "Schweiz": 4, "Liechtenstein": 4, "Luxemburg": 4,
                      "Belgien": 4, "Dänemark": 4, "Ungarn": 4, "Frankreich": 5, "Italien": 5,
                      "Spanien": 5, "Kroatien": 5}
PLACEHOLDER_RE = re.compile(r"^(?:\?+|x+|-+|n/?a|k\.?a\.?|unbekannt|\.+)\Z", re.I)
_PUNCT = {"(": "open_parenthesis", ")": "close_parenthesis", " ": "space", "-": "hyphen", "/": "slash",
          ".": "dot", ",": "comma", "*": "asterisk", "x": "letter_x", "X": "letter_x"}


def _land_group(land: str) -> str:
    if not land or PLACEHOLDER_RE.match(land):
        return "missing_or_placeholder"
    return land if land in ("Deutschland", "Österreich", "Schweiz") else "other_country"


def _char_kinds(values: List[str]) -> Dict[str, int]:
    """In how many values each non-digit character kind occurs (kinds, not the values)."""
    counts: Counter = Counter()
    for v in values:
        kinds = set()
        for ch in v:
            if ch.isdigit():
                continue
            kinds.add(_PUNCT.get(ch, "letter" if ch.isalpha() else "other_symbol"))
        counts.update(kinds)
    return dict(sorted(counts.items()))


def _length_dist(values: List[str]) -> Dict[str, int]:
    return {str(k): v for k, v in sorted(Counter(len(x) for x in values).items())}


def geography_deep(land: List[str], bund: List[str], plz: List[str], plzu: List[str],
                   ort: List[str]) -> Dict[str, Any]:
    """Format and consistency checks for the geographic fields. Counts only; codes are never printed."""
    n = len(land)
    group = [_land_group(x) for x in land]
    plz_idx = [i for i, v in enumerate(plz) if v]
    out: Dict[str, Any] = {}

    # --- Land
    out["Land"] = {
        "placeholder_values": sum(1 for x in land if x and PLACEHOLDER_RE.match(x)),
        "non_country_marker": sum(1 for x in land if re.search(r"ozean|meer\b|see\b", x, re.I)),
        "groups": dict(sorted(Counter(group).items())),
    }
    # --- PLZ
    digits = [plz[i] for i in plz_idx if plz[i].isdigit()]
    non_digits = [plz[i] for i in plz_idx if not plz[i].isdigit()]
    by_group: Dict[str, Any] = {}
    for g in sorted(set(group)):
        vals = [plz[i] for i in plz_idx if group[i] == g]
        by_group[g] = {"filled": len(vals), "digits_only": sum(1 for v in vals if v.isdigit()),
                       "length_distribution": _length_dist(vals)}
    expected_mismatch: Dict[str, Any] = {}
    for country, ndig in PLZ_DIGITS_BY_LAND.items():
        vals = [plz[i] for i in plz_idx if land[i] == country]
        if vals:
            expected_mismatch[country] = {
                "filled": len(vals), "expected_digits": ndig,
                "not_expected_format": sum(1 for v in vals if not (v.isdigit() and len(v) == ndig)),
                "shorter_all_digits": sum(1 for v in vals if v.isdigit() and len(v) < ndig),
                "longer_or_non_digit": sum(1 for v in vals if not v.isdigit() or len(v) > ndig)}
    de5 = [plz[i] for i in plz_idx if land[i] == "Deutschland" and plz[i].isdigit() and len(plz[i]) == 5]
    cells = Counter(plz[i] for i in plz_idx)
    row_cell = [cells[plz[i]] for i in plz_idx]
    out["PLZ"] = {
        "filled": len(plz_idx), "digits_only": len(digits), "not_digits_only": len(non_digits),
        "length_distribution": _length_dist([plz[i] for i in plz_idx]),
        "non_digit_character_kinds_in_values": _char_kinds(non_digits),
        "non_digit_value_shapes": _top(Counter(_pattern(v) for v in non_digits), 15),
        "leading_zero_preserved_count": sum(1 for v in digits if v.startswith("0") and len(v) > 1),
        "all_zero_values": sum(1 for v in digits if int(v) == 0),
        "repeated_single_digit_values": sum(1 for v in digits if len(v) >= 3 and len(set(v)) == 1 and int(v) != 0),
        "placeholder_values": sum(1 for i in plz_idx if PLACEHOLDER_RE.match(plz[i])),
        "germany_five_digit_starting_00": sum(1 for v in de5 if v.startswith("00")),
        "by_land_group": by_group,
        "expected_format_by_country": expected_mismatch,
        "filled_but_land_missing_or_placeholder": sum(1 for i in plz_idx if group[i] == "missing_or_placeholder"),
        "small_cells": {"distinct_plz": len(cells),
                        "rows_in_plz_with_fewer_than_5_rows": sum(1 for c in row_cell if c < 5),
                        "rows_in_plz_with_fewer_than_3_rows": sum(1 for c in row_cell if c < 3),
                        "rows_in_plz_with_exactly_1_row": sum(1 for c in row_cell if c == 1)},
    }
    # --- PLZ_unvollstaendig
    u_idx = [i for i, v in enumerate(plzu) if v]
    u_vals = [plzu[i] for i in u_idx]
    u_digits = [v for v in u_vals if v.isdigit()]
    long_ = [v for v in u_digits if len(v) >= 4]
    out["PLZ_unvollstaendig"] = {
        "filled": len(u_vals), "digits_only": len(u_digits), "not_digits_only": len(u_vals) - len(u_digits),
        "length_distribution": _length_dist(u_vals),
        "non_digit_character_kinds_in_values": _char_kinds([v for v in u_vals if not v.isdigit()]),
        "groups": {"1_to_3_digits": sum(1 for v in u_digits if len(v) <= 3),
                   "4_or_more_digits": len(long_),
                   "4_or_more_digits_by_length": _length_dist(long_),
                   "6_or_more_digits": sum(1 for v in long_ if len(v) >= 6)},
        "values_ending_with_zero_padding": {"ends_with_000": sum(1 for v in long_ if v.endswith("000")),
                                            "ends_with_0000": sum(1 for v in long_ if v.endswith("0000"))},
        "leading_zero_preserved_count": sum(1 for v in u_digits if v.startswith("0") and len(v) > 1),
        "land_groups_of_filled_rows": dict(sorted(Counter(group[i] for i in u_idx).items())),
        "sichtungsort_filled_for_filled_rows": sum(1 for i in u_idx if ort[i]),
        "overlap_with_plz": sum(1 for i in u_idx if plz[i]),
        "reading_bands": {
            "facts": "length bands of digits-only values",
            "1_to_3_digits": sum(1 for v in u_digits if len(v) <= 3),
            "4_digits": sum(1 for v in u_digits if len(v) == 4),
            "4_digits_in_land_with_4_digit_postcodes": sum(
                1 for i in u_idx if plzu[i].isdigit() and len(plzu[i]) == 4
                and land[i] in ("Österreich", "Schweiz", "Liechtenstein", "Luxemburg", "Belgien",
                                "Dänemark", "Ungarn")),
            "5_digits": sum(1 for v in u_digits if len(v) == 5),
            "6_or_more_digits": sum(1 for v in u_digits if len(v) >= 6)},
        "meaning_status": "UNVERIFIED. Heuristic reading: 1-3 digit values are compatible with a truncated "
                          "postal code; 4 digit values are compatible with a truncated 5-digit German code "
                          "but also with a complete 4-digit code of AT/CH and similar; 5 digit values look "
                          "complete; values with 6 or more digits fit no postal format. The GEP "
                          "documentation was not consulted. Do not use this field for analysis before that.",
    }
    # --- Bundesland
    out["Bundesland"] = {
        "placeholder_values": sum(1 for x in bund if x and PLACEHOLDER_RE.match(x)),
        "filled_by_land_group": dict(sorted(Counter(group[i] for i, v in enumerate(bund) if v).items())),
        "filled_but_land_missing_or_placeholder": sum(1 for i, v in enumerate(bund) if v and group[i] == "missing_or_placeholder"),
        "missing_for_germany": sum(1 for i in range(n) if land[i] == "Deutschland" and not bund[i]),
    }
    return out


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
    deep = geography_deep(land, bund, plz, plzu, c.get("Sichtungsort", [""] * n))
    report["geography_deep"] = deep
    for i, v in enumerate(land):
        if not v:
            flag("missing_land", i)
        elif PLACEHOLDER_RE.match(v):
            flag("land_placeholder", i)
        elif re.search(r"ozean|meer\b|see\b", v, re.I):
            flag("land_not_a_country", i)
    for i, v in enumerate(plzu):
        if v.isdigit() and len(v) >= 4:
            flag("plz_unvollstaendig_4_or_more_digits", i)
    for i, v in enumerate(plz):
        exp = PLZ_DIGITS_BY_LAND.get(land[i])
        if v and exp and not (v.isdigit() and len(v) == exp):
            flag("plz_format_unexpected_for_land", i)
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
    precision = datum_precision(c.get("Datum", [""] * n), datum)
    prec_counts = Counter(precision)
    report["dates"] = {
        "Datum_precision": {k: prec_counts.get(k, 0) for k in
                            ("full", "year_month", "year_only", "other_unparsable", "missing")},
        "Datum_precision_sums_to_rows": sum(prec_counts.values()) == n,
        "delay_basis": "reporting-delay and order checks use only rows with full-precision Datum and a "
                       "parsed Gemeldet; year- or month-precision events never get a daily delay",
        "gemeldet_present_by_datum_precision": {k: sum(1 for pr, g in zip(precision, c.get("Gemeldet", [""] * n))
                                                       if pr == k and g)
                                                for k in ("full", "year_month", "year_only",
                                                          "other_unparsable", "missing")},
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
    broad_rows = [i for i, v in enumerate(sach) if v and PRIVACY_PATTERNS["age_phrase_broad"].search(v)]
    strict_ages = {i: age_findings(v) for i, v in enumerate(sach) if v}
    strict_rows = [i for i, a in strict_ages.items() if a]
    all_ages = [a for i in strict_rows for a in strict_ages[i]]
    per_form = {name: sum(1 for v in sach if v and AGE_FORMS[name].search(v)) for name in AGE_FORMS}
    broad_only = [i for i in broad_rows if i not in set(strict_rows)]
    priv["age_analysis"] = {
        "definition": "strict = an age phrase bound to a person noun from a closed German vocabulary; "
                      "broad = any 'NN-jährig' or 'NN Jahre alt'. Heuristic: neither proves a real person.",
        "rows_broad": len(broad_rows), "rows_strict": len(strict_rows),
        "rows_broad_not_strict": len(broad_only),
        "rows_by_form": per_form,
        "rows_with_more_than_one_strict_age": sum(1 for i in strict_rows if len(strict_ages[i]) > 1),
        "strict_age_values": {"count": len(all_ages),
                              "under_18": sum(1 for a in all_ages if a < 18),
                              "over_100": sum(1 for a in all_ages if a > 100),
                              "by_band": _top(Counter(
                                  "0-17" if a < 18 else "18-39" if a < 40 else "40-64" if a < 65 else "65+"
                                  for a in all_ages), 10)},
        "unverified": "rows_broad_not_strict could not be assigned to a person or to something else "
                      "without reading the text; they are neither confirmed nor dismissed.",
    }
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
                     ("email", "url", "phone_like", "street_with_number", "salutation_name")):
            flag("sachverhalt_direct_identifier_pattern", i)
        if strict_ages.get(i):
            flag("sachverhalt_person_age_strict", i)
        elif i in set(broad_only):
            flag("sachverhalt_age_phrase_unclassified", i)
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
    report["evidence_levels"] = {
        "facts": ["input", "identifier", "geography (counts and formats)", "dates.Datum_precision",
                  "dates.Datum_unparsable_breakdown", "classification.value_counts",
                  "geography_deep (counts, lengths, character kinds, group sizes)",
                  "summary.rule_counts for structural rules (missing_*, datum_*, plz_*, hendry_*, land_*)"],
        "heuristic_hits": ["privacy_screen.*.rows_matching", "privacy_screen.age_analysis",
                           "summary.rule_counts: sachverhalt_*, sichtungsort_*, duplicate_sachverhalt_text"],
        "unverified_assumptions": [
            "that the country-specific PLZ formats used for the consistency check (PLZ_DIGITS_BY_LAND) are "
            "the right expectation for every row of that country",
            "that PLZ_unvollstaendig holds truncated postal codes (see geography_deep.PLZ_unvollstaendig)",
            "that a heuristic hit corresponds to a real personal identifier (not checked: no text is read)",
            "that PLZ_unvollstaendig and the Hynek/Hendry codes mean what their names suggest (GEP "
            "documentation not consulted)",
            "that the dataset is free of submitter personal data, as its description states",
            "that Datum/Gemeldet formats are consistently day-month-year (only parseability was tested)"],
    }
    report["input"]["sha256_after"] = sha256_file(path)
    report["input"]["unchanged_during_analysis"] = report["input"]["sha256_after"] == sha_before
    return report
