import threading
import time
import os
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image
import hashlib

from settings_manager import SettingsManager
from chromecast_manager import ChromecastManager
from image_server import get_image_server


class SlideshowController:
    """Controls the slideshow timing and image distribution."""
    
    def __init__(self, settings_manager: SettingsManager, chromecast_manager: ChromecastManager, socketio=None):
        self.settings_manager = settings_manager
        self.chromecast_manager = chromecast_manager
        self.socketio = socketio
        self.logger = logging.getLogger(__name__)
        
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
        
        self._lock = threading.Lock()
        self.supported_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
    
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
            self.logger.info(f"Found {len(images)} images in {directory}")
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
            self.slideshow_thread = threading.Thread(target=self._slideshow_loop, daemon=True)
            self.slideshow_thread.start()
        
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
                    time.sleep(max(interval, 10))  # At least 10 seconds between checks
                else:
                    time.sleep(interval)
                
            except Exception as e:
                self.logger.error(f"Error in slideshow loop: {e}")
                if self.socketio:
                    self.socketio.emit('error', {'message': f'Slideshow error: {str(e)}'})
                time.sleep(5)  # Wait before retrying
        
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
        
        # Send images to devices
        device_uuids = [device['uuid'] for device in devices]
        results = self.chromecast_manager.send_image_to_multiple_devices(device_uuids, image_urls)
        
        # Update current images tracking
        for i, (uuid, success) in enumerate(results.items()):
            if success and i < len(current_images):
                self.current_images[uuid] = current_images[i]
        
        # Only advance to next set of images if rotation is enabled
        rotation_enabled = self.settings_manager.is_rotation_enabled()
        if rotation_enabled:
            self.current_image_index = (self.current_image_index + num_devices) % len(images)
        
        # Emit status update
        if self.socketio:
            successful_sends = sum(1 for success in results.values() if success)
            self.socketio.emit('slideshow_update', {
                'current_index': self.current_image_index,
                'total_images': len(images),
                'successful_devices': successful_sends,
                'total_devices': len(devices),
                'current_images': [os.path.basename(img) for img in current_images],
                'rotation_enabled': rotation_enabled
            })
        
        rotation_status = "with rotation" if rotation_enabled else "without rotation"
        self.logger.info(f"Distributed {len(image_urls)} images to {len(devices)} devices "
                        f"({sum(results.values())} successful) {rotation_status}")
    
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
            self.playlist_thread = threading.Thread(target=self._playlist_loop, daemon=True)
            self.playlist_thread.start()
        
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
        
        while self.is_playlist_running:
            try:
                # Get current playlist item
                current_item = valid_items[self.current_playlist_index % len(valid_items)]
                directory = current_item['directory_path']
                duration_minutes = current_item['duration_minutes']
                
                self.logger.info(f"Starting playlist item: {current_item['directory_name']} for {duration_minutes} minutes")
                
                # Start slideshow for this directory
                if not self._start_directory_slideshow(directory):
                    self.logger.error(f"Failed to start slideshow for directory: {directory}")
                    # Move to next item
                    self.current_playlist_index = (self.current_playlist_index + 1) % len(valid_items)
                    continue
                
                # Reset timing
                item_start_time = time.time()
                item_accumulated_time = 0
                self.current_item_start_time = item_start_time  # Track for status
                
                # Get images for logging
                images = self.get_images_in_directory(directory)
                
                # Skip WebSocket emission here to avoid conflicts with API endpoint
                self.logger.info(f"Starting playlist directory: {current_item['directory_name']} with {len(images)} images")
                
                # Run slideshow for specified duration
                while self.is_playlist_running:
                    if self.is_playlist_paused:
                        time.sleep(1)  # Check every second when paused
                        # Skip WebSocket updates when paused to avoid conflicts with API endpoint
                        continue
                    
                    # Calculate elapsed time
                    current_time = time.time()
                    elapsed_time = (current_time - item_start_time) + item_accumulated_time
                    
                    # Check if duration has elapsed or skip was requested
                    if elapsed_time >= duration_minutes * 60 or self.skip_requested:
                        if self.skip_requested:
                            self.logger.info("Processing skip request - advancing to next playlist item")
                            self.skip_requested = False  # Reset the flag
                        else:
                            self.logger.info(f"Duration elapsed ({elapsed_time:.1f}s >= {duration_minutes * 60}s) - advancing to next playlist item")
                        break
                    
                    # Update slideshow (only if rotation is enabled to save CPU)
                    if self.settings_manager.is_rotation_enabled():
                        enabled_devices = self.chromecast_manager.get_enabled_devices()
                        if enabled_devices:
                            images = self.get_images_in_directory(directory)
                            if images:
                                self._distribute_images_to_devices(images, enabled_devices)
                    
                    # Skip periodic WebSocket updates to avoid conflicts with API endpoint
                    # Let API endpoint handle all WebSocket communications
                    if int(elapsed_time) % 5 == 0:
                        status = self.get_playlist_status()
                        self.logger.info(f"Periodic status check (no WebSocket): running={status.get('running')}, time_remaining={status.get('time_remaining')}")
                    
                    # Wait for slideshow interval, but check for skip requests more frequently
                    interval = self.settings_manager.get_slideshow_interval()
                    sleep_chunks = max(1, interval)  # Sleep in 1-second chunks
                    for _ in range(sleep_chunks):
                        if self.skip_requested or not self.is_playlist_running:
                            break
                        time.sleep(1)
                    
                    # Handle pause state changes
                    if self.is_playlist_paused and self.playlist_pause_time:
                        item_accumulated_time += self.playlist_pause_time - item_start_time
                        item_start_time = time.time()  # Reset start time when resuming
                        self.playlist_pause_time = None
                
                # Move to next playlist item
                old_index = self.current_playlist_index
                self.current_playlist_index = (self.current_playlist_index + 1) % len(valid_items)
                
                # Skip WebSocket emission here to avoid conflicts - let API endpoint handle it
                self.logger.info(f"SKIP DEBUG: Advanced from index {old_index} to {self.current_playlist_index}, item: {valid_items[self.current_playlist_index]['directory_name']}")
                
            except Exception as e:
                self.logger.error(f"Error in playlist loop: {e}")
                time.sleep(5)  # Wait before retrying
        
        # Cleanup
        with self._lock:
            self.is_playlist_running = False
        
        # Stop any running slideshow
        self.image_server.stop()
        self.current_images.clear()
        
        # Skip WebSocket emissions to avoid conflicts - let API endpoint handle stop notifications
    
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
        
        # Send initial images to devices (but don't start regular slideshow thread)
        enabled_devices = self.chromecast_manager.get_enabled_devices()
        if enabled_devices:
            self._distribute_images_to_devices(images, enabled_devices)
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