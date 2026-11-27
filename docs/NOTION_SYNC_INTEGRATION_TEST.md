# notion-sync: isolated integration test log

Status: **in progress, not complete.** Merge of PR #20 and PR #21 stays on HOLD until the
metadata and write-back phases below have run and passed. Production: HOLD (unchanged).
GEP and SDDb: `retrieval_blocked` (unchanged).

## Test environment

| Item | Value |
|---|---|
| Notion database | `UFO Pipeline TEST – Submissions v1.4` (`4246cb55-c9f4-812a-b024-e7816c719c3b`), private, synthetic data only |
| Token | repository secret `NOTION_TOKEN` (never printed; masked in logs) |
| Workflow | `.github/workflows/notion-sync.yml`, `workflow_dispatch`, `permissions: contents: read` |
| Code under test | branch `integration/pr20-pr21` = `main` + PR #20 (`fix/notion-personal-flag-fail-closed`) + PR #21 (`fix/notion-text-decoding`) |

## Runs so far (all dry run, metadata-only, nothing written to Notion)

| Run | Code | Submitted rows | Result |
|---|---|---|---|
| 45922879238 | `main` | 4 | all rejected, incl. `dataset_id must be a lowercase slug` — title not decoded (the bug PR #21 addresses) |
| 45924733809 | `main` | 0 | empty report: no row was in status `submitted` at that time (rows had been set to `failed` outside this workflow between 01:29 and 01:49 UTC+1; the writer is not identified) |
| 45925708507 | PR #21 (before edge-case commits) | 3 | title decoded; personal-data row rejected; all 3 rejected for empty record ID / DOI (expected: the synthetic rows have none) |
| 45926151643 | `integration/pr20-pr21` | 3 | same as above — the two fixes work together; personal-data flag (PR #20) and decoding (PR #21) both effective |

Exit code 1 on these runs is by design: `notion-sync` exits 1 whenever a row is rejected.

Before run 45925708507 the three synthetic `*-submitted` rows were set back to `submitted`
(Notion TEST database only, approved by the maintainer).

## PR #21 edge-case hardening

Finding: with PR #21 as first written, a present but undecodable text value (conflicting
`plain_text`/`text.content`, non-object segment, non-string `plain_text`, wrong container type)
was read as `""`. For `Claimed SHA-256` that silently dropped the pin, so a row that claimed a
checksum would have been checked **unpinned** instead of rejected (fail open).

Fix (commits `99ffe89` tests, `2836111` fix on `fix/notion-text-decoding`): `_decode_text`
returns `None` for undecodable values; `parse_row` marks the property as malformed and a
submitted row is rejected. Absent properties and empty values still read as `""`.
The 13 new tests fail on the previous PR #21 code (6 failing) and pass with the fix.

Note: the edge cases were derived from the code; the reviewer's own list of flagged edge cases
was not available in the repository or on the PR. If it names cases not covered here, they are
still open.

## CI (GitHub Actions, Python 3.10 / 3.11 / 3.12 / 3.13)

| Commit | Branch | Result |
|---|---|---|
| `3a6d497` | PR #20 | 4/4 pass |
| `2836111` | PR #21 incl. edge cases | 4/4 pass |
| `27cd778` | `integration/pr20-pr21` | 4/4 pass |

Locally (Python 3.13): 456 tests pass on the integration branch.

## Prepared, not yet executed: metadata and write-back phase

New Notion rows are approval-gated. Nothing below has been inserted or run.

### Test record

Zenodo record **12772944** — "Pingüinos de Palmer" (Palmer penguins data, Spanish translation
from the R package `datos`), version DOI `10.5281/zenodo.12772944`, licence **CC0 1.0**, one file
`pinguinos.csv` (15.6 kB, md5 `e51d5df0daa7c44c132e14e456dcf363`). Content: measurements of
penguins; no data about people. Read from the public Zenodo web page on 2026-11-27; the API could
not be reached from the authoring environment, so the version-DOI / record-ID match is confirmed
only by the dry run itself. SHA-256 is not published by Zenodo and is therefore not yet known.

### Rows to add (after approval)

| Dataset ID | Zenodo record ID | Version DOI | File name | Claimed SHA-256 | Personal data | Status |
|---|---|---|---|---|---|---|
| `test-v14-zenodo-unpinned` | `12772944` | `10.5281/zenodo.12772944` | `pinguinos.csv` | *(empty)* | no | `submitted` |
| `test-v14-zenodo-wrongpin` | `12772944` | `10.5281/zenodo.12772944` | `pinguinos.csv` | `000…0` (64 zeros) | no | `submitted` |

Both rows use one record but different dataset IDs; they must not share a dataset ID
(rows of one dataset_id with the same file name would be rejected as duplicates).

### Steps and expected results

1. **Dry run** on `integration/pr20-pr21` (`apply=false`).
   Expected: both new rows `metadata_ok` (record lists `pinguinos.csv`); the three synthetic rows
   rejected as before; `applied: false`, `downloaded: false`; Notion unchanged.
2. **Apply** on `integration/pr20-pr21` (`apply=true`) — writes to the TEST database only.
   Expected: `test-v14-zenodo-unpinned` → `unpinned` with `Checked SHA-256`, size and
   `Source checksum = ok`; `test-v14-zenodo-wrongpin` → `failed` (pin mismatch);
   the three synthetic rows → `failed` with notes; only pipeline-owned properties change;
   provenance appears only as the workflow artifact (no repository write).
3. **Pinned check**: add a third row `test-v14-zenodo-pinned` with the SHA-256 written back in
   step 2 as claim, apply again. Expected: `verified`.
4. Re-run the dry run: expected no `submitted` rows left (empty report is correct here).

Abort criteria: any write to a non-pipeline property, any request to a blocked record, any
download in dry run, any write outside the TEST database → stop, keep merge on HOLD.

## Remaining audit limitations

- **OA-1** remains a documented open audit limitation (unchanged by this work).
- PR #21: root cause of the original decoding failure is not confirmed from a raw Notion REST
  response; the fix accepts both observed shapes conservatively.
- PR #21 edge-case commits have not yet had an independent review.
- Metadata (`metadata_checks`) and write-back paths have never run against live services;
  the HTTP clients are covered only by unit tests until phase 2 runs.
- Who set the synthetic rows to `failed` between runs 45922879238 and 45924733809 is unknown;
  it was not this workflow (dry run does not write).
- Live (non-TEST) Notion database and Production were not touched and remain on HOLD.
