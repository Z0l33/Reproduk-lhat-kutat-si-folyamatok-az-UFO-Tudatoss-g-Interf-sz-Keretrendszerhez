# UFO Research Observatory (static page source)

`index.html` is generated: `python observatory/build.py` embeds only aggregates from `reports/gep-ufo-uap-2026-10.qa.json` into `template.html`.
`tests/test_site.py` fails if `index.html` is stale. The status block in the template is hand-maintained and dated; it does not update itself.

This directory is a source export only. It is not deployed anywhere, there is no hosting configuration, and publishing it publicly is a separate decision.
