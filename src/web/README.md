# AutomateX Web UI

AutomateX 的图形化管理界面，基于 Electron + HTML 构建。

## 功能特性

- 📊 **仪表盘** - 查看任务统计、快速操作
- 📋 **任务列表** - 筛选、搜索、排序任务
- 📝 **任务详情** - 查看执行进度、日志、提供输入
- ➕ **创建任务** - 填写任务信息快速创建
- 🎨 **主题切换** - 支持深色/浅色主题
- 🔔 **实时通知** - WebSocket 推送任务状态更新

## 项目结构

```
web/
├── index.html        # 主界面（单页应用）
├── main.js           # Electron 主进程
├── preload.js        # 预加载脚本（安全桥接）
├── server.py         # FastAPI 后端 API 服务（1063 行）
├── ws_manager.py     # WebSocket 连接管理（订阅、心跳、广播）
├── package.json      # 项目配置（Electron 28）
├── assets/           # 静态资源
│   ├── logo.ico      # 应用图标
│   └── logo.svg      # 矢量图标
├── scripts/          # 构建脚本
│   ├── build-protected.js   # bytenode 编译保护
│   ├── compile-js.js        # JS 编译
│   ├── generate-icon.js     # 图标生成
│   └── setup-python.js      # 嵌入式 Python 配置
├── build/            # 嵌入式 Python 运行时
│   ├── get-pip.py
│   └── python-embed/ # Python 3.10 嵌入式发行版
└── README.md         # 本文档
```

## 环境要求

- **Node.js** >= 18.0.0
- **Python** >= 3.10
- **依赖包**:
  - `fastapi`
  - `uvicorn`
  - `pydantic`

## 快速开始

### 1. 安装 Python 依赖

```bash
pip install fastapi uvicorn pydantic
```

### 2. 安装 Node.js 依赖

```bash
cd src/web
npm install
```

### 3. 启动应用

```bash
npm start
```

应用会自动启动后端 API 服务和 Electron 窗口。

## 开发模式

```bash
npm run dev
```

开发模式会启用调试工具。

## 单独启动后端服务

如果只需要 API 服务（用于调试或其他前端）：

```bash
python server.py --port 8765
```

API 文档地址: http://localhost:8765/docs

## API 接口

### 任务管理

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/tasks` | 获取任务列表 |
| POST | `/api/tasks` | 创建新任务 |
| GET | `/api/tasks/:id` | 获取任务详情 |
| PUT | `/api/tasks/:id` | 更新任务 |
| DELETE | `/api/tasks/:id` | 删除任务 |

### 任务执行

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | `/api/tasks/:id/run` | 开始执行 |
| POST | `/api/tasks/:id/stop` | 停止执行 |
| POST | `/api/tasks/:id/pause` | 暂停任务 |
| POST | `/api/tasks/:id/resume` | 恢复执行 |
| POST | `/api/tasks/:id/cancel` | 取消任务 |
| POST | `/api/tasks/:id/input` | 提交用户输入 |
| POST | `/api/tasks/:id/append` | 追加任务 |
| POST | `/api/tasks/:id/retry` | 重试失败任务 |

### 消息与统计

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/api/tasks/:id/messages` | 获取消息历史 |
| GET | `/api/stats` | 获取统计信息 |
| GET | `/api/settings` | 获取系统设置 |
| PUT | `/api/settings` | 更新系统设置 |

### WebSocket

| 路径 | 描述 |
|------|------|
| `/ws/tasks/:id` | 订阅特定任务更新 |
| `/ws/global` | 订阅所有任务更新 |

## 界面截图

### 仪表盘
- 显示任务统计卡片（总数、执行中、已完成、失败）
- 快速操作按钮
- 最近任务列表

### 任务列表
- 支持按状态、标签筛选
- 支持关键词搜索
- 支持多字段排序
- 状态徽章颜色区分

### 任务详情
- 进度条显示执行进度
- 实时日志输出
- 输入面板（等待输入时显示）
- 操作按钮（暂停/继续/取消等）

### 创建任务
- 任务名称、描述输入
- 优先级选择
- 工作目录设置
- 标签管理

## 打包发布

```bash
# Windows 安装包
npm run build:win

# 仅打包不生成安装程序
npm run pack
```

输出目录: `web/dist/`

## 技术栈

- **前端**: 原生 HTML + CSS + JavaScript（无框架依赖）
- **桌面框架**: Electron 28
- **后端**: FastAPI + Uvicorn
- **通信**: RESTful API + WebSocket

## 注意事项

1. 首次使用需要配置 AI API（设置页面或编辑 `src/config/user_config.json`）
2. 确保 API Key 和 Base URL 配置正确
3. 任务执行需要相应的系统权限
4. WebSocket 连接会自动重连（心跳检测 + 自动重连机制）

## 故障排除

### 应用无法启动
- 检查 Python 和 Node.js 版本
- 确保依赖已正确安装
- 查看控制台错误信息

### API 连接失败
- 检查端口 8765 是否被占用
- 确保防火墙允许本地连接
- 尝试手动启动 `python server.py`

### 任务执行失败
- 检查 API 配置是否正确
- 查看任务日志获取详细错误
- 确保工作目录存在且可访问
