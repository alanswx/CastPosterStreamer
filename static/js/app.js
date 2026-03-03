class ChromecastSlideshowController {
    constructor() {
        this.socket = io();
        this.currentPath = '';
        this.selectedDirectory = '';
        this.devices = [];
        this.images = [];
        this.isConnected = false;
        
        this.initializeElements();
        this.setupEventListeners();
        this.setupSocketEventListeners();
        this.loadInitialData();
    }
    
    initializeElements() {
        // Directory elements
        this.currentPathEl = document.getElementById('current-path');
        this.directoryListEl = document.getElementById('directory-list');
        this.selectDirectoryBtn = document.getElementById('select-directory');
        
        // Image elements
        this.selectedDirectoryEl = document.getElementById('selected-directory');
        this.imageCountEl = document.getElementById('image-count');
        this.imageGridEl = document.getElementById('image-grid');
        this.refreshImagesBtn = document.getElementById('refresh-images');
        
        // Device elements
        this.discoverDevicesBtn = document.getElementById('discover-devices');
        this.deviceCountEl = document.getElementById('device-count');
        this.deviceListEl = document.getElementById('device-list');
        
        // Control elements
        this.slideshowIntervalEl = document.getElementById('slideshow-interval');
        this.rotationEnabledEl = document.getElementById('rotation-enabled');
        this.startSlideshowBtn = document.getElementById('start-slideshow');
        this.stopSlideshowBtn = document.getElementById('stop-slideshow');
        this.skipNextBtn = document.getElementById('skip-next');
        
        // Status elements
        this.connectionStatusEl = document.getElementById('connection-status');
        this.slideshowRunningEl = document.getElementById('slideshow-running');
        this.currentImagesEl = document.getElementById('current-images');
        
        // Log elements
        this.logContainerEl = document.getElementById('log-container');
        this.clearLogBtn = document.getElementById('clear-log');
        this.autoScrollEl = document.getElementById('auto-scroll');
    }
    
    setupEventListeners() {
        // Directory browser events
        this.selectDirectoryBtn.addEventListener('click', () => this.selectCurrentDirectory());
        
        // Image events
        this.refreshImagesBtn.addEventListener('click', () => this.refreshImages());
        
        // Device events
        this.discoverDevicesBtn.addEventListener('click', () => this.discoverDevices());
        
        // Control events
        this.slideshowIntervalEl.addEventListener('change', () => this.saveSettings());
        this.rotationEnabledEl.addEventListener('change', () => this.saveSettings());
        this.startSlideshowBtn.addEventListener('click', () => this.startSlideshow());
        this.stopSlideshowBtn.addEventListener('click', () => this.stopSlideshow());
        this.skipNextBtn.addEventListener('click', () => this.skipToNext());
        
        // Log events
        this.clearLogBtn.addEventListener('click', () => this.clearLog());
    }
    
    setupSocketEventListeners() {
        this.socket.on('connect', () => {
            this.isConnected = true;
            this.updateConnectionStatus();
            this.logMessage('Connected to server', 'success');
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
        
        this.socket.on('slideshow_started', () => {
            this.updateSlideshowControls(true);
            this.logMessage('Slideshow started', 'success');
        });
        
        this.socket.on('slideshow_stopped', () => {
            this.updateSlideshowControls(false);
            this.logMessage('Slideshow stopped', 'info');
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
    }
    
    async loadInitialData() {
        await this.loadSettings();
        await this.browseDirectory();
        await this.loadDevices();
        await this.loadImages();
        await this.loadSlideshowStatus();
    }
    
    async loadSettings() {
        try {
            const response = await fetch('/api/settings');
            const settings = await response.json();
            
            if (settings.slideshow_interval) {
                this.slideshowIntervalEl.value = settings.slideshow_interval;
            }
            
            if (settings.rotation_enabled !== undefined) {
                this.rotationEnabledEl.checked = settings.rotation_enabled === 'true';
            }
            
            if (settings.selected_directory) {
                this.selectedDirectory = settings.selected_directory;
                this.selectedDirectoryEl.textContent = settings.selected_directory;
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
                    this.logMessage(`Permission denied: "${path}". Grant Full Disk Access to Terminal in System Settings → Privacy & Security → Full Disk Access, then restart the server.`, 'error');
                } else {
                    this.logMessage(`Error browsing directory: ${data.error}`, 'error');
                }
                return;
            }

            this.currentPath = data.current_path;
            this.currentPathEl.textContent = data.current_path;
            this.updateDirectoryList(data.items);

            if (this.selectedDirectory === data.current_path) {
                this.selectDirectoryBtn.disabled = false;
                this.selectDirectoryBtn.textContent = 'Directory Selected ✓';
            } else {
                this.selectDirectoryBtn.disabled = false;
                this.selectDirectoryBtn.textContent = 'Select This Directory';
            }
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
    
    async selectCurrentDirectory() {
        try {
            await this.saveSettings({ selected_directory: this.currentPath });
            this.selectedDirectory = this.currentPath;
            this.selectedDirectoryEl.textContent = this.currentPath;
            this.selectDirectoryBtn.textContent = 'Directory Selected ✓';
            this.logMessage(`Selected directory: ${this.currentPath}`, 'success');
            
            await this.loadImages();
        } catch (error) {
            this.logMessage(`Error selecting directory: ${error.message}`, 'error');
        }
    }
    
    async loadImages() {
        if (!this.selectedDirectory) return;
        
        try {
            const response = await fetch('/api/images');
            const data = await response.json();
            
            this.images = data.images || [];
            this.imageCountEl.textContent = data.count || 0;
            this.updateImageGrid();
        } catch (error) {
            this.logMessage(`Error loading images: ${error.message}`, 'error');
        }
    }
    
    updateImageGrid() {
        if (this.images.length === 0) {
            this.imageGridEl.innerHTML = '<div class="no-images">No images found in selected directory</div>';
            return;
        }
        
        this.imageGridEl.innerHTML = '';
        
        this.images.forEach(imageData => {
            const div = document.createElement('div');
            div.className = 'image-thumbnail';
            
            // Use actual thumbnail URL if available, otherwise placeholder
            const imageSrc = imageData.thumbnail_url || 
                `data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='150' height='120'%3E%3Crect width='150' height='120' fill='%23f0f0f0'/%3E%3Ctext x='50%25' y='50%25' text-anchor='middle' dy='.3em' fill='%23999'%3EImage%3C/text%3E%3C/svg%3E`;
            
            const imageName = typeof imageData === 'string' ? imageData : imageData.name;
            
            div.innerHTML = `
                <img src="${imageSrc}" alt="${imageName}" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'150\\' height=\\'120\\'%3E%3Crect width=\\'150\\' height=\\'120\\' fill=\\'%23f0f0f0\\'/%3E%3Ctext x=\\'50%25\\' y=\\'50%25\\' text-anchor=\\'middle\\' dy=\\'.3em\\' fill=\\'%23999\\'%3EError%3C/text%3E%3C/svg%3E'">
                <div class="filename">${imageName}</div>
            `;
            
            this.imageGridEl.appendChild(div);
        });
    }
    
    async refreshImages() {
        this.logMessage('Refreshing images...', 'info');
        await this.loadImages();
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
    
    async startSlideshow() {
        if (!this.canStartSlideshow()) {
            return;
        }
        
        try {
            const response = await fetch('/api/slideshow/start', { method: 'POST' });
            const result = await response.json();
            
            if (!response.ok) {
                throw new Error(result.error || 'Failed to start slideshow');
            }
        } catch (error) {
            this.logMessage(`Error starting slideshow: ${error.message}`, 'error');
        }
    }
    
    async stopSlideshow() {
        try {
            const response = await fetch('/api/slideshow/stop', { method: 'POST' });
            const result = await response.json();
            
            if (!response.ok) {
                throw new Error(result.error || 'Failed to stop slideshow');
            }
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
    
    updateSlideshowControls(isRunning) {
        this.startSlideshowBtn.disabled = isRunning || !this.canStartSlideshow();
        this.stopSlideshowBtn.disabled = !isRunning;
        this.skipNextBtn.disabled = !isRunning;
        this.slideshowRunningEl.textContent = isRunning ? 'Running' : 'Stopped';
        this.slideshowRunningEl.style.color = isRunning ? '#27ae60' : '#e74c3c';
    }
    
    updateCurrentImages(images) {
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
        this.startSlideshowBtn.disabled = !this.canStartSlideshow();
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
}

// Initialize the controller when the page loads
let controller;
document.addEventListener('DOMContentLoaded', () => {
    controller = new ChromecastSlideshowController();
    // Make controller globally accessible for inline event handlers
    window.controller = controller;
});