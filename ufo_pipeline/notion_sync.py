"""Notion <-> pipeline bridge for the 'Submissions' database.

Design rules (see docs):
  * Notion rows are untrusted PROPOSALS. A row's 'Pipeline status' is display-only; nothing is
    ever trusted because Notion says 'verified'. Verification comes from retrieve_dataset().
  * Only rows in status 'submitted' are processed, every time from scratch.
  * The submitted SHA-256 is used as the pin, so a wrong claim makes the file 'failed'.
  * Rows naming a record or dataset_id that the repository manifest blocks (retrieval_blocked)
    are rejected before any network call, in dry run and in apply mode alike.
  * Dry run is the default and is metadata-only: it reads Zenodo record metadata, never
    downloads a file, never writes provenance and never writes to Notion.
  * The HTTP client is untested against the live Notion API.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .zenodo import load_manifest, validate_entry

# The repository manifest whose retrieval blocks every sync must honour (fail closed if missing).
DEFAULT_MANIFEST = Path(__file__).resolve().parent.parent / "data" / "manifest" / "datasets.json"

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# Notion property names (must match the database schema).
P_ID, P_RECORD, P_DOI, P_FILE = "Dataset ID", "Zenodo record ID", "Version DOI", "File name"
P_CLAIM, P_PERSONAL, P_STATUS = "Claimed SHA-256", "Contains personal data", "Pipeline status"
P_SHA, P_SIZE, P_SRC, P_AT = "Checked SHA-256", "Checked size (bytes)", "Source checksum", "Checked at"
P_RUN, P_DECISION = "Pipeline run URL", "Maintainer decision"
P_NOTES = "Pipeline notes"          # the ONLY free-text field the pipeline writes
P_SUBMITTER_NOTE = "Source / note"  # submitter-owned: never written by the pipeline

# Properties owned by the submitter or the maintainer. No payload built here may contain any of them.
PROTECTED_FIELDS = frozenset({P_ID, P_RECORD, P_DOI, P_FILE, P_CLAIM, P_PERSONAL, P_SUBMITTER_NOTE, P_DECISION})
# Properties the pipeline may write.
PIPELINE_FIELDS = frozenset({P_STATUS, P_SHA, P_SIZE, P_SRC, P_AT, P_RUN, P_NOTES})


_CONTAINERS = ("title", "rich_text")


def _decode_text(prop: Any, kind: str) -> Optional[str]:
    """Plain text of a PRESENT Notion title / rich_text property value (REST API shape).

    kind is the container the schema expects for this field ("title" or "rich_text"). Returns the text
    ("" for an empty list), or None when the value cannot be decoded unambiguously. None must never be
    read as "empty": parse_row marks the property as malformed and a submitted row is rejected.
    An ABSENT property is handled by the caller; an explicit null value arrives here and is None.

    Rejected (None): a non-object value (incl. null); a value without the expected container, with both
    containers, or whose "type" names another type; a container that is not a list; a segment that is
    not an object; a non-string plain_text; a "text" key that is not an object or lacks a string
    "content" (an explicit null content is malformed, not absent); content that disagrees with
    plain_text; a segment with neither plain_text nor a text-type content.
    """
    if kind not in _CONTAINERS:
        raise ValueError(f"unknown text container {kind!r}")
    if not isinstance(prop, dict):
        return None
    other = "rich_text" if kind == "title" else "title"
    if kind not in prop or other in prop:
        return None
    if "type" in prop and prop["type"] != kind:
        return None
    parts = prop[kind]
    if not isinstance(parts, list):
        return None
    chunks = []
    for part in parts:
        if not isinstance(part, dict):
            return None
        content: Optional[str] = None
        if "text" in part:
            nested = part["text"]
            if not isinstance(nested, dict) or not isinstance(nested.get("content"), str):
                return None
            content = nested["content"]
        if "plain_text" in part:
            plain = part["plain_text"]
            if not isinstance(plain, str):
                return None
            if content is not None and content != plain:
                return None
            chunks.append(plain)
        elif part.get("type") == "text" and content is not None:
            chunks.append(content)
        else:
            return None
    return "".join(chunks)


UNIDENTIFIED = "unidentified-row-"


def row_keys(rows: List[Dict[str, Any]]) -> List[str]:
    """Report key per row: the Notion page id, or 'unidentified-row-<n>' (n = 1-based position in the reply) when the
    page has no usable string id. The generated key exists only to tell rows apart inside a report; it is never a
    Notion page id and is never written to."""
    keys = []
    for n, row in enumerate(rows, 1):
        pid = row.get("page_id")
        keys.append(pid if isinstance(pid, str) and pid and not pid.startswith(UNIDENTIFIED) else f"{UNIDENTIFIED}{n}")
    return keys


def is_page_id(key: str) -> bool:
    return not key.startswith(UNIDENTIFIED)


def parse_row(page: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a Notion page object into plain values. Missing or oddly typed fields become empty."""
    malformed: List[str] = []
    if not isinstance(page, dict):
        page = {}
        malformed.append("page")
    raw_props = page.get("properties") or {}
    if not isinstance(raw_props, dict):
        malformed.append("properties")
    props: Dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}

    def select(name: str) -> Optional[str]:
        value = props.get(name)
        if value is None:
            return None
        sel = value.get("select") if isinstance(value, dict) else False
        if sel is False or not (sel is None or isinstance(sel, dict)):
            malformed.append(name)
            return None
        return None if sel is None else sel.get("name")

    def checkbox(name: str) -> bool:
        value = props.get(name)
        if value is None:
            return False
        if not isinstance(value, dict):
            malformed.append(name)
            return False
        return bool(value.get("checkbox"))

    def text(name: str, kind: str = "rich_text") -> str:
        if name not in props:   # absent: allowed, reads as empty (e.g. no claimed SHA-256 -> no pin)
            return ""
        value = _decode_text(props[name], kind)   # present, incl. explicit null
        if value is None:   # present but undecodable: never read as empty (an empty claim would drop the pin)
            malformed.append(name)
            return ""
        return value

    row = {
        "page_id": page.get("id"),
        "dataset_id": text(P_ID, "title"),
        "record_id": text(P_RECORD),
        "doi": text(P_DOI),
        "file_name": text(P_FILE),
        "claimed_sha256": text(P_CLAIM),
        "personal_data": checkbox(P_PERSONAL),
        "status": select(P_STATUS),
        "decision": select(P_DECISION),
    }
    row["malformed"] = sorted(set(malformed))   # properties with an unexpected JSON type; never trusted
    return row


