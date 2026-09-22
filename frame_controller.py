"""Drive a Samsung Frame through Art Mode.

The barn screens have Chromecast built in, so the slideshow casts image URLs
to them and they fetch the files themselves. The Frame has no Cast at all —
its only route is Samsung's Art Mode: images are *uploaded* to the TV and then
selected one at a time. That makes this a separate playback engine rather than
another device in the existing one.

Practical consequences, all measured on the real TV:
  * upload costs ~3s per image regardless of size, so a show is pre-uploaded
    in the background and cached by content id; switching is then ~1s.
  * art mode renders to the panel's native LANDSCAPE 3840x2160 canvas and has
    no idea the TV is physically mounted portrait, so images are letterboxed
    into a portrait viewport and then rotated to cancel the mounting out.
  * the Frame reports PowerState "on" while in art mode (the panel is lit).
    KEY_POWER only toggles art mode vs TV mode; true standby needs a long
    press, and waking from it needs Wake-on-LAN.
"""

import io
import json
import logging
import os
import socket
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

REST_PORT = 8001
WS_PORT = 8002
REMOTE_NAME = "Posters"

# The panel as art mode sees it, and as you see it once physically rotated.
PANEL_LANDSCAPE = (3840, 2160)
PANEL_PORTRAIT = (2160, 3840)
ROTATE_DEGREES = 90          # CCW; cancels out this TV's portrait mounting
JPEG_QUALITY = 90

UPLOAD_GAP_SECONDS = 0.5     # consecutive art requests dislike being rushed
WAKE_TIMEOUT_SECONDS = 90


def _power_state(host: str, timeout: float = 3.0) -> Optional[str]:
    """'on' / 'standby', or None when the TV is off the network entirely."""
    try:
        with urllib.request.urlopen(f"http://{host}:{REST_PORT}/api/v2/", timeout=timeout) as r:
            return json.load(r)["device"].get("PowerState")
    except Exception:
        return None


def _device_info(host: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"http://{host}:{REST_PORT}/api/v2/", timeout=timeout) as r:
            return json.load(r).get("device")
    except Exception:
        return None


def send_wol(mac: str, broadcast: str = "255.255.255.255"):
    """Wake-on-LAN magic packet. The Frame can't be woken by casting."""
    packet = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "").replace("-", "")) * 16
    for host, port in ((broadcast, 9), (broadcast, 7)):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            for _ in range(3):
                s.sendto(packet, (host, port))
                time.sleep(0.05)
        except Exception as e:
            logger.debug(f"[frame] WoL to {host}:{port} failed: {e}")
        finally:
            s.close()


