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
import re
import socket
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

try:
    import gevent
    _GEVENT = True
except ImportError:                     # pragma: no cover
    _GEVENT = False


def _offload(fn, *args, **kwargs):
    """Run blocking, CPU-bound work off the gevent hub.

    Resizing a 4K image and scanning a /24 are both long enough to starve the
    event loop — the watchdog caught a 78s freeze that took the whole web UI
    down with it. gevent's threadpool keeps them on real threads.
    """
    if _GEVENT:
        return gevent.get_hub().threadpool.apply(fn, args, kwargs)
    return fn(*args, **kwargs)

REST_PORT = 8001
WS_PORT = 8002
REMOTE_NAME = "Posters"

# Art mode always renders to the panel's native landscape canvas, whichever
# way the TV is physically hung.
PANEL_LANDSCAPE = (3840, 2160)
JPEG_QUALITY = 90

UPLOAD_GAP_SECONDS = 0.5     # consecutive art requests dislike being rushed
ART_CAPACITY = 260           # our uploads kept on the TV; this LS03R refuses more
                             # past ~310 distinct images (it lists each one twice)
EVICT_BATCH = 40             # evict in batches so a long show doesn't evict per image
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
    """Render a poster exactly the way the barn screens receive it.

    The screens — barn and Frame alike — are mounted on their side, and the
    posters have been rotated to suit that, so a correctly prepared image is
    LANDSCAPE and appears upright once the panel is turned. Two things produce
    that landscape image, and both must be handled the same way:

      * Finder's "Rotate Left" is lossless: it leaves the original portrait
        pixels alone and writes an EXIF orientation tag. exif_transpose applies
        it, giving the landscape image.
      * Some files instead have the rotation baked into their pixels and carry
        no tag. They are already landscape.

    So: apply EXIF, then fit to the panel's native landscape canvas. Rotating
    here as well — which this used to do — turned the second kind through 90
    degrees twice, which is why those posters came out sideways and
    letterboxed while the barn showed them correctly.
    """
    with Image.open(path) as im:
        oriented = ImageOps.exif_transpose(im).convert("RGB")

    fitted = ImageOps.contain(oriented, PANEL_LANDSCAPE, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", PANEL_LANDSCAPE, (0, 0, 0))
    canvas.paste(fitted, ((PANEL_LANDSCAPE[0] - fitted.width) // 2,
                          (PANEL_LANDSCAPE[1] - fitted.height) // 2))

    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=JPEG_QUALITY)
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
        # What the loop actually put on the panel, and the list it is working
        # through. Status reports these rather than indexing into a freshly
        # read playlist: the stored playlist can be replaced mid-show, and
        # then the index means nothing — it named a show from the new list
        # while the Frame was still displaying one from the old.
        self.current_item: Optional[Dict[str, Any]] = None
        self.playing_items: Optional[List[Dict[str, Any]]] = None
        self.virtual_items: Optional[List[Dict[str, Any]]] = None
        self.virtual_name: Optional[str] = None
        self._lock = threading.Lock()
        # Bumped on every start. A loop whose generation is stale exits, so a
        # stop()/start() pair can never leave two loops driving the same TV.
        self._generation = 0

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

        def _reachable(ip):
            sock = socket.socket()
            sock.settimeout(0.25)
            try:
                sock.connect((ip, REST_PORT))
                return True
            except Exception:
                return False
            finally:
                sock.close()

        for i in range(1, 255):
            ip = f"{base}.{i}"
            if not _offload(_reachable, ip):
                continue
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
        """Wake from standby and put the panel into art mode.

        Deep standby takes the TV off the network completely — no scan will
        find it — so the magic packet goes out on the stored MAC first, and
        only then do we look for it. Waiting to locate it before waking is a
        deadlock: it cannot be located until it is awake.
        """
        host = self.host or self.settings_manager.get_setting("frame_host")
        mac = self.mac or self.settings_manager.get_setting("frame_mac")

        if host and _power_state(host) == "on":
            self.host = host
            self.set_art_mode(True)
            return {"success": True}

        if mac:
            self.logger.info(f"[frame] waking {mac} (deep standby drops it off the network)")
            send_wol(mac)
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(3)
                if host and _power_state(host) == "on":
                    self.host = host
                    self.set_art_mode(True)
                    return {"success": True}
        elif not host:
            return {"success": False, "error": "Frame never located; no stored MAC"}

        # Still nothing: it may have taken a different address while asleep.
        if self.locate(rescan=True) and _power_state(self.host) == "on":
            self.set_art_mode(True)
            return {"success": True}

        return {"success": False, "error": "did not wake"}

    def ensure_awake(self) -> bool:
        """Make sure the TV is awake and in art mode before any art request.

        Uploads and selects fail with error -10 against a sleeping Frame, and
        the TV can fall asleep on its own mid-show, so this is called on
        failure as well as at the start of a show.
        """
        host = self.host or self.settings_manager.get_setting("frame_host")
        if host and _power_state(host) == "on":
            self.host = host
            return True
        return self.power_on().get("success", False)

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
                cid = self._upload_one(art, path, keep=ids)
                if cid:
                    ids.append(cid)
                    time.sleep(UPLOAD_GAP_SECONDS)
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
        self._touch(ready)
        return ready, pending

    # The Frame's art store is finite: at a little over 600 artworks every
    # upload fails with error -1, and since nothing here ever deleted what it
    # uploaded, every show not already on the TV stopped loading and the panel
    # froze on the previous poster. So this keeps its own uploads under a cap,
    # evicting the least recently shown. Only artwork recorded in
    # frame_art_cache is ever deleted — the owner's own photos are not ours.

    def _touch(self, content_ids: List[str]):
        """Mark artworks as just used, so eviction takes the stalest first."""
        if not content_ids:
            return
        import sqlite3
        with sqlite3.connect(self.settings_manager.db_path) as conn:
            conn.executemany(
                "UPDATE frame_art_cache SET uploaded_at = CURRENT_TIMESTAMP WHERE content_id = ?",
                [(c,) for c in content_ids])
            conn.commit()

    def _evict(self, art, count: int, keep=()) -> int:
        """Delete up to `count` of our least recently used artworks, never
        touching anything in `keep` (the show on screen). Returns how many."""
        import sqlite3
        keep = set(keep)
        with sqlite3.connect(self.settings_manager.db_path) as conn:
            rows = conn.execute(
                "SELECT content_id FROM frame_art_cache ORDER BY uploaded_at ASC").fetchall()
        victims = [r[0] for r in rows if r[0] not in keep][:count]
        if not victims:
            return 0
        try:
            art.delete_list(victims)
        except Exception as e:
            # Ids the TV no longer has make the whole batch fail; they are
            # still worth forgetting, so fall through and drop the rows.
            self.logger.warning(f"[frame] evict: delete_list failed ({e}); trying one by one")
            for cid in victims:
                try:
                    art.delete(cid)
                except Exception:
                    pass
        with sqlite3.connect(self.settings_manager.db_path) as conn:
            conn.executemany("DELETE FROM frame_art_cache WHERE content_id = ?",
                             [(c,) for c in victims])
            conn.commit()
        self.logger.info(f"[frame] evicted {len(victims)} old artworks to make room")
        return len(victims)

    def _make_room(self, art, keep=()):
        import sqlite3
        with sqlite3.connect(self.settings_manager.db_path) as conn:
            have = conn.execute("SELECT COUNT(*) FROM frame_art_cache").fetchone()[0]
        if have >= ART_CAPACITY:
            self._evict(art, have - ART_CAPACITY + EVICT_BATCH, keep)

    def _upload_one(self, art, path: str, keep=()) -> Optional[str]:
        self._make_room(art, keep)
        for attempt in (1, 2):
            try:
                data = _offload(prepare_image, path)
                cid = art.upload(data, file_type="JPEG", matte="none")
                self._remember_upload(path, cid)
                self.logger.info(f"[frame] uploaded {os.path.basename(path)} -> {cid}")
                return cid
            except Exception as e:
                self.logger.error(f"[frame] upload failed for {os.path.basename(path)}: {e}")
                # -1: the art store is full (possibly with art we don't track).
                # Free space and try once more rather than freezing the show.
                if attempt == 1 and re.search(r"error number -1(?!\d)", str(e)):
                    if self._evict(art, EVICT_BATCH, keep):
                        continue
                # -10 and timeouts usually mean the TV went to sleep under us.
                if "-10" in str(e) or "time" in str(e).lower():
                    self.ensure_awake()
                return None
        return None

    # -------------------------------------------------------------- playback

    def _record_play(self, item: Dict[str, Any]):
        """Add a show to the kitchen's Recently Played once something from it
        is actually on the panel. Never allowed to disturb playback."""
        from settings_manager import ZONE_KITCHEN
        try:
            self.settings_manager.record_play(ZONE_KITCHEN, item["directory_path"],
                                              item.get("directory_name"))
            if self.socketio:
                self.socketio.emit("recent_updated", {"zone": ZONE_KITCHEN})
        except Exception as e:
            self.logger.warning(f"[frame] could not record play history: {e}")

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
            self._generation += 1
            generation = self._generation

        if self.socketio:
            self.thread = self.socketio.start_background_task(self._loop, generation)
        else:
            self.thread = threading.Thread(target=self._loop, args=(generation,), daemon=True)
            self.thread.start()
        self.logger.info(f"[frame] started ({len(self._active_items())} shows)")
        return {"success": True}

    def stop(self):
        with self._lock:
            if not self.is_running:
                return
            # Invalidate the running loop as well as clearing the flag: start()
            # may be called immediately afterwards, and without this the old
            # loop wakes, sees is_running true again and keeps playing its own
            # stale list alongside the new one.
            self._generation += 1
            self.is_running = False
            self.is_paused = False
            self.skip_requested = False
            self.virtual_items = None
            self.virtual_name = None
            self.current_item = None
            self.playing_items = None
        self.logger.info("[frame] stopped")

    def toggle_pause(self):
        with self._lock:
            self.is_paused = not self.is_paused
        return self.is_paused

    def skip(self):
        with self._lock:
            self.skip_requested = True

    def _sleep(self, seconds: float, generation: Optional[int] = None):
        """Sleep in small slices so stop/skip/restart are responsive."""
        end = time.time() + seconds
        while time.time() < end and self.is_running:
            if self.skip_requested or (generation is not None and generation != self._generation):
                return
            time.sleep(min(1.0, max(0.05, end - time.time())))

    def _alive(self, generation: int) -> bool:
        return self.is_running and generation == self._generation

    def _loop(self, generation: int = 0):
        items = self._active_items()
        if not items:
            self.is_running = False
            return
        self.playing_items = items

        failures = 0
        while self._alive(generation):
            try:
                item = items[self.current_index % len(items)]
                self.current_item = item
                directory = item["directory_path"]
                duration = (item.get("duration_minutes") or 10) * 60
                self.logger.info(f"[frame] show: {item['directory_name']} for {duration/60:.0f} min")

                # A sleeping Frame rejects every art request with error -10,
                # so wake it before connecting rather than after failing.
                if not self.ensure_awake():
                    failures += 1
                    self.logger.error("[frame] could not wake; retrying")
                    self._sleep(min(60, 10 * failures), generation)
                    continue

                tv = self._tv()
                if not tv:
                    failures += 1
                    self._sleep(min(60, 5 * failures), generation)
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
                    first = self._upload_one(art, pending.pop(0), keep=content_ids)
                    if first:
                        content_ids.append(first)

                if not content_ids:
                    self.logger.error(f"[frame] nothing to show for {directory}, skipping")
                    self.current_index = (self.current_index + 1) % len(items)
                    try:
                        tv.close()
                    except Exception:
                        pass
                    failures += 1
                    self._sleep(2 if failures < len(items) else min(60, 10 * failures), generation)
                    continue

                failures = 0
                self.current_item_start = time.time()
                self._record_play(item)
                started = time.time()
                idx = 0
                interval = self._interval()

                while self._alive(generation) and (time.time() - started) < duration:
                    if self.skip_requested:
                        break
                    if self.is_paused:
                        time.sleep(1)
                        started += 1      # paused time doesn't count against the show
                        continue
                    # Fill the rest of the show one image per cycle, on THIS
                    # connection: the TV accepts only one websocket client, so
                    # a second uploader connection kills them both.
                    if pending:
                        got = self._upload_one(art, pending.pop(0), keep=content_ids)
                        if got:
                            content_ids.append(got)

                    cid = content_ids[idx % len(content_ids)]
                    try:
                        art.select_image(cid, show=True)
                    except Exception as e:
                        # The TV drops long-lived websockets, so a show lasting
                        # minutes will lose its connection mid-way. Reconnect
                        # once and retry rather than skipping the image.
                        self.logger.warning(f"[frame] select failed ({e}); recovering")
                        try:
                            tv.close()
                        except Exception:
                            pass
                        # The TV can put itself to sleep mid-show (its own idle
                        # timer), and everything then fails with -10 or a
                        # timeout. Wake it before reconnecting, or we just log
                        # errors until the show ends.
                        if not self.ensure_awake():
                            self.logger.error("[frame] could not wake mid-show; ending this show")
                            break
                        tv = self._tv()
                        if tv:
                            art = tv.art()
                            try:
                                art.select_image(cid, show=True)
                            except Exception as e2:
                                self.logger.error(f"[frame] select failed after recovery: {e2}")
                        else:
                            self.logger.error("[frame] reconnect failed")
                            break
                    idx += 1
                    self._emit_status()
                    # Emit while waiting too: the kitchen interval is minutes,
                    # and a UI that only hears on image change sits blind.
                    waited = 0.0
                    while waited < interval and self._alive(generation) and not self.skip_requested:
                        step = min(15.0, interval - waited)
                        self._sleep(step, generation)
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
                self._sleep(5, generation)

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
        items = self.playing_items if self.is_running and self.playing_items else self._active_items()
        item = self.current_item
        if not self.is_running or not item:
            return {"running": False, "paused": False, "current_item": None,
                    "time_remaining": 0, "total_items": len(items),
                    "virtual_name": None}
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
