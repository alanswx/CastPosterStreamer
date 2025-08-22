from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import os
import threading
import logging
from pathlib import Path

from settings_manager import SettingsManager
from chromecast_manager import ChromecastManager
from slideshow_controller import SlideshowController

# The 'DATA_FILES' setting in setup.py now correctly copies the 'templates'
# and 'static' folders, so we can revert to the standard Flask configuration.
app = Flask(__name__)
app.config['SECRET_KEY'] = 'chromecast-slideshow-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Initialize components
settings_manager = SettingsManager()
chromecast_manager = ChromecastManager(settings_manager)
slideshow_controller = SlideshowController(settings_manager, chromecast_manager, socketio)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    path = request.args.get('path', settings_manager.get_selected_directory())
    
    try:
        directory_path = Path(path)
        if not directory_path.exists() or not directory_path.is_dir():
            directory_path = Path(os.path.expanduser('~'))
        
        items = []
        
        # Add parent directory link (except for root)
        if directory_path.parent != directory_path:
            items.append({
                'name': '..',
                'path': str(directory_path.parent),
                'type': 'directory'
            })
        
        # Add subdirectories
        for item in sorted(directory_path.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                items.append({
                    'name': item.name,
                    'path': str(item),
                    'type': 'directory'
                })
        
        return jsonify({
            'current_path': str(directory_path),
            'items': items
        })
        
    except Exception as e:
        logger.error(f"Error browsing directory {path}: {e}")
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


@app.route('/api/thumbnails/<filename>')
def serve_thumbnail(filename):
    """Serve thumbnail images."""
    try:
        thumbnail_dir = str(settings_manager.get_thumbnail_dir())
        
        # Find thumbnail by original filename
        directory = settings_manager.get_selected_directory()
        original_path = os.path.join(directory, filename)
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
    """Get all discovered Chromecast devices."""
    devices = chromecast_manager.get_all_devices()
    saved_devices = settings_manager.get_all_devices()
    
    # Merge discovered devices with saved settings
    device_map = {d['uuid']: d for d in saved_devices}
    
    for device in devices:
        if device['uuid'] in device_map:
            device['enabled'] = device_map[device['uuid']]['enabled']
        else:
            device['enabled'] = True
    
    return jsonify(devices)


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


@socketio.on('connect')
def handle_connect():
    """Handle client connection."""
    logger.info('Client connected')
    emit('connected', {'status': 'Connected to slideshow controller'})


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info('Client disconnected')


@socketio.on('discover_devices')
def handle_discover_devices():
    """Trigger device discovery using subprocess to avoid asyncio conflicts."""
    try:
        logger.info("Starting device discovery...")
        import subprocess
        import json
        import threading
        
        def discovery_worker():
            try:
                # Run discovery in a separate process to avoid asyncio conflicts
                result = subprocess.run([
                    'python3', '-c', '''
import json
from catt.discovery import get_cast_infos

try:
    cast_infos = get_cast_infos()
    devices = []
    for cast_info in cast_infos:
        device_info = {
            "uuid": str(cast_info.uuid),
            "name": cast_info.friendly_name,
            "host": cast_info.host,
            "port": cast_info.port,
            "model": cast_info.model_name,
            "manufacturer": cast_info.manufacturer,
            "status": "available",
            "cast_type": "cast"
        }
        devices.append(device_info)
    print(json.dumps(devices))
except Exception as e:
    print(json.dumps({"error": str(e)}))
'''
                ], capture_output=True, text=True, timeout=15)
                
                if result.returncode == 0:
                    try:
                        data = json.loads(result.stdout.strip())
                        if isinstance(data, dict) and "error" in data:
                            logger.error(f"Discovery subprocess error: {data['error']}")
                            socketio.emit('error', {'message': data['error']})
                        else:
                            # Save discovered devices to database
                            for device in data:
                                settings_manager.save_device(
                                    device['uuid'],
                                    device['name'], 
                                    device['host'],
                                    device['port']
                                )
                                # Update manager's discovered devices
                                chromecast_manager.discovered_devices[device['uuid']] = device
                            
                            socketio.emit('devices_discovered', data)
                            logger.info(f"Discovery completed, found {len(data)} devices")
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse discovery results: {e}")
                        socketio.emit('error', {'message': 'Failed to parse discovery results'})
                else:
                    logger.error(f"Discovery subprocess failed: {result.stderr}")
                    socketio.emit('error', {'message': 'Device discovery failed'})
                    
            except subprocess.TimeoutExpired:
                logger.error("Discovery timeout")
                socketio.emit('error', {'message': 'Discovery timeout - please try again'})
            except Exception as e:
                logger.error(f"Error in discovery worker: {e}")
                socketio.emit('error', {'message': str(e)})
        
        # Run discovery in a separate thread to avoid blocking
        thread = threading.Thread(target=discovery_worker)
        thread.daemon = True
        thread.start()
        
    except Exception as e:
        logger.error(f"Error starting device discovery: {e}")
        emit('error', {'message': str(e)})


if __name__ == '__main__':
    try:
        # Note: Automatic discovery disabled on startup to avoid asyncio threading issues
        # Discovery will be triggered when user clicks "Discover Devices" button
        
        logger.info("Starting Chromecast Slideshow Server...")
        socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)
        
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        slideshow_controller.stop_slideshow()
    except Exception as e:
        logger.error(f"Application error: {e}")
        raise