"""Daily on/off schedule for the barn screens.

Turns the screens on and starts the current playlist at `schedule_on_time`,
then stops the show and puts the screens into standby at `schedule_off_time`.

Design notes
------------
* Reconcile, don't fire-at-the-second.  Every tick the scheduler asks "should
  the screens be on *right now*?" and acts only when that answer changes (plus
  once at startup and whenever the settings change).  That makes it survive app
  restarts and missed ticks, gives instant feedback when you enable it, and
  means it never fights a manual stop mid-window.
* Power ON = start the playlist.  Casting wakes a Chromecast-built-in TV, so no
  separate wake step is needed (see tv_power.py for why not Wake-on-LAN).
* Power OFF = stop the show, then KEY_POWER each enabled screen (tv_power.py).
* Runs as a gevent background task via socketio.start_background_task, so all
  the blocking calls below are cooperative and never freeze the hub.
"""

import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

import tv_power

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
STARTUP_DELAY_SECONDS = 10   # let discovery / socket clients settle first
WAKE_SETTLE_SECONDS = 8      # a panel takes a few seconds longer to light than its network stack
VERIFY_TIMEOUT_SECONDS = 15

DEFAULT_ON_TIME = "08:00"
DEFAULT_OFF_TIME = "19:00"
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class PowerScheduler:
    def __init__(self, settings_manager, slideshow_controller, socketio,
                 discover_fn=None, all_shows_fn=None):
        self.settings_manager = settings_manager
        self.slideshow_controller = slideshow_controller
        self.socketio = socketio
        # Runs Chromecast discovery synchronously; see app.run_discovery_sync.
        # Needed because nothing discovers the screens at startup, so after a
        # reboot the playlist can't start until something does.
        self.discover_fn = discover_fn
        # Builds the merged All Shows list, for when that's what's loaded.
        self.all_shows_fn = all_shows_fn
        self.token_dir = settings_manager.app_support_dir / "tv_tokens"

        self._lock = threading.Lock()          # monkey-patched -> gevent-safe
        self._running = False
        self._last_applied: Optional[str] = None   # 'on' | 'off' | None
        self._last_action: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ config

    def get_config(self) -> Dict[str, Any]:
        sm = self.settings_manager
        return {
            "enabled": (sm.get_setting("schedule_enabled") or "false").lower() == "true",
            "on_time": sm.get_setting("schedule_on_time") or DEFAULT_ON_TIME,
            "off_time": sm.get_setting("schedule_off_time") or DEFAULT_OFF_TIME,
        }

    def save_config(self, enabled: bool, on_time: str, off_time: str) -> Dict[str, Any]:
        for label, value in (("on_time", on_time), ("off_time", off_time)):
            if not _HHMM.match(value or ""):
                raise ValueError(f"{label} must be HH:MM (24h), got {value!r}")
        sm = self.settings_manager
        sm.save_setting("schedule_enabled", "true" if enabled else "false")
        sm.save_setting("schedule_on_time", on_time)
        sm.save_setting("schedule_off_time", off_time)
        logger.info(f"[schedule] config saved: enabled={enabled} on={on_time} off={off_time}")

        # Re-evaluate right away rather than waiting for the next tick.
        self._last_applied = None
        self.socketio.start_background_task(self.reconcile, "settings changed")
        return self.get_config()

    # ------------------------------------------------------------- state math

    @staticmethod
    def _minutes(hhmm: str) -> int:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)

    def desired_state(self, now: Optional[datetime] = None) -> Optional[str]:
        """'on' / 'off' per the schedule, or None if disabled or misconfigured."""
        cfg = self.get_config()
        if not cfg["enabled"]:
            return None
        on, off = self._minutes(cfg["on_time"]), self._minutes(cfg["off_time"])
        if on == off:
            return None
        cur = (now or datetime.now())
        cur = cur.hour * 60 + cur.minute
        if on < off:
            in_window = on <= cur < off
        else:                                   # window crosses midnight, e.g. 20:00 -> 06:00
            in_window = cur >= on or cur < off
        return "on" if in_window else "off"

    def next_transition(self) -> Optional[Dict[str, str]]:
        desired = self.desired_state()
        if desired is None:
            return None
        cfg = self.get_config()
        return {"state": "off", "at": cfg["off_time"]} if desired == "on" \
            else {"state": "on", "at": cfg["on_time"]}

    def status(self) -> Dict[str, Any]:
        cfg = self.get_config()
        return {
            **cfg,
            "desired_now": self.desired_state(),
            "next_transition": self.next_transition(),
            "last_action": self._last_action,
        }

    def power_states(self) -> Dict[str, Optional[str]]:
        """Live PowerState for every enabled screen (for the UI)."""
        return {d["name"]: tv_power.get_power_state(d["host"])
                for d in self.settings_manager.get_enabled_devices()}

    # ------------------------------------------------------------------- loop

    def start(self):
        if self._running:
            return
        self._running = True
        self.socketio.start_background_task(self._loop)
        logger.info("[schedule] scheduler started")

    def stop(self):
        self._running = False

    def _loop(self):
        time.sleep(STARTUP_DELAY_SECONDS)
        while self._running:
            try:
                self.reconcile("tick")
            except Exception as e:
                logger.error(f"[schedule] reconcile error: {e}")
            time.sleep(TICK_SECONDS)

    def reconcile(self, reason: str = "tick"):
        desired = self.desired_state()
        if desired is None:
            self._last_applied = None
            return
        with self._lock:
            if desired == self._last_applied:
                return
            # Claim the state *before* the slow apply so a reconcile that arrives
            # while this one is still running sees it as already handled.
            logger.info(f"[schedule] desired={desired}, last applied={self._last_applied} ({reason})")
            self._last_applied = desired
            self._apply(desired, reason)

    def run_now(self, state: str) -> Dict[str, Any]:
        """Execute the on/off sequence immediately (the UI's test buttons).

        Works whether or not the schedule is enabled, which is also how the
        one-time TV pairing gets done.  Afterwards we mark the *current desired*
        state as applied so the tick loop treats the manual run as acknowledged
        and won't override it until the next scheduled transition.
        """
        if state not in ("on", "off"):
            raise ValueError("state must be 'on' or 'off'")
        with self._lock:
            result = self._apply(state, "manual")
            self._last_applied = self.desired_state()
        return result

    # ---------------------------------------------------------------- actions

    def _apply(self, state: str, reason: str) -> Dict[str, Any]:
        """Run one transition. Caller must hold self._lock.

        There is deliberately no automatic retry: a transition is considered
        handled once attempted, and the outcome (including any screen that
        failed to verify) is logged and shown in the UI.  Retrying on a timer
        would mean fighting the user if they'd since stopped the show by hand;
        the Run On/Off Now buttons are the retry.
        """
        started = time.time()
        result = self._do_on(reason) if state == "on" else self._do_off(reason)
        result["duration_s"] = round(time.time() - started, 1)
        result["at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._last_action = result
        ok = "OK" if result["ok"] else "PROBLEM"
        logger.info(f"[schedule] {state.upper()} ({reason}) -> {ok} in {result['duration_s']}s: {result['screens']}")
        self.socketio.emit("schedule_status", self.status())
        return result

    def _do_on(self, reason: str) -> Dict[str, Any]:
        sc = self.slideshow_controller
        result: Dict[str, Any] = {"action": "on", "reason": reason, "ok": False, "show": None, "screens": {}}

        if sc.is_playlist_running or sc.is_slideshow_running:
            result["show"] = "already running"
        else:
            if not sc.chromecast_manager.get_enabled_devices() and self.discover_fn:
                logger.info("[schedule] no screens known yet, running discovery first")
                result["discovery"] = "ran" if self.discover_fn() else "failed"
                if not sc.chromecast_manager.get_enabled_devices():
                    logger.error("[schedule] discovery found no enabled screens")

            # Start whatever the user last loaded — a single show or the
            # playlist — rather than always forcing the playlist.
            kind = (self.settings_manager.get_setting("loaded_kind") or "playlist").lower()
            if kind == "show":
                path = self.settings_manager.get_selected_directory()
                label = path.rstrip("/").split("/")[-1] or path
                res = sc.play_single_show(path)
                started, what = res.get("success"), f"show started: {label}"
            elif kind == "all_shows" and self.all_shows_fn:
                items = self.all_shows_fn()
                res = sc.start_playlist(items=items, name="All Shows") if items \
                    else {"success": False, "error": "no shows in any saved playlist"}
                started, what = res.get("success"), f"all shows started ({len(items)} shows)"
            else:
                res = sc.play_current_playlist()
                started, what = res.get("success"), "playlist started"

            if started:
                result["show"] = what
                self.socketio.emit("playlist_started")
                self.socketio.emit("playlist_status_update", sc.get_playlist_status())
            else:
                result["show"] = f"could not start ({kind}): {res.get('error')}"
                logger.error(f"[schedule] {result['show']}")
                # Still verify below — the screens may have been on already.

        # Casting wakes the TVs; give the panels a moment before checking.
        time.sleep(WAKE_SETTLE_SECONDS)
        for d in self.settings_manager.get_enabled_devices():
            result["screens"][d["name"]] = tv_power.wait_for_state(d["host"], "on", timeout=VERIFY_TIMEOUT_SECONDS)

        result["ok"] = bool(result["screens"]) and all(s == "on" for s in result["screens"].values()) \
            and not str(result["show"]).startswith("could not")
        return result

    def _do_off(self, reason: str) -> Dict[str, Any]:
        sc = self.slideshow_controller
        result: Dict[str, Any] = {"action": "off", "reason": reason, "ok": False, "show": None, "screens": {}, "errors": {}}

        was_running = sc.is_playlist_running or sc.is_slideshow_running
        sc.stop_playlist()
        sc.stop_slideshow()
        result["show"] = "stopped" if was_running else "was not running"
        self.socketio.emit("playlist_stopped")
        self.socketio.emit("playlist_status_update", sc.get_playlist_status())
        time.sleep(1)

        devices = self.settings_manager.get_enabled_devices()
        for d in devices:
            r = tv_power.power_off(d["host"], d["uuid"], self.token_dir, d["name"])
            if r["error"]:
                result["errors"][d["name"]] = r["error"]
        for d in devices:
            result["screens"][d["name"]] = tv_power.wait_for_state(d["host"], "standby", timeout=VERIFY_TIMEOUT_SECONDS)

        result["ok"] = bool(devices) and all(s == "standby" for s in result["screens"].values())
        return result
