#!/usr/bin/env python3

from PIL import Image
import os
import shutil
import subprocess

def create_icon_from_image(source_image_path):
    """Create a .icns file from a source image."""
    
    if not os.path.exists(source_image_path):
        print(f"❌ Source image not found at: {source_image_path}")
        return False

    # Create different sizes for the .icns file
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    
    # Create the icon directory
    iconset_dir = "Posters.iconset"
    if os.path.exists(iconset_dir):
        shutil.rmtree(iconset_dir)
    os.makedirs(iconset_dir)
    
    try:
        img = Image.open(source_image_path)
    except Exception as e:
        print(f"❌ Error opening image: {e}")
        return False

    for size in sizes:
        # Resize image
        resized_img = img.resize((size, size), Image.Resampling.LANCZOS)
        
        # Save with appropriate naming for .icns
        filename = f"icon_{size}x{size}.png"
        resized_img.save(os.path.join(iconset_dir, filename))
        
        # Also create @2x versions for retina displays
        if size >= 32:
            retina_filename = f"icon_{size//2}x{size//2}@2x.png"
            resized_img.save(os.path.join(iconset_dir, retina_filename))

        print(f"Created {filename} and its @2x version")

    # Convert to .icns using iconutil (macOS only)
    try:
        subprocess.run(['iconutil', '-c', 'icns', iconset_dir], check=True)
        print("✅ Created Posters.icns")
        
        # Clean up iconset directory
        shutil.rmtree(iconset_dir)
        
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ Could not create .icns file (iconutil not available or failed)")
        print("📁 Icon files saved in:", iconset_dir)
        return False

if __name__ == "__main__":
    source_image = "icon_big.png"
    create_icon_from_image(source_image)
