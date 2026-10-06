"""Build the README's brand art from the iFixAi website.

Writes docs/assets/brand/, one light and one dark file of each, so the README
can hand GitHub's two themes their own art through <picture>:

  ifixai-logo-*.svg       the wordmark on its own
  ifixai-masthead-*.svg   the wordmark over "Independent auditing for AI
                          agents", ruled off the way the site's hero sets it
  hero-audit-*.svg        the site's hero audit window, animated
  button-site-*.svg       the "Visit ifixai.ai" link under the hero
  button-pro-*.svg        the "iFixAi Pro" link beside it

and, for phones, hero-audit-phone.svg, button-site.svg and button-pro.svg,
which carry both themes in one file. GitHub's <picture> handling replaces a
source's whole media query with its theme's, so a source cannot ask for a
phone and a theme at once; the phone source asks for the width only, and its
file picks its own theme from the viewer's colour scheme.

The hero is the landing page's HeroAudit (app/components/landing/HeroAudit.tsx
in the website repo) ported from GSAP to CSS keyframes, because GitHub shows an
SVG through an <img>, where no script runs but CSS animation does. Its timings
are HeroAudit's, its colours the site's, its geometry measured off
www.ifixai.ai into brand_hero_layouts.json (the desktop layout and the phone
one), and its text is set in subsets of Inter and IBM Plex Mono embedded in
each file, so it renders the same on every machine. With reduced motion it
rests on the finished audit, as the site does.

Needs network (the fonts come from Google Fonts) and fontTools:

    pip install fonttools brotli
    python scripts/build_brand_assets.py

When the site's hero changes, re-measure it first (needs Playwright and
Google Chrome):

    pip install playwright
    python scripts/build_brand_assets.py --measure
"""

from __future__ import annotations

import base64
import io
import re
import urllib.request
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets" / "brand"

# ── colour helpers ────────────────────────────────────────────────────────


def rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def alpha(hex_: str, a: float) -> str:
    r, g, b = rgb(hex_)
    return f"rgba({r},{g},{b},{a:g})"


def mix(hex_: str, base: str, amount: float) -> str:
    """`amount` of `hex_` over `base`, as CSS color-mix() would paint it."""
    (r1, g1, b1), (r2, g2, b2) = rgb(hex_), rgb(base)
    return "#{:02x}{:02x}{:02x}".format(
        *(
            round(c1 * amount + c2 * (1 - amount))
            for c1, c2 in ((r1, r2), (g1, g2), (b1, b2))
        )
    )


# ── themes ────────────────────────────────────────────────────────────────
# LIGHT is the site's own "warm" theme, value for value (app/styles/os.css).
# The site has no dark theme, so DARK is drawn for GitHub's dark canvas
# (#0d1117): the same desk, window and ink roles, with GitHub's own greens
# and reds where the site's would sit too dim on a dark window.

LIGHT = {
    "desk": "#ece4d6",
    "desk_dot": "#d5c9ae",
    "desk_edge": None,
    "sheen": alpha("#6366f1", 0.09),
    "glow": alpha("#6366f1", 0.17),
    "window": "#fffdf8",
    "window_2": "#f5efe4",
    "sunken": "#efe7d8",
    "ink": "#1d1a15",
    "border_soft": "#d8ccb6",
    "bevel_light": "#ffffff",
    "bevel_dark": "#cbbfa5",
    "text": "#1d1a15",
    "text_2": "#4a4436",
    "text_3": "#746b56",
    "desk_ink_3": alpha("#1d1a15", 0.55),
    "pinstripe": alpha("#6366f1", 0.35),
    "accent": "#6366f1",
    "accent_2": "#a855f7",
    "accent_shadow": alpha("#6366f1", 0.35),
    "on_accent": "#ffffff",
    "pass": "#1f9d4d",
    "fail": "#d63a2f",
    "fail_fill": "#d63a2f",
    "cyan": "#33b0e1",
    "cyan_ink": "#167fa8",
    "shadow_soft": alpha("#1d1a15", 0.2),
    "shadow_hard": alpha("#1d1a15", 0.85),
    "button_shadow": alpha("#1d1a15", 0.14),
    # the site mixes these in oklab; these are its results
    "fail_face": "#fef0ea",
    "pass_face": "#f1f6ec",
    "run_bg": "#eaf4f6",
}

DARK = {
    "desk": "#151b23",
    "desk_dot": "#2a323e",
    "desk_edge": "#262e39",
    "sheen": alpha("#6366f1", 0.16),
    "glow": alpha("#6366f1", 0.24),
    "window": "#1b2129",
    "window_2": "#262e39",
    "sunken": "#11161d",
    "ink": "#c9d2dc",
    "border_soft": "#353e4a",
    "bevel_light": alpha("#ffffff", 0.16),
    "bevel_dark": alpha("#000000", 0.45),
    "text": "#f0f6fc",
    "text_2": "#c4ccd6",
    "text_3": "#9aa4b0",
    "desk_ink_3": alpha("#f0f6fc", 0.5),
    "pinstripe": alpha("#818cf8", 0.45),
    "accent": "#6366f1",
    "accent_2": "#a855f7",
    "accent_shadow": alpha("#6366f1", 0.5),
    "on_accent": "#ffffff",
    "pass": "#3fb950",
    "fail": "#ff6e64",
    "fail_fill": "#da3633",
    "cyan": "#33b0e1",
    "cyan_ink": "#6fd0f5",
    "shadow_soft": alpha("#000000", 0.55),
    "shadow_hard": alpha("#010409", 0.9),
    "button_shadow": alpha("#000000", 0.55),
}
DARK["fail_face"] = mix(DARK["fail_fill"], DARK["window"], 0.12)
DARK["pass_face"] = mix(DARK["pass"], DARK["window"], 0.09)
DARK["run_bg"] = mix(DARK["cyan"], DARK["window"], 0.14)

# The wordmark's ink per GitHub theme: the brand navy on light, GitHub's own
# foreground on dark. The brackets keep the brand cyan on both.
LOGO_INK = {"light": "#143154", "dark": "#f0f6fc"}
LOGO_ACCENT = "#33b0e1"
# the masthead's tagline, in GitHub's muted foreground for each theme
MUTED = {"light": "#59636e", "dark": "#9198a1"}

# ── the mark ──────────────────────────────────────────────────────────────
# The wordmark and badge as the site draws them (app/components/os/Logo.tsx
# and BrandBadge.tsx), which is the brand export with the C2PA manifest
# dropped, the canvas cropped to the artwork, and the A's counter a knockout
# rather than a white shape, so it stays a hole on any background.

LOGO_VIEW_BOX = (831, 3511, 19225, 7077)
BADGE_VIEW_BOX = (16886, 3228, 3201, 3201)

