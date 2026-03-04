import threading
import time
import logging
from typing import List, Dict, Any, Optional
import socket
import json

# _OrigPopen and _OrigDEVNULL are injected by app.py with the UNPATCHED
# stdlib Popen class (saved before gevent monkey-patching).  For standalone
# / non-gevent usage, fall back to the regular subprocess module.
import subprocess as _subprocess

# These will be overwritten by app.py with the real, unpatched versions.
# Defaults here allow standalone usage without gevent.
_OrigPopen = _subprocess.Popen
_OrigDEVNULL = _subprocess.DEVNULL

try:
    import gevent
    GEVENT_AVAILABLE = True
except ImportError:
    GEVENT_AVAILABLE = False

# Note: CATT/pychromecast operations are now handled via subprocess to avoid asyncio threading conflicts
# No direct imports needed here - all operations go through chromecast_subprocess.py

from settings_manager import SettingsManager


def _subprocess_in_thread(args, timeout, logger):
    """Run a subprocess entirely in a real OS thread.

    This function must NOT call any gevent APIs.  It uses the unpatched
    subprocess module and regular time.sleep so that gevent's event loop
    is never involved in child-process management (no SIGCHLD conflicts,
    no patched os.waitpid deadlocks).
    """
    import os
    import tempfile

    cmd_label = ' '.join(args[2:4]) if len(args) > 3 else ' '.join(args)
    logger.info(f"[SUBPROCESS] Starting: {cmd_label}")

    tmp_fd, tmp_path = tempfile.mkstemp(suffix='.json')
    tmp_file = os.fdopen(tmp_fd, 'w')
    proc = None
    start_time = time.time()
    try:
        proc = _OrigPopen(args, stdout=tmp_file, stderr=_OrigDEVNULL)
        tmp_file.close()
        tmp_file = None
        logger.info(f"[SUBPROCESS] PID {proc.pid} launched for: {cmd_label}")

        deadline = start_time + timeout
        while proc.poll() is None:
            if time.time() > deadline:
                logger.warning(f"[SUBPROCESS] PID {proc.pid} timed out after {timeout}s, killing")
                proc.kill()
                proc.wait()
                return None, "subprocess timed out"
            time.sleep(0.5)  # Real sleep, NOT gevent.sleep

        elapsed = time.time() - start_time
        logger.info(f"[SUBPROCESS] PID {proc.pid} exited rc={proc.returncode} in {elapsed:.1f}s")

        with open(tmp_path, 'r') as f:
            output = f.read().strip()

        if proc.returncode == 0 and output:
            return json.loads(output), None
        else:
            return None, f"subprocess failed (rc={proc.returncode})"
    except BaseException as e:
        if proc and proc.poll() is None:
            logger.warning(f"[SUBPROCESS] Killing PID {proc.pid} due to {type(e).__name__}")
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
        # Re-raise GreenletExit so gevent can clean up the greenlet
        if isinstance(e, Exception):
            return None, str(e)
        raise
    finally:
        if tmp_file and not tmp_file.closed:
            tmp_file.close()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