def canonical_record_id(value: Any) -> str:
    """One spelling per record: ASCII digits with leading zeros removed ('023906585' -> '23906585').

    Anything that is not plain ASCII digits is returned unchanged (it fails validation later anyway).
    """
    text = str(value).strip()
    if text.isascii() and text.isdigit():
        return str(int(text))
    return text


def blocked_targets(manifest_entries: List[Dict[str, Any]]) -> Dict[Tuple[str, str], str]:
    """(kind, value) -> reason for every manifest entry with retrieval_blocked=true.

    kind is 'record' (Zenodo record ID) or 'dataset' (dataset_id); a row matching either is blocked.
    """
    blocked: Dict[Tuple[str, str], str] = {}
    for entry in manifest_entries:
        if entry.get("retrieval_blocked") is True:
            reason = str(entry.get("retrieval_blocked_reason", "")).strip() or "blocked in manifest"
            blocked[("record", canonical_record_id(entry.get("record_id")))] = reason
            blocked[("dataset", str(entry.get("dataset_id")))] = reason
    return blocked


def load_blocked_targets(extra_manifest: Any = None) -> Dict[Tuple[str, str], str]:
    """Blocks from the repository manifest, plus those of an optional extra manifest.

    The repository's own manifest is ALWAYS read: an extra manifest can add blocks, never remove
    them. Raises if either manifest is missing or invalid (fail closed).
    """
    blocked = blocked_targets(load_manifest(DEFAULT_MANIFEST))
    if extra_manifest:
        for key, reason in blocked_targets(load_manifest(extra_manifest)).items():
            blocked.setdefault(key, reason)
    return blocked