# the A's counter, knocked out of the ink
A_COUNTER = "M15917.816 8106.714c-64.445-195.15-920.818-2784.986-945.725-2812.829-327.493 925.73-626.738 1895.704-967.14 2813.284a82222 82222 0 0 1 954.737-1.57z"
# the letters, their dots, and the badge's dot
INK = (
    "m15305.304 4369.35-172.226-2.02c-169.391 430.406-326.329 932.428-478.912 1374.664l-800.99 2312.123c-100.591 296.14-203.157 591.623-307.546 886.396-154.204 438.035-359.893 1075.37-727.481 1376.218-299.396-201.628-586.795-687.805-792.433-991.437a57559 57559 0 0 1-814.152-1215.695c99.327-124.328 192.224-253.516 286.386-381.895 176.63-240.86 356.096-481.013 547.813-710.079 198.804-237.57 404.291-494.377 704.042-605.847 122.057-44.194 254.998-60.848 376.498-107.876 15.998-6.227 16.909-29.21 17.618-42.22-85.81-70.972-1532.42-75.427-1690.217-14.933l-9.113 14.124c25.566 50.875 112.438 77.907 210.246 108.382 220.978 68.846 497.745 155.056 248.822 572.791-226.193 379.668-497.492 725.772-762.008 1079.622-22.173-33.765-51.232-76.794-85-126.758-239.709-354.862-716.697-1060.942-662.326-1315.218 11.593-54.267 33.868-79.275 79.836-107.522 99.73-61.253 431.223-132.175 463.218-214.487-71.634-66.872-2082.61-74.11-2228.866-3.999-3.899 12.453-5.822 16.706-5.519 20.806.304 4.657 3.392 9.112 9.518 25.26 53.966 42.523 245.835 63.835 329.72 91.88 119.83 39.992 246.595 88.387 347.44 168.32 314.28 249.06 558.14 657.684 781.802 986.88 213.485 314.112 408.139 634.349 618.992 946.486l-640.862 855.213c-231.559 309.302-468.94 653.028-767.93 897.382-231.104 188.872-596.718 253.719-847.109 66.011-147.673-110.71-203.715-284.143-225.94-461.422-41.512-403.814-40.196-825.7-38.98-1236.349.202-68.745.404-137.186.404-205.121l.405-2201.615c-23.54-.05-55.738-.253-94.567-.556-311.192-2.177-1047.583-7.34-1174.095 45.205l-12.454 17.92c21.01 39.992 140.08 79.933 184.478 87.73 512.072 89.651 510.553 523.029 509.034 949.37-.203 60.595-.405 121.088.86 180.418 11.442 408.724 13.973 817.651 7.493 1226.527-.81 89.652-.557 179.861-.304 270.323.962 369.948 1.975 744.3-69.811 1105.642-59.485 299.532-538.802 280.346-691.133 409.13l-5.467 19.945c20.35 17.11 13.06 11.845 41.816 17.312 388.901 56.9 3436.425 88.387 3615.485-26.374-2.278-16.401-2.835-30.576-20.047-38.928-164.076-79.427-609.323-60.696-646.836-313.251-33.311-224.308 242.697-619.01 373.106-797.757 184.022-252.301 379.992-505.767 563.71-760.195 270.337 420.418 585.63 858.554 817.138 1301.145 74.064 141.641 113.552 347.471-48.195 421.836-106.009 65.555-378.422 99.523-436.286 169.129l-5.417 22.122c118.513 77.857 993.617 69.858 1417.956 66.011 80.29-.76 144.433-1.316 184.274-1.164 148.686.607 311.445-2.177 477.496-4.961 239.304-4.1 485.392-8.302 705.965-2.582 38.93 1.013 92.593-4.505 128.638-21.413 9.619-17.11 9.619-17.212 5.063-31.082-1.468-4.455-3.443-10.378-5.67-18.73-37.868-38.777-100.39-46.978-151.116-58.773-277.678-64.392-482.405-129.29-478.609-452.816 4.607-390.45 347.085-1339.466 496.986-1740.446 306.028 3.544 615.55 1.873 925.02.203 363.589-1.975 727.026-3.949 1084.54 2.581 105.249 305.911 208.878 612.328 310.837 919.352 84.594 249.568 180.529 509.514 230.9 764.751 45.26 230.433-6.58 377.997-230.799 454.943-91.58 31.436-307.75 70.922-368.044 126.252l-1.012 20.4c57.915 46.421 357.817 39.74 523.462 36.044 34.679-.76 63.484-1.418 82.924-1.418l1029.257.911 1188.878 7.847c149.95-.456 797.95-3.037 889.835-57.861l4.253-15.086c-38.323-52.293-119.07-68.137-212.018-86.463-189.49-37.258-429.857-84.539-465.851-470.28-37.817-404.726-37.614-791.278-37.412-1195.598v-87.172l8.708-2164.66c-53.157 1.873-147.825 2.531-262.744 3.34-366.373 2.532-938.942 6.531-1032.092 55.027l-8.05 18.882c61.662 92.285 473.294 80.844 590.592 301.203 67.23 126.455 99.58 258.376 111.375 406.244 24.806 311.58 22.832 635.867 20.857 953.825-.506 90.412-1.063 180.317-1.012 269.26 1.164 294.875.506 589.75-2.278 884.625-.304 19.743-.557 40.7-.81 62.62-2.785 239.443-6.936 595.774-122.057 788.695-62.37 104.333-277.122 167.257-396.698 191.353-225.889-74.06-353.21-169.433-482.962-372.834-164.025-257.211-257.732-542.266-359.083-827.877l-339.593-986.476-838.502-2444.349-272.615-793.241c-69.357-205.435-144.788-442.743-222.447-643.845",
    "M3925.169 4393.446c-332.915-2.657-677.347-5.406-986.479 5.528-12.798.375-25.378.588-37.771.795-74.389 1.25-142.135 2.384-211.193 37.612l-10.347 16.286c14.038 28.809 49.197 42.816 78.382 51.989 250.539 78.672 546.634 68.603 678.072 345.345 56.79 119.59 90.765 244.445 102.171 378.341 32.679 383.768 36.222 769.864 36.896 1154.745l.734 1176.058-.932 1373.889c-.157 56.9.456 120.38 1.109 187.96 3.037 314.77 6.935 718.989-61.596 965.469-122.021 438.794-1043.102 463.75-1220.032 42.472-51.263-108.433-78.413-259.794-91.094-379.515-27.085-371.72-26.746-750.527-26.411-1125.132.065-74.516.131-148.83-.02-222.89a150518 150518 0 0 1 3.214-2195.691c-17.957 0-46.848-.152-84.134-.355-300.753-1.518-1147.42-5.922-1203.189 49.408-2.243 2.227-4.252 4.657-6.379 6.986 32.345 101.7 479.272 120.835 570.357 288.496 151.799 279.435 149.171 587.016 146.56 893.028-.37 43.535-.745 87.07-.67 130.504l.614 804.794-1.788 805.603c-.207 43.738 0 87.88.213 132.327 1.367 284.649 2.78 579.98-106.606 844.177-105.892 255.845-544.25 222.536-660.742 331.222l-3.033 21.363c59.378 58.57 819.421 62.67 1069.696 64.037 25.96.101 46.439.253 60.077.354 636.533 6.43 1273.117 7.29 1909.65 2.582 184.114-1.266 1217.416-2.582 1301.808-66.062l7.897-20.907c-42.373-65.759-373.496-86.97-469.04-111.876-329.66-84.387-445.333-175.963-486.871-524.7-34.835-292.394-34.056-580.435-33.26-872.576.126-45.611.247-91.272.237-137.136l.395-1356.88c44.89-1.114 92.142-2.683 141.208-4.354 458.961-15.44 1076.966-36.245 1411.547 278.423 214.346 201.527 287.651 625.388 341.668 900.318 5.164 26.02 16.807 36.346 35.539 51.128 39.386-13.162 42.069-64.24 42.12-102.915.151-175.508.455-351.066.759-526.675.962-563.628 1.924-1127.46-1.468-1690.886-.253-41.712-7.088-138.047-43.183-163.51-51.334 19.946-64.142 220.207-71.837 277.664-116.843 872.07-867.713 861.44-1542.341 851.872-107.599-1.519-213.258-3.038-314.098-.911l.05-1221.06c.016-26.426-.035-55.078-.09-85.553-.557-314.87-1.464-824.333 65.857-1082.046a501.9 501.9 0 0 1 130.178-228.565c200.454-201.775 541.692-202.838 805.185-200.707 430.009 3.462 905.226 31.81 1222.898 359.175 300.206 309.383 387.483 708.641 502.098 1107.545 5.215 18.072 18.833 18.528 33.261 21.768 36.906-44.345 30.476-338.765 26.983-499.187-.76-35.233-1.417-63.986-1.417-82.21V4396.499l-2635.072-.77c-134.258-.03-274.246-1.149-416.34-2.283",
    "M18919.474 4648.932c-118.412-161.272-314.483-246.399-513.135-222.855-302.18 35.81-519.362 307.961-487.215 610.526 32.147 302.59 301.674 522.999 604.665 494.448 199.159-18.73 372.903-143.16 454.916-325.755 81.86-182.574 59.231-395.091-59.231-556.364",
    "M2349.962 4903.481c-29.53-273.775-274.803-472.22-548.735-443.957-275.734 28.444-475.768 275.679-446.041 551.256 29.727 275.613 277.885 474.458 553.346 443.477 273.654-30.83 470.96-277.005 441.43-550.776",
    "M8430.682 4889.768c-36.753-272.991-287.296-464.875-560.469-429.191-274.64 35.876-467.775 288.111-430.87 562.575 36.957 274.48 289.93 466.692 564.267 428.675 272.869-37.764 463.826-289.053 427.072-562.06",
)
BADGE_DOT = INK[2]
# the badge's two cyan brackets
BRACKETS = (
    "M17946.208 3512.183c-338.934-2.035-686.07-4.116-1002.527 6.363l-21.364 7.118c-6.58 51.958-5.568 114.062-4.606 173.128.455 27.781.91 54.895.506 79.963-3.139 194.106-1.266 389.427.557 584.794 2.177 232.873 4.404 465.816-1.873 696.856 193.59.623 387.331.036 580.922-1.771-.406-49.25-1.317-101.002-2.279-154.166-3.442-195.402-7.239-409.868 11.948-589.289 3.645-33.051 38.576-99.969 58.067-129.198 52.346-44.72 98.77-70.345 167.822-77.953 182.604-20.092 377.865-15.794 566.797-11.633 56.346 1.24 112.135 2.47 166.86 3.032-1.62-190.046 0-396.707-7.391-585.867-165.392.714-338.327-.324-513.439-1.377",
    "m20054.74 4646.831-581.277.922c.354 21.15.658 42.305.962 63.46 2.734 178.215 5.468 356.492 2.683 534.834-1.468 89.045-3.797 191.859-68.749 261.262-83.328 87.02-213.587 93.246-326.278 94.461-73.001.86-146.61.05-220.27-.81-91.377-1.012-182.755-2.075-272.716.203 0 54.672-.253 110.053-.557 165.737-.557 125.392-1.164 252.1 1.215 374.656l571.86-1.823c68.951.405 143.826 1.52 221.434 2.683 226.496 3.342 476.685 6.986 673.16-5.315z",
)

# ── fonts ─────────────────────────────────────────────────────────────────

GOOGLE_FONTS = (
    "https://fonts.googleapis.com/css2?family=Inter:ital,wght@0,400..900;1,400"
    "&family=IBM+Plex+Mono:wght@400;600;700&display=swap"
)
# Google serves woff2 only to a browser it recognises
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
SANS, MONO = "ifxSans", "ifxMono"
FAMILY = {SANS: "Inter", MONO: "IBM Plex Mono"}
# the subsets to look in, in order, for a character the first one lacks
# (Plex Mono keeps its numero sign in the Cyrillic file)
SUBSET_ORDER = ("latin", "latin-ext", "cyrillic")


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


