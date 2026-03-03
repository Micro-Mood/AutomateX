/**
 * AutomateX Web UI - Preload Script
 * 在渲染进程加载前运行，通过contextBridge安全地暴露API
 */

const { contextBridge, ipcRenderer } = require('electron');

// 暴露安全的API给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
    // 窗口控制
    minimizeWindow: () => ipcRenderer.invoke('window:minimize'),
    maximizeWindow: () => ipcRenderer.invoke('window:maximize'),
    closeWindow: () => ipcRenderer.invoke('window:close'),
    
    // 窗口状态监听
    onWindowMaximized: (callback) => {
        ipcRenderer.on('window:maximized', () => callback(true));
    },
    onWindowUnmaximized: (callback) => {
        ipcRenderer.on('window:unmaximized', () => callback(false));
    },
    
    // 服务器状态
    onServerReady: (callback) => {
        ipcRenderer.on('server:ready', (event, port) => callback(port));
    },
    onServerError: (callback) => {
        ipcRenderer.on('server:error', (event, error) => callback(error));
    },
    
    // 获取服务器端口
    getServerPort: () => ipcRenderer.invoke('server:getPort'),
    
    // 打开外部链接
    openExternal: (url) => ipcRenderer.invoke('shell:openExternal', url),
    
    // 选择文件/文件夹
    selectFile: (options) => ipcRenderer.invoke('dialog:selectFile', options),
    selectFolder: () => ipcRenderer.invoke('dialog:selectFolder'),
    
    // 系统信息
    getPlatform: () => process.platform,
    getVersion: () => ipcRenderer.invoke('app:getVersion'),
    
    // 通知
    showNotification: (title, body) => ipcRenderer.invoke('notification:show', { title, body }),
    
    // 剪贴板
    copyToClipboard: (text) => ipcRenderer.invoke('clipboard:write', text),
    readFromClipboard: () => ipcRenderer.invoke('clipboard:read'),
});

// 暴露任务相关API
contextBridge.exposeInMainWorld('taskAPI', {
    // 任务CRUD操作 - 这些会通过HTTP API调用后端
    // 这里只提供辅助方法
    
    // 格式化日期时间
    formatDateTime: (isoString) => {
        if (!isoString) return '-';
        const date = new Date(isoString);
        return date.toLocaleString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    },
    
    // 格式化相对时间
    formatRelativeTime: (isoString) => {
        if (!isoString) return '-';
        const date = new Date(isoString);
        const now = new Date();
        const diff = now - date;
        
        const seconds = Math.floor(diff / 1000);
        const minutes = Math.floor(seconds / 60);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);
        
        if (days > 0) return `${days}天前`;
        if (hours > 0) return `${hours}小时前`;
        if (minutes > 0) return `${minutes}分钟前`;
        return '刚刚';
    },
    
    // 状态映射
    getStatusInfo: (status) => {
        const statusMap = {
            'pending': { text: '待处理', class: 'status-pending', icon: '⏳' },
            'planning': { text: '规划中', class: 'status-planning', icon: '🧠' },
            'executing': { text: '执行中', class: 'status-executing', icon: '⚡' },
            'waiting_input': { text: '等待输入', class: 'status-waiting', icon: '✋' },
            'paused': { text: '已暂停', class: 'status-paused', icon: '⏸️' },
            'completed': { text: '已完成', class: 'status-completed', icon: '✅' },
            'failed': { text: '失败', class: 'status-failed', icon: '❌' },
            'cancelled': { text: '已取消', class: 'status-cancelled', icon: '🚫' }
        };
        return statusMap[status] || { text: status, class: 'status-unknown', icon: '❓' };
    },
    
    // 生成唯一ID
    generateId: () => {
        return 'task_' + Date.now().toString(36) + Math.random().toString(36).substr(2);
    }
});

// 页面加载完成后的初始化
window.addEventListener('DOMContentLoaded', () => {
    console.log('AutomateX Web UI - Preload script loaded');
    
    // 添加平台标识到body
    document.body.classList.add(`platform-${process.platform}`);
    
    // 如果是macOS，调整窗口控制按钮位置
    if (process.platform === 'darwin') {
        document.body.classList.add('macos');
    }
});

// 处理拖放文件
window.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.stopPropagation();
});

window.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    
    // 触发自定义事件，让渲染进程处理
    const files = Array.from(e.dataTransfer.files).map(f => f.path);
    if (files.length > 0) {
        window.dispatchEvent(new CustomEvent('files-dropped', { detail: files }));
    }
});
