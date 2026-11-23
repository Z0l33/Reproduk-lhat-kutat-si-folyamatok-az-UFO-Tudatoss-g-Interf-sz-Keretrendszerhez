"""Machine-readable provenance records and local-file integrity checks."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

from .hashing import digests_equal, sha256_file

PROVENANCE_SCHEMA_VERSION = 1
# File statuses that count as integrity-verified against the source.
VERIFIED_FILE_STATUSES = ("verified", "verified_pin_only")
FILE_STATUSES = VERIFIED_FILE_STATUSES + ("unpinned", "failed", "synthetic")
DATASET_STATUSES = ("verified", "unpinned", "failed", "synthetic")

# NB: \Z, not $ -- in Python "$" also matches just before a trailing newline.
SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")  # lowercase hex only
RECORD_ID_RE = re.compile(r"^[0-9]+\Z")
VERSION_DOI_RE = re.compile(r"^10\.5281/zenodo\.([0-9]+)\Z")

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


def _plain_name(name: Any) -> bool:
    return (isinstance(name, str) and name not in ("", ".", "..") and Path(name).name == name
            and "/" not in name and "\\" not in name)


def _safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def _aware_iso(value: Any) -> bool:
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def validate_provenance(prov: Any) -> List[str]:
    """Strict structural validation of a provenance record. Returns a list of problems.

    Hashes must be lowercase 64-hex SHA-256 strings; file names must be plain and unique;
    statuses must come from closed vocabularies; a 'verified' dataset must be fully
    consistent (no errors, every file verified, timezone-aware first-retrieval time).
    """
    if not isinstance(prov, dict):
        return ["provenance must be a JSON object"]
    problems: List[str] = []
    for key in ("schema_version", "dataset_id", "source", "status", "files", "synthetic"):
        if key not in prov:
            problems.append(f"missing key: {key}")
    if prov.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        problems.append(f"unsupported schema_version: {prov.get('schema_version')!r}")
    if not isinstance(prov.get("synthetic"), bool):
        problems.append("synthetic must be a boolean")
    if prov.get("status") not in DATASET_STATUSES:
        problems.append(f"invalid dataset status: {prov.get('status')!r}")
    if prov.get("synthetic") is True and prov.get("status") != "synthetic":
        problems.append("synthetic dataset must have status 'synthetic'")
    if prov.get("synthetic") is False and prov.get("status") == "synthetic":
        problems.append("status 'synthetic' requires synthetic=true")

    if prov.get("source") == "zenodo":
        if prov.get("synthetic") is True:
            problems.append("a zenodo record cannot be synthetic")
        record_id, doi = prov.get("record_id"), prov.get("doi")
        match = VERSION_DOI_RE.match(doi) if isinstance(doi, str) else None
        if not (isinstance(record_id, str) and RECORD_ID_RE.match(record_id)):
            problems.append("zenodo record_id must be a numeric string")
        if not match:
            problems.append("zenodo doi must be a version DOI 10.5281/zenodo.<record_id>")
        elif isinstance(record_id, str) and match.group(1) != record_id:
            problems.append("zenodo doi does not match record_id")

    files = prov.get("files")
    if not isinstance(files, list):
        problems.append("files must be a list")
        files = []
    names: List[str] = []
    for index, entry in enumerate(files):
        label = f"files[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be an object")
            continue
        name = entry.get("name")
        if _plain_name(name):
            names.append(name)
        else:
            problems.append(f"{label}.name must be a plain file name")
        status = entry.get("status")
        if status not in FILE_STATUSES:
            problems.append(f"{label}.status invalid: {status!r}")
        sha = entry.get("sha256")
        well_formed = isinstance(sha, str) and bool(SHA256_RE.match(sha))
        if sha is None and status == "failed":
            pass  # a failed download may legitimately have no hash
        elif not well_formed:
            problems.append(f"{label}.sha256 must be 64 lowercase hex characters")
        size = entry.get("size")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            problems.append(f"{label}.size must be a non-negative integer")
        if entry.get("path") is not None and not _safe_relative_path(entry["path"]):
            problems.append(f"{label}.path must be a safe relative path")
    if len(names) != len(set(names)):
        problems.append("duplicate file names in files")

    if prov.get("status") == "verified":
        if not files:
            problems.append("verified dataset lists no files")
        if prov.get("errors"):
            problems.append("verified dataset has errors")
        for index, entry in enumerate(files):
            if isinstance(entry, dict) and entry.get("status") not in VERIFIED_FILE_STATUSES:
                problems.append(f"verified dataset but files[{index}] is not verified")
        if not _aware_iso(prov.get("first_retrieved_at")):
            problems.append("verified dataset needs a timezone-aware first_retrieved_at")
    return problems


def check_local_file(prov: Dict[str, Any], file_path: PathLike) -> Dict[str, Any]:
    """Identify a local file in a provenance record and check its content.

    Identity is NOT by name alone. The file must exist; its name must match exactly one
    record entry; the recorded relative `path` (if any) must be a suffix of the real path;
    the size must match when recorded; and the recomputed SHA-256 must equal the recorded,
    well-formed digest. A file whose content equals a recorded file but whose name differs
    is refused rather than silently accepted.
    """
    file_path = Path(file_path)
    result: Dict[str, Any] = {"ok": False, "name": file_path.name, "expected": None,
                              "actual": None, "message": ""}
    if not file_path.is_file():
        result["message"] = "file not found"
        return result
    listed = [f for f in prov.get("files", []) if isinstance(f, dict)]
    entries = [f for f in listed if f.get("name") == file_path.name]
    actual = sha256_file(file_path)
    result["actual"] = actual
    if not entries:
        twins = [f.get("name") for f in listed
                 if isinstance(f.get("sha256"), str) and digests_equal(actual, f["sha256"])]
        result["message"] = "file not listed in provenance record"
        if twins:
            result["message"] += f" (content equals recorded file {twins[0]!r}; refusing renamed file)"
        return result
    if len(entries) > 1:
        result["message"] = "ambiguous: several provenance entries share this file name"
        return result
    entry = entries[0]
    expected = entry.get("sha256")
    result["expected"] = expected
    if not (isinstance(expected, str) and SHA256_RE.match(expected)):
        result["message"] = "recorded sha256 is missing or malformed"
        return result
    recorded_path = entry.get("path")
    if recorded_path is not None:
        if not (_safe_relative_path(recorded_path)
                and file_path.resolve().as_posix().endswith("/" + recorded_path)):
            result["message"] = f"file location does not match recorded path {recorded_path!r}"
            return result
    size = entry.get("size")
    if size is not None and file_path.stat().st_size != size:
        result["message"] = "size MISMATCH"
        return result
    if not digests_equal(actual, expected):
        result["message"] = "sha256 MISMATCH"
        return result
    result.update(ok=True, message="sha256 matches")
    return result


def dataset_claims_verified(prov: Dict[str, Any]) -> bool:
    """What the RECORD says: strictly valid, status 'verified', no errors. This is a claim, not proof."""
    return not validate_provenance(prov) and prov.get("status") == "verified" and not prov.get("errors")


def dataset_is_verified(prov: Dict[str, Any], data_dir: PathLike) -> bool:
    """True only if the record is valid AND every listed file exists in `data_dir` and its
    recomputed SHA-256 (plus size and path) matches the record. A status string alone is never trusted."""
    if not dataset_claims_verified(prov):
        return False
    base = Path(data_dir)
    return all(check_local_file(prov, base / (entry.get("path") or entry["name"]))["ok"]
               for entry in prov["files"])


def require_local_file_integrity(prov: Dict[str, Any], file_path: PathLike) -> Dict[str, Any]:
    result = check_local_file(prov, file_path)
    if not result["ok"]:
        raise IntegrityError(f"{result['name']}: {result['message']} "
                             f"(expected {result['expected']}, actual {result['actual']})")
    return result