def rows_to_entries(rows: List[Dict[str, Any]], blocked: Optional[Dict[Tuple[str, str], str]] = None
                    ) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
    """Turn parsed 'submitted' rows into manifest entries (one entry per dataset_id).

    Returns (entries, problems_by_page_id). A row with problems yields no entry and is never
    downloaded. The claimed SHA-256 is used verbatim as the pin: no case folding or trimming,
    so a sloppy claim is reported rather than silently repaired.
    """
    problems: Dict[str, List[str]] = {}
    entries: Dict[str, Dict[str, Any]] = {}
    keys = row_keys(rows)
    for row, key in zip(rows, keys):
        bad = row.get("malformed") or []
        if not is_page_id(key):
            why = "page is not an object" if "page" in bad else "page has no usable page id"
            extra = [b_ for b_ in bad if b_ != "page"]
            problems[key] = [why + (f"; unexpected type in: {', '.join(extra)}" if extra else "") + "; row rejected"]
            continue
        if bad and (row.get("status") == "submitted" or P_STATUS in bad or "properties" in bad):
            problems[key] = [f"unexpected type in Notion propert{'y' if len(bad) == 1 else 'ies'}: "
                             + ", ".join(bad) + "; row rejected"]
            continue
        if row.get("status") != "submitted":
            continue
        pid = key
        issues: List[str] = []
        if row.get("personal_data"):
            issues.append("row is flagged as containing personal data; rejected")
        for kind, value in (("record", canonical_record_id(row["record_id"])),
                            ("dataset", row["dataset_id"].strip().lower())):
            reason = (blocked or {}).get((kind, value))
            if reason:
                issues.append(f"retrieval_blocked ({kind} {value} is blocked in the repository manifest: "
                              f"{reason}); rejected, nothing checked or downloaded")
                break
        entry = entries.get(row["dataset_id"])
        if entry and (entry["record_id"], entry["doi"]) != (row["record_id"], row["doi"]):
            issues.append("rows of one dataset_id disagree on record_id/doi")
        spec: Dict[str, Any] = {"name": row["file_name"]}
        if row["claimed_sha256"]:
            spec["sha256"] = row["claimed_sha256"]
        candidate = {"dataset_id": row["dataset_id"], "record_id": row["record_id"],
                     "doi": row["doi"], "files": [spec]}
        issues += validate_entry(candidate)
        if issues:
            problems[pid] = issues
            continue
        if entry:
            entry["files"].append(spec)
        else:
            entries[row["dataset_id"]] = candidate
    # a dataset whose merged entry is no longer valid (e.g. duplicate file names) is dropped
    valid: List[Dict[str, Any]] = []
    for entry in entries.values():
        merged = validate_entry(entry)
        if merged:
            for row, key in zip(rows, keys):
                if row.get("dataset_id") == entry["dataset_id"] and row.get("status") == "submitted" and is_page_id(key):
                    problems.setdefault(key, []).extend(merged)
        else:
            valid.append(entry)
    return valid, problems


def _rich(value: str) -> Dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}


def properties_from_provenance(prov: Dict[str, Any], file_name: str, run_url: Optional[str] = None
                               ) -> Dict[str, Any]:
    """Notion property payload (REST shape) reporting the result for one file of a dataset."""
    file_rec = next((f for f in prov.get("files", []) if f.get("name") == file_name), None)
    if prov.get("status") == "failed" and file_rec is None:
        status = "failed"
    else:
        status = (file_rec or {}).get("status", "failed")
    if status not in ("verified", "verified_pin_only", "unpinned", "failed"):
        status = "failed"
    props: Dict[str, Any] = {P_STATUS: {"select": {"name": status}},
                             P_AT: {"date": {"start": prov.get("retrieved_at")}}}
    if file_rec:
        checked = file_rec.get("api_checksum_verified")
        props[P_SRC] = {"select": {"name": "ok" if checked is True else
                                   "mismatch" if checked is False else "unavailable"}}
        if file_rec.get("sha256"):
            props[P_SHA] = _rich(file_rec["sha256"])
        if file_rec.get("size") is not None:
            props[P_SIZE] = {"number": file_rec["size"]}
    errors = list(prov.get("errors") or []) + [e for f in prov.get("files", [])
                                                if f.get("name") == file_name for e in f.get("errors", [])]
    if errors:
        props[P_NOTES] = _rich("; ".join(errors))
    if run_url:
        props[P_RUN] = {"url": run_url}
    return props


def rejection_properties(issues: List[str]) -> Dict[str, Any]:
    return {P_STATUS: {"select": {"name": "failed"}}, P_NOTES: _rich("; ".join(issues))}


class NotionClient(Protocol):
    def query_submitted(self, database_id: str) -> List[Dict[str, Any]]: ...
    def update_page(self, page_id: str, properties: Dict[str, Any]) -> None: ...


