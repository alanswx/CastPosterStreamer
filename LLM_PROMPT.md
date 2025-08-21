# Chromecast Slideshow Project - LLM Implementation Prompt

Create a Python-based Chromecast slideshow application with the following specifications:

## Core Functionality
- Send JPEG/PNG/GIF images to multiple Chromecast devices simultaneously
- Each device displays a unique image from the selected directory
- Images cycle every 5 seconds (configurable via web UI)
- When fewer images than devices: reuse images
- When reaching end of directory: restart from beginning
- Images sent in directory order (whatever Python's file listing provides)

## Architecture Requirements
- Single integrated Python application
- Web interface for local network control
- Auto-discover Chromecast devices on network
- Serve images locally via HTTP server (don't resize images)
- SQLite database for persistent settings
- Real-time web interface updates

## Web Interface Features
- **Directory Selection**: directory selector component that allows a user to navigate a hierarchical folder structure and select a specific directory. Think of it like a simplified version of the folder tree in Windows File Explorer or macOS Finder. The folder chosen will contain the images.
- **Device Selection**: Checkboxes to select subset of discovered Chromecast devices
- **Controls**: Start/stop slideshow, timing adjustment box
- **Image Preview**: Thumbnail previews of images in selected directory
- **Error Log**: Real-time display of errors and status messages
- **Auto-save**: Automatically persist all settings (directory, devices, timing)

## Technical Specifications
- **Image Distribution**: Distribute unique images across selected devices simultaneously
- **Timing Sync**: All devices change images at approximately the same time
- **Error Handling**: Continue slideshow if devices disconnect; log errors but don't stop
- **File Formats**: Support JPEG, PNG, GIF
- **No Authentication**: No security requirements
- **Directory Scanning**: Flat scan of selected directory only (no subdirectories)

## Implementation Details
- Use catt as a library for Chromecast communication
- Use Flask for web server with WebSocket support for real-time updates
- Local HTTP server to serve images to Chromecasts
- SQLite for settings persistence
- Frontend with directory tree browser and real-time status updates

## Project Structure
```
CastPosterStreamer/
├── app.py                 # Main Flask application
├── chromecast_manager.py  # Chromecast discovery & communication
├── slideshow_controller.py # Image rotation logic & timing
├── settings_manager.py    # Persistent settings & database
├── static/
│   ├── css/
│   ├── js/               # Frontend JS for real-time updates
│   └── thumbnails/       # Generated image previews
├── templates/
│   └── index.html        # Single-page web interface
├── requirements.txt
└── config.db             # SQLite for settings persistence
```

Build a complete, working implementation that meets all these requirements.
