#!/usr/bin/env python3

import time
from settings_manager import SettingsManager
from chromecast_manager import ChromecastManager  
from slideshow_controller import SlideshowController

def test_rotation_setting():
    print("=== Testing Rotation Setting ===")
    
    # Initialize components
    settings_manager = SettingsManager('test_rotation.db')
    chromecast_manager = ChromecastManager(settings_manager)
    slideshow_controller = SlideshowController(settings_manager, chromecast_manager)
    
    # Test default setting
    print(f"Default rotation enabled: {settings_manager.is_rotation_enabled()}")
    
    # Test setting rotation to false
    settings_manager.save_setting('rotation_enabled', 'false')
    print(f"After setting to false: {settings_manager.is_rotation_enabled()}")
    
    # Test setting rotation to true
    settings_manager.save_setting('rotation_enabled', 'true')
    print(f"After setting to true: {settings_manager.is_rotation_enabled()}")
    
    # Test all settings
    all_settings = settings_manager.get_all_settings()
    print(f"All settings: {all_settings}")
    
    print("✓ Rotation setting test completed successfully!")

if __name__ == '__main__':
    test_rotation_setting()