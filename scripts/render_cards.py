#!/usr/bin/env python3
"""Render the profile README cards into cards/ as animated SVGs.

    python scripts/render_cards.py            # animated cards (what the Action commits)
    STATIC=1 python scripts/render_cards.py   # final frame only + preview.html to screenshot

Everything here works inside <img>: one inline <style> (CSS keyframes), SMIL
<animate*> elements, and fonts subset with pyftsubset and embedded as base64
WOFF data URIs. Under prefers-reduced-motion every animation is switched off and
the card shows its final frame — the same frame STATIC=1 renders.

Needs: fontTools (pyftsubset). Pillow only if assets/portrait.(jpg|png|webp) exists.
"""
import base64, calendar, json, os, re, shutil, subprocess, sys, tempfile
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "cards"
FONTS = ROOT / "fonts"
DATA = ROOT / "data" / "contributions.json"
STATIC = os.environ.get("STATIC", "") not in ("", "0", "false")

# ── Content ──────────────────────────────────────────────────────────────────
PROFILE = {
    "name": "Waqas Zafar",
    "eyebrow": "SOFTWARE ENGINEER · WEB & MOBILE",
    # one line about me — the part in [brackets] is drawn in the brand colour
    "line": "I build things that still work [after launch day].",
    "meta": ["Lahore, Pakistan", "Lab 23 Technology", "React · Next.js · Flutter"],
    "monogram": "WZ",            # shown in the ring when there's no portrait
}
TILES = [  # exactly three, rendered side by side at width 32.5%
    {"slug": "portfolio", "kicker": "01 / PORTFOLIO", "title": "waqaszafar.tech",
     "sub": "Work, experience & contact", "icon": "globe", "href": "https://waqaszafar.tech"},
    {"slug": "linkedin", "kicker": "02 / LINKEDIN", "title": "in/m-waqas-zafar",
     "sub": "Let's connect", "icon": "linkedin", "href": "https://www.linkedin.com/in/m-waqas-zafar"},
    {"slug": "email", "kicker": "03 / EMAIL", "title": "Say hello",
     "sub": "mwaqaszafar76@gmail.com", "icon": "mail", "href": "mailto:mwaqaszafar76@gmail.com"},
]

# ── Brand: the glow-green and deep blue of the GitHub avatar, on dark forest ──
ACCENT = "#7ee08f"     # avatar glow green
ACCENT2 = "#4e87b6"    # avatar cloak blue (secondary glow)
BG = "#040805"
SURFACE = "#0a110c"
BORDER = "#1d2b22"
FG = "#ffffff"
MUTED = "#9ca3af"
DIM = "#6b7280"
EASE = "cubic-bezier(.22,1,.36,1)"   # the site's easing curve

FONT_FILES = {  # key -> file. Manrope = --font-sans, Geist Mono = --font-mono
    "s4": "Manrope-400.woff", "s6": "Manrope-600.woff", "s7": "Manrope-700.woff",
    "m4": "GeistMono-400.woff", "m5": "GeistMono-500.woff",
}
FALLBACK = {"s": "Manrope,'Segoe UI',Helvetica,Arial,sans-serif",
            "m": "'Geist Mono',ui-monospace,SFMono-Regular,Menlo,monospace"}

# Page timeline: cards play top to bottom (seconds at which each card starts)
T_HERO, T_TILES, T_ACTIVITY, T_REPOS, T_REPO_STEP, T_GRAPH = 0.0, 0.55, 1.05, 1.6, 0.14, 2.2

# ── Fonts: metrics for layout, pyftsubset for embedding ──────────────────────
_metrics = {}


def font_metrics(key):
    if key not in _metrics:
        f = TTFont(FONTS / FONT_FILES[key])
        _metrics[key] = (f.getBestCmap(), f["hmtx"].metrics, f["head"].unitsPerEm)
    return _metrics[key]


