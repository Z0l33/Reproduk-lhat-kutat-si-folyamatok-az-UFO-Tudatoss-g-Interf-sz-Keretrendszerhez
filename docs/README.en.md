# Reproducible research pipeline — UFO Consciousness-Interface Framework

Hungarian version: [README.hu.md](README.hu.md)

This package turns the framework's empirical claims into **checkable** procedures. It does not
contain, and must not be read as containing, any result about the framework itself: the dataset
manifest is empty, and every bundled dataset is synthetic test data.

## What it does

| Step | Module | Guarantee |
|---|---|---|
| Retrieval | `zenodo.py` | Datasets come only from records you list in `data/manifest/datasets.json`. The version DOI must equal `10.5281/zenodo.<record_id>`, and the Zenodo API must report the same id and DOI. |
| Integrity | `hashing.py`, `zenodo.py` | Each download is checked against the checksum **Zenodo reports** (to my knowledge an MD5 — it is verified with whatever algorithm the API names) **and** against a **SHA-256 you pin** in the manifest. A failed file is deleted, never kept. |
| Provenance | `provenance.py` | One JSON record per dataset (`provenance/<dataset_id>.json`): record, DOI, timestamps (incl. first retrieval), per-file sizes, SHA-256, source checksum, verification status, tool version. `validate_provenance()` is strict: SHA-256 values must be 64 lowercase hex characters, file names plain and unique, statuses from a closed vocabulary, and a `verified` record must be fully consistent. |
| Local re-verification | `provenance.py` | `check_local_file()` identifies a file by name **and** recorded path, size and SHA-256 (a renamed copy with identical content is refused). `dataset_is_verified()` re-hashes **every** listed file on disk; a `verified` status string alone is never trusted, and any tampered, missing or unlisted file makes the dataset unverified. |
| Validation | `validation.py` | Column/type/range/missing/duplicate checks. |
| Leakage | `validation.py` | Subject in several groups; subject or identical row in train and test; test not later than train (if a time column is given); features that are near-perfect proxies of the outcome. Any finding blocks the analysis. |
| Statistics | `stats.py` | Hedges' g, percentile-bootstrap CI, permutation p-value (seeded, deterministic), verdict against the preregistered SESOI. |
| Evidence class | `evidence.py` | The only place that decides what counts as positive evidence. |

## Exploratory vs confirmatory

An analysis is **confirmatory** only if *all* hold, otherwise it is **exploratory** (hypothesis-generating, never evidence):

1. a preregistration file is supplied and complete;
2. it names an external `registry_id` (not empty / placeholder);
3. its `registered_at` is earlier than the dataset's *first retrieval*;
4. every analysis parameter (groups, outcome, direction, alpha, SESOI, permutations, bootstrap, seed, replication requirement) equals the preregistered value;
5. it refers to the dataset it was registered for.

## What can never count as positive evidence

Invalid data · data leakage · unverified datasets · synthetic data · exploratory analyses · null results · effects equivalent to zero within the SESOI · effects that are significant but whose CI does not clear the SESOI · effects in the wrong direction · failed replications · invalid ("not independent") replications. Only `confirmatory_positive_replicated` and (if the preregistration waives replication) `confirmatory_positive_unreplicated` count. By default replication is **required**, so a lone confirmatory result is reported as `awaiting_independent_replication` and does not count.

A replication is valid only if it uses a different dataset **and** Zenodo record **and** different file contents, is itself preregistered with `replicates_prereg_sha256` pointing at the original, and is confirmatory. Re-running the same data is "invalid", not a replication.

## Usage

```bash
pip install -e ".[dev]"
python -m pytest -q                                     # tests (offline)
python -m ufo_pipeline validate-manifest                # check data/manifest/datasets.json
python -m ufo_pipeline fetch                            # needs network + real manifest entries
python -m ufo_pipeline analyze --config configs/my.json --out results
python -m ufo_pipeline replicate --original results/a.json --replication results/b.json --out results/a.replication.json
python -m ufo_pipeline synthetic --scenario true_effect # synthetic TEST data only
```

Workflow for a real dataset: add the entry to the manifest → `fetch` (status `unpinned`, note the reported SHA-256 after reviewing the file) → pin that SHA-256 in the manifest → `fetch` again (must be `verified`) → write and externally register the preregistration → copy `configs/analysis.TEMPLATE.json`, `analyze`.

## Limitations (read these)

- **Not tested against live Zenodo.** Retrieval was developed and tested only against fake API responses (field names follow the Zenodo records API as I know it: `id`, `doi`, `conceptdoi`, `files[].key/size/checksum/links.self`). Run `verify-datasets` once with a real record and report any mismatch.
- A preregistration timestamp inside the repository is self-reported. Register externally (OSF, a Zenodo deposit…) and put its identifier in `registry_id`; the code cannot verify that the registry entry exists or that the data were really unseen.
- Trust-on-first-use: the SHA-256 pin is only as good as the review you did when pinning.
- Statistics cover a two-group comparison of a continuous outcome. Other designs need new, preregistered analysis code.
- Leakage detection finds the listed patterns; absence of findings is not proof of absence of leakage.
- The statistical verdict is a methodological gate. Passing it says nothing about *why* an effect exists.

## Column access (allowlist between loading and analysis)

Real datasets need an explicit `data_access.allowed_columns` list in the analysis config and an entry in
`policies/sensitive_columns.json`. Denied columns (per-dataset list plus global name patterns, e.g. free
text, place and postal-code fields) can be neither allowed nor referenced anywhere in the config. All
checks run **before** the data file is opened, and the loader keeps only the allowed columns, so a denied
column never reaches validation, leakage detection or statistics. A violation stops the run with
`COLUMN ACCESS DENIED` (exit code 2) and writes no report. The policy location is fixed; a config cannot
override it. A real dataset without a policy entry is refused. Not covered: re-identification through
combinations of *allowed* columns (see `reports/`); choose allowlists conservatively.
