#!/usr/bin/env python3
"""
Subprocess wrapper for Chromecast operations to avoid asyncio threading conflicts.
"""

import sys
import json
import argparse


def discover_devices():
    """Discover Chromecast devices."""
    try:
        from catt.discovery import get_cast_infos
        
        cast_infos = get_cast_infos()
        devices = []
        for cast_info in cast_infos:
            device_info = {
                "uuid": str(cast_info.uuid),
                "name": cast_info.friendly_name,
                "host": cast_info.host,
                "port": cast_info.port,
                "model": cast_info.model_name,
                "manufacturer": cast_info.manufacturer,
                "status": "available",
                "cast_type": "cast"
            }
            devices.append(device_info)
        
        return {"success": True, "devices": devices}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def send_image(device_name, image_url):
    """Send an image to a specific Chromecast device."""
    try:
        from catt.discovery import get_cast_with_name
        
        # Get the cast device
        cast = get_cast_with_name(device_name)
        if not cast:
            return {"success": False, "error": f"Device '{device_name}' not found"}
        
        # Wait for connection
        cast.wait()
        
        # Send image
        mc = cast.media_controller
        mc.play_media(image_url, 'image/jpeg')
        mc.block_until_active()
        
        return {"success": True, "message": f"Image sent to {device_name}"}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_device_status(device_name):
    """Get status of a Chromecast device."""
    try:
        from catt.discovery import get_cast_with_name
        
        cast = get_cast_with_name(device_name)
        if not cast:
            return {"success": False, "error": f"Device '{device_name}' not found"}
        
        cast.wait()
        
        status_info = {
            "connected": cast.socket_client.is_connected if hasattr(cast, 'socket_client') else True,
            "app_name": getattr(cast, 'app_display_name', 'Unknown'),
            "status": str(cast.status) if hasattr(cast, 'status') else 'Unknown'
        }
        
        return {"success": True, "status": status_info}
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description='Chromecast subprocess operations')
    parser.add_argument('operation', choices=['discover', 'send_image', 'get_status'],
                       help='Operation to perform')
    parser.add_argument('--device-name', help='Name of Chromecast device')
    parser.add_argument('--image-url', help='URL of image to send')
    
    args = parser.parse_args()
    
    result = {"success": False, "error": "Unknown operation"}
    
    if args.operation == 'discover':
        result = discover_devices()
    elif args.operation == 'send_image':
        if not args.device_name or not args.image_url:
            result = {"success": False, "error": "device-name and image-url required for send_image"}
        else:
            result = send_image(args.device_name, args.image_url)
    elif args.operation == 'get_status':
        if not args.device_name:
            result = {"success": False, "error": "device-name required for get_status"}
        else:
            result = get_device_status(args.device_name)
    
    print(json.dumps(result))


if __name__ == '__main__':
    main()