def text_width(s, key, size, ls=0.0):
    cmap, hmtx, upm = font_metrics(key)
    w = sum(hmtx.get(cmap.get(ord(c), ".notdef"), (upm // 2, 0))[0] for c in s)
    return w * size / upm + ls * len(s)


def fit_size(s, key, size, max_w, ls=0.0, min_size=9):
    while size > min_size and text_width(s, key, size, ls) > max_w:
        size -= 0.5
    return size


def ellipsize(s, key, size, max_w, ls=0.0):
    if text_width(s, key, size, ls) <= max_w:
        return s
    while s and text_width(s + "…", key, size, ls) > max_w:
        s = s[:-1]
    return s.rstrip(" .,") + "…"


def wrap(s, key, size, max_w, lines):
    words, out, cur = s.split(), [], ""
    for i, w in enumerate(words):
        t = (cur + " " + w).strip()
        if text_width(t, key, size) <= max_w:
            cur = t
            continue
        if len(out) == lines - 1:
            out.append(ellipsize(" ".join([cur] + words[i:]), key, size, max_w))
            return out
        out.append(cur)
        cur = w
    if cur:
        out.append(cur)
    return out[:lines]


def subset_woff_b64(key, chars):
    """Subset a font to exactly `chars` with pyftsubset; return base64 WOFF."""
    chars = "".join(sorted(set(chars) | {" "}))
    exe = shutil.which("pyftsubset")
    cmd = [exe] if exe else [sys.executable, "-m", "fontTools.subset"]
    with tempfile.TemporaryDirectory() as tmp:
        txt, out = Path(tmp) / "chars.txt", Path(tmp) / "out.woff"
        txt.write_text(chars, encoding="utf-8")
        subprocess.run(cmd + [str(FONTS / FONT_FILES[key]), f"--text-file={txt}",
                              f"--output-file={out}", "--flavor=woff", "--layout-features=kern,liga",
                              "--no-hinting", "--desubroutinize", "--name-IDs=", "--drop-tables+=DSIG"],
                       check=True, capture_output=True)
        return base64.b64encode(out.read_bytes()).decode()


# ── Card builder ─────────────────────────────────────────────────────────────
KEYFRAMES = {
    "fu": "@keyframes fu{from{opacity:0;transform:translateY(14px)}}",
    "fi": "@keyframes fi{from{opacity:0}}",
    "fl": "@keyframes fl{from{opacity:0;transform:translateX(-18px)}}",
    "gy": "@keyframes gy{from{transform:scaleY(0)}}",
    "gx": "@keyframes gx{from{transform:scaleX(0)}}",
    "pop": "@keyframes pop{from{opacity:0;transform:scale(.2)}}",
    "draw": "@keyframes draw{from{stroke-dashoffset:1}}",
    "breathe": "@keyframes breathe{50%{opacity:.55}}",
}
ANIM_RULES = {
    "fu": f".fu{{animation:fu .8s {EASE} both}}",
    "fi": ".fi{animation:fi .9s ease-out both}",
    "fl": f".fl{{animation:fl .8s {EASE} both}}",
    "gy": f".gy{{transform-box:fill-box;transform-origin:50% 100%;animation:gy .9s {EASE} both}}",
    "gx": f".gx{{transform-box:fill-box;transform-origin:0 50%;animation:gx .8s {EASE} both}}",
    "pop": f".pop{{transform-box:fill-box;transform-origin:50% 50%;animation:pop .5s {EASE} both}}",
    "draw": f".draw{{stroke-dasharray:1;animation:draw 1.6s {EASE} both}}",
    "breathe": ".breathe{animation:breathe 6s ease-in-out infinite}",
}


class Card:
    def __init__(self, name, w, h, start, title):
        self.name, self.w, self.h, self.start, self.title = name, w, h, start, title
        self.body, self.defs, self.chars, self.used = [], [], {}, set()
        self.uid = re.sub(r"[^a-z0-9]", "", name.lower())[:10]

    # -- animation helpers. Every animated thing is a wrapper <g> with no transform
    #    attribute of its own, so CSS transforms never clobber layout transforms.
    def a(self, kind, t):
        """Attributes for a CSS-animated wrapper starting t seconds into this card."""
        if STATIC:
            return ""
        self.used.add(kind)
        return f' class="{kind}" style="animation-delay:{self.start + t:.2f}s"'

    def smil(self, moving, still=""):
        """SMIL content: `moving` plays normally; `still` is its reduced-motion /
        STATIC twin (the final frame). Reduced motion hides .m and shows .s."""
        if STATIC:
            return still
        self.used.add("_smil")
        return f'<g class="m">{moving}</g>' + (f'<g class="s">{still}</g>' if still else "")

    def begin(self, t):
        return f"{self.start + t:.2f}s"

    # -- primitives
    def text(self, x, y, s, key="s4", size=14, fill=FG, anchor="start", ls=0.0, extra=""):
        self.chars.setdefault(key, set()).update(s)
        lsa = f' letter-spacing="{ls:g}"' if ls else ""
        anc = f' text-anchor="{anchor}"' if anchor != "start" else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" class="{key}" font-size="{size:g}" fill="{fill}"'
                f'{anc}{lsa}{extra}>{escape(s)}</text>')

    def tspan(self, s, key, fill):
        self.chars.setdefault(key, set()).update(s)
        return f'<tspan class="{key}" fill="{fill}">{escape(s)}</tspan>'

    def add(self, *parts):
        self.body.extend(parts)

    # -- shared chrome
    def frame(self, glow=(0.18, 0.0), glow_r=0.75, grid=True):
        u = self.uid
        self.defs.append(
            f'<radialGradient id="g{u}" cx="{glow[0]}" cy="{glow[1]}" r="{glow_r}">'
            f'<stop offset="0" stop-color="{ACCENT}" stop-opacity=".28"/>'
            f'<stop offset=".55" stop-color="{ACCENT}" stop-opacity=".05"/>'
            f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>'
            f'<radialGradient id="b{u}" cx="{1 - glow[0]}" cy="{1 - glow[1]}" r="{glow_r}">'
            f'<stop offset="0" stop-color="{ACCENT2}" stop-opacity=".16"/>'
            f'<stop offset="1" stop-color="{ACCENT2}" stop-opacity="0"/></radialGradient>'
            f'<clipPath id="c{u}"><rect width="{self.w}" height="{self.h}" rx="18"/></clipPath>'
            f'<pattern id="p{u}" width="28" height="28" patternUnits="userSpaceOnUse">'
            f'<path d="M28 0H0V28" fill="none" stroke="#fff" stroke-opacity=".035"/></pattern>')
        self.add(f'<g clip-path="url(#c{u})"><rect width="{self.w}" height="{self.h}" fill="{BG}"/>',
                 f'<rect width="{self.w}" height="{self.h}" fill="url(#b{u})"/>',
                 f'<g{self.a("breathe", 0)}><rect width="{self.w}" height="{self.h}" fill="url(#g{u})"/></g>',
                 f'<rect width="{self.w}" height="{self.h}" fill="url(#p{u})"/>' if grid else "",
                 "</g>")
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="17.5" '
                 f'fill="none" stroke="{BORDER}"/>')

    def eyebrow(self, x, y, label, t=0.0):
        """Red rule + mono caps label, like the section labels on the site."""
        self.add(f'<g{self.a("gx", t)}><rect x="{x}" y="{y - 4.5}" width="26" height="1.5" fill="{ACCENT}"/></g>',
                 f'<g{self.a("fi", t + 0.15)}>{self.text(x + 38, y, label, "m5", 11, MUTED, ls=2.2)}</g>')

    # -- output
    def svg(self):
        faces = []
        for key, chars in sorted(self.chars.items()):
            b64 = subset_woff_b64(key, "".join(chars))
            faces.append(f"@font-face{{font-family:{key};src:url(data:font/woff;base64,{b64}) format('woff')}}")
        cls = " ".join(f".{k}{{font-family:{k},{FALLBACK[k[0]]};font-weight:{int(k[1]) * 100}}}"
                       for k in sorted(self.chars))
        css = faces + [cls]
        if not STATIC:
            css += [KEYFRAMES[k] + ANIM_RULES[k] for k in sorted(self.used - {"_smil"})]
            css.append(".s{display:none}")
            css.append("@media (prefers-reduced-motion:reduce){*{animation:none!important}"
                       ".m{display:none}.s{display:inline}}")
        style = "\n".join(css)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t{self.uid}">'
                f'<title id="t{self.uid}">{escape(self.title)}</title>\n<style>\n{style}\n</style>\n'
                f'<defs>{"".join(self.defs)}</defs>\n' + "\n".join(p for p in self.body if p) + "\n</svg>\n")

    def save(self):
        CARDS.mkdir(exist_ok=True)
        out = CARDS / f"{self.name}.svg"
        out.write_text(self.svg(), encoding="utf-8")
        return out


