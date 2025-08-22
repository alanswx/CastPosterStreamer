# Posters - Chromecast Slideshow Controller

A macOS menu bar application that displays images from your local directories as synchronized slideshows across multiple Chromecast devices. Each device displays unique images that rotate automatically at configurable intervals.

Available as both a native macOS app bundle and a Python web application.

## Features

- **Native macOS App**: Menu bar app with system integration and proper file management
- **Multi-Device Support**: Send different images to multiple Chromecast devices simultaneously
- **Directory Browser**: Web-based interface to select image directories
- **Real-Time Control**: Start/stop slideshows and adjust timing via web interface
- **Auto-Discovery**: Automatically finds Chromecast devices on your network
- **Image Preview**: Thumbnail previews of images in selected directories
- **Persistent Settings**: Remembers your preferences across sessions using proper macOS directories
- **Error Recovery**: Continues slideshow even if devices disconnect
- **Real-Time Updates**: Live status updates via WebSocket connection
- **Python Fallback**: Automatically switches to system Python if bundled version fails

## Requirements

- macOS 10.14+ (for app bundle) or Python 3.7+ (for development)
- Chromecast/Google TV devices on the same network
- Images in JPEG, PNG, GIF, BMP, or WebP format

## Installation

### Option 1: Native macOS App (Recommended)

1. **Download the Posters.app bundle**
2. **Move to Applications folder** (optional but recommended)
3. **Launch the app** - it will appear in your menu bar with a 📺 icon
4. **Grant permissions** if prompted (network access, file access)

The app will automatically:
- Create necessary directories in your Library folder
- Migrate any existing configuration files
- Set up proper file permissions

### Option 2: Development/Python Mode

1. **Clone or download this project**:
   ```bash
   cd CastPosterStreamerBundled
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the menu bar app**:
   ```bash
   python menu_bar_app.py
   ```

## Usage

### Using the Native macOS App

1. **Launch Posters** - Look for the 📺 icon in your menu bar
2. **Click the menu bar icon** to access options:
   - **Open Web Interface** - Opens the control panel in your browser
   - **Check Server** - Verify the background server is running
   - **Restart Server** - Restart if needed
   - **Show Debug Log** - View logs for troubleshooting
   - **Quit** - Close the application

3. **The web interface** will open at `http://localhost:5001`

### Using Python Development Mode

1. **Run the menu bar app**:
   ```bash
   python menu_bar_app.py
   ```

2. **Access via menu bar** or navigate directly to:
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

### Application Files
```
CastPosterStreamerBundled/
├── menu_bar_app.py         # Native macOS menu bar application
├── app.py                  # Main Flask web server
├── chromecast_manager.py   # Chromecast device discovery and communication
├── slideshow_controller.py # Image rotation logic and timing
├── settings_manager.py     # Database and settings persistence with macOS directories
├── image_server.py         # HTTP server for serving images to devices
├── chromecast_subprocess.py # Subprocess handling for device communication
├── static/
│   ├── css/style.css      # Web interface styling
│   └── js/app.js          # Frontend JavaScript and WebSocket handling
├── templates/
│   └── index.html         # Main web interface
├── setup.py              # py2app configuration for building native app
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

### User Data Files (Created Automatically)

The app follows proper macOS conventions and stores user data in standard locations:

```
~/Library/Application Support/Posters/
├── config.db            # SQLite database with settings and device info
└── menu_config.json     # Menu bar configuration (port settings)

~/Library/Caches/Posters/
└── thumbnails/          # Generated image thumbnails (auto-created)
    ├── a1b2c3d4...jpg  # Cached thumbnails (hash-named)
    └── ...

~/Library/Logs/Posters/
├── app_bundle.log       # Application logs (bundled app)
└── app_debug.log        # Application logs (development mode)
```

### Built Application
```
dist/
└── Posters.app/         # Native macOS application bundle (created by py2app)
    ├── Contents/
    │   ├── MacOS/
    │   │   ├── Posters  # Main executable
    │   │   └── python   # Bundled Python (with fallback to system Python)
    │   └── Resources/   # Application resources and Python modules
    └── ...
