"""
WebSocket 连接管理器
====================

管理 WebSocket 连接生命周期、订阅和消息广播。

主要职责：
1. 连接生命周期管理（connect/disconnect/heartbeat）
2. 订阅管理（subscribe_task / unsubscribe_task）
3. 事件路由（根据事件类型分发到不同处理器）
4. 批量异步广播（asyncio.gather）
5. 连接状态追踪（在线用户数、连接时长）
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set
import logging

# 添加项目根目录到路径以便导入 config
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import WebSocket, WebSocketDisconnect

from src.tasks.config import get_logger

logger = get_logger(__name__)


@dataclass
class WebSocketConnection:
    """WebSocket 连接信息"""
    id: str                                      # 连接ID
    websocket: WebSocket                         # WebSocket 对象
    client_id: Optional[str] = None              # 客户端ID（可选）
    connected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_ping: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    subscribed_tasks: Set[str] = field(default_factory=set)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_alive(self) -> bool:
        """检查连接是否存活（基于最后心跳时间）"""
        try:
            last = datetime.fromisoformat(self.last_ping.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            # 5分钟无心跳视为死连接
            return (now - last).total_seconds() < 300
        except Exception:
            return False


class WebSocketManager:
    """
    WebSocket 连接管理器

    功能：
    - 管理所有 WebSocket 连接
    - 支持任务级别的订阅
    - 批量异步广播
    - 心跳检测和死连接清理
    - 连接数量限制保护
    """
    
    # 连接限制配置
    MAX_TOTAL_CONNECTIONS = 100  # 最大总连接数
    MAX_CONNECTIONS_PER_CLIENT = 5  # 每个客户端最大连接数
    MAX_SUBSCRIPTIONS_PER_CONNECTION = 20  # 每个连接最大订阅数

    def __init__(
        self,
        heartbeat_interval: int = 30,
        heartbeat_timeout: int = 300,
        max_connections: int = None,
    ):
        """
        初始化 WebSocket 管理器

        Args:
            heartbeat_interval: 心跳间隔（秒）
            heartbeat_timeout: 心跳超时时间（秒）
            max_connections: 最大连接数（可选，默认使用类常量）
        """
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_timeout = heartbeat_timeout
        self.max_connections = max_connections or self.MAX_TOTAL_CONNECTIONS

        # 连接管理
        self._connections: Dict[str, WebSocketConnection] = {}
        self._task_subscriptions: Dict[str, Set[str]] = {}  # task_id -> {conn_ids}

        # 全局消息序号
        self._message_seq = 0

        # 心跳任务
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

        # 事件处理器
        self._event_handlers: Dict[str, Callable] = {}

    # ==================== 连接管理 ====================

    def _count_client_connections(self, client_id: str) -> int:
        """统计指定客户端的连接数"""
        if not client_id:
            return 0
        return sum(1 for c in self._connections.values() if c.client_id == client_id)

    async def connect(
        self,
        websocket: WebSocket,
        client_id: Optional[str] = None
    ) -> Optional[str]:
        """
        处理新连接

        Args:
            websocket: WebSocket 对象
            client_id: 客户端ID（可选）

        Returns:
            连接ID，如果拒绝连接则返回 None
        """
        # 检查总连接数限制
        if len(self._connections) >= self.max_connections:
            logger.warning(f"拒绝连接：已达最大连接数 {self.max_connections}")
            await websocket.accept()
            await websocket.send_json({
                "event": "error",
                "data": {
                    "code": "MAX_CONNECTIONS_EXCEEDED",
                    "message": f"服务器连接数已满 ({self.max_connections})，请稍后重试"
                }
            })
            await websocket.close(code=1013, reason="Max connections exceeded")
            return None
        
        # 检查单客户端连接数限制
        if client_id:
            client_conn_count = self._count_client_connections(client_id)
            if client_conn_count >= self.MAX_CONNECTIONS_PER_CLIENT:
                logger.warning(f"拒绝连接：客户端 {client_id} 已达最大连接数 {self.MAX_CONNECTIONS_PER_CLIENT}")
                await websocket.accept()
                await websocket.send_json({
                    "event": "error",
                    "data": {
                        "code": "MAX_CLIENT_CONNECTIONS_EXCEEDED",
                        "message": f"该客户端连接数已满 ({self.MAX_CONNECTIONS_PER_CLIENT})"
                    }
                })
                await websocket.close(code=1013, reason="Max client connections exceeded")
                return None

        await websocket.accept()

        conn_id = f"conn_{uuid.uuid4().hex[:12]}"
        connection = WebSocketConnection(
            id=conn_id,
            websocket=websocket,
            client_id=client_id
        )

        self._connections[conn_id] = connection

        logger.info(f"WebSocket 连接建立: {conn_id} (client: {client_id}, 当前总数: {len(self._connections)})")

        # 发送连接确认
        await self._send_to_connection(conn_id, {
            "event": "connected",
            "data": {
                "connection_id": conn_id,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        })

        return conn_id

    async def disconnect(self, conn_id: str) -> None:
        """
        处理断开连接

        Args:
            conn_id: 连接ID
        """
        if conn_id not in self._connections:
            return

        connection = self._connections[conn_id]

        # 取消所有订阅
        for task_id in list(connection.subscribed_tasks):
            await self.unsubscribe(conn_id, task_id)

        # 移除连接
        del self._connections[conn_id]

        logger.info(f"WebSocket 连接断开: {conn_id}")

    def get_connection(self, conn_id: str) -> Optional[WebSocketConnection]:
        """获取连接信息"""
        return self._connections.get(conn_id)

    def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self._connections)

    def get_all_connections(self) -> List[WebSocketConnection]:
        """获取所有连接"""
        return list(self._connections.values())

    # ==================== 订阅管理 ====================

    async def subscribe(self, conn_id: str, task_id: str) -> bool:
        """
        订阅任务更新

        Args:
            conn_id: 连接ID
            task_id: 任务ID

        Returns:
            是否成功
        """
        if conn_id not in self._connections:
            return False

        connection = self._connections[conn_id]
        
        # 检查订阅数量限制
        if len(connection.subscribed_tasks) >= self.MAX_SUBSCRIPTIONS_PER_CONNECTION:
            logger.warning(f"连接 {conn_id} 订阅数已达上限 {self.MAX_SUBSCRIPTIONS_PER_CONNECTION}")
            await self._send_to_connection(conn_id, {
                "event": "error",
                "data": {
                    "code": "MAX_SUBSCRIPTIONS_EXCEEDED",
                    "message": f"订阅数量已达上限 ({self.MAX_SUBSCRIPTIONS_PER_CONNECTION})",
                    "task_id": task_id
                }
            })
            return False
        
        connection.subscribed_tasks.add(task_id)

        if task_id not in self._task_subscriptions:
            self._task_subscriptions[task_id] = set()
        self._task_subscriptions[task_id].add(conn_id)

        logger.debug(f"连接 {conn_id} 订阅任务 {task_id} (当前订阅数: {len(connection.subscribed_tasks)})")

        # 发送订阅确认
        await self._send_to_connection(conn_id, {
            "event": "subscribed",
            "data": {"task_id": task_id}
        })

        return True

    async def unsubscribe(self, conn_id: str, task_id: str) -> bool:
        """
        取消订阅任务

        Args:
            conn_id: 连接ID
            task_id: 任务ID

        Returns:
            是否成功
        """
        if conn_id not in self._connections:
            return False

        connection = self._connections[conn_id]
        connection.subscribed_tasks.discard(task_id)

        if task_id in self._task_subscriptions:
            self._task_subscriptions[task_id].discard(conn_id)
            if not self._task_subscriptions[task_id]:
                del self._task_subscriptions[task_id]

        logger.debug(f"连接 {conn_id} 取消订阅任务 {task_id}")
        return True

    def get_task_subscribers(self, task_id: str) -> Set[str]:
        """获取订阅某任务的所有连接ID"""
        return self._task_subscriptions.get(task_id, set()).copy()

    # ==================== 消息发送 ====================

    async def _send_to_connection(
        self,
        conn_id: str,
        message: Dict[str, Any]
    ) -> bool:
        """
        发送消息到指定连接

        Args:
            conn_id: 连接ID
            message: 消息内容

        Returns:
            是否成功
        """
        if conn_id not in self._connections:
            return False

        connection = self._connections[conn_id]

        try:
            # 添加序号和时间戳
            self._message_seq += 1
            message["seq"] = self._message_seq
            if "timestamp" not in message:
                message["timestamp"] = datetime.now(timezone.utc).isoformat()

            await connection.websocket.send_json(message)
            return True

        except Exception as e:
            logger.warning(f"发送消息失败: {conn_id} - {e}")
            return False

    async def send(
        self,
        conn_id: str,
        event: str,
        data: Any,
        task_id: Optional[str] = None
    ) -> bool:
        """
        发送事件到指定连接

        Args:
            conn_id: 连接ID
            event: 事件类型
            data: 事件数据
            task_id: 关联的任务ID（可选）

        Returns:
            是否成功
        """
        message = {
            "event": event,
            "data": data
        }
        if task_id:
            message["task_id"] = task_id

        return await self._send_to_connection(conn_id, message)

    async def broadcast(
        self,
        task_id: str,
        event: str,
        data: Any
    ) -> int:
        """
        广播事件到订阅某任务的所有连接

        Args:
            task_id: 任务ID
            event: 事件类型
            data: 事件数据

        Returns:
            成功发送的连接数
        """
        conn_ids = self.get_task_subscribers(task_id)
        if not conn_ids:
            return 0

        message = {
            "event": event,
            "task_id": task_id,
            "data": data
        }

        # 并行发送
        results = await asyncio.gather(
            *[self._send_to_connection(cid, message.copy()) for cid in conn_ids],
            return_exceptions=True
        )

        # 统计成功数
        success_count = sum(1 for r in results if r is True)

        # 清理失败的连接
        dead_conns = [
            cid for cid, result in zip(conn_ids, results)
            if result is not True
        ]
        for cid in dead_conns:
            await self.disconnect(cid)

        return success_count

    async def broadcast_global(self, event: str, data: Any) -> int:
        """
        广播事件到所有连接

        Args:
            event: 事件类型
            data: 事件数据

        Returns:
            成功发送的连接数
        """
        if not self._connections:
            return 0

        message = {
            "event": event,
            "data": data
        }

        conn_ids = list(self._connections.keys())

        results = await asyncio.gather(
            *[self._send_to_connection(cid, message.copy()) for cid in conn_ids],
            return_exceptions=True
        )

        success_count = sum(1 for r in results if r is True)

        # 清理失败的连接
        dead_conns = [
            cid for cid, result in zip(conn_ids, results)
            if result is not True
        ]
        for cid in dead_conns:
            await self.disconnect(cid)

        return success_count

    # ==================== 专用事件广播 ====================

    async def broadcast_task_status(self, task_id: str, task_data: Dict[str, Any]) -> int:
        """广播任务状态更新"""
        return await self.broadcast(task_id, "task_status", {"task": task_data})

    async def broadcast_tool_start(
        self,
        task_id: str,
        tool: str,
        args: Dict[str, Any],
        call_id: str
    ) -> int:
        """广播工具开始执行"""
        return await self.broadcast(task_id, "tool_start", {
            "tool": tool,
            "args": args,
            "call_id": call_id
        })

    async def broadcast_tool_end(
        self,
        task_id: str,
        tool: str,
        result: Dict[str, Any],
        call_id: str,
        duration_ms: float
    ) -> int:
        """广播工具执行完成"""
        return await self.broadcast(task_id, "tool_end", {
            "tool": tool,
            "result": result,
            "call_id": call_id,
            "duration_ms": duration_ms
        })

    async def broadcast_tool_error(
        self,
        task_id: str,
        tool: str,
        error: str,
        call_id: str
    ) -> int:
        """广播工具执行失败"""
        return await self.broadcast(task_id, "tool_error", {
            "tool": tool,
            "error": error,
            "call_id": call_id
        })

    async def broadcast_ai_thinking(
        self,
        task_id: str,
        content: str,
        partial: bool = True
    ) -> int:
        """广播 AI 思考内容"""
        return await self.broadcast(task_id, "ai_thinking", {
            "content": content,
            "partial": partial
        })

    async def broadcast_progress(
        self,
        task_id: str,
        progress: int,
        current_step: str
    ) -> int:
        """广播进度更新"""
        return await self.broadcast(task_id, "progress", {
            "progress": progress,
            "current_step": current_step
        })

    async def broadcast_output(
        self,
        task_id: str,
        stream: str,
        data: str
    ) -> int:
        """广播实时输出"""
        return await self.broadcast(task_id, "output", {
            "stream": stream,
            "data": data
        })

    # ==================== 心跳管理 ====================

    async def handle_ping(self, conn_id: str) -> None:
        """
        处理客户端 ping

        Args:
            conn_id: 连接ID
        """
        if conn_id in self._connections:
            self._connections[conn_id].last_ping = datetime.now(timezone.utc).isoformat()
            await self._send_to_connection(conn_id, {
                "event": "pong",
                "data": {"timestamp": datetime.now(timezone.utc).isoformat()}
            })

    async def _heartbeat_loop(self) -> None:
        """心跳检测循环"""
        while self._running:
            try:
                await asyncio.sleep(self.heartbeat_interval)

                # 检查死连接
                now = datetime.now(timezone.utc)
                dead_conns = []

                for conn_id, conn in self._connections.items():
                    try:
                        last_ping = datetime.fromisoformat(
                            conn.last_ping.replace('Z', '+00:00')
                        )
                        if (now - last_ping).total_seconds() > self.heartbeat_timeout:
                            dead_conns.append(conn_id)
                    except Exception:
                        dead_conns.append(conn_id)

                # 清理死连接
                for conn_id in dead_conns:
                    logger.info(f"心跳超时，断开连接: {conn_id}")
                    await self.disconnect(conn_id)

                # 向所有存活连接发送 ping
                await self.broadcast_global("ping", {
                    "timestamp": now.isoformat()
                })

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳循环异常: {e}")

    def start_heartbeat(self) -> None:
        """启动心跳任务"""
        if self._running:
            return

        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("心跳任务已启动")

    async def stop_heartbeat(self) -> None:
        """停止心跳任务"""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        logger.info("心跳任务已停止")

    # ==================== 消息处理 ====================

    def register_handler(self, event_type: str, handler: Callable) -> None:
        """注册事件处理器"""
        self._event_handlers[event_type] = handler

    async def handle_message(
        self,
        conn_id: str,
        message: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        处理客户端消息

        Args:
            conn_id: 连接ID
            message: 消息内容

        Returns:
            响应消息（可选）
        """
        msg_type = message.get("type")

        if msg_type == "ping":
            await self.handle_ping(conn_id)
            return None

        elif msg_type == "subscribe":
            task_id = message.get("task_id")
            if task_id:
                await self.subscribe(conn_id, task_id)
            return None

        elif msg_type == "unsubscribe":
            task_id = message.get("task_id")
            if task_id:
                await self.unsubscribe(conn_id, task_id)
            return None

        elif msg_type in self._event_handlers:
            handler = self._event_handlers[msg_type]
            try:
                return await handler(conn_id, message)
            except Exception as e:
                logger.error(f"事件处理失败: {msg_type} - {e}")
                return {"error": str(e)}

        else:
            logger.warning(f"未知消息类型: {msg_type}")
            return None

    # ==================== WebSocket 端点辅助 ====================

    async def websocket_endpoint(
        self,
        websocket: WebSocket,
        client_id: Optional[str] = None
    ) -> None:
        """
        WebSocket 端点处理（完整生命周期）

        Args:
            websocket: WebSocket 对象
            client_id: 客户端ID
        """
        conn_id = await self.connect(websocket, client_id)

        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    response = await self.handle_message(conn_id, data)
                    if response:
                        await self._send_to_connection(conn_id, response)

                except WebSocketDisconnect:
                    break
                except json.JSONDecodeError:
                    await self._send_to_connection(conn_id, {
                        "event": "error",
                        "data": {"message": "无效的 JSON 格式"}
                    })

        except Exception as e:
            logger.error(f"WebSocket 端点异常: {conn_id} - {e}")

        finally:
            await self.disconnect(conn_id)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "total_connections": len(self._connections),
            "active_subscriptions": sum(len(s) for s in self._task_subscriptions.values()),
            "subscribed_tasks": len(self._task_subscriptions),
            "message_seq": self._message_seq
        }


# 全局实例
_ws_manager: Optional[WebSocketManager] = None


def get_ws_manager() -> WebSocketManager:
    """获取全局 WebSocket 管理器"""
    global _ws_manager
    if _ws_manager is None:
        _ws_manager = WebSocketManager()
    return _ws_manager
