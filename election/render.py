"""Draw the NYT live-blog slide for a sideways barn screen.

Laid out as a portrait 1080x1920 page, then turned 90 degrees
counter-clockwise — the same "Rotate Left" the poster files get — so it
reads upright on a TV mounted on its side.
"""
import json
import os
import sys
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920
M = 70                       # side margin
BG, FG, MUTED, RED, RULE = (16, 16, 18), (242, 242, 240), (150, 154, 160), (214, 32, 39), (60, 60, 64)

GEORGIA = "/System/Library/Fonts/Supplemental/Georgia.ttf"
GEORGIA_B = "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"
HELV = "/System/Library/Fonts/HelveticaNeue.ttc"


def font(path, size, index=0):
    return ImageFont.truetype(path, size, index=index)


def wrap(draw, text, f, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=f) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def local_time(iso):
    t = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    return t.strftime("%-I:%M %p").lower().replace("am", "a.m.").replace("pm", "p.m.")


def meta(p):
    who = p.get("author", "")
    where = p.get("tagline", "")
    bits = [local_time(p["time"])] + [b for b in (who, where) if b]
    return "  ·  ".join(bits)


def draw_block(d, y, lines, f, fill, leading, max_y):
    """Draw lines until max_y; ellipsize the last one that fits."""
    lh = int(f.size * leading)
    for i, ln in enumerate(lines):
        if y + lh > max_y:
            return y, True
        last_fits = y + 2 * lh > max_y
        if last_fits and i < len(lines) - 1:
            ln = ln.rstrip(",;:") + " …"
        d.text((M, y), ln, font=f, fill=fill)
        y += lh
        if last_fits and i < len(lines) - 1:
            return y, True
    return y, False


def render(posts, out_path):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    width = W - 2 * M

    # Header: LIVE pill + title
    pill_f = font(HELV, 34, 1)
    d.rounded_rectangle((M, 70, M + 120, 122), radius=10, fill=RED)
    d.text((M + 60, 96), "LIVE", font=pill_f, fill=(255, 255, 255), anchor="mm")
    d.text((M + 145, 96), "Election Night", font=font(HELV, 46, 1), fill=FG, anchor="lm")
    d.text((M, 150), "New York Times live updates", font=font(HELV, 30), fill=MUTED)
    d.line((M, 205, W - M, 205), fill=RULE, width=2)

    y = 245
    if posts:
        p = posts[0]
        d.text((M, y), meta(p), font=font(HELV, 38), fill=MUTED)
        y += 70
        if p.get("headline"):
            y, _ = draw_block(d, y, wrap(d, p["headline"], font(GEORGIA_B, 70), width),
                              font(GEORGIA_B, 70), FG, 1.16, 1380)
            y += 20
        body_f = font(GEORGIA, 56)
        for para in p.get("paragraphs", []):
            y, cut = draw_block(d, y, wrap(d, para, body_f, width), body_f, FG, 1.34, 1440)
            if cut:
                break
            y += 22

    # Earlier posts start right after the main one, so a short post leaves
    # no dead space; as many fit as there is room for.
    y = y + 40
    d.line((M, y, W - M, y), fill=RULE, width=2)
    y += 30
    d.text((M, y), "EARLIER", font=font(HELV, 28, 1), fill=MUTED)
    y += 55
    small_meta, small_body = font(HELV, 32), font(GEORGIA, 42)
    for p in posts[1:]:
        if y + 48 + 2 * int(small_body.size * 1.3) > 1840:
            break
        d.text((M, y), meta(p), font=small_meta, fill=MUTED)
        y += 48
        text = p.get("headline") or (p.get("paragraphs") or [""])[0]
        lines = wrap(d, text, small_body, width)
        if len(lines) > 2:
            lines = lines[:2]
            lines[1] = lines[1].rstrip(",;:") + " …"
        y, _ = draw_block(d, y, lines, small_body, (210, 210, 208), 1.3, 1840)
        y += 26

    stamp = datetime.now().strftime("%-I:%M %p").lower()
    d.text((W - M, H - 50), f"updated {stamp}", font=font(HELV, 26), fill=MUTED, anchor="rs")

    img.rotate(90, expand=True).save(out_path, "JPEG", quality=90)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "posts.json")
    with open(src) as f:
        posts = json.load(f)["posts"]
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    out = os.path.join(HERE, "out", "nyt.jpg")
    render(posts, out)
    print(out)
