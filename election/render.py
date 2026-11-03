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
M = 56                       # side margin
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
    bits = [local_time(p["time"])] + ([p["author"]] if p.get("author") else [])
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


def render(post, out_path, latest=True):
    """One post per screen: the newest on Barn One, the ones before it on the others."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    width = W - 2 * M
    bottom = H - 90

    # Header: LIVE pill on the newest post, a quieter EARLIER pill on the rest,
    # with the source beside it on the same line
    pill_f, title_f = font(HELV, 42, 1), font(HELV, 50, 1)   # largest that keeps one line
    label = "LIVE" if latest else "EARLIER"
    pill_w = int(d.textlength(label, font=pill_f)) + 50
    d.rounded_rectangle((M, 64, M + pill_w, 128), radius=12, fill=RED if latest else RULE)
    d.text((M + pill_w // 2, 96), label, font=pill_f, fill=(255, 255, 255), anchor="mm")
    d.text((M + pill_w + 25, 96), "New York Times live updates", font=title_f, fill=FG, anchor="lm")
    d.line((M, 160, W - M, 160), fill=RULE, width=2)

    y = 200
    if post:
        meta_f = font(HELV, 44)
        line = meta(post)
        while d.textlength(line, font=meta_f) > width:   # long bylines: trim to fit
            line = line[:-2].rstrip(" ,·") + "…"
        d.text((M, y), line, font=meta_f, fill=MUTED)
        y += 84
        # One text size on every screen; a long post is cut off with "…"
        # rather than shrunk, so the four screens match.
        head_f, body_f = font(GEORGIA_B, 86), font(GEORGIA, 76)
        if post.get("headline"):
            y, _ = draw_block(d, y, wrap(d, post["headline"], head_f, width), head_f, FG, 1.14, bottom)
            y += 24
        for para in post.get("paragraphs", []):
            y, cut = draw_block(d, y, wrap(d, para, body_f, width), body_f, FG, 1.3, bottom)
            if cut:
                break
            y += 26

    img.rotate(90, expand=True).save(out_path, "JPEG", quality=90)


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "posts.json")
    with open(src) as f:
        posts = json.load(f)["posts"]
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    for i, p in enumerate(posts[:4]):
        out = os.path.join(HERE, "out", f"nyt{i + 1}.jpg")
        render(p, out, latest=(i == 0))
        print(out)