class FontFiles:
    """Google's font files, fetched once each and kept for every SVG."""

    def __init__(self) -> None:
        css = _get(GOOGLE_FONTS).decode()
        self.urls: dict[tuple[str, str, str], str] = {}
        for unicode_subset, block in re.findall(
            r"/\* (\S+) \*/\s*@font-face \{(.*?)\}", css, re.S
        ):
            family = re.search(r"font-family: '([^']+)'", block).group(1)
            weight = re.search(r"font-weight: ([\d ]+);", block).group(1)
            style = re.search(r"font-style: (\w+)", block).group(1)
            url = re.search(r"url\((\S+?)\)", block).group(1)
            for w in self._weights(weight):
                self.urls[(family, f"{w}-{style}", unicode_subset)] = url
        self.cache: dict[str, bytes] = {}

    @staticmethod
    def _weights(spec: str) -> list[int]:
        # a variable font is listed once with its range, "400 900"
        low, _, high = spec.partition(" ")
        return list(range(int(low), int(high or low) + 1, 100))

    def get(
        self, family: str, weight: int, style: str, unicode_subset: str
    ) -> bytes | None:
        url = self.urls.get((family, f"{weight}-{style}", unicode_subset))
        if url is None:
            return None
        if url not in self.cache:
            self.cache[url] = _get(url)
        return self.cache[url]


def _subset(data: bytes, chars: str, weight: int) -> str:
    font = TTFont(io.BytesIO(data))
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": weight})
    options = subset.Options()
    options.flavor = "woff2"
    options.hinting = False
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=chars)
    subsetter.subset(font)
    buffer = io.BytesIO()
    font.flavor = "woff2"
    font.save(buffer)
    return base64.b64encode(buffer.getvalue()).decode()


class Fonts:
    """The faces one SVG sets text in, embedded as subsets of what it sets."""

    def __init__(self, files: FontFiles) -> None:
        self.files = files
        self.used: dict[tuple[str, int, str], set[str]] = {}

    def use(self, family: str, weight: int, style: str, text: str) -> None:
        self.used.setdefault((family, weight, style), set()).update(text)

    def css(self) -> str:
        rules = []
        for (family, weight, style), chars in sorted(self.used.items()):
            left = set(chars) - {" "}
            for unicode_subset in SUBSET_ORDER:
                data = self.files.get(FAMILY[family], weight, style, unicode_subset)
                if data is None or not left:
                    continue
                cmap = TTFont(io.BytesIO(data)).getBestCmap()
                here = {c for c in left if ord(c) in cmap}
                if not here:
                    continue
                left -= here
                face = _subset(data, "".join(sorted(here | {" "})), weight)
                ranges = ",".join(f"U+{ord(c):04X}" for c in sorted(here | {" "}))
                rules.append(
                    f"@font-face{{font-family:{family};font-weight:{weight};font-style:{style};"
                    f"unicode-range:{ranges};src:url(data:font/woff2;base64,{face}) format('woff2')}}"
                )
            if left:
                raise SystemExit(
                    f"{FAMILY[family]} {weight} has no glyph for {''.join(sorted(left))!r}"
                )
        return "".join(rules)


# ── svg helpers ───────────────────────────────────────────────────────────


def n(v: float) -> str:
    """A coordinate, as short as it can be written."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def svg_doc(width: float, height: float, title: str, style: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{n(width)}" height="{n(height)}" '
        f'viewBox="0 0 {n(width)} {n(height)}" role="img" aria-label="{esc(title)}">'
        f"<title>{esc(title)}</title><style>{style}</style>{body}</svg>\n"
    )


# 1-bit line icons on a 16 grid, as the site's PixelIcon draws them
ICONS = {
    "close": '<path d="M3 3l10 10M13 3L3 13"/>',
    "minimize": '<path d="M3 8h10"/>',
    "maximize": '<rect x="3" y="3" width="10" height="10"/>',
    "check": '<path d="M2 8l4 4 8-9"/>',
    "cross": '<path d="M3 3l10 10M13 3L3 13"/>',
    "warn": '<path d="M8 1.5L15 14H1z"/><path d="M8 6v4"/><circle cx="8" cy="12" r="0.4"/>',
    "pulse": '<path d="M1 8h3l2-5 3 10 2-6 1 1h3"/>',
    "arrow": '<path d="M2 8h11M9 4l4 4-4 4"/>',
    "bot": (
        '<rect x="2.5" y="5.5" width="11" height="8.5"/><path d="M8 5.5V3"/>'
        '<circle cx="8" cy="2.1" r="0.9"/><path d="M5.8 8.6v1.8M10.2 8.6v1.8M1 9v2.6M15 9v2.6"/>'
    ),
}


def icon(
    name: str, x: float, y: float, size: float, color: str, stroke: float = 1.4
) -> str:
    return (
        f'<g transform="translate({n(x)} {n(y)}) scale({n(size / 16)})" fill="none" stroke="{color}" '
        f'stroke-width="{n(stroke)}" stroke-linecap="square">{ICONS[name]}</g>'
    )


def badge(x: float, y: float, size: float, dot: str, accent: str = LOGO_ACCENT) -> str:
    """The bracket-and-dot mark; the dot takes the ink it sits on."""
    vx, vy, vw, vh = BADGE_VIEW_BOX
    return (
        f'<svg x="{n(x)}" y="{n(y)}" width="{n(size)}" height="{n(size)}" viewBox="{vx} {vy} {vw} {vh}">'
        f'<g fill="{accent}">{"".join(f"<path d={chr(34)}{d}{chr(34)}/>" for d in BRACKETS)}</g>'
        f'<path fill="{dot}" d="{BADGE_DOT}"/></svg>'
    )


def wordmark(x: float, y: float, height: float, ink: str, mask_id: str) -> str:
    vx, vy, vw, vh = LOGO_VIEW_BOX
    width = height * vw / vh
    return (
        f'<svg x="{n(x)}" y="{n(y)}" width="{n(width)}" height="{n(height)}" viewBox="{vx} {vy} {vw} {vh}">'
        f'<mask id="{mask_id}"><rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="#fff"/>'
        f'<path d="{A_COUNTER}" fill="#000"/></mask>'
        f'<g fill="{ink}" mask="url(#{mask_id})">{"".join(f"<path d={chr(34)}{d}{chr(34)}/>" for d in INK)}</g>'
        f'<g fill="{LOGO_ACCENT}">{"".join(f"<path d={chr(34)}{d}{chr(34)}/>" for d in BRACKETS)}</g></svg>'
    )


# ── animation ─────────────────────────────────────────────────────────────
# GSAP's eases as CSS curves. The back.out overshoots are matched by peak:
# back.out(k) overshoots by 4k^3 / 27(k+1)^2.

EASE = {
    "linear": "linear",
    "power1.inOut": "cubic-bezier(.45,0,.55,1)",
    "power2.inOut": "cubic-bezier(.65,0,.35,1)",
    "power3.inOut": "cubic-bezier(.76,0,.24,1)",
    "power2.in": "cubic-bezier(.32,0,.67,0)",
    "back.out(1.7)": "cubic-bezier(.34,1.56,.64,1)",
    "back.out(2.2)": "cubic-bezier(.34,1.76,.64,1)",
    "back.out(2.4)": "cubic-bezier(.34,1.84,.64,1)",
    "back.out(3)": "cubic-bezier(.34,2.1,.64,1)",
    # the site's --ease-os, which the switch labels' colour transition uses
    "os": "cubic-bezier(.2,.9,.25,1)",
}


class Timeline:
    """CSS keyframes on one looping period.

    A track is one element's values over the loop: (time, value) to start,
    then (time, value, ease) for each move, the ease being GSAP's name for the
    curve that leads into that point. The first value holds from the loop's
    start and the last one to its end."""

    def __init__(self, period: float, prefix: str = "") -> None:
        self.period = period
        self.prefix = prefix
        self.keyframes: list[str] = []

    def track(self, points: list[tuple], fmt) -> str:
        name = f"{self.prefix}k{len(self.keyframes)}"
        keys = [
            (0.0, points[0][1], "linear"),
            *[(p[0], p[1], p[2] if len(p) > 2 else "linear") for p in points],
        ]
        keys.append((self.period, keys[-1][1], "linear"))
        frames, last_pct = [], -1.0
        for i, (t, value, _) in enumerate(keys):
            pct = round(t / self.period * 100, 4)
            if pct <= last_pct:
                continue
            last_pct = pct
            ease = "linear"
            if i + 1 < len(keys) and keys[i + 1][1] != value:
                ease = keys[i + 1][2]
            frames.append(
                f"{n(pct)}%{{{fmt(value)};animation-timing-function:{EASE.get(ease, ease)}}}"
            )
        self.keyframes.append(f"@keyframes {name}{{{''.join(frames)}}}")
        return name

    def css(self) -> str:
        return "".join(self.keyframes)


def anim(*names: str, rest: str = "", box: str = "") -> str:
    """The attributes that run these tracks, resting on `rest` with motion off.
    `box` "c" scales and turns about the element's centre, "t" about its top."""
    return f'class="a{" " + box if box else ""}" style="animation-name:{",".join(names)};{rest}"'


def op(v: float) -> str:
    return f"opacity:{n(v)}"


