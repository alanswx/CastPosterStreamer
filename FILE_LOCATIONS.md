# Posters App File Location Implementation

This document describes the implemented file structure for the Posters app, which now follows proper macOS guidelines.

## ✅ **IMPLEMENTED - Current File Locations**

### Database Files
- **Location**: `~/Library/Application Support/Posters/config.db`
- **Implementation**: SettingsManager automatically creates directory and migrates existing files
- **Status**: ✅ **FIXED** - No more read-only errors, survives app updates

### Configuration Files  
- **Location**: `~/Library/Application Support/Posters/menu_config.json`
- **Implementation**: SettingsManager creates with defaults if missing, migrates from old location
- **Status**: ✅ **FIXED** - Proper user data location, survives updates

### Thumbnail Cache
- **Location**: `~/Library/Caches/Posters/thumbnails/`
- **Implementation**: Updated slideshow_controller.py and app.py to use cache directory
- **Status**: ✅ **FIXED** - Proper cache location, can grow as needed, expendable data

### Log Files
- **Location**: `~/Library/Logs/Posters/`
  - **Bundle**: `app_bundle.log`
  - **Development**: `app_debug.log`
- **Implementation**: Updated menu_bar_app.py to use proper logs directory  
- **Status**: ✅ **FIXED** - No more Desktop clutter, proper system location

## ✅ **IMPLEMENTED - macOS Directory Structure**

```
~/Library/Application Support/Posters/
├── config.db                    # SQLite database for settings and devices
└── menu_config.json             # Menu configuration (port settings)

~/Library/Caches/Posters/
└── thumbnails/                  # Generated thumbnail images (hash-named)
    ├── a1b2c3d4...jpg          # MD5 hash of original image path
    ├── e5f6g7h8...jpg
    └── ...

~/Library/Logs/Posters/
├── app_bundle.log               # Application logs (bundled app)
└── app_debug.log                # Application logs (development)
```

## ✅ **IMPLEMENTED - Files Accessed by Components**

### SettingsManager (settings_manager.py)
- **Opens**: `config.db` (SQLite database)
- **Operations**: Read/write settings, device configurations, menu config
- **Location**: `~/Library/Application Support/Posters/config.db`
- **Status**: ✅ **IMPLEMENTED** - Auto-creates directories, migrates old files

### SlideshowController (slideshow_controller.py)
- **Creates**: Thumbnail images with MD5 hash filenames
- **Operations**: Generate and cache image thumbnails
- **Location**: `~/Library/Caches/Posters/thumbnails/`
- **Status**: ✅ **IMPLEMENTED** - Uses proper cache directory via SettingsManager

### MenuBarApp (menu_bar_app.py)
- **Creates**: Log files in proper system location
- **Locations**: 
  - `~/Library/Logs/Posters/app_bundle.log` (bundled app)
  - `~/Library/Logs/Posters/app_debug.log` (development)
- **Status**: ✅ **IMPLEMENTED** - No more Desktop clutter

### Flask App (app.py)
- **Serves**: Thumbnail images from cache directory
- **Accesses**: Database via SettingsManager
- **Location**: Serves from `~/Library/Caches/Posters/thumbnails/`
- **Status**: ✅ **IMPLEMENTED** - Updated to use proper cache location

### Setup (setup.py)
- **Bundles**: Only code files and static web assets
- **Removed**: `config.db` and `menu_config.json` from bundle
- **Status**: ✅ **IMPLEMENTED** - User data files created at runtime

## ✅ **IMPLEMENTED - Migration Features**

### Automatic Migration
- **Database**: Copies `config.db` from app bundle to Application Support
- **Menu Config**: Copies `menu_config.json` from app bundle to Application Support  
- **Thumbnails**: Moves entire `static/thumbnails/` to Caches directory
- **Error Handling**: Graceful fallback if migration fails
- **User Feedback**: Console messages inform about file movements

### Runtime Creation
- **Directories**: All required directories created automatically on startup
- **Defaults**: Menu config created with default values if missing
- **Permissions**: Proper directory permissions for user data

## ✅ **macOS Guidelines Compliance**

- ✅ **Application Support**: Used for `config.db` and `menu_config.json`
- ✅ **Caches**: Used for regeneratable thumbnail images
- ✅ **Logs**: Used for diagnostic log files
- ✅ **No Desktop**: Log files moved to proper system location
- ✅ **No App Bundle**: User data no longer stored in read-only bundle

## ✅ **Security & Best Practices**

- ✅ **Code Signing Safe**: No app bundle modifications after installation
- ✅ **Update Survival**: User data preserved during app updates
- ✅ **Sandboxing Ready**: Uses standard macOS directories
- ✅ **Cache Management**: Thumbnails in expendable cache location
- ✅ **User Privacy**: Data stored in user's private Library folder

## ✅ **Implementation Status: COMPLETE**

All file location issues have been resolved:

1. ✅ **Database errors fixed** - Can now write to proper location
2. ✅ **Configuration preserved** - Survives app updates
3. ✅ **Desktop cleanup** - No more log file clutter  
4. ✅ **Proper caching** - Thumbnails in appropriate location
5. ✅ **Migration handled** - Existing files automatically moved
6. ✅ **Apple compliance** - Follows all macOS file system guidelines

The app now follows Apple's recommended file system layout and should work correctly on any macOS system, including when deployed to other computers.