ICONS = {
    "globe": '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    "linkedin": '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M8 10.5v6M8 7.5v.01M12 16.5v-6m0 2.5a2.5 2.5 0 0 1 5 0v3.5"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="m3.5 7 8.5 6 8.5-6"/>',
    "arrow": '<path d="M7 17 17 7M9 7h8v8"/>',
    "star": '<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
    "fork": '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 7v1.5a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7M12 11.5V17"/>',
    "repo": '<path d="M5 19.5v-15A1.5 1.5 0 0 1 6.5 3H19v15H6.5A1.5 1.5 0 0 0 5 19.5a1.5 1.5 0 0 0 1.5 1.5H19"/>',
    "flame": '<path d="M12 21c-3.9 0-6.5-2.6-6.5-6.1 0-3.6 3-5.6 3.8-9.4 2.4 1.5 3.4 3.7 3.4 5.6 1-.5 1.8-1.6 2-2.9 2 1.6 3.8 4 3.8 6.7 0 3.5-2.6 6.1-6.5 6.1z"/>',
}


def icon(name, x, y, size=20, color=MUTED, sw=1.7):
    return (f'<g transform="translate({x:.1f} {y:.1f}) scale({size / 24:.4f})" fill="none" stroke="{color}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')


def fmt(n):
    return f"{n:,}"


def short_date(d):
    return f"{d.day} {calendar.month_abbr[d.month]}"


