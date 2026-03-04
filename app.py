# CRITICAL: gevent monkey patching must be done FIRST, before any other imports.
# We skip patching os and subprocess because:
#   - Patched os.waitpid deadlocks in threadpool threads with concurrent children
#   - Patched subprocess.Popen requires gevent child watchers (default loop only)
#   - Patched subprocess nullifies _posixsubprocess, breaking stdlib Popen
# Flask-SocketIO only needs socket/threading/time/select patches to work.
import gevent
from gevent import monkey
monkey.patch_all(subprocess=False, os=False)

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_cors import CORS
import os
import threading
import logging
import time
from pathlib import Path

from settings_manager import SettingsManager
from chromecast_manager import ChromecastManager
from slideshow_controller import SlideshowController


# The 'DATA_FILES' setting in setup.py now correctly copies the 'templates'
# and 'static' folders, so we can revert to the standard Flask configuration.
app = Flask(__name__)
CORS(app)
app.config['SECRET_KEY'] = 'chromecast-slideshow-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

# Initialize components
settings_manager = SettingsManager()
chromecast_manager = ChromecastManager(settings_manager)

# Initialize SocketIO with default async mode (auto-detects best backend)
# socketio = SocketIO(app, cors_allowed_origins="*")  <-- Removed duplicate initialization
slideshow_controller = SlideshowController(settings_manager, chromecast_manager)
slideshow_controller.init_app(socketio, app)

# Configure logging — write to both stderr and a persistent file so we can
# diagnose freezes by inspecting the log after the app crashes.
# Use a custom handler that flushes after every record so nothing is lost
# if the process crashes (segfault).
logging.basicConfig(level=logging.INFO)


class _FlushingFileHandler(logging.FileHandler):
    """FileHandler that flushes after every log record (crash-safe)."""
    def emit(self, record):
        super().emit(record)
        self.flush()


_file_handler = _FlushingFileHandler('/tmp/posters_debug.log', mode='w')
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(logging.Formatter(
    '%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'))
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger(__name__)

# Install crash signal handlers so we can detect segfaults/bus errors.
import signal as _signal
import faulthandler as _faulthandler
_crash_file = open('/tmp/posters_crash.log', 'w')
_faulthandler.enable(file=_crash_file, all_threads=True)
# Also dump tracebacks on SIGUSR1 for debugging live freezes
_faulthandler.register(_signal.SIGUSR1, file=_crash_file, all_threads=True)

# Discovery state management
discovery_lock = threading.Lock()
discovery_running = False