def tx(v: float) -> str:
    return f"transform:translateX({n(v)}px)"


def ty(v: float) -> str:
    return f"transform:translateY({n(v)}px)"


def scale(v: float) -> str:
    return f"transform:scale({n(v)})"


def scale_y(v: float) -> str:
    return f"transform:scaleY({n(v)})"


def fade_y(v: tuple[float, float]) -> str:
    return f"opacity:{n(v[0])};transform:translateY({n(v[1])}px)"


def fade_scale(v: tuple[float, float]) -> str:
    return f"opacity:{n(v[0])};transform:scale({n(v[1])})"


def color(v: str) -> str:
    return f"color:{v}"


def stops(*pairs: tuple[float, str]) -> str:
    return "".join(f'<stop offset="{n(o)}" stop-color="{c}"/>' for o, c in pairs)


def clear(rgba: str) -> str:
    """The same colour at zero alpha, so a fade to it never greys on the way."""
    return (
        re.sub(r"[\d.]+\)$", "0)", rgba) if rgba.startswith("rgba") else alpha(rgba, 0)
    )


def shadow_filter(
    fid: str,
    box: tuple[float, float, float, float],
    soft: str,
    soft_dy: float,
    soft_blur: float,
    hard: str,
    hard_d: float,
) -> str:
    """A window's two shadows, a soft drop and the hard offset ink edge. The
    region is the box grown by the blur's full reach, so no edge of it shows."""
    x, y, w, h = box
    reach = soft_blur * 1.5
    return (
        f'<filter id="{fid}" filterUnits="userSpaceOnUse" x="{n(x - reach)}" y="{n(y - reach)}" '
        f'width="{n(w + 2 * reach)}" height="{n(h + 2 * reach + soft_dy)}" color-interpolation-filters="sRGB">'
        f'<feGaussianBlur in="SourceAlpha" stdDeviation="{n(soft_blur / 2)}"/><feOffset dy="{n(soft_dy)}" result="b"/>'
        f'<feFlood flood-color="{soft}"/><feComposite in2="b" operator="in" result="soft"/>'
        f'<feOffset in="SourceAlpha" dx="{n(hard_d)}" dy="{n(hard_d)}" result="o"/>'
        f'<feFlood flood-color="{hard}"/><feComposite in2="o" operator="in" result="hard"/>'
        f'<feMerge><feMergeNode in="soft"/><feMergeNode in="hard"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
    )


def glow_filter(fid: str, blur: float) -> str:
    return (
        f'<filter id="{fid}" x="-100%" y="-100%" width="300%" height="300%">'
        f'<feGaussianBlur stdDeviation="{n(blur / 2)}"/></filter>'
    )


def mono_width(text: str, size: float, spacing: float = 0) -> float:
    """IBM Plex Mono sets every glyph 600/1000 em wide."""
    return len(text) * (0.6 * size + spacing)


# ── the hero audit window ─────────────────────────────────────────────────
# The loop, in seconds, is HeroAudit's rhythm: the evals rest, the scan runs
# (starting 1.2s in, as the site's first pass does), the findings rest 7s,
# then every row wipes back and the evals rest out the rest of their 2.6s.
SCAN = 1.2
ROW_AT = tuple(SCAN + t for t in (0.95, 2.34, 3.73, 5.12))
DONE = SCAN + 6.06  # the last row is through: the lens opens and lifts
SETTLED = SCAN + 7.06  # the seal has landed
BACK = SETTLED + 7.0
PERIOD = round(BACK + 0.7 + 1.4, 2)

# The window as the site lays it out, measured off www.ifixai.ai by
# --measure: at a 1440px desktop, and at a 390px phone, where the site
# switches to its phone layout (tighter boxes, details wrapping to two
# lines, the seal without its reference). Positions are CSS px from the
# window's top-left corner and text positions are baselines. Chrome draws
# the site's 1.5px borders a whole pixel wide, so the boxes here are 1px.
LAYOUTS = Path(__file__).with_name("brand_hero_layouts.json")
# where the window sits on its desk: (left, top, desk width, desk height)
DESKS = {"desktop": (72, 56, 728, 620), "phone": (14, 18, 386, 576)}

HERO_ALT = (
    "A customer support agent's refund ticket as its evals see it, resolved with every check green. "
    "The same ticket through an iFixAi audit shows the agent hid a fraud flag, paid a refund a manager "
    "had declined and changed a customer's payout account without authorisation, while closing the "
    "ticket was handled correctly. Illustrative scenario."
)

# the text styles, as (family, weight, style); each layout has its own sizes
TEXT = {
    "tb": (MONO, 700, "normal"),  # the title bar
    "ag": (SANS, 800, "normal"),  # the agent
    "tk": (MONO, 400, "normal"),  # its ticket
    "pl": (MONO, 700, "normal"),  # the status pill
    "sw": (MONO, 700, "normal"),  # the switch
    "ti": (SANS, 800, "normal"),  # a row's title
    "de": (SANS, 400, "normal"),  # a row's detail
    "ch": (MONO, 700, "normal"),  # a severity chip
    "ft": (MONO, 700, "normal"),  # the footer
    "nt": (SANS, 400, "italic"),  # the caption under the window
    "sb": (MONO, 700, "normal"),  # the seal's "Audited by"
    "sn": (MONO, 700, "normal"),  # the seal's name
    "sr": (MONO, 400, "normal"),  # the seal's reference
}
FALLBACK = {
    SANS: "Inter,system-ui,sans-serif",
    MONO: '"IBM Plex Mono",ui-monospace,monospace',
}


def text_css(classes, sizes: dict) -> str:
    rules = []
    for cls in classes:
        family, weight, style = TEXT[cls]
        size, spacing = sizes[cls]
        rule = f".{cls}{{font:{style} {weight} {n(size)}px {family},{FALLBACK[family]}"
        rule += f";letter-spacing:{n(spacing)}px}}" if spacing else "}"
        rules.append(rule)
    return "".join(rules)


# with motion off, every animated element rests on the finished audit
ANIMATION_CSS = (
    f".a{{animation-duration:{n(PERIOD)}s;animation-iteration-count:infinite}}"
    ".c{transform-box:fill-box;transform-origin:center}"
    ".t{transform-box:fill-box;transform-origin:top}"
    ".bl{animation:bl .8s infinite}@keyframes bl{0%,50%{opacity:1}50.01%,100%{opacity:.2}}"
    "@media (prefers-reduced-motion:reduce){.a,.bl{animation:none!important}}"
)
# a file carrying both themes shows the one the viewer's colour scheme
# asks for (GitHub's own theme in Chrome and Firefox, the OS's in Safari)
BOTH_THEMES_CSS = ".td{display:none}@media (prefers-color-scheme:dark){.tl{display:none}.td{display:inline}}"


