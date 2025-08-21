import sqlite3
import json
import os
from typing import Optional, Dict, List, Any
from pathlib import Path


class SettingsManager:
    def __init__(self, db_path: str = "config.db"):
        self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        """Initialize the SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Settings table for key-value configuration
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Chromecast devices table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    uuid TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            
            # Set default values if they don't exist
            self.set_default_settings()
    
    def set_default_settings(self):
        """Set default configuration values."""
        defaults = {
            'slideshow_interval': '5',
            'selected_directory': os.path.expanduser('~'),
            'http_server_port': '0',  # Auto-select
            'thumbnail_size': '150',
            'rotation_enabled': 'true'  # Enable rotation by default
        }
        
        for key, value in defaults.items():
            if self.get_setting(key) is None:
                self.save_setting(key, value)
    
    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value by key."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            result = cursor.fetchone()
            return result[0] if result else None
    
    def save_setting(self, key: str, value: str):
        """Save or update a setting."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, value))
            conn.commit()
    
    def get_all_settings(self) -> Dict[str, str]:
        """Get all settings as a dictionary."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            return dict(cursor.fetchall())
    
    def save_device(self, uuid: str, name: str, host: str, port: int, enabled: bool = True):
        """Save or update a Chromecast device."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO devices (uuid, name, host, port, enabled, last_seen)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (str(uuid), str(name), str(host), int(port), int(enabled)))
            conn.commit()
    
    def get_enabled_devices(self) -> List[Dict[str, Any]]:
        """Get all enabled Chromecast devices."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT uuid, name, host, port, enabled, last_seen
                FROM devices WHERE enabled = 1
                ORDER BY name
            """)
            
            columns = ['uuid', 'name', 'host', 'port', 'enabled', 'last_seen']
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def get_all_devices(self) -> List[Dict[str, Any]]:
        """Get all Chromecast devices (enabled and disabled)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT uuid, name, host, port, enabled, last_seen
                FROM devices
                ORDER BY name
            """)
            
            columns = ['uuid', 'name', 'host', 'port', 'enabled', 'last_seen']
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    def toggle_device(self, uuid: str, enabled: bool):
        """Enable or disable a specific device."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE devices SET enabled = ? WHERE uuid = ?
            """, (int(enabled), uuid))
            conn.commit()
    
    def remove_device(self, uuid: str):
        """Remove a device from the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM devices WHERE uuid = ?", (uuid,))
            conn.commit()
    
    def get_slideshow_interval(self) -> int:
        """Get slideshow interval in seconds."""
        interval = self.get_setting('slideshow_interval')
        return int(interval) if interval else 5
    
    def get_selected_directory(self) -> str:
        """Get the currently selected image directory."""
        directory = self.get_setting('selected_directory')
        return directory if directory else os.path.expanduser('~')
    
    def get_thumbnail_size(self) -> int:
        """Get thumbnail size in pixels."""
        size = self.get_setting('thumbnail_size')
        return int(size) if size else 150
    
    def is_rotation_enabled(self) -> bool:
        """Check if image rotation is enabled."""
        enabled = self.get_setting('rotation_enabled')
        return enabled and enabled.lower() == 'true'