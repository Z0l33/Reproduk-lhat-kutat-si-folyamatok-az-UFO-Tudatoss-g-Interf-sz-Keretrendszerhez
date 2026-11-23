"""Machine-readable provenance records and local-file integrity checks."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

from .hashing import digests_equal, sha256_file

PROVENANCE_SCHEMA_VERSION = 1
# File statuses that count as integrity-verified against the source.
VERIFIED_FILE_STATUSES = ("verified", "verified_pin_only")

PathLike = Union[str, Path]


class IntegrityError(Exception):
    """A local data file does not match its recorded SHA-256."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_of_obj(obj: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def write_json(path: PathLike, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def load_json(path: PathLike) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_provenance(prov: Dict[str, Any]) -> List[str]:
    """Structural validation of a provenance record. Returns a list of problems."""
    problems: List[str] = []
    for key in ("schema_version", "dataset_id", "source", "status", "files", "synthetic"):
        if key not in prov:
            problems.append(f"missing key: {key}")
    if prov.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        problems.append(f"unsupported schema_version: {prov.get('schema_version')!r}")
    for index, entry in enumerate(prov.get("files") or []):
        for key in ("name", "sha256", "status"):
            if key not in entry:
                problems.append(f"files[{index}] missing key: {key}")
    if prov.get("source") == "zenodo":
        for key in ("record_id", "doi"):
            if not prov.get(key):
                problems.append(f"zenodo provenance missing {key}")
    return problems


def dataset_is_verified(prov: Dict[str, Any]) -> bool:
    """True only for a non-failed dataset whose every file is source-verified and SHA-256 pinned."""
    if prov.get("status") != "verified" or prov.get("errors"):
        return False
    files = prov.get("files") or []
    return bool(files) and all(f.get("status") in VERIFIED_FILE_STATUSES for f in files)


def check_local_file(prov: Dict[str, Any], file_path: PathLike) -> Dict[str, Any]:
    """Compare a local file's SHA-256 with the value recorded in the provenance record."""
    file_path = Path(file_path)
    recorded = {f["name"]: f.get("sha256") for f in prov.get("files", [])}
    if file_path.name not in recorded:
        return {"ok": False, "name": file_path.name, "expected": None, "actual": None,
                "message": "file not listed in provenance record"}
    actual = sha256_file(file_path)
    expected = recorded[file_path.name]
    ok = bool(expected) and digests_equal(actual, expected)
    return {"ok": ok, "name": file_path.name, "expected": expected, "actual": actual,
            "message": "sha256 matches" if ok else "sha256 MISMATCH"}


def require_local_file_integrity(prov: Dict[str, Any], file_path: PathLike) -> Dict[str, Any]:
    result = check_local_file(prov, file_path)
    if not result["ok"]:
        raise IntegrityError(f"{result['name']}: {result['message']} "
                             f"(expected {result['expected']}, actual {result['actual']})")
    return result