# ── Stats ────────────────────────────────────────────────────────────────────
def compute_stats(data):
    days = sorted(((date.fromisoformat(d["date"]), d["count"]) for d in data["days"]), key=lambda x: x[0])
    counts = [c for _, c in days]
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:      # today not over yet: count the streak up to yesterday
        i -= 1
    current = 0
    while i >= 0 and counts[i]:
        current, i = current + 1, i - 1
    busiest = max(days, key=lambda x: (x[1], x[0])) if days else (date.today(), 0)
    last = days[-1][0] if days else date.today()
    months, y, m = [], last.year, last.month
    for _ in range(12):
        months.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    months.reverse()
    per_month = OrderedDict((ym, 0) for ym in months)
    for d, c in days:
        if (d.year, d.month) in per_month:
            per_month[(d.year, d.month)] += c
    return {"days": days, "total": data.get("total", sum(counts)), "active": sum(1 for c in counts if c),
            "span": len(counts), "current": current, "longest": longest, "busiest": busiest,
            "per_month": per_month, "last": last}


# ── Cards ────────────────────────────────────────────────────────────────────
def find_portrait():
    for ext in ("jpg", "jpeg", "png", "webp"):
        p = ROOT / "assets" / f"portrait.{ext}"
        if p.exists():
            return p
    return None


def portrait_data_uri(path, px=320):
    from io import BytesIO
    from PIL import Image, ImageOps
    im = ImageOps.fit(ImageOps.exif_transpose(Image.open(path)).convert("RGB"), (px, px), centering=(0.5, 0.35))
    im = ImageOps.grayscale(im)          # black & white so it sits on the green palette
    buf = BytesIO()
    im.save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def card_hero():
    W, H = 840, 300
    c = Card("hero", W, H, T_HERO, f"{PROFILE['name']} — {PROFILE['eyebrow'].title()}")
    c.frame(glow=(0.82, 0.15), glow_r=0.7)
    x0 = 48
    c.eyebrow(x0, 74, PROFILE["eyebrow"], 0.0)

    # name + blinking cursor (SMIL)
    nsize = 58
    c.add(f'<g{c.a("fu", 0.15)}>{c.text(x0 - 2, 142, PROFILE["name"], "s7", nsize, FG, ls=-1.2)}</g>')
    cx = x0 + text_width(PROFILE["name"], "s7", nsize, -1.2) + 6
    cur = f'<rect x="{cx:.1f}" y="100" width="5" height="46" rx="1.5" fill="{ACCENT}"/>'
    c.add(f'<g{c.a("fi", 0.5)}>' + c.smil(
        f'<rect x="{cx:.1f}" y="100" width="5" height="46" rx="1.5" fill="{ACCENT}">'
        f'<animate attributeName="opacity" values="1;1;0;0;1" keyTimes="0;.45;.5;.95;1" dur="1.1s" '
        f'begin="{c.begin(0.5)}" repeatCount="indefinite"/></rect>', cur) + "</g>")

    # one line about me, the [bracketed] part in brand red
    m = re.match(r"(.*)\[(.*)\](.*)", PROFILE["line"]) or re.match(r"(.*)()()", PROFILE["line"])
    a_, b_, z_ = m.groups()
    spans = c.tspan(a_, "s6", "#d1d5db") + c.tspan(b_, "s6", ACCENT) + c.tspan(z_, "s6", "#d1d5db")
    c.add(f'<g{c.a("fu", 0.32)}><text x="{x0}" y="190" font-size="23" letter-spacing="-.3">{spans}</text></g>')

    # meta row with a live dot (SMIL pulse)
    y, x = 240, x0
    c.add(f'<g{c.a("fi", 0.55)}>')
    c.add(f'<circle cx="{x + 4}" cy="{y - 4}" r="3.5" fill="{ACCENT}"/>',
          c.smil(f'<circle cx="{x + 4}" cy="{y - 4}" r="3.5" fill="none" stroke="{ACCENT}">'
                 f'<animate attributeName="r" values="3.5;10" dur="2s" begin="{c.begin(0.9)}" repeatCount="indefinite"/>'
                 f'<animate attributeName="opacity" values=".9;0" dur="2s" begin="{c.begin(0.9)}" repeatCount="indefinite"/>'
                 f'</circle>'))
    x += 18
    limit = 712 - 92 - 34          # stop before the ring on the right
    for i, item in enumerate(PROFILE["meta"]):
        if x + (14 if i else 0) + text_width(item, "m4", 12, 0.2) > limit:
            break                  # drop items that would run into the ring
        if i:
            c.add(f'<rect x="{x:.1f}" y="{y - 10}" width="1" height="12" fill="{BORDER}"/>')
            x += 14
        c.add(c.text(x, y, item, "m4", 12, DIM, ls=0.2))
        x += text_width(item, "m4", 12, 0.2) + 14
    c.add("</g>")

    # ring on the right: portrait if assets/portrait.* exists, otherwise a monogram
    rx, ry, r = 712, 150, 92
    c.defs.append(f'<clipPath id="pc{c.uid}"><circle cx="{rx}" cy="{ry}" r="{r - 8}"/></clipPath>')
    portrait = find_portrait()
    if portrait:
        inner = (f'<image href="{portrait_data_uri(portrait)}" x="{rx - r + 8}" y="{ry - r + 8}" '
                 f'width="{2 * r - 16}" height="{2 * r - 16}" clip-path="url(#pc{c.uid})" preserveAspectRatio="xMidYMid slice"/>')
    else:
        inner = (f'<circle cx="{rx}" cy="{ry}" r="{r - 8}" fill="{SURFACE}"/>'
                 + c.text(rx, ry + 17, PROFILE["monogram"], "s7", 50, FG, "middle", -1.5))
    c.add(f'<g{c.a("pop", 0.25)}>{inner}</g>')
    # red ring draws itself on (CSS stroke-dashoffset with pathLength=1)
    c.add(f'<g transform="rotate(-90 {rx} {ry})"><circle cx="{rx}" cy="{ry}" r="{r}" fill="none" '
          f'stroke="{ACCENT}" stroke-width="2" pathLength="1"{c.a("draw", 0.3)}/></g>')
    dashed = (f'<circle cx="{rx}" cy="{ry}" r="{r + 14}" fill="none" stroke="{ACCENT}" stroke-opacity=".35" '
              f'stroke-dasharray="2 7" stroke-linecap="round"')
    c.add(f'<g{c.a("fi", 0.8)}>' + c.smil(
        dashed + f'><animateTransform attributeName="transform" type="rotate" from="0 {rx} {ry}" '
                 f'to="360 {rx} {ry}" dur="40s" begin="{c.begin(0.8)}" repeatCount="indefinite"/></circle>',
        dashed + "/>") + "</g>")

    # shimmer sweep across the card every 7s (SMIL) — decoration only, no still twin
    c.defs.append(f'<linearGradient id="sh{c.uid}" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
                  f'<stop offset=".5" stop-color="#fff" stop-opacity=".05"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>')
    c.add(f'<g clip-path="url(#c{c.uid})">' + c.smil(
        f'<rect x="-260" y="-60" width="200" height="{H + 120}" fill="url(#sh{c.uid})" transform="skewX(-20)">'
        f'<animateTransform attributeName="transform" type="translate" values="0 0;{W + 520} 0;{W + 520} 0" '
        f'keyTimes="0;.35;1" dur="7s" begin="{c.begin(1.2)}" repeatCount="indefinite" additive="sum"/></rect>') + "</g>")
    return c.save()


