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
