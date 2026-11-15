"""Generate the Lanturn logo SVGs from one geometry definition.

  python scripts/build_brand.py

The wordmark is a monoline drawing (round caps, one stroke weight) on a 100-unit cap height. The U
is a U-turn arrow: the left stroke drops, turns round the bottom and rises on the right into a small
upward arrowhead. The amber bulb is a separate mark. Colours come from the design tokens
(design/figma-tokens.json): `color/brand/wordmark` for the lettering and `color/route/lit` for the bulb.

Writes design/brand/*.svg (masters, the same geometry that is in the Figma file) and the two files
the app serves: web/logo.svg (header lockup) and web/icon.svg (favicon and app icon).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STROKE = 14

# Letter paths, already positioned. Top of the letters is y = 0, baseline y = 100.
LETTERS = {
    "L": "M0 0V100H50",
    "A": "M78 100L112 0L146 100M91.6 60H132.4",
    "N": "M174 100V0L232 100V0",
    "T": "M260 0H322M291 0V100",
    # The U-turn arrow: down the left, round the bottom, up the right, arrowhead at the top.
    "U": "M350 0V66C350 84.78 365.22 100 384 100C402.78 100 418 84.78 418 66V0M403 15L418 0L433 15",
    "R": "M456 100V0H488C502.9 0 515 12.1 515 27C515 41.9 502.9 54 488 54H456M486 54L516 100",
    "N2": "M544 100V0L602 100V0",
}
# Bulb in a 100 x 120 box: glass, then a separate base.
BULB_GLASS = ("M50 2C76.5 2 96 21.5 96 46C96 61 88.5 71 82 81C77.5 88 75.5 93 75.5 100H24.5C24.5 93 22.5 88 18 81"
              "C11.5 71 4 61 4 46C4 21.5 23.5 2 50 2Z")
BULB_BASE = (36, 106, 28, 14, 3.5)      # x, y, width, height, corner radius


def tokens() -> dict[str, str]:
    src = json.loads((ROOT / "design" / "figma-tokens.json").read_text(encoding="utf-8"))
    return {name: value for name, _css, _kind, value in src["variables"]}


def wordmark(ink: str) -> str:
    paths = "".join(f'<path id="{k[0]}{"2" if k.endswith("2") else ""}" d="{d}"/>' for k, d in LETTERS.items())
    return (f'<g id="wordmark" fill="none" stroke="{ink}" stroke-width="{STROKE}" stroke-linecap="round" '
            f'stroke-linejoin="round">{paths}</g>')


def bulb(amber: str, transform: str = "") -> str:
    x, y, w, h, r = BULB_BASE
    t = f' transform="{transform}"' if transform else ""
    return (f'<g id="bulb" fill="{amber}"{t}><path id="glass" d="{BULB_GLASS}"/>'
            f'<rect id="base" x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/></g>')


def svg(view_box: str, body: str, title: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" role="img" aria-label="{title}">'
            f'<title>{title}</title>{body}</svg>\n')


def main() -> int:
    t = tokens()
    ink, amber, bg = t["color/brand/wordmark"], t["color/route/lit"], t["color/bg/app"]
    files = {
        "lanturn-wordmark.svg": svg("-7 -7 616 114", wordmark(ink), "Lanturn"),
        "lanturn-bulb.svg": svg("0 0 100 122", bulb(amber), "Lanturn bulb"),
        # Header lockup: wordmark, then the bulb standing just proud of the cap height, as in the sketch.
        "lanturn-lockup.svg": svg("-7 -12 750 124", wordmark(ink) + bulb(amber, "translate(638 -10) scale(0.98)"), "Lanturn"),
        # App icon: the bulb alone on the app's dark background, inside the maskable safe zone.
        "lanturn-app-icon.svg": svg("0 0 128 128", f'<rect width="128" height="128" rx="28" fill="{bg}"/>'
                                    + bulb(amber, "translate(27 18.5) scale(0.74)"), "Lanturn"),
    }
    out = ROOT / "design" / "brand"
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    (ROOT / "web" / "logo.svg").write_text(files["lanturn-lockup.svg"], encoding="utf-8")
    (ROOT / "web" / "icon.svg").write_text(files["lanturn-app-icon.svg"], encoding="utf-8")
    print(f"wrote {len(files)} files to {out}, plus web/logo.svg and web/icon.svg (ink {ink}, amber {amber})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
