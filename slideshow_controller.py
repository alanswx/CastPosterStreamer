import time
import os
import logging
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image
import hashlib

try:
    import gevent
    import gevent.lock
    GEVENT_AVAILABLE = True
except ImportError:
    GEVENT_AVAILABLE = False

from settings_manager import SettingsManager
from chromecast_manager import ChromecastManager
from image_server import get_image_server


class SlideshowController:
    """Controls the slideshow timing and image distribution."""
    
    def __init__(self, settings_manager: SettingsManager, chromecast_manager: ChromecastManager):
        self.settings_manager = settings_manager
        self.chromecast_manager = chromecast_manager
        self.socketio = None
        self.logger = logging.getLogger(__name__)
    
    def init_app(self, socketio, app=None):
        self.socketio = socketio
        self.app = app
        
        self.image_server = get_image_server()
        self.slideshow_thread = None
        self.is_slideshow_running = False
        self.current_image_index = 0
        self.current_images = {}  # device_uuid -> current_image_path
        
        # Playlist functionality
        self.playlist_thread = None
        self.is_playlist_running = False
        self.is_playlist_paused = False
        self.current_playlist_index = 0
        self.playlist_start_time = None
        self.playlist_pause_time = None
        self.playlist_accumulated_time = 0
        self.skip_requested = False
        
        # Use gevent lock if available, otherwise regular threading lock
        if GEVENT_AVAILABLE:
            self._lock = gevent.lock.BoundedSemaphore()
        else:
            self._lock = threading.Lock()
        self.supported_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
    def _sleep(self, seconds):
        """Sleep using gevent if available, otherwise regular time.sleep"""
        if GEVENT_AVAILABLE:
            gevent.sleep(seconds)
        else:
            time.sleep(seconds)
    
    def get_images_in_directory(self, directory: str) -> List[str]:
        """Get all supported image files in the specified directory."""
        if not os.path.isdir(directory):
            self.logger.error(f"Directory does not exist: {directory}")
            return []
        
        images = []
        try:
            for filename in os.listdir(directory):
                file_path = os.path.join(directory, filename)
                
                if os.path.isfile(file_path):
                    _, ext = os.path.splitext(filename.lower())
                    if ext in self.supported_extensions:
                        images.append(file_path)
            
            # Sort images by filename for consistent ordering
            images.sort()
            self.logger.debug(f"Found {len(images)} images in {directory}")
            return images
            
        except Exception as e:
            self.logger.error(f"Error scanning directory {directory}: {e}")
            return []
    
    def generate_thumbnail(self, image_path: str, thumbnail_dir: str = None) -> Optional[str]:
        """Generate a thumbnail for the given image."""
        if thumbnail_dir is None:
            thumbnail_dir = str(self.settings_manager.get_thumbnail_dir())
        
        # Create thumbnail directory if it doesn't exist
        os.makedirs(thumbnail_dir, exist_ok=True)
        
        try:
            # Create thumbnail filename based on original path hash
            image_hash = hashlib.md5(image_path.encode()).hexdigest()
            thumbnail_filename = f"{image_hash}.jpg"
            thumbnail_path = os.path.join(thumbnail_dir, thumbnail_filename)
            
            # Check if thumbnail already exists
            if os.path.exists(thumbnail_path):
                return thumbnail_path
            
            # Generate thumbnail
            size = self.settings_manager.get_thumbnail_size()
            with Image.open(image_path) as img:
                # Convert to RGB if necessary (for PNG with transparency, etc.)
                if img.mode not in ('RGB', 'L'):
                    img = img.convert('RGB')
                
                # Create thumbnail maintaining aspect ratio
                img.thumbnail((size, size), Image.Resampling.LANCZOS)
                img.save(thumbnail_path, 'JPEG', quality=85, optimize=True)
            
            self.logger.debug(f"Generated thumbnail: {thumbnail_path}")
            return thumbnail_path
            
        except Exception as e:
            self.logger.error(f"Error generating thumbnail for {image_path}: {e}")
            return None
    
    def start_slideshow(self):
        """Start the slideshow with the current settings."""
        with self._lock:
            if self.is_slideshow_running:
                self.logger.warning("Slideshow is already running")
                return False
                
            if self.is_playlist_running:
                self.logger.warning("Cannot start regular slideshow while playlist is running")
                if self.socketio:
                    self.socketio.emit('error', {'message': 'Cannot start regular slideshow while playlist is running. Stop playlist first.'})
                return False
        
        # Get enabled devices
        enabled_devices = self.chromecast_manager.get_enabled_devices()
        if not enabled_devices:
            self.logger.error("No enabled Chromecast devices found")
            if self.socketio:
                self.socketio.emit('error', {'message': 'No enabled Chromecast devices found'})
            return False
        
        # Get images from selected directory
        directory = self.settings_manager.get_selected_directory()
        images = self.get_images_in_directory(directory)
        if not images:
            self.logger.error(f"No images found in directory: {directory}")
            if self.socketio:
                self.socketio.emit('error', {'message': f'No images found in directory: {directory}'})
            return False
        
        # Start image server
        self.image_server.set_image_directory(directory)
        if not self.image_server.start():
            self.logger.error("Failed to start image server")
            if self.socketio:
                self.socketio.emit('error', {'message': 'Failed to start image server'})
            return False
        
        # Start slideshow thread
        with self._lock:
            self.is_slideshow_running = True
            self.current_image_index = 0
            self.slideshow_thread = gevent.spawn(self._slideshow_loop)
        
        self.logger.info(f"Started slideshow with {len(images)} images and {len(enabled_devices)} devices")
        if self.socketio:
            self.socketio.emit('slideshow_status', {
                'running': True,
                'images_count': len(images),
                'devices_count': len(enabled_devices)
            })
        
        return True
    
    def stop_slideshow(self):
        """Stop the slideshow."""
        with self._lock:
            if not self.is_slideshow_running:
                return
            
            self.is_slideshow_running = False
        
        # Wait for slideshow thread to finish
        if self.slideshow_thread:
            self.slideshow_thread.join(timeout=5)
        
        # Stop image server
        self.image_server.stop()
        
        # Clear current images
        self.current_images.clear()
        
        self.logger.info("Slideshow stopped")
        if self.socketio:
            self.socketio.emit('slideshow_stopped')
    
    def _slideshow_loop(self):
        """Main slideshow loop that runs in a background thread."""
        directory = self.settings_manager.get_selected_directory()
        
        # Send initial images
        first_run = True
        
        while self.is_slideshow_running:
            try:
                # Get current images and devices
                images = self.get_images_in_directory(directory)
                enabled_devices = self.chromecast_manager.get_enabled_devices()
                
                if not images or not enabled_devices:
                    self.logger.warning("No images or devices available, stopping slideshow")
                    # Notify frontend that slideshow stopped
                    if self.socketio:
                        self.socketio.emit('slideshow_stopped')
                    break
                
                # Check if rotation is enabled
                rotation_enabled = self.settings_manager.is_rotation_enabled()
                
                # Distribute images to devices (always on first run, then only if rotation enabled)
                if first_run or rotation_enabled:
                    self._distribute_images_to_devices(images, enabled_devices)
                    first_run = False
                
                # Wait for the specified interval
                interval = self.settings_manager.get_slideshow_interval()
                
                # If rotation is disabled, wait longer to reduce CPU usage
                if not rotation_enabled:
                    self._sleep(max(interval, 10))  # At least 10 seconds between checks
                else:
                    self._sleep(interval)
                
            except Exception as e:
                self.logger.error(f"Error in slideshow loop: {e}")
                if self.socketio:
                    self.socketio.emit('error', {'message': f'Slideshow error: {str(e)}'})
                self._sleep(5)  # Wait before retrying
        
        # Slideshow stopped
        with self._lock:
            self.is_slideshow_running = False
        
        # Always notify frontend when slideshow loop ends
        if self.socketio:
            self.socketio.emit('slideshow_stopped')
    
    def _distribute_images_to_devices(self, images: List[str], devices: List[Dict[str, Any]]):
        """Distribute images to devices ensuring each gets a unique image."""
        if not images or not devices:
            return
        
        # Calculate how many images to advance based on number of devices
        num_devices = len(devices)
        
        # Get images for this round (cycling if necessary)
        current_images = []
        for i in range(num_devices):
            image_index = (self.current_image_index + i) % len(images)
            current_images.append(images[image_index])
        
        # Generate URLs for images
        image_urls = []
        for image_path in current_images:
            url = self.image_server.get_image_url(os.path.basename(image_path))
            if url:
                image_urls.append(url)
            else:
                self.logger.error(f"Could not generate URL for image: {image_path}")
        
        if not image_urls:
            self.logger.error("No valid image URLs generated")
            return
        
        # Send images to devices (use background task to avoid blocking the gevent loop)
        device_uuids = [device['uuid'] for device in devices]
        if self.socketio:
            # When using socketio, handle everything in the background task
            rotation_enabled = self.settings_manager.is_rotation_enabled()
            self.socketio.start_background_task(self._send_images_background, device_uuids, image_urls, current_images, num_devices, len(images), rotation_enabled)
            return
        else:
            # Fallback for non-socketio usage (direct execution)
            results = self.chromecast_manager.send_image_to_multiple_devices(device_uuids, image_urls)
            
            # Update current images tracking
            for i, (uuid, success) in enumerate(results.items()):
                if success and i < len(current_images):
                    self.current_images[uuid] = current_images[i]
            
            # Only advance to next set of images if rotation is enabled
            rotation_enabled = self.settings_manager.is_rotation_enabled()
            if rotation_enabled:
                self.current_image_index = (self.current_image_index + num_devices) % len(images)
            
            rotation_status = "with rotation" if rotation_enabled else "without rotation"
            self.logger.info(f"Distributed {len(image_urls)} images to {len(devices)} devices "
                            f"({sum(results.values())} successful) {rotation_status}")
    
    def _send_images_background(self, device_uuids: List[str], image_urls: List[str], current_images: List[str], num_devices: int, total_images: int, rotation_enabled: bool):
        """Background task to send images to devices without blocking the main loop."""
        try:
            results = self.chromecast_manager.send_image_to_multiple_devices(device_uuids, image_urls)
            
            # Update current images tracking
            for i, (uuid, success) in enumerate(results.items()):
                if success and i < len(current_images):
                    self.current_images[uuid] = current_images[i]
            
            # Only advance to next set of images if rotation is enabled
            if rotation_enabled:
                self.current_image_index = (self.current_image_index + num_devices) % total_images
            
            # Calculate success count for logging and WebSocket updates
            successful_count = sum(1 for success in results.values() if success)
            
            rotation_status = "with rotation" if rotation_enabled else "without rotation"
            self.logger.info(f"Distributed {len(image_urls)} images to {len(device_uuids)} devices ({successful_count} successful) {rotation_status}")
            
            # Emit update to WebSocket clients
            if self.socketio and self.app:
                with self.app.app_context():
                    self.socketio.emit('slideshow_update', {
                        'current_index': self.current_image_index,
                        'total_images': total_images,
                        'successful_devices': successful_count,
                        'total_devices': len(device_uuids),
                        'current_images': [os.path.basename(img) for img in current_images],
                        'rotation_enabled': rotation_enabled
                    })
        except Exception as e:
            self.logger.error(f"Error in background image distribution: {e}", exc_info=True)

    def is_running(self) -> bool:
        """Check if slideshow is currently running."""
        with self._lock:
            return self.is_slideshow_running
    
    def get_current_images(self) -> Dict[str, str]:
        """Get the current images being displayed on each device."""
        return {uuid: os.path.basename(path) for uuid, path in self.current_images.items()}
    
    def get_slideshow_status(self) -> Dict[str, Any]:
        """Get comprehensive slideshow status."""
        directory = self.settings_manager.get_selected_directory()
        images = self.get_images_in_directory(directory)
        enabled_devices = self.chromecast_manager.get_enabled_devices()
        
        return {
            'running': self.is_running(),
            'directory': directory,
            'images_count': len(images),
            'devices_count': len(enabled_devices),
            'interval': self.settings_manager.get_slideshow_interval(),
            'current_index': self.current_image_index,
            'current_images': self.get_current_images(),
            'server_running': self.image_server.is_running,
            'server_url': self.image_server.get_server_url()
        }
    
    def skip_to_next(self):
        """Manually skip to the next set of images."""
        if not self.is_running():
            return False
        
        try:
            directory = self.settings_manager.get_selected_directory()
            images = self.get_images_in_directory(directory)
            enabled_devices = self.chromecast_manager.get_enabled_devices()
            
            if images and enabled_devices:
                # Force advance to next images regardless of rotation setting
                num_devices = len(enabled_devices)
                self.current_image_index = (self.current_image_index + num_devices) % len(images)
                self._distribute_images_to_devices(images, enabled_devices)
                return True
                
        except Exception as e:
            self.logger.error(f"Error skipping to next images: {e}")
        
        return False
    
    def refresh_directory(self):
        """Refresh the image list from the current directory."""
        if self.is_running():
            directory = self.settings_manager.get_selected_directory()
            images = self.get_images_in_directory(directory)
            
            if self.socketio:
                self.socketio.emit('images_refreshed', {
                    'directory': directory,
                    'count': len(images),
                    'images': [os.path.basename(img) for img in images]
                })
            
            self.logger.info(f"Refreshed directory: {len(images)} images found")
    
    def cleanup(self):
        """Clean up resources when shutting down."""
        self.stop_slideshow()
        self.stop_playlist()
        if self.image_server.is_running:
            self.image_server.stop()
    
    # Playlist functionality
    def start_playlist(self) -> Dict[str, Any]:
        """Start playlist mode slideshow."""
        with self._lock:
            if self.is_playlist_running:
                return {'success': False, 'error': 'Playlist is already running'}
            
            if self.is_slideshow_running:
                return {'success': False, 'error': 'Regular slideshow is running. Stop it first.'}
        
        # Get playlist items
        playlist_items = self.settings_manager.get_playlist_items()
        valid_items = [item for item in playlist_items if item['is_valid']]
        
        if not valid_items:
            return {'success': False, 'error': 'No valid directories in playlist'}
        
        # Get enabled devices
        enabled_devices = self.chromecast_manager.get_enabled_devices()
        if not enabled_devices:
            return {'success': False, 'error': 'No enabled Chromecast devices found'}
        
        # Start playlist thread
        with self._lock:
            self.is_playlist_running = True
            self.is_playlist_paused = False
            self.current_playlist_index = 0
            self.playlist_start_time = time.time()
            self.playlist_pause_time = None
            self.playlist_accumulated_time = 0
            self.skip_requested = False
            self.playlist_thread = self.socketio.start_background_task(target=self._playlist_loop)
        
        self.logger.info(f"Started playlist with {len(valid_items)} valid items")
        return {'success': True}
    
    def stop_playlist(self):
        """Stop the playlist."""
        with self._lock:
            if not self.is_playlist_running:
                return
            
            self.is_playlist_running = False
            self.is_playlist_paused = False
            self.skip_requested = False
        
        # Wait for playlist thread to finish
        if self.playlist_thread:
            self.playlist_thread.join(timeout=5)
        
        # Stop image server
        self.image_server.stop()
        
        # Clear current images
        self.current_images.clear()
        
        self.logger.info("Playlist stopped")
        # Skip WebSocket emissions to avoid conflicts - let API endpoint handle stop notifications
    
    def toggle_playlist_pause(self):
        """Toggle pause state of the playlist."""
        with self._lock:
            if not self.is_playlist_running:
                return False
            
            if self.is_playlist_paused:
                # Resume
                if self.playlist_pause_time:
                    self.playlist_accumulated_time += time.time() - self.playlist_pause_time
                    self.playlist_pause_time = None
                self.is_playlist_paused = False
                self.logger.info("Playlist resumed")
            else:
                # Pause
                self.playlist_pause_time = time.time()
                self.is_playlist_paused = True
                self.logger.info("Playlist paused")
            
            # Skip WebSocket update to avoid conflicts with API endpoint
        
        return True
    
    def skip_playlist_item(self):
        """Skip to the next item in the playlist."""
        with self._lock:
            if not self.is_playlist_running:
                return False
            
            # Signal the playlist loop to skip to the next item
            self.skip_requested = True
            self.logger.info("Skip requested for playlist item")
        
        return True
    
    def _playlist_loop(self):
        """Main playlist loop that runs in a background thread."""
        playlist_items = self.settings_manager.get_playlist_items()
        valid_items = [item for item in playlist_items if item['is_valid']]
        
        if not valid_items:
            self.logger.error("No valid playlist items found")
            with self._lock:
                self.is_playlist_running = False
            return
        
        self.logger.info("Playlist loop - Outer loop started.")
        while self.is_playlist_running:
            try:
                # Get current playlist item
                current_item = valid_items[self.current_playlist_index % len(valid_items)]
                directory = current_item['directory_path']
                duration_minutes = current_item['duration_minutes']
                
                self.logger.info(f"Playlist item starting: {current_item['directory_name']} for {duration_minutes} min.")
                
                # Start slideshow for this directory
                if not self._start_directory_slideshow(directory):
                    self.logger.error(f"Failed to start slideshow for directory: {directory}, skipping.")
                    self.current_playlist_index = (self.current_playlist_index + 1) % len(valid_items)
                    self._sleep(1) # Avoid fast spinning loop on error
                    continue
                
                # Reset timing
                item_start_time = time.time()
                item_accumulated_time = 0
                self.current_item_start_time = item_start_time
                last_image_time = 0  # force immediate first image send

                # Run slideshow for specified duration
                self.logger.info("Playlist loop - Inner loop started.")
                while self.is_playlist_running:
                    if self.is_playlist_paused:
                        self.logger.info("Playlist is paused.")
                        self._sleep(1)
                        continue

                    current_time = time.time()
                    elapsed_time = (current_time - item_start_time) + item_accumulated_time
                    since_last_send = current_time - last_image_time
                    self.logger.info(f"[TICK] elapsed={elapsed_time:.1f}s  since_last_send={since_last_send:.1f}s")

                    if elapsed_time >= duration_minutes * 60 or self.skip_requested:
                        if self.skip_requested:
                            self.logger.info("Skip requested, breaking inner loop.")
                            self.skip_requested = False
                        else:
                            self.logger.info(f"Duration elapsed, breaking inner loop.")
                        break

                    interval = self.settings_manager.get_slideshow_interval()
                    rotation_on = self.settings_manager.is_rotation_enabled()
                    if rotation_on and since_last_send >= interval:
                        self.logger.info(f"[TICK] Rotation triggered (interval={interval}s)")
                        self.logger.info(f"[TICK] Getting enabled devices...")
                        enabled_devices = self.chromecast_manager.get_enabled_devices()
                        self.logger.info(f"[TICK] Got {len(enabled_devices)} enabled devices")
                        if enabled_devices:
                            self.logger.info(f"[TICK] Getting images from {directory}...")
                            images = self.get_images_in_directory(directory)
                            self.logger.info(f"[TICK] Got {len(images)} images")
                            if images:
                                self.logger.info(f"[TICK] Calling _distribute_images_to_devices...")
                                self._distribute_images_to_devices(images, enabled_devices)
                                self.logger.info(f"[TICK] _distribute_images_to_devices returned")
                                last_image_time = current_time

                    self.logger.info(f"[TICK] Sleeping 1s...")
                    self._sleep(1)
                    self.logger.info(f"[TICK] Woke up from sleep")
                    
                    if int(elapsed_time) % 2 == 0:
                        status = self.get_playlist_status()
                        self.logger.info(f"🔍 DEBUG: Attempting playlist_status_update emission. SocketIO exists: {self.socketio is not None}, App exists: {self.app is not None}")
                        if self.socketio and self.app:
                            try:
                                with self.app.app_context():
                                    self.logger.info(f"🔍 DEBUG: Inside app context, about to emit playlist_status_update. Status data: {status}")
                                    self.socketio.emit('playlist_status_update', status)
                                    self.logger.info("✅ DEBUG: playlist_status_update emission completed successfully")
                            except Exception as e:
                                self.logger.error(f"❌ DEBUG: Error during playlist_status_update emission: {e}", exc_info=True)
                        else:
                            self.logger.warning(f"⚠️ DEBUG: Cannot emit playlist_status_update - SocketIO: {self.socketio is not None}, App: {self.app is not None}")
                        self.logger.info(f"Periodic status update: running={status.get('running')}, time_remaining={status.get('time_remaining')}")
                    
                    if self.is_playlist_paused and self.playlist_pause_time:
                        item_accumulated_time += self.playlist_pause_time - item_start_time
                        item_start_time = time.time()
                        self.playlist_pause_time = None
                
                self.logger.info("Playlist loop - Inner loop finished.")
                if not self.is_playlist_running:
                    self.logger.warning("Playlist was stopped during inner loop execution.")
                    break # Exit outer loop as well

                # Move to next playlist item
                self.current_playlist_index = (self.current_playlist_index + 1) % len(valid_items)
                self.logger.info(f"Advancing to next playlist item, index: {self.current_playlist_index}")
                
            except Exception as e:
                self.logger.error(f"Error in playlist loop: {e}", exc_info=True)
                self._sleep(5)
        
        self.logger.info("Playlist loop - Outer loop finished.")
        
        # Cleanup
        with self._lock:
            self.is_playlist_running = False
        
        # Stop any running slideshow
        self.image_server.stop()
        self.current_images.clear()
        
        # Emit final status update to client
        final_status = self.get_playlist_status()
        self.logger.info(f"🔍 DEBUG: Attempting FINAL playlist_status_update emission. SocketIO exists: {self.socketio is not None}, App exists: {self.app is not None}")
        if self.socketio and self.app:
            try:
                with self.app.app_context():
                    self.logger.info(f"🔍 DEBUG: Inside app context, about to emit FINAL playlist_status_update. Status data: {final_status}")
                    self.socketio.emit('playlist_status_update', final_status)
                    self.logger.info("✅ DEBUG: FINAL playlist_status_update emission completed successfully")
            except Exception as e:
                self.logger.error(f"❌ DEBUG: Error during FINAL playlist_status_update emission: {e}", exc_info=True)
        else:
            self.logger.warning(f"⚠️ DEBUG: Cannot emit FINAL playlist_status_update - SocketIO: {self.socketio is not None}, App: {self.app is not None}")
        
        self.logger.info("Playlist loop finished.")
    
    def _start_directory_slideshow(self, directory: str) -> bool:
        """Start slideshow for a specific directory (used by playlist)."""
        # Get images from directory
        images = self.get_images_in_directory(directory)
        if not images:
            self.logger.error(f"No images found in directory: {directory}")
            return False
        
        # Start image server for this directory
        self.image_server.set_image_directory(directory)
        if not self.image_server.start():
            self.logger.error("Failed to start image server")
            return False

        # Don't send initial images here - the inner loop handles it
        # via last_image_time = 0 which triggers an immediate first send
        enabled_devices = self.chromecast_manager.get_enabled_devices()
        if enabled_devices:
            self.logger.info(f"Starting playlist directory: {os.path.basename(directory)} with {len(images)} images to {len(enabled_devices)} devices")

        return True
    
    def get_playlist_status(self) -> Dict[str, Any]:
        """Get current playlist execution status."""
        if not self.is_playlist_running:
            self.logger.debug("get_playlist_status: is_playlist_running is False")
            return {
                'running': False,
                'paused': False,
                'current_item': None,
                'time_remaining': 0,
                'total_items': 0
            }
        
        playlist_items = self.settings_manager.get_playlist_items()
        valid_items = [item for item in playlist_items if item['is_valid']]
        
        if not valid_items:
            self.logger.debug("get_playlist_status: no valid items found")
            return {
                'running': False,
                'paused': False,
                'current_item': None,
                'time_remaining': 0,
                'total_items': 0
            }
        
        current_item = valid_items[self.current_playlist_index % len(valid_items)]
        self.logger.debug(f"get_playlist_status: returning running=True, current_item={current_item['directory_name']}")
        
        # Calculate time remaining for current item - this needs to be based on individual item timing
        # For now, use a simple calculation based on start time
        if hasattr(self, 'current_item_start_time'):
            if self.is_playlist_paused and self.playlist_pause_time:
                elapsed_time = self.playlist_pause_time - self.current_item_start_time
            else:
                elapsed_time = time.time() - self.current_item_start_time
        else:
            elapsed_time = 0
        
        duration_seconds = current_item['duration_minutes'] * 60
        time_remaining = max(0, duration_seconds - elapsed_time)
        
        return {
            'running': True,
            'paused': self.is_playlist_paused,
            'current_item': current_item,
            'current_index': self.current_playlist_index,
            'time_remaining': int(time_remaining),
            'total_items': len(valid_items)
        }
