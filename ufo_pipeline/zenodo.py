"""Zenodo retrieval with source-checksum and SHA-256 verification.

No record IDs or DOIs are hard-coded: every dataset must be declared in a manifest
(data/manifest/datasets.json) by the researcher. The retrieval code has been exercised
only against synthetic fake responses (see tests); it has NOT been run against the live
Zenodo API from the environment in which it was written.
"""
from __future__ import annotations

import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Union

from . import __version__
from .hashing import digests_equal, hash_file, parse_checksum
from .provenance import (PROVENANCE_SCHEMA_VERSION, VERIFIED_FILE_STATUSES, load_json,
                         utc_now_iso)

API_ROOT = "https://zenodo.org/api/records/"
WEB_ROOT = "https://zenodo.org/records/"
ALLOWED_DOWNLOAD_PREFIX = "https://zenodo.org/"

RECORD_ID_RE = re.compile(r"^[0-9]+$")
VERSION_DOI_RE = re.compile(r"^10\.5281/zenodo\.([0-9]+)$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PathLike = Union[str, Path]


class ZenodoError(Exception):
    pass


class HttpClient(Protocol):
    def get_json(self, url: str) -> Dict[str, Any]: ...
    def download(self, url: str, dest: Path) -> None: ...


class UrllibClient:
    """Minimal stdlib HTTP client (HTTPS to zenodo.org only)."""

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout
        self.headers = {"User-Agent": f"ufo-pipeline/{__version__}", "Accept": "application/json"}

    def get_json(self, url: str) -> Dict[str, Any]:
        import json
        req = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))

    def download(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": self.headers["User-Agent"]})
        with urllib.request.urlopen(req, timeout=self.timeout) as response, open(dest, "wb") as out:  # noqa: S310
            shutil.copyfileobj(response, out, length=1024 * 1024)


def validate_entry(entry: Dict[str, Any]) -> List[str]:
    """Validate one manifest entry. Returns a list of problems (empty = valid)."""
    problems: List[str] = []
    if entry.get("synthetic"):
        problems.append("synthetic datasets must not be listed in the Zenodo manifest")
    if not SLUG_RE.match(str(entry.get("dataset_id", ""))):
        problems.append("dataset_id must be a lowercase slug (a-z, 0-9, '.', '_', '-')")
    record_id = str(entry.get("record_id", ""))
    if not RECORD_ID_RE.match(record_id):
        problems.append("record_id must be a numeric Zenodo record ID given as a string")
    match = VERSION_DOI_RE.match(str(entry.get("doi", "")))
    if not match:
        problems.append("doi must be a version DOI of the form 10.5281/zenodo.<record_id>")
    elif RECORD_ID_RE.match(record_id) and match.group(1) != record_id:
        problems.append("doi does not match record_id (use the version DOI of this exact record)")
    files = entry.get("files")
    if not isinstance(files, list) or not files:
        problems.append("files must be a non-empty list")
        return problems
    seen = set()
    for index, spec in enumerate(files):
        name = str(spec.get("name", ""))
        if (not name or name in (".", "..") or Path(name).name != name
                or "\\" in name or "/" in name):
            problems.append(f"files[{index}].name is empty or not a plain file name: {name!r}")
        if name in seen:
            problems.append(f"files[{index}].name duplicated: {name!r}")
        seen.add(name)
        pin = spec.get("sha256")
        if pin is not None and not SHA256_RE.match(str(pin)):
            problems.append(f"files[{index}].sha256 must be 64 lowercase hex characters")
        if spec.get("zenodo_checksum") is not None:
            try:
                parse_checksum(spec["zenodo_checksum"])
            except ValueError as exc:
                problems.append(f"files[{index}].zenodo_checksum invalid: {exc}")
    return problems


def load_manifest(path: PathLike) -> List[Dict[str, Any]]:
    manifest = load_json(path)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("datasets"), list):
        raise ZenodoError("manifest must contain schema_version=1 and a 'datasets' list")
    ids = [d.get("dataset_id") for d in manifest["datasets"]]
    if len(ids) != len(set(ids)):
        raise ZenodoError("duplicate dataset_id values in manifest")
    for entry in manifest["datasets"]:
        problems = validate_entry(entry)
        if problems:
            raise ZenodoError(f"invalid manifest entry {entry.get('dataset_id')!r}: " + "; ".join(problems))
    return manifest["datasets"]


