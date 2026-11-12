#!/usr/bin/env python3
"""Render the profile README cards into cards/ as animated SVGs.

    python scripts/render_cards.py            # animated cards (what the Action commits)
    STATIC=1 python scripts/render_cards.py   # final frame only + preview.html to screenshot

Everything here works inside <img>: one inline <style> (CSS keyframes, plus SMIL
<animate> if a card uses it), and fonts subset with pyftsubset and embedded as
base64 WOFF data URIs. Under prefers-reduced-motion every animation is switched
off and the card shows its final frame, the same frame STATIC=1 renders.

Design rules: flat surfaces, one typeface family, sentence-case labels, and the
accent colour only on data. Motion is a single short entrance per element.

Needs: fontTools (pyftsubset). Pillow only if assets/portrait.(jpg|png|webp) exists.
"""
import base64, calendar, json, os, re, shutil, subprocess, sys, tempfile
from collections import OrderedDict
from datetime import date
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
    # one line about me; the part in [brackets] is set in full white
    "line": "I build web and mobile apps that [still work after launch day].",
    "meta": ["Software engineer", "Lab 23 Technology", "Lahore, Pakistan"],
}
TILES = [  # exactly three, rendered side by side at width 32.5%
    {"slug": "portfolio", "label": "Portfolio", "title": "waqaszafar.tech", "href": "https://waqaszafar.tech"},
    {"slug": "linkedin", "label": "LinkedIn", "title": "m-waqas-zafar", "href": "https://www.linkedin.com/in/m-waqas-zafar"},
    {"slug": "email", "label": "Email", "title": "mwaqaszafar76@gmail.com", "href": "mailto:mwaqaszafar76@gmail.com"},
]
WORK = [  # from the Experience section on waqaszafar.tech, newest first
    {"role": "Software Engineer", "org": "Lab 23 Technology", "period": "Oct 2025 – Present"},
    {"role": "Frontend Engineer Intern", "org": "Lab 23 Technology", "period": "Jul 2025 – Oct 2025"},
    {"role": "App Developer Intern", "org": "Social Swirl", "period": "Aug 2024 – Oct 2024"},
]
WORK_ORGS = [  # GitHub orgs shown under the roles; logos are fetched into assets/orgs/
    {"login": "lab23-sites", "name": "Lab 23 Sites"},
    {"login": "Lynx-System", "name": "Lynx Systems"},
    {"login": "Cooksbook", "name": "Cooksbook"},
    {"login": "Milestone-Payment-App", "name": "Milestone Payment App"},
    {"login": "Carbon-Credit-Team", "name": "Carbon Credit Team"},
    {"login": "Global-Asset-Xchange", "name": "Global Asset Xchange"},
]
STACK = [
    ("Web", ["React", "Next.js", "TypeScript", "JavaScript", "Tailwind CSS"]),
    ("Mobile", ["Flutter", "Dart", "React Native", "Expo"]),
    ("Backend", ["Node.js", "Express", "REST APIs", "Firebase", "MongoDB", "MySQL"]),
    ("Tools", ["Git", "GitHub Actions", "AWS", "Figma"]),
]

# ── Palette: avatar green for data only; everything else is neutral ──────────
ACCENT = "#7ee08f"
BG = "#0f1411"
BORDER = "#222b25"
FG = "#eef2ef"
MUTED = "#9aa49d"
DIM = "#6d7770"
EMPTY = "#1a211c"
EASE = "cubic-bezier(.22,1,.36,1)"

FONT_FILES = {"s4": "Manrope-400.woff", "s6": "Manrope-600.woff", "s7": "Manrope-700.woff"}
FALLBACK = "Manrope,'Segoe UI',Helvetica,Arial,sans-serif"

# Page timeline: cards play top to bottom (seconds at which each card starts)
T_HERO, T_TILES, T_WORK, T_STACK, T_ACTIVITY, T_REPOS, T_REPO_STEP, T_GRAPH = \
    0.0, 0.35, 0.55, 0.75, 0.95, 1.2, 0.08, 1.5

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


def ellipsize(s, key, size, max_w):
    if text_width(s, key, size) <= max_w:
        return s
    while s and text_width(s + "…", key, size) > max_w:
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
                              f"--output-file={out}", "--flavor=woff", "--layout-features=kern,liga,tnum",
                              "--no-hinting", "--desubroutinize", "--name-IDs=", "--drop-tables+=DSIG"],
                       check=True, capture_output=True)
        return base64.b64encode(out.read_bytes()).decode()


