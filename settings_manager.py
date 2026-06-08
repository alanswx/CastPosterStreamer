import sqlite3
import json
import os
import sys
import shutil
from typing import Optional, Dict, List, Any
from pathlib import Path


class SettingsManager:
    def __init__(self, db_name: str = "config.db", menu_config_name: str = "menu_config.json"):
        
        # Use proper macOS directories
        self.app_support_dir = Path.home() / "Library" / "Application Support" / "Posters"
        self.cache_dir = Path.home() / "Library" / "Caches" / "Posters"
        self.logs_dir = Path.home() / "Library" / "Logs" / "Posters"
        
        # Create directories if they don't exist
        self.app_support_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        
        # Set file paths
        self.db_path = self.app_support_dir / db_name
        self.menu_config_path = self.app_support_dir / menu_config_name
        
        # Migrate existing files if needed
        self._migrate_existing_files()
        
        # Initialize database and config
        self.init_database()
        self.init_menu_config()
    
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
            
            # Playlist items table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    directory_path TEXT NOT NULL,
                    directory_name TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 10,
                    order_index INTEGER NOT NULL,
                    is_valid INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Saved playlists table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS saved_playlists (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    items TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    
    def save_device(self, uuid: str, name: str, host: str, port: int, enabled: bool = None):
        """Save or update a Chromecast device."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if device already exists
            cursor.execute("SELECT enabled FROM devices WHERE uuid = ?", (str(uuid),))
            existing = cursor.fetchone()
            
            # If device exists and enabled is not explicitly set, preserve existing state
            # If device is new and enabled is not explicitly set, default to False
            if enabled is None:
                enabled = bool(existing[0]) if existing else False
            
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
    
    def _migrate_existing_files(self):
        """Migrate existing files from old locations to new proper locations."""
        # Get script directory (where old files might be)
        if getattr(sys, 'frozen', False):
            # Bundled app - check Resources directory
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        else:
            # Development environment
            script_dir = Path(os.path.dirname(os.path.abspath(__file__)))
        
        # Migrate database
        old_db_path = script_dir / "config.db"
        if old_db_path.exists() and not self.db_path.exists():
            try:
                shutil.copy2(old_db_path, self.db_path)
                print(f"Migrated database from {old_db_path} to {self.db_path}")
            except Exception as e:
                print(f"Warning: Could not migrate database: {e}")
        
        # Migrate menu config
        old_menu_config = script_dir / "menu_config.json"
        if old_menu_config.exists() and not self.menu_config_path.exists():
            try:
                shutil.copy2(old_menu_config, self.menu_config_path)
                print(f"Migrated menu config from {old_menu_config} to {self.menu_config_path}")
            except Exception as e:
                print(f"Warning: Could not migrate menu config: {e}")
        
        # Migrate thumbnails
        old_thumbnail_dir = script_dir / "static" / "thumbnails"
        new_thumbnail_dir = self.cache_dir / "thumbnails"
        if old_thumbnail_dir.exists() and not new_thumbnail_dir.exists():
            try:
                shutil.copytree(old_thumbnail_dir, new_thumbnail_dir)
                print(f"Migrated thumbnails from {old_thumbnail_dir} to {new_thumbnail_dir}")
            except Exception as e:
                print(f"Warning: Could not migrate thumbnails: {e}")
    
    def init_menu_config(self):
        """Initialize menu configuration file with defaults if it doesn't exist."""
        if not self.menu_config_path.exists():
            default_config = {
                "port": 5002
            }
            try:
                with open(self.menu_config_path, 'w') as f:
                    json.dump(default_config, f, indent=2)
                print(f"Created default menu config at {self.menu_config_path}")
            except Exception as e:
                print(f"Warning: Could not create menu config: {e}")
    
    def get_menu_config(self) -> Dict[str, Any]:
        """Get menu configuration as a dictionary."""
        try:
            if self.menu_config_path.exists():
                with open(self.menu_config_path, 'r') as f:
                    return json.load(f)
            else:
                # Return defaults if file doesn't exist
                return {"port": 5002}
        except Exception as e:
            print(f"Warning: Could not read menu config: {e}")
            return {"port": 5002}
    
    def save_menu_config(self, config: Dict[str, Any]):
        """Save menu configuration to file."""
        try:
            with open(self.menu_config_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Error: Could not save menu config: {e}")
    
    def get_cache_dir(self) -> Path:
        """Get the cache directory path."""
        return self.cache_dir
    
    def get_logs_dir(self) -> Path:
        """Get the logs directory path."""
        return self.logs_dir
    
    def get_thumbnail_dir(self) -> Path:
        """Get the thumbnail cache directory path."""
        thumbnail_dir = self.cache_dir / "thumbnails"
        thumbnail_dir.mkdir(exist_ok=True)
        return thumbnail_dir
    
    # Playlist management methods
    def add_playlist_item(self, directory_path: str, directory_name: str, duration_minutes: int = 10) -> int:
        """Add a new item to the playlist."""
        import os
        is_valid = 1 if os.path.exists(directory_path) and os.path.isdir(directory_path) else 0
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Get the next order index
            cursor.execute("SELECT MAX(order_index) FROM playlist_items")
            max_order = cursor.fetchone()[0]
            next_order = (max_order or 0) + 1
            
            cursor.execute("""
                INSERT INTO playlist_items (directory_path, directory_name, duration_minutes, order_index, is_valid)
                VALUES (?, ?, ?, ?, ?)
            """, (directory_path, directory_name, duration_minutes, next_order, is_valid))
            
            conn.commit()
            return cursor.lastrowid
    
    def get_playlist_items(self) -> List[Dict[str, Any]]:
        """Get all playlist items ordered by order_index."""
        import os
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, directory_path, directory_name, duration_minutes, order_index, is_valid, created_at
                FROM playlist_items
                ORDER BY order_index
            """)
            
            columns = ['id', 'directory_path', 'directory_name', 'duration_minutes', 'order_index', 'is_valid', 'created_at']
            items = [dict(zip(columns, row)) for row in cursor.fetchall()]
            
            # Validate directory paths and update is_valid flag
            for item in items:
                current_valid = os.path.exists(item['directory_path']) and os.path.isdir(item['directory_path'])
                if bool(item['is_valid']) != current_valid:
                    self.update_playlist_item_validity(item['id'], current_valid)
                    item['is_valid'] = 1 if current_valid else 0
            
            return items
    
    def update_playlist_item_validity(self, item_id: int, is_valid: bool):
        """Update the validity status of a playlist item."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE playlist_items SET is_valid = ? WHERE id = ?
            """, (1 if is_valid else 0, item_id))
            conn.commit()
    
    def remove_playlist_item(self, item_id: int):
        """Remove an item from the playlist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM playlist_items WHERE id = ?", (item_id,))
            conn.commit()
            
            # Reorder remaining items to fill gaps
            self._reorder_playlist_items()
    
    def update_playlist_item_duration(self, item_id: int, duration_minutes: int):
        """Update the duration of a playlist item."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE playlist_items SET duration_minutes = ? WHERE id = ?
            """, (duration_minutes, item_id))
            conn.commit()
    
    def reorder_playlist_items(self, item_ids: List[int]):
        """Reorder playlist items based on provided list of IDs."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for index, item_id in enumerate(item_ids):
                cursor.execute("""
                    UPDATE playlist_items SET order_index = ? WHERE id = ?
                """, (index + 1, item_id))
            conn.commit()
    
    def _reorder_playlist_items(self):
        """Internal method to reorder playlist items to fill gaps."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM playlist_items ORDER BY order_index")
            item_ids = [row[0] for row in cursor.fetchall()]
            
            for index, item_id in enumerate(item_ids):
                cursor.execute("""
                    UPDATE playlist_items SET order_index = ? WHERE id = ?
                """, (index + 1, item_id))
            conn.commit()
    
    def clear_playlist(self):
        """Remove all items from the playlist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM playlist_items")
            conn.commit()
    
    def get_playlist_total_duration(self) -> int:
        """Get total duration of all valid playlist items in minutes."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT SUM(duration_minutes) FROM playlist_items WHERE is_valid = 1")
            result = cursor.fetchone()[0]
            return result or 0

    # Saved playlist methods
    def list_saved_playlists(self) -> List[Dict[str, Any]]:
        """List all saved playlists (id, name, updated_at) without full items."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, updated_at FROM saved_playlists ORDER BY updated_at DESC
            """)
            columns = ['id', 'name', 'updated_at']
            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def save_playlist(self, name: str, items: List[Dict[str, Any]], playlist_id: int = None) -> int:
        """Create or update a saved playlist. Returns the playlist id."""
        items_json = json.dumps(items)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if playlist_id is not None:
                cursor.execute("""
                    UPDATE saved_playlists SET name = ?, items = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (name, items_json, playlist_id))
                conn.commit()
                return playlist_id
            else:
                cursor.execute("""
                    INSERT OR REPLACE INTO saved_playlists (name, items, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (name, items_json))
                conn.commit()
                return cursor.lastrowid

    def get_saved_playlist(self, playlist_id: int) -> Optional[Dict[str, Any]]:
        """Get a saved playlist by id, including its items."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, items, updated_at FROM saved_playlists WHERE id = ?
            """, (playlist_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[0],
                'name': row[1],
                'items': json.loads(row[2]),
                'updated_at': row[3],
            }

    def delete_saved_playlist(self, playlist_id: int):
        """Delete a saved playlist."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM saved_playlists WHERE id = ?", (playlist_id,))
            conn.commit()