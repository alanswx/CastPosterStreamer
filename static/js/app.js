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

        // Selected directory tracking (for backend compatibility)
        this.selectedDirectory = '';

        // Device elements
        this.discoverDevicesBtn = document.getElementById('discover-devices');
        this.deviceCountEl = document.getElementById('device-count');
        this.deviceListEl = document.getElementById('device-list');

        // Control elements
        this.slideshowIntervalEl = document.getElementById('slideshow-interval');
        this.rotationEnabledEl = document.getElementById('rotation-enabled');
        this.startPlaylistBtn = document.getElementById('start-playlist');   // main Play
        this.pauseSlideshowBtn = document.getElementById('pause-slideshow');
        this.skipSlideshowBtn = document.getElementById('skip-slideshow');
        this.stopSlideshowBtn = document.getElementById('stop-slideshow');

        // Playlist / show picker
        this.playlistLabelEl = document.getElementById('playlist-label');
        this.loadShowBtn = document.getElementById('load-show');
        this.addShowBtn = document.getElementById('add-show');
        this.showPickerEl = document.getElementById('show-picker');
        this.pickerTitleEl = document.getElementById('picker-title');
        this.pickerConfirmBtn = document.getElementById('picker-confirm');
        this.pickerCloseBtn = document.getElementById('picker-close');
        this.pickerMode = null;          // 'load' | 'add' while the picker is open

        // What the Play button will start: the playlist, or a single loaded show.
        this.selection = { type: 'playlist', name: null, path: null };
        this.isVirtualPlaylist = false;
        this._wasVirtual = false;
        this._wasAllShows = false;
        this.showPlaying = false;       // a single show (not a playlist) is casting
        this.allShowsQueue = null;      // All Shows loaded but not yet playing

        // Status elements
        this.connectionStatusEl = document.getElementById('connection-status');

        // Log elements
        this.logContainerEl = document.getElementById('log-container');
        this.clearLogBtn = document.getElementById('clear-log');
        this.autoScrollEl = document.getElementById('auto-scroll');

        // Playlist elements
        this.playlistListEl = document.getElementById('playlist-list');
        this.playlistNameEl = document.getElementById('playlist-name');
        this.playlistDirtyEl = document.getElementById('playlist-dirty');
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

        // Screen schedule
        this.scheduleEnabledEl = document.getElementById('schedule-enabled');
        this.scheduleOnTimeEl = document.getElementById('schedule-on-time');
        this.scheduleOffTimeEl = document.getElementById('schedule-off-time');
        this.scheduleRunOnBtn = document.getElementById('schedule-run-on');
        this.scheduleRunOffBtn = document.getElementById('schedule-run-off');
        this.scheduleCheckBtn = document.getElementById('schedule-check-screens');
        this.scheduleSummaryEl = document.getElementById('schedule-summary');
        this.scheduleLastActionEl = document.getElementById('schedule-last-action');
        this.scheduleScreensEl = document.getElementById('schedule-screens');
    }

    setupEventListeners() {


        // Device events
        this.discoverDevicesBtn.addEventListener('click', () => this.discoverDevices());

        // Control events
        this.slideshowIntervalEl.addEventListener('change', () => this.saveSettings());
        this.rotationEnabledEl.addEventListener('change', () => this.saveSettings());
        this.startPlaylistBtn.addEventListener('click', () => this.playSelected());
        this.pauseSlideshowBtn.addEventListener('click', () => this.pauseSlideshow());
        this.skipSlideshowBtn.addEventListener('click', () => this.skipSlideshow());
        this.stopSlideshowBtn.addEventListener('click', () => this.stopSlideshow());

        // Picker / playlist events
        this.loadShowBtn.addEventListener('click', () => this.openPicker('load'));
        this.addShowBtn.addEventListener('click', () => this.openPicker('add'));
        this.pickerConfirmBtn.addEventListener('click', () => this.confirmPicker());
        this.pickerCloseBtn.addEventListener('click', () => this.closePicker());

        // Log events
        this.clearLogBtn.addEventListener('click', () => this.clearLog());
        document.getElementById('test-websocket').addEventListener('click', () => this.testWebSocket());

        // Playlist events
        this.savePlaylistBtn.addEventListener('click', () => this.savePlaylist());
        this.loadPlaylistBtn.addEventListener('click', (e) => { e.stopPropagation(); this.toggleLoadDropdown(); });
        this.savePlaylistConfirmBtn.addEventListener('click', () => this.confirmSavePlaylist());
        this.savePlaylistCancelBtn.addEventListener('click', () => this.hideSaveModal());
        this.savePlaylistNameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.confirmSavePlaylist(); if (e.key === 'Escape') this.hideSaveModal(); });
        // Close dropdown when clicking outside
        document.addEventListener('click', () => this.hideLoadDropdown());

        // Screen schedule
        this.scheduleEnabledEl.addEventListener('change', () => this.saveSchedule());
        this.scheduleOnTimeEl.addEventListener('change', () => this.saveSchedule());
        this.scheduleOffTimeEl.addEventListener('change', () => this.saveSchedule());
        this.scheduleRunOnBtn.addEventListener('click', () => this.runScheduleNow('on'));
        this.scheduleRunOffBtn.addEventListener('click', () => this.runScheduleNow('off'));
        this.scheduleCheckBtn.addEventListener('click', () => this.checkScreens());

        // Reconnect when tab becomes visible again (browser throttles WebSocket heartbeat in background)
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && !this.isConnected) {
                this.socket.connect();
            }
        });

        this.startStaleConnectionWatchdog();
    }

    /**
     * The socket can go stale while still reporting connected — the server
     * stops receiving from it, so status events never arrive and the page sits
     * frozen behind a green "Connected" badge. Nothing detects that, because
     * socket.io believes it is fine. If something should be playing and we
     * have heard nothing for a while, force a reconnect.
     */
    startStaleConnectionWatchdog() {
        const STALE_MS = 30000;
        this._lastEventAt = Date.now();

        setInterval(async () => {
            if (document.hidden) return;
            if (Date.now() - this._lastEventAt < STALE_MS) return;

            // Only act if the server says something is playing — an idle app
            // legitimately emits nothing.
            try {
                const status = await (await fetch('/api/playlist/status')).json();
                if (!status.running && !this.showPlaying) {
                    this._lastEventAt = Date.now();
                    return;
                }
            } catch (e) {
                return;     // server unreachable; nothing useful to do here
            }

            this.logMessage('Connection went quiet — reconnecting…', 'info');
            this._lastEventAt = Date.now();
            try {
                this.socket.disconnect();
                this.socket.connect();
            } catch (e) {
                console.error('Reconnect failed', e);
            }
        }, 10000);
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

        this.socket.on('schedule_status', (status) => {
            this.renderSchedule(status);
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

        this.socket.onAny(() => { this._lastEventAt = Date.now(); });

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
        // Ask the server whether a single show is casting: showPlaying is
        // otherwise only set by this browser's own actions, so a reload (or a
        // show the scheduler started) would leave the page thinking nothing
        // is playing.
        try {
            const s = await (await fetch('/api/slideshow/status')).json();
            this.showPlaying = !!s.running;
        } catch (e) { /* leave it false */ }
        await this.loadPlaylist();
        this.updateSlideshowControls(false, 'show');
        await this.loadSchedule();
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

            // A loaded All Shows queue survives a reload.
            if (settings.loaded_kind === 'all_shows') {
                try {
                    const all = await (await fetch('/api/playlist/all-shows')).json();
                    if (all.item_count) {
                        this.allShowsQueue = all.items;
                        this.selection = { type: 'all-shows', name: all.name, path: null };
                    }
                } catch (e) { /* fall through to the stored playlist */ }
            }

            // A single loaded show survives a reload.
            if (settings.loaded_kind === 'show' && settings.selected_directory) {
                const p = settings.selected_directory;
                this.selection = {
                    type: 'show',
                    name: p.split('/').filter(Boolean).pop() || p,
                    path: p
                };
            }

            if (settings.current_playlist_name) {
                this.currentSavedPlaylistName = settings.current_playlist_name;
                if (!this.isVirtualPlaylist && this.selection.type !== 'show') {
                    this.playlistNameEl.textContent = `(Playlist) ${settings.current_playlist_name}`;
                }
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

            if (data.requested_path_missing) {
                this.logMessage(
                    `Could not open "${data.requested_path_missing}" (is the external drive connected?) — showing ${data.current_path} instead`,
                    'error'
                );
            }

            this.currentPath = data.current_path;
            this.currentPathEl.textContent = data.current_path;
            this.updateDirectoryList(data.items || []);

            // You can only "use" a folder once you've navigated into one.
            if (this.pickerConfirmBtn) this.pickerConfirmBtn.disabled = !this.currentPath;

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

    /**
     * Play button: start whatever is loaded, replacing whatever is running.
     * Play stays available while something else plays, so loading a show and
     * pressing Play works without stopping the playlist by hand first.
     */
    async playSelected() {
        if (this.selection.type === 'show' && this.selection.path) {
            await this.playShow(this.selection.path);   // stops the playlist first
            return;
        }
        if (this.selection.type === 'all-shows') {
            await this.playAllShows();                  // stops both, then plays
            return;
        }

        // The server stops a running single show for us, so there's no
        // client-side state to get wrong here.
        this.showPlaying = false;
        await this.startPlaylistSlideshow();
    }

    /**
     * Play a single show, overriding the playlist.
     * The backend refuses to start a single-directory slideshow while a
     * playlist is running, so stop it first. The playlist itself stays loaded.
     */
    async playShow(path = null) {
        const showPath = path || this.currentPath;
        if (!showPath) {
            this.logMessage('Browse to a show first', 'error');
            return;
        }
        const name = showPath.split('/').filter(Boolean).pop() || showPath;

        try {
            // One atomic call: stopping and starting separately left a gap
            // wide enough for the scheduler (or another tab) to slip in.
            const response = await fetch('/api/show/play', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: showPath })
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to start show');

            this.selectedDirectory = showPath;

            this.selection = { type: 'show', name, path: showPath };
            this.showPlaying = true;
            this.logMessage(`Playing show: ${name}`, 'success');
            this.updateNowPlaying();
            // isRunning=false: the *playlist* isn't running, so Pause/Skip
            // stay off. showPlaying keeps Stop live.
            this.updateSlideshowControls(false, 'show');
        } catch (error) {
            this.logMessage(`Error playing show: ${error.message}`, 'error');
        }
    }

    /**
     * Load All Shows (every show across all saved playlists) as the queue,
     * without playing it — Play starts it. The stored playlist is untouched.
     */
    async loadAllShows() {
        try {
            const response = await fetch('/api/playlist/all-shows');
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Failed to load All Shows');
            if (!data.item_count) {
                this.logMessage('No shows found in any saved playlist', 'error');
                return;
            }

            this.allShowsQueue = data.items;
            this.selection = { type: 'all-shows', name: data.name, path: null };
            await this.saveSettings({ loaded_kind: 'all_shows' });
            await this.playAllShows();   // loading plays immediately
        } catch (error) {
            this.logMessage(`Error loading All Shows: ${error.message}`, 'error');
        }
    }

    /**
     * Play every show from all saved playlists as a VIRTUAL playlist: it stops
     * what's running and is shown in the playlist box so you can see what's in
     * it, but the stored playlist is never modified and returns on stop.
     */
    async playAllShows() {
        try {
            const response = await fetch('/api/playlist/play-all-shows', { method: 'POST' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Failed to play all shows');

            const skipped = result.skipped
                ? ` (${result.skipped} skipped — folder missing)` : '';
            this.logMessage(
                `Playing All Shows: ${result.count - (result.skipped || 0)} show(s) from saved playlists${skipped}`,
                'success'
            );

            this.selection = { type: 'playlist', name: result.name, path: null };
            await this.loadPlaylist();
        } catch (error) {
            this.logMessage(`Error playing all shows: ${error.message}`, 'error');
        }
    }

    // --- Show picker (collapsible, shared by Load Show and Add Show) ---

    /** Open the picker at the library folder. mode: 'load' | 'add'. */
    async openPicker(mode) {
        this.pickerMode = mode;
        this.pickerTitleEl.textContent = mode === 'load' ? 'Load a show' : 'Add a show to the playlist';
        this.pickerConfirmBtn.textContent = mode === 'load' ? 'Load this show' : 'Add to playlist';
        this.pickerConfirmBtn.disabled = true;
        this.showPickerEl.style.display = '';
        this.directoryThumbnailsEl.innerHTML = '<div class="no-preview">Browse to a folder to see a preview</div>';
        // No path argument: the server opens the configured library folder.
        await this.browseDirectory(null);
    }

    closePicker() {
        this.showPickerEl.style.display = 'none';
        this.pickerMode = null;
    }

    /** Use the folder currently open in the picker. */
    async confirmPicker() {
        const path = this.currentPath;
        if (!path) return;
        const mode = this.pickerMode;
        this.closePicker();

        if (mode === 'add') {
            await this.addCurrentDirectoryToPlaylist();
            // Adding means you're working on the playlist, so show it.
            this.selection = { type: 'playlist', name: this.currentSavedPlaylistName, path: null };
            await this.saveSettings({ loaded_kind: 'playlist' });
            await this.loadPlaylist();
        } else {
            await this.loadShow(path);
        }
    }

    /**
     * Load a single show and start it, replacing whatever was playing.
     * Loading plays immediately rather than queueing: what's listed is always
     * what's on the screens, which is both simpler and keeps the "Now Playing"
     * marker honest.
     */
    async loadShow(path) {
        const name = path.split('/').filter(Boolean).pop() || path;
        await this.saveSettings({ loaded_kind: 'show' });
        await this.playShow(path);
        await this.loadPlaylist();
    }

    /** Reflect the selection (and, while running, what's actually playing). */
    /**
     * Mark the playing row in the list with a "Now Playing" label and the
     * time left, and clear it from every other row. There is no separate
     * current-show panel — the list itself shows what's on.
     */
    updateNowPlaying(playlistStatus = null) {
        const rows = this.playlistListEl
            ? this.playlistListEl.querySelectorAll('.playlist-item')
            : [];
        if (!rows.length) return;

        const item = playlistStatus && playlistStatus.current_item;
        let playingRow = null;
        let label = '';

        if (playlistStatus && playlistStatus.running && item) {
            playingRow = this.playlistListEl.querySelector(`[data-item-id="${item.id}"]`);
            const mins = Math.floor(playlistStatus.time_remaining / 60);
            const secs = playlistStatus.time_remaining % 60;
            const clock = `${mins}:${String(secs).padStart(2, '0')} left`;
            label = playlistStatus.paused ? `Now Playing · paused · ${clock}` : `Now Playing · ${clock}`;
        } else if (this.showPlaying && this.selection.type === 'show') {
            // A single show is casting AND it's the show being listed. The
            // selection check matters: without it, a show left playing while a
            // playlist is displayed would badge that playlist's first row.
            playingRow = rows[0];
            label = 'Now Playing';
        }

        rows.forEach(row => {
            const badge = row.querySelector('.now-playing-badge');
            if (!badge) return;
            if (row === playingRow) {
                badge.textContent = label;
                badge.style.display = '';
            } else {
                badge.style.display = 'none';
            }
        });
    }

    async startPlaylistSlideshow() {
        try {
            await this.saveSettings();
            const response = await fetch('/api/playlist/start', { method: 'POST' });
            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to start playlist');
            }

            this.selection = { type: 'playlist', name: this.currentSavedPlaylistName, path: null };
            this.updateNowPlaying();
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

            // Stop only stops playback — whatever is loaded stays loaded, the
            // same way Load Playlist / Load Show leave things. (An All Shows
            // run ends, so the stored playlist reappears via loadPlaylist.)
            this.showPlaying = false;
            await this.loadPlaylist();
            this.updateNowPlaying();

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
        // A single show casts outside playlist mode, so playlist status events
        // report running=false while it plays; keep the transport live for it.
        const playing = isRunning || this.showPlaying;

        // Play stays enabled while something plays: it starts what's *loaded*,
        // which may differ from what's currently casting.
        this.startPlaylistBtn.disabled = false;
        this.stopSlideshowBtn.disabled = !playing;
        // Pause/Skip act on playlist timing, which a single show has none of.
        this.pauseSlideshowBtn.disabled = !isRunning;
        this.skipSlideshowBtn.disabled = !isRunning;

        if (!playing) this.updateNowPlaying();
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
        // The picker's confirm button is the only thing gated on a browsed folder.
        if (this.pickerConfirmBtn) this.pickerConfirmBtn.disabled = !this.currentPath;
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

    // Screen Schedule Methods
    async loadSchedule() {
        try {
            const response = await fetch('/api/schedule');
            this.renderSchedule(await response.json());
        } catch (error) {
            this.logMessage(`Error loading schedule: ${error.message}`, 'error');
        }
    }

    async saveSchedule() {
        const payload = {
            enabled: this.scheduleEnabledEl.checked,
            on_time: this.scheduleOnTimeEl.value,
            off_time: this.scheduleOffTimeEl.value
        };
        try {
            const response = await fetch('/api/schedule', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Failed to save schedule');
            this.logMessage(
                `Schedule saved: ${payload.enabled ? 'enabled' : 'disabled'}, on ${payload.on_time}, off ${payload.off_time}`,
                'success'
            );
        } catch (error) {
            this.logMessage(`Error saving schedule: ${error.message}`, 'error');
            await this.loadSchedule();   // revert the inputs to what the server has
        }
    }

    async runScheduleNow(state) {
        const btn = state === 'on' ? this.scheduleRunOnBtn : this.scheduleRunOffBtn;
        const label = btn.textContent;
        this.scheduleRunOnBtn.disabled = this.scheduleRunOffBtn.disabled = true;
        btn.textContent = state === 'on' ? 'Turning on…' : 'Turning off…';
        this.logMessage(`Running "${state}" sequence now (first time on a screen may need you to press Allow on the TV)`, 'info');
        try {
            const response = await fetch(`/api/schedule/run/${state}`, { method: 'POST' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || `Failed to run ${state}`);
            this.logMessage(this.describeScheduleResult(result), result.ok ? 'success' : 'error');
        } catch (error) {
            this.logMessage(`Error running ${state}: ${error.message}`, 'error');
        } finally {
            btn.textContent = label;
            this.scheduleRunOnBtn.disabled = this.scheduleRunOffBtn.disabled = false;
            await this.loadSchedule();
        }
    }

    async checkScreens() {
        this.scheduleScreensEl.textContent = 'Checking…';
        try {
            const response = await fetch('/api/schedule/power-states');
            this.renderScreenStates(await response.json());
        } catch (error) {
            this.scheduleScreensEl.textContent = '—';
            this.logMessage(`Error checking screens: ${error.message}`, 'error');
        }
    }

    renderSchedule(status) {
        if (!status) return;
        this.scheduleEnabledEl.checked = !!status.enabled;
        if (status.on_time) this.scheduleOnTimeEl.value = status.on_time;
        if (status.off_time) this.scheduleOffTimeEl.value = status.off_time;

        if (!status.enabled) {
            this.scheduleSummaryEl.textContent = 'Disabled';
        } else if (!status.desired_now) {
            this.scheduleSummaryEl.textContent = 'Enabled, but on and off times are the same — nothing will happen';
        } else {
            const next = status.next_transition;
            this.scheduleSummaryEl.textContent =
                `Enabled — screens should be ${status.desired_now.toUpperCase()} now` +
                (next ? ` (next: ${next.state} at ${next.at})` : '');
        }

        const last = status.last_action;
        this.scheduleLastActionEl.textContent = last ? this.describeScheduleResult(last) : 'None yet';
        if (last && last.screens) this.renderScreenStates(last.screens);
    }

    renderScreenStates(states) {
        const parts = Object.entries(states || {}).map(([name, s]) => `${name}: ${s || 'unreachable'}`);
        this.scheduleScreensEl.textContent = parts.length ? parts.join(' · ') : 'No enabled screens';
    }

    describeScheduleResult(r) {
        const screens = Object.entries(r.screens || {}).map(([n, s]) => `${n} ${s || 'unreachable'}`).join(', ');
        const errors = r.errors && Object.keys(r.errors).length
            ? ' — errors: ' + Object.entries(r.errors).map(([n, e]) => `${n}: ${e}`).join('; ')
            : '';
        const discovery = r.discovery ? ` (discovery ${r.discovery})` : '';
        return `${r.action.toUpperCase()} (${r.reason}${r.at ? ', ' + r.at : ''}) ${r.ok ? '✓' : '✗'} — show: ${r.show}${discovery}; ${screens}${errors}`;
    }

    // Playlist Management Methods
    /**
     * Render what's loaded, in order of precedence:
     *   1. a virtual playlist (All Shows) while it's playing
     *   2. a single loaded show, shown as a one-item list
     *   3. the stored playlist
     */
    async loadPlaylist() {
        try {
            const response = await fetch('/api/playlist');
            const data = await response.json();

            this.isVirtualPlaylist = !!data.virtual;

            if (this.isVirtualPlaylist) {
                this.playlistItems = data.items || [];
                this.playlistLabelEl.textContent = 'Now Playing:';
                this.playlistNameEl.textContent = `(Playlist) ${data.virtual_name || 'All Shows'}`;
                this.playlistDirtyEl.style.display = 'none';
            } else if (this.selection.type === 'all-shows' && this.allShowsQueue) {
                // All Shows loaded but not yet playing.
                this.playlistItems = this.allShowsQueue;
                this.playlistLabelEl.textContent = 'Now Playing:';
                this.playlistNameEl.textContent = '(Playlist) All Shows';
                this.playlistDirtyEl.style.display = 'none';
            } else if (this.selection.type === 'show' && this.selection.path) {
                // Single show: a one-item list, so what you see is what plays.
                this.playlistItems = [{
                    id: 'loaded-show',
                    directory_path: this.selection.path,
                    directory_name: this.selection.name,
                    duration_minutes: null,
                    is_valid: 1
                }];
                this.playlistLabelEl.textContent = 'Now Playing:';
                this.playlistNameEl.textContent = this.selection.name;
                this.playlistDirtyEl.style.display = 'none';
            } else {
                this.playlistItems = data.items || [];
                this.playlistLabelEl.textContent = 'Now Playing:';
                this.playlistNameEl.textContent = `(Playlist) ${this.currentSavedPlaylistName}`;
            }

            this._wasVirtual = this.isVirtualPlaylist;
            this._wasShow = this.selection.type === 'show';
            this._wasAllShows = this.selection.type === 'all-shows';
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

        // A virtual playlist isn't stored, so its rows can't be edited.
        if (this.isVirtualPlaylist) {
            this.playlistListEl.querySelectorAll(
                '.playlist-item-remove, .playlist-item-duration, .playlist-item-drag-handle'
            ).forEach(el => {
                el.disabled = true;
                el.style.opacity = '0.35';
                el.style.pointerEvents = 'none';
                el.title = 'All Shows is a temporary playlist and cannot be edited';
            });
            this.playlistListEl.querySelectorAll('.playlist-item').forEach(el => {
                el.draggable = false;
            });
        }

        this.updateNowPlaying();
    }

    createPlaylistItemElement(item, index) {
        const div = document.createElement('div');
        const broken = !item.is_valid;
        div.className = `playlist-item ${broken ? 'invalid item-broken' : ''}`;
        div.draggable = true;
        div.dataset.itemId = item.id;

        const durationOptions = [1, 2, 5, 10, 15, 20, 30, 45, 60]
            .map(minutes => `<option value="${minutes}" ${item.duration_minutes === minutes ? 'selected' : ''}>${minutes} min</option>`)
            .join('');

        // A loaded single show has no playlist duration and nothing to remove.
        const isLoadedShow = item.id === 'loaded-show';
        const durationCell = isLoadedShow
            ? ''
            : `<div class="playlist-item-duration">
                <select onchange="controller.updatePlaylistItemDuration(${item.id}, this.value)">
                    ${durationOptions}
                </select>
            </div>`;
        const actionsCell = isLoadedShow
            ? ''
            : `<div class="playlist-item-actions">
                <button class="playlist-item-remove" onclick="controller.removePlaylistItem(${item.id})" title="Remove">
                    🗑️
                </button>
            </div>`;
        const brokenBadge = broken
            ? '<span class="item-broken-badge" title="This folder no longer exists, so this show is skipped">MISSING</span>'
            : '';

        div.innerHTML = `
            <span class="playlist-item-number">[${index + 1}]</span>
            <span class="playlist-item-drag-handle">☰</span>
            <div class="playlist-item-thumbnail">
                <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3Crect width='40' height='40' fill='%23f0f0f0'/%3E%3Ctext x='50%' y='50%' text-anchor='middle' dy='.3em' fill='%23999'%3E📷%3C/text%3E%3C/svg%3E" alt="Loading..." class="thumbnail-img">
            </div>
            <div class="playlist-item-info">
                <div class="playlist-item-name">${item.directory_name}${brokenBadge}</div>
                <span class="now-playing-badge" style="display:none"></span>
            </div>
            ${durationCell}
            ${actionsCell}
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
        this.playlistLabelEl.textContent = 'Now Playing:';
        this.playlistNameEl.textContent = `(Playlist) ${name}`;
        this.playlistDirtyEl.style.display = 'none';
        // Loading or creating a playlist leaves single-show / All Shows mode.
        this.selection = { type: 'playlist', name, path: null };
        this.allShowsQueue = null;
        // Persist what's loaded so it survives a page reload (and reappears
        // correctly after an All Shows run).
        this.saveSettings({ current_playlist_name: name, loaded_kind: 'playlist' });
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

            // "All Shows" first: every show across all saved playlists.
            const allItem = document.createElement('div');
            allItem.className = 'playlist-dropdown-item playlist-dropdown-all';
            allItem.innerHTML = '<span class="playlist-dropdown-item-name">All Shows</span>';
            allItem.addEventListener('click', (e) => {
                e.stopPropagation();
                this.hideLoadDropdown();
                this.loadAllShows();
            });
            this.loadPlaylistDropdown.appendChild(allItem);

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

            // "Create New" sits under the saved playlists, as its own option.
            const createItem = document.createElement('div');
            createItem.className = 'playlist-dropdown-item playlist-dropdown-create';
            createItem.innerHTML = '<span class="playlist-dropdown-item-name">+ Create New</span>';
            createItem.addEventListener('click', (e) => {
                e.stopPropagation();
                this.hideLoadDropdown();
                this.createPlaylist();
            });
            this.loadPlaylistDropdown.appendChild(createItem);

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
                // Loading swaps what's on the screens straight away.
                await this.startPlaylistSlideshow();
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
        // Play stays enabled — see updateSlideshowControls.
        this.startPlaylistBtn.disabled = false;
        this.pauseSlideshowBtn.disabled = !isRunning;
        this.skipSlideshowBtn.disabled = !isRunning;

        if (isPaused) {
            this.pauseSlideshowBtn.classList.add('is-paused');
        } else {
            this.pauseSlideshowBtn.classList.remove('is-paused');
        }
    }

    updatePlaylistProgress(status) {
        this.updateNowPlaying(status);

        // Update pause button text
        if (status.paused) {
            this.pauseSlideshowBtn.classList.add('is-paused');
        } else {
            this.pauseSlideshowBtn.classList.remove('is-paused');
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