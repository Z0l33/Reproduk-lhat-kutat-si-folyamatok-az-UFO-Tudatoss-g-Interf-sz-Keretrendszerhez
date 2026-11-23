"""Column access control between data loading and analysis.

Rules (fail closed):
  * a real (non-synthetic) dataset needs an explicit `data_access.allowed_columns` list and an entry in
    policies/sensitive_columns.json; synthetic test data may default the allowlist to the columns the
    config references;
  * denied columns (per-dataset list + global name patterns) can be neither allowed nor referenced
    anywhere in the config (any string or key in the config is scanned, not only the known fields);
  * all checks run BEFORE the data file is opened; the loader then materialises ONLY allowed columns,
    so a denied column never exists in the rows handed to validation, leakage or statistics;
  * the policy location is fixed; the config cannot point to another policy.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from .hashing import sha256_file

PathLike = Union[str, Path]
POLICY_REL_PATH = "policies/sensitive_columns.json"
KNOWN_TOP_LEVEL = {"analysis_id", "dataset_provenance", "data_file", "preregistration", "validation",
                   "leakage", "analysis", "data_access"}
_PATH_KEYS = ("analysis_id", "dataset_provenance", "data_file", "preregistration")
_ACCESS_KEYS = {"allowed_columns"}


# Built-in fallback so that tools which only DISPLAY values (profile) never list values of columns whose
# names look sensitive, even when no policy file is present. The policy file adds to this, never replaces it.
DEFAULT_DENIED_PATTERNS = (
    r"(^|[_ -])(name|vorname|nachname|email|e-mail|phone|telefon|adresse|address|plz|zip|postal|postleit)([_ -]|$)",
    r"(^|[_ -])(sachverhalt|narrative|freitext|free[_ -]?text|beschreibung|sichtungsort)([_ -]|$)",
)


class ColumnAccessError(ValueError):
    """A config tried to use a column the access policy does not permit. No data was read."""


def _canon(name: str) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def load_policy(base_dir: PathLike) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    path = Path(base_dir) / POLICY_REL_PATH
    if not path.exists():
        return None, None
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ColumnAccessError(f"column policy unreadable: {exc}") from exc
    problems = validate_policy(policy)
    if problems:
        raise ColumnAccessError("column policy invalid: " + "; ".join(problems))
    return policy, sha256_file(path)


def validate_policy(policy: Any) -> List[str]:
    if not isinstance(policy, dict):
        return ["policy must be a JSON object"]
    problems: List[str] = []
    if policy.get("schema_version") != 1:
        problems.append("unsupported schema_version")
    patterns = policy.get("global_denied_column_patterns", [])
    if not isinstance(patterns, list):
        problems.append("global_denied_column_patterns must be a list")
    else:
        for p in patterns:
            try:
                re.compile(p, re.I)
            except (re.error, TypeError):
                problems.append(f"invalid pattern: {p!r}")
    datasets = policy.get("datasets")
    if not isinstance(datasets, dict):
        problems.append("datasets must be an object")
    else:
        for ds, entry in datasets.items():
            cols = entry.get("denied_columns") if isinstance(entry, dict) else None
            if not isinstance(cols, list) or not all(isinstance(c, str) and c for c in cols):
                problems.append(f"datasets.{ds}.denied_columns must be a list of column names")
    return problems


def _strings(node: Any, where: str = "") -> Iterator[Tuple[str, str]]:
    """Every string key and value in a config, with its location."""
    if isinstance(node, dict):
        for k, v in node.items():
            if where == "" and k in _PATH_KEYS:
                continue
            yield (f"{where}.{k}" if where else str(k)), str(k)
            yield from _strings(v, f"{where}.{k}" if where else str(k))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _strings(v, f"{where}[{i}]")
    elif isinstance(node, str):
        yield where, node


def referenced_columns(config: Dict[str, Any]) -> Dict[str, List[str]]:
    refs: Dict[str, List[str]] = {}

    def add(name: Any, where: str) -> None:
        if isinstance(name, str) and name:
            refs.setdefault(name, []).append(where)

    v = config.get("validation") or {}
    for col in (v.get("columns") or {}):
        add(col, "validation.columns")
    add(v.get("id_column"), "validation.id_column")
    leak = config.get("leakage") or {}
    for col in leak.get("feature_columns") or []:
        add(col, "leakage.feature_columns")
    add(leak.get("split_column"), "leakage.split_column")
    add(leak.get("time_column"), "leakage.time_column")
    a = config.get("analysis") or {}
    add(a.get("group_column"), "analysis.group_column")
    add(a.get("outcome_column"), "analysis.outcome_column")
    return refs


def resolve_access(config: Dict[str, Any], prov: Dict[str, Any], base_dir: PathLike) -> Dict[str, Any]:
    """Decide which columns may be loaded. Performs NO data-file I/O."""
    unknown = sorted(set(config) - KNOWN_TOP_LEVEL)
    if unknown:
        raise ColumnAccessError(f"unknown config section(s) {unknown}: column references must sit in "
                                "validation, leakage, analysis or data_access")
    access = config.get("data_access")
    if access is not None:
        if not isinstance(access, dict) or set(access) - _ACCESS_KEYS:
            raise ColumnAccessError("data_access may only contain 'allowed_columns' "
                                    "(the policy location cannot be overridden)")
    synthetic = prov.get("synthetic") is True
    refs = referenced_columns(config)

    allowed_cfg = (access or {}).get("allowed_columns")
    if allowed_cfg is None:
        if not synthetic:
            raise ColumnAccessError("data_access.allowed_columns is required for a real dataset")
        allowed = sorted(refs)
        mode = "synthetic_default_allowlist"
    else:
        if (not isinstance(allowed_cfg, list) or not allowed_cfg
                or not all(isinstance(c, str) and c for c in allowed_cfg)
                or len({_canon(c) for c in allowed_cfg}) != len(allowed_cfg)):
            raise ColumnAccessError("data_access.allowed_columns must be a non-empty list of unique names")
        allowed = list(allowed_cfg)
        mode = "explicit_allowlist"

    policy, policy_sha = load_policy(base_dir)
    dataset_id = prov.get("dataset_id")
    denied_exact: set = set()
    patterns: List[Any] = []
    if policy is None:
        if not synthetic:
            raise ColumnAccessError(f"{POLICY_REL_PATH} is required for a real dataset and was not found")
    else:
        patterns = [re.compile(p, re.I) for p in policy.get("global_denied_column_patterns", [])]
        entry = (policy.get("datasets") or {}).get(dataset_id)
        if entry is None and not synthetic:
            raise ColumnAccessError(f"no entry for dataset {dataset_id!r} in {POLICY_REL_PATH}: "
                                    "a real dataset needs an explicit access decision")
        denied_exact = {_canon(c) for c in (entry or {}).get("denied_columns", [])}

    def is_denied(name: str) -> bool:
        return _canon(name) in denied_exact or any(p.search(name) for p in patterns)

    for name in allowed:
        if is_denied(name):
            raise ColumnAccessError(f"column {name!r} is denied by the access policy and cannot be allowed")
    for name, where in refs.items():
        if is_denied(name):
            raise ColumnAccessError(f"config references denied column {name!r} at {sorted(set(where))}")
        if name not in allowed:
            raise ColumnAccessError(f"config references column {name!r} at {sorted(set(where))} "
                                    "that is not in the allowlist")
    for where, text in _strings(config):  # catch references hidden in unexpected places
        if _canon(text) in denied_exact:
            raise ColumnAccessError(f"config mentions denied column {text!r} at {where}")
    return {"mode": mode, "allowed_columns": allowed, "policy_sha256": policy_sha,
            "denied_exact": sorted(denied_exact), "_is_denied": is_denied}


def load_csv_allowlisted(path: PathLike, allowed: Sequence[str],
                         is_denied: Any = None) -> Tuple[List[str], List[Dict[str, str]], Dict[str, Any]]:
    """Read a CSV keeping ONLY allowed columns. Other columns are dropped while parsing."""
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ColumnAccessError("data file is empty")
        index = {name: i for i, name in enumerate(header)}
        missing = [c for c in allowed if c not in index]
        if missing:
            raise ColumnAccessError(f"allowed column(s) not in the data file: {missing}")
        picked = [(c, index[c]) for c in allowed]
        rows = [{c: (r[i] if i < len(r) else "") for c, i in picked} for r in reader]
    info = {"columns_in_file": len(header), "columns_loaded": len(picked),
            "columns_not_loaded": len(header) - len(picked),
            "policy_denied_columns_present_in_file": sorted(
                c for c in header if is_denied is not None and is_denied(c))}
    return [c for c, _ in picked], rows, info


def assert_only_allowed(columns: Sequence[str], rows: Sequence[Dict[str, Any]], allowed: Sequence[str]) -> None:
    """Defence in depth: nothing outside the allowlist may be present in what the analysis receives."""
    extra = set(columns) - set(allowed)
    for row in rows[:1] + rows[-1:]:
        extra |= set(row) - set(allowed)
    if extra:
        raise ColumnAccessError(f"internal error: non-allowlisted column(s) in loaded data: {sorted(extra)}")


def denied_name_matcher(base_dir: PathLike) -> Any:
    """Conservative name matcher for display tools: a column counts as denied if ANY dataset in the policy
    denies it, if a global pattern matches, or if a built-in default pattern matches."""
    policy, _ = load_policy(base_dir)
    exact: set = set()
    patterns = [re.compile(p, re.I) for p in DEFAULT_DENIED_PATTERNS]
    if policy:
        patterns += [re.compile(p, re.I) for p in policy.get("global_denied_column_patterns", [])]
        for entry in (policy.get("datasets") or {}).values():
            exact |= {_canon(c) for c in entry.get("denied_columns", [])}
    return lambda name: _canon(name) in exact or any(p.search(name) for p in patterns)
