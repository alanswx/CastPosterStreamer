"""Samsung Tizen TV power control for the scheduled on/off feature.

The barn screens are Samsung QLEDs with Chromecast built in.  Each one serves
two protocols on the same IP:

  - Google Cast on 8008/8009 (what the slideshow already uses)
  - Samsung's Tizen remote API on 8001 (REST) / 8002 (TLS WebSocket)

Power ON is deliberately NOT handled here: casting to a Chromecast-built-in TV
wakes it, so the scheduler simply starts the playlist.  (Wake-on-LAN was tested
and does not work on all four screens; cast-wake does.)

This module only does two things:
  - read PowerState from the REST endpoint ("on" / "standby" / None if unreachable)
  - send KEY_POWER over the Tizen WebSocket to put a TV into standby

KEY_POWER is a *toggle*, so power_off() always reads the state first and only
sends the key when the TV is actually on.

The TVs require "IP Remote" to be enabled (Settings > General > Network >
Expert Settings) or the WebSocket command is rejected.  The first KEY_POWER to
a given TV pops an "Allow?" dialog on-screen; once accepted, samsungtvws saves
a token to token_dir and later calls are silent.
"""

import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

REST_PORT = 8001
WS_PORT = 8002
REMOTE_NAME = "Posters"
# Generous: on first contact the TV shows an "Allow?" dialog and the person in
# the barn needs time to reach the remote.  Paired TVs answer in ~1s regardless.
WS_TIMEOUT = 30


def get_power_state(host: str, timeout: float = 3.0) -> Optional[str]:
    """Return the TV's PowerState ("on" / "standby"), or None if unreachable."""
    try:
        with urllib.request.urlopen(f"http://{host}:{REST_PORT}/api/v2/", timeout=timeout) as r:
            info = json.load(r)
        return info.get("device", {}).get("PowerState")
    except Exception as e:
        logger.debug(f"PowerState read failed for {host}: {e}")
        return None


def wait_for_state(host: str, want: str, timeout: float = 20.0, interval: float = 2.0) -> Optional[str]:
    """Poll until the TV reports `want` or timeout elapses. Returns the last state seen."""
    deadline = time.time() + timeout
    state = get_power_state(host)
    while state != want and time.time() < deadline:
        time.sleep(interval)
        state = get_power_state(host)
    return state


def _token_path(token_dir: Path, uuid: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", uuid)
    return str(token_dir / f"{safe}.token")


def power_off(host: str, uuid: str, token_dir: Path, name: str = "") -> Dict[str, Any]:
    """Put one TV into standby. Returns a result dict; never raises."""
    label = name or host
    result: Dict[str, Any] = {"host": host, "name": label, "before": None, "sent": False, "error": None}

    state = get_power_state(host)
    result["before"] = state
    if state is None:
        result["error"] = "unreachable"
        logger.warning(f"[power] {label}: unreachable, skipping power off")
        return result
    if state != "on":
        logger.info(f"[power] {label}: already {state}, nothing to do")
        return result

    try:
        # Imported lazily so a missing package only breaks power-off, not the whole app.
        from samsungtvws import SamsungTVWS

        token_dir.mkdir(parents=True, exist_ok=True)
        tv = SamsungTVWS(
            host=host,
            port=WS_PORT,
            token_file=_token_path(token_dir, uuid),
            name=REMOTE_NAME,
            timeout=WS_TIMEOUT,
        )
        tv.send_key("KEY_POWER")
        tv.close()
        result["sent"] = True
        logger.info(f"[power] {label}: KEY_POWER sent")
    except Exception as e:
        result["error"] = str(e) or type(e).__name__
        logger.error(f"[power] {label}: KEY_POWER failed: {result['error']}")

    return result
