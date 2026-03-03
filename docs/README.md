# AutomateX 文档中心

本目录包含 AutomateX 项目的完整技术文档。

## 文档索引

| 文档 | 说明 | 适合谁 |
|------|------|--------|
| [快速入门](getting-started.md) | 安装、配置、首次运行 | 新用户 |
| [API 接口文档](api-reference.md) | REST API、WebSocket、Python API 完整参考 | 开发者 / 集成方 |
| [系统架构](architecture.md) | V3 引擎、MCP Server、安全机制、缓存策略 | 开发者 / 贡献者 |
| [部署与打包](deployment.md) | Electron 打包、生产部署、故障排除 | 运维 / 发布者 |

## 架构图

| 文件 | 说明 |
|------|------|
| [message_flow.drawio](message_flow.drawio) | 消息保存与流转流程图 |
| [../.dev/core.drawio](../.dev/core.drawio) | 核心架构设计图（开发用） |

> 使用 [draw.io](https://app.diagrams.net/) 或 VS Code 的 Draw.io 插件打开 `.drawio` 文件。

## 项目概述

**AutomateX** 是一个 AI 驱动的 Windows 任务自动化引擎，核心特性：

- **两阶段工具调用（V3）** — 先选工具再填参数，降低约 90% Token 消耗
- **MCP Server** — 安全隔离的工具执行环境（路径验证 + 命令过滤 + 文件锁）
- **Electron 桌面应用** — 图形化任务管理，WebSocket 实时推送
- **多模型支持** — Kimi、DeepSeek、Qwen 等 OpenAI 兼容 API

## 技术栈

| 层 | 技术 |
|----|------|
| 桌面框架 | Electron 28 |
| 前端 | 原生 HTML + CSS + JS（无框架依赖） |
| 后端 API | FastAPI + Uvicorn |
| 实时通信 | WebSocket（心跳 + 订阅 + 批量广播） |
| AI 引擎 | OpenAI 兼容 API（流式响应） |
| 工具服务 | MCP JSON-RPC 2.0（TCP / stdio） |
| 语言 | Python 3.10+, Node.js 18+ |