@app.route('/')
def index():
    """Main page with slideshow controls."""
    return render_template('index.html')


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get all current settings."""
    return jsonify(settings_manager.get_all_settings())


@app.route('/api/settings', methods=['POST'])
def save_settings():
    """Save settings from the frontend."""
    data = request.get_json()
    
    for key, value in data.items():
        settings_manager.save_setting(key, str(value))
    
    socketio.emit('settings_updated', data)
    return jsonify({'status': 'success'})


@app.route('/api/directories', methods=['GET'])
def browse_directories():
    """Browse directory structure for image selection."""
    explicit_path = request.args.get('path')
    path = explicit_path or settings_manager.get_selected_directory()

    def list_directory(directory_path):
        items = []
        if directory_path.parent != directory_path:
            items.append({'name': '..', 'path': str(directory_path.parent), 'type': 'directory'})
        for item in sorted(directory_path.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                items.append({'name': item.name, 'path': str(item), 'type': 'directory'})
        return items

    try:
        directory_path = Path(path)
        if not directory_path.exists() or not directory_path.is_dir():
            directory_path = Path(os.path.expanduser('~'))

        items = list_directory(directory_path)
        return jsonify({'current_path': str(directory_path), 'items': items})

    except Exception as e:
        logger.error(f"Error browsing directory {path}: {e}")
        # If we used a stored/default path and it failed, fall back to home
        if not explicit_path:
            try:
                home_path = Path(os.path.expanduser('~'))
                items = list_directory(home_path)
                return jsonify({'current_path': str(home_path), 'items': items})
            except Exception:
                pass
        return jsonify({'error': str(e)}), 500


@app.route('/api/images', methods=['GET'])
def get_images():
    """Get images from the selected directory."""
    directory = settings_manager.get_selected_directory()
    
    try:
        images = slideshow_controller.get_images_in_directory(directory)
        image_data = []
        
        for img_path in images:
            img_name = os.path.basename(img_path)
            # Generate thumbnail and get URL
            thumbnail_path = slideshow_controller.generate_thumbnail(img_path)
            thumbnail_url = f"/api/thumbnails/{img_name}" if thumbnail_path else None
            
            image_data.append({
                'name': img_name,
                'thumbnail_url': thumbnail_url
            })
        
        return jsonify({
            'directory': directory,
            'images': image_data,
            'count': len(images)
        })
    except Exception as e:
        logger.error(f"Error getting images from {directory}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/directory-images', methods=['GET'])
def get_directory_images():
    """Get images from a specific directory for preview."""
    directory = request.args.get('path')
    if not directory:
        return jsonify({'error': 'No directory path provided'}), 400
    
    try:
        images = slideshow_controller.get_images_in_directory(directory)
        image_data = []
        
        # Limit to first 8 images for preview
        preview_images = images[:8]
        
        for img_path in preview_images:
            img_name = os.path.basename(img_path)
            # Generate thumbnail for this specific image
            thumbnail_path = slideshow_controller.generate_thumbnail(img_path)
            
            image_data.append({
                'name': img_name,
                'path': img_path,
                'has_thumbnail': thumbnail_path is not None
            })
        
        return jsonify({
            'directory': directory,
            'images': image_data,
            'count': len(images),
            'total_count': len(images)
        })
    except Exception as e:
        logger.error(f"Error getting images from {directory}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/thumbnails/<filename>')
def serve_thumbnail(filename):
    """Serve thumbnail images."""
    try:
        thumbnail_dir = str(settings_manager.get_thumbnail_dir())
        
        # Check if a specific directory is requested via query parameter
        requested_dir = request.args.get('dir')
        if requested_dir:
            directory = requested_dir
        else:
            # Fall back to selected directory
            directory = settings_manager.get_selected_directory()
            
        if not directory:
            return "No directory specified", 400
        
        # Find thumbnail by original filename
        original_path = os.path.join(directory, filename)
        if not os.path.exists(original_path):
            return "Image not found", 404
            
        thumbnail_path = slideshow_controller.generate_thumbnail(original_path, thumbnail_dir)
        
        if thumbnail_path and os.path.exists(thumbnail_path):
            from flask import send_file
            return send_file(thumbnail_path, mimetype='image/jpeg')
        else:
            # Return 404 if thumbnail doesn't exist
            return "Thumbnail not found", 404
            
    except Exception as e:
        logger.error(f"Error serving thumbnail for {filename}: {e}")
        return "Error generating thumbnail", 500



@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Get all Chromecast devices (discovered + saved from database)."""
    # Get currently discovered devices (in-memory)
    discovered_devices = chromecast_manager.get_all_devices()
    
    # Get all devices from database (includes offline/previous devices)
    saved_devices = settings_manager.get_all_devices()
    
    # Create a combined list, prioritizing discovered (online) devices
    device_map = {}
    
    # First add all saved devices from database
    for device in saved_devices:
        device_map[device['uuid']] = {
            'uuid': device['uuid'],
            'name': device['name'],
            'host': device['host'],
            'port': device['port'],
            'enabled': bool(device['enabled']),
            'online': False,  # Assume offline until proven online
            'last_seen': device.get('last_seen', 'Unknown')
        }
    
    # Then update with discovered devices (these are online)
    for device in discovered_devices:
        device_map[device['uuid']] = {
            'uuid': device['uuid'],
            'name': device['name'],
            'host': device['host'],
            'port': device['port'],
            'enabled': device['enabled'],  # Already merged by chromecast_manager
            'online': True,
            'model': device.get('model', 'Unknown'),
            'last_seen': 'Now'
        }
    
    return jsonify(list(device_map.values()))


@app.route('/api/devices/<uuid>/toggle', methods=['POST'])
def toggle_device(uuid):
    """Toggle device enabled/disabled status."""
    data = request.get_json()
    enabled = data.get('enabled', False)
    
    settings_manager.toggle_device(uuid, enabled)
    socketio.emit('device_updated', {'uuid': uuid, 'enabled': enabled})
    
    return jsonify({'status': 'success'})


@app.route('/api/slideshow/start', methods=['POST'])
def start_slideshow():
    """Start the slideshow."""
    try:
        slideshow_controller.start_slideshow()
        socketio.emit('slideshow_started')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error starting slideshow: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/slideshow/stop', methods=['POST'])
def stop_slideshow():
    """Stop the slideshow."""
    try:
        slideshow_controller.stop_slideshow()
        socketio.emit('slideshow_stopped')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error stopping slideshow: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/slideshow/status', methods=['GET'])
def get_slideshow_status():
    """Get current slideshow status."""
    return jsonify({
        'running': slideshow_controller.is_running(),
        'current_images': slideshow_controller.get_current_images()
    })