```

## How It Works

1. **Menu Bar Integration**: Native macOS app runs in the background, accessible via menu bar
2. **Image Server**: A separate HTTP server serves images from your selected directory
3. **Device Discovery**: Uses mDNS to find Chromecast devices on your network
4. **Image Distribution**: Distributes unique images across devices, cycling through your collection
5. **Synchronization**: All devices change images at approximately the same time
6. **Persistence**: Settings and device preferences are saved in SQLite database in proper macOS location
7. **Python Fallback**: If bundled Python fails, automatically switches to system Python
8. **File Migration**: Automatically migrates existing files to proper macOS directories

## Troubleshooting

### App Won't Start or Database Errors
- **Fixed in current version**: App now uses proper macOS directories
- Check the log files in `~/Library/Logs/Posters/` for detailed error messages
- Try using "Restart Server" from the menu bar
- If issues persist, delete `~/Library/Application Support/Posters/` to reset

### No Devices Found
- Ensure all devices are on the same network
- Check that mDNS/Bonjour is enabled on your network
- Try clicking "Discover Devices" multiple times
- Restart your Chromecast devices

### Images Not Loading
- Verify the selected directory contains supported image formats
- Check that images aren't corrupted or too large
- Look at the log files in `~/Library/Logs/Posters/` for specific error messages

### Connection Issues
- Check firewall settings
- Ensure port 5001 (Flask) and the image server port are not blocked
- Try "Restart Server" from the menu bar menu
- For persistent issues, try "Show Debug Log" to view detailed logs

### Python/Server Issues
- The app automatically falls back to system Python if bundled version fails
- Check logs for Python-related errors
- Ensure you have Python 3.7+ installed if using development mode
- Try rebuilding the app if using py2app

### Performance Issues
- Large images may take longer to load on devices
- Consider using smaller image files for better performance
- Thumbnails are cached in `~/Library/Caches/Posters/thumbnails/` for faster loading
- Monitor the log files for timeout messages

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

- **macOS Integration**: Native app bundle with proper directory usage and file migration
- **Python Reliability**: Automatic fallback from bundled to system Python for maximum compatibility
- **File Management**: Uses standard macOS directories (`~/Library/Application Support`, `~/Library/Caches`, `~/Library/Logs`)
- **Images**: Served directly without resizing for optimal quality
- **Database**: SQLite database stores settings and device preferences in Application Support
- **Real-time**: WebSocket connection provides live updates
- **Caching**: Thumbnail generation happens on-demand, cached in proper system location
- **Recovery**: Error recovery ensures slideshow continues if devices disconnect
- **Migration**: Automatic migration of existing files to proper locations

## Customization

The application can be customized by modifying:

- **Timing Logic**: Edit `slideshow_controller.py` for different image distribution patterns
- **Web Interface**: Modify `templates/index.html` and `static/css/style.css` for UI changes
- **Device Management**: Update `chromecast_manager.py` for different Chromecast interactions
- **Settings**: Extend `settings_manager.py` for additional configuration options

## Building the macOS App

To build the native macOS application:

1. **Ensure dependencies are installed**:
   ```bash
   pip install -r requirements.txt
   pip install py2app
   ```

2. **Build the app bundle**:
   ```bash
   python setup.py py2app --arch=universal2
   ```

3. **Find the built app**:
   ```bash
   open dist/
   ```

The built `Posters.app` will be in the `dist/` directory and can be distributed to other macOS computers.

## Security Notes

- **Local Network Only**: Application runs on local network only (localhost:5001)
- **No Authentication**: No authentication required (designed for home/private network use)  
- **Path Protection**: Images are served from selected directory only (path traversal protection included)
- **Offline Operation**: No external network access required for core functionality
- **Data Privacy**: All user data stored locally in standard macOS directories
- **Code Signing**: App bundle supports code signing for distribution

## License

This project is provided as-is for personal and educational use.
