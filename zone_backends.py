"""Per-zone adapters for the scheduler.

The two zones reach their screens in completely different ways — the barn
casts to Chromecast-built-in QLEDs, the kitchen uploads to a Frame's art
store — and their power semantics differ too (a Frame reports "on" while
showing art, and needs a long press for true standby). The scheduler shouldn't
care, so each zone provides the same small interface:

    is_playing()     -> bool
    start_loaded()   -> {'success': bool, 'what': str, 'error': str|None}
    stop()           -> None
    power_off()      -> {'success': bool, 'errors': {...}}
    verify(state)    -> {screen name: reported state}
"""

import logging
import os
import time
from typing import Any, Dict

import tv_power
from settings_manager import ZONE_BARN, ZONE_KITCHEN

logger = logging.getLogger(__name__)

WAKE_SETTLE_SECONDS = 8
VERIFY_TIMEOUT_SECONDS = 15


class BarnBackend:
    """The four Chromecast-built-in QLEDs. Casting wakes them; KEY_POWER over
    the Tizen API puts them to sleep."""

    zone = ZONE_BARN

    def __init__(self, settings_manager, slideshow_controller, socketio,
                 discover_fn=None, all_shows_fn=None):
        self.settings_manager = settings_manager
        self.sc = slideshow_controller
        self.socketio = socketio
        self.discover_fn = discover_fn
        self.all_shows_fn = all_shows_fn
        self.token_dir = settings_manager.app_support_dir / "tv_tokens"

    def is_playing(self) -> bool:
        return self.sc.is_playlist_running or self.sc.is_slideshow_running

    def start_loaded(self) -> Dict[str, Any]:
        # Nothing discovers the screens at boot, so a scheduled start would
        # otherwise fail with "No enabled Chromecast devices found".
        if not self.sc.chromecast_manager.get_enabled_devices() and self.discover_fn:
            logger.info("[barn] no screens known yet, discovering first")
            self.discover_fn()

        kind = (self.settings_manager.get_zone_setting(ZONE_BARN, "loaded_kind") or "playlist").lower()
        if kind == "show":
            path = self.settings_manager.get_zone_setting(ZONE_BARN, "selected_directory") or ""
            res = self.sc.play_single_show(path)
            what = f"show started: {path.rstrip('/').split('/')[-1] or path}"
        elif kind == "all_shows" and self.all_shows_fn:
            items = self.all_shows_fn()
            res = self.sc.start_playlist(items=items, name="All Shows") if items \
                else {"success": False, "error": "no shows in any saved playlist"}
            what = f"all shows started ({len(items)} shows)"
        else:
            res = self.sc.play_current_playlist()
            what = "playlist started"

        if res.get("success"):
            self.socketio.emit("playlist_started")
            self.socketio.emit("playlist_status_update", self.sc.get_playlist_status())
        return {"success": bool(res.get("success")), "what": what, "error": res.get("error")}

    def stop(self):
        self.sc.stop_playlist()
        self.sc.stop_slideshow()
        self.socketio.emit("playlist_stopped")
        self.socketio.emit("playlist_status_update", self.sc.get_playlist_status())

    def power_off(self) -> Dict[str, Any]:
        errors = {}
        devices = self.settings_manager.get_enabled_devices()
        for d in devices:
            r = tv_power.power_off(d["host"], d["uuid"], self.token_dir, d["name"])
            if r["error"]:
                errors[d["name"]] = r["error"]
        return {"success": not errors, "errors": errors}

    def verify(self, want: str) -> Dict[str, Any]:
        """Casting wakes these screens, so 'on' needs only a settle delay."""
        if want == "on":
            time.sleep(WAKE_SETTLE_SECONDS)
        target = "on" if want == "on" else "standby"
        return {d["name"]: tv_power.wait_for_state(d["host"], target, timeout=VERIFY_TIMEOUT_SECONDS)
                for d in self.settings_manager.get_enabled_devices()}


class KitchenBackend:
    """The Samsung Frame. No Chromecast: art mode uploads, Wake-on-LAN to
    wake, long-press power for true standby."""

    zone = ZONE_KITCHEN

    def __init__(self, settings_manager, frame_controller, socketio, all_shows_fn=None):
        self.settings_manager = settings_manager
        self.fc = frame_controller
        self.socketio = socketio
        self.all_shows_fn = all_shows_fn

    def is_playing(self) -> bool:
        return self.fc.is_running

    def start_loaded(self) -> Dict[str, Any]:
        # Unlike the barn, this screen must be awake before anything is sent:
        # art requests against a sleeping Frame fail with error -10.
        if not self.fc.ensure_awake():
            return {"success": False, "what": "frame unreachable", "error": "could not wake the Frame"}

        kind = (self.settings_manager.get_zone_setting(ZONE_KITCHEN, "loaded_kind") or "playlist").lower()
        if kind == "show":
            path = self.settings_manager.get_zone_setting(ZONE_KITCHEN, "selected_directory") or ""
            name = path.rstrip("/").split("/")[-1] or path
            items = [{"directory_path": path, "directory_name": name,
                      "duration_minutes": 60, "is_valid": 1 if os.path.isdir(path) else 0,
                      "id": "loaded-show"}]
            res = self.fc.start(items=items, name=name)
            what = f"show started: {name}"
        elif kind == "all_shows" and self.all_shows_fn:
            items = self.all_shows_fn()
            res = self.fc.start(items=items, name="All Shows") if items \
                else {"success": False, "error": "no shows in any saved playlist"}
            what = f"all shows started ({len(items)} shows)"
        else:
            res = self.fc.start()
            what = "playlist started"

        if res.get("success"):
            self.socketio.emit("kitchen_status_update", self.fc.get_status())
        return {"success": bool(res.get("success")), "what": what, "error": res.get("error")}

    def stop(self):
        self.fc.stop()
        self.socketio.emit("kitchen_status_update", self.fc.get_status())

    def power_off(self) -> Dict[str, Any]:
        r = self.fc.power_off()
        return {"success": bool(r.get("success")),
                "errors": {} if r.get("success") else {"Frame": r.get("error")}}

    def verify(self, want: str) -> Dict[str, Any]:
        name = "Kitchen Frame"
        if want == "on":
            time.sleep(3)
            return {name: self.fc.power_state()}
        # "Off" for a Frame is true standby: it drops off the network entirely
        # for a while, so unreachable is a success, not a failure.
        deadline = time.time() + VERIFY_TIMEOUT_SECONDS
        state = self.fc.power_state()
        while state == "on" and time.time() < deadline:
            time.sleep(3)
            state = self.fc.power_state()
        return {name: state if state else "standby"}