def hero_art(
    c: dict, L: dict, desk: tuple, fonts: Fonts, p: str = ""
) -> tuple[str, str]:
    """One theme of the hero on its desk, as (markup, keyframes). `p`
    prefixes every id and keyframe, so two themes can share a file."""
    tl = Timeline(PERIOD, p)
    defs: list[str] = []
    out: list[str] = []
    win_w, win_h = L["win"][2], L["win"][3]

    def text(
        cls: str,
        line: list,
        fill: str,
        dx: float = 0,
        dy: float = 0,
        extra: str = "",
        upper: bool = False,
    ) -> str:
        s, x, y = line
        s = s.upper() if upper else s
        family, weight, style = TEXT[cls]
        fonts.use(family, weight, style, s)
        return f'<text class="{cls}" x="{n(x - dx)}" y="{n(y - dy)}" fill="{fill}"{extra}>{esc(s)}</text>'

    def pill_rect(
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        stroke: str,
        sw: float = 1,
        r: float | None = None,
    ) -> str:
        """A CSS box as SVG: the border's centre line sits half its width in."""
        i = sw / 2
        radius = h / 2 - i if r is None else max(r - i, 0)
        return (
            f'<rect x="{n(x + i)}" y="{n(y + i)}" width="{n(w - sw)}" height="{n(h - sw)}" rx="{n(radius)}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{n(sw)}"/>'
        )

    # ── the window's own chrome ──
    defs.append(
        shadow_filter(
            f"{p}ws",
            (0, 0, win_w, win_h),
            c["shadow_soft"],
            18,
            50,
            c["shadow_hard"],
            4,
        )
    )
    defs.append(
        shadow_filter(
            f"{p}ss", L["seal"], c["shadow_soft"], 18, 50, c["shadow_hard"], 4
        )
    )
    defs.append(
        f'<radialGradient id="{p}glow">{stops((0, c["glow"]), (0.74, clear(c["glow"])))}</radialGradient>'
    )
    gx, gy, gw, gh = L["glow"]
    out.append(
        f'<ellipse cx="{n(gx + gw / 2)}" cy="{n(gy + gh / 2)}" rx="{n(gw / 2)}" ry="{n(gh / 2)}" fill="url(#{p}glow)"/>'
    )
    out.append(
        f'<rect x="1" y="1" width="{n(win_w - 2)}" height="{n(win_h - 2)}" rx="6" fill="{c["window"]}" '
        f'stroke="{c["ink"]}" stroke-width="2" filter="url(#{p}ws)"/>'
    )
    # title bar: pinstripes, the title on its own plate, three boxes
    bx, by, bw, bh = L["titlebar"]
    out.append(
        f'<rect x="{n(bx)}" y="{n(by + bh - 2)}" width="{n(bw)}" height="2" fill="{c["ink"]}"/>'
    )
    sx, sy, sw, sh = L["stripe"]
    stripe_y = sy + sh - 1
    while stripe_y >= sy:
        out.append(
            f'<rect x="{n(sx)}" y="{n(stripe_y)}" width="{n(sw)}" height="1" fill="{c["pinstripe"]}"/>'
        )
        stripe_y -= 3
    px, py, pw, ph = L["plate"]
    out.append(
        f'<rect x="{n(px)}" y="{n(py)}" width="{n(pw)}" height="{n(ph)}" fill="{c["window"]}"/>'
    )
    out.append(text("tb", L["title"][0], c["text"]))
    for (x, y, s, _), (ix, iy, isz, _), name in zip(
        L["tbBoxes"], L["tbIcons"], ("close", "minimize", "maximize")
    ):
        out.append(pill_rect(x, y, s, s, c["window"], c["ink"], r=4))
        out.append(
            f'<path d="M{n(x + 1)} {n(y + s - 1.5)}H{n(x + s - 1.5)}V{n(y + 1)}" fill="none" stroke="{c["bevel_dark"]}"/>'
            f'<path d="M{n(x + 1.5)} {n(y + s - 1.5)}V{n(y + 1.5)}H{n(x + s - 1)}" fill="none" stroke="{c["bevel_light"]}"/>'
        )
        out.append(icon(name, ix, iy, isz, c["text"]))

    # ── the agent, its ticket and where the ticket stands ──
    ax, ay, asz, _ = L["avatar"]
    out.append(pill_rect(ax, ay, asz, asz, c["window_2"], c["ink"], r=4))
    ix, iy, isz, _ = L["avatarIcon"]
    out.append(icon("bot", ix, iy, isz, c["text"], 1.5))
    # On a phone the name wraps to two lines and the site cuts the ticket
    # short with an ellipsis; the whole ticket is drawn here, since it runs
    # below the status pill rather than into it.
    out += [text("ag", line, c["text"]) for line in L["agent"]]
    out.append(text("tk", L["ticket"][0], c["text_3"]))

    def status(i: int, ink: str, bg: str, border: str, track: str, rest: float) -> str:
        item = L["status"][i]
        x, y, w, h = item["rect"]
        dx, dy, ds, _ = item["dot"]
        label = item["text"][0]
        parts = [pill_rect(x, y, w, h, bg, border)]
        dot = f'<circle cx="{n(dx + ds / 2)}" cy="{n(dy + ds / 2)}" r="{n(ds / 2)}" fill="{ink}"'
        parts.append(dot + (' class="bl"/>' if i == 1 else "/>"))
        parts.append(text("pl", label, ink, upper=True))
        if i == 1:
            # The count runs on a column of every number from 0 to 250,
            # stepped through one at a time inside a window one line high.
            slash = next(line for line in item["text"] if line[0] == "/")
            right, base = slash[1], slash[2]
            size = L["fonts"]["pl"][0]
            column = "".join(
                f'<tspan x="{n(right)}" y="{n(base + k * 16)}">{k}</tspan>'
                for k in range(251)
            )
            fonts.use(MONO, 700, "normal", "0123456789/")
            defs.append(
                f'<clipPath id="{p}count"><rect x="{n(right - 30)}" y="{n(base - 1.1 * size)}" '
                f'width="30" height="{n(1.5 * size)}"/></clipPath>'
            )
            points = [(SCAN + 0.3, 0)]
            span = DONE - (SCAN + 0.3)
            for j in range(1, 13):
                f = j / 12
                eased = 2 * f * f if f < 0.5 else 1 - (-2 * f + 2) ** 2 / 2
                k = round(250 * eased)
                steps = max(k - round(-points[-1][1] / 16), 1)
                points.append((SCAN + 0.3 + span * f, -16 * k, f"steps({steps},end)"))
            column_track = tl.track(points, ty)
            parts.append(
                f'<g clip-path="url(#{p}count)" opacity=".85"><g {anim(column_track, rest="transform:translateY(-4000px)")}>'
                f'<text class="pl" fill="{ink}" text-anchor="end">{column}</text></g></g>'
            )
            parts.append(text("pl", ["/250", right, base], ink, extra=' opacity=".85"'))
        return f"<g {anim(track, rest=f'opacity:{n(rest)}')}>{''.join(parts)}</g>"

    dash_track = tl.track(
        [
            (SCAN + 0.15, (1, 0)),
            (SCAN + 0.4, (0, -8), "power3.inOut"),
            (SETTLED, (0, -8)),
            (SETTLED + 0.01, (0, 0)),
            (BACK + 0.15, (0, 0)),
            (BACK + 0.45, (1, 0), "power2.inOut"),
        ],
        fade_y,
    )
    run_track = tl.track(
        [
            (SCAN + 0.3, (0, 8)),
            (SCAN + 0.55, (1, 0), "power3.inOut"),
            (DONE + 0.1, (1, 0)),
            (DONE + 0.35, (0, -8), "power3.inOut"),
        ],
        fade_y,
    )
    audit_track = tl.track(
        [
            (DONE + 0.25, (0, 8)),
            (DONE + 0.55, (1, 0), "power3.inOut"),
            (BACK, (1, 0)),
            (BACK + 0.2, (0, 0), "power2.inOut"),
        ],
        fade_y,
    )
    out.append(
        status(
            0, c["pass"], alpha(c["pass"], 0.12), alpha(c["pass"], 0.45), dash_track, 0
        )
    )
    out.append(
        status(1, c["cyan_ink"], c["run_bg"], alpha(c["cyan"], 0.55), run_track, 0)
    )
    out.append(
        status(
            2, c["fail"], alpha(c["fail"], 0.12), alpha(c["fail"], 0.5), audit_track, 1
        )
    )

    # ── the switch: the same ticket as the evals tell it, or as the audit finds it ──
    out.append(pill_rect(*L["switch"], c["sunken"], c["ink"], sw=2, r=4))
    tx_, ty_, tw, th = L["thumb"]
    reach = (tw + th) * 0.25  # a CSS 135deg gradient's half-length along each axis
    defs.append(
        f'<linearGradient id="{p}thumb" gradientUnits="userSpaceOnUse" x1="{n(tw / 2 - reach)}" '
        f'y1="{n(th / 2 - reach)}" x2="{n(tw / 2 + reach)}" y2="{n(th / 2 + reach)}">'
        f"{stops((0, c['accent']), (1, c['accent_2']))}</linearGradient>"
    )
    defs.append(
        f'<filter id="{p}ts" x="-10%" y="-40%" width="120%" height="200%">'
        f'<feDropShadow dy="2" stdDeviation="3.5" flood-color="{c["accent_shadow"]}"/></filter>'
    )
    thumb_track = tl.track(
        [
            (SCAN, 0),
            (SCAN + 0.45, tw, "power3.inOut"),
            (BACK, tw),
            (BACK + 0.45, 0, "power3.inOut"),
        ],
        tx,
    )
    out.append(
        f'<g transform="translate({n(tx_)} {n(ty_)})"><g {anim(thumb_track, rest=f"transform:translateX({n(tw)}px)")}>'
        f'<rect width="{n(tw)}" height="{n(th)}" rx="3" fill="url(#{p}thumb)" filter="url(#{p}ts)"/></g></g>'
    )
    # the labels change hands as the thumb passes halfway
    evals_color = tl.track(
        [
            (SCAN + 0.2, c["on_accent"]),
            (SCAN + 0.42, c["text_2"], "os"),
            (BACK + 0.2, c["text_2"]),
            (BACK + 0.42, c["on_accent"], "os"),
        ],
        color,
    )
    audit_color = tl.track(
        [
            (SCAN + 0.2, c["text_2"]),
            (SCAN + 0.42, c["on_accent"], "os"),
            (BACK + 0.2, c["on_accent"]),
            (BACK + 0.42, c["text_2"], "os"),
        ],
        color,
    )
    evals, audit = L["btns"]
    ex, ey, es, _ = evals["icon"]
    out.append(
        f"<g {anim(evals_color, rest='color:' + c['text_2'])}>{icon('pulse', ex, ey, es, 'currentColor', 1.6)}"
        f"{text('sw', evals['text'][0], 'currentColor')}</g>"
    )
    ux, uy, us, _ = audit["icon"]
    out.append(
        f"<g {anim(audit_color, rest='color:' + c['on_accent'])}>{badge(ux, uy, us, 'currentColor')}"
        f"{text('sw', audit['text'][0], 'currentColor')}</g>"
    )

    # ── the ledger ──
    chip_style = {
        "ok": (c["pass"], alpha(c["pass"], 0.5), c["window"]),
        "passed": (c["pass"], alpha(c["pass"], 0.5), c["window"]),
        "high": (c["fail"], alpha(c["fail"], 0.6), c["window"]),
        "critical": ("#ffffff", c["fail_fill"], c["fail_fill"]),
    }
    defs.append(
        f'<linearGradient id="{p}scan">{stops((0.35, clear(alpha(c["cyan"], 0.18))), (1, alpha(c["cyan"], 0.18)))}</linearGradient>'
    )
    defs.append(glow_filter(f"{p}sg", 10))

    def face(fc: dict, finding: bool, row: int) -> str:
        """One reading of a row, in the row's face coordinates."""
        fx, fy, fw, fh = fc["rect"]
        kind = fc["chipText"][0][0].lower()
        fail = kind in ("high", "critical")
        bg = (c["fail_face"] if fail else c["pass_face"]) if finding else c["window"]
        parts = [f'<rect width="{n(fw)}" height="{n(fh)}" rx="3" fill="{bg}"/>']
        mx, my, ms, _ = fc["mark"]
        ix, iy, isz, _ = fc["icon"]
        circle = f'<circle cx="{n(mx - fx + ms / 2)}" cy="{n(my - fy + ms / 2)}" r="{n(ms / 2 - 0.5)}"'
        if fail:
            mark = (
                f'{circle} fill="{c["fail_fill"]}" stroke="{c["fail_fill"]}"/>'
                + icon("cross", ix - fx, iy - fy, isz, "#ffffff", 2.4)
            )
        else:
            mark = (
                f'{circle} fill="{alpha(c["pass"], 0.12)}" stroke="{alpha(c["pass"], 0.45)}"/>'
                + icon("check", ix - fx, iy - fy, isz, c["pass"], 2.4)
            )
        ink, border, chip_bg = chip_style[kind]
        cx, cy, cw, ch = fc["chip"]
        chip = pill_rect(cx - fx, cy - fy, cw, ch, chip_bg, border) + text(
            "ch", fc["chipText"][0], ink, fx, fy, upper=True
        )
        if finding:
            # the verdict lands just behind the scan line: the mark pops,
            # then the pill
            at = ROW_AT[row]
            mark_track = tl.track(
                [
                    (at + 0.12, 0.35),
                    (at + 0.52, 1, "back.out(3)"),
                    (BACK + 0.7, 1),
                    (BACK + 0.71, 0.35),
                ],
                scale,
            )
            chip_track = tl.track(
                [
                    (at + 0.46, (0, 0.6)),
                    (at + 0.81, (1, 1), "back.out(2.4)"),
                    (BACK + 0.7, (1, 1)),
                    (BACK + 0.71, (0, 0.6)),
                ],
                fade_scale,
            )
            mark = f"<g {anim(mark_track, box='c')}>{mark}</g>"
            chip = f"<g {anim(chip_track, box='c')}>{chip}</g>"
        parts.append(mark)
        parts += [text("ti", line, c["text"], fx, fy) for line in fc["title"]]
        detail_ink = c["text_2"] if finding else c["text_3"]
        parts += [text("de", line, detail_ink, fx, fy) for line in fc["detail"]]
        parts.append(chip)
        return "".join(parts)

    for i, row in enumerate(L["rows"]):
        rx, ry, rw, rh = row["rect"]
        surface, truth = row["faces"]
        fx, fy, fw, fh = surface["rect"]
        at = ROW_AT[i]
        out.append(pill_rect(rx, ry, rw, rh, c["window"], c["border_soft"], r=4))
        defs.append(
            f'<clipPath id="{p}face{i}"><rect width="{n(fw)}" height="{n(fh)}" rx="3"/></clipPath>'
        )
        # The finding is uncovered by a viewport sliding in from the left
        # while its content slides the other way, so the content holds still
        # and only the edge moves: a wipe made of plain transforms, which
        # every browser animates (a clip-path's shape is not).
        back_at = BACK + 0.05 + 0.05 * i
        outer = tl.track(
            [
                (at, -fw),
                (at + 0.62, 0, "power2.inOut"),
                (back_at, 0),
                (back_at + 0.5, -fw, "power2.inOut"),
            ],
            tx,
        )
        inner = tl.track(
            [
                (at, fw),
                (at + 0.62, 0, "power2.inOut"),
                (back_at, 0),
                (back_at + 0.5, fw, "power2.inOut"),
            ],
            tx,
        )
        scan_x = tl.track([(at, -fw), (at + 0.62, 0, "power2.inOut")], tx)
        scan_o = tl.track(
            [(at, 0), (at + 0.01, 1), (at + 0.62, 1), (at + 0.82, 0, "linear")], op
        )
        out.append(
            f'<g transform="translate({n(fx)} {n(fy)})">'
            + face(surface, finding=False, row=i)
            + f'<g {anim(outer)}><svg width="{n(fw)}" height="{n(fh)}" overflow="hidden">'
            f"<g {anim(inner)}>{face(truth, finding=True, row=i)}</g></svg></g>"
            f'<g clip-path="url(#{p}face{i})"><g {anim(scan_x, scan_o, rest="opacity:0")}>'
            f'<rect width="{n(fw)}" height="{n(fh)}" fill="url(#{p}scan)"/>'
            f'<rect x="{n(fw - 3)}" width="4" height="{n(fh)}" fill="{alpha(c["cyan"], 0.7)}" filter="url(#{p}sg)"/>'
            f'<rect x="{n(fw - 2)}" width="2" height="{n(fh)}" fill="{c["cyan"]}"/></g></g></g>'
        )

    # The lens: the badge's two brackets closing on the row under
    # inspection. It is drawn the first row's height and stretched to each
    # row's own, since phone rows wrap to different heights.
    defs.append(glow_filter(f"{p}lg", 6))
    lens_x, _, lens_w, _ = L["lens"]
    ledger_y, ledger_h = L["ledger"][1], L["ledger"][3]
    tops = [row["rect"][1] - ledger_y for row in L["rows"]]
    heights = [row["rect"][3] for row in L["rows"]]
    h0 = heights[0]
    lens_o = tl.track(
        [
            (SCAN + 0.45, 0),
            (SCAN + 0.87, 1, "back.out(2.2)"),
            (DONE + 0.55, 1),
            (DONE + 0.9, 0, "linear"),
        ],
        op,
    )

    def follow(at_row, final):
        points = [(SCAN + 0.45, at_row(0))]
        for i in range(1, 4):
            points += [
                (ROW_AT[i] - 0.45, at_row(i - 1)),
                (ROW_AT[i], at_row(i), "power3.inOut"),
            ]
        return [*points, (DONE, at_row(3)), (DONE + 0.5, final, "power3.inOut")]

    lens_y = tl.track(follow(lambda i: tops[i], 0), ty)
    lens_open = tl.track(follow(lambda i: heights[i] / h0, ledger_h / h0), scale_y)
    lens_foot = tl.track(follow(lambda i: heights[i] - h0, ledger_h - h0), ty)
    lens_scale = tl.track(
        [(SCAN + 0.45, 1.1), (SCAN + 0.87, 1, "back.out(2.2)")], scale
    )
    corner = "M0 22V8a8 8 0 0 1 8-8h14v4H8a4 4 0 0 0-4 4v14z"
    corners = (
        f'<path d="{corner}" transform="translate(0 -5)" fill="{c["cyan"]}"/>'
        f'<g {anim(lens_foot)}><path d="{corner}" transform="translate({n(lens_w)} {n(h0 + 5)}) rotate(180)" '
        f'fill="{c["cyan"]}"/></g>'
    )
    out.append(
        f'<g transform="translate({n(lens_x)} {n(ledger_y)})"><g {anim(lens_y, lens_o, rest="opacity:0")}>'
        f"<g {anim(lens_scale, box='c')}><g {anim(lens_open, box='t')}>"
        f'<rect width="{n(lens_w)}" height="{n(h0)}" rx="8" fill="{alpha(c["cyan"], 0.06)}"/></g>'
        f'<g filter="url(#{p}lg)" opacity=".55">{corners}</g>{corners}</g></g></g>'
    )

    # ── what the window concludes, in one line ──
    fx, fy, fw, _ = L["foot"]
    out.append(
        f'<path d="M{n(fx)} {n(fy + 0.5)}H{n(fx + fw)}" stroke="{c["border_soft"]}" stroke-dasharray="3 3"/>'
    )
    foot_dash = tl.track(
        [
            (DONE + 0.1, (1, 0)),
            (DONE + 0.35, (0, -6), "power3.inOut"),
            (SETTLED, (0, -6)),
            (SETTLED + 0.01, (0, 0)),
            (BACK + 0.15, (0, 0)),
            (BACK + 0.45, (1, 0), "power2.inOut"),
        ],
        fade_y,
    )
    foot_audit = tl.track(
        [
            (DONE + 0.25, (0, 6)),
            (DONE + 0.55, (1, 0), "power3.inOut"),
            (BACK, (1, 0)),
            (BACK + 0.2, (0, 0), "power2.inOut"),
        ],
        fade_y,
    )
    for item, name, ink, track, rest in (
        (L["footItems"][0], "pulse", c["pass"], foot_dash, 0),
        (L["footItems"][1], "warn", c["fail"], foot_audit, 1),
    ):
        ix, iy, isz, _ = item["icon"]
        out.append(
            f"<g {anim(track, rest=f'opacity:{rest}')}>{icon(name, ix, iy, isz, ink, 1.6)}"
            f"{text('ft', item['text'][0], ink)}</g>"
        )
    out.append(text("nt", L["note"][0], c["desk_ink_3"]))

    # ── the mark the audit leaves, stamped across the window's corner ──
    sx, sy, sw_, sh = L["seal"]
    seal_track = tl.track(
        [
            (DONE + 0.45, (0, 1.7, -16)),
            (DONE + 1.0, (1, 1, -6), "back.out(1.7)"),
            (BACK, (1, 1, -6)),
            (BACK + 0.28, (0, 0.9, -6), "power2.in"),
            (BACK + 0.7, (0, 0.9, -6)),
            (BACK + 0.71, (0, 1.7, -16)),
        ],
        lambda v: f"opacity:{n(v[0])};transform:rotate({n(v[2])}deg) scale({n(v[1])})",
    )
    bx, by, bs, _ = L["sealBadge"]
    seal = [
        f'<rect x="{n(sx + 1)}" y="{n(sy + 1)}" width="{n(sw_ - 2)}" height="{n(sh - 2)}" rx="6" fill="{c["window"]}" '
        f'stroke="{c["ink"]}" stroke-width="2" filter="url(#{p}ss)"/>',
        f'<rect x="{n(sx + 5.75)}" y="{n(sy + 5.75)}" width="{n(sw_ - 11.5)}" height="{n(sh - 11.5)}" rx="1.25" '
        f'fill="none" stroke="{c["cyan"]}" stroke-width="1.5"/>',
        badge(bx, by, bs, c["text"]),
        text("sb", L["sealBy"][0], c["text_3"], upper=True),
        text("sn", L["sealName"][0], c["text"]),
    ]
    if L["sealRef"]:  # the phone layout drops the reference number
        rx, ry, _, rh = L["sealRef"]
        seal.append(
            f'<path d="M{n(rx + 0.5)} {n(ry)}V{n(ry + rh)}" stroke="{c["border_soft"]}" stroke-dasharray="3 3"/>'
        )
        seal.append(text("sr", L["sealRefText"][0], c["text_3"]))
    out.append(
        f"<g {anim(seal_track, rest='transform:rotate(-6deg)', box='c')}>{''.join(seal)}</g>"
    )

    # ── the desk it all sits on ──
    ox, oy, width, height = desk
    defs.append(
        f'<pattern id="{p}dots" width="22" height="22" patternUnits="userSpaceOnUse" x="-11" y="-11">'
        f'<circle cx="11" cy="11" r="1.2" fill="{c["desk_dot"]}"/></pattern>'
    )
    defs.append(
        f'<radialGradient id="{p}sheen">{stops((0, c["sheen"]), (0.56, clear(c["sheen"])))}</radialGradient>'
    )
    defs.append(
        f'<clipPath id="{p}desk"><rect width="{width}" height="{height}" rx="16"/></clipPath>'
    )
    desk_art = (
        f'<rect width="{width}" height="{height}" fill="{c["desk"]}"/>'
        f'<rect width="{width}" height="{height}" fill="url(#{p}dots)"/>'
        f'<ellipse cx="{n(width / 2)}" cy="{n(-0.14 * height)}" rx="{n(1.25 * width)}" ry="{n(0.7 * height)}" '
        f'fill="url(#{p}sheen)"/>'
    )
    edge = c["desk_edge"]
    frame = (
        f'<rect x=".5" y=".5" width="{width - 1}" height="{height - 1}" rx="15.5" fill="none" stroke="{edge}"/>'
        if edge
        else ""
    )
    markup = (
        f'<defs>{"".join(defs)}</defs><g clip-path="url(#{p}desk)">{desk_art}'
        f'<g transform="translate({ox} {oy})">{"".join(out)}</g></g>{frame}'
    )
    return markup, tl.css()


