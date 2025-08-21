#!/usr/bin/env python3

import rumps
import subprocess
import webbrowser
import sys
from pathlib import Path

class PosterApp(rumps.App):
    def __init__(self):
        super(PosterApp, self).__init__("Posters", "📺", quit_button=None)
        self.server_process = None
        
        # Simple menu with quit handler
        self.menu = [
            rumps.MenuItem("Open Web Interface", callback=self.open_browser),
            rumps.separator,
            rumps.MenuItem("Quit", callback=self.quit_application)
        ]
        
        # Start server immediately
        self.start_server()
    
    def start_server(self):
        """Start the Flask server."""
        try:
            app_path = Path(__file__).parent / "app.py"
            if app_path.exists():
                self.server_process = subprocess.Popen([
                    sys.executable, str(app_path)
                ], cwd=str(Path(__file__).parent))
        except Exception as e:
            rumps.alert("Error", f"Failed to start server: {e}")
    
    def open_browser(self, sender):
        """Open the web interface in the default browser."""
        webbrowser.open("http://localhost:5001")
    
    def quit_application(self, sender):
        """Clean shutdown."""
        if self.server_process:
            try:
                self.server_process.terminate()
                self.server_process.wait(timeout=3)
            except:
                try:
                    self.server_process.kill()
                except:
                    pass
        rumps.quit_application()

if __name__ == "__main__":
    app = PosterApp()
    app.run()