# Dataset manifest / Adatkészlet-jegyzék

`datasets.json` lists the Zenodo records the pipeline may retrieve. No record ID or DOI has been
invented: every entry was checked against its public Zenodo record page and the Notion Data Registry.
Add only records you have verified yourself.

Current entries / Jelenlegi tételek:

| dataset_id | Domain | Zenodo record | SHA-256 pin | Licence (Notion registry) |
|---|---|---|---|---|
| `gep-ufo-uap-2026-10` | GEP | 23906585 (v2026.10) | none (suspended) | Unverified |
| `sddb-dreams-2025-12-28` | DREAMS | 18076716 (v2) | from local check, to be confirmed by `verify-datasets` | Unverified (Zenodo deposit says CC BY 4.0; SDDb site says All Rights Reserved) |

Both entries carry `"retrieval_blocked": true` with a reason. `fetch` (and therefore the
`verify-datasets` and `profile-dataset` workflows) makes no Zenodo call and downloads nothing
for a blocked entry; the run fails with `retrieval_blocked`. Remove the flag only after a
documented, human rights decision, in its own reviewed PR. A test fails if either flag is removed.
Mindkét tétel letöltése blokkolva van (`retrieval_blocked`) a jogtisztázásig.

Download size limit: a file is downloaded only if Zenodo declares a valid size, and that size is at
most 100 MB (`DEFAULT_MAX_DOWNLOAD_BYTES`) or the entry's own `"max_download_bytes"` (a positive integer).
The stream is aborted as soon as it exceeds the declared size, so nothing larger reaches the disk.

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

## external-sources.json (candidate register, metadata only)

Lists candidate public UFO/UAP sources found on the web on 2026-11-23. It is **not** a retrieval manifest:
nothing was downloaded, no raw data is stored, no SHA-256 exists, and every licence status is `unverified`
or `restricted`. Statements about licences are quotes of what each page says, read by an automated fetch,
not a legal determination. Move a source into `datasets.json` only after a person has confirmed the licence
and the record is a Zenodo record with a version DOI. UAPDrop stays excluded by project policy.