def prepare_image(path: str) -> bytes:
    """Fit a poster to the portrait viewport, pad it, and rotate for the panel.

    Uploading a portrait poster raw gets it stretched across the landscape
    canvas and shown sideways; this cancels both problems out.
    """
    with Image.open(path) as im:
        im = im.convert("RGB")
        # contain() scales up as well as down — thumbnail() only shrinks, which
        # would leave a small poster marooned in a large black canvas.
        fitted = ImageOps.contain(im, PANEL_PORTRAIT, Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", PANEL_PORTRAIT, (0, 0, 0))
    canvas.paste(fitted, ((PANEL_PORTRAIT[0] - fitted.width) // 2,
                          (PANEL_PORTRAIT[1] - fitted.height) // 2))
    rotated = canvas.rotate(ROTATE_DEGREES, expand=True)

    buf = io.BytesIO()
    rotated.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


class FrameController:
    """Playback and power for the kitchen Frame."""

    SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

    def __init__(self, settings_manager, socketio=None):
        self.settings_manager = settings_manager
        self.socketio = socketio
        self.logger = logger

        self.host: Optional[str] = None
        self.mac: Optional[str] = None

        self.is_running = False
        self.is_paused = False
        self.skip_requested = False
        self.thread = None
        self.current_index = 0
        self.current_item_start = None
        self.virtual_items: Optional[List[Dict[str, Any]]] = None
        self.virtual_name: Optional[str] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------- discovery

    def locate(self, rescan: bool = False) -> Optional[str]:
        """Find the Frame. Its IP moves, so prefer the stored host but fall
        back to scanning the subnet for a TV reporting FrameTVSupport."""
        stored = self.settings_manager.get_setting("frame_host")
        if stored and not rescan:
            info = _device_info(stored)
            if info and str(info.get("FrameTVSupport", "")).lower() == "true":
                self.host, self.mac = stored, info.get("wifiMac")
                return self.host

        base = ".".join((stored or "192.168.4.1").split(".")[:3])
        self.logger.info(f"[frame] locating Frame on {base}.0/24")
        for i in range(1, 255):
            ip = f"{base}.{i}"
            s = socket.socket()
            s.settimeout(0.25)
            try:
                s.connect((ip, REST_PORT))
            except Exception:
                continue
            finally:
                s.close()
            info = _device_info(ip, timeout=2)
            if info and str(info.get("FrameTVSupport", "")).lower() == "true":
                self.host, self.mac = ip, info.get("wifiMac")
                self.settings_manager.save_setting("frame_host", ip)
                if self.mac:
                    self.settings_manager.save_setting("frame_mac", self.mac)
                self.logger.info(f"[frame] found at {ip} ({self.mac})")
                return ip
        self.logger.warning("[frame] not found on the network")
        return None

    def _tv(self):
        """A connected SamsungTVWS, or None. Imported lazily so a missing
        package degrades to 'kitchen unavailable' rather than breaking boot."""
        host = self.host or self.locate()
        if not host:
            return None
        try:
            from samsungtvws import SamsungTVWS
            token = str(self.settings_manager.app_support_dir / "frame_token.txt")
            return SamsungTVWS(host=host, port=WS_PORT, token_file=token,
                               name=REMOTE_NAME, timeout=30)
        except Exception as e:
            self.logger.error(f"[frame] connect failed: {e}")
            return None

    # ----------------------------------------------------------------- power

    def power_state(self) -> Optional[str]:
        host = self.host or self.locate()
        return _power_state(host) if host else None

    def power_off(self) -> Dict[str, Any]:
        """True standby (dark). A short KEY_POWER would only toggle art mode."""
        tv = self._tv()
        if not tv:
            return {"success": False, "error": "Frame unreachable"}
        try:
            tv.hold_key("KEY_POWER", 3)
            tv.close()
            self.logger.info("[frame] sent long power press (-> standby)")
            return {"success": True}
        except Exception as e:
            self.logger.error(f"[frame] power off failed: {e}")
            return {"success": False, "error": str(e)}

    def power_on(self, timeout: float = WAKE_TIMEOUT_SECONDS) -> Dict[str, Any]:
        """Wake from standby and put the panel into art mode."""
        host = self.host or self.locate()
        mac = self.mac or self.settings_manager.get_setting("frame_mac")
        if not host:
            return {"success": False, "error": "Frame not found"}

        if _power_state(host) != "on":
            if mac:
                send_wol(mac)
            deadline = time.time() + timeout
            while time.time() < deadline and _power_state(host) != "on":
                time.sleep(3)

        state = _power_state(host)
        if state != "on":
            # It may have taken a new IP while asleep.
            if self.locate(rescan=True):
                state = _power_state(self.host)
        if state != "on":
            return {"success": False, "error": f"did not wake (state={state})"}

        self.set_art_mode(True)
        return {"success": True}

    def ensure_awake(self) -> bool:
        """Wake the TV if it's asleep and put it in art mode.

        Uploads and selects fail with error -10 against a sleeping Frame, so
        playback must do this before touching the art API — not merely try
        set_artmode and hope.
        """
        host = self.host or self.locate()
        if not host:
            return False
        state = _power_state(host)
        if state is None:
            # Off the network entirely: it may be in deep standby, or moved.
            if not self.locate(rescan=True):
                return False
            state = _power_state(self.host)
        if state != "on":
            self.logger.info(f"[frame] asleep (state={state}); waking before art requests")
            return self.power_on().get("success", False)
        return True

    def set_art_mode(self, on: bool = True) -> bool:
        tv = self._tv()
        if not tv:
            return False
        try:
            art = tv.art()
            if art.get_artmode() != ("on" if on else "off"):
                art.set_artmode("on" if on else "off")
            tv.close()
            return True
        except Exception as e:
            self.logger.warning(f"[frame] set_art_mode failed: {e}")
            return False

    # --------------------------------------------------------------- uploads

    def _source_key(self, path: str) -> str:
        try:
            st = os.stat(path)
            return f"{path}|{int(st.st_mtime)}|{st.st_size}"
        except OSError:
            return path

    def _cached_content_id(self, path: str) -> Optional[str]:
        import sqlite3
        with sqlite3.connect(self.settings_manager.db_path) as conn:
            row = conn.execute(
                "SELECT content_id FROM frame_art_cache WHERE source_key = ?",
                (self._source_key(path),)).fetchone()
            return row[0] if row else None

    def _remember_upload(self, path: str, content_id: str):
        import sqlite3
        with sqlite3.connect(self.settings_manager.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO frame_art_cache (source_key, content_id, directory_path)"
                " VALUES (?, ?, ?)",
                (self._source_key(path), content_id, os.path.dirname(path)))
            conn.commit()

    def images_in(self, directory: str) -> List[str]:
        try:
            names = sorted(f for f in os.listdir(directory)
                           if os.path.splitext(f.lower())[1] in self.SUPPORTED_EXTS)
        except OSError as e:
            self.logger.error(f"[frame] cannot read {directory}: {e}")
            return []
        return [os.path.join(directory, n) for n in names]

    def preload_show(self, directory: str, limit: int = 30, art=None) -> List[str]:
        """Upload a show's images (skipping ones already on the TV) and return
        their content ids, in order."""
        own_connection = art is None
        tv = None
        if own_connection:
            if not self.ensure_awake():
                self.logger.error("[frame] cannot preload: TV asleep/unreachable")
                return []
            tv = self._tv()
            if not tv:
                return []
            art = tv.art()

        ids: List[str] = []
        try:
            for path in self.images_in(directory)[:limit]:
                cid = self._cached_content_id(path)
                if cid:
                    ids.append(cid)
                    continue
                try:
                    data = prepare_image(path)
                    cid = art.upload(data, file_type="JPEG", matte="none")
                    self._remember_upload(path, cid)
                    ids.append(cid)
                    self.logger.info(f"[frame] uploaded {os.path.basename(path)} -> {cid}")
                    time.sleep(UPLOAD_GAP_SECONDS)
                except Exception as e:
                    self.logger.error(f"[frame] upload failed for {path}: {e}")
        finally:
            if own_connection and tv:
                try:
                    tv.close()
                except Exception:
                    pass
        return ids

    def _split_cached(self, directory: str, limit: int = 30):
        """(content ids already on the TV, source paths still to upload)."""
        ready, pending = [], []
        for path in self.images_in(directory)[:limit]:
            cid = self._cached_content_id(path)
            (ready if cid else pending).append(cid or path)
        return ready, pending

    def _upload_one(self, art, path: str) -> Optional[str]:
        try:
            cid = art.upload(prepare_image(path), file_type="JPEG", matte="none")
            self._remember_upload(path, cid)
            self.logger.info(f"[frame] uploaded {os.path.basename(path)} -> {cid}")
            return cid
        except Exception as e:
            self.logger.error(f"[frame] upload failed for {os.path.basename(path)}: {e}")
            return None

    def _spawn_background_upload(self, paths: List[str], sink: List[str]):
        """Upload the remainder of a show while it's already playing.

        Uses its own connection: the display loop is using the other one, and
        sharing a websocket across greenlets corrupts the stream.
        """
        def worker():
            tv = self._tv()
            if not tv:
                return
            try:
                art = tv.art()
                for path in paths:
                    if not self.is_running:
                        return
                    cid = self._upload_one(art, path)
                    if cid:
                        sink.append(cid)
                    time.sleep(UPLOAD_GAP_SECONDS)
            except Exception as e:
                self.logger.warning(f"[frame] background upload stopped: {e}")
            finally:
                try:
                    tv.close()
                except Exception:
                    pass

        if self.socketio:
            self.socketio.start_background_task(worker)
        else:
            threading.Thread(target=worker, daemon=True).start()

    # -------------------------------------------------------------- playback

    def _active_items(self) -> List[Dict[str, Any]]:
        if self.virtual_items is not None:
            return self.virtual_items
        from settings_manager import ZONE_KITCHEN
        items = self.settings_manager.get_playlist_items(zone=ZONE_KITCHEN)
        return [i for i in items if i["is_valid"]]

    def start(self, items: Optional[List[Dict[str, Any]]] = None,
              name: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            if self.is_running:
                return {"success": True}
            valid = [i for i in items if i.get("is_valid", 1)] if items is not None else None
            if valid is not None and not valid:
                return {"success": False, "error": "No valid shows"}
            if valid is None:
                valid = self._active_items()
                if not valid:
                    return {"success": False, "error": "No valid shows in the kitchen playlist"}
            self.virtual_items = valid if items is not None else None
            self.virtual_name = name if items is not None else None
            self.is_running = True
            self.is_paused = False
            self.skip_requested = False
            self.current_index = 0

        if self.socketio:
            self.thread = self.socketio.start_background_task(self._loop)
        else:
            self.thread = threading.Thread(target=self._loop, daemon=True)
            self.thread.start()
        self.logger.info(f"[frame] started ({len(self._active_items())} shows)")
        return {"success": True}

    def stop(self):
        with self._lock:
            if not self.is_running:
                return
            self.is_running = False
            self.is_paused = False
            self.skip_requested = False
            self.virtual_items = None
            self.virtual_name = None
        self.logger.info("[frame] stopped")

    def toggle_pause(self):
        with self._lock:
            self.is_paused = not self.is_paused
        return self.is_paused

    def skip(self):
        with self._lock:
            self.skip_requested = True

    def _sleep(self, seconds: float):
        """Sleep in small slices so stop/skip are responsive."""
        end = time.time() + seconds
        while time.time() < end and self.is_running:
            if self.skip_requested:
                return
            time.sleep(min(1.0, max(0.05, end - time.time())))

    def _loop(self):
        items = self._active_items()
        if not items:
            self.is_running = False
            return

        failures = 0
        while self.is_running:
            try:
                item = items[self.current_index % len(items)]
                directory = item["directory_path"]
                duration = (item.get("duration_minutes") or 10) * 60
                self.logger.info(f"[frame] show: {item['directory_name']} for {duration/60:.0f} min")

                # A sleeping Frame rejects every art request with error -10,
                # so wake it before connecting rather than after failing.
                if not self.ensure_awake():
                    failures += 1
                    self.logger.error("[frame] could not wake; retrying")
                    self._sleep(min(60, 10 * failures))
                    continue

                tv = self._tv()
                if not tv:
                    failures += 1
                    self._sleep(min(60, 5 * failures))
                    continue
                art = tv.art()

                try:
                    art.set_artmode("on")
                except Exception:
                    pass

                # Show something fast: anything already cached is instant, and
                # otherwise we upload just the first image before starting and
                # let the rest arrive in the background. Uploading a whole show
                # up front would leave the Frame blank for a minute.
                content_ids, pending = self._split_cached(directory)
                if not content_ids and pending:
                    first = self._upload_one(art, pending.pop(0))
                    if first:
                        content_ids.append(first)
                if content_ids and pending:
                    self._spawn_background_upload(pending, content_ids)

                if not content_ids:
                    self.logger.error(f"[frame] nothing to show for {directory}, skipping")
                    self.current_index = (self.current_index + 1) % len(items)
                    try:
                        tv.close()
                    except Exception:
                        pass
                    failures += 1
                    self._sleep(2 if failures < len(items) else min(60, 10 * failures))
                    continue

                failures = 0
                self.current_item_start = time.time()
                started = time.time()
                idx = 0
                interval = self._interval()

                while self.is_running and (time.time() - started) < duration:
                    if self.skip_requested:
                        break
                    if self.is_paused:
                        time.sleep(1)
                        started += 1      # paused time doesn't count against the show
                        continue
                    try:
                        art.select_image(content_ids[idx % len(content_ids)], show=True)
                    except Exception as e:
                        self.logger.warning(f"[frame] select failed: {e}")
                    idx += 1
                    self._emit_status()
                    # Emit while waiting too: the kitchen interval is minutes,
                    # and a UI that only hears on image change sits blind.
                    waited = 0.0
                    while waited < interval and self.is_running and not self.skip_requested:
                        step = min(15.0, interval - waited)
                        self._sleep(step)
                        waited += step
                        self._emit_status()
                    interval = self._interval()

                try:
                    tv.close()
                except Exception:
                    pass

                if self.skip_requested:
                    self.skip_requested = False
                self.current_index = (self.current_index + 1) % len(items)
                items = self._active_items() or items

            except Exception as e:
                self.logger.error(f"[frame] loop error: {e}")
                self._sleep(5)

        self._emit_status()

    def _interval(self) -> int:
        from settings_manager import ZONE_KITCHEN
        raw = self.settings_manager.get_zone_setting(ZONE_KITCHEN, "slideshow_interval")
        try:
            return max(10, int(raw))
        except (TypeError, ValueError):
            return 300

    # ---------------------------------------------------------------- status

    def get_status(self) -> Dict[str, Any]:
        items = self._active_items()
        if not self.is_running or not items:
            return {"running": False, "paused": False, "current_item": None,
                    "time_remaining": 0, "total_items": len(items),
                    "virtual_name": None}
        item = items[self.current_index % len(items)]
        duration = (item.get("duration_minutes") or 10) * 60
        elapsed = time.time() - (self.current_item_start or time.time())
        return {
            "running": True,
            "paused": self.is_paused,
            "current_item": item,
            "current_index": self.current_index,
            "time_remaining": int(max(0, duration - elapsed)),
            "total_items": len(items),
            "virtual_name": self.virtual_name,
        }

    def _emit_status(self):
        if not self.socketio:
            return
        try:
            self.socketio.emit("kitchen_status_update", self.get_status())
        except Exception:
            pass
