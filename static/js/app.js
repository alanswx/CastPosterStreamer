class ChromecastSlideshowController {
    constructor() {
        this.socket = io({
            transports: ['websocket'],
            upgrade: false
        });
        this.currentPath = '';
        this.selectedDirectory = '';
        this.devices = [];
        this.images = [];
        this.isConnected = false;
        this.playlistItems = [];
        this.draggedElement = null;
        this._suppressDirty = false;

        this.initializeElements();
        this.setupEventListeners();
        this.setupSocketEventListeners();
        this.loadInitialData();
    }

    initializeElements() {
        // Directory elements
        this.currentPathEl = document.getElementById('current-path');
        this.directoryListEl = document.getElementById('directory-list');
        this.directoryThumbnailsEl = document.getElementById('directory-thumbnails');
        this.addDirectoryToPlaylistBtn = document.getElementById('add-directory-to-playlist');

        // Selected directory tracking (for backend compatibility)
        this.selectedDirectory = '';

        // Device elements
        this.discoverDevicesBtn = document.getElementById('discover-devices');
        this.deviceCountEl = document.getElementById('device-count');
        this.deviceListEl = document.getElementById('device-list');

        // Control elements
        this.slideshowIntervalEl = document.getElementById('slideshow-interval');
        this.rotationEnabledEl = document.getElementById('rotation-enabled');
        this.startSingleDirectoryBtn = document.getElementById('start-single-directory');
        this.startPlaylistBtn = document.getElementById('start-playlist');
        this.pauseSlideshowBtn = document.getElementById('pause-slideshow');
        this.skipSlideshowBtn = document.getElementById('skip-slideshow');
        this.stopSlideshowBtn = document.getElementById('stop-slideshow');

        // Status elements
        this.connectionStatusEl = document.getElementById('connection-status');
        this.slideshowRunningEl = document.getElementById('slideshow-running');
        this.slideshowModeEl = document.getElementById('slideshow-mode');
        this.slideshowProgressEl = document.getElementById('slideshow-progress');

        // Log elements
        this.logContainerEl = document.getElementById('log-container');
        this.clearLogBtn = document.getElementById('clear-log');
        this.autoScrollEl = document.getElementById('auto-scroll');

        // Playlist elements
        this.playlistListEl = document.getElementById('playlist-list');
        this.playlistNameEl = document.getElementById('playlist-name');
        this.playlistDirtyEl = document.getElementById('playlist-dirty');
        this.createPlaylistBtn = document.getElementById('create-playlist');
        this.savePlaylistBtn = document.getElementById('save-playlist');
        this.loadPlaylistBtn = document.getElementById('load-playlist');
        this.loadPlaylistDropdown = document.getElementById('load-playlist-dropdown');
        this.savePlaylistModal = document.getElementById('save-playlist-modal');
        this.savePlaylistNameInput = document.getElementById('save-playlist-name-input');
        this.savePlaylistConfirmBtn = document.getElementById('save-playlist-confirm');
        this.savePlaylistCancelBtn = document.getElementById('save-playlist-cancel');

        // Saved playlist state
        this.currentSavedPlaylistId = null;
        this.currentSavedPlaylistName = 'New Playlist';
        this.isDirty = false;
    }

    setupEventListeners() {
        // Directory browser events
        this.addDirectoryToPlaylistBtn.addEventListener('click', () => this.addCurrentDirectoryToPlaylist());


        // Device events
        this.discoverDevicesBtn.addEventListener('click', () => this.discoverDevices());

        // Control events
        this.slideshowIntervalEl.addEventListener('change', () => this.saveSettings());
        this.rotationEnabledEl.addEventListener('change', () => this.saveSettings());
        this.startSingleDirectoryBtn.addEventListener('click', () => this.startSingleDirectorySlideshow());
        this.startPlaylistBtn.addEventListener('click', () => this.startPlaylistSlideshow());
        this.pauseSlideshowBtn.addEventListener('click', () => this.pauseSlideshow());
        this.skipSlideshowBtn.addEventListener('click', () => this.skipSlideshow());
        this.stopSlideshowBtn.addEventListener('click', () => this.stopSlideshow());

        // Log events
        this.clearLogBtn.addEventListener('click', () => this.clearLog());
        document.getElementById('test-websocket').addEventListener('click', () => this.testWebSocket());

        // Playlist events
        this.createPlaylistBtn.addEventListener('click', () => this.createPlaylist());
        this.savePlaylistBtn.addEventListener('click', () => this.savePlaylist());
        this.loadPlaylistBtn.addEventListener('click', (e) => { e.stopPropagation(); this.toggleLoadDropdown(); });
        this.savePlaylistConfirmBtn.addEventListener('click', () => this.confirmSavePlaylist());
        this.savePlaylistCancelBtn.addEventListener('click', () => this.hideSaveModal());
        this.savePlaylistNameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.confirmSavePlaylist(); if (e.key === 'Escape') this.hideSaveModal(); });
        // Close dropdown when clicking outside
        document.addEventListener('click', () => this.hideLoadDropdown());

        // Reconnect when tab becomes visible again (browser throttles WebSocket heartbeat in background)
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !this.isConnected) {
                this.socket.connect();
            }
        });
    }

    setupSocketEventListeners() {
        this.socket.on('connect', async () => {
            this.isConnected = true;
            this.updateConnectionStatus();
            this.logMessage('Connected to server', 'success');

            // Ensure playlist is loaded before requesting status
            await this.loadPlaylist();
            // Sync with current playlist status on connect
            this.loadPlaylistStatus();
        });

        this.socket.on('disconnect', () => {
            this.isConnected = false;
            this.updateConnectionStatus();
            this.logMessage('Disconnected from server', 'error');
        });

        this.socket.on('discovery_started', () => {
            this.discoverDevicesBtn.disabled = true;
            this.discoverDevicesBtn.textContent = 'Discovering...';
            this.logMessage('Device discovery started...', 'info');
        });

        this.socket.on('devices_discovered', (devices) => {
            this.logMessage(`Discovered ${devices.length} devices`, 'success');
            // Reload devices from API to get proper enabled/online status
            this.loadDevices();
        });

        this.socket.on('discovery_finished', () => {
            this.discoverDevicesBtn.disabled = false;
            this.discoverDevicesBtn.textContent = 'Discover Devices';
            this.logMessage('Device discovery completed', 'info');
        });

        this.socket.on('slideshow_update', (data) => {
            this.updateCurrentImages(data.current_images);
            const rotationStatus = data.rotation_enabled ? 'with rotation' : 'without rotation';
            this.logMessage(`Images updated: ${data.successful_devices}/${data.total_devices} devices ${rotationStatus}`, 'info');
        });

        this.socket.on('error', (data) => {
            this.logMessage(data.message, 'error');
        });

        this.socket.on('settings_updated', () => {
            this.logMessage('Settings saved', 'success');
        });

        this.socket.on('device_updated', (data) => {
            this.updateDeviceInList(data.uuid, data.enabled);
        });

        this.socket.on('playlist_updated', () => {
            if (!this._suppressDirty) this.markDirty();
            this.loadPlaylist();
            // Scroll to the last item after a short delay to ensure it's rendered
            setTimeout(() => {
                const playlistItems = this.playlistListEl.querySelectorAll('.playlist-item');
                if (playlistItems.length > 0) {
                    const lastItem = playlistItems[playlistItems.length - 1];
                    lastItem.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }, 100);
        });

        this.socket.on('playlist_paused', () => {
            this.logMessage('Playlist paused/resumed', 'info');
            // Status will be updated via playlist_status_update WebSocket message
        });

        this.socket.on('playlist_skipped', () => {
            this.logMessage('Skipped to next playlist item', 'info');
            // Status will be updated via playlist_status_update WebSocket message
        });

        this.socket.on('playlist_status_update', (status) => {
            // Ensure status is an object if received as string
            if (typeof status === 'string') {
                try {
                    status = JSON.parse(status);
                } catch (e) {
                    console.error('Failed to parse status string', e);
                }
            }

            try {
                this.updateSlideshowControls(status.running, 'playlist');
                this.updatePlaylistProgress(status);
                this.highlightCurrentPlaylistItem(status);
            } catch (error) {
                console.error('Error processing playlist_status_update:', error);
            }
        });

        // Test WebSocket handlers for debugging
        this.socket.on('test_response', (data) => {
            console.log('✅ WebSocket TEST: Direct emission received:', data);
            this.logMessage(`WebSocket test successful: ${data.message}`, 'success');
        });

        this.socket.on('test_background_response', (data) => {
            console.log('✅ WebSocket TEST: Background emission received:', data);
            this.logMessage(`Background WebSocket test successful: ${data.message}`, 'success');
        });
    }

    async loadInitialData() {
        await this.loadSettings();
        // Don't auto-browse on load — user navigates explicitly via shortcuts
        this.directoryListEl.innerHTML = '';
        await this.loadDevices();
        await this.loadPlaylist();
        // Removed loadSlideshowStatus() - using playlist system exclusively
        // Removed loadPlaylistStatus() - rely on WebSocket updates for real-time status
        this.discoverDevices();
    }

    async loadSettings() {
        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();

            if (settings.slideshow_interval && this.slideshowIntervalEl) {
                this.slideshowIntervalEl.value = settings.slideshow_interval;
            }

            if (settings.rotation_enabled !== undefined && this.rotationEnabledEl) {
                this.rotationEnabledEl.checked = settings.rotation_enabled === 'true';
            }

            if (settings.selected_directory) {
                this.selectedDirectory = settings.selected_directory;
                // selectedDirectoryEl removed from UI - no longer needed
            }
        } catch (error) {
            this.logMessage(`Error loading settings: ${error.message}`, 'error');
        }
    }

    async browseDirectory(path = null) {
        try {
            const url = path ? `/api/directories?path=${encodeURIComponent(path)}` : '/api/directories';
            const response = await fetch(url);
            const data = await response.json();

            if (data.error) {
                const isPermission = data.error.includes('Operation not permitted') || data.error.includes('Permission denied');
                if (isPermission) {
                    this.logMessage(`Permission denied: "${path}". Grant Full Disk Access to this app in System Settings → Privacy & Security → Full Disk Access.`, 'error');
                } else {
                    this.logMessage(`Error browsing directory: ${data.error}`, 'error');
                }
                return;
            }

            this.currentPath = data.current_path;
            this.currentPathEl.textContent = data.current_path;
            this.updateDirectoryList(data.items || []);

            this.addDirectoryToPlaylistBtn.disabled = false;

            // Load directory thumbnails
            this.loadDirectoryThumbnails(data.current_path);
        } catch (error) {
            this.logMessage(`Error browsing directory: ${error.message}`, 'error');
        }
    }

    updateDirectoryList(items) {
        this.directoryListEl.innerHTML = '';

        items.forEach(item => {
            const div = document.createElement('div');
            div.className = `directory-item ${item.name === '..' ? 'parent' : ''}`;
            div.textContent = item.name;
            div.addEventListener('click', () => this.browseDirectory(item.path));
            this.directoryListEl.appendChild(div);
        });
    }

    async loadDirectoryThumbnails(directoryPath) {
        try {
            // Get images from this directory
            const response = await fetch(`/api/directory-images?path=${encodeURIComponent(directoryPath)}`);

            if (!response.ok) {
                this.directoryThumbnailsEl.innerHTML = '<div class="no-preview">No images found in this directory</div>';
                return;
            }

            const data = await response.json();

            if (!data.images || data.images.length === 0) {
                this.directoryThumbnailsEl.innerHTML = '<div class="no-preview">No images found in this directory</div>';
                return;
            }

            // Show first few images as thumbnails
            this.directoryThumbnailsEl.innerHTML = '';
            const maxThumbnails = Math.min(8, data.images.length);

            for (let i = 0; i < maxThumbnails; i++) {
                const imageData = data.images[i];
                const img = document.createElement('img');
                img.src = `/api/thumbnails/${imageData.name}?dir=${encodeURIComponent(directoryPath)}`;
                img.alt = imageData.name;
                img.onerror = () => {
                    img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="60" height="60"%3E%3Crect width="60" height="60" fill="%23f0f0f0"/%3E%3Ctext x="50%" y="50%" text-anchor="middle" dy=".3em" fill="%23999"%3E📷%3C/text%3E%3C/svg%3E';
                };
                this.directoryThumbnailsEl.appendChild(img);
            }

            if (data.images.length > maxThumbnails) {
                const moreDiv = document.createElement('div');
                moreDiv.style.cssText = 'display: flex; align-items: center; justify-content: center; background: #e9e9e9; color: #666; font-size: 0.8rem; border-radius: 3px;';
                moreDiv.textContent = `+${data.images.length - maxThumbnails}`;
                this.directoryThumbnailsEl.appendChild(moreDiv);
            }

        } catch (error) {
            this.directoryThumbnailsEl.innerHTML = '<div class="no-preview">Could not load preview</div>';
        }
    }

    async addCurrentDirectoryToPlaylist() {
        if (!this.currentPath) {
            this.logMessage('No directory selected', 'warning');
            return;
        }

        try {
            // First save this as the selected directory
            await this.saveSettings({ selected_directory: this.currentPath });
            this.selectedDirectory = this.currentPath;

            // Then add to playlist
            const response = await fetch('/api/playlist/items', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (response.ok) {
                const dirName = this.currentPath.split('/').pop() || this.currentPath;
                this.logMessage(`Added "${dirName}" to playlist`, 'success');
            } else {
                const error = await response.json();
                this.logMessage(`Error adding to playlist: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error adding directory to playlist: ${error.message}`, 'error');
        }
    }


    async loadDevices() {
        try {
            const response = await fetch('/api/devices');
            this.devices = await response.json();
            this.updateDeviceList();
        } catch (error) {
            this.logMessage(`Error loading devices: ${error.message}`, 'error');
        }
    }

    updateDeviceList() {
        this.deviceCountEl.textContent = this.devices.length;

        if (this.devices.length === 0) {
            this.deviceListEl.innerHTML = '<div class="no-devices">No devices found. Click "Discover Devices" to search.</div>';
            return;
        }

        this.deviceListEl.innerHTML = '';

        this.devices.forEach(device => {
            const div = document.createElement('div');
            const statusClass = device.online ? 'online' : 'offline';
            const enabledClass = device.enabled ? 'enabled' : 'disabled';
            div.className = `device-item ${enabledClass} ${statusClass}`;
            div.setAttribute('data-uuid', device.uuid);

            const statusIndicator = device.online ? '🟢' : '🔴';
            const statusText = device.online ? 'Online' : 'Offline';

            div.innerHTML = `
                <div class="device-info">
                    <h4>${device.name} ${statusIndicator}</h4>
                    <div class="device-details">
                        ${device.host}:${device.port} • ${device.model || 'Chromecast'} • ${statusText}
                        ${device.last_seen ? `<br><small>Last seen: ${device.last_seen}</small>` : ''}
                    </div>
                </div>
                <div class="device-toggle">
                    <input type="checkbox" ${device.enabled ? 'checked' : ''} 
                           onchange="controller.toggleDevice('${device.uuid}', this.checked)"
                           ${!device.online ? 'title="Device is offline"' : ''}>
                    <label>Enable</label>
                </div>
            `;

            this.deviceListEl.appendChild(div);
        });

        this.updateStartButtonState();
        this.updatePlaylistButtonStates();
    }

    async toggleDevice(uuid, enabled) {
        try {
            const response = await fetch(`/api/devices/${uuid}/toggle`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled })
            });

            if (response.ok) {
                const device = this.devices.find(d => d.uuid === uuid);
                if (device) {
                    device.enabled = enabled;
                    this.updateDeviceInList(uuid, enabled);
                    this.updateStartButtonState();
                }
            }
        } catch (error) {
            this.logMessage(`Error toggling device: ${error.message}`, 'error');
        }
    }

    updateDeviceInList(uuid, enabled) {
        const deviceEl = this.deviceListEl.querySelector(`[data-uuid="${uuid}"]`);
        if (deviceEl) {
            deviceEl.className = `device-item ${enabled ? 'enabled' : 'disabled'}`;
            const checkbox = deviceEl.querySelector('input[type="checkbox"]');
            checkbox.checked = enabled;
        }
    }

    async discoverDevices() {
        // Button state will be managed by WebSocket events
        this.socket.emit('discover_devices');
    }

    testWebSocket() {
        console.log('🧪 Testing WebSocket communication...');
        this.logMessage('Testing WebSocket communication...', 'info');
        this.socket.emit('test_websocket');
    }

    async startSingleDirectorySlideshow() {
        if (!this.canStartSlideshow()) {
            return;
        }

        try {
            const response = await fetch('/api/slideshow/start', { method: 'POST' });
            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to start slideshow');
            }

            this.slideshowModeEl.textContent = 'Single Directory';
        } catch (error) {
            this.logMessage(`Error starting slideshow: ${error.message}`, 'error');
        }
    }

    async startPlaylistSlideshow() {
        try {
            await this.saveSettings();
            const response = await fetch('/api/playlist/start', { method: 'POST' });
            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to start playlist');
            }

            // Removed manual mode setting - rely on WebSocket update
            // this.slideshowModeEl.textContent = 'Playlist Mode';
            this.logMessage('Playlist started', 'success');
        } catch (error) {
            this.logMessage(`Error starting playlist: ${error.message}`, 'error');
        }
    }

    async pauseSlideshow() {
        // Check which mode is running and pause accordingly
        try {
            // Try playlist pause first
            const playlistResponse = await fetch('/api/playlist/status');
            const playlistStatus = await playlistResponse.json();

            if (playlistStatus.running) {
                const response = await fetch('/api/playlist/pause', { method: 'POST' });
                if (!response.ok) {
                    const error = await response.json();
                    this.logMessage(`Error pausing playlist: ${error.error}`, 'error');
                }
                return;
            }

            // If no playlist running, this might be single directory mode
            this.logMessage('Pause only available in playlist mode', 'info');
        } catch (error) {
            this.logMessage(`Error pausing slideshow: ${error.message}`, 'error');
        }
    }

    async skipSlideshow() {
        try {
            // Check which mode is running and skip accordingly
            const playlistResponse = await fetch('/api/playlist/status');
            const playlistStatus = await playlistResponse.json();

            if (playlistStatus.running) {
                const response = await fetch('/api/playlist/skip', { method: 'POST' });
                if (!response.ok) {
                    const error = await response.json();
                    this.logMessage(`Error skipping playlist item: ${error.error}`, 'error');
                } else {
                    // Status will be updated by the websocket
                    this.logMessage('Skipping to next playlist item...', 'info');
                }
                return;
            }

            // Single directory skip
            const response = await fetch('/api/slideshow/skip', { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                this.logMessage(`Error skipping: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error skipping: ${error.message}`, 'error');
        }
    }

    async stopSlideshow() {
        try {
            // Force stop both playlist and regular slideshow regardless of frontend state
            const playlistResponse = await fetch('/api/playlist/stop', { method: 'POST' });
            const slideshowResponse = await fetch('/api/slideshow/stop', { method: 'POST' });

            // Check results
            if (playlistResponse.ok) {
                this.logMessage('Playlist stopped', 'success');
            }

            if (slideshowResponse.ok) {
                this.logMessage('Slideshow stopped', 'success');
            }

            this.slideshowModeEl.textContent = 'None';
            this.slideshowProgressEl.textContent = '';

            // Force enable the start button regardless of state
            this.startPlaylistBtn.disabled = false;
            this.stopSlideshowBtn.disabled = true;
        } catch (error) {
            this.logMessage(`Error stopping slideshow: ${error.message}`, 'error');
        }
    }

    async skipToNext() {
        this.logMessage('Skipping to next images...', 'info');
        // This would trigger the slideshow controller to immediately advance
    }

    async loadSlideshowStatus() {
        try {
            const response = await fetch('/api/slideshow/status');
            const status = await response.json();

            this.updateSlideshowControls(status.running);
            if (status.current_images) {
                this.updateCurrentImages(Object.values(status.current_images));
            }
        } catch (error) {
            this.logMessage(`Error loading slideshow status: ${error.message}`, 'error');
        }
    }

    updateSlideshowControls(isRunning, mode = 'playlist') {
        console.log('🎮 updateSlideshowControls called:', { isRunning, mode });
        console.log('🎮 Button elements exist:', {
            startPlaylist: !!this.startPlaylistBtn,
            stop: !!this.stopSlideshowBtn,
            statusEl: !!this.slideshowRunningEl
        });

        console.log('🎮 Updating button states - isRunning:', isRunning, 'playlistItems.length:', this.playlistItems.length);
        this.startSingleDirectoryBtn.disabled = isRunning || !this.canStartSlideshow();
        this.startPlaylistBtn.disabled = isRunning; // Force-enable for testing
        this.pauseSlideshowBtn.disabled = !isRunning;
        this.skipSlideshowBtn.disabled = !isRunning;
        this.stopSlideshowBtn.disabled = !isRunning;
        console.log('🎮 Button states after update:', {
            startPlaylist: this.startPlaylistBtn.disabled,
            stop: this.stopSlideshowBtn.disabled,
            skip: this.skipSlideshowBtn.disabled,
            pause: this.pauseSlideshowBtn.disabled
        });

        console.log('🎮 Setting status text to:', isRunning ? 'Running' : 'Stopped');
        console.log('🎮 slideshowRunningEl exists:', !!this.slideshowRunningEl);
        if (this.slideshowRunningEl) {
            this.slideshowRunningEl.textContent = isRunning ? 'Running' : 'Stopped';
            this.slideshowRunningEl.className = isRunning ? 'status-running' : 'status-stopped';
            this.slideshowRunningEl.style.color = isRunning ? '#27ae60' : '#e74c3c';
        } else {
            console.log('🎮 ERROR: slideshowRunningEl is null!');
        }

        if (!isRunning) {
            this.slideshowModeEl.textContent = 'None';
            this.slideshowProgressEl.textContent = '';
        } else {
            this.slideshowModeEl.textContent = 'Playlist Mode';
        }
        console.log('🎮 updateSlideshowControls completed');
    }

    updateCurrentImages(images) {
        // Skip if element doesn't exist (removed from UI)
        if (!this.currentImagesEl) {
            return;
        }

        if (!images || images.length === 0) {
            this.currentImagesEl.innerHTML = '<div class="no-images">No images currently displayed</div>';
            return;
        }

        this.currentImagesEl.innerHTML = '';
        images.forEach(imageName => {
            const div = document.createElement('div');
            div.className = 'current-image-item';
            div.textContent = imageName;
            this.currentImagesEl.appendChild(div);
        });
    }

    canStartSlideshow() {
        const hasEnabledDevices = this.devices.some(d => d.enabled);
        const hasImages = this.images.length > 0;
        const hasSelectedDirectory = this.selectedDirectory !== '';

        return hasEnabledDevices && hasImages && hasSelectedDirectory;
    }

    updateStartButtonState() {
        this.startSingleDirectoryBtn.disabled = !this.canStartSlideshow();
    }

    async saveSettings(additionalSettings = {}) {
        const settings = {
            slideshow_interval: this.slideshowIntervalEl.value,
            rotation_enabled: this.rotationEnabledEl.checked ? 'true' : 'false',
            ...additionalSettings
        };

        try {
            const response = await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });

            if (!response.ok) {
                throw new Error('Failed to save settings');
            }
        } catch (error) {
            this.logMessage(`Error saving settings: ${error.message}`, 'error');
        }
    }

    updateConnectionStatus() {
        this.connectionStatusEl.textContent = this.isConnected ? 'Connected' : 'Disconnected';
        this.connectionStatusEl.className = this.isConnected ? 'status-connected' : 'status-disconnected';
    }

    logMessage(message, type = 'info') {
        const div = document.createElement('div');
        div.className = `log-entry ${type}`;

        const timestamp = new Date().toLocaleTimeString();
        div.innerHTML = `
            <span class="timestamp">[${timestamp}]</span>
            <span class="message">${message}</span>
        `;

        this.logContainerEl.appendChild(div);

        // Auto-scroll to bottom if enabled
        if (this.autoScrollEl.checked) {
            this.logContainerEl.scrollTop = this.logContainerEl.scrollHeight;
        }

        // Limit log entries to prevent memory issues
        const entries = this.logContainerEl.querySelectorAll('.log-entry');
        if (entries.length > 1000) {
            entries[0].remove();
        }
    }

    clearLog() {
        this.logContainerEl.innerHTML = '';
        this.logMessage('Log cleared', 'info');
    }

    // Playlist Management Methods
    async loadPlaylist() {
        try {
            const response = await fetch('/api/playlist');
            const data = await response.json();

            this.playlistItems = data.items || [];
            this.updatePlaylistDisplay();
        } catch (error) {
            this.logMessage(`Error loading playlist: ${error.message}`, 'error');
        }
    }

    updatePlaylistDisplay() {
        if (this.playlistItems.length === 0) {
            this.playlistListEl.innerHTML = '<div class="no-playlist-items">No items in playlist. Browse to a directory above and click "Add This Directory to Playlist".</div>';
        } else {
            this.playlistListEl.innerHTML = '';
            this.playlistItems.forEach((item, index) => this.createPlaylistItemElement(item, index));
        }

        this.updatePlaylistButtonStates();
    }

    createPlaylistItemElement(item, index) {
        const div = document.createElement('div');
        div.className = `playlist-item ${item.is_valid ? '' : 'invalid'}`;
        div.draggable = true;
        div.dataset.itemId = item.id;

        const durationOptions = [1, 2, 5, 10, 15, 20, 30, 45, 60]
            .map(minutes => `<option value="${minutes}" ${item.duration_minutes === minutes ? 'selected' : ''}>${minutes} min</option>`)
            .join('');

        div.innerHTML = `
            <span class="playlist-item-number">[${index + 1}]</span>
            <span class="playlist-item-drag-handle">☰</span>
            <div class="playlist-item-thumbnail">
                <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='%23f0f0f0'/%3E%3Ctext x='50%' y='50%' text-anchor='middle' dy='.3em' fill='%23999'%3E📷%3C/text%3E%3C/svg%3E" alt="Loading..." class="thumbnail-img">
            </div>
            <div class="playlist-item-info">
                <div class="playlist-item-name">${item.directory_name}</div>
                <div class="playlist-item-path">${item.directory_path}</div>
            </div>
            <div class="playlist-item-duration">
                <select onchange="controller.updatePlaylistItemDuration(${item.id}, this.value)">
                    ${durationOptions}
                </select>
            </div>
            <div class="playlist-item-actions">
                <button class="playlist-item-remove" onclick="controller.removePlaylistItem(${item.id})" title="Remove">
                    🗑️
                </button>
            </div>
        `;

        // Add drag and drop event listeners
        div.addEventListener('dragstart', (e) => this.handleDragStart(e));
        div.addEventListener('dragover', (e) => this.handleDragOver(e));
        div.addEventListener('drop', (e) => this.handleDrop(e));
        div.addEventListener('dragend', (e) => this.handleDragEnd(e));

        this.playlistListEl.appendChild(div);

        // Load thumbnail for this directory
        this.loadPlaylistItemThumbnail(item.directory_path, div);
    }

    async loadPlaylistItemThumbnail(directoryPath, itemElement) {
        try {
            const response = await fetch(`/api/directory-images?path=${encodeURIComponent(directoryPath)}`);

            if (!response.ok) {
                return; // Keep default placeholder
            }

            const data = await response.json();

            if (data.images && data.images.length > 0) {
                // Get the first image as thumbnail
                const firstImage = data.images[0];
                const thumbnailImg = itemElement.querySelector('.thumbnail-img');

                if (thumbnailImg) {
                    thumbnailImg.src = `/api/thumbnails/${firstImage.name}?dir=${encodeURIComponent(directoryPath)}`;
                    thumbnailImg.onerror = () => {
                        // Keep the default placeholder if thumbnail fails to load
                        thumbnailImg.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='%23f0f0f0'/%3E%3Ctext x='50%' y='50%' text-anchor='middle' dy='.3em' fill='%23999'%3E📷%3C/text%3E%3C/svg%3E";
                    };
                }
            }
        } catch (error) {
            // Silently fail - keep placeholder thumbnail
            console.log(`Could not load thumbnail for ${directoryPath}: ${error.message}`);
        }
    }

    async removePlaylistItem(itemId) {
        try {
            const response = await fetch(`/api/playlist/items/${itemId}`, { method: 'DELETE' });
            if (response.ok) {
                this.logMessage('Item removed from playlist', 'success');
            } else {
                const error = await response.json();
                this.logMessage(`Error removing item: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error removing playlist item: ${error.message}`, 'error');
        }
    }

    async updatePlaylistItemDuration(itemId, duration) {
        try {
            const response = await fetch(`/api/playlist/items/${itemId}/duration`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ duration_minutes: parseInt(duration) })
            });

            if (response.ok) {
                this.logMessage('Duration updated', 'success');
                await this.loadPlaylist(); // Refresh to update total duration
            } else {
                const error = await response.json();
                this.logMessage(`Error updating duration: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error updating duration: ${error.message}`, 'error');
        }
    }

    // --- Saved Playlist State ---
    markDirty() {
        this.isDirty = true;
        this.playlistDirtyEl.style.display = '';
    }

    markClean(name, id) {
        this.isDirty = false;
        this.currentSavedPlaylistId = id;
        this.currentSavedPlaylistName = name;
        this.playlistNameEl.textContent = name;
        this.playlistDirtyEl.style.display = 'none';
    }

    // --- Create (New) ---
    async createPlaylist() {
        this._suppressDirty = true;
        try {
            const response = await fetch('/api/playlist/clear', { method: 'DELETE' });
            if (response.ok) {
                this.markClean('New Playlist', null);
                this.logMessage('New playlist created', 'success');
                setTimeout(() => { this._suppressDirty = false; }, 300);
            } else {
                this._suppressDirty = false;
            }
        } catch (error) {
            this.logMessage(`Error creating playlist: ${error.message}`, 'error');
            this._suppressDirty = false;
        }
    }

    // --- Save ---
    async savePlaylist() {
        if (this.playlistItems.length === 0) return;
        const prefill = this.currentSavedPlaylistName === 'New Playlist' ? '' : this.currentSavedPlaylistName;
        this.showSaveModal(prefill);
    }

    showSaveModal(prefill = '') {
        this.savePlaylistNameInput.value = prefill;
        this.savePlaylistModal.style.display = 'flex';
        this.savePlaylistNameInput.focus();
        this.savePlaylistNameInput.select();
    }

    hideSaveModal() {
        this.savePlaylistModal.style.display = 'none';
    }

    async confirmSavePlaylist() {
        const name = this.savePlaylistNameInput.value.trim();
        if (!name) { this.savePlaylistNameInput.focus(); return; }
        this.hideSaveModal();
        await this._doSave(name, this.currentSavedPlaylistId);
    }

    async _doSave(name, id) {
        try {
            let response;
            if (id !== null) {
                response = await fetch(`/api/saved-playlists/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
            } else {
                response = await fetch('/api/saved-playlists', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
            }
            if (response.ok) {
                const data = await response.json();
                this.markClean(data.name, data.id);
                this.logMessage(`Playlist saved: "${data.name}"`, 'success');
            } else {
                const err = await response.json();
                this.logMessage(`Error saving playlist: ${err.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error saving playlist: ${error.message}`, 'error');
        }
    }

    // --- Load Dropdown ---
    async toggleLoadDropdown() {
        if (this.loadPlaylistDropdown.style.display !== 'none') {
            this.hideLoadDropdown();
            return;
        }
        await this.showLoadDropdown();
    }

    hideLoadDropdown() {
        this.loadPlaylistDropdown.style.display = 'none';
    }

    async showLoadDropdown() {
        try {
            const response = await fetch('/api/saved-playlists');
            const playlists = await response.json();
            this.loadPlaylistDropdown.innerHTML = '';
            if (playlists.length === 0) {
                this.loadPlaylistDropdown.innerHTML = '<div class="playlist-dropdown-empty">No saved playlists</div>';
            } else {
                playlists.forEach(pl => {
                    const item = document.createElement('div');
                    item.className = 'playlist-dropdown-item';
                    item.innerHTML = `
                        <span class="playlist-dropdown-item-name" title="${pl.name}">${pl.name}</span>
                        <button class="playlist-dropdown-item-delete" title="Delete">✕</button>
                    `;
                    item.querySelector('.playlist-dropdown-item-name').addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.loadSavedPlaylist(pl.id, pl.name);
                    });
                    item.querySelector('.playlist-dropdown-item-delete').addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.deleteSavedPlaylist(pl.id, pl.name, item);
                    });
                    this.loadPlaylistDropdown.appendChild(item);
                });
            }
            this.loadPlaylistDropdown.style.display = 'block';
        } catch (error) {
            this.logMessage(`Error loading playlists: ${error.message}`, 'error');
        }
    }

    async loadSavedPlaylist(id, name) {
        this.hideLoadDropdown();
        // Stop any running slideshow before swapping the playlist
        try { await fetch('/api/playlist/stop', { method: 'POST' }); } catch (_) {}
        this._suppressDirty = true;
        try {
            const response = await fetch(`/api/saved-playlists/${id}/load`, { method: 'POST' });
            if (response.ok) {
                const data = await response.json();
                this.markClean(data.name, data.id);
                this.logMessage(`Loaded playlist: "${data.name}"`, 'success');
                setTimeout(() => { this._suppressDirty = false; }, 300);
            } else {
                const err = await response.json();
                this.logMessage(`Error loading playlist: ${err.error}`, 'error');
                this._suppressDirty = false;
            }
        } catch (error) {
            this.logMessage(`Error loading playlist: ${error.message}`, 'error');
            this._suppressDirty = false;
        }
    }

    async deleteSavedPlaylist(id, name, itemEl) {
        try {
            const response = await fetch(`/api/saved-playlists/${id}`, { method: 'DELETE' });
            if (response.ok) {
                itemEl.remove();
                if (this.loadPlaylistDropdown.children.length === 0) {
                    this.loadPlaylistDropdown.innerHTML = '<div class="playlist-dropdown-empty">No saved playlists</div>';
                }
                // If we deleted the currently loaded one, reset name
                if (this.currentSavedPlaylistId === id) {
                    this.markDirty();
                    this.currentSavedPlaylistId = null;
                }
                this.logMessage(`Deleted playlist: "${name}"`, 'success');
            }
        } catch (error) {
            this.logMessage(`Error deleting playlist: ${error.message}`, 'error');
        }
    }

    async startPlaylist() {
        try {
            const response = await fetch('/api/playlist/start', { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                this.logMessage(`Error starting playlist: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error starting playlist: ${error.message}`, 'error');
        }
    }

    async pausePlaylist() {
        try {
            const response = await fetch('/api/playlist/pause', { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                this.logMessage(`Error pausing playlist: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error pausing playlist: ${error.message}`, 'error');
        }
    }

    async skipPlaylist() {
        try {
            const response = await fetch('/api/playlist/skip', { method: 'POST' });
            if (!response.ok) {
                const error = await response.json();
                this.logMessage(`Error skipping playlist item: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error skipping playlist item: ${error.message}`, 'error');
        }
    }

    async loadPlaylistStatus() {
        try {
            const response = await fetch('/api/playlist/status');
            const status = await response.json();

            console.log('📡 SYNC: loadPlaylistStatus got:', { running: status.running, current_item_id: status.current_item?.id, current_item_name: status.current_item?.directory_name });
            console.log('📡 SYNC: playlistItems.length at sync time:', this.playlistItems.length);
            // Use the main UI update functions for consistent behavior
            this.updateSlideshowControls(status.running, 'playlist');
            this.updatePlaylistProgress(status);

            // Delay highlighting to allow playlist component to finish rendering
            setTimeout(() => {
                this.highlightCurrentPlaylistItem(status);
                console.log('📡 SYNC: Delayed highlighting applied');
            }, 500);
        } catch (error) {
            this.logMessage(`Error loading playlist status: ${error.message}`, 'error');
        }
    }

    updatePlaylistControls(isRunning, isPaused) {
        this.startPlaylistBtn.disabled = isRunning;
        this.pauseSlideshowBtn.disabled = !isRunning;
        this.skipSlideshowBtn.disabled = !isRunning;

        if (isPaused) {
            this.pauseSlideshowBtn.textContent = '▶️ Resume';
        } else {
            this.pauseSlideshowBtn.textContent = '⏸️ Pause';
        }
    }

    updatePlaylistProgress(status) {
        if (!status.running) {
            this.slideshowProgressEl.textContent = 'Playlist stopped';
            this.slideshowProgressEl.className = 'progress-display stopped';
            return;
        }

        const className = status.paused ? 'paused' : 'running';
        this.slideshowProgressEl.className = `progress-display ${className}`;

        if (status.current_item) {
            const minutes = Math.floor(status.time_remaining / 60);
            const seconds = status.time_remaining % 60;
            const statusText = status.paused ? 'Paused' : 'Playing';
            this.slideshowProgressEl.textContent =
                `${statusText}: ${status.current_item.directory_name} - ${minutes}:${String(seconds).padStart(2, '0')} remaining`;
        }

        // Update pause button text
        if (status.paused) {
            this.pauseSlideshowBtn.textContent = '▶️ Resume';
        } else {
            this.pauseSlideshowBtn.textContent = '⏸️ Pause';
        }
    }


    updatePlaylistButtonStates() {
        this.startPlaylistBtn.disabled = false; // Force-enable for testing
        this.savePlaylistBtn.disabled = this.playlistItems.length === 0;
    }

    highlightCurrentPlaylistItem(status) {
        // Remove highlighting from all playlist items
        const allItems = this.playlistListEl.querySelectorAll('.playlist-item');
        allItems.forEach(item => {
            item.classList.remove('currently-playing');
        });

        // Add highlighting to the current item if the playlist is running
        if (status.running && status.current_item) {
            const currentItemElement = this.playlistListEl.querySelector(`[data-item-id="${status.current_item.id}"]`);
            if (currentItemElement) {
                currentItemElement.classList.add('currently-playing');
            }
        }
    }

    // Drag and Drop functionality
    handleDragStart(e) {
        this.draggedElement = e.target;
        e.target.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        e.dataTransfer.setData('text/html', e.target.outerHTML);
    }

    handleDragOver(e) {
        if (e.preventDefault) {
            e.preventDefault();
        }

        e.dataTransfer.dropEffect = 'move';

        const target = e.target.closest('.playlist-item');
        if (target && target !== this.draggedElement) {
            target.classList.add('drag-over');
        }

        return false;
    }

    handleDrop(e) {
        if (e.stopPropagation) {
            e.stopPropagation();
        }

        const target = e.target.closest('.playlist-item');
        if (target && target !== this.draggedElement) {
            const draggedId = parseInt(this.draggedElement.dataset.itemId);
            const targetId = parseInt(target.dataset.itemId);

            this.reorderPlaylistItems(draggedId, targetId);
        }

        return false;
    }

    handleDragEnd(e) {
        e.target.classList.remove('dragging');

        // Remove all drag-over classes
        const items = this.playlistListEl.querySelectorAll('.playlist-item');
        items.forEach(item => item.classList.remove('drag-over'));

        this.draggedElement = null;
    }

    async reorderPlaylistItems(draggedId, targetId) {
        // Find positions of dragged and target items
        const draggedIndex = this.playlistItems.findIndex(item => item.id === draggedId);
        const targetIndex = this.playlistItems.findIndex(item => item.id === targetId);

        if (draggedIndex === -1 || targetIndex === -1) return;

        // Create new order array
        const newOrder = [...this.playlistItems];
        const [draggedItem] = newOrder.splice(draggedIndex, 1);
        newOrder.splice(targetIndex, 0, draggedItem);

        const itemIds = newOrder.map(item => item.id);

        try {
            const response = await fetch('/api/playlist/reorder', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_ids: itemIds })
            });

            if (!response.ok) {
                const error = await response.json();
                this.logMessage(`Error reordering playlist: ${error.error}`, 'error');
            }
        } catch (error) {
            this.logMessage(`Error reordering playlist: ${error.message}`, 'error');
        }
    }
}

// Initialize the controller when the page loads
let controller;
document.addEventListener('DOMContentLoaded', () => {
    controller = new ChromecastSlideshowController();
    // Make controller globally accessible for inline event handlers
    window.controller = controller;
});