def card_tile(i, t):
    W, H, P = 272, 156, 22
    c = Card(f"tile-{t['slug']}", W, H, T_TILES + i * 0.13, f"{t['kicker'].split('/')[-1].strip().title()}: {t['title']}")
    c.frame(glow=(0.0, 0.0), glow_r=0.9)
    c.add(f'<g{c.a("pop", 0.0)}><rect x="{P}" y="{P}" width="38" height="38" rx="10" fill="{SURFACE}" stroke="{BORDER}"/>'
          f'{icon(t["icon"], P + 9, P + 9, 20, ACCENT)}</g>')
    arrow_still = (f'<circle cx="{W - P - 14}" cy="{P + 14}" r="14" fill="none" stroke="{BORDER}"/>'
                   + icon("arrow", W - P - 22, P + 6, 16, FG, 2))
    arrow_moving = (f'<circle cx="{W - P - 14}" cy="{P + 14}" r="14" fill="none" stroke="{BORDER}"/>'
                    f'<g>{icon("arrow", W - P - 22, P + 6, 16, FG, 2)}<animateTransform attributeName="transform" '
                    f'type="translate" values="0 0;0 0;2.5 -2.5;0 0" keyTimes="0;.7;.85;1" dur="3s" '
                    f'begin="{c.begin(0.6)}" repeatCount="indefinite"/></g>')
    c.add(f'<g{c.a("fi", 0.2)}>{c.smil(arrow_moving, arrow_still)}</g>')
    c.add(f'<g{c.a("fu", 0.12)}>{c.text(P, 94, t["kicker"], "m5", 10.5, ACCENT, ls=1.8)}</g>')
    ts = fit_size(t["title"], "s7", 21, W - 2 * P, -0.4)
    c.add(f'<g{c.a("fu", 0.2)}>{c.text(P, 121, t["title"], "s7", ts, FG, ls=-0.4)}</g>')
    ss = fit_size(t["sub"], "s4", 13, W - 2 * P)
    c.add(f'<g{c.a("fu", 0.28)}>{c.text(P, 141, t["sub"], "s4", ss, MUTED)}</g>')
    return c.save()


