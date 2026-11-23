"""Schema validation and data-leakage detection for tabular (CSV) datasets."""
from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

PathLike = Union[str, Path]
Row = Dict[str, str]
MAX_LISTED = 10


def load_csv(path: PathLike) -> Tuple[List[str], List[Row]]:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(r) for r in reader]
        return list(reader.fieldnames or []), rows


def _is_missing(value: Optional[str]) -> bool:
    return value is None or str(value).strip() == ""


def parse_float(value: Optional[str]) -> Optional[float]:
    if _is_missing(value):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_schema(columns: Sequence[str], rows: Sequence[Row], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Validate rows against a column spec.

    spec = {"columns": {name: {"type": "float"|"int"|"str", "required": bool, "min": x, "max": y}},
            "id_column": str|None, "unique_id": bool, "min_rows": int}
    """
    errors: List[str] = []
    warnings: List[str] = []
    col_specs: Dict[str, Dict[str, Any]] = spec.get("columns", {})
    for name in col_specs:
        if name not in columns:
            errors.append(f"missing_column:{name}")
    if len(rows) < int(spec.get("min_rows", 1)):
        errors.append(f"too_few_rows:{len(rows)}<{spec.get('min_rows', 1)}")

    for name, cspec in col_specs.items():
        if name not in columns:
            continue
        bad: List[int] = []
        kinds: Dict[str, List[int]] = {}
        for i, row in enumerate(rows, start=2):  # 1-based, +1 for header line
            value = row.get(name)
            if _is_missing(value):
                if cspec.get("required", True):
                    kinds.setdefault("missing_value", []).append(i)
                continue
            ctype = cspec.get("type", "str")
            if ctype in ("float", "int"):
                number = parse_float(value)
                if number is None:
                    kinds.setdefault("bad_type", []).append(i)
                    continue
                if ctype == "int" and number != int(number):
                    kinds.setdefault("bad_type", []).append(i)
                    continue
                if "min" in cspec and number < cspec["min"]:
                    kinds.setdefault("below_min", []).append(i)
                if "max" in cspec and number > cspec["max"]:
                    kinds.setdefault("above_max", []).append(i)
        for kind, lines in kinds.items():
            shown = ",".join(str(n) for n in lines[:MAX_LISTED])
            more = f"(+{len(lines) - MAX_LISTED} more)" if len(lines) > MAX_LISTED else ""
            errors.append(f"{kind}:{name}:lines {shown}{more}")

    id_col = spec.get("id_column")
    if id_col and id_col in columns and spec.get("unique_id", False):
        ids = [r.get(id_col) for r in rows]
        if len(ids) != len(set(ids)):
            errors.append(f"duplicate_ids:{id_col}")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "n_rows": len(rows)}


def _parse_time(value: str) -> Optional[float]:
    number = parse_float(value)
    if number is not None:
        return number
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def detect_leakage(rows: Sequence[Row], *, group_column: str, outcome_column: str,
                   id_column: Optional[str] = None, feature_columns: Sequence[str] = (),
                   split_column: Optional[str] = None, train_value: str = "train",
                   test_value: str = "test", time_column: Optional[str] = None,
                   proxy_threshold: float = 0.99) -> Dict[str, Any]:
    """Detect contamination that would invalidate an analysis.

    Checks: subject present in several groups; subject or exact row shared between train
    and test splits; test data not strictly later than training data (when a time column is
    given); features that are (near-)perfect proxies of the outcome.
    """
    findings: List[Dict[str, Any]] = []

    if id_column:
        groups_by_id: Dict[str, set] = {}
        for row in rows:
            groups_by_id.setdefault(row.get(id_column, ""), set()).add(row.get(group_column))
        shared = sorted(i for i, g in groups_by_id.items() if len(g) > 1)
        if shared:
            findings.append({"type": "subject_in_multiple_groups", "count": len(shared),
                             "examples": shared[:MAX_LISTED]})

    if split_column:
        train = [r for r in rows if r.get(split_column) == train_value]
        test = [r for r in rows if r.get(split_column) == test_value]
        if id_column:
            overlap = sorted({r.get(id_column) for r in train} & {r.get(id_column) for r in test})
            if overlap:
                findings.append({"type": "subject_in_train_and_test", "count": len(overlap),
                                 "examples": overlap[:MAX_LISTED]})
        cols = [c for c in list(feature_columns) + [outcome_column]]
        key = lambda r: tuple(r.get(c) for c in cols)  # noqa: E731
        dup = {key(r) for r in train} & {key(r) for r in test}
        if dup:
            findings.append({"type": "identical_rows_in_train_and_test", "count": len(dup)})
        if time_column:
            t_train = [t for t in (_parse_time(r.get(time_column, "")) for r in train) if t is not None]
            t_test = [t for t in (_parse_time(r.get(time_column, "")) for r in test) if t is not None]
            if t_train and t_test and min(t_test) <= max(t_train):
                findings.append({"type": "temporal_overlap_test_not_after_train"})

    outcome = [parse_float(r.get(outcome_column)) for r in rows]
    for column in feature_columns:
        values = [parse_float(r.get(column)) for r in rows]
        pairs = [(v, o) for v, o in zip(values, outcome) if v is not None and o is not None]
        if len(pairs) < 3:
            continue
        x = np.array([p[0] for p in pairs])
        y = np.array([p[1] for p in pairs])
        if np.std(x) == 0 or np.std(y) == 0:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if abs(r) >= proxy_threshold:
            findings.append({"type": "proxy_feature", "column": column, "abs_pearson_r": round(abs(r), 6)})

    return {"leakage_detected": bool(findings), "findings": findings}
