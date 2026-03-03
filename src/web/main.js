const { app, BrowserWindow, ipcMain, Tray, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

let mainWindow;
let tray = null;
let isQuitting = false;
let serverProcess = null;
let mcpServerProcess = null;

// 获取资源路径
function getResourcePath(filename) {
    if (app.isPackaged) {
        return path.join(app.getAppPath(), filename);
    }
    return path.join(__dirname, filename);
}

function getPythonPath() {
    if (app.isPackaged && process.platform === 'win32') {
        const bundledPython = path.join(process.resourcesPath, 'python', 'python.exe');
        if (fs.existsSync(bundledPython)) {
            return bundledPython;
        }
    }
    return process.platform === 'win32' ? 'python' : 'python3';
}

// 初始化嵌入式 Python：在 ._pth 中动态注入 resources 和 web 路径
function initEmbeddedPython() {
    if (!app.isPackaged) return;
    const pythonDir = path.join(process.resourcesPath, 'python');
    const pthFiles = fs.readdirSync(pythonDir).filter(f => f.endsWith('._pth'));
    for (const pthFile of pthFiles) {
        const pthPath = path.join(pythonDir, pthFile);
        let content = fs.readFileSync(pthPath, 'utf8');
        const resPath = process.resourcesPath.replace(/\\/g, '/');
        const webPath = path.join(process.resourcesPath, 'web').replace(/\\/g, '/');
        // 避免重复添加
        if (!content.includes(resPath)) {
            content = content.trimEnd() + '\n' + resPath + '\n' + webPath + '\n';
            fs.writeFileSync(pthPath, content, 'utf8');
            console.log(`[Python] 已向 ${pthFile} 注入路径: ${resPath}`);
        }
    }
}

// 启动后端服务器
function startServer() {
    const serverScript = app.isPackaged
        ? path.join(process.resourcesPath, 'web', 'server.pyc')
        : path.join(__dirname, 'server.py');
    const pythonPath = getPythonPath();
    
    const serverCwd = app.isPackaged ? process.resourcesPath : path.dirname(serverScript);
    serverProcess = spawn(pythonPath, [serverScript], {
        cwd: serverCwd,
        stdio: ['pipe', 'pipe', 'pipe'],
        env: {
            ...process.env,
            PYTHONUTF8: '1',
            PYTHONIOENCODING: 'UTF-8',
            PYTHONPATH: app.isPackaged ? process.resourcesPath : ''
        }
    });

    serverProcess.stdout.on('data', (data) => {
        console.log(`[Server] ${data.toString('utf8')}`);
    });

    serverProcess.stderr.on('data', (data) => {
        const msg = data.toString('utf8');
        // 过滤 INFO 级别日志，不当作错误显示
        if (msg.includes('INFO:') || msg.includes('DeprecationWarning')) {
            console.log(`[Server] ${msg}`);
        } else {
            console.error(`[Server Error] ${msg}`);
        }
    });

    serverProcess.on('error', (err) => {
        console.error(`[Server] 启动失败: ${err.message}`);
    });

    serverProcess.on('close', (code) => {
        console.log(`[Server] 进程退出，代码: ${code}`);
        serverProcess = null;
    });

    console.log('[Server] 后端服务器已启动');
}

// 停止后端服务器
function stopServer() {
    if (serverProcess) {
        serverProcess.kill();
        serverProcess = null;
        console.log('[Server] 后端服务器已停止');
    }
}

// 启动 MCP Server
function startMCPServer() {
    const mcpDir = app.isPackaged 
        ? path.join(process.resourcesPath, 'src', 'mcp')
        : path.join(__dirname, '..', 'mcp');
    
    const mcpSrcDir = mcpDir;
    const pythonPath = getPythonPath();
    
    // 不通过 CLI 强制传 workspace（MCP 无此参数或不一致）
    mcpServerProcess = spawn(pythonPath, ['-m', 'src.mcp.server', '--port', '8080'], {
        cwd: app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..'),
        stdio: ['pipe', 'pipe', 'pipe'],
        env: {
            ...process.env,
            PYTHONUTF8: '1',
            PYTHONIOENCODING: 'UTF-8',
            PYTHONPATH: app.isPackaged ? process.resourcesPath : path.join(__dirname, '..', '..')
        }
    });

    mcpServerProcess.stdout.on('data', (data) => {
        console.log(`[MCP] ${data.toString('utf8')}`);
    });

    mcpServerProcess.stderr.on('data', (data) => {
        const msg = data.toString('utf8');
        if (msg.includes('INFO:') || msg.includes('DeprecationWarning')) {
            console.log(`[MCP] ${msg}`);
        } else {
            console.error(`[MCP Error] ${msg}`);
        }
    });

    mcpServerProcess.on('error', (err) => {
        console.error(`[MCP] 启动失败: ${err.message}`);
    });

    mcpServerProcess.on('close', (code) => {
        console.log(`[MCP] 进程退出，代码: ${code}`);
        mcpServerProcess = null;
    });

    console.log('[MCP] MCP Server 已启动 (port 8080)');
}

// 停止 MCP Server
function stopMCPServer() {
    if (mcpServerProcess) {
        mcpServerProcess.kill();
        mcpServerProcess = null;
        console.log('[MCP] MCP Server 已停止');
    }
}

// 创建主窗口
function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 1024,
        minHeight: 700,
        show: true,
        skipTaskbar: false,
        frame: false,
        backgroundColor: '#1a1a2e',
        webPreferences: {
            preload: path.join(__dirname, app.isPackaged ? 'preload-loader.js' : 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false
        },
        icon: process.platform === 'win32'
            ? path.join(app.isPackaged ? path.join(__dirname, '..', 'app.asar.unpacked') : __dirname, 'assets', 'logo.ico')
            : path.join(__dirname, 'assets', 'logo.svg')
    });

    mainWindow.loadFile(getResourcePath('index.html'));

    // 开发模式下打开DevTools
    if (process.argv.includes('--dev')) {
        mainWindow.webContents.openDevTools();
    }

    mainWindow.on('closed', () => {
        mainWindow = null;
    });

    // 关闭/最小化时隐藏到托盘
    mainWindow.on('close', (e) => {
        if (!isQuitting) {
            e.preventDefault();
            hideToTray();
        }
    });

    mainWindow.on('minimize', (e) => {
        e.preventDefault();
        hideToTray();
    });

    mainWindow.on('show', () => {
        mainWindow.setSkipTaskbar(false);
    });

    mainWindow.on('hide', () => {
        mainWindow.setSkipTaskbar(true);
    });

    // 窗口状态变化通知
    mainWindow.on('maximize', () => {
        mainWindow.webContents.send('window-state', 'maximized');
    });

    mainWindow.on('unmaximize', () => {
        mainWindow.webContents.send('window-state', 'restored');
    });

    mainWindow.on('enter-full-screen', () => {
        mainWindow.webContents.send('window-state', 'fullscreen');
    });

    mainWindow.on('leave-full-screen', () => {
        mainWindow.webContents.send('window-state', 'restored');
    });
}

