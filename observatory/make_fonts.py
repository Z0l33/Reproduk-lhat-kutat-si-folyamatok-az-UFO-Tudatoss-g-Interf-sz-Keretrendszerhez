"""Subset the self-hosted fonts and record checksums in fonts/MANIFEST.json.

    python observatory/make_fonts.py [--inter-dir DIR] [--jetbrains-dir DIR] [--out DIR]
    python observatory/make_fonts.py --check          # verify committed WOFF files; needs no source fonts

Source locations (first match wins): the command-line option, then the environment variable
UFO_FONT_SRC_INTER / UFO_FONT_SRC_JETBRAINS, then the default directory used when the files were generated.
Requires fontTools. Output is WOFF (zlib). Timestamps are not recalculated, so one environment reproduces
identical bytes; other fontTools versions may produce different bytes (the manifest records the version used).
Subset: Basic Latin, Latin-1, Latin Extended-A (incl. Hungarian o/u double acute), common punctuation, U+03A3.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
FONTS = HERE / "fonts"
MANIFEST = FONTS / "MANIFEST.json"
UNICODES = list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + list(range(0x100, 0x180)) + [
    0x2013, 0x2014, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E, 0x2026, 0x2022, 0x00B7, 0x03A3, 0x2212]
FAMILIES = {
    "inter": {"env": "UFO_FONT_SRC_INTER", "default": "/usr/share/fonts/opentype/inter",
              "files": {"Inter-Regular": "Inter-Regular.otf", "Inter-Medium": "Inter-Medium.otf",
                        "Inter-SemiBold": "Inter-SemiBold.otf", "Inter-Bold": "Inter-Bold.otf"},
              "upstream": "https://github.com/rsms/inter",
              "acquired_from": "Debian package fonts-inter 4.0+ds-1 (files in /usr/share/fonts/opentype/inter)"},
    "jetbrains": {"env": "UFO_FONT_SRC_JETBRAINS", "default": "/mnt/skills/examples/canvas-design/canvas-fonts",
                  "files": {"JetBrainsMono-Regular": "JetBrainsMono-Regular.ttf"},
                  "upstream": "https://github.com/JetBrains/JetBrainsMono",
                  "acquired_from": "copy bundled with the build environment (canvas-design example fonts); "
                                   "the exact upstream release asset was not verified"},
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bad = []
    for name, rec in data["outputs"].items():
        p = FONTS / name
        if not p.is_file() or sha256(p) != rec["sha256"]:
            bad.append(name)
    if bad:
        print("MISMATCH or missing: " + ", ".join(bad), file=sys.stderr)
        return 1
    print(f"ok: {len(data['outputs'])} WOFF files match MANIFEST.json")
    return 0


def resolve_sources(args):
    missing, found = [], {}
    for fam, spec in FAMILIES.items():
        directory = Path(getattr(args, fam + "_dir") or os.environ.get(spec["env"]) or spec["default"])
        for stem, filename in spec["files"].items():
            p = directory / filename
            (found.__setitem__(stem, (fam, p)) if p.is_file() else missing.append(str(p)))
    if missing:
        print("ERROR: source font file(s) not found:\n  " + "\n  ".join(missing) +
              "\nSet --inter-dir / --jetbrains-dir or the environment variables UFO_FONT_SRC_INTER / "
              "UFO_FONT_SRC_JETBRAINS. Sources: see fonts/MANIFEST.json (Inter 4.000, JetBrains Mono 2.211).",
              file=sys.stderr)
        sys.exit(2)
    return found


def generate(args):
    found = resolve_sources(args)                      # clear error before anything else is needed
    from fontTools import subset, version as ft_version
    from fontTools.ttLib import TTFont
    out = Path(args.out) if args.out else FONTS
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"fonttools_version": ft_version, "subset": "see make_fonts.py docstring",
                "sources": {}, "outputs": {}}
    for stem, (fam, path) in found.items():
        opts = subset.Options(); opts.flavor = "woff"; opts.layout_features = ["kern", "liga", "tnum", "calt"]
        font = TTFont(path, recalcTimestamp=False)
        s = subset.Subsetter(opts); s.populate(unicodes=UNICODES); s.subset(font)
        font.flavor = "woff"; font.save(out / f"{stem}.woff")
        spec = FAMILIES[fam]
        manifest["sources"][path.name] = {
            "version": font["name"].getDebugName(5), "sha256": sha256(path),
            "upstream": spec["upstream"], "acquired_from": spec["acquired_from"]}
        manifest["outputs"][f"{stem}.woff"] = {"sha256": sha256(out / f"{stem}.woff"), "from": path.name}
    (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(found)} WOFF files and MANIFEST.json to {out}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--inter-dir"); ap.add_argument("--jetbrains-dir"); ap.add_argument("--out")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    return check() if args.check else generate(args)


if __name__ == "__main__":
    sys.exit(main())
