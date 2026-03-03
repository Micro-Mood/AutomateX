# 部署与打包

本文档介绍如何将 AutomateX 打包为 Windows 桌面应用并发布。

---

## 目录

- [开发模式运行](#开发模式运行)
- [打包发布](#打包发布)
- [嵌入式 Python](#嵌入式-python)
- [代码保护](#代码保护)
- [发布产物](#发布产物)
- [故障排除](#故障排除)

---

## 开发模式运行

### 后端（Python）

```bash
# 安装依赖
pip install -r requirements.txt

# 单独运行后端 API
cd src/web
python server.py --port 8000

# 单独运行 MCP Server
python -m src.mcp --port 8080
```

### 前端（Electron）

```bash
cd src/web
npm install

# 开发模式（启用 DevTools）
npm run dev

# 正式启动
npm start
```

---

## 打包发布

### 标准打包

```bash
cd src/web
npm run build:win
```

生成 Windows 安装包（`.exe`），输出到 `src/web/release-{timestamp}/` 目录。

### 带代码保护打包

```bash
cd src/web
npm run build:protected
```

使用 `bytenode` 将 JavaScript 编译为 `.jsc` 字节码，防止源码泄漏。

### 仅打包不生成安装程序

```bash
npm run pack
```

输出到 `src/web/release-{timestamp}/win-unpacked/`。

### 构建配置

打包由 `electron-builder` 驱动，配置位于 `src/web/package.json`：

```json
{
  "build": {
    "appId": "com.automatex.app",
    "productName": "AutomateX",
    "win": {
      "target": ["nsis"],
      "icon": "assets/logo.ico"
    },
    "extraResources": [
      {
        "from": "../",
        "to": "app",
        "filter": ["**/*.pyc", "**/*.json", "**/*.md"]
      }
    ]
  }
}
```

---

## 嵌入式 Python

发布版本内嵌 Python 3.10 运行时，用户无需单独安装 Python。

### 目录结构

```
src/web/build/
├── get-pip.py           # pip 安装脚本
├── python-3.10.11-embed.zip  # Python 嵌入式发行版
└── python-embed/        # 解压后的 Python 运行时
    ├── python.exe
    ├── python310.dll
    ├── python310._pth   # 路径配置
    └── Lib/
        └── site-packages/  # 依赖包
```

### 构建脚本

| 脚本 | 位置 | 作用 |
|------|------|------|
| `setup-python.js` | `src/web/scripts/` | 下载并配置嵌入式 Python |
| `compile-js.js` | `src/web/scripts/` | 编译 JS 为 .jsc |
| `build-protected.js` | `src/web/scripts/` | 带保护的完整构建流程 |
| `generate-icon.js` | `src/web/scripts/` | 生成应用图标 |

---

## 代码保护

### JavaScript 保护

使用 `bytenode` 将 Electron 的 JS 代码编译为 V8 字节码：

- `main.js` → `main.jsc`（不可逆编译）
- `preload.js` → `preload.jsc`

### Python 保护

Python 源码编译为 `.pyc` 字节码后打包：

- `.py` 文件在打包时编译为 `.pyc`
- 原始 `.py` 文件不包含在发布包中

> ⚠️ `.pyc` 可被反编译，仅提供基础保护。如需更强保护可考虑 Cython / PyArmor 等方案。

---

## 发布产物

打包完成后的输出目录：

```
src/web/release-{timestamp}/
├── AutomateX-1.0.0-x64.exe            # Windows 安装包（NSIS）
├── AutomateX-1.0.0-x64.exe.blockmap   # 增量更新映射
├── builder-debug.yml                    # 构建调试信息
├── builder-effective-config.yaml        # 实际构建配置
└── win-unpacked/                        # 解压版（免安装）
    ├── AutomateX.exe                    # 主程序
    ├── resources/                       # 应用资源
    │   └── app/                         # Python 源码 + 配置
    └── ...                              # Electron 运行时文件
```

---

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AUTOMATEX_LOG_LEVEL` | 日志级别 | `INFO` |
| `AUTOMATEX_WS_TOKEN` | WebSocket 认证 Token | 空（不校验） |
| `AUTOMATEX_WORKING_DIR` | 默认工作目录 | 用户主目录 |

---

## 端口说明

| 端口 | 服务 | 配置位置 |
|------|------|----------|
| 8000 | FastAPI 后端 API | `server.py` 启动参数 |
| 8080 | MCP Server | `sys_config.json` → `mcp.server.port` |

> 两个端口都只监听 `127.0.0.1`，不对外暴露。

---

## 故障排除

### 应用无法启动

| 可能原因 | 解决方案 |
|----------|----------|
| Python 未安装或版本低于 3.10 | 安装 Python 3.10+，或确保嵌入式 Python 完整 |
| Node.js 版本过低 | 升级到 18.0.0+ |
| 依赖未安装 | 执行 `pip install -r requirements.txt` 和 `npm install` |
| 端口被占用 | 检查 8000/8080 端口，使用 `netstat -ano | findstr :8000` |

### API 连接失败

| 可能原因 | 解决方案 |
|----------|----------|
| 后端未启动 | 检查控制台是否有 Python 进程 |
| 端口被防火墙拦截 | 允许本地回环连接 |
| CORS 问题 | 检查请求来源是否在白名单中 |

### 任务执行失败

| 可能原因 | 解决方案 |
|----------|----------|
| API Key 未配置 | 在设置页面或 `user_config.json` 配置 |
| API Key 过期/无效 | 联系 AI 服务提供商更新 Key |
| 网络超时 | 检查网络连通性，API 默认超时 180 秒 |
| 工作目录不存在 | 确保配置的工作目录路径有效 |
| MCP Server 未启动 | 引擎会自动降级到 Local Fallback 模式 |

### 打包失败

| 可能原因 | 解决方案 |
|----------|----------|
| electron-builder 未安装 | 执行 `npm install` |
| Python 嵌入式包未准备 | 执行 `node scripts/setup-python.js` |
| 图标文件缺失 | 确保 `assets/logo.ico` 存在 |
| 磁盘空间不足 | 完整打包约需 500MB+ 空间 |

### Windows 特有问题

| 问题 | 说明 |
|------|------|
| 中文乱码 | 配置模块已处理 Windows 控制台编码（`safe_print`） |
| 路径过长 | Windows 默认 260 字符限制，可启用长路径支持 |
| ProactorEventLoop bug | 引擎在子线程中自动使用 SelectorEventLoop |
| 管理员权限 | 部分系统目录操作需要以管理员身份运行 |
