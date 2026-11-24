# Dataset manifest / Adatkészlet-jegyzék

`datasets.json` lists the Zenodo records the pipeline may retrieve. No record ID or DOI has been
invented: every entry was checked against its public Zenodo record page and the Notion Data Registry.
Add only records you have verified yourself.

Current entries / Jelenlegi tételek:

| dataset_id | Domain | Zenodo record | SHA-256 pin | Licence (Notion registry) |
|---|---|---|---|---|
| `gep-ufo-uap-2026-10` | GEP | 23906585 (v2026.10) | none (suspended) | Unverified |
| `sddb-dreams-2025-12-28` | DREAMS | 18076716 (v2) | from local check, to be confirmed by `verify-datasets` | Unverified |

Listing a record here does not clear its reuse rights. Raw files stay out of the repository
(`data/raw/` is git-ignored); reports hold aggregate counts only.
A nyilvántartásba vétel nem jelent jogtisztázást; nyers fájl nem kerül a repóba.
A `datasets.json` entry looks like this (all values are placeholders, NOT real records):

```json
{
  "dataset_id": "my-dataset-v1",
  "record_id": "<numeric Zenodo record id>",
  "doi": "10.5281/zenodo.<same numeric id>",
  "files": [
    {"name": "<file name as listed on the record>",
     "sha256": "<64 hex chars, pinned after you reviewed the file>",
     "zenodo_checksum": "<optional: md5:... as shown by Zenodo>"}
  ]
}
```

The `doi` must be the **version DOI** of that exact record (`10.5281/zenodo.<record_id>`), not the concept DOI.
