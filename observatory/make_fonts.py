"""Subset the self-hosted fonts (Latin, Latin-1, Latin Extended-A incl. Hungarian ő/ű, common punctuation, Σ).

Sources are the OFL-licensed Inter (fonts-inter package) and JetBrains Mono OTF/TTF files; adjust the paths
below. Output is WOFF (zlib) in observatory/fonts/. Requires fontTools. Not run by CI.
"""
from pathlib import Path
from fontTools import subset
from fontTools.ttLib import TTFont

OUT = Path(__file__).parent / "fonts"
SRC = {
    "Inter-Regular": "/usr/share/fonts/opentype/inter/Inter-Regular.otf",
    "Inter-Medium": "/usr/share/fonts/opentype/inter/Inter-Medium.otf",
    "Inter-SemiBold": "/usr/share/fonts/opentype/inter/Inter-SemiBold.otf",
    "Inter-Bold": "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "JetBrainsMono-Regular": "/mnt/skills/examples/canvas-design/canvas-fonts/JetBrainsMono-Regular.ttf",
}
UNICODES = list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + list(range(0x100, 0x180)) + [
    0x2013, 0x2014, 0x2018, 0x2019, 0x201A, 0x201C, 0x201D, 0x201E, 0x2026, 0x2022, 0x00B7, 0x03A3, 0x2212]

for name, path in SRC.items():
    opts = subset.Options(); opts.flavor = "woff"; opts.layout_features = ["kern", "liga", "tnum", "calt"]
    font = TTFont(path)
    s = subset.Subsetter(opts); s.populate(unicodes=UNICODES); s.subset(font)
    font.flavor = "woff"; font.save(OUT / f"{name}.woff")
    print(name, (OUT / f"{name}.woff").stat().st_size)