class UrllibNotionClient:
    """Minimal REST client. NOT yet exercised against the live Notion API."""

    def __init__(self, token: str, timeout: int = 30) -> None:
        self._headers = {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION,
                         "Content-Type": "application/json"}
        self._timeout = timeout

    def _call(self, method: str, url: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec - fixed https host
            return json.loads(resp.read().decode("utf-8"))

    def query_submitted(self, database_id: str) -> List[Dict[str, Any]]:
        pages: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            body: Dict[str, Any] = {"filter": {"property": P_STATUS,
                                                "select": {"equals": "submitted"}}}
            if cursor:
                body["start_cursor"] = cursor
            out = self._call("POST", f"{API}/databases/{database_id}/query", body)
            pages += out.get("results", [])
            if not out.get("has_more"):
                return pages
            cursor = out.get("next_cursor")
            if not isinstance(cursor, str) or not cursor:
                raise ValueError("Notion reply says has_more but gives no next_cursor; refusing to repeat the query")

    def update_page(self, page_id: str, properties: Dict[str, Any]) -> None:
        self._call("PATCH", f"{API}/pages/{page_id}", {"properties": properties})


def sync(client: NotionClient, database_id: str, dest_dir: Any, provenance_dir: Any,
         retriever: Any, *, apply: bool = False, run_url: Optional[str] = None,
         blocked: Optional[Dict[Tuple[str, str], str]] = None,
         metadata_checker: Any = None) -> Dict[str, Any]:
    """Process every 'submitted' row.

    blocked: retrieval blocks to honour; by default they are read from the repository manifest
    (fail closed: a missing or invalid manifest raises before Notion is queried).

    Dry run (apply=False): metadata only. metadata_checker (default zenodo.check_record_metadata)
    reads record metadata; the retriever is never called, nothing is downloaded, no provenance
    file is written and nothing is written to Notion.

    Apply (apply=True): the retriever (ufo_pipeline.zenodo.retrieve_dataset, injected for tests)
    downloads and verifies; provenance is written and results are written back to Notion.
    """
    from .provenance import load_json, write_json

    if blocked is None:
        blocked = load_blocked_targets()
    pages = client.query_submitted(database_id)
    parsed = [parse_row(p) for p in pages]
    entries, problems = rows_to_entries(parsed, blocked)
    report: Dict[str, Any] = {"rows": len(parsed), "rejected": problems, "datasets": {},
                              "mode": "apply" if apply else "dry_run_metadata_only", "downloaded": False}
    keys = row_keys(parsed)
    # a result is written back only to a row with a real page id; 'unidentified-row-<n>' keys are report-only
    updates: List[Tuple[str, Dict[str, Any]]] = [
        (pid, rejection_properties(issues)) for pid, issues in problems.items() if is_page_id(pid)]
    if not apply:
        if metadata_checker is None:
            from .zenodo import check_record_metadata as metadata_checker
        checks: Dict[str, Any] = {}
        for entry in entries:
            result = metadata_checker(entry)
            checks[entry["dataset_id"]] = result
            report["datasets"][entry["dataset_id"]] = result["status"]
        report["metadata_checks"] = checks
        report["updates"] = len(updates)   # what apply would write for rejected rows; not written now
        report["applied"] = False
        return report

    prov_dir = Path(provenance_dir)
    for entry in entries:
        path = prov_dir / f"{entry['dataset_id']}.json"
        existing = load_json(path) if path.exists() else None
        prov = retriever(entry, dest_dir, existing=existing)
        write_json(path, prov)
        report["downloaded"] = True
        report["datasets"][entry["dataset_id"]] = prov["status"]
        for row, key in zip(parsed, keys):
            if row["dataset_id"] == entry["dataset_id"] and row["status"] == "submitted" \
                    and key not in problems and is_page_id(key):
                updates.append((key, properties_from_provenance(prov, row["file_name"], run_url)))
    report["updates"] = len(updates)
    report["applied"] = True
    for pid, props in updates:
        foreign = set(props) - PIPELINE_FIELDS
        if foreign:   # defence in depth: never touch submitter-owned or unknown properties
            raise ValueError(f"refusing to write non-pipeline Notion properties: {sorted(foreign)}")
        client.update_page(pid, props)
    return report
