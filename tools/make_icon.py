"""Draw the sscs_garage brand icon (custom_components/sscs_garage/brand/).

    python tools/make_icon.py custom_components/sscs_garage/brand      (needs Pillow)

Home Assistant 2026.10 serves icon.png / icon@2x.png from that folder for custom
integrations. Original artwork: a garage with a roller shutter and signal arcs.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
N = 2048                      # draw big, downsample for anti-aliasing
s = N / 1024                  # design units are on a 1024 grid
S = lambda *v: tuple(round(x * s) for x in v)

TOP, BOTTOM = (38, 110, 196), (13, 51, 102)
WHITE = (255, 255, 255, 255)
ACCENT = (255, 176, 32, 255)

# Rounded tile with a vertical gradient.
grad = Image.new("RGBA", (N, N))
gd = ImageDraw.Draw(grad)
for y in range(N):
    t = y / (N - 1)
    gd.line([(0, y), (N, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
mask = Image.new("L", (N, N), 0)
ImageDraw.Draw(mask).rounded_rectangle(S(0, 0, 1023, 1023), radius=round(224 * s), fill=255)
img = Image.new("RGBA", (N, N), (0, 0, 0, 0))
img.paste(grad, (0, 0), mask)
d = ImageDraw.Draw(img)

# Roof.
d.line([S(178, 486), S(512, 262), S(846, 486)], fill=WHITE, width=round(60 * s), joint="curve")
for x, y in ((178, 486), (846, 486)):
    r = 30 * s
    d.ellipse((x * s - r, y * s - r, x * s + r, y * s + r), fill=WHITE)

# Shutter: white door with slat gaps in the tile colour.
d.rounded_rectangle(S(292, 492, 732, 820), radius=round(28 * s), fill=WHITE)
for i in range(1, 5):
    y = 492 + i * 328 / 5
    d.rounded_rectangle(S(322, y - 9, 702, y + 9), radius=round(9 * s), fill=(22, 70, 138, 255))

# Signal arcs above the right roof slope.
cx, cy = 722, 238
dot = 30
d.ellipse(S(cx - dot, cy - dot, cx + dot, cy + dot), fill=ACCENT)
for r in (82, 148):
    d.arc(S(cx - r, cy - r, cx + r, cy + r), start=-80, end=10, fill=ACCENT, width=round(40 * s))

for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
    img.resize((size, size), Image.LANCZOS).save(out / name, optimize=True)
print("ok")