class ChromecastManager:
    def __init__(self, settings_manager: SettingsManager):
        self.settings_manager = settings_manager
        self.logger = logging.getLogger(__name__)
        self.discovered_devices = {}
        self.active_devices = {}
        self._discovery_running = False
        self._discovery_thread = None

    def _run_subprocess(self, args, timeout=15):
        """Run a subprocess in a real OS thread to avoid gevent hub deadlocks.

        gevent's SIGCHLD handler and patched os.waitpid deadlock the hub when
        multiple child processes run concurrently.  By offloading to a real
        thread via gevent's threadpool, subprocess operations are completely
        outside the event loop.
        """
        if GEVENT_AVAILABLE:
            return gevent.get_hub().threadpool.apply(
                _subprocess_in_thread, (args, timeout, self.logger))
        else:
            return _subprocess_in_thread(args, timeout, self.logger)

    def discover_devices(self, timeout: int = 5) -> List[Dict[str, Any]]:
        """Discover Chromecast devices using subprocess to avoid threading issues."""
        self.logger.info("Starting Chromecast device discovery via subprocess...")

        try:
            import os
            script_path = os.path.join(os.path.dirname(__file__), 'chromecast_subprocess.py')
            data, error = self._run_subprocess(
                ['python3', script_path, 'discover'],
                timeout=timeout + 10
            )

            if data and data.get('success'):
                devices = data.get('devices', [])
                for device in devices:
                    self.discovered_devices[device['uuid']] = device
                    self.settings_manager.save_device(
                        device['uuid'],
                        device['name'],
                        device['host'],
                        device['port']
                    )
                self.logger.info(f"Discovered {len(devices)} Chromecast devices")
                return devices
            else:
                self.logger.error(f"Discovery failed: {error or data.get('error', 'unknown')}")
                return []

        except Exception as e:
            self.logger.error(f"Error during device discovery: {e}")
            return []

    def start_discovery(self):
        """Start continuous device discovery in background."""
        if self._discovery_running:
            return

        self._discovery_running = True
        self._discovery_thread = threading.Thread(target=self._discovery_loop, daemon=True)
        self._discovery_thread.start()
        self.logger.info("Started continuous device discovery")

    def stop_discovery(self):
        """Stop continuous device discovery."""
        self._discovery_running = False
        if self._discovery_thread:
            self._discovery_thread.join(timeout=5)
        self.logger.info("Stopped device discovery")

    def _discovery_loop(self):
        """Background loop for continuous device discovery."""
        while self._discovery_running:
            try:
                self.discover_devices(timeout=3)
                time.sleep(30)  # Discover every 30 seconds
            except Exception as e:
                self.logger.error(f"Error in discovery loop: {e}")
                time.sleep(10)
            except KeyboardInterrupt:
                break

    def get_all_devices(self) -> List[Dict[str, Any]]:
        """Get all discovered devices with their current status."""
        devices = list(self.discovered_devices.values())

        # Update with saved device settings
        saved_devices = self.settings_manager.get_all_devices()
        device_settings = {d['uuid']: d for d in saved_devices}

        for device in devices:
            uuid = device['uuid']
            if uuid in device_settings:
                device['enabled'] = bool(device_settings[uuid]['enabled'])
                self.logger.info(f"Device {uuid} ({device['name']}) enabled status from DB: {device['enabled']}")
            else:
                # New devices default to disabled - user must explicitly enable them
                device['enabled'] = False
                self.logger.info(f"Device {uuid} ({device['name']}) not in DB, defaulting to disabled")

        return devices

    def get_enabled_devices(self) -> List[Dict[str, Any]]:
        """Get only enabled devices."""
        all_devices = self.get_all_devices()
        return [d for d in all_devices if d.get('enabled', False)]

    def get_device_by_uuid(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Get a specific device by UUID."""
        return self.discovered_devices.get(uuid)

    def connect_to_device(self, uuid: str) -> Optional[bool]:
        """Check if device is available (simplified since we use subprocess for operations)."""
        device = self.get_device_by_uuid(uuid)
        if not device:
            self.logger.error(f"Device {uuid} not found")
            return None

        # Since we use subprocess for actual operations, just return True if device exists
        return True

    def send_image_to_device(self, uuid: str, image_url: str) -> bool:
        """Send an image to a specific Chromecast device using subprocess."""
        try:
            device = self.get_device_by_uuid(uuid)
            if not device:
                self.logger.error(f"Device {uuid} not found")
                return False

            import os
            script_path = os.path.join(os.path.dirname(__file__), 'chromecast_subprocess.py')
            data, error = self._run_subprocess([
                'python3', script_path, 'send_image',
                '--device-name', device['name'],
                '--image-url', image_url
            ], timeout=15)

            if data and data.get('success'):
                self.logger.info(f"Sent image {image_url} to {device['name']}")
                return True
            else:
                self.logger.error(f"Failed to send image to {device['name']}: {error or (data.get('error') if data else 'unknown')}")
                return False

        except Exception as e:
            device = self.get_device_by_uuid(uuid)
            device_name = device['name'] if device else uuid
            self.logger.error(f"Failed to send image to {device_name}: {e}")
            return False

    def send_image_to_multiple_devices(self, device_uuids: List[str], image_urls: List[str]) -> Dict[str, bool]:
        """Send different images to multiple devices using real OS threads.

        Each device gets its own thread (via gevent threadpool) so subprocess
        calls never interact with the gevent event loop.
        """
        results = {}

        self.logger.info(f"[MULTI-SEND] Sending to {len(device_uuids)} devices")

        # Ensure we have enough images for all devices
        if len(image_urls) < len(device_uuids):
            repeated_urls = []
            for i, uuid in enumerate(device_uuids):
                repeated_urls.append(image_urls[i % len(image_urls)])
            image_urls = repeated_urls

        def send_to_device(uuid, url):
            results[uuid] = self.send_image_to_device(uuid, url)

        if GEVENT_AVAILABLE:
            # Use real OS threads via gevent's threadpool.  Each call to
            # _run_subprocess already uses threadpool.apply(), so we just
            # spawn greenlets that will each block on their thread.
            greenlets = [gevent.spawn(send_to_device, uuid, url)
                         for uuid, url in zip(device_uuids, image_urls)]
            self.logger.info(f"[MULTI-SEND] Spawned {len(greenlets)} greenlets, waiting with joinall(timeout=30)")
            gevent.joinall(greenlets, timeout=30)
            killed = 0
            for g in greenlets:
                if not g.dead:
                    g.kill(block=False)
                    killed += 1
            if killed:
                self.logger.warning(f"[MULTI-SEND] Killed {killed} timed-out greenlets")
            self.logger.info(f"[MULTI-SEND] Done: {sum(1 for v in results.values() if v)}/{len(device_uuids)} successful")
        else:
            for uuid, url in zip(device_uuids, image_urls):
                thread = threading.Thread(target=send_to_device, args=(uuid, url))
                threads = []
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        return results

    def disconnect_device(self, uuid: str):
        """Disconnect from a specific device."""
        if uuid in self.active_devices:
            try:
                cast = self.active_devices[uuid]
                cast.disconnect()
                del self.active_devices[uuid]
                self.logger.info(f"Disconnected from device {uuid}")
            except Exception as e:
                self.logger.error(f"Error disconnecting from device {uuid}: {e}")

    def disconnect_all_devices(self):
        """Disconnect from all active devices."""
        for uuid in list(self.active_devices.keys()):
            self.disconnect_device(uuid)

    def get_device_status(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a device using subprocess."""
        try:
            device = self.get_device_by_uuid(uuid)
            if not device:
                return None

            import os

            script_path = os.path.join(os.path.dirname(__file__), 'chromecast_subprocess.py')
            data, error = self._run_subprocess([
                'python3', script_path, 'get_status',
                '--device-name', device['name']
            ], timeout=10)

            if data and data.get('success'):
                status_info = data.get('status', {})
                return {
                    'uuid': uuid,
                    'connected': status_info.get('connected', False),
                    'app_name': status_info.get('app_name', 'Unknown'),
                    'status': status_info.get('status', 'Unknown')
                }
            else:
                self.logger.error(f"Status check failed: {error or (data.get('error') if data else 'unknown')}")
                return None

        except Exception as e:
            self.logger.error(f"Error getting status for device {uuid}: {e}")
            return None
