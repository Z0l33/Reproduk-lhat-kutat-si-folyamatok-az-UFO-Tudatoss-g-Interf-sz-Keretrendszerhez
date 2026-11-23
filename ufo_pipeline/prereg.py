"""Preregistration loading, hashing and conformity checking.

An analysis is 'confirmatory' ONLY if every check here passes; otherwise it is 'exploratory'.
Limitation: `registered_at` is self-reported unless `registry_id` points to an external,
independently time-stamped registration (e.g. OSF or a Zenodo deposit). A local timestamp
alone can not prove that the data were unseen at registration.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .provenance import load_json, sha256_of_obj

PREREG_REQUIRED = ("prereg_id", "registered_at", "registry_id", "hypothesis", "dataset_id", "analysis")
ANALYSIS_REQUIRED = ("group_column", "group_a", "group_b", "outcome_column", "expected_direction",
                     "alpha", "sesoi_g", "n_permutations", "n_bootstrap", "seed", "require_replication")
DIRECTIONS = ("a>b", "a<b", "any")
PLACEHOLDER_PREFIXES = ("REPLACE", "TEMPLATE", "TODO", "TBD")

PathLike = Union[str, Path]


def load_prereg(path: PathLike) -> Dict[str, Any]:
    return load_json(path)


def prereg_hash(prereg: Dict[str, Any]) -> str:
    return sha256_of_obj(prereg)


def parse_aware_datetime(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def check_prereg(prereg: Dict[str, Any], analysis_params: Dict[str, Any], *, dataset_id: str,
                 dataset_first_retrieved_at: Optional[str]) -> List[str]:
    """Return a list of problems; an empty list means the analysis is confirmatory."""
    problems: List[str] = []
    for key in PREREG_REQUIRED:
        if key not in prereg:
            problems.append(f"preregistration missing field: {key}")
    spec = prereg.get("analysis") or {}
    for key in ANALYSIS_REQUIRED:
        if key not in spec:
            problems.append(f"preregistration.analysis missing field: {key}")
    if problems:
        return problems

    registry_id = str(prereg["registry_id"]).strip()
    if not registry_id or registry_id.upper().startswith(PLACEHOLDER_PREFIXES):
        problems.append("registry_id is empty or a placeholder (no external registration)")
    if spec["expected_direction"] not in DIRECTIONS:
        problems.append(f"expected_direction must be one of {DIRECTIONS}")
    if not isinstance(spec["sesoi_g"], (int, float)) or spec["sesoi_g"] <= 0:
        problems.append("sesoi_g must be a number > 0")
    if prereg["dataset_id"] != dataset_id:
        problems.append(f"dataset_id differs from preregistration ({dataset_id!r} vs {prereg['dataset_id']!r})")

    registered = parse_aware_datetime(prereg["registered_at"])
    retrieved = parse_aware_datetime(dataset_first_retrieved_at) if dataset_first_retrieved_at else None
    if registered is None:
        problems.append("registered_at is not a timezone-aware ISO 8601 timestamp")
    elif retrieved is None:
        problems.append("first retrieval time of the dataset is unknown; cannot show data were unseen")
    elif not registered < retrieved:
        problems.append("preregistration is not earlier than the first retrieval of the data")

    for key in ANALYSIS_REQUIRED:
        if spec.get(key) != analysis_params.get(key):
            problems.append(f"deviation from preregistration: {key} "
                            f"(registered {spec.get(key)!r}, used {analysis_params.get(key)!r})")
    return problems