def card_activity(st):
    W, H, P = 840, 420, 44
    busy_d, busy_n = st["busiest"]
    c = Card("activity", W, H, T_ACTIVITY,
             f"{fmt(st['total'])} contributions in the last year; current streak {st['current']} days, "
             f"longest {st['longest']}, {st['active']} active days, busiest day {short_date(busy_d)} with {busy_n}")
    c.frame(glow=(0.1, 0.0), glow_r=0.8)
    c.eyebrow(P, 56, "CONTRIBUTIONS · LAST 12 MONTHS", 0.0)
    c.add(f'<g{c.a("fi", 0.1)}>{c.text(W - P, 56, "UPDATED " + short_date(st["last"]).upper() + " " + str(st["last"].year), "m4", 10.5, DIM, "end", 1.5)}</g>')
    tot = fmt(st["total"])
    c.add(f'<g{c.a("fu", 0.1)}>{c.text(P - 2, 106, tot, "s7", 44, FG, ls=-1.5)}'
          f'{c.text(P + text_width(tot, "s7", 44, -1.5) + 10, 106, "contributions", "s6", 16, MUTED)}</g>')

    # four stat tiles
    stats = [("CURRENT STREAK", f"{st['current']}", "days" if st["current"] != 1 else "day", True),
             ("LONGEST STREAK", f"{st['longest']}", "days" if st["longest"] != 1 else "day", False),
             ("ACTIVE DAYS", f"{st['active']}", f"of {st['span']}", False),
             ("BUSIEST DAY", f"{busy_n}", f"on {short_date(busy_d)}", False)]
    gap, ty, th = 12, 128, 78
    tw = (W - 2 * P - 3 * gap) / 4
    for i, (label, val, unit, flame) in enumerate(stats):
        x = P + i * (tw + gap)
        parts = [f'<rect x="{x:.1f}" y="{ty}" width="{tw:.1f}" height="{th}" rx="12" fill="{SURFACE}" stroke="{BORDER}"/>',
                 c.text(x + 16, ty + 26, label, "m5", 10, DIM, ls=1.6),
                 c.text(x + 15, ty + 60, val, "s7", 28, ACCENT if flame else FG, ls=-0.8),
                 c.text(x + 15 + text_width(val, "s7", 28, -0.8) + 7, ty + 60, unit, "s6", 13, MUTED)]
        if flame and st["current"]:
            parts.append(icon("flame", x + tw - 34, ty + 14, 18, ACCENT, 1.8))
        c.add(f'<g{c.a("fu", 0.2 + i * 0.08)}>{"".join(parts)}</g>')

    # monthly bar chart
    top, base = 252, 372
    ch = base - top
    vals = list(st["per_month"].values())
    keys = list(st["per_month"].keys())
    vmax = max(vals) or 1
    nice = next(v for v in [5, 10, 20, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 5000, 10**9] if v >= vmax)
    for f in (0.5, 1.0):
        gy_ = base - ch * f
        c.add(f'<g{c.a("fi", 0.5)}><path d="M{P} {gy_:.1f}H{W - P}" stroke="#fff" stroke-opacity=".06" stroke-dasharray="3 5"/>'
              f'{c.text(W - P, gy_ - 5, fmt(int(nice * f)), "m4", 9.5, DIM, "end")}</g>')
    c.add(f'<path d="M{P} {base}H{W - P}" stroke="{BORDER}"/>')
    c.defs.append(f'<linearGradient id="bar{c.uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{ACCENT}"/>'
                  f'<stop offset="1" stop-color="{ACCENT}" stop-opacity=".25"/></linearGradient>'
                  f'<linearGradient id="bd{c.uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{ACCENT}" stop-opacity=".55"/>'
                  f'<stop offset="1" stop-color="{ACCENT}" stop-opacity=".12"/></linearGradient>'
                  f'<filter id="glow{c.uid}" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="8"/></filter>')
    slot = (W - 2 * P - 40) / 12
    bw = min(34, slot * 0.58)
    imax = vals.index(vmax)
    for i, ((yy, mm), v) in enumerate(zip(keys, vals)):
        cx = P + slot * (i + 0.5)
        bh = max(2.0, ch * v / nice) if v else 2.0
        hot = i == imax and v
        fill = f"url(#bar{c.uid})" if hot else f"url(#bd{c.uid})"
        t = 0.55 + i * 0.06
        bar = f'<rect x="{cx - bw / 2:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="5" fill="{fill}"/>'
        if hot:
            bar = (f'<rect x="{cx - bw / 2:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="5" '
                   f'fill="{ACCENT}" opacity=".35" filter="url(#glow{c.uid})"/>') + bar
        c.add(f'<g{c.a("gy", t)}>{bar}</g>')
        c.add(f'<g{c.a("fi", t + 0.45)}>{c.text(cx, base - bh - 8, fmt(v), "m5", 10.5, ACCENT if hot else MUTED, "middle")}</g>')
        lbl = calendar.month_abbr[mm] + (f" '{str(yy)[2:]}" if mm == 1 or i == 0 else "")
        c.add(f'<g{c.a("fi", t)}>{c.text(cx, base + 22, lbl, "m4", 10.5, FG if hot else DIM, "middle")}</g>')
        if hot:  # pulsing marker over the busiest month (SMIL)
            my = base - bh - 26
            c.add(f'<g{c.a("fi", t + 0.6)}><circle cx="{cx:.1f}" cy="{my:.1f}" r="2.5" fill="{ACCENT}"/>' + c.smil(
                f'<circle cx="{cx:.1f}" cy="{my:.1f}" r="2.5" fill="none" stroke="{ACCENT}">'
                f'<animate attributeName="r" values="2.5;9" dur="1.8s" begin="{c.begin(t + 0.6)}" repeatCount="indefinite"/>'
                f'<animate attributeName="opacity" values="1;0" dur="1.8s" begin="{c.begin(t + 0.6)}" repeatCount="indefinite"/>'
                f'</circle>') + "</g>")
    return c.save()


