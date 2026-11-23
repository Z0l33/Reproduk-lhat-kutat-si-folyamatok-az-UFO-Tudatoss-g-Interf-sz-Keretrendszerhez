# Dataset manifest / Adatkészlet-jegyzék

`datasets.json` lists the Zenodo records the pipeline may retrieve. It is intentionally **empty**:
no record ID or DOI has been invented. Add only records you have verified yourself.
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
