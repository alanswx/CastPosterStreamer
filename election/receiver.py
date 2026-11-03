"""Election-night receiver.

The NYT live blog is read in Dave's own signed-in Chrome (NYT blocks
automated fetching), and the page POSTs the posts here. This saves them
and serves rendered slides to the Chromecasts.

  POST /posts      JSON {"posts": [...]}   -> saved to posts.json
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
# Newest post on Barn One, the ones before it on Two, Three and Four.
SCREENS = ['Barn One 32" QLED', 'Barn Two 32" QLED', 'Barn Three 32" QLED', 'Barn Four 32" QLED']
REFRESH_SECONDS = 600        # re-send even if nothing changed
# The party ends at 23:00. The barn's power schedule is set to turn the
# screens off then, so stop casting just before (a cast would wake them),
# and afterwards put the schedule back to its everyday off time and exit.
STOP_CASTING_AT = "22:59"
RESTORE_AT = "23:05"
EVERYDAY_SCHEDULE = {"zone": "barn", "enabled": True, "on_time": "09:00", "off_time": "18:00"}

_state = {s: {"sig": None, "sent_at": 0.0} for s in SCREENS}
_lock = threading.Lock()


def now_hhmm():
    return time.strftime("%H:%M")


def cast(screen, filename):
    url = f"http://{HOST_IP}:{PORT}/{filename}?t={int(time.time())}"
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(REPO, "chromecast_subprocess.py"),
                        "send_image", "--device-name", screen, "--image-url", url],
                       capture_output=True, text=True, timeout=90)
    print(time.strftime("%H:%M:%S"), screen, (r.stdout.strip().splitlines() or ["?"])[-1], flush=True)


def update_screen(i, post):
    """Redraw and send one screen's slide if its post changed (or it's due anyway)."""
    screen = SCREENS[i]
    sig = hashlib.sha1(json.dumps(post, sort_keys=True).encode()).hexdigest()
    with _lock:
        st = _state[screen]
        due = time.time() - st["sent_at"] > REFRESH_SECONDS
        if sig == st["sig"] and not due:
            return
        st["sig"], st["sent_at"] = sig, time.time()
    try:
        name = f"nyt{i + 1}.jpg"
        render.render(post, os.path.join(OUT, name), latest=(i == 0))
        cast(screen, name)
    except Exception as e:
        print(screen, "update failed:", e, flush=True)


def update_screens(posts):
    if now_hhmm() >= STOP_CASTING_AT:
        return
    importlib.reload(render)   # pick up layout edits without a restart
    # Video/graphic-only posts have no text to show; skip them.
    posts = [p for p in posts if p.get("headline") or p.get("paragraphs")]
    for i, post in enumerate(posts[:len(SCREENS)]):
        threading.Thread(target=update_screen, args=(i, post), daemon=True).start()


def end_of_night():
    while now_hhmm() < RESTORE_AT:
        time.sleep(30)
    req = urllib.request.Request("http://localhost:5001/api/schedule",
                                 data=json.dumps(EVERYDAY_SCHEDULE).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        print("schedule restored:", urllib.request.urlopen(req, timeout=30).read().decode(), flush=True)
    except Exception as e:
        print("schedule restore FAILED:", e, flush=True)
    os._exit(0)


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
        with open(os.path.join(HERE, "posts.json"), "w") as f:
            json.dump(data, f, indent=1)
        threading.Thread(target=update_screens, args=(data.get("posts", []),), daemon=True).start()
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
    threading.Thread(target=end_of_night, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
