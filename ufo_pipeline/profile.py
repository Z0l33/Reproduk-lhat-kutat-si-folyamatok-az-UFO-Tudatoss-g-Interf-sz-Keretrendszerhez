"""Structural profile of a delimited text file. Reports counts and ranges only.

Free-text values are never printed: example values are shown only for low-cardinality, short
columns. This is a data-quality screen, not evidence about the research hypotheses.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Union

ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_DATE_FORMATS = ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S")
_NUM = re.compile(r"^-?[0-9]+([.,][0-9]+)?\Z")
MAX_EXAMPLE_LEN, MAX_EXAMPLE_DISTINCT = 40, 20


def _decode(raw: bytes):
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError("cannot decode file")


def _date(value: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def profile_csv(path: Union[str, Path], is_denied: Any = None) -> Dict[str, Any]:
    """is_denied(name) -> bool: for such columns only counts are reported, never values or extremes."""
    raw = Path(path).read_bytes()
    text, encoding = _decode(raw)
    sample = text[:20000]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not rows:
        return {"encoding": encoding, "delimiter": delimiter, "rows": 0, "columns": []}
    header, body = rows[0], rows[1:]
    width = len(header)
    report: Dict[str, Any] = {
        "encoding": encoding, "delimiter": delimiter, "bytes": len(raw), "data_rows": len(body),
        "header_duplicates": sorted(h for h, c in Counter(header).items() if c > 1),
        "rows_with_wrong_width": sum(1 for r in body if len(r) != width),
        "fully_duplicated_rows": len(body) - len({tuple(r) for r in body}),
        "fully_empty_rows": sum(1 for r in body if not any(c.strip() for c in r)),
        "columns": [],
    }
    for i, name in enumerate(header):
        values = [r[i].strip() for r in body if i < len(r)]
        filled = [v for v in values if v]
        info: Dict[str, Any] = {"name": name, "filled": len(filled),
                                "empty": len(values) - len(filled), "distinct": len(set(filled))}
        if is_denied is not None and is_denied(name):
            info["values_withheld"] = True
            report["columns"].append(info)
            continue
        if filled:
            nums = [float(v.replace(",", ".")) for v in filled if _NUM.match(v)]
            dates = [d for d in (_date(v) for v in filled) if d]
            if len(nums) >= 0.9 * len(filled):
                info.update(kind="numeric", min=min(nums), max=max(nums))
            elif len(dates) >= 0.9 * len(filled):
                info.update(kind="date", min=min(dates).isoformat(), max=max(dates).isoformat())
            else:
                info["kind"] = "text"
                info["max_length"] = max(len(v) for v in filled)
                counts = Counter(filled)
                if len(counts) <= MAX_EXAMPLE_DISTINCT and all(len(v) <= MAX_EXAMPLE_LEN for v in counts):
                    info["value_counts"] = dict(counts.most_common())
        report["columns"].append(info)
    return report
