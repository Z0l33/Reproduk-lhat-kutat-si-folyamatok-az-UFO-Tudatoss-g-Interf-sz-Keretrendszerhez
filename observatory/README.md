# UFO Research Observatory (static page source)

`index.html` is generated: `python observatory/build.py` embeds only aggregates from `reports/gep-ufo-uap-2026-10.qa.json` into `template.html`.
`tests/test_observatory.py` fails if `index.html` is stale. The status block in the template is hand-maintained and dated; it does not update itself.

This directory is a source export only. It is not deployed anywhere, there is no hosting configuration, and publishing it publicly is a separate decision.

Fonts: Inter and JetBrains Mono (SIL OFL 1.1) are self-hosted in `fonts/` as subsetted WOFF files (Latin, Latin-1, Latin Extended-A incl. Hungarian ő/ű); licence texts are alongside. The page makes no external requests. Regenerate the fonts with `python observatory/make_fonts.py` (needs fontTools and the source font files). Headings now use Inter; Space Grotesk was dropped because no licensed copy was available to self-host. The subsetted files are modified versions of the originals, as the OFL permits.
