# Reproducibility check log

Each entry records what was checked, on which commit, and what was NOT shown. Entries are facts about one run, not guarantees.

## 2026-11-23 · GEP dataset QA report (`gep-ufo-uap-2026-10`)

| Item | Result |
|---|---|
| Run | GitHub Actions `profile-dataset`, run 45210062668, commit `b364dd3` (merged to `main` as part of PR #6) |
| Environment | Python 3.12.16, numpy 2.5.5, ubuntu-latest |
| CSV SHA-256 before and after the checks | `4dc9787e8a4b77d6ab52e952e93439418662041e3e0830c998d3f1aadbe125d3` (unchanged) |
| Two consecutive QA runs | byte-identical |
| Report SHA-256 | `8adc18317bef535b9292fdafefcf01271d6d7a851a1490e4d6da39db7845c264` |
| Committed `reports/gep-ufo-uap-2026-10.qa.json` | byte-identical to the run output |
| SHA-256 pin in manifest | none (suspended by decision) |

Not shown: that the report is reproducible on another machine, other Python/numpy versions, or another version of the file; that the heuristic hits are correct; that the data are accurate. The CSV is not in this repository or in Notion.

## Separation of observation, inference, uncertainty and testable hypotheses

Reports keep `facts` (counts, formats), `heuristic_hits` (including re-identification estimates) and `unverified_assumptions` apart (`evidence_levels`); `tests/test_committed_reports.py` enforces this for committed reports. Hypotheses are tested only through the preregistration/replication path (`evidence.py`), never by the QA report.
