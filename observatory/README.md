# UFO Research Observatory (static page source)

`index.html` is generated: `python observatory/build.py` embeds only aggregates from `reports/gep-ufo-uap-2026-10.qa.json` into `template.html`.
`tests/test_observatory.py` fails if `index.html` is stale. The status block in the template is hand-maintained and dated; it does not update itself.

This directory is a source export only. It is not deployed anywhere, there is no hosting configuration, and publishing it publicly is a separate decision.

## Fonts

Inter and JetBrains Mono (SIL OFL 1.1) are self-hosted in `fonts/` as subsetted WOFF files (Latin, Latin-1, Latin Extended-A incl. Hungarian ő/ű). Licence texts are alongside. The page makes no external requests. Headings use Inter; Space Grotesk was dropped because no licensed copy was available to self-host. The subsetted files are modified versions of the originals, as the OFL permits.

| Font | Version | Acquired from | Upstream |
|---|---|---|---|
| Inter (Regular, Medium, SemiBold, Bold) | 4.000 (git a52131595) | Debian package `fonts-inter` 4.0+ds-1 | https://github.com/rsms/inter |
| JetBrains Mono Regular | 2.211 | copy bundled with the build environment; exact upstream release asset not verified | https://github.com/JetBrains/JetBrainsMono |

`fonts/MANIFEST.json` records the SHA-256 of every source file and every generated WOFF file, and the fontTools version used (4.67.0).

- Verify the committed files (no sources needed): `python observatory/make_fonts.py --check` (also run by the tests).
- Regenerate: `python observatory/make_fonts.py` with source locations given by `--inter-dir` / `--jetbrains-dir`, or the environment variables `UFO_FONT_SRC_INTER` / `UFO_FONT_SRC_JETBRAINS`. A missing source file stops with an error that lists the expected paths.

Reproducibility, as far as shown: in one environment (fontTools 4.67.0, the sources above) two generations gave identical bytes, and the regeneration test compares against the manifest. That test is skipped when fontTools, the sources, or the same fontTools version are missing, which is the case on CI, so CI verifies the committed checksums but does not regenerate. Other fontTools versions may produce different bytes. Timestamps are not recalculated; an earlier generation without that setting produced different bytes on every run.

## Marking systems and accessibility report (branch `observatory-hybrid`)

The page defines two independent marking systems with separate legends: **A, claim level** (01 lived experience, 02 psychological/neurobiological mechanism, 03 metaphysical/ontological interpretation, 04 external physical event or autonomous agent; icon + constant number; 04 never proves an event, the claim's truth or NHI presence) and **B, data status** (fact, heuristic hit, unverified assumption; distinct shape + text label). They are design proposals, not approved scientific categories; the example on the page is labelled as an illustration.

`python observatory/a11y_report.py` writes `observatory/a11y-report.json` with three categories only: PASS (what an automated check proved, with check name and scope), MANUAL REQUIRED (the human check needed and whether it is done) and NOT VERIFIED (no evidence). A PASS without evidence becomes NOT VERIFIED; the overall status is PASS only when nothing is open. `content` is reproducible; `metadata` (time, environment, tool version, content hash) is kept separate and is not compared. `python observatory/a11y_report.py --check` and `tests/test_observatory_a11y.py` verify this. Screen-reader behaviour (NVDA, VoiceOver) is a manual validation and is not proven by the report.

**Closing a manual check.** Only a human record in `observatory/a11y-manual-results.json` closes a MANUAL REQUIRED item: `{item id: {result, performed_by, performed_on, environment}}`, all four fields valid: strings that are not placeholders, `performed_on` a real `YYYY-MM-DD` date, `environment` naming tool/device and a version. Invalid fields keep the item open and are listed under `closure_problems`; a future date is not rejected (keeps the report reproducible). The script never closes an item itself. A closed manual item does not make NOT VERIFIED items PASS. The report's `overall` lists which items are open (manual vs. not verified), which are closed and how many passed.
