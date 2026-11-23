# Data-quality reports

Counts and format patterns only; no free text from the source files.

- `gep-ufo-uap-2026-10.qa.json`: output of `python -m ufo_pipeline gep-qa <file>` on
  `UFO-UAP-Falldaten_GEP_1972-2026.10.csv` (SHA-256 recorded inside the report).
  Produced by GitHub Actions run 45204233544 (workflow `profile-dataset`).
  The privacy patterns are heuristics: a zero does not prove absence, a hit is a lead for manual review.
  This is a quality screen, not evidence about any research hypothesis, and not a pin decision.