@app.route('/api/slideshow/skip', methods=['POST'])
def skip_slideshow():
    """Skip to next image in single directory slideshow."""
    try:
        if slideshow_controller.skip_to_next():
            return jsonify({'status': 'success'})
        else:
            return jsonify({'error': 'No slideshow running'}), 400
    except Exception as e:
        logger.error(f"Error skipping slideshow: {e}")
        return jsonify({'error': str(e)}), 500


# Playlist API endpoints
@app.route('/api/playlist', methods=['GET'])
def get_playlist():
    """Get current playlist items."""
    items = settings_manager.get_playlist_items()
    total_duration = settings_manager.get_playlist_total_duration()
    return jsonify({
        'items': items,
        'total_duration': total_duration,
        'item_count': len(items)
    })


@app.route('/api/playlist/items', methods=['POST'])
def add_playlist_item():
    """Add current directory to playlist."""
    try:
        current_dir = settings_manager.get_selected_directory()
        if not current_dir:
            return jsonify({'error': 'No directory selected'}), 400
        
        # Get directory name for display
        directory_name = os.path.basename(current_dir) or current_dir
        
        # Default duration
        duration = 10
        
        item_id = settings_manager.add_playlist_item(current_dir, directory_name, duration)
        socketio.emit('playlist_updated')
        
        return jsonify({
            'status': 'success',
            'item_id': item_id
        })
    except Exception as e:
        logger.error(f"Error adding playlist item: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/items/<int:item_id>', methods=['DELETE'])
def remove_playlist_item(item_id):
    """Remove an item from the playlist."""
    try:
        settings_manager.remove_playlist_item(item_id)
        socketio.emit('playlist_updated')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error removing playlist item: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/items/<int:item_id>/duration', methods=['PUT'])
def update_playlist_item_duration(item_id):
    """Update the duration of a playlist item."""
    data = request.get_json()
    duration = data.get('duration_minutes')
    
    if not duration or duration < 1:
        return jsonify({'error': 'Invalid duration'}), 400
    
    try:
        settings_manager.update_playlist_item_duration(item_id, duration)
        socketio.emit('playlist_updated')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error updating playlist item duration: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/reorder', methods=['PUT'])
def reorder_playlist():
    """Reorder playlist items."""
    data = request.get_json()
    item_ids = data.get('item_ids', [])
    
    if not item_ids:
        return jsonify({'error': 'No item IDs provided'}), 400
    
    try:
        settings_manager.reorder_playlist_items(item_ids)
        socketio.emit('playlist_updated')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error reordering playlist: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/clear', methods=['DELETE'])
def clear_playlist():
    """Clear all items from the playlist."""
    try:
        settings_manager.clear_playlist()
        socketio.emit('playlist_updated')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error clearing playlist: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/start', methods=['POST'])
def start_playlist():
    """Start playlist mode slideshow."""
    try:
        result = slideshow_controller.start_playlist()
        logger.info(f"Start playlist result: {result}")
        
        # Always emit status update, even if already running (for frontend sync)
        status = slideshow_controller.get_playlist_status()
        logger.info(f"Start playlist API - got status from backend: {status}")
        
        if result is None:
            logger.error("start_playlist() returned None - this should not happen")
            return jsonify({'error': 'Internal error: start_playlist returned None'}), 500
        
        if result.get('success'):
            logger.info("Playlist started successfully - emitting WebSocket events")
            socketio.emit('playlist_started')
        else:
            logger.info(f"Playlist start failed or already running: {result.get('error')}")
        
        current_item = status.get('current_item')
        dir_name = current_item.get('directory_name') if current_item else 'None'
        logger.info(f"Emitting playlist_status_update: running={status.get('running')}, current_item={dir_name}")
        socketio.emit('playlist_status_update', status)
        
        if result.get('success'):
            return jsonify({'status': 'success'})
        else:
            return jsonify({'error': result.get('error', 'Unknown error')}), 400
    except Exception as e:
        logger.error(f"Error starting playlist: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/pause', methods=['POST'])
def pause_playlist():
    """Pause or resume playlist."""
    try:
        slideshow_controller.toggle_playlist_pause()
        socketio.emit('playlist_paused')
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error pausing playlist: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/skip', methods=['POST'])
def skip_playlist():
    """Skip to next item in playlist."""
    try:
        slideshow_controller.skip_playlist_item()
        # Note: Controller will handle all WebSocket emissions - removed blocking sleep and duplicate emissions
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error skipping playlist item: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/stop', methods=['POST'])
def stop_playlist():
    """Stop playlist mode slideshow."""
    try:
        slideshow_controller.stop_playlist()
        socketio.emit('playlist_stopped')
        
        # Send final status update to clear highlighting
        status = slideshow_controller.get_playlist_status()
        with app.app_context():
            socketio.emit('playlist_status_update', status)
        
        return jsonify({'status': 'success'})
    except Exception as e:
        logger.error(f"Error stopping playlist: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/playlist/status', methods=['GET'])
def get_playlist_status():
    """Get current playlist execution status."""
    status = slideshow_controller.get_playlist_status()
    return jsonify(status)


@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info('Client connected')
    
    
    # Check if discovery is currently running and notify client
    global discovery_running
    with discovery_lock:
        if discovery_running:
            socketio.emit('discovery_started')
            logger.info("Notified new client that discovery is in progress")
    
    # Send current device list to newly connected client if any exist
    try:
        discovered_devices = list(chromecast_manager.discovered_devices.values())
        if discovered_devices:
            socketio.emit('devices_discovered', discovered_devices)
            logger.info(f"Sent {len(discovered_devices)} existing devices to new client")
    except Exception as e:
        logger.error(f"Error sending devices to new client: {e}")
    emit('connected', {'status': 'Connected to slideshow controller'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info('Client disconnected')


@socketio.on('test_websocket')
def handle_test_websocket():
    """Test WebSocket event handler for debugging."""
    logger.info("Test WebSocket event received from client")
    try:
        # Test immediate emission
        socketio.emit('test_response', {'message': 'WebSocket test successful', 'timestamp': time.time()})
        logger.info("Test WebSocket response emitted successfully")
        
        # Test emission with Flask app context (like our background threads)
        with app.app_context():
            socketio.emit('test_background_response', {'message': 'Background WebSocket test successful', 'timestamp': time.time()})
        logger.info("Test background WebSocket response emitted successfully")
        
    except Exception as e:
        logger.error(f"Error in test WebSocket handler: {e}")
        emit('error', {'message': f'Test WebSocket error: {str(e)}'})


@socketio.on('discover_devices')
def handle_discover_devices():
    """Trigger device discovery using chromecast_manager (temp-file subprocess)."""
    global discovery_running

    with discovery_lock:
        if discovery_running:
            logger.warning("Discovery already in progress, ignoring request")
            socketio.emit('error', {'message': 'Device discovery already in progress'})
            return
        discovery_running = True

    try:
        logger.info("Starting device discovery...")
        socketio.emit('discovery_started')

        def discovery_worker():
            try:
                devices = chromecast_manager.discover_devices(timeout=5)
                if devices:
                    socketio.emit('devices_discovered', devices)
                    logger.info(f"Discovery completed, found {len(devices)} devices")
                else:
                    logger.warning("Discovery completed but found no devices")
            except Exception as e:
                logger.error(f"Error in discovery worker: {e}")
                socketio.emit('error', {'message': str(e)})
            finally:
                global discovery_running
                with discovery_lock:
                    discovery_running = False
                socketio.emit('discovery_finished')

        socketio.start_background_task(discovery_worker)

    except Exception as e:
        logger.error(f"Error starting device discovery: {e}")
        with discovery_lock:
            discovery_running = False
        emit('error', {'message': str(e)})


def start_auto_discovery():
    """Start automatic device discovery after a short delay to let the server initialize."""
    import gevent

    def delayed_discovery():
        gevent.sleep(5)
        logger.info("Starting automatic device discovery...")

        global discovery_running
        with discovery_lock:
            if discovery_running:
                return
            discovery_running = True

        try:
            socketio.emit('discovery_started')
            devices = chromecast_manager.discover_devices(timeout=5)
            if devices:
                socketio.emit('devices_discovered', devices)
                logger.info(f"Auto-discovery completed, found {len(devices)} devices")
        except Exception as e:
            logger.warning(f"Auto-discovery error: {e}")
        finally:
            with discovery_lock:
                discovery_running = False
            socketio.emit('discovery_finished')

    socketio.start_background_task(delayed_discovery)


def _hub_heartbeat():
    """Background greenlet that logs a heartbeat every 2 seconds.
    When this stops appearing in the log, the gevent hub has frozen."""
    beat = 0
    while True:
        beat += 1
        logger.info(f"[HEARTBEAT] #{beat}")
        gevent.sleep(2)


if __name__ == '__main__':
    try:
        logger.info("Starting Chromecast Slideshow Server...")

        # TEMPORARY: Disable auto-discovery to test WebSocket functionality
        # socketio.start_background_task(start_auto_discovery)

        # Start hub heartbeat monitor
        socketio.start_background_task(_hub_heartbeat)

        socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)
        
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        slideshow_controller.stop_slideshow()
    except Exception as e:
        logger.error(f"Application error: {e}")
        raise