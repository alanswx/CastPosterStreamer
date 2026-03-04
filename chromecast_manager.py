import threading
import time
import logging
from typing import List, Dict, Any, Optional
import socket
import json

import subprocess as _subprocess  # Use stdlib subprocess (monkey-patched by gevent if available)

try:
    import gevent
    GEVENT_AVAILABLE = True
except ImportError:
    GEVENT_AVAILABLE = False

# Note: CATT/pychromecast operations are now handled via subprocess to avoid asyncio threading conflicts
# No direct imports needed here - all operations go through chromecast_subprocess.py

from settings_manager import SettingsManager


class ChromecastManager:
    def __init__(self, settings_manager: SettingsManager):
        self.settings_manager = settings_manager
        self.logger = logging.getLogger(__name__)
        self.discovered_devices = {}
        self.active_devices = {}
        self._discovery_running = False
        self._discovery_thread = None
    
    def discover_devices(self, timeout: int = 5) -> List[Dict[str, Any]]:
        """Discover Chromecast devices using subprocess to avoid threading issues."""
        self.logger.info("Starting Chromecast device discovery via subprocess...")
        
        try:
            import os

            # Use subprocess to avoid asyncio threading conflicts
            script_path = os.path.join(os.path.dirname(__file__), 'chromecast_subprocess.py')
            result = _subprocess.run([
                'python3', script_path, 'discover'
            ], capture_output=True, text=True, timeout=timeout + 10)
            
            if result.returncode == 0:
                data = json.loads(result.stdout.strip())
                if data.get('success'):
                    devices = data.get('devices', [])
                    
                    # Update internal state and database
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
                    self.logger.error(f"Discovery subprocess error: {data.get('error')}")
                    return []
            else:
                self.logger.error(f"Discovery subprocess failed: {result.stderr}")
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
        proc = None
        try:
            device = self.get_device_by_uuid(uuid)
            if not device:
                self.logger.error(f"Device {uuid} not found")
                return False

            import os

            script_path = os.path.join(os.path.dirname(__file__), 'chromecast_subprocess.py')
            proc = _subprocess.Popen([
                'python3', script_path, 'send_image',
                '--device-name', device['name'],
                '--image-url', image_url
            ], stdout=_subprocess.PIPE, stderr=_subprocess.PIPE)

            # Poll for completion with explicit yields to keep the gevent hub alive.
            # Do NOT use proc.communicate(timeout=N) — it blocks the hub in py2app bundles.
            deadline = time.time() + 15
            while proc.poll() is None:
                if time.time() > deadline:
                    proc.kill()
                    proc.wait()
                    self.logger.error(f"Subprocess timed out sending to {device['name']}")
                    return False
                if GEVENT_AVAILABLE:
                    gevent.sleep(0.5)
                else:
                    time.sleep(0.5)

            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            proc.stdout.close()
            proc.stderr.close()

            if proc.returncode == 0:
                data = json.loads(stdout.decode().strip())
                if data.get('success'):
                    self.logger.info(f"Sent image {image_url} to {device['name']}")
                    return True
                else:
                    self.logger.error(f"Failed to send image: {data.get('error')}")
                    return False
            else:
                self.logger.error(f"Image send subprocess failed: {stderr.decode()}")
                return False

        except Exception as e:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                    proc.wait()
                except Exception:
                    pass
            device = self.get_device_by_uuid(uuid)
            device_name = device['name'] if device else uuid
            self.logger.error(f"Failed to send image to {device_name}: {e}")
            return False
    
    def send_image_to_multiple_devices(self, device_uuids: List[str], image_urls: List[str]) -> Dict[str, bool]:
        """Send different images to multiple devices simultaneously."""
        results = {}
        threads = []
        
        # Ensure we have enough images for all devices
        if len(image_urls) < len(device_uuids):
            # Repeat images if we don't have enough
            repeated_urls = []
            for i, uuid in enumerate(device_uuids):
                repeated_urls.append(image_urls[i % len(image_urls)])
            image_urls = repeated_urls
        
        def send_to_device(uuid, url):
            results[uuid] = self.send_image_to_device(uuid, url)

        if GEVENT_AVAILABLE:
            # Use gevent greenlets so joinall properly yields to the event loop
            greenlets = [gevent.spawn(send_to_device, uuid, url)
                         for uuid, url in zip(device_uuids, image_urls)]
            gevent.joinall(greenlets, timeout=10)
            # Kill any greenlets that didn't finish in time to prevent
            # orphaned subprocesses from accumulating and wedging the hub
            for g in greenlets:
                if not g.dead:
                    g.kill(block=False)
        else:
            # Fallback to threading
            for uuid, url in zip(device_uuids, image_urls):
                thread = threading.Thread(target=send_to_device, args=(uuid, url))
                threads.append(thread)
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        
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

            # Use subprocess to avoid asyncio threading conflicts
            script_path = os.path.join(os.path.dirname(__file__), 'chromecast_subprocess.py')
            result = _subprocess.run([
                'python3', script_path, 'get_status',
                '--device-name', device['name']
            ], capture_output=True, text=True, timeout=10)
            
            if result.returncode == 0:
                data = json.loads(result.stdout.strip())
                if data.get('success'):
                    status_info = data.get('status', {})
                    return {
                        'uuid': uuid,
                        'connected': status_info.get('connected', False),
                        'app_name': status_info.get('app_name', 'Unknown'),
                        'status': status_info.get('status', 'Unknown')
                    }
                else:
                    self.logger.error(f"Status check failed: {data.get('error')}")
                    return None
            else:
                self.logger.error(f"Status subprocess failed: {result.stderr}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error getting status for device {uuid}: {e}")
            return None