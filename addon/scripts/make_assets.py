#!/usr/bin/env python3
"""Generate icon.png and logo.png for the Kilometerregistratie HA add-on.

Pure-Pillow vector-style drawing with 4x supersampling for crisp edges.
Motif: a speedometer / odometer gauge — a single glyph that reads clearly
at small sizes. Palette matches the app's dark UI theme.
"""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---- palette ----
NAVY = (15, 23, 42)        # #0f172a background
CARD = (30, 41, 59)        # #1e293b card
CYAN = (56, 189, 248)      # #38bdf8 accent
TEAL = (13, 148, 136)      # #0d9488 business
AMBER = (245, 158, 11)     # #f59e0b private/needle
TEXT = (226, 232, 240)     # #e2e8f0 light text
MUTED = (148, 163, 184)    # slate-400

SS = 4  # supersample factor


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(len(a)))


def vgradient(size, top, bottom):
    """Vertical gradient image."""
    w, h = size
    g = Image.new("RGB", (1, h))
    for y in range(h):
        g.putpixel((0, y), lerp(top, bottom, y / max(1, h - 1)))
    return g.resize((w, h))


def rounded_mask(size, radius):
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius=radius, fill=255)
    return m


def load_font(size, bold=True):
    candidates = [
        "/System/Library/Fonts/SFNSRounded.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def arc_gradient(draw, box, start_a, end_a, width, c_start, c_end, steps=120):
    """Draw an arc with a colour gradient along its sweep."""
    for i in range(steps):
        t0 = i / steps
        t1 = (i + 1) / steps
        a0 = start_a + (end_a - start_a) * t0
        a1 = start_a + (end_a - start_a) * t1
        col = lerp(c_start, c_end, (t0 + t1) / 2)
        draw.arc(box, a0, a1 + 0.6, fill=col, width=width)


def draw_gauge(draw, cx, cy, r, needle_frac=0.68, tick_color=TEXT,
               track_color=None, hub=True):
    """Draw a speedometer gauge glyph centred at (cx,cy) with radius r."""
    if track_color is None:
        track_color = lerp(NAVY, CARD, 0.5)
    # geometry: sweep from 150deg down to -150deg (i.e. 150 -> 390),
    # drawn in PIL angle space (0 = east, CW positive going down).
    start = 150
    end = 390  # 240deg sweep
    track_w = int(r * 0.17)
    box = [cx - r, cy - r, cx + r, cy + r]

    # background track (dim)
    draw.arc(box, start, end, fill=track_color, width=track_w)

    # active coloured arc: cyan -> teal, up to needle_frac of the sweep
    active_end = start + (end - start) * needle_frac
    arc_gradient(draw, box, start, active_end, track_w, CYAN, TEAL)

    # tick marks around the dial
    n = 9
    for i in range(n):
        a = math.radians(start + (end - start) * (i / (n - 1)))
        r_out = r - track_w * 0.5 - r * 0.06
        r_in = r_out - (r * 0.14 if i in (0, (n - 1) // 2, n - 1) else r * 0.08)
        x0 = cx + math.cos(a) * r_in
        y0 = cy + math.sin(a) * r_in
        x1 = cx + math.cos(a) * r_out
        y1 = cy + math.sin(a) * r_out
        lw = max(2, int(r * (0.045 if i in (0, (n - 1) // 2, n - 1) else 0.03)))
        draw.line([x0, y0, x1, y1], fill=tick_color, width=lw)

    # needle (amber) pointing to needle_frac
    na = math.radians(start + (end - start) * needle_frac)
    nlen = r * 0.62
    nx = cx + math.cos(na) * nlen
    ny = cy + math.sin(na) * nlen
    # tail opposite, short
    tx = cx - math.cos(na) * (r * 0.14)
    ty = cy - math.sin(na) * (r * 0.14)
    draw.line([tx, ty, nx, ny], fill=AMBER, width=max(3, int(r * 0.07)))

    if hub:
        hr = int(r * 0.11)
        draw.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=AMBER)
        hr2 = int(r * 0.05)
        draw.ellipse([cx - hr2, cy - hr2, cx + hr2, cy + hr2], fill=NAVY)


# ---------------------------------------------------------------------------
# ICON  (256 x 256, square, rounded dark tile with gauge glyph)
# ---------------------------------------------------------------------------
def make_icon(path, size=256):
    S = size * SS
    radius = int(S * 0.22)

    # base rounded tile with subtle vertical gradient
    bg = vgradient((S, S), lerp(CARD, NAVY, 0.15), NAVY).convert("RGBA")

    # inner glow / vignette-free; add a faint top sheen
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.ellipse([-S * 0.3, -S * 0.75, S * 1.3, S * 0.5],
               fill=(56, 189, 248, 22))
    sheen = sheen.filter(ImageFilter.GaussianBlur(S * 0.05))
    bg = Image.alpha_composite(bg, sheen)

    draw = ImageDraw.Draw(bg)

    cx, cy = S // 2, int(S * 0.52)
    r = int(S * 0.33)
    draw_gauge(draw, cx, cy, r, needle_frac=0.66)

    # mask to rounded square
    mask = rounded_mask((S, S), radius)
    # subtle 1px inner border
    bd = ImageDraw.Draw(bg)
    bd.rounded_rectangle([SS, SS, S - 1 - SS, S - 1 - SS], radius=radius,
                         outline=(56, 189, 248, 60), width=max(2, SS))

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)

    out = out.resize((size, size), Image.LANCZOS)
    out.save(path)
    return out.size


# ---------------------------------------------------------------------------
# LOGO  (500 x 200, dark rounded banner: gauge glyph + wordmark)
# ---------------------------------------------------------------------------
def fit_font(draw, text, max_w, start_px):
    """Return the largest font (<= start_px) whose text width fits max_w."""
    px = start_px
    while px > 8:
        f = load_font(px)
        b = draw.textbbox((0, 0), text, font=f)
        if (b[2] - b[0]) <= max_w:
            return f, b
        px -= 2
    f = load_font(8)
    return f, draw.textbbox((0, 0), text, font=f)


def make_logo(path, w=500, h=200):
    W, H = w * SS, h * SS
    radius = int(H * 0.16)

    bg = vgradient((W, H), lerp(CARD, NAVY, 0.1), NAVY).convert("RGBA")

    sheen = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.ellipse([-W * 0.1, -H * 1.1, W * 0.6, H * 0.5], fill=(56, 189, 248, 20))
    sheen = sheen.filter(ImageFilter.GaussianBlur(H * 0.08))
    bg = Image.alpha_composite(bg, sheen)

    draw = ImageDraw.Draw(bg)

    # gauge glyph on the left
    gr = int(H * 0.34)
    gcx = int(H * 0.52)
    gcy = int(H * 0.5)
    draw_gauge(draw, gcx, gcy, gr, needle_frac=0.66)

    # ---- wordmark, auto-fitted to the available width ----
    pad_right = int(W * 0.05)
    tx = gcx + gr + int(H * 0.22)
    avail = W - tx - pad_right

    name1, name2 = "Kilometer", "registratie"
    full = name1 + name2
    title_f, bb = fit_font(draw, full, avail, int(H * 0.26))
    w1 = draw.textbbox((0, 0), name1, font=title_f)[2]
    ascent, descent = title_f.getmetrics()
    th = ascent + descent

    sub = "Rittenregistratie \u00b7 zakelijk / priv\u00e9"
    sub_f, _ = fit_font(draw, sub, avail, int(H * 0.13))
    sa, sd2 = sub_f.getmetrics()
    sh = sa + sd2

    gap = int(H * 0.06)
    block_h = th + gap + sh
    ty = (H - block_h) // 2

    # draw two-tone title (light + cyan accent) on one baseline
    draw.text((tx, ty), name1, font=title_f, fill=TEXT)
    draw.text((tx + w1, ty), name2, font=title_f, fill=CYAN)

    # subtitle
    sy = ty + th + gap
    draw.text((tx, sy), sub, font=sub_f, fill=MUTED)

    mask = rounded_mask((W, H), radius)
    bd = ImageDraw.Draw(bg)
    bd.rounded_rectangle([SS, SS, W - 1 - SS, H - 1 - SS], radius=radius,
                         outline=(56, 189, 248, 50), width=max(2, SS))

    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    out = out.resize((w, h), Image.LANCZOS)
    out.save(path)
    return out.size


if __name__ == "__main__":
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else "."
    i = make_icon(f"{base}/icon.png", 256)
    l = make_logo(f"{base}/logo.png", 500, 200)
    print("icon.png", i)
    print("logo.png", l)
