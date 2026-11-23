"""Accessibility report for observatory/index.html: what an automated check proved, what needs a human, what has no evidence.

Three result categories, nothing else:
  PASS             exactly what the named automated check proved (check name + statement + scope are stored)
  MANUAL REQUIRED  which human check is needed and whether it was done (done=false until a result is recorded)
  NOT VERIFIED     no evidence yet

Rules enforced by normalize_item()/derive_overall():
  * a PASS without evidence (check name and a statement of what it proved) becomes NOT VERIFIED automatically;
  * the overall status is "PASS" only if every item is PASS; any open item makes it MANUAL REQUIRED or NOT VERIFIED;
  * an empty report is NOT VERIFIED;
  * an automated check that FAILS is not a category: report generation stops with an error.

The report separates `content` (deterministic: depends only on index.html) from `metadata` (time, environment, tool
version, content hash). Only `content` is compared by --check and by the tests.

Run: python observatory/a11y_report.py [--check]    (writes observatory/a11y-report.json)
Real screen-reader behaviour (NVDA, VoiceOver) is a manual validation; static analysis cannot prove it.
"""
import hashlib
import json
import platform
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

OBS = Path(__file__).resolve().parent
INDEX = OBS / "index.html"
REPORT = OBS / "a11y-report.json"
MANUAL_RESULTS = OBS / "a11y-manual-results.json"
CLOSURE_FIELDS = ("result", "performed_by", "performed_on", "environment")
CLOSURE_RULE = ("A MANUAL REQUIRED item is closed only by a human record in observatory/a11y-manual-results.json "
                "({item id: {result, performed_by, performed_on (YYYY-MM-DD), environment (tool, version, device)}}). "
                "All four fields must be non-empty; with any missing the item stays open. This script never closes an item "
                "itself, and a closed item keeps the status MANUAL REQUIRED with done=true and its closure record shown. "
                "A closed manual item does not turn NOT VERIFIED items into PASS; the overall status is PASS only when no "
                "item is NOT VERIFIED and no manual item is open.")
TOOL_VERSION = "1"
PASS, MANUAL, NOTVER = "PASS", "MANUAL REQUIRED", "NOT VERIFIED"
STATUSES = (PASS, MANUAL, NOTVER)

SCOPE = ("Static analysis of the generated observatory/index.html source (markup, CSS tokens, the script's string "
         "tables and render templates). Not an accessibility-tree measurement, not a screen-reader run, "
         "not a WCAG conformance audit.")
CLAIM_NAMES = {
    "hu": ["Átélt élmény", "Pszichológiai és neurobiológiai mechanizmus", "Metafizikai vagy ontológiai értelmezés",
           "Külső fizikai esemény vagy önálló ágens"],
    "en": ["Lived experience", "Psychological and neurobiological mechanism",
           "Metaphysical or ontological interpretation", "External physical event or autonomous agent"],
}
A_IDS = ["a01", "a02", "a03", "a04"]
B_IDS = ["b-fact", "b-heur", "b-unv"]


class CheckFailed(AssertionError):
    """An automated check found a real defect; this is not a report category."""


def need(cond, msg):
    if not cond:
        raise CheckFailed(msg)


