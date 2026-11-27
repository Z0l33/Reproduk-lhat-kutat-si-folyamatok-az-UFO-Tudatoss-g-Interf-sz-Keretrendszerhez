# notion-sync: isolated integration test log

Status (2026-11-27): **isolated integration test PASSED** on `integration/pr20-pr21` at
`e5c64d8`. Merge of PR #20 and PR #21 still waits for the final audit and the maintainer's
approval. Production: HOLD (untouched). Live (non-TEST) Notion database: untouched.
GEP and SDDb: `retrieval_blocked` (unchanged). OA-1: open.

## Test environment

| Item | Value |
|---|---|
| Notion database | `UFO Pipeline TEST – Submissions v1.4` (`4246cb55-c9f4-812a-b024-e7816c719c3b`), private, synthetic test rows only |
| Token | repository secret `NOTION_TOKEN` (masked in logs, never printed) |
| Workflow | `.github/workflows/notion-sync.yml`, `workflow_dispatch`, `permissions: contents: read` (no repository writes) |
| Code under test | `integration/pr20-pr21` = `main` + PR #20 (`fix/notion-personal-flag-fail-closed`, `3a6d497`) + PR #21 (`fix/notion-text-decoding`, `2510184`) |
| Test record | Zenodo **12772944** "Pingüinos de Palmer", version DOI `10.5281/zenodo.12772944`, licence **CC0 1.0** (Zenodo record page), one file `pinguinos.csv`, 15 616 bytes, md5 `e51d5df0daa7c44c132e14e456dcf363`; penguin measurements, no data about people |

## Code changes under test

### PR #20
Personal-data flag fails closed when missing, null or wrongly typed. Static audit: PASS.

### PR #21 — Notion text decoding
1. Original commits: decode `text.content` when `plain_text` is absent; reject conflicting values.
2. Edge-case hardening (`99ffe89`, `2836111`): undecodable values are malformed, never `""`.
   Before, an undecodable `Claimed SHA-256` dropped the pin silently (row checked unpinned).
3. Audit round R21 (regression tests first — 8 of them fail on the previous code — then the fix, ending at `2510184`):
   - **R21-01** absent text property → `""` (no claim = no pin, allowed); present `null` → malformed.
   - **R21-02** a `text` key must be an object with a string `content`; `null`/missing content → malformed.
   - **R21-03** each field must use its schema container (`Dataset ID`: `title`, all others: `rich_text`);
     both containers present, or a `type` naming another type → malformed.

Static audit of the R21 commits: pending (final audit).

## CI (GitHub Actions, Python 3.10 / 3.11 / 3.12 / 3.13)

| Commit | Branch | Result |
|---|---|---|
| `3a6d497` | PR #20 | 4/4 pass |
| `2510184` | PR #21 (incl. R21-01..03) | 4/4 pass |
| `e5c64d8` | `integration/pr20-pr21` | 4/4 pass |

Locally (Python 3.13): 468 tests pass on the integration branch.

## Runs

Exit code 1 means at least one row was rejected or failed (by design).

| # | Run | Code | Mode | Rows | Result |
|---|---|---|---|---|---|
| 1 | 45922879238 | `main` | dry | 4 | all rejected incl. `dataset_id must be a lowercase slug` — title not decoded (bug fixed by PR #21) |
| 2 | 45924733809 | `main` | dry | 0 | no `submitted` rows at that time (set to `failed` outside this workflow; writer not identified) |
| 3 | 45925708507 | PR #21 (early) | dry | 3 | title decoded; personal-data row rejected; 3 synthetic rows rejected for empty record ID / DOI (expected) |
| 4 | 45926151643 | integration (pre-R21) | dry | 3 | same as 3 — both fixes effective together |
| 5 | 45926782146 | integration `e5c64d8` | dry | 5 | both Zenodo rows `metadata_ok` (file listed, size 15616, md5 as on the record page); 3 synthetic rows rejected; `applied: false`, `downloaded: false` |
| 6 | 45926914739 | integration `e5c64d8` | **apply** | 5 | `test-v14-zenodo-unpinned` → `unpinned`; `test-v14-zenodo-wrongpin` → `failed` (`pinned_sha256_mismatch`); 3 synthetic rows → `failed` with rejection notes; 5 updates |
| 7 | 45927007173 | integration `e5c64d8` | **apply** | 1 | `test-v14-zenodo-pinned` → `verified`; 1 update; exit 0 |
| 8 | 45927051943 | integration `e5c64d8` | dry | 0 | no `submitted` rows left — correct; exit 0 |

Notion changes made by hand for the test (TEST database only, approved): the three synthetic
`*-submitted` rows reset to `submitted` before run 3; rows `test-v14-zenodo-unpinned`,
`test-v14-zenodo-wrongpin` added before run 5; `test-v14-zenodo-pinned` added before run 7 with
the SHA-256 written back in run 6 as its claim.

## Verification of results (read back from Notion after each apply)

| Row | Status | Checked SHA-256 | Size | Source checksum | Run URL | Notes |
|---|---|---|---|---|---|---|
| zenodo-unpinned | `unpinned` | `7b478d89…7b82f8` | 15616 | ok | run 6 | — |
| zenodo-wrongpin | `failed` | `7b478d89…7b82f8` | 15616 | ok | run 6 | `pinned_sha256_mismatch` |
| zenodo-pinned | `verified` | `7b478d89…7b82f8` | 15616 | ok | run 7 | — |
| synthetic `*-submitted` (3) | `failed` | — | — | — | — | rejection reasons |

Full SHA-256 of `pinguinos.csv`: `7b478d894910876d434127ae860a398343f2d2643b3b4221549fa49f4d7b82f8`.

Checked and confirmed:
- Only pipeline-owned properties changed (`Pipeline status`, `Checked SHA-256`, `Checked size`,
  `Source checksum`, `Checked at`, `Pipeline run URL`, `Pipeline notes`). Submitter/maintainer fields
  (`Dataset ID`, record ID, DOI, file name, `Claimed SHA-256`, `Contains personal data`,
  `Source / note`, `Maintainer decision`) unchanged on every row.
- Dry runs wrote nothing and downloaded nothing.
- Rows not in `submitted` (e.g. `test-v14-*-verified`, `test-v14-true-pending`) were never processed.
- Personal-data rows rejected in every run.
- No blocked record (GEP, SDDb) was contacted.
- Provenance was produced only as a workflow artifact (run 6: 1328 bytes, run 7: 655 bytes);
  no repository write (`contents: read`).

## Remaining audit limitations

- **OA-1** remains open (unchanged by this work).
- **Provenance content not inspected**: the artifacts exist, but their contents could not be
  downloaded from the test environment (artifact storage host blocked by its network policy).
  To be checked by downloading the `provenance` artifact of runs 45926914739 and 45927007173.
- The SHA-256 was computed by the pipeline itself; run 7 confirms consistency, not an independent
  hash. Independent anchor: Zenodo's md5 matched (`Source checksum = ok`).
- Licence is checked by policy (record page: CC0 1.0), not by code: the pipeline has no licence gate.
- Rejection write-backs do not set `Pipeline run URL` (only notes and status) — by current design,
  noted for review.
- PR #21: the root cause of the original decoding failure is not confirmed from a raw Notion REST
  response; the decoder accepts both observed shapes and rejects anything ambiguous.
- R21-01..03 commits: final static audit pending.
- The writer that set the synthetic rows to `failed` between runs 1 and 2 is not identified
  (not this workflow; dry runs do not write).
- Only the TEST database was exercised; the live database and Production remain on HOLD.