def _file_index(record: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for item in record.get("files") or []:
        name = item.get("key") or item.get("filename") or item.get("name")
        links = item.get("links") or {}
        index[name] = {
            "size": item.get("size", item.get("filesize")),
            "checksum": item.get("checksum"),
            "url": links.get("self") or links.get("download"),
        }
    return index


def retrieve_dataset(entry: Dict[str, Any], dest_dir: PathLike, client: Optional[HttpClient] = None,
                     *, existing: Optional[Dict[str, Any]] = None,
                     now: Optional[str] = None) -> Dict[str, Any]:
    """Download and verify every file of one manifest entry; return a provenance record.

    A file is 'verified' only if (a) the download matches the checksum Zenodo reports and
    (b) its SHA-256 equals the SHA-256 pinned in the manifest. Without a pin the status is
    'unpinned' and the dataset is NOT considered verified.
    """
    client = client or UrllibClient()
    now = now or utc_now_iso()
    dest_dir = Path(dest_dir)
    prov: Dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "dataset_id": entry.get("dataset_id"),
        "source": "zenodo",
        "record_id": str(entry.get("record_id")),
        "doi": entry.get("doi"),
        "record_url": WEB_ROOT + str(entry.get("record_id")),
        "api_url": API_ROOT + str(entry.get("record_id")),
        "retrieved_at": now,
        "first_retrieved_at": (existing or {}).get("first_retrieved_at"),
        "tool": {"name": "ufo-pipeline", "version": __version__},
        "synthetic": False,
        "record_metadata": {},
        "files": [],
        "errors": [],
        "status": "failed",
    }
    problems = validate_entry(entry)
    if problems:
        prov["errors"] = [f"invalid_manifest_entry: {p}" for p in problems]
        return prov

    try:
        record = client.get_json(prov["api_url"])
    except Exception as exc:  # network / HTTP / JSON errors
        prov["errors"].append(f"record_fetch_failed: {exc}")
        return prov

    if str(record.get("id")) != prov["record_id"]:
        prov["errors"].append(f"record_id_mismatch: API returned {record.get('id')!r}")
    api_doi = record.get("doi")
    if api_doi != entry["doi"]:
        if record.get("conceptdoi") == entry["doi"]:
            prov["errors"].append("doi_is_concept_doi: pin the version DOI of this exact record "
                                  "(a concept DOI resolves to the latest version)")
        else:
            prov["errors"].append(f"doi_mismatch: manifest {entry['doi']!r} vs API {api_doi!r}")
    meta = record.get("metadata") or {}
    prov["record_metadata"] = {k: meta.get(k) for k in ("title", "version", "publication_date")
                               if meta.get(k) is not None}
    if prov["errors"]:
        return prov

    dest_dir.mkdir(parents=True, exist_ok=True)
    index = _file_index(record)
    for spec in entry["files"]:
        result = _retrieve_file(prov, spec, index.get(spec["name"]), dest_dir, client)
        prov["files"].append(result)
        if result["status"] != "failed" and not prov["first_retrieved_at"]:
            prov["first_retrieved_at"] = now

    if any(f["status"] == "failed" for f in prov["files"]):
        prov["status"] = "failed"
    elif all(f["status"] in VERIFIED_FILE_STATUSES for f in prov["files"]):
        prov["status"] = "verified"
    else:
        prov["status"] = "unpinned"
    return prov


def _retrieve_file(prov: Dict[str, Any], spec: Dict[str, Any], meta: Optional[Dict[str, Any]],
                   dest_dir: Path, client: HttpClient) -> Dict[str, Any]:
    name = spec["name"]
    result: Dict[str, Any] = {
        "name": name, "size": None, "sha256": None, "api_checksum": None,
        "api_checksum_verified": None, "pinned_sha256": spec.get("sha256"),
        "pin_status": "unpinned", "manifest_checksum_status": "not_given",
        "status": "failed", "errors": [],
    }
    if meta is None:
        result["errors"].append("file_not_in_record")
        return result
    result["api_checksum"] = meta.get("checksum")
    url = meta.get("url") or f"{WEB_ROOT}{prov['record_id']}/files/{urllib.parse.quote(name)}?download=1"
    if not url.startswith(ALLOWED_DOWNLOAD_PREFIX):
        result["errors"].append(f"unexpected_download_host: {url}")
        return result

    final_path = dest_dir / name
    part_path = dest_dir / (name + ".part")
    try:
        client.download(url, part_path)
    except Exception as exc:
        part_path.unlink(missing_ok=True)
        result["errors"].append(f"download_failed: {exc}")
        return result

    api_algorithm = None
    if meta.get("checksum"):
        try:
            api_algorithm, api_digest = parse_checksum(meta["checksum"])
        except ValueError as exc:
            result["errors"].append(f"api_checksum_unparseable: {exc}")
    algorithms = ("sha256",) if api_algorithm in (None, "sha256") else ("sha256", api_algorithm)
    hashes = hash_file(part_path, algorithms)
    result["sha256"] = hashes["sha256"]
    result["size"] = part_path.stat().st_size

    if meta.get("size") is not None and int(meta["size"]) != result["size"]:
        result["errors"].append(f"size_mismatch: API {meta['size']} vs downloaded {result['size']}")
    if api_algorithm:
        result["api_checksum_verified"] = digests_equal(hashes[api_algorithm], api_digest)
        if not result["api_checksum_verified"]:
            result["errors"].append("source_checksum_mismatch")
    if spec.get("zenodo_checksum"):
        same = parse_checksum(spec["zenodo_checksum"]) == (parse_checksum(meta["checksum"])
                                                           if meta.get("checksum") else None)
        result["manifest_checksum_status"] = "match" if same else "mismatch"
        if not same:
            result["errors"].append("manifest_zenodo_checksum_differs_from_api")
    if spec.get("sha256"):
        match = digests_equal(hashes["sha256"], spec["sha256"])
        result["pin_status"] = "match" if match else "mismatch"
        if not match:
            result["errors"].append("pinned_sha256_mismatch")

    if result["errors"]:
        part_path.unlink(missing_ok=True)  # never keep a file that failed verification
        return result
    part_path.replace(final_path)
    if result["pin_status"] == "match" and result["api_checksum_verified"]:
        result["status"] = "verified"
    elif result["pin_status"] == "match":
        result["status"] = "verified_pin_only"  # source offered no checksum; pin only
    else:
        result["status"] = "unpinned"  # source checksum OK but no SHA-256 pin to enforce
    return result
