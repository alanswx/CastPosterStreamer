import threading
import socket
import http.server
import socketserver
import os
import logging
import mimetypes
from pathlib import Path
from typing import Optional
from urllib.parse import unquote


class ImageHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP request handler for serving images."""
    
    def __init__(self, *args, image_directory=None, **kwargs):
        self.image_directory = image_directory or os.getcwd()
        super().__init__(*args, **kwargs)
    
    def translate_path(self, path):
        """Translate URL path to local file system path."""
        # Remove query parameters and decode URL
        path = path.split('?', 1)[0]
        path = path.split('#', 1)[0]
        path = unquote(path, errors='replace')
        
        # Remove leading slash and join with image directory
        if path.startswith('/'):
            path = path[1:]
        
        # Construct the full path
        full_path = os.path.join(self.image_directory, path)
        
        # Security check: ensure the path is within the image directory
        try:
            real_image_dir = os.path.realpath(self.image_directory)
            real_full_path = os.path.realpath(full_path)
            
            if not real_full_path.startswith(real_image_dir):
                # Path traversal attempt, return image directory instead
                return self.image_directory
                
        except (OSError, ValueError):
            return self.image_directory
        
        return full_path
    
    def guess_type(self, path):
        """Guess content type for image files."""
        base, ext = os.path.splitext(path.lower())
        
        # Map common image extensions
        image_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
            '.svg': 'image/svg+xml'
        }
        
        if ext in image_types:
            return image_types[ext], None
        
        # Fall back to standard guess
        return mimetypes.guess_type(path)
    
    def log_message(self, format, *args):
        """Override to use custom logger."""
        logger = logging.getLogger('ImageServer')
        logger.info(format % args)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Threaded HTTP server for concurrent image serving."""
    allow_reuse_address = True
    daemon_threads = True


class ImageServer:
    """HTTP server for serving images to Chromecast devices."""
    
    def __init__(self, image_directory: str = None):
        self.image_directory = image_directory or os.getcwd()
        self.server = None
        self.server_thread = None
        self.host = '0.0.0.0'
        self.port = None
        self.is_running = False
        self.logger = logging.getLogger(__name__)
    
    def _find_available_port(self, start_port: int = 8000, max_attempts: int = 100) -> Optional[int]:
        """Find an available port starting from start_port."""
        for port in range(start_port, start_port + max_attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.bind(('', port))
                    return port
            except OSError:
                continue
        return None
    
    def start(self, port: int = 0) -> bool:
        """Start the HTTP image server."""
        if self.is_running:
            self.logger.warning("Image server is already running")
            return True
        
        try:
            # Find an available port if not specified
            if port == 0:
                port = self._find_available_port()
                if port is None:
                    self.logger.error("Could not find an available port")
                    return False
            
            # Create request handler with image directory
            def handler_class(*args, **kwargs):
                return ImageHTTPRequestHandler(*args, image_directory=self.image_directory, **kwargs)
            
            # Create and configure server
            self.server = ThreadedHTTPServer((self.host, port), handler_class)
            self.port = port
            
            # Start server in background thread
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            
            self.is_running = True
            self.logger.info(f"Image server started on http://{self.host}:{self.port}")
            self.logger.info(f"Serving images from: {self.image_directory}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start image server: {e}")
            return False
    
    def stop(self):
        """Stop the HTTP image server."""
        if not self.is_running:
            return
        
        try:
            if self.server:
                self.server.shutdown()
                self.server.server_close()
            
            if self.server_thread:
                self.server_thread.join(timeout=5)
            
            self.is_running = False
            self.logger.info("Image server stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping image server: {e}")
    
    def get_server_url(self) -> Optional[str]:
        """Get the base URL of the image server."""
        if not self.is_running or not self.port:
            return None
        
        # Get local IP address for better Chromecast compatibility
        try:
            # Connect to a remote address to get local IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                local_ip = sock.getsockname()[0]
            return f"http://{local_ip}:{self.port}"
        except:
            # Fall back to localhost if we can't determine local IP
            return f"http://localhost:{self.port}"
    
    def get_image_url(self, image_path: str) -> Optional[str]:
        """Get the full URL for a specific image."""
        base_url = self.get_server_url()
        if not base_url:
            return None
        
        # Get relative path from image directory
        try:
            image_path = Path(image_path)
            image_dir = Path(self.image_directory)
            
            if image_path.is_absolute():
                # Convert absolute path to relative
                rel_path = image_path.relative_to(image_dir)
            else:
                rel_path = image_path
            
            # URL encode the path
            url_path = str(rel_path).replace('\\', '/')
            return f"{base_url}/{url_path}"
            
        except Exception as e:
            self.logger.error(f"Error generating URL for {image_path}: {e}")
            return None
    
    def set_image_directory(self, directory: str):
        """Change the image directory (requires server restart)."""
        if not os.path.isdir(directory):
            raise ValueError(f"Directory does not exist: {directory}")
        
        old_directory = self.image_directory
        self.image_directory = directory
        
        if self.is_running:
            self.logger.info(f"Image directory changed from {old_directory} to {directory}")
            self.logger.info("Note: Server restart may be required for changes to take effect")
    
    def list_images(self) -> list:
        """List all image files in the current directory."""
        if not os.path.isdir(self.image_directory):
            return []
        
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}
        images = []
        
        try:
            for item in os.listdir(self.image_directory):
                if os.path.isfile(os.path.join(self.image_directory, item)):
                    _, ext = os.path.splitext(item.lower())
                    if ext in image_extensions:
                        images.append(item)
            
            return sorted(images)
            
        except Exception as e:
            self.logger.error(f"Error listing images: {e}")
            return []


# Global image server instance
image_server = None


def get_image_server() -> ImageServer:
    """Get the global image server instance."""
    global image_server
    if image_server is None:
        image_server = ImageServer()
    return image_server