def hero(themes: list[dict], layout: str, files: FontFiles, layouts: dict) -> str:
    """The hero for one layout, in one theme, or in both when the file has to
    follow the viewer's colour scheme by itself (the phone one, see README)."""
    L = layouts[layout]
    desk = DESKS[layout]
    fonts = Fonts(files)
    if len(themes) == 1:
        markup, keyframes = hero_art(themes[0], L, desk, fonts)
        both = ""
    else:
        light, light_keys = hero_art(themes[0], L, desk, fonts, "l")
        dark, dark_keys = hero_art(themes[1], L, desk, fonts, "d")
        markup = f'<g class="tl">{light}</g><g class="td">{dark}</g>'
        keyframes, both = light_keys + dark_keys, BOTH_THEMES_CSS
    used = sorted({m for m in re.findall(r'class="([a-z]{2})"', markup) if m in TEXT})
    style = fonts.css() + text_css(used, L["fonts"]) + ANIMATION_CSS + both + keyframes
    return svg_doc(desk[2], desk[3], HERO_ALT, style, markup)


# ── the wordmark and the masthead ─────────────────────────────────────────


def logo(mode: str) -> str:
    height = 160
    width = height * LOGO_VIEW_BOX[2] / LOGO_VIEW_BOX[3]
    return svg_doc(
        width, height, "iFixAi", "", wordmark(0, 0, height, LOGO_INK[mode], "counter")
    )


