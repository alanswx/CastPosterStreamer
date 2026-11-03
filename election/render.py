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


def fit_body(d, p, width, top, bottom):
    """Largest headline/body size at which the whole post fits the screen."""
    for size in range(84, 50, -4):
        head_f, body_f = font(GEORGIA_B, size + 10), font(GEORGIA, size)
        h = 0
        if p.get("headline"):
            h += len(wrap(d, p["headline"], head_f, width)) * int(head_f.size * 1.14) + 24
        for para in p.get("paragraphs", []):
            h += len(wrap(d, para, body_f, width)) * int(body_f.size * 1.3) + 26
        if top + h <= bottom:
            break
    return head_f, body_f


def render(post, out_path, latest=True):
    """One post per screen: the newest on Barn One, the ones before it on the others."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    width = W - 2 * M
    bottom = H - 90

    # Header: LIVE pill on the newest post, a quieter EARLIER pill on the rest
    pill_f = font(HELV, 34, 1)
    label, pill_w = ("LIVE", 120) if latest else ("EARLIER", 190)
    d.rounded_rectangle((M, 70, M + pill_w, 122), radius=10, fill=RED if latest else RULE)
    d.text((M + pill_w // 2, 96), label, font=pill_f, fill=(255, 255, 255), anchor="mm")
    d.text((M + pill_w + 25, 96), "Election Night", font=font(HELV, 46, 1), fill=FG, anchor="lm")
    d.text((M, 150), "New York Times live updates", font=font(HELV, 30), fill=MUTED)
    d.line((M, 205, W - M, 205), fill=RULE, width=2)

    y = 245
    if post:
        meta_f = font(HELV, 44)
        line = meta(post)
        while d.textlength(line, font=meta_f) > width:   # long bylines: trim to fit
            line = line[:-2].rstrip(" ,·") + "…"
        d.text((M, y), line, font=meta_f, fill=MUTED)
        y += 84
        head_f, body_f = fit_body(d, post, width, y, bottom)
        if post.get("headline"):
            y, _ = draw_block(d, y, wrap(d, post["headline"], head_f, width), head_f, FG, 1.14, bottom)
            y += 24
        for para in post.get("paragraphs", []):
            y, cut = draw_block(d, y, wrap(d, para, body_f, width), body_f, FG, 1.3, bottom)
            if cut:
                break
            y += 26

    stamp = datetime.now().strftime("%-I:%M %p").lower()
    d.text((W - M, H - 50), f"updated {stamp}", font=font(HELV, 26), fill=MUTED, anchor="rs")

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
