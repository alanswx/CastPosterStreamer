#!/usr/bin/env python3

import sys
import time
from chromecast_manager import ChromecastManager
from settings_manager import SettingsManager

def main():
    print("=== Chromecast Discovery Test ===")
    
    try:
        # Initialize managers
        print("Initializing managers...")
        settings_manager = SettingsManager('test_discovery.db')
        chromecast_manager = ChromecastManager(settings_manager)
        
        # Test discovery
        print("Starting discovery...")
        devices = chromecast_manager.discover_devices()
        
        print(f"Discovery completed! Found {len(devices)} devices:")
        for i, device in enumerate(devices, 1):
            print(f"  {i}. {device['name']}")
            print(f"     Host: {device['host']}:{device['port']}")
            print(f"     UUID: {device['uuid']}")
            print(f"     Model: {device['model']}")
            print()
        
        if devices:
            print("Testing connection to first device...")
            device = devices[0]
            cast = chromecast_manager.connect_to_device(device['uuid'])
            
            if cast:
                print(f"✓ Successfully connected to {device['name']}")
                print(f"  Status: {cast.status}")
                
                # Test image sending if we have our test image server
                try:
                    from image_server import ImageServer
                    
                    print("Starting image server...")
                    image_server = ImageServer('/Users/alans/test-images')
                    if image_server.start():
                        print(f"✓ Image server started: {image_server.get_server_url()}")
                        
                        # Try to send an image
                        image_url = image_server.get_image_url('test1.jpg')
                        print(f"Sending test image: {image_url}")
                        
                        success = chromecast_manager.send_image_to_device(device['uuid'], image_url)
                        if success:
                            print("✓ Image sent successfully!")
                        else:
                            print("✗ Failed to send image")
                        
                        time.sleep(2)
                        image_server.stop()
                    else:
                        print("✗ Failed to start image server")
                        
                except Exception as e:
                    print(f"Image test error: {e}")
                
            else:
                print(f"✗ Failed to connect to {device['name']}")
        
        print("\n=== Test Complete ===")
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()