def card_repo(i, r):
    W, H, P = 412, 176, 24
    c = Card(f"repo-{re.sub(r'[^A-Za-z0-9_.-]', '-', r['name'])}", W, H, T_REPOS + i * T_REPO_STEP,
             f"{r['name']}: {r.get('description') or 'repository'}")
    c.frame(glow=(1.0, 0.0), glow_r=0.9)
    c.add(f'<g{c.a("gy", 0.05)}><rect x="0" y="34" width="3" height="{H - 68}" rx="1.5" fill="{ACCENT}"/></g>')
    c.add(f'<g{c.a("fi", 0.0)}>{icon("repo", P, P + 1, 18, ACCENT, 1.8)}</g>')
    name = ellipsize(r["name"], "s7", 18, W - 2 * P - 70, -0.3)
    c.add(f'<g{c.a("fl", 0.05)}>{c.text(P + 28, P + 15, name, "s7", 18, FG, ls=-0.3)}</g>')
    c.add(f'<g{c.a("fi", 0.15)}>{c.text(W - P - 32, P + 15, f"{i + 1:02d}", "m5", 11, ACCENT, "end", 1.2)}'
          f'{icon("arrow", W - P - 16, P + 1, 16, MUTED, 2)}</g>')
    desc = r.get("description") or "No description yet."
    lines = wrap(desc, "s4", 13.5, W - 2 * P, 2)
    for j, ln in enumerate(lines):
        c.add(f'<g{c.a("fu", 0.15 + j * 0.06)}>{c.text(P, P + 50 + j * 21, ln, "s4", 13.5, MUTED if r.get("description") else DIM)}</g>')
    # footer: language · stars · forks
    y, x = H - P, P
    foot = []
    if r.get("language"):
        foot.append(f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{r.get("languageColor") or MUTED}"/>')
        foot.append(c.text(x + 16, y, r["language"], "m4", 12, MUTED))
        x += 16 + text_width(r["language"], "m4", 12) + 22
    for ic, n in (("star", r.get("stars", 0)), ("fork", r.get("forks", 0))):
        foot.append(icon(ic, x, y - 13, 16, MUTED, 1.8))
        foot.append(c.text(x + 21, y, fmt(n), "m4", 12, MUTED))
        x += 21 + text_width(fmt(n), "m4", 12) + 22
    c.add(f'<path d="M{P} {y - 26}H{W - P}" stroke="{BORDER}"/>', f'<g{c.a("fi", 0.3)}>{"".join(foot)}</g>')
    return c.save()


def card_graph(st, n_repos):
    W, H = 840, 232
    c = Card("graph", W, H, T_GRAPH + max(0, n_repos - 2) * T_REPO_STEP / 2,
             f"Contribution graph: {fmt(st['total'])} contributions in the last year")
    c.frame(glow=(0.95, 1.0), glow_r=0.8)
    c.eyebrow(44, 50, "CONTRIBUTION GRAPH", 0.0)
    c.add(f'<g{c.a("fi", 0.1)}>{c.text(W - 44, 50, fmt(st["total"]) + " IN THE LAST YEAR", "m4", 10.5, DIM, "end", 1.5)}</g>')

    days = st["days"][-371:]
    first = days[0][0]
    lead = (first.weekday() + 1) % 7            # rows run Sunday..Saturday like GitHub
    cell, gap = 11, 3
    step = cell + gap
    ncols = (lead + len(days) + 6) // 7
    x0 = 44 + 30 + (W - 88 - 30 - ncols * step + gap) / 2
    y0 = 92
    nz = sorted(cnt for _, cnt in days if cnt)
    q = [nz[int(len(nz) * f)] for f in (0.25, 0.5, 0.75)] if nz else [1, 2, 3]

    def level(n):
        return 0 if n == 0 else 1 + sum(n > t for t in q)
    shades = ["#141f18", ACCENT + "4d", ACCENT + "80", ACCENT + "bf", ACCENT]
    for r_, lbl in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.add(f'<g{c.a("fi", 0.1)}>{c.text(x0 - 10, y0 + r_ * step + 9, lbl, "m4", 9.5, DIM, "end")}</g>')
    seen = None
    for k, (d, n) in enumerate(days):
        col, row = divmod(k + lead, 7)
        x, y = x0 + col * step, y0 + row * step
        if d.day <= 7 and row == 0 or (k == 0):
            if seen != d.month and col < ncols - 2:
                c.add(f'<g{c.a("fi", 0.1 + col * 0.012)}>{c.text(x, y0 - 10, calendar.month_abbr[d.month], "m4", 9.5, DIM)}</g>')
                seen = d.month
        rect = f'<rect x="{x:.1f}" y="{y}" width="{cell}" height="{cell}" rx="2.5" fill="{shades[level(n)]}"/>'
        c.add(f'<g{c.a("pop", 0.15 + col * 0.014 + row * 0.03)}>{rect}</g>' if not STATIC else rect)
    # today marker (SMIL)
    k = len(days) - 1
    col, row = divmod(k + lead, 7)
    tx, ty = x0 + col * step + cell / 2, y0 + row * step + cell / 2
    c.add(f'<g{c.a("fi", 0.15 + col * 0.014 + 0.3)}>' + c.smil(
        f'<rect x="{tx - 8.5:.1f}" y="{ty - 8.5:.1f}" width="17" height="17" rx="4.5" fill="none" stroke="{ACCENT}">'
        f'<animate attributeName="opacity" values="0;1;0" dur="2.2s" begin="{c.begin(1.2)}" repeatCount="indefinite"/></rect>') + "</g>")
    # footer
    fy = y0 + 7 * step + 28
    bd, bn = st["busiest"]
    c.add(f'<g{c.a("fi", 1.0)}>{c.text(x0, fy, f"Busiest day: {bn} on {short_date(bd)} {bd.year}", "m4", 10.5, MUTED)}</g>')
    lx = W - 44 - 5 * step - 34
    leg = [c.text(lx - 8, fy, "Less", "m4", 10, DIM, "end")]
    for i, s in enumerate(shades):
        leg.append(f'<rect x="{lx + i * step}" y="{fy - 10}" width="{cell}" height="{cell}" rx="2.5" fill="{s}"/>')
    leg.append(c.text(lx + 5 * step + 6, fy, "More", "m4", 10, DIM))
    c.add(f'<g{c.a("fi", 1.0)}>{"".join(leg)}</g>')
    return c.save()


# ── README preview (STATIC=1) ────────────────────────────────────────────────
def write_preview(tiles, repos):
    row = lambda imgs, w: "".join(f'<img src="cards/{p.name}" width="{w}">' for p in imgs)
    pairs = "".join(f'<p>{row(repos[i:i + 2], "49%")}</p>' for i in range(0, len(repos), 2))
    (ROOT / "preview.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Profile cards preview</title>"
        "<style>body{background:#0d1117;margin:0;padding:32px 16px}main{max-width:838px;margin:auto}"
        "p{margin:0 0 10px;display:flex;justify-content:space-between}img{display:block}</style><main>"
        f'<p>{row([CARDS / "hero.svg"], "100%")}</p><p>{row(tiles, "32.5%")}</p>'
        f'<p>{row([CARDS / "activity.svg"], "100%")}</p>{pairs}<p>{row([CARDS / "graph.svg"], "100%")}</p></main>')


def update_readme(repos, cards):
    """Rewrite the block between <!-- repos:start --> and <!-- repos:end --> so the
    README always links one card per featured repo, two per row."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    rows = []
    for i in range(0, len(repos), 2):
        cells = " ".join(f'<a href="{r["url"]}"><img src="cards/{p.name}" width="49%" alt="{escape(r["name"])}"></a>'
                         for r, p in zip(repos[i:i + 2], cards[i:i + 2]))
        rows.append(f'<p align="center">\n  {cells}\n</p>')
    block = "<!-- repos:start -->\n" + "\n".join(rows) + "\n<!-- repos:end -->"
    text = readme.read_text(encoding="utf-8")
    new = re.sub(r"<!-- repos:start -->.*?<!-- repos:end -->", lambda _: block, text, flags=re.S)
    if new != text:
        readme.write_text(new, encoding="utf-8")


def main():
    data = json.loads(DATA.read_text())
    st = compute_stats(data)
    repos = data.get("repos", [])
    for old in CARDS.glob("repo-*.svg"):   # drop cards for repos no longer featured
        old.unlink()
    out = [card_hero()]
    tiles = [card_tile(i, t) for i, t in enumerate(TILES)]
    out += tiles
    out.append(card_activity(st))
    repo_cards = [card_repo(i, r) for i, r in enumerate(repos)]
    out += repo_cards
    out.append(card_graph(st, len(repos)))
    update_readme(repos, repo_cards)
    for p in out:
        print(f"{p.relative_to(ROOT)}  {p.stat().st_size / 1024:.1f} KB")
    if STATIC:
        write_preview(tiles, repo_cards)
        print("STATIC=1: final frames written; open preview.html to screenshot")


if __name__ == "__main__":
    main()