function getIconPath() {
    const base = app.isPackaged
        ? path.join(process.resourcesPath, 'app.asar.unpacked')
        : __dirname;
    return process.platform === 'win32'
        ? path.join(base, 'assets', 'logo.ico')
        : path.join(base, 'assets', 'logo.svg');
}

function showFromTray() {
    if (!mainWindow) {
        createWindow();
    }
    mainWindow.show();
    mainWindow.focus();
    mainWindow.setSkipTaskbar(false);
}

function hideToTray() {
    if (mainWindow) {
        mainWindow.hide();
        mainWindow.setSkipTaskbar(true);
    }
}

function createTray() {
    if (tray) return;
    tray = new Tray(getIconPath());
    tray.setToolTip('AutomateX');

    const contextMenu = Menu.buildFromTemplate([
        {
            label: '显示窗口',
            click: () => showFromTray()
        },
        { type: 'separator' },
        {
            label: '退出',
            click: () => app.quit()
        }
    ]);

    tray.setContextMenu(contextMenu);

    tray.on('click', () => {
        if (mainWindow && mainWindow.isVisible()) {
            hideToTray();
        } else {
            showFromTray();
        }
    });
}

// 轮询检测服务是否就绪
function waitForServer(url, callback, interval = 200, maxAttempts = 50) {
    let attempts = 0;
    const check = () => {
        const http = require('http');
        const req = http.get(url, (res) => {
            callback();
        });
        req.on('error', () => {
            attempts++;
            if (attempts < maxAttempts) {
                setTimeout(check, interval);
            } else {
                console.error(`[Startup] 服务 ${url} 启动超时，强制继续`);
                callback();
            }
        });
        req.setTimeout(500, () => { req.destroy(); });
    };
    check();
}