TAGLINE = "Independent auditing for AI agents"


def masthead(mode: str, files: FontFiles) -> str:
    """The wordmark over the category line, as the site's hero sets them:
    IBM Plex Mono at 0.13 of the mark's height, spaced 0.2em, ruled off on
    both sides the way the site's stacked layout centres it."""
    fonts = Fonts(files)
    label = TAGLINE.upper()
    fonts.use(MONO, 600, "normal", label)
    logo_h, size, spacing = 78, 10.08, 2.016
    logo_w = logo_h * LOGO_VIEW_BOX[2] / LOGO_VIEW_BOX[3]
    # Plex Mono sets the spacing after the last letter too; centre the ink
    label_w = mono_width(label, size, spacing) - spacing
    width, height, gap = 400, 106, 14
    rule = (width - label_w) / 2 - gap
    muted = MUTED[mode]
    body = (
        wordmark((width - logo_w) / 2, 0, logo_h, LOGO_INK[mode], "counter")
        + f'<text x="{n((width - label_w) / 2)}" y="101" fill="{muted}" class="tg">{esc(label)}</text>'
        + f'<g fill="{muted}" opacity=".5"><rect y="97.05" width="{n(rule)}" height="1"/>'
        f'<rect x="{n(width - rule)}" y="97.05" width="{n(rule)}" height="1"/></g>'
    )
    style = (
        fonts.css()
        + f".tg{{font:600 {n(size)}px {MONO},{FALLBACK[MONO]};letter-spacing:{n(spacing)}px}}"
    )
    return svg_doc(width, height, f"iFixAi: {TAGLINE}", style, body)


# ── the links under the hero, drawn as the site's buttons ─────────────────


def sans_width(files: FontFiles, text: str, weight: int, size: float) -> float:
    """Inter's advance widths for `text`, kerning aside (a pixel at most here)."""
    font = TTFont(io.BytesIO(files.get("Inter", weight, "normal", "latin")))
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": weight})
    cmap, hmtx = font.getBestCmap(), font["hmtx"]
    return sum(hmtx[cmap[ord(ch)]][0] for ch in text) * size / font["head"].unitsPerEm


BUTTON_SIZE, BUTTON_PAD, BUTTON_H, BUTTON_MARGIN = 15.2, 24, 44, 16


def button_art(
    c: dict, files: FontFiles, fonts: Fonts, label: str, primary: bool, p: str = ""
) -> tuple[str, float]:
    """The site's .os-btn in one theme, as (markup, canvas width): Inter 600
    at 15.2px, a 2px ink border, the hard ink shadow, the accent gradient
    when it is the primary action. The primary ends on the arrow the site's
    "Start now" carries; the other leads with the badge."""
    fonts.use(SANS, 600, "normal", label)
    size, height, margin = BUTTON_SIZE, BUTTON_H, BUTTON_MARGIN
    label_w = sans_width(files, label, 600, size)
    icon_w, gap = (13, 7) if primary else (18, 9)
    width = BUTTON_PAD * 2 + label_w + gap + icon_w
    x0 = y0 = margin
    content_x = x0 + BUTTON_PAD
    # Inter's capitals stand 0.73em tall: centre them, not the line box
    base = y0 + height / 2 + 0.73 * size / 2
    defs = shadow_filter(
        f"{p}bs",
        (x0, y0, width, height),
        c["button_shadow"],
        4,
        12,
        c["shadow_hard"],
        3,
    )
    if primary:
        reach = (width + height) / 4
        cx, cy = x0 + width / 2, y0 + height / 2
        defs += (
            f'<linearGradient id="{p}bg" gradientUnits="userSpaceOnUse" x1="{n(cx - reach)}" y1="{n(cy - reach)}" '
            f'x2="{n(cx + reach)}" y2="{n(cy + reach)}">{stops((0, c["accent"]), (1, c["accent_2"]))}</linearGradient>'
        )
        fill, ink = f"url(#{p}bg)", c["on_accent"]
        content = (
            f'<text x="{n(content_x)}" y="{n(base)}" fill="{ink}" class="bt">{esc(label)}</text>'
            + icon(
                "arrow",
                content_x + label_w + gap,
                y0 + (height - icon_w) / 2,
                icon_w,
                ink,
            )
        )
    else:
        fill, ink = c["window"], c["text"]
        content = badge(content_x, y0 + (height - icon_w) / 2, icon_w, ink) + (
            f'<text x="{n(content_x + icon_w + gap)}" y="{n(base)}" fill="{ink}" class="bt">{esc(label)}</text>'
        )
    markup = (
        f"<defs>{defs}</defs>"
        f'<rect x="{n(x0 + 1)}" y="{n(y0 + 1)}" width="{n(width - 2)}" height="{n(height - 2)}" rx="3" fill="{fill}" '
        f'stroke="{c["ink"]}" stroke-width="2" filter="url(#{p}bs)"/>{content}'
    )
    return markup, width + margin * 2


