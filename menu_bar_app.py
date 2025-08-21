#!/usr/bin/env python3

import rumps
import subprocess
import webbrowser
import sys
import logging
import os
import time
import urllib.request
from pathlib import Path
import threading

# When bundled, the CWD is often '/', which can cause issues for file access.
# This changes the CWD to the app's Resources folder.
if getattr(sys, 'frozen', False):
    executable_path = os.path.dirname(sys.executable)
    resources_path = os.path.abspath(os.path.join(executable_path, '..', 'Resources'))
    os.chdir(resources_path)
    # Use a different log file for bundled app to avoid confusion
    log_file = Path.home() / "Desktop" / "Posters_bundle.log"
else:
    log_file = Path.home() / "Desktop" / "Posters_debug.log"

# Set up logging to file
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class PosterApp(rumps.App):
    def __init__(self):
        logger.info("Initializing Posters app")
        try:
            super(PosterApp, self).__init__("Posters", "📺", quit_button=None)
            logger.info("rumps.App initialization successful")
            self.server_process = None
            self.port = 5001
            
            # Simple menu with debugging info
            self.status_item = rumps.MenuItem("Server Status: Starting...")
            self.menu = [
                self.status_item,
                rumps.separator,
                rumps.MenuItem("Open Web Interface", callback=self.open_browser),
                rumps.MenuItem("Check Server", callback=self.check_server_manual),
                rumps.MenuItem("Restart Server", callback=self.restart_server),
                rumps.separator,
                rumps.MenuItem("Show Debug Log", callback=self.show_log),
                rumps.separator,
                rumps.MenuItem("Quit", callback=self.quit_application)
            ]
            
            # Start server immediately with logging
            logger.info("Starting server on initialization")
            self.start_server()
            logger.info("App initialization completed successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize app: {e}", exc_info=True)
            # Don't crash, try to show an error
            try:
                rumps.alert("Posters Error", f"Failed to initialize: {e}")
            except:
                pass
            raise
    
    def _log_server_output(self, stream):
        """Read and log output from a stream in a separate thread."""
        for line in iter(stream.readline, ''):
            logger.info(f"Server: {line.strip()}")
        stream.close()
        logger.info("Server output stream closed.")
    
    def start_server(self):
        """Start the Flask server with detailed logging."""
        logger.info("start_server() called")
        
        if self.server_process:
            logger.info("Server process already exists, checking if alive")
            if self.server_process.poll() is None:
                logger.info("Server process is still running")
                return
            else:
                logger.info("Server process died, cleaning up")
                self.server_process = None
        
        try:
            app_path = Path(__file__).parent / "app.py"
            logger.info(f"Looking for app.py at: {app_path}")
            logger.info(f"app.py exists: {app_path.exists()}")
            logger.info(f"Current working directory: {os.getcwd()}")
            logger.info(f"Script directory: {Path(__file__).parent}")
            
            if app_path.exists():
                logger.info(f"Starting server with Python: {sys.executable}")
                self.status_item.title = "Server Status: Starting..."
                
                # Use system Python but with virtual environment packages
                logger.info("Using system Python with bundled packages")
                python_cmd = sys.executable
                
                # Check architecture and build command
                import platform
                if platform.machine() == 'arm64':
                    logger.info("Using ARM64 architecture for server")
                    cmd = ['arch', '-arm64', python_cmd, str(app_path)]
                else:
                    logger.info("Using x86_64 architecture for server")
                    cmd = [python_cmd, str(app_path)]
                
                logger.info(f"Server command: {' '.join(cmd)}")
                
                # Set environment to ensure native architecture and use bundled packages
                env = os.environ.copy()
                env['ARCHFLAGS'] = '-arch arm64' if platform.machine() == 'arm64' else '-arch x86_64'
                
                # Always use bundled virtual environment packages
                venv_site_packages = Path(__file__).parent / "lib" / "python3.9"
                if venv_site_packages.exists():
                    env['PYTHONPATH'] = f"{venv_site_packages}:{env.get('PYTHONPATH', '')}"
                    logger.info(f"Added to PYTHONPATH: {venv_site_packages}")
                else:
                    logger.warning("Bundled packages not found, using system packages")
                
                # Start with output capture for debugging
                self.server_process = subprocess.Popen(
                    cmd,
                    cwd=str(Path(__file__).parent),
                    env=env,
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT, 
                    text=True,
                    bufsize=1, # Line-buffered
                    universal_newlines=True
                )
                
                # Start a thread to log the server's output
                log_thread = threading.Thread(target=self._log_server_output, args=(self.server_process.stdout,))
                log_thread.daemon = True
                log_thread.start()
                
                logger.info(f"Server process started with PID: {self.server_process.pid}")
                
                # Wait a moment and check if server started
                time.sleep(2)
                if self.is_server_running():
                    logger.info("Server is responding!")
                    self.status_item.title = "Server Status: Running"
                else:
                    logger.warning("Server not responding yet, checking process...")
                    if self.server_process.poll() is None:
                        logger.info("Process is still running, server may need more time")
                        self.status_item.title = "Server Status: Starting (slow)..."
                    else:
                        logger.error("Server process died immediately")
                        self.status_item.title = "Server Status: Failed"
                        self.server_process = None
            else:
                logger.error(f"app.py not found at {app_path}")
                self.status_item.title = "Server Status: app.py missing"
                rumps.notification("Posters", "Error", f"app.py not found at {app_path}")
                
        except Exception as e:
            logger.error(f"Failed to start server: {e}", exc_info=True)
            self.status_item.title = "Server Status: Error"
            rumps.notification("Posters", "Error", f"Failed to start server: {e}")

    def is_server_running(self):
        """Check if server is responding."""
        try:
            logger.info(f"Checking server at http://localhost:{self.port}")
            response = urllib.request.urlopen(f"http://localhost:{self.port}", timeout=2)
            logger.info(f"Server responded with status: {response.status}")
            return response.status == 200
        except Exception as e:
            logger.info(f"Server check failed: {e}")
            return False

    def check_server_manual(self, sender):
        """Manual server check for debugging."""
        logger.info("Manual server check requested")
        if self.is_server_running():
            rumps.notification("Posters", "Server Check", "✅ Server is running!")
            self.status_item.title = "Server Status: Running"
        else:
            rumps.notification("Posters", "Server Check", "❌ Server not responding")
            self.status_item.title = "Server Status: Not responding"
            
        # Also check process
        if self.server_process:
            if self.server_process.poll() is None:
                logger.info("Server process is alive")
            else:
                logger.warning("Server process is dead")
                self.server_process = None

    def restart_server(self, sender):
        """Restart the server."""
        logger.info("Server restart requested")
        if self.server_process:
            logger.info("Killing existing server process")
            self.server_process.terminate()
            try:
                self.server_process.wait(timeout=3)
            except:
                self.server_process.kill()
            self.server_process = None
            
        self.start_server()

    def show_log(self, sender):
        """Show the debug log file."""
        os.system(f"open -a Console '{log_file}'")
    
    def open_browser(self, sender):
        """Open the web interface in the default browser."""
        logger.info("Opening web interface")
        if self.is_server_running():
            logger.info(f"Opening browser to http://localhost:{self.port}")
            webbrowser.open(f"http://localhost:{self.port}")
        else:
            logger.warning("Server not running, cannot open browser")
            rumps.notification("Posters", "Error", "Server is not running. Try 'Check Server' or 'Restart Server'")
    
    def quit_application(self, sender):
        """Clean shutdown."""
        logger.info("Quit requested")
        if self.server_process:
            logger.info("Terminating server process")
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=3)
                logger.info("Server terminated cleanly")
            except:
                try:
                    self.server_process.kill()
                    logger.info("Server killed forcefully")
                except:
                    logger.error("Failed to kill server process")
        rumps.quit_application()

if __name__ == "__main__":
    try:
        logger.info("Starting main application")
        app = PosterApp()
        logger.info("App created, starting run loop")
        app.run()
    except Exception as e:
        logger.error(f"Application crashed: {e}", exc_info=True)
        try:
            rumps.alert("Posters Crash", f"Application crashed: {e}")
        except:
            pass
        raise