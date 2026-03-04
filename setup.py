from setuptools import setup
from glob import glob

APP = ['menu_bar_app.py']

# Correctly define DATA_FILES to preserve directory structure.
# 'static' will be a directory in the app bundle's Resources.
DATA_FILES = [
    'app.py',
    'settings_manager.py',
    'chromecast_manager.py',
    'slideshow_controller.py',
    'image_server.py',
    'requirements.txt',
    'chromecast_subprocess.py',
    ('static', glob('static/**/*', recursive=True)),
    ('templates', glob('templates/**/*.*', recursive=True)),
]

OPTIONS = {
    'argv_emulation': False,
    'alias': False,  # Create standalone bundle instead of alias
    'site_packages': True,
    'strip': False,
    'iconfile': 'Posters.icns',
    'frameworks': [],  # Don't bundle frameworks separately
    'plist': {
        'CFBundleName': 'Posters',
        'CFBundleDisplayName': 'Posters', 
        'CFBundleIdentifier': 'com.posters.app',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSUIElement': True,  # Run as menu bar app without dock icon
        'NSHighResolutionCapable': True,
        'NSPrincipalClass': 'NSApplication',
        'NSAppleEventsUsageDescription': 'This app needs permission to control other applications to manage presentations.',
        'NSRemovableVolumesUsageDescription': 'Posters needs access to external drives to load images from them.',
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'All Files',
                'CFBundleTypeRole': 'Viewer',
                'LSHandlerRank': 'Alternate',
                'LSItemContentTypes': ['public.content']
            }
        ],
        'com.apple.security.cs.allow-jit': True,
        'com.apple.security.cs.allow-unsigned-executable-memory': True,
        'com.apple.security.cs.disable-library-validation': True,
        'com.apple.security.network.client': True,
        'com.apple.security.network.server': True,
    },
    'packages': ['rumps', 'flask', 'flask_socketio', 'flask_cors', 'PIL', 'catt', 'pychromecast', 'socketio', 'engineio', 'zeroconf', 'packaging', 'gevent', 'geventwebsocket'],
    'includes': ['subprocess', 'webbrowser', 'threading', 'pathlib', 'json', 'os', 'sys'],
    'excludes': ['tkinter'],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