def button(themes: list[dict], files: FontFiles, label: str, primary: bool) -> str:
    """A button in one theme, or in both for the phone layout (see hero())."""
    fonts = Fonts(files)
    if len(themes) == 1:
        markup, width = button_art(themes[0], files, fonts, label, primary)
        both = ""
    else:
        light, width = button_art(themes[0], files, fonts, label, primary, "l")
        dark, _ = button_art(themes[1], files, fonts, label, primary, "d")
        markup, both = (
            f'<g class="tl">{light}</g><g class="td">{dark}</g>',
            BOTH_THEMES_CSS,
        )
    style = (
        fonts.css()
        + f".bt{{font:600 {n(BUTTON_SIZE)}px {SANS},{FALLBACK[SANS]}}}"
        + both
    )
    return svg_doc(width, BUTTON_H + BUTTON_MARGIN * 2, label, style, markup)


# ── measuring the site ────────────────────────────────────────────────────

SITE = "https://www.ifixai.ai/"
VIEWPORTS = {
    "desktop": {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2},
    "phone": {
        "viewport": {"width": 390, "height": 844},
        "device_scale_factor": 3,
        "is_mobile": True,
    },
}
# Lays a copy of the hero window out with every animated style stripped, so
# each reading of each row is measured where the stylesheet puts it, and
# returns every box and every line of text (wrapped lines one by one) the
# drawing above reads.
MEASURE_JS = r"""
() => {
  document.querySelectorAll('.ha-measure').forEach(n => n.remove());
  const fig = document.querySelector('figure.ha');
  const clone = fig.cloneNode(true);
  clone.classList.add('ha-measure');
  clone.querySelectorAll('[style]').forEach(n => {
    if (n.tagName.toLowerCase() !== 'svg' && !n.classList.contains('os-titlebar-box')) n.removeAttribute('style');
  });
  clone.style.cssText = `position:absolute;left:0;top:0;width:${fig.getBoundingClientRect().width}px;`;
  fig.parentElement.appendChild(clone);
  // reduced motion pre-slides the thumb and tilts the seal; measure them at rest
  clone.querySelector('.ha-seal').style.transform = 'none';
  clone.querySelector('.ha-switch-thumb').style.transform = 'none';
  const W = clone.querySelector('.ha-win').getBoundingClientRect();
  const f = v => Math.round(v * 100) / 100;
  const box = el => {
    const r = el.getBoundingClientRect();
    return r.width ? [f(r.x - W.x), f(r.y - W.y), f(r.width), f(r.height)] : null;
  };
  const q = (s, root = clone) => root.querySelector(s);
  const qa = (s, root = clone) => [...root.querySelectorAll(s)];
  // every line of every text node under el, as [text, x, baseline]
  const lines = el => {
    if (!el || !el.getBoundingClientRect().width) return null;
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    const nodes = []; let n;
    while ((n = walker.nextNode())) if (n.nodeValue.trim()) nodes.push(n);
    const out = [];
    for (const node of nodes) {
      const span = document.createElement('span');
      node.parentNode.insertBefore(span, node); span.appendChild(node);
      const probe = document.createElement('span');
      probe.style.cssText = 'display:inline-block;width:0;height:0;vertical-align:baseline';
      span.insertBefore(probe, node);
      const r = document.createRange();
      r.setStart(node, 0); r.setEnd(node, 1);
      const ascent = probe.getBoundingClientRect().y - r.getClientRects()[0].y;
      probe.remove();
      const s = node.nodeValue; let cur = null; const mine = [];
      for (let i = 0; i < s.length; i++) {
        r.setStart(node, i); r.setEnd(node, i + 1);
        const c = r.getClientRects()[0]; if (!c) continue;
        if (!cur || Math.abs(c.y - cur.y) > 2) { cur = {y: c.y, x: c.x, text: ''}; mine.push(cur); }
        cur.text += s[i];
      }
      for (const l of mine) {
        const lead = l.text.length - l.text.trimStart().length;
        out.push([l.text.trim(), f(l.x - W.x), f(l.y + ascent - W.y), lead]);
      }
    }
    return out.map(([t, x, b]) => [t, x, b]);
  };
  const font = s => { const c = getComputedStyle(q(s)); return [parseFloat(c.fontSize), parseFloat(c.letterSpacing) || 0]; };
  const spec = {
    win: box(q('.ha-win')),
    titlebar: box(q('.os-titlebar')),
    tbBoxes: qa('.os-titlebar-box').map(box),
    tbIcons: qa('.os-titlebar-box svg').map(box),
    stripe: box(q('.os-titlebar-pinstripe')),
    plate: box(q('.os-titlebar-text')),
    title: lines(q('.os-titlebar-text')),
    avatar: box(q('.ha-avatar')), avatarIcon: box(q('.ha-avatar svg')),
    agent: lines(q('.ha-agent')), ticket: lines(q('.ha-ticket')),
    status: qa('.ha-status-item').map(e => ({rect: box(e), dot: box(q('.ha-status-dot', e)), text: lines(e)})),
    switch: box(q('.ha-switch')), thumb: box(q('.ha-switch-thumb')),
    btns: qa('.ha-switch-btn').map(e => ({icon: box(q('svg', e)), text: lines(e)})),
    ledger: box(q('.ha-ledger')),
    rows: qa('.ha-row').map(row => ({rect: box(row), faces: qa('.ha-face', row).map(fc => ({
      rect: box(fc), mark: box(q('.ha-mark', fc)), icon: box(q('.ha-mark svg', fc)),
      title: lines(q('.ha-title', fc)), detail: lines(q('.ha-detail', fc)),
      chip: box(q('.ha-chip', fc)), chipText: lines(q('.ha-chip', fc)),
    }))})),
    lens: box(q('.ha-lens')),
    foot: box(q('.ha-foot')),
    footItems: qa('.ha-foot-item').map(e => ({icon: box(q('svg', e)), text: lines(e)})),
    note: lines(q('.ha-note')),
    seal: box(q('.ha-seal')), sealBadge: box(q('.ha-seal svg')),
    sealBy: lines(q('.ha-seal-by')), sealName: lines(q('.ha-seal-name')),
    sealRef: box(q('.ha-seal-ref')), sealRefText: lines(q('.ha-seal-ref')),
    glow: box(q('.ha-glow')),
    fonts: {
      tb: font('.os-titlebar-text'), ag: font('.ha-agent'), tk: font('.ha-ticket'), pl: font('.ha-status-item'),
      sw: font('.ha-switch-btn'), ti: font('.ha-title'), de: font('.ha-detail'), ch: font('.ha-chip'),
      ft: font('.ha-foot-item'), nt: font('.ha-note'), sb: font('.ha-seal-by'), sn: font('.ha-seal-name'), sr: font('.ha-seal-ref'),
    },
  };
  clone.remove();
  return spec;
}
"""


def measure() -> None:
    """Re-measure the hero off the live site into brand_hero_layouts.json.
    Needs Playwright (pip install playwright) and Google Chrome."""
    import asyncio
    import json

    from playwright.async_api import async_playwright

    async def run() -> dict:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="chrome")
            layouts = {}
            for name, viewport in VIEWPORTS.items():
                # reduced motion opens the site on the finished audit, so
                # the count reads 250 and its pill is at its full width
                context = await browser.new_context(reduced_motion="reduce", **viewport)
                page = await context.new_page()
                await page.goto(SITE, wait_until="networkidle")
                await page.evaluate("document.fonts.ready")
                layouts[name] = await page.evaluate(MEASURE_JS)
                await context.close()
            await browser.close()
            return layouts

    LAYOUTS.write_text(
        json.dumps(asyncio.run(run()), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"measured {LAYOUTS.name}")


def main() -> None:
    import json
    import sys

    if "--measure" in sys.argv:
        measure()
    layouts = json.loads(LAYOUTS.read_text(encoding="utf-8"))
    files = FontFiles()
    OUT.mkdir(parents=True, exist_ok=True)
    art = {}
    for mode, theme in (("light", LIGHT), ("dark", DARK)):
        art[f"ifixai-logo-{mode}.svg"] = logo(mode)
        art[f"ifixai-masthead-{mode}.svg"] = masthead(mode, files)
        art[f"hero-audit-{mode}.svg"] = hero([theme], "desktop", files, layouts)
        art[f"button-site-{mode}.svg"] = button(
            [theme], files, "Visit ifixai.ai", primary=True
        )
        art[f"button-pro-{mode}.svg"] = button(
            [theme], files, "iFixAi Pro", primary=False
        )
    # the phone files carry both themes; see the README's <picture> sources
    art["hero-audit-phone.svg"] = hero([LIGHT, DARK], "phone", files, layouts)
    art["button-site.svg"] = button(
        [LIGHT, DARK], files, "Visit ifixai.ai", primary=True
    )
    art["button-pro.svg"] = button([LIGHT, DARK], files, "iFixAi Pro", primary=False)
    for name, svg in art.items():
        (OUT / name).write_text(svg, encoding="utf-8")
        print(f"{name:28} {len(svg.encode()) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
