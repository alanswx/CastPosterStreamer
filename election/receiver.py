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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import render

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")
PORT = 5055
HOST_IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.4.41"
SCREEN = 'Barn One 32" QLED'
REFRESH_SECONDS = 600        # re-send even if nothing changed

_state = {"sig": None, "sent_at": 0.0}
_lock = threading.Lock()


def cast(screen, filename):
    url = f"http://{HOST_IP}:{PORT}/{filename}?t={int(time.time())}"
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(REPO, "chromecast_subprocess.py"),
                        "send_image", "--device-name", screen, "--image-url", url],
                       capture_output=True, text=True, timeout=90)
    print(time.strftime("%H:%M:%S"), screen, (r.stdout.strip().splitlines() or ["?"])[-1], flush=True)


def update_screen(posts, force=False):
    """Redraw and send the slide if the posts changed (or it's due anyway)."""
    sig = hashlib.sha1(json.dumps(posts[:6], sort_keys=True).encode()).hexdigest()
    with _lock:
        due = time.time() - _state["sent_at"] > REFRESH_SECONDS
        if sig == _state["sig"] and not (force or due):
            return
        _state["sig"], _state["sent_at"] = sig, time.time()
    try:
        importlib.reload(render)   # pick up layout edits without a restart
        render.render(posts, os.path.join(OUT, "nyt.jpg"))
        cast(SCREEN, "nyt.jpg")
    except Exception as e:
        print("update failed:", e, flush=True)


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
        threading.Thread(target=update_screen, args=(data.get("posts", []),), daemon=True).start()
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
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
