# Create a fresh virtual environment in a folder named 'venv'
python3 -m venv venv

# Activate it
source venv/bin/activate

# Now, inside the venv, reinstall your packages
pip install --upgrade pip
pip install -r requirements.txt

# Finally, run the build command again
python setup.py py2app 

# Sign the application
codesign --force --verify --verbose --sign "Apple Development: Alan Steremberg (DALPRRY8F7)" "dist/Posters.app"
