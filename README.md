# Chromecast Slideshow Controller

A Python web application that displays images from your local directories as synchronized slideshows across multiple Chromecast devices. Each device displays unique images that rotate automatically at configurable intervals.

## Features

- **Multi-Device Support**: Send different images to multiple Chromecast devices simultaneously
- **Directory Browser**: Web-based interface to select image directories
- **Real-Time Control**: Start/stop slideshows and adjust timing via web interface
- **Auto-Discovery**: Automatically finds Chromecast devices on your network
- **Image Preview**: Thumbnail previews of images in selected directories
- **Persistent Settings**: Remembers your preferences across sessions
- **Error Recovery**: Continues slideshow even if devices disconnect
- **Real-Time Updates**: Live status updates via WebSocket connection

## Requirements

- Python 3.7+
- Chromecast/Google TV devices on the same network
- Images in JPEG, PNG, GIF, BMP, or WebP format

## Installation

1. **Clone or download this project**:
   ```bash
   cd CastPosterStreamer 
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Ensure network connectivity**:
   - Your computer and Chromecast devices must be on the same network
   - Firewall should allow mDNS/Bonjour traffic for device discovery

## Usage

### Starting the Application

1. **Run the application**:
   ```bash
   python app.py
   ```

2. **Open your web browser** and navigate to:
   ```
   http://localhost:5001
   ```

### Setting Up Your Slideshow

1. **Select Image Directory**:
   - Use the directory browser to navigate to your image folder
   - Click "Select This Directory" to confirm your choice
   - The app remembers your selection for next time

2. **Discover Chromecast Devices**:
   - Click "Discover Devices" to find Chromecast devices on your network
   - Enable/disable devices using the checkboxes
   - Device preferences are saved automatically

3. **Configure Settings**:
   - Set slideshow interval (default: 5 seconds)
   - Settings are saved automatically

4. **Start Slideshow**:
   - Click "Start Slideshow" to begin
   - Each device will show different images
   - Images rotate automatically at your chosen interval

### Web Interface Sections

- **Directory Selection**: Browse and select folders containing images
- **Image Preview**: View thumbnails of images in selected directory
- **Chromecast Devices**: Manage discovered devices and their settings
- **Slideshow Controls**: Start/stop slideshow and adjust timing
- **Status & Error Log**: Real-time updates and error messages

## File Structure

```
CastPosterStreamer/
├── app.py                  # Main Flask application
├── chromecast_manager.py   # Chromecast device discovery and communication
├── slideshow_controller.py # Image rotation logic and timing
├── settings_manager.py     # Database and settings persistence
├── image_server.py         # HTTP server for serving images to devices
├── static/
│   ├── css/style.css      # Web interface styling
│   ├── js/app.js          # Frontend JavaScript and WebSocket handling
│   └── thumbnails/        # Generated image thumbnails (created automatically)
├── templates/
│   └── index.html         # Main web interface
├── requirements.txt       # Python dependencies
├── config.db             # SQLite database (created automatically)
└── README.md             # This file
```

## How It Works

1. **Image Server**: A separate HTTP server serves images from your selected directory
2. **Device Discovery**: Uses mDNS to find Chromecast devices on your network
3. **Image Distribution**: Distributes unique images across devices, cycling through your collection
4. **Synchronization**: All devices change images at approximately the same time
5. **Persistence**: Settings and device preferences are saved in SQLite database

## Troubleshooting

### No Devices Found
- Ensure all devices are on the same network
- Check that mDNS/Bonjour is enabled on your network
- Try clicking "Discover Devices" multiple times
- Restart your Chromecast devices

### Images Not Loading
- Verify the selected directory contains supported image formats
- Check that images aren't corrupted or too large
- Look at the error log for specific error messages

### Connection Issues
- Check firewall settings
- Ensure port 5000 (Flask) and the image server port are not blocked
- Try restarting the application

### Performance Issues
- Large images may take longer to load on devices
- Consider using smaller image files for better performance
- Monitor the error log for timeout messages

## Network Requirements

- **Same Network**: All devices must be on the same local network
- **mDNS Support**: Network must support multicast DNS for device discovery
- **Port Access**: Application needs access to:
  - Port 5001 (Flask web interface)
  - Dynamic port for image server (auto-selected)
  - Standard Chromecast ports (8008, 8009)

## Supported Image Formats

- JPEG (.jpg, .jpeg)
- PNG (.png)
- GIF (.gif) - displayed as static images
- BMP (.bmp)
- WebP (.webp)

## Technical Notes

- Images are served directly without resizing for optimal quality
- SQLite database stores settings and device preferences
- WebSocket connection provides real-time updates
- Thumbnail generation happens on-demand
- Error recovery ensures slideshow continues if devices disconnect

## Customization

The application can be customized by modifying:

- **Timing Logic**: Edit `slideshow_controller.py` for different image distribution patterns
- **Web Interface**: Modify `templates/index.html` and `static/css/style.css` for UI changes
- **Device Management**: Update `chromecast_manager.py` for different Chromecast interactions
- **Settings**: Extend `settings_manager.py` for additional configuration options

## Security Notes

- Application runs on local network only (0.0.0.0:5001)
- No authentication required (designed for home/private network use)
- Images are served from selected directory only (path traversal protection included)
- No external network access required for core functionality

## License

This project is provided as-is for personal and educational use.
