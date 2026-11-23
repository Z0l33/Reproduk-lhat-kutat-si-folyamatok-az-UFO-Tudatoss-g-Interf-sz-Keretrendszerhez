"""Notion <-> pipeline bridge for the 'Submissions' database.

Design rules (see docs):
  * Notion rows are untrusted PROPOSALS. A row's 'Pipeline status' is display-only; nothing is
    ever trusted because Notion says 'verified'. Verification comes from retrieve_dataset().
  * Only rows in status 'submitted' are processed, every time from scratch.
  * The submitted SHA-256 is used as the pin, so a wrong claim makes the file 'failed'.
  * Dry run is the default; the HTTP client is untested against the live Notion API.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, Tuple

from .zenodo import validate_entry

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"

# Notion property names (must match the database schema).
P_ID, P_RECORD, P_DOI, P_FILE = "Dataset ID", "Zenodo record ID", "Version DOI", "File name"
P_CLAIM, P_PERSONAL, P_STATUS = "Claimed SHA-256", "Contains personal data", "Pipeline status"
P_SHA, P_SIZE, P_SRC, P_AT = "Checked SHA-256", "Checked size (bytes)", "Source checksum", "Checked at"
P_RUN, P_DECISION = "Pipeline run URL", "Maintainer decision"


def _text(prop: Any) -> str:
    """Plain text of a Notion title / rich_text property value (REST API shape)."""
    if not isinstance(prop, dict):
        return ""
    parts = prop.get("title") if "title" in prop else prop.get("rich_text")
    if not isinstance(parts, list):
        return ""
    return "".join(str(p.get("plain_text", "")) for p in parts if isinstance(p, dict))


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

    row = {
        "page_id": page.get("id"),
        "dataset_id": _text(props.get(P_ID)),
        "record_id": _text(props.get(P_RECORD)),
        "doi": _text(props.get(P_DOI)),
        "file_name": _text(props.get(P_FILE)),
        "claimed_sha256": _text(props.get(P_CLAIM)),
        "personal_data": checkbox(P_PERSONAL),
        "status": select(P_STATUS),
        "decision": select(P_DECISION),
    }
    row["malformed"] = sorted(set(malformed))   # properties with an unexpected JSON type; never trusted
    return row


def rows_to_entries(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, List[str]]]:
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
        props["Source / note"] = _rich("; ".join(errors))
    if run_url:
        props[P_RUN] = {"url": run_url}
    return props


def rejection_properties(issues: List[str]) -> Dict[str, Any]:
    return {P_STATUS: {"select": {"name": "failed"}}, "Source / note": _rich("; ".join(issues))}


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
         retriever: Any, *, apply: bool = False, run_url: Optional[str] = None) -> Dict[str, Any]:
    """Process every 'submitted' row. With apply=False nothing is written to Notion (dry run).

    The retriever is ufo_pipeline.zenodo.retrieve_dataset (injected for tests). Provenance
    records are written to provenance_dir either way; Notion is only a display of the result.
    """
    from pathlib import Path
    from .provenance import load_json, write_json

    pages = client.query_submitted(database_id)
    parsed = [parse_row(p) for p in pages]
    entries, problems = rows_to_entries(parsed)
    report: Dict[str, Any] = {"rows": len(parsed), "rejected": problems, "datasets": {}}
    keys = row_keys(parsed)
    # a result is written back only to a row with a real page id; 'unidentified-row-<n>' keys are report-only
    updates: List[Tuple[str, Dict[str, Any]]] = [
        (pid, rejection_properties(issues)) for pid, issues in problems.items() if is_page_id(pid)]
    prov_dir = Path(provenance_dir)
    for entry in entries:
        path = prov_dir / f"{entry['dataset_id']}.json"
        existing = load_json(path) if path.exists() else None
        prov = retriever(entry, dest_dir, existing=existing)
        write_json(path, prov)
        report["datasets"][entry["dataset_id"]] = prov["status"]
        for row, key in zip(parsed, keys):
            if row["dataset_id"] == entry["dataset_id"] and row["status"] == "submitted" \
                    and key not in problems and is_page_id(key):
                updates.append((key, properties_from_provenance(prov, row["file_name"], run_url)))
    report["updates"] = len(updates)
    report["applied"] = apply
    if apply:
        for pid, props in updates:
            client.update_page(pid, props)
    return report
