"""Election-night receiver.

The NYT and CNN live pages are read in Dave's own signed-in Chrome (NYT
blocks automated fetching), and the add-on POSTs the posts here. This saves
them, alternates the screens between the two sources, and serves the rendered
slides to the Chromecasts.

  POST /posts      JSON {"source": url, "posts": [...]}  -> posts.json / posts_cnn.json
  GET  /<file>.jpg rendered slides from out/
"""
import hashlib
import importlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import render

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")
PORT = 5055
HOST_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.41"
# Newest post on the left, older to the right. The barn's screens hang
# left to right as One, Three, Four, Two (not in name order).
SCREENS = ['Barn One 32" QLED', 'Barn Three 32" QLED', 'Barn Four 32" QLED', 'Barn Two 32" QLED']
REFRESH_SECONDS = 600        # re-send even if nothing changed
ROTATE_SECONDS = 60          # how long each news source stays up
FEEDS = {"nyt": ("New York Times", "posts.json"), "cnn": ("CNN", "posts_cnn.json")}
# The party ends at 23:00. Stop casting just before (a cast would wake the
# screens), then turn the barn's everyday schedule back on: being outside its
# hours, the Posters app powers the screens off straight away.
STOP_CASTING_AT = "22:59"
RESTORE_AT = "23:00"
EVERYDAY_SCHEDULE = {"zone": "barn", "enabled": True, "on_time": "09:00", "off_time": "18:00"}

_posts = {f: [] for f in FEEDS}
_shown = {s: {"sig": None, "sent_at": 0.0} for s in SCREENS}


def now_hhmm():
    return time.strftime("%H:%M")


def cast(screen, filename):
    """Send one slide; True if the screen took it."""
    url = f"http://{HOST_IP}:{PORT}/{filename}?t={int(time.time())}"
    try:
        r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(REPO, "chromecast_subprocess.py"),
                            "send_image", "--device-name", screen, "--image-url", url],
                           capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        print(time.strftime("%H:%M:%S"), screen, "timed out", flush=True)
        return False
    last = (r.stdout.strip().splitlines() or ["?"])[-1]
    print(time.strftime("%H:%M:%S"), screen, filename, last, flush=True)
    return '"success": true' in last


def send_screen(screen, name, sig):
    if cast(screen, name):
        _shown[screen].update(sig=sig, sent_at=time.time())


def show_feed(feed):
    """Bring the screens up to date with this feed. A screen whose send failed
    keeps its old signature, so the next pass tries it again."""
    title = FEEDS[feed][0]
    jobs = []
    for i, (screen, post) in enumerate(zip(SCREENS, _posts[feed])):
        sig = hashlib.sha1(json.dumps([feed, post], sort_keys=True).encode()).hexdigest()
        st = _shown[screen]
        if sig == st["sig"] and time.time() - st["sent_at"] < REFRESH_SECONDS:
            continue
        name = f"{feed}{i + 1}.jpg"
        importlib.reload(render)   # pick up layout edits without a restart
        render.render(post, os.path.join(OUT, name), latest=(i == 0), source=title)
        jobs.append(threading.Thread(target=send_screen, args=(screen, name, sig), daemon=True))
    for j in jobs:
        j.start()
    for j in jobs:
        j.join()


def run_screens():
    """Alternate between the news sources until the party ends."""
    feed, shown_at = "nyt", time.time()
    while now_hhmm() < STOP_CASTING_AT:
        other = "cnn" if feed == "nyt" else "nyt"
        if _posts[other] and (not _posts[feed] or time.time() - shown_at >= ROTATE_SECONDS):
            feed, shown_at = other, time.time()
        try:
            show_feed(feed)
        except Exception as e:
            print("update failed:", e, flush=True)
        time.sleep(3)


def end_of_night():
    run_screens()
    while now_hhmm() < RESTORE_AT:
        time.sleep(5)
    req = urllib.request.Request("http://localhost:5001/api/schedule",
                                 data=json.dumps(EVERYDAY_SCHEDULE).encode(),
                                 headers={"Content-Type": "application/json"})
    for _ in range(10):
        try:
            print("schedule restored:", urllib.request.urlopen(req, timeout=30).read().decode(), flush=True)
            break
        except Exception as e:
            print("schedule restore FAILED:", e, flush=True)
            time.sleep(30)
    os._exit(0)


def usable(posts):
    # Video/graphic-only posts have no text to show; skip them.
    return [p for p in posts if p.get("headline") or p.get("paragraphs")]


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        # The page is https://www.nytimes.com; Chrome also asks before a
        # public site may talk to a private address (Private Network Access).
        self.send_header("Access-Control-Allow-Origin", "https://www.nytimes.com")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path != "/posts":
            self.send_error(404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        data = json.loads(body)
        feed = "cnn" if "cnn.com" in data.get("source", "") else "nyt"
        with open(os.path.join(HERE, FEEDS[feed][1]), "w") as f:
            json.dump(data, f, indent=1)
        _posts[feed] = usable(data.get("posts", []))
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"saved": len(data.get("posts", []))}).encode())

    def do_GET(self):
        name = os.path.basename(self.path.split("?")[0])
        path = os.path.join(OUT, name)
        if not name.endswith(".jpg") or not os.path.isfile(path):
            self.send_error(404)
            return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print(f"receiver on :{PORT}", flush=True)
    for feed, (_, filename) in FEEDS.items():   # pick up where we left off
        path = os.path.join(HERE, filename)
        if os.path.isfile(path):
            with open(path) as f:
                _posts[feed] = usable(json.load(f).get("posts", []))
    threading.Thread(target=end_of_night, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
