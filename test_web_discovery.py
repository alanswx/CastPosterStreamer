#!/usr/bin/env python3

import requests
import time
import socketio
import json

def test_web_discovery():
    print("=== Testing Web Interface Discovery ===")
    
    # Test basic API endpoints first
    try:
        print("1. Testing basic API endpoints...")
        
        # Test settings endpoint
        response = requests.get('http://localhost:5001/api/settings', timeout=5)
        if response.status_code == 200:
            print("✓ Settings API working")
        else:
            print(f"✗ Settings API failed: {response.status_code}")
            return False
            
        # Test devices endpoint  
        response = requests.get('http://localhost:5001/api/devices', timeout=5)
        if response.status_code == 200:
            print("✓ Devices API working")
        else:
            print(f"✗ Devices API failed: {response.status_code}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"✗ API test failed: {e}")
        return False
    
    # Test WebSocket discovery
    try:
        print("2. Testing WebSocket discovery...")
        
        # Create SocketIO client
        sio = socketio.SimpleClient()
        
        # Connect to server
        sio.connect('http://localhost:5001')
        print("✓ WebSocket connected")
        
        # Set up event handlers
        devices_found = []
        error_message = None
        
        def on_devices_discovered(data):
            nonlocal devices_found
            devices_found = data
            print(f"✓ Devices discovered: {len(data)} devices")
            for device in data:
                print(f"  - {device['name']} ({device['host']}:{device['port']})")
        
        def on_error(data):
            nonlocal error_message
            error_message = data.get('message', 'Unknown error')
            print(f"✗ Discovery error: {error_message}")
        
        # Register event handlers
        sio.on('devices_discovered', on_devices_discovered)
        sio.on('error', on_error)
        
        # Trigger discovery
        print("Triggering device discovery...")
        sio.emit('discover_devices')
        
        # Wait for response (up to 20 seconds)
        timeout = 20
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            sio.sleep(0.5)  # Check every 500ms
            
            if devices_found:
                print("✓ Discovery successful!")
                break
            elif error_message:
                print(f"✗ Discovery failed: {error_message}")
                break
        else:
            print("✗ Discovery timeout")
            return False
        
        # Disconnect
        sio.disconnect()
        
        return len(devices_found) > 0
        
    except Exception as e:
        print(f"✗ WebSocket test failed: {e}")
        return False

if __name__ == '__main__':
    # Test if server is running first
    try:
        response = requests.get('http://localhost:5001/', timeout=2)
        if response.status_code != 200:
            print("Server not responding properly. Start the app with: python3 app.py")
            exit(1)
    except requests.exceptions.RequestException:
        print("Server not running. Start the app with: python3 app.py")
        exit(1)
    
    success = test_web_discovery()
    print(f"\n=== Test {'PASSED' if success else 'FAILED'} ===")