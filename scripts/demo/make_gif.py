"""Assemble the frames written by record.cjs into docs/demo.gif (needs Pillow).

    python scripts/demo/make_gif.py FRAMES_DIR docs/demo.gif

Frames are shrunk from 1000x625 to 800x500 and share one 256-colour palette
built from all frames: the most frequent colours (backgrounds, lines) are kept
exactly, and the rest of the palette is a median cut over the *distinct*
colours, so small coloured text (the green/red verdict) keeps its own entries
instead of being outvoted by large white areas.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

SIZE = (800, 500)
EXACT = 24  # palette entries reserved for the most frequent colours


def build_palette(frames: list[Image.Image]) -> Image.Image:
    counts: Counter = Counter()
    for frame in frames:
        for n, colour in frame.getcolors(maxcolors=SIZE[0] * SIZE[1]):
            counts[colour] += n
    exact = [c for c, _ in counts.most_common(EXACT)]
    rest = [c for c in counts if c not in set(exact)]
    strip = Image.new("RGB", (len(rest), 1))
    strip.putdata(rest)
    cut = strip.quantize(colors=256 - len(exact), method=Image.Quantize.MEDIANCUT)
    entries = [v for c in exact for v in c] + cut.getpalette()[: 3 * (256 - len(exact))]
    palette = Image.new("P", (1, 1))
    palette.putpalette(entries + [0] * (768 - len(entries)))
    return palette


def main(frames_dir: str, out: str) -> None:
    root = Path(frames_dir)
    spec = json.loads((root / "frames.json").read_text(encoding="utf-8"))
    frames = [
        Image.open(root / f["file"]).convert("RGB").resize(SIZE, Image.Resampling.LANCZOS)
        for f in spec
    ]
    palette = build_palette(frames)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(
        out,
        save_all=True,
        append_images=gif[1:],
        duration=[f["ms"] for f in spec],
        loop=0,
        optimize=False,
        disposal=1,
    )
    size_kb = Path(out).stat().st_size / 1024
    total = sum(f["ms"] for f in spec) / 1000
    print(f"{out}: {len(gif)} frames, {total:.2f} s, {size_kb:.0f} KB")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
