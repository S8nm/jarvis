const { app, BrowserWindow, screen } = require('electron');
const path = require('path');

// Prevent multiple instances — must be before anything else
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
    console.log('[JARVIS] Another instance already running — quitting');
    app.quit();
    process.exit(0);
}

// ── Configuration ──
const DEV_URL = 'http://localhost:5173';
const IS_DEV = !app.isPackaged;

function findTargetDisplay() {
    /**
     * Find the best display for JARVIS:
     * 1. Look for 49" Samsung ultra-wide (~5120x1440 or ~3840x1080)
     * 2. Fall back to display #4 (index 3)
     * 3. Fall back to the last external display
     * 4. Fall back to primary display
     */
    const displays = screen.getAllDisplays();
    console.log(`[JARVIS] Found ${displays.length} display(s):`);

    displays.forEach((d, i) => {
        console.log(`  Display ${i + 1}: ${d.size.width}x${d.size.height} @ (${d.bounds.x},${d.bounds.y}) scaleFactor=${d.scaleFactor}`);
    });

    // Strategy 1: Look for ultra-wide (49" is typically 5120x1440 or 3840x1080)
    const ultraWide = displays.find(d =>
        d.size.width >= 3840 && d.size.height <= 1440 && d.size.width / d.size.height > 2
    );
    if (ultraWide) {
        console.log('[JARVIS] Found ultra-wide display — using it');
        return ultraWide;
    }

    // Strategy 2: 4th display (index 3)
    if (displays.length >= 4) {
        console.log('[JARVIS] Using display #4');
        return displays[3];
    }

    // Strategy 3: Last non-primary display
    const externals = displays.filter(d => !d.isPrimary || displays.length === 1);
    if (externals.length > 0) {
        const target = externals[externals.length - 1];
        console.log(`[JARVIS] Using last external display: ${target.size.width}x${target.size.height}`);
        return target;
    }

    // Strategy 4: Primary
    console.log('[JARVIS] Falling back to primary display');
    return screen.getPrimaryDisplay();
}

function createWindow() {
    const targetDisplay = findTargetDisplay();
    const { x, y, width, height } = targetDisplay ? targetDisplay.bounds : { x: 0, y: 0, width: 800, height: 600 };

    const win = new BrowserWindow({
        x,
        y,
        width,
        height,
        frame: false,             // Borderless
        fullscreen: true,
        transparent: false,
        backgroundColor: '#050810',
        autoHideMenuBar: true,
        alwaysOnTop: false,       // Set to true if you want always-visible
        skipTaskbar: false,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            devTools: IS_DEV,
        },
        title: 'J.A.R.V.I.S. Protocol',
        icon: path.join(__dirname, '../public/icon.png'),
    });

    if (IS_DEV) {
        win.loadURL(DEV_URL);
        // win.webContents.openDevTools(); // Uncomment for debug
    } else {
        win.loadFile(path.join(__dirname, '../dist/index.html'));
    }

    // Move to target display and go fullscreen
    win.setBounds({ x, y, width, height });

    win.on('closed', () => {
        app.quit();
    });

    return win;
}

// ── App Events ──
app.whenReady().then(() => {
    createWindow();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on('window-all-closed', () => {
    app.quit();
});

// Single-instance lock handled at top of file
