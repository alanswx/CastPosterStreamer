# CastPosterStreamer (Posters)

macOS menu bar app that displays synchronized image slideshows across multiple Chromecast/Google TV devices on the local network.

## Architecture

- **Backend**: Python (Flask + Flask-SocketIO) — `app.py` is the main server entry point
- **Frontend**: Single-page app in `templates/index.html` + `static/js/app.js`, communicates over WebSocket (Socket.IO) and REST
- **macOS menu bar**: `menu_bar_app.py` using `rumps`
- **Chromecast operations**: Isolated in a subprocess (`chromecast_subprocess.py`) to avoid asyncio/threading conflicts with Flask-SocketIO
- **Image server**: Dedicated HTTP server (`image_server.py`) on a dynamically chosen port, separate from Flask

## Key Files

| File | Role |
|------|------|
| `app.py` | Flask server, HTTP endpoints, WebSocket event handlers |
| `menu_bar_app.py` | macOS menu bar app; spawns Flask server |
| `chromecast_manager.py` | Device discovery & coordination |
| `chromecast_subprocess.py` | Chromecast operations run in subprocess |
| `slideshow_controller.py` | Image rotation logic and timing |
| `settings_manager.py` | SQLite persistence, macOS directory paths |
| `image_server.py` | Serves images to Chromecast devices over HTTP |
| `setup.py` | py2app config for building the `.app` bundle |

## macOS File Locations

| Purpose | Path |
|---------|------|
| Settings / database | `~/Library/Application Support/Posters/config.db` |
| Menu config | `~/Library/Application Support/Posters/menu_config.json` |
| Thumbnail cache | `~/Library/Caches/Posters/thumbnails/` |
| Logs | `~/Library/Logs/Posters/` |

## Running in Development

```bash
pip install -r requirements.txt

# Run menu bar app (also starts Flask on port 5001)
python menu_bar_app.py

# Or run just the web server
python app.py
```

Web UI: `http://localhost:5001`

## Building the macOS App

```bash
pip install py2app
python setup.py py2app --arch=universal2
# Output: dist/Posters.app
```

## Default Settings

| Setting | Default |
|---------|---------|
| Flask port | 5001 |
| Image server port | auto |
| Slideshow interval | 5 seconds |
| Selected directory | `~` |
| Thumbnail size | 150px |
| Rotation | enabled |

New discovered devices default to **disabled** (require manual enablement to prevent accidental casting).

## Supported Image Formats

`.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.webp`

## Key Design Decisions

- **Subprocess isolation**: Chromecast uses asyncio internally; running it in a subprocess avoids conflicts with Flask-SocketIO's threading model.
- **Two HTTP servers**: Flask (port 5001) serves the web UI; a separate server on a dynamic port serves images to Chromecast devices.
- **WebSocket push**: Server pushes status changes to all connected browsers; no polling.
- **Universal2 binary**: Built for both Apple Silicon (ARM64) and Intel (x86_64).
- **No tests**: The project currently has no test suite.
- **No environment variables**: Config lives in the SQLite database and `menu_config.json`; defaults are hardcoded.