# ---------- parsing helpers ----------
class _Svgs(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.svgs, self.symbols, self.buttons, self.interactive, self._sym = [], {}, [], [], None
        self._depth_svg = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "svg":
            self.svgs.append(a)
        if tag == "symbol":
            self._sym = a.get("id")
            self.symbols[self._sym] = ""
        elif self._sym:
            self.symbols[self._sym] += "<%s %s>" % (tag, " ".join(f'{k}="{v}"' for k, v in attrs))
        if tag == "button":
            self.buttons.append(a)
        if tag in ("a", "input", "select", "textarea") or "onclick" in a or a.get("tabindex") is not None:
            self.interactive.append(tag)

    def handle_endtag(self, tag):
        if tag == "symbol":
            self._sym = None


def parse(html):
    p = _Svgs()
    p.feed(html)
    return p


def script_text(html):
    return html.split("<script>", 1)[1].rsplit("</script>", 1)[0]


def lang_blocks(html):
    parts = re.split(r"\n(hu|en):\{", script_text(html).split("const T={", 1)[1])
    return {parts[i]: parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def tokens(html):
    css = html.split("<style>", 1)[1].split("</style>", 1)[0]
    return css, dict(re.findall(r"--([\w-]+):\s*([^;]+);", css))


def _rgb(c):
    c = c.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", c)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), 1.0
    m = re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", c)
    need(m, f"unparsable colour {c!r}")
    return tuple(int(m.group(i)) for i in (1, 2, 3)), float(m.group(4) or 1)


def _over(fg, bg):
    (f, a), (b, _) = _rgb(fg), _rgb(bg)
    return tuple(round(a * f[i] + (1 - a) * b[i]) for i in range(3))


def _lum(rgb):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ---------- automated checks: each returns the statement of what it proved, or raises CheckFailed ----------
def check_claim_marks_complete(html):
    sy = parse(html).symbols
    for i in A_IDS:
        need(i in sy and sy[i], f"missing icon symbol {i}")
    js = script_text(html)
    need(re.search(r"const AI=\['a01','a02','a03','a04'\]", js), "AI array must map marks 01-04 to a01-a04 in order")
    blocks = lang_blocks(html)
    for lang in ("hu", "en"):
        items = re.findall(r"\['(0[1-4])','([^']+)','([^']+)'\]", blocks[lang])
        need([n for n, _, _ in items] == ["01", "02", "03", "04"], f"{lang}: numbers 01-04 not complete or not in order")
        need([nm for _, nm, _ in items] == CLAIM_NAMES[lang], f"{lang}: claim-level names differ from the specification")
    return ("Four claim-level marks exist in both languages with constant numbers 01-04 in order, the specified names, "
            "and a fixed icon mapping a01-a04.")


def check_legends_separate(html):
    need('id="legend-claim"' in html and 'id="legend-status"' in html, "two separate legend containers required")
    js = script_text(html)
    a = js.split("$('legend-claim').innerHTML=", 1)[1].split("\n", 1)[0]
    b = js.split("$('legend-status').innerHTML=", 1)[1].split("\n", 1)[0]
    need("AI[" in a and "BI[" not in a, "claim legend must use only the claim icon set")
    need("BI[" in b and "AI[" not in b, "status legend must use only the status icon set")
    need(re.search(r"\$\('legend-claim'\)", js) and re.search(r"\$\('legend-status'\)", js), "legend renderers missing")
    return "Claim level (A) and data status (B) have separate legend containers; each renderer uses only its own icon set."


def check_shapes_disjoint_and_distinct(html):
    sy = parse(html).symbols
    need(not set(A_IDS) & set(B_IDS), "A and B share an icon id")
    for i in B_IDS:
        need(i in sy and sy[i], f"missing status symbol {i}")
    bodies = [sy[i] for i in A_IDS + B_IDS]
    need(len(set(bodies)) == len(bodies), "two marks have identical shapes")
    for i in B_IDS:
        need("<circle" not in sy[i], f"{i} uses a circle (collides with the crosshair mark)")
    need("<rect" in sy["b-fact"] and "<path" in sy["b-heur"] and "stroke-dasharray" in sy["b-unv"],
         "status shapes must be square / triangle / dashed hexagon")
    return ("All seven mark shapes are pairwise different; the status set (square, triangle, dashed hexagon) shares "
            "no icon id and no circle primitive with the claim set.")


def check_status_has_text_label(html):
    js = script_text(html)
    status_line = js.split("$('legend-status').innerHTML=", 1)[1].split("\n", 1)[0]
    need("<span class=\"nm\">${nm}</span>" in status_line and "bl.map((nm,i)" in status_line,
         "status legend must print a text label next to each shape")
    for key in ("facts", "heur", "unv"):
        need(re.search(r"ic\('b-%s'\)\}<span>\$\{t\.%s\}</span>" % ({"facts": "fact", "heur": "heur", "unv": "unv"}[key], key), js),
             f"evidence column {key} must show its status label as text beside the shape")
    return "Every data status is shown with a shape and a visible text label (legend and evidence column headings)."


def check_icons_not_read_twice(html):
    p = parse(html)
    need(p.svgs, "no svg found")
    for a in p.svgs:
        need(a.get("aria-hidden") == "true", "every static svg must be aria-hidden")
        need("aria-label" not in a and "role" not in a, "no svg may carry its own accessible name or role")
    js = script_text(html)
    ic = re.search(r"const ic=.*", js).group(0)
    need('aria-hidden="true"' in ic and "aria-label" not in ic and "<title" not in ic and "role=" not in ic,
         "the ic() helper must emit decorative svg only")
    need(not any("<title" in b for b in re.findall(r"<svg\b.*?</svg>", html.split("<script>", 1)[0], flags=re.S)),
         "svg <title> would be read twice")
    return ("All static svg and every svg built by ic() are aria-hidden with no own label, title or role, so an icon next "
            "to its text label cannot be announced twice (the screen-reader behaviour itself is a manual check).")


def check_claim_04_disclaimer(html):
    b = lang_blocks(html)
    need(re.search(r"n04:'[^']*nem bizonyít[^']*'", b["hu"]), "HU 04 disclaimer missing")
    need(re.search(r"n04:'[^']*does not prove[^']*'", b["en"]), "EN 04 disclaimer missing")
    need("$('mk-04').textContent=t.n04" in script_text(html), "04 disclaimer is not rendered")
    return "The mark-04 disclaimer (does not prove an external event, the claim's truth or NHI presence) exists in HU and EN and is rendered."


def check_design_proposal_notice(html):
    b = lang_blocks(html)
    need("nem jóváhagyott tudományos kategóriák" in b["hu"] and "not approved scientific categories" in b["en"],
         "notice that the marks are design proposals is missing")
    need("nem kutatási állítás" in b["hu"].lower() and "not a research claim" in b["en"].lower(),
         "illustration label (not a research claim) is missing")
    return "The marks are labelled design proposals, and the example is labelled as an illustration, in both languages."


def check_text_contrast(html):
    _, t = tokens(html)
    bg = _rgb(t["bg"])[0]
    panel = _over(t["panel"], t["bg"])
    warnbg = _over(t["warn-bg"], t["bg"])
    pairs = [(n, c, bg) for n, c in (("fg", t["fg"]), ("mute", t["mute"]), ("teal", t["teal"]), ("violet", t["violet"]),
                                    ("gold", t["gold"]), ("warn", t["warn"]), ("bad", t["bad"]), ("ok", t["ok"]))]
    pairs += [(n + " on panel", _rgb(t[n])[0], panel) for n in ("fg", "mute", "teal", "violet", "gold", "bad", "ok")]
    pairs += [("warn on warn-bg", _rgb(t["warn"])[0], warnbg)]
    pairs += [("white on pressed toggle", (255, 255, 255), _rgb(re.search(r"aria-pressed=true\]\{background:(#[0-9a-fA-F]{6})", html).group(1))[0])]
    worst = 99.0
    for name, c, b in pairs:
        c = _rgb(c)[0] if isinstance(c, str) else c
        r = contrast(c, b)
        worst = min(worst, r)
        need(r >= 4.5, f"contrast {name}: {r:.2f} < 4.5")
    return (f"{len(pairs)} text/background token pairs (flat colours, panels composited over --bg) have contrast >= 4.5:1; "
            f"lowest {worst:.2f}:1. Pixels of the decorative star/gradient backdrop are not measured.")


def check_interactive_targets(html):
    p = parse(html)
    need(p.buttons and not p.interactive, f"only <button> may be interactive, found {p.interactive}")
    css, _ = tokens(html)
    m = re.search(r"\.lang button\{[^}]*\}", css)
    need(m and "min-height:44px" in m.group(0) and "min-width:44px" in m.group(0), "language buttons must declare >=44x44 px")
    for b in p.buttons:
        need("aria-pressed" in b, "toggle button without aria-pressed")
    need("aria-label=\"Language\"" in html, "language group needs a name")
    return ("The only interactive elements are the two language buttons; CSS declares >= 44x44 px and they expose "
            "aria-pressed inside a named group. Icons are not interactive and carry no target-size requirement.")


def check_no_colour_only_meaning(html):
    css, _ = tokens(html)
    need(re.search(r"\.t-fact\{border-top", css), "evidence column accents expected")
    js = script_text(html)
    for needle in ("${t.facts}", "${t.heur}", "${t.unv}"):
        need(needle in js, f"status label {needle} missing from render")
    return "Colour is only an accent: every status and claim mark also has a distinct shape and a text label or number."


AUTOMATED = [
    ("claim-marks-complete", "Claim-level marks 01-04", check_claim_marks_complete),
    ("legends-separate", "Two separate legends (claim level, data status)", check_legends_separate),
    ("shapes-distinct", "Mark shapes distinct, claim and status sets disjoint", check_shapes_disjoint_and_distinct),
    ("status-text-label", "Data status shown with shape and text label", check_status_has_text_label),
    ("icons-not-read-twice", "Decorative icons hidden from assistive technology", check_icons_not_read_twice),
    ("claim-04-disclaimer", "Mark 04 does not prove an event, truth or NHI", check_claim_04_disclaimer),
    ("design-proposal-notice", "Marks labelled as design proposals; example labelled", check_design_proposal_notice),
    ("text-contrast", "Text contrast >= 4.5:1 (token pairs)", check_text_contrast),
    ("interactive-targets", "Interactive targets >= 44x44 px, named, stateful", check_interactive_targets),
    ("no-colour-only-meaning", "No meaning carried by colour alone", check_no_colour_only_meaning),
]

MANUAL_ITEMS = [
    ("sr-nvda-voiceover", "Screen-reader reading (NVDA on Windows, VoiceOver on macOS/iOS)",
     "Listen through the legends, evidence columns and the example chips in both languages; confirm no mark is announced "
     "twice and the order is understandable."),
    ("shape-legibility", "Shape legibility at small size and under colour-vision-deficiency simulation",
     "Look at the seven marks at 100% and 200% zoom and in a colour-blindness simulation; confirm the solid square (status) "
     "is not confused with the outlined rhombus (claim 03)."),
    ("mobile-readability", "Readability on a real phone (about 400 px wide) and at 200% text zoom",
     "Open the page on a phone and with browser zoom 200%; confirm the legends and chips wrap without clipped text."),
    ("keyboard-focus", "Keyboard-only use and visible focus",
     "Tab to the language buttons, toggle with Enter/Space, confirm the focus ring is visible on the dark background."),
]

NOT_VERIFIED_ITEMS = [
    ("forced-colors", "Forced-colors / Windows high-contrast behaviour", "No test or inspection has been done."),
    ("backdrop-contrast", "Contrast over the decorative star/gradient backdrop pixels", "Only flat token pairs were computed."),
    ("wcag-audit", "WCAG conformance", "No audit was performed; this report makes no conformance claim."),
    ("scientific-categories", "Claim-level categories as validated scientific categories",
     "They are design proposals and have not been reviewed as a scientific taxonomy."),
]


# ---------- normalisation and status derivation ----------
def normalize_item(item):
    """Return a complete, honest copy of an item; missing or empty evidence can never leave a PASS standing."""
    out = {k: item.get(k) for k in ("id", "title", "scope", "status")}
    out["scope"] = item.get("scope") or ""
    st = item.get("status")
    notes = []
    if st not in STATUSES:
        notes.append(f"unknown status {st!r}")
        st = NOTVER
    if st == PASS:
        ev = item.get("evidence") or {}
        if not (isinstance(ev, dict) and str(ev.get("test", "")).strip() and str(ev.get("proved", "")).strip()):
            notes.append("PASS without evidence (test name and statement of what it proved)")
            st = NOTVER
        elif not out["scope"].strip():
            notes.append("PASS without a stated scope")
            st = NOTVER
        else:
            out["evidence"] = {"test": ev["test"], "proved": ev["proved"]}
    if st == MANUAL:
        need_check = str(item.get("needed_check", "")).strip()
        if not need_check:
            notes.append("MANUAL REQUIRED without a description of the human check")
            st = NOTVER
        else:
            cl = item.get("closure") or {}
            done = isinstance(cl, dict) and all(str(cl.get(f, "")).strip() for f in CLOSURE_FIELDS)
            out["needed_check"], out["done"] = need_check, done
            if done:
                out["closure"] = {f: str(cl[f]) for f in CLOSURE_FIELDS}
            elif cl:
                out["closure_incomplete"] = sorted(f for f in CLOSURE_FIELDS if not str(cl.get(f, "")).strip()) \
                    if isinstance(cl, dict) else list(CLOSURE_FIELDS)
    if st == NOTVER:
        out["reason"] = "; ".join(notes) or str(item.get("reason") or "no evidence yet")
    out["status"] = st
    return out


def derive_overall(items):
    """Detailed overall status. PASS only if there is at least one item, none is NOT VERIFIED and no manual item is open."""
    if not items:
        return {"status": NOTVER, "open_items": 0, "reason": "no evidence: the report has no items",
                "open": {"manual_required": [], "not_verified": []}, "closed_manual": [], "passed": 0}
    manual_open = [i["id"] for i in items if i["status"] == MANUAL and not i.get("done")]
    closed = [i["id"] for i in items if i["status"] == MANUAL and i.get("done")]
    notver = [i["id"] for i in items if i["status"] == NOTVER]
    status = NOTVER if notver else (MANUAL if manual_open else PASS)
    return {"status": status, "open_items": len(manual_open) + len(notver),
            "open": {"manual_required": manual_open, "not_verified": notver},
            "closed_manual": closed, "passed": sum(1 for i in items if i["status"] == PASS),
            "reason": {NOTVER: "at least one item has no evidence", MANUAL: "a human check is still open",
                       PASS: "every item is PASS or a closed human check"}[status]}


def counts(items):
    c = {s: 0 for s in STATUSES}
    for i in items:
        c[i["status"]] += 1
    return c


def load_manual_results():
    if not MANUAL_RESULTS.exists():
        return {}
    data = json.loads(MANUAL_RESULTS.read_text(encoding="utf-8"))
    need(isinstance(data, dict), "manual results file must be an object keyed by item id")
    return data


def build_items(html):
    raw = []
    for cid, title, fn in AUTOMATED:
        proved = fn(html)           # raises CheckFailed: a failing check is not a category
        raw.append({"id": cid, "title": title, "scope": SCOPE, "status": PASS,
                    "evidence": {"test": fn.__name__, "proved": proved}})
    results = load_manual_results()
    unknown = set(results) - {m[0] for m in MANUAL_ITEMS}
    need(not unknown, f"manual results for unknown items: {sorted(unknown)}")
    for mid, title, how in MANUAL_ITEMS:
        raw.append({"id": mid, "title": title, "scope": "Human check on a real device or tool.", "status": MANUAL,
                    "needed_check": how, "closure": results.get(mid)})
    for nid, title, why in NOT_VERIFIED_ITEMS:
        raw.append({"id": nid, "title": title, "scope": "Not covered by this report.", "status": NOTVER, "reason": why})
    return [normalize_item(i) for i in raw]


def build_content(html):
    items = build_items(html)
    return {"schema": 1, "scope": SCOPE, "input": {"file": "observatory/index.html",
                                                    "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest()},
            "manual_closure_rule": CLOSURE_RULE, "items": items, "counts": counts(items), "overall": derive_overall(items)}


def content_hash(content):
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_metadata(content, now=None):
    return {"generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "environment": {"python": platform.python_version(), "platform": platform.platform()},
            "tool": "observatory/a11y_report.py", "tool_version": TOOL_VERSION,
            "content_sha256": content_hash(content),
            "note": "metadata describes this run; it is not part of the reproducible content"}


def build_report(html, now=None):
    content = build_content(html)
    return {"content": content, "metadata": build_metadata(content, now)}


def serialize(report):
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv):
    html = INDEX.read_text(encoding="utf-8")
    if "--check" in argv:
        committed = json.loads(REPORT.read_text(encoding="utf-8"))
        fresh = build_content(html)
        ok = committed["content"] == fresh and committed["metadata"]["content_sha256"] == content_hash(fresh)
        print("a11y report content up to date" if ok else "a11y report content differs: run python observatory/a11y_report.py")
        return 0 if ok else 1
    report = build_report(html)
    REPORT.write_text(serialize(report), encoding="utf-8")
    print(f"overall: {report['content']['overall']['status']}  counts: {report['content']['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