# ── Card builder ─────────────────────────────────────────────────────────────
ANIM = {  # name: (keyframes, rule)
    "rise": ("@keyframes rise{from{opacity:0;transform:translateY(8px)}}",
             f".rise{{animation:rise .6s {EASE} both}}"),
    "fade": ("@keyframes fade{from{opacity:0}}", ".fade{animation:fade .5s ease-out both}"),
    "grow": ("@keyframes grow{from{transform:scaleY(0)}}",
             f".grow{{transform-box:fill-box;transform-origin:50% 100%;animation:grow .7s {EASE} both}}"),
}


class Card:
    def __init__(self, name, w, h, start, title):
        self.name, self.w, self.h, self.start, self.title = name, w, h, start, title
        self.body, self.defs, self.chars, self.used = [], [], {}, set()
        self.uid = re.sub(r"[^a-z0-9]", "", name.lower())[:10]

    def a(self, kind, t):
        """Attributes for a CSS-animated wrapper <g> starting t seconds into this card.
        Wrappers never carry a transform attribute, so CSS never clobbers layout."""
        if STATIC:
            return ""
        self.used.add(kind)
        return f' class="{kind}" style="animation-delay:{self.start + t:.2f}s"'

    def smil(self, moving, still=""):
        """SMIL content plus its reduced-motion/STATIC twin (the final frame)."""
        if STATIC:
            return still
        self.used.add("_smil")
        return f'<g class="m">{moving}</g>' + (f'<g class="s">{still}</g>' if still else "")

    def text(self, x, y, s, key="s4", size=14, fill=FG, anchor="start", ls=0.0):
        self.chars.setdefault(key, set()).update(s)
        lsa = f' letter-spacing="{ls:g}"' if ls else ""
        anc = f' text-anchor="{anchor}"' if anchor != "start" else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" class="{key}" font-size="{size:g}" fill="{fill}"'
                f'{anc}{lsa}>{escape(s)}</text>')

    def tspan(self, s, key, fill):
        self.chars.setdefault(key, set()).update(s)
        return f'<tspan class="{key}" fill="{fill}">{escape(s)}</tspan>'

    def add(self, *parts):
        self.body.extend(p for p in parts if p)

    def frame(self):
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="10" '
                 f'fill="{BG}" stroke="{BORDER}"/>')

    def svg(self):
        css = []
        for key, chars in sorted(self.chars.items()):
            b64 = subset_woff_b64(key, "".join(chars))
            css.append(f"@font-face{{font-family:{key};src:url(data:font/woff;base64,{b64}) format('woff')}}")
        css += [f".{k}{{font-family:{k},{FALLBACK};font-weight:{int(k[1]) * 100};"
                f"font-feature-settings:'tnum'}}" for k in sorted(self.chars)]
        if not STATIC:
            css += [kf + rule for k, (kf, rule) in sorted(ANIM.items()) if k in self.used]
            if "_smil" in self.used:
                css.append(".s{display:none}")
            css.append("@media (prefers-reduced-motion:reduce){*{animation:none!important}"
                       ".m{display:none}.s{display:inline}}")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t{self.uid}">'
                f'<title id="t{self.uid}">{escape(self.title)}</title>\n<style>\n' + "\n".join(css) +
                f'\n</style>\n<defs>{"".join(self.defs)}</defs>\n' + "\n".join(self.body) + "\n</svg>\n")

    def save(self):
        CARDS.mkdir(exist_ok=True)
        out = CARDS / f"{self.name}.svg"
        out.write_text(self.svg(), encoding="utf-8")
        return out


def star(x, y, size, color):
    s = size / 24
    return (f'<path transform="translate({x:.1f} {y:.1f}) scale({s:.3f})" fill="{color}" '
            f'd="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>')