// 应用就绪
app.whenReady().then(() => {
    createTray();

    // 初始化嵌入式 Python 路径
    initEmbeddedPython();

    // 先启动 MCP Server
    startMCPServer();
    
    // MCP 启动后立即启动后端服务（无需盲等）
    startServer();
    
    // 轮询检测后端服务就绪后再创建窗口
    waitForServer('http://127.0.0.1:8000/api/health', () => {
        createWindow();
    });

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on('before-quit', () => {
    isQuitting = true;
    stopServer();
    stopMCPServer();
});

// 所有窗口关闭时
app.on('window-all-closed', () => {
    // 保持驻留托盘，不退出
    if (isQuitting && process.platform !== 'darwin') {
        app.quit();
    }
});

// 应用退出前
app.on('before-quit', () => {
    stopServer();
    stopMCPServer();
});

// ========== IPC 通信 ==========

// 窗口控制
ipcMain.on('window-control', (event, action) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) return;

    switch (action) {
        case 'minimize':
            window.minimize();
            break;
        case 'maximize-toggle':
            if (window.isMaximized()) {
                window.restore();
            } else {
                window.maximize();
            }
            break;
        case 'close':
            window.close();
            break;
        case 'fullscreen-toggle':
            window.setFullScreen(!window.isFullScreen());
            break;
    }
});

// 窗口控制（与 preload 的 invoke 对应）
ipcMain.handle('window:minimize', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (window) window.minimize();
});

ipcMain.handle('window:maximize', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) return;
    if (window.isMaximized()) {
        window.restore();
        event.sender.send('window:unmaximized');
    } else {
        window.maximize();
        event.sender.send('window:maximized');
    }
});

ipcMain.handle('window:close', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (window) window.close();
});

// 获取窗口状态
ipcMain.handle('get-window-state', (event) => {
    const window = BrowserWindow.fromWebContents(event.sender);
    if (!window) return 'normal';
    
    if (window.isMaximized()) return 'maximized';
    if (window.isFullScreen()) return 'fullscreen';
    return 'normal';
});

// 服务器控制
ipcMain.handle('server-status', () => {
    return serverProcess !== null;
});

ipcMain.on('restart-server', () => {
    stopServer();
    setTimeout(startServer, 500);
});

// 文件夹选择对话框
ipcMain.handle('dialog:selectFolder', async () => {
    const { dialog } = require('electron');
    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ['openDirectory'],
        title: '选择工作目录'
    });
    if (!result.canceled && result.filePaths.length > 0) {
        return result.filePaths[0];
    }
    return null;
});

// 设置 MCP 工作区：写入 MCP 配置并重启 MCP Server（MCP 无动态 API 修改 workspace）
ipcMain.handle('mcp:setWorkspace', async (event, workspacePath) => {
    const fs = require('fs');
    const mcpDir = app.isPackaged ? path.join(process.resourcesPath, 'src', 'mcp') : path.join(__dirname, '..', 'mcp');
    const configDir = path.join(mcpDir, 'config');
    const configPath = path.join(configDir, 'mcp.json');

    try {
        // 确保目录存在
        fs.mkdirSync(configDir, { recursive: true });

        // 读取已有配置（若存在）并更新 workspace.root_path
        let cfg = {};
        if (fs.existsSync(configPath)) {
            try {
                cfg = JSON.parse(fs.readFileSync(configPath, 'utf8')) || {};
            } catch (e) {
                cfg = {};
            }
        }

        cfg.workspace = cfg.workspace || {};
        cfg.workspace.root_path = workspacePath;

        fs.writeFileSync(configPath, JSON.stringify(cfg, null, 2), 'utf8');

        // 通过重启 MCP Server 使新配置生效
        stopMCPServer();
        // 小延迟确保进程退出
        await new Promise((r) => setTimeout(r, 500));
        startMCPServer();

        return { status: 'ok', path: workspacePath };
    } catch (err) {
        console.error('[MCP] 设置工作区失败:', err);
        return { status: 'error', message: String(err) };
    }
});