def arrow(x, y, color):
    return (f'<path d="M{x:.1f} {y + 8:.1f}l8-8M{x + 2:.1f} {y:.1f}h6v6" fill="none" stroke="{color}" '
            f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>')


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
    if i >= 0 and counts[i] == 0:      # today isn't over: count the streak up to yesterday
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
    per_month = OrderedDict((ym, 0) for ym in reversed(months))
    for d, c in days:
        if (d.year, d.month) in per_month:
            per_month[(d.year, d.month)] += c
    return {"days": days, "total": data.get("total", sum(counts)), "active": sum(1 for c in counts if c),
            "current": current, "longest": longest, "busiest": busiest, "per_month": per_month, "last": last}


# ── Cards ────────────────────────────────────────────────────────────────────
def find_portrait():
    """assets/portrait.* if you add one, otherwise the GitHub avatar fetch_data saved."""
    for stem in ("portrait", "avatar"):
        for ext in ("jpg", "jpeg", "png", "webp"):
            p = ROOT / "assets" / f"{stem}.{ext}"
            if p.exists():
                return p
    return None


def image_data_uri(path, px):
    from io import BytesIO
    from PIL import Image, ImageOps
    im = ImageOps.fit(Image.open(path).convert("RGB"), (px, px))
    buf = BytesIO()
    im.save(buf, "JPEG", quality=86, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def card_hero():
    W, H, P = 840, 200, 40
    c = Card("hero", W, H, T_HERO, f"{PROFILE['name']}. {PROFILE['line'].replace('[', '').replace(']', '')}")
    c.frame()
    photo = find_portrait()
    s = 112                                   # photo diameter
    x = P + (s + 28 if photo else 0)          # text column starts after the photo
    if photo:
        cy = H / 2
        c.defs.append(f'<clipPath id="pc{c.uid}"><circle cx="{P + s / 2}" cy="{cy}" r="{s / 2}"/></clipPath>')
        c.add(f'<g{c.a("fade", 0)}><image href="{image_data_uri(photo, 224)}" x="{P}" y="{cy - s / 2}" '
              f'width="{s}" height="{s}" clip-path="url(#pc{c.uid})"/>'
              f'<circle cx="{P + s / 2}" cy="{cy}" r="{s / 2 - .5}" fill="none" stroke="{BORDER}"/></g>')
    text_w = W - P - x
    c.add(f'<g{c.a("rise", 0.05)}>{c.text(x - 1, 80, PROFILE["name"], "s7", 40, FG, ls=-0.8)}</g>')
    m = re.match(r"(.*)\[(.*)\](.*)", PROFILE["line"]) or re.match(r"(.*)()()", PROFILE["line"])
    a_, b_, z_ = m.groups()
    size = fit_size(a_ + b_ + z_, "s4", 19, text_w)
    spans = c.tspan(a_, "s4", MUTED) + c.tspan(b_, "s4", FG) + c.tspan(z_, "s4", MUTED)
    c.add(f'<g{c.a("rise", 0.12)}><text x="{x}" y="118" font-size="{size:g}">{spans}</text></g>')
    meta = "  ·  ".join(PROFILE["meta"])
    c.add(f'<g{c.a("fade", 0.24)}>{c.text(x, 156, meta, "s4", 14, DIM)}</g>')
    return c.save()


def card_work():
    W, P = 840, 40
    rows = len(WORK)
    org_rows = (len(WORK_ORGS) + 2) // 3
    H = 70 + rows * 44 + (40 + org_rows * 40 if WORK_ORGS else 0) + 14
    c = Card("work", W, H, T_WORK, "Work history: " + "; ".join(
        f"{w['role']} at {w['org']}, {w['period']}" for w in WORK))
    c.frame()
    c.add(f'<g{c.a("fade", 0)}>{c.text(P, 46, "Work", "s6", 15, FG)}</g>')
    y = 90
    for i, w in enumerate(WORK):
        row = (c.text(P, y, w["role"], "s6", 15, FG)
               + c.text(P + text_width(w["role"], "s6", 15) + 10, y, w["org"], "s4", 15, MUTED)
               + c.text(W - P, y, w["period"], "s4", 13, DIM, "end"))
        c.add(f'<g{c.a("rise", 0.06 + i * 0.06)}>{row}</g>')
        if i < rows - 1:
            c.add(f'<path d="M{P} {y + 20.5}H{W - P}" stroke="{BORDER}"/>')
        y += 44
    if WORK_ORGS:
        y += 8
        c.add(f'<path d="M{P} {y - 22.5}H{W - P}" stroke="{BORDER}"/>')
        c.add(f'<g{c.a("fade", 0.3)}>{c.text(P, y + 4, "Teams on GitHub", "s4", 13, DIM)}</g>')
        col = (W - 2 * P) / 3
        for i, org in enumerate(WORK_ORGS):
            ox, oy = P + (i % 3) * col, y + 22 + (i // 3) * 40
            logo = ROOT / "assets" / "orgs" / f"{org['login']}.png"
            if logo.exists():
                c.defs.append(f'<clipPath id="o{i}{c.uid}"><rect x="{ox}" y="{oy}" width="24" height="24" rx="6"/></clipPath>')
                mark = (f'<image href="{image_data_uri(logo, 48)}" x="{ox}" y="{oy}" width="24" height="24" '
                        f'clip-path="url(#o{i}{c.uid})"/>')
            else:  # no logo yet: a quiet initial
                mark = (f'<rect x="{ox}" y="{oy}" width="24" height="24" rx="6" fill="{EMPTY}" stroke="{BORDER}"/>'
                        + c.text(ox + 12, oy + 16.5, org["name"][0], "s6", 12, MUTED, "middle"))
            name = ellipsize(org["name"], "s4", 14, col - 44)
            c.add(f'<g{c.a("rise", 0.36 + i * 0.04)}>{mark}{c.text(ox + 34, oy + 17, name, "s4", 14, FG)}</g>')
    return c.save()


def card_stack():
    W, P = 840, 40
    H = 70 + len(STACK) * 42 + 8
    c = Card("stack", W, H, T_STACK, "Stack: " + "; ".join(f"{g}: {', '.join(items)}" for g, items in STACK))
    c.frame()
    c.add(f'<g{c.a("fade", 0)}>{c.text(P, 46, "Stack", "s6", 15, FG)}</g>')
    y = 72
    for i, (group, items) in enumerate(STACK):
        parts = [c.text(P, y + 19, group, "s4", 13, DIM)]
        x = P + 110
        for it in items:
            w = text_width(it, "s4", 13.5) + 24
            parts.append(f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="28" rx="6" fill="none" stroke="{BORDER}"/>'
                         + c.text(x + 12, y + 19, it, "s4", 13.5, FG))
            x += w + 8
        c.add(f'<g{c.a("rise", 0.06 + i * 0.06)}>{"".join(parts)}</g>')
        y += 42
    return c.save()


def card_tile(i, t):
    W, H, P = 272, 96, 20
    c = Card(f"tile-{t['slug']}", W, H, T_TILES + i * 0.06, f"{t['label']}: {t['title']}")
    c.frame()
    c.add(f'<g{c.a("fade", 0)}>{c.text(P, 38, t["label"], "s4", 13, DIM)}{arrow(W - P - 9, 28, DIM)}</g>')
    size = fit_size(t["title"], "s6", 17, W - 2 * P)
    c.add(f'<g{c.a("rise", 0.05)}>{c.text(P, 68, t["title"], "s6", size, FG)}</g>')
    return c.save()


def card_activity(st):
    W, H, P = 840, 316, 40
    bd, bn = st["busiest"]
    c = Card("activity", W, H, T_ACTIVITY,
             f"{fmt(st['total'])} contributions in the last year. Current streak {st['current']} days, "
             f"longest {st['longest']}. {st['active']} active days. Busiest day {short_date(bd)} with {bn}.")
    c.frame()
    c.add(f'<g{c.a("fade", 0)}>{c.text(P, 46, "Activity", "s6", 15, FG)}'
          f'{c.text(W - P, 46, f"Last 12 months, updated {short_date(st["last"])}", "s4", 13, DIM, "end")}</g>')

    days = lambda n: f"{n} day" + ("" if n == 1 else "s")
    stats = [(fmt(st["total"]), "contributions"),
             (days(st["current"]), f"current streak, best {st['longest']}"),
             (fmt(st["active"]), "active days"),
             (short_date(bd), f"busiest day, {bn} contributions")]
    col = (W - 2 * P) / 4
    for i, (val, label) in enumerate(stats):
        x = P + i * col
        c.add(f'<g{c.a("rise", 0.06 + i * 0.05)}>{c.text(x, 104, val, "s7", 26, FG, ls=-0.4)}'
              f'{c.text(x, 128, label, "s4", 13, MUTED)}</g>')

    top, base = 168, 270
    vals, keys = list(st["per_month"].values()), list(st["per_month"].keys())
    vmax = max(vals) or 1
    slot = (W - 2 * P) / 12
    bw = slot - 14
    c.add(f'<path d="M{P} {base + .5}H{W - P}" stroke="{BORDER}"/>')
    for i, ((yy, mm), v) in enumerate(zip(keys, vals)):
        x = P + slot * i + 7
        bh = max(2.0, (base - top) * v / vmax) if v else 0
        t = 0.3 + i * 0.03
        if bh:
            c.add(f'<g{c.a("grow", t)}><rect x="{x:.1f}" y="{base - bh:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                  f'rx="3" fill="{ACCENT}" fill-opacity="{1 if v == vmax else .45}"/></g>')
        c.add(f'<g{c.a("fade", t + 0.3)}>{c.text(x + bw / 2, base - bh - 7, fmt(v), "s4", 11, MUTED, "middle")}</g>')
        lbl = calendar.month_abbr[mm] + (f" {yy}" if mm == 1 or i == 0 else "")
        c.add(c.text(x + bw / 2, base + 22, lbl, "s4", 11.5, DIM, "middle"))
    return c.save()


def month_year(iso):
    return f"{calendar.month_abbr[int(iso[5:7])]} {iso[:4]}" if iso else ""


def card_repo(i, r):
    """A repo you committed to recently: yours or anyone's. Private repos show only
    owner/name, your commit count and month; no description, language or link."""
    W, H, P = 412, 124, 22
    owner, _, name = r["name"].partition("/")
    c = Card(f"repo-{re.sub(r'[^A-Za-z0-9_.-]', '-', r['name'])}", W, H, T_REPOS + i * T_REPO_STEP,
             f"{r['name']}: {fmt(r.get('commits', 0))} commits in the last year"
             + (" (private repository)" if r.get("private") else ""))
    c.frame()
    right = 64 if r.get("private") else 24
    o = ellipsize(owner + " / ", "s4", 15, (W - 2 * P - right) * 0.55)
    n = ellipsize(name, "s6", 15, W - 2 * P - right - text_width(o, "s4", 15))
    head = c.text(P, 38, o, "s4", 15, MUTED) + c.text(P + text_width(o, "s4", 15), 38, n, "s6", 15, FG)
    if r.get("private"):
        pw = text_width("Private", "s4", 11.5) + 16
        head += (f'<rect x="{W - P - pw:.1f}" y="24" width="{pw:.1f}" height="20" rx="10" fill="none" stroke="{BORDER}"/>'
                 + c.text(W - P - pw / 2, 38, "Private", "s4", 11.5, DIM, "middle"))
    else:
        head += arrow(W - P - 9, 28, DIM)
    c.add(f'<g{c.a("rise", 0)}>{head}</g>')
    commits = fmt(r.get("commits", 0))
    unit = "commit" if r.get("commits") == 1 else "commits"
    c.add(f'<g{c.a("rise", 0.06)}>{c.text(P, 74, commits, "s7", 20, FG, ls=-0.3)}'
          f'{c.text(P + text_width(commits, "s7", 20, -0.3) + 6, 74, unit + " in the last year", "s4", 13.5, MUTED)}</g>')
    y, x, foot = H - 22, P, []
    if r.get("last"):
        t = "Last commit " + month_year(r["last"])
        foot.append(c.text(x, y, t, "s4", 12.5, DIM))
        x += text_width(t, "s4", 12.5) + 18
    if r.get("language"):
        foot.append(f'<circle cx="{x + 4}" cy="{y - 4.5}" r="4" fill="{r.get("languageColor") or DIM}"/>')
        foot.append(c.text(x + 14, y, r["language"], "s4", 12.5, DIM))
        x += 14 + text_width(r["language"], "s4", 12.5) + 18
    if r.get("stars"):
        foot.append(star(x, y - 12, 14, DIM))
        foot.append(c.text(x + 18, y, fmt(r["stars"]), "s4", 12.5, DIM))
    c.add(f'<g{c.a("fade", 0.15)}>{"".join(foot)}</g>')
    return c.save()


def card_graph(st, n_repos):
    W, H, P = 840, 206, 40
    c = Card("graph", W, H, T_GRAPH + max(0, n_repos - 2) * T_REPO_STEP / 2,
             f"Contribution graph: {fmt(st['total'])} contributions in the last year")
    c.frame()
    c.add(f'<g{c.a("fade", 0)}>{c.text(P, 46, "Daily contributions", "s6", 15, FG)}</g>')

    days = st["days"][-371:]
    lead = (days[0][0].weekday() + 1) % 7        # rows run Sunday..Saturday like GitHub
    cell, gap = 11, 3
    step = cell + gap
    ncols = (lead + len(days) + 6) // 7
    x0 = P + 28 + (W - 2 * P - 28 - ncols * step + gap) / 2
    y0 = 92
    nz = sorted(n for _, n in days if n)
    q = [nz[int(len(nz) * f)] for f in (0.25, 0.5, 0.75)] if nz else [1, 2, 3]
    fills = [EMPTY] + [ACCENT] * 4
    opac = [1, .3, .5, .75, 1]
    for r_, lbl in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.add(c.text(x0 - 8, y0 + r_ * step + 9, lbl, "s4", 10.5, DIM, "end"))
    seen, cols = None, {}
    for k, (d, n) in enumerate(days):
        col, row = divmod(k + lead, 7)
        lv = 0 if n == 0 else 1 + sum(n > t for t in q)
        cols.setdefault(col, []).append(
            f'<rect x="{x0 + col * step:.1f}" y="{y0 + row * step}" width="{cell}" height="{cell}" rx="2" '
            f'fill="{fills[lv]}"' + (f' fill-opacity="{opac[lv]}"' if lv else "") + "/>")
        if (row == 0 and d.day <= 7 or k == 0) and seen != d.month and col < ncols - 2:
            c.add(c.text(x0 + col * step, y0 - 9, calendar.month_abbr[d.month], "s4", 10.5, DIM))
            seen = d.month
    for col, rects in cols.items():                  # one wrapper per week: a left-to-right wipe
        c.add(f'<g{c.a("fade", 0.1 + col * 0.008)}>{"".join(rects)}</g>')
    return c.save()


# ── README block + preview ───────────────────────────────────────────────────
def update_readme(repos, cards):
    """Rewrite the block between <!-- repos:start --> and <!-- repos:end --> so the
    README shows one card per recent repo, two per row. Public repos link to GitHub;
    private ones are a plain image (the link would 404 for visitors)."""
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    def cell(r, p):
        img = f'<img src="cards/{p.name}" width="49%" alt="{escape(r["name"])}">'
        return img if r.get("private") or not r.get("url") else f'<a href="{r["url"]}">{img}</a>'
    rows = [f'<p align="center">\n  ' + " ".join(cell(r, p) for r, p in zip(repos[i:i + 2], cards[i:i + 2])) + "\n</p>"
            for i in range(0, len(repos), 2)]
    block = "<!-- repos:start -->\n" + "\n".join(rows) + "\n<!-- repos:end -->"
    text = readme.read_text(encoding="utf-8")
    new = re.sub(r"<!-- repos:start -->.*?<!-- repos:end -->", lambda _: block, text, flags=re.S)
    if new != text:
        readme.write_text(new, encoding="utf-8")


def write_preview(tiles, repos):
    row = lambda imgs, w: "".join(f'<img src="cards/{p.name}" width="{w}">' for p in imgs)
    one = lambda n: f'<p>{row([CARDS / n], "100%")}</p>'
    pairs = "".join(f'<p>{row(repos[i:i + 2], "49%")}</p>' for i in range(0, len(repos), 2))
    (ROOT / "preview.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Profile cards preview</title>"
        "<style>body{background:#0d1117;margin:0;padding:32px 16px}main{max-width:838px;margin:auto}"
        "p{margin:0 0 10px;display:flex;justify-content:space-between}img{display:block}</style><main>"
        + one("hero.svg") + f'<p>{row(tiles, "32.5%")}</p>' + one("work.svg") + one("stack.svg")
        + one("activity.svg") + pairs + one("graph.svg") + "</main>")


def main():
    data = json.loads(DATA.read_text())
    st = compute_stats(data)
    repos = data.get("repos", [])
    for old in CARDS.glob("*.svg"):   # drop cards that are no longer rendered
        old.unlink()
    out = [card_hero()]
    tiles = [card_tile(i, t) for i, t in enumerate(TILES)]
    out += tiles + [card_work(), card_stack(), card_activity(st)]
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
