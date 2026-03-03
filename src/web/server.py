# -*- coding: utf-8 -*-
"""
AutomateX Web UI - Backend API Server (V3)
提供RESTful API和WebSocket支持，连接前端UI与tasks模块
使用 TaskEngine V3 实现两阶段工具调用
"""

import asyncio
import json
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# 添加路径：项目根目录 + 当前web目录
CURRENT_DIR = Path(__file__).parent
PROJECT_ROOT = CURRENT_DIR.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(CURRENT_DIR))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import uvicorn

# 导入tasks模块 (V3)
from src.tasks.models import Task, TaskStatus
from src.tasks.store import TaskStore
from src.tasks.engine import TaskEngine, EngineConfig
from src.tasks.config import resolve_working_directory, get_default_working_directory, setup_logging

# 导入统一配置系统
from src.config import config

# 导入WebSocket管理器
from ws_manager import WebSocketManager, get_ws_manager

# 初始化日志系统
setup_logging()


# ============== EngineManager 类 ==============

class EngineManager:
    """
    任务引擎管理器
    
    线程安全地管理任务引擎的生命周期，防止竞态条件。
    """
    
    def __init__(self):
        from typing import Set
        self._engines: Dict[str, TaskEngine] = {}
        self._lock = threading.RLock()
        self._pending_stops: Set[str] = set()
        self._pending_starts: Set[str] = set()
    
    def get(self, task_id: str) -> Optional[TaskEngine]:
        """获取引擎"""
        with self._lock:
            return self._engines.get(task_id)
    
    def contains(self, task_id: str) -> bool:
        """检查是否存在"""
        with self._lock:
            return task_id in self._engines
    
    def is_stopping(self, task_id: str) -> bool:
        """检查是否正在停止"""
        with self._lock:
            return task_id in self._pending_stops
    
    def is_starting(self, task_id: str) -> bool:
        """检查是否正在启动"""
        with self._lock:
            return task_id in self._pending_starts
    
    def register(self, task_id: str, engine: TaskEngine) -> bool:
        """
        注册引擎
        
        Returns:
            是否成功注册（如果已存在或正在操作则返回 False）
        """
        with self._lock:
            if task_id in self._engines:
                return False
            if task_id in self._pending_stops or task_id in self._pending_starts:
                return False
            self._engines[task_id] = engine
        return True
    
    def unregister(self, task_id: str) -> Optional[TaskEngine]:
        """
        注销引擎
        
        Returns:
            被注销的引擎，如果不存在则返回 None
        """
        with self._lock:
            return self._engines.pop(task_id, None)
    
    def stop_and_unregister(self, task_id: str) -> bool:
        """
        停止并注销引擎（线程安全）
        
        Returns:
            是否成功停止
        """
        with self._lock:
            if task_id in self._pending_stops:
                return False  # 已在停止中
            if task_id not in self._engines:
                return False  # 不存在
            self._pending_stops.add(task_id)
            engine = self._engines.get(task_id)
        
        try:
            if engine:
                engine.stop()
            with self._lock:
                self._engines.pop(task_id, None)
            return True
        finally:
            with self._lock:
                self._pending_stops.discard(task_id)
    
    def get_active_count(self) -> int:
        """获取活跃引擎数量"""
        with self._lock:
            return len(self._engines)
    
    def get_all_ids(self) -> List[str]:
        """获取所有任务ID"""
        with self._lock:
            return list(self._engines.keys())


# ============== Lifespan Event Handler ==============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # Startup
    wsm = get_ws_manager_instance()
    wsm.start_heartbeat()
    print("✅ WebSocket 心跳任务已启动")
    
    yield
    
    # Shutdown
    wsm = get_ws_manager_instance()
    await wsm.stop_heartbeat()
    print("✅ WebSocket 心跳任务已停止")


# ============== Pydantic Models ==============

class CreateTaskRequest(BaseModel):
    """创建任务请求"""
    description: str = Field(default="", min_length=0)
    working_directory: Optional[str] = None
    todo_items: Optional[List[str]] = None  # TODO 清单内容列表


class UpdateTaskRequest(BaseModel):
    """更新任务请求"""
    description: Optional[str] = None
    working_directory: Optional[str] = None


class UserInputRequest(BaseModel):
    """用户输入请求"""
    input_text: str


class AppendTaskRequest(BaseModel):
    """追加任务请求"""
    additional_description: str = Field(..., min_length=1)


class TaskRunOptions(BaseModel):
    """任务运行选项"""
    auto_mode: bool = Field(default=True, description="是否自动模式运行")


class UpdateDescriptionRequest(BaseModel):
    """更新任务描述请求"""
    description: str = Field(..., min_length=1)


# ============== Application Setup ==============

app = FastAPI(
    title="AutomateX API",
    description="AutomateX Windows任务自动化引擎API (V3)",
    version="3.0.0",
    lifespan=lifespan
)

# CORS配置 - 仅允许本地访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "file://",  # Electron file:// 协议
    ],
    allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1):\d+$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局状态
store: Optional[TaskStore] = None
engine_manager = EngineManager()  # 线程安全的任务引擎管理
mcp_host: str = "127.0.0.1"  # MCP Server 主机
mcp_port: int = 8080  # MCP Server 端口
ws_manager: Optional[WebSocketManager] = None  # WebSocket 管理器


def get_store() -> TaskStore:
    """获取任务存储实例"""
    global store
    if store is None:
        store_path = PROJECT_ROOT / "src" / "tasks" / "store.json"
        store = TaskStore(store_path)
    return store


def get_ws_manager_instance() -> WebSocketManager:
    """获取 WebSocket 管理器实例"""
    global ws_manager
    if ws_manager is None:
        ws_manager = get_ws_manager()
    return ws_manager


def get_api():
    """获取 AI API 实例"""
    from src.tasks.chat import get_api
    return get_api()


def create_engine(task: Task, store: TaskStore, main_loop=None) -> TaskEngine:
    """创建任务引擎"""
    api = get_api()
    config = EngineConfig(
        max_history=20,
        max_iterations=50,
        mcp_host=mcp_host,
        mcp_port=mcp_port,
        use_mcp=True,
    )
    
    wsm = get_ws_manager_instance()
    
    # 使用外部传入的主线程事件循环（子线程中无法通过 get_running_loop 获取）
    if main_loop is None:
        try:
            main_loop = asyncio.get_running_loop()
        except RuntimeError:
            main_loop = None
    
    # 输出回调 - 仅打印日志，不阻塞等待 WebSocket
    def on_output(msg: str):
        print(f"[Engine] {msg}")
    
    # AI 思考内容回调 - 广播到 WebSocket（非阻塞）
    def on_thinking(content: str):
        if main_loop is None or main_loop.is_closed():
            return
        try:
            normalized = normalize_prefixes(content)
            asyncio.run_coroutine_threadsafe(
                wsm.broadcast_ai_thinking(task.id, normalized, partial=True),
                main_loop
            )
            # 不等待结果，避免阻塞引擎线程
        except Exception:
            pass
    
    # 工具开始执行回调
    def on_tool_start(task_id, tool, args, call_id):
        if main_loop is None or main_loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                wsm.broadcast_tool_start(task_id, tool, args, call_id),
                main_loop
            )
        except Exception:
            pass
    
    # 工具执行完成回调
    def on_tool_end(task_id, tool, result, call_id, duration_ms):
        if main_loop is None or main_loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                wsm.broadcast_tool_end(task_id, tool, result, call_id, duration_ms),
                main_loop
            )
        except Exception:
            pass
    
    return TaskEngine(
        api=api,
        store=store,
        config=config,
        on_output=on_output,
        on_thinking=on_thinking,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
    )


# ============== Helper Functions ==============

def normalize_prefixes(text: str) -> str:
    """去掉日志/思考内容的固定前缀和 emoji 符号。"""
    if not text:
        return ""
    import re
    cleaned = text.strip()
    # 去掉开头的 emoji 符号
    cleaned = re.sub(r'^[✅❌❓⚠️🔧📋📂📝🤖💡🔄🎯✨🚀⛔🔒🗑📦🔍📁🔎]\s*', '', cleaned)
    for prefix in ("任务完成:", "完成:", "任务:"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
    return cleaned

def task_to_dict(task: Task) -> Dict[str, Any]:
    """将Task对象转换为可JSON序列化的字典"""
    return task.to_dict()


def run_task_in_thread(task_id: str, task: Task, store: TaskStore, main_loop=None):
    """在后台线程中运行任务"""
    try:
        engine = create_engine(task, store, main_loop=main_loop)
    except ValueError as e:
        # API 未配置等启动前错误 → 直接标记任务失败并通知前端
        error_msg = str(e)
        print(f"[Engine] 任务 {task_id} 启动失败: {error_msg}")
        task.update_status(TaskStatus.FAILED, force=True)
        task.error_message = error_msg
        task.current_step = error_msg
        store.update_task(task)
        store.add_message(task_id, "assistant", error_msg, "execution")
        if main_loop and not main_loop.is_closed():
            try:
                wsm = get_ws_manager_instance()
                asyncio.run_coroutine_threadsafe(
                    wsm.broadcast_task_status(task_id, task_to_dict(task)),
                    main_loop
                )
            except Exception:
                pass
        return

    if not engine_manager.register(task_id, engine):
        return
    
    result = None
    try:
        # Windows ProactorEventLoop 有管道写入竞争 bug，
        # 在子线程中使用 SelectorEventLoop 更稳定
        if sys.platform == "win32":
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(engine.run(task))
            finally:
                loop.close()
        else:
            result = asyncio.run(engine.run(task))
        print(f"Task {task_id} finished: {result.status.value}")
    except Exception as e:
        print(f"Task {task_id} error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        engine_manager.unregister(task_id)
        # 任务结束后广播最终状态，让前端即时更新（不阻塞等待）
        final_task = result or store.get_task(task_id)
        if final_task and main_loop and not main_loop.is_closed():
            try:
                wsm = get_ws_manager_instance()
                asyncio.run_coroutine_threadsafe(
                    wsm.broadcast_task_status(task_id, task_to_dict(final_task)),
                    main_loop
                )
            except Exception as e:
                print(f"[WS] 广播任务最终状态失败: {e}")


# ============== REST API Endpoints ==============

@app.get("/")
async def root():
    """API根路径"""
    return {
        "name": "AutomateX API",
        "version": "3.0.0",
        "status": "running",
        "engine": "TaskEngine V3"
    }


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


# ---------- 任务CRUD ----------

@app.get("/api/tasks")
async def list_tasks(
    status: Optional[str] = Query(None, description="按状态筛选"),
    search: Optional[str] = Query(None, description="搜索关键词"),
    sort_by: str = Query("created_at", description="排序字段"),
    sort_order: str = Query("desc", description="排序方向: asc/desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
):
    """获取任务列表"""
    s = get_store()
    tasks = s.list_tasks()
    
    # 筛选
    if status:
        try:
            status_enum = TaskStatus(status)
            tasks = [t for t in tasks if t.status == status_enum]
        except ValueError:
            pass
    
    if search:
        search_lower = search.lower()
        tasks = [t for t in tasks if search_lower in t.description.lower()]
    
    # 排序
    reverse = sort_order.lower() == "desc"
    if sort_by == "created_at":
        tasks.sort(key=lambda t: t.created_at or "", reverse=reverse)
    elif sort_by == "updated_at":
        tasks.sort(key=lambda t: t.updated_at or "", reverse=reverse)
    elif sort_by == "status":
        tasks.sort(key=lambda t: t.status.value, reverse=reverse)
    
    # 分页
    total = len(tasks)
    tasks = tasks[offset:offset + limit]
    
    return {
        "tasks": [task_to_dict(t) for t in tasks],
        "total": total,
        "limit": limit,
        "offset": offset
    }


@app.post("/api/tasks")
async def create_task(request: CreateTaskRequest):
    """创建新任务"""
    s = get_store()
    
    # 用 TODO 第一项作为 description（如果没有单独指定）
    desc = request.description.strip()
    todos = request.todo_items or []
    if not desc and todos:
        desc = todos[0]  # 默认用第一个 TODO 作为描述
    if not desc:
        raise HTTPException(status_code=400, detail="描述或 TODO 不能为空")
    
    # 使用 store 的 create_task 方法
    task = s.create_task(
        description=desc,
        working_directory=str(resolve_working_directory(request.working_directory)),
    )
    
    # 添加 TODO 项
    import uuid as _uuid
    for content in todos:
        content = content.strip()
        if content:
            from src.tasks.models import TodoItem
            task.todo_items.append(TodoItem(
                id=_uuid.uuid4().hex[:8],
                content=content,
            ))
    if task.todo_items:
        s.update_task(task)
    
    return {
        "success": True,
        "task": task_to_dict(task)
    }


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return {"task": task_to_dict(task)}


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: str, request: UpdateTaskRequest):
    """更新任务"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 只能更新等待状态的任务
    if task.status != TaskStatus.WAITING:
        raise HTTPException(status_code=400, detail="只能更新等待状态的任务")
    
    if request.description is not None:
        task.description = request.description
    if request.working_directory is not None:
        task.working_directory = request.working_directory
    
    task.updated_at = datetime.now().isoformat()
    s.update_task(task)
    
    return {"success": True, "task": task_to_dict(task)}


@app.put("/api/tasks/{task_id}/description")
async def update_task_description(task_id: str, request: UpdateDescriptionRequest):
    """更新任务的描述"""
    try:
        s = get_store()
        task = s.get_task(task_id)
        
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        
        task.description = request.description
        task.updated_at = datetime.now().isoformat()
        
        # 检查是否是已结束的任务，需要重新启动
        needs_restart = task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
        
        if needs_restart:
            # 重置任务状态
            task.progress = 0
            task.error_message = None
            task.current_step = ""
            task.update_status(TaskStatus.WAITING, force=True)
        
        s.update_task(task)
        
        # 通过 WebSocket 广播更新
        await get_ws_manager().broadcast_task_status(task_id, task_to_dict(task))
        
        # 如果需要重启，自动启动任务
        if needs_restart:
            _loop = asyncio.get_running_loop()
            thread = threading.Thread(
                target=run_task_in_thread, 
                args=(task_id, task, s, _loop), 
                daemon=True
            )
            thread.start()
            
            return {
                "success": True, 
                "task": task_to_dict(task),
                "message": "任务描述已更新，任务已重新开始执行",
                "restarted": True
            }
        
        return {
            "success": True, 
            "task": task_to_dict(task),
            "message": "任务描述已更新",
            "restarted": False
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"更新描述失败: {str(e)}")


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除任务"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 如果任务正在执行，先停止
    engine_manager.stop_and_unregister(task_id)
    
    s.delete_task(task_id)
    
    return {"success": True}


# ---------- 任务执行历史 ----------

@app.get("/api/tasks/{task_id}/history")
async def get_task_history(task_id: str):
    """获取任务的执行历史"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    messages = s.get_messages(task_id)
    normalized_messages = []
    for msg in messages:
        content = msg.get("content", "")
        msg_copy = dict(msg)
        msg_copy["content"] = normalize_prefixes(content)
        normalized_messages.append(msg_copy)

    return {
        "task_id": task_id,
        "description": task.description,
        "status": task.status.value,
        "progress": task.progress,
        "command_results": [r.to_dict() for r in task.command_results],
        "file_operations": [o.to_dict() for o in task.file_operations],
        "messages": normalized_messages,
        "last_thinking": normalize_prefixes(task.last_thinking),
        "current_step": normalize_prefixes(task.current_step),
        "next_step": task.next_step,
        "token_usage": task.token_usage,
        "todo_items": [t.to_dict() for t in task.todo_items],
        "need_input": task.need_input.to_dict(),
    }


# ---------- 任务执行控制 ----------

@app.post("/api/tasks/{task_id}/run")
async def run_task(task_id: str, options: TaskRunOptions = Body(default=TaskRunOptions())):
    """开始执行任务"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 检查任务是否已在执行中或正在启动/停止
    if engine_manager.contains(task_id):
        raise HTTPException(status_code=400, detail="任务已在执行中")
    if engine_manager.is_starting(task_id) or engine_manager.is_stopping(task_id):
        raise HTTPException(status_code=409, detail="任务正在启动或停止中")
    
    if task.status == TaskStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="任务已完成，无需重复执行。如需重新执行，请使用追加或重试功能")
    
    if task.status not in [TaskStatus.WAITING, TaskStatus.PAUSED, TaskStatus.FAILED]:
        raise HTTPException(status_code=400, detail=f"任务状态 {task.status.value} 无法执行")

    if task.status == TaskStatus.PAUSED:
        task.update_status(TaskStatus.RUNNING, force=True)
        s.update_task(task)
    
    # 在后台线程中执行
    _loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=run_task_in_thread, 
        args=(task_id, task, s, _loop), 
        daemon=True
    )
    thread.start()
    
    return {
        "success": True,
        "message": "任务已开始执行",
        "auto_mode": options.auto_mode
    }


@app.post("/api/tasks/{task_id}/stop")
async def stop_task(task_id: str):
    """停止执行任务"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 先检查状态再停止引擎，避免竞态条件
    if task.status not in [TaskStatus.RUNNING, TaskStatus.NEED_INPUT]:
        raise HTTPException(status_code=400, detail=f"任务状态 {task.status.value} 无法停止")
    
    # 如果任务在活跃引擎中，停止它
    engine_manager.stop_and_unregister(task_id)
    
    # 更新任务状态
    task.update_status(TaskStatus.PAUSED, force=True)
    s.update_task(task)
    
    return {"success": True, "message": "任务已停止"}


@app.post("/api/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 如果正在执行，先停止
    engine_manager.stop_and_unregister(task_id)
    
    task.update_status(TaskStatus.CANCELLED, force=True)
    s.update_task(task)
    
    return {"success": True, "message": "任务已取消"}


@app.post("/api/tasks/{task_id}/input")
async def submit_user_input(task_id: str, request: UserInputRequest):
    """提交用户输入"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status != TaskStatus.NEED_INPUT:
        raise HTTPException(status_code=400, detail="任务当前不在等待输入状态")
    
    # 仅设置用户回答到 need_input，由 engine.run() 恢复上下文时处理
    task.need_input.user_response = request.input_text
    s.update_task(task)
    
    # 重新启动任务
    _loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=run_task_in_thread, 
        args=(task_id, task, s, _loop), 
        daemon=True
    )
    thread.start()

    return {"success": True, "message": "输入已提交，任务继续执行"}


@app.post("/api/tasks/{task_id}/append")
async def append_task(task_id: str, request: AppendTaskRequest):
    """追加任务描述"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if engine_manager.contains(task_id):
        raise HTTPException(status_code=400, detail="任务正在执行中，无法追加")

    if task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        raise HTTPException(status_code=400, detail="只能对已完成、失败或取消的任务追加内容")
    
    # 追加描述并重置状态
    task.description = f"{task.description}\n\n[追加需求]\n{request.additional_description}"
    task.progress = 0
    task.error_message = None
    task.update_status(TaskStatus.WAITING, force=True)
    s.update_task(task)
    
    # 启动任务
    _loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=run_task_in_thread, 
        args=(task_id, task, s, _loop), 
        daemon=True
    )
    thread.start()
    
    return {"success": True, "message": "追加任务已开始处理"}


# ---------- TODO CRUD ----------

@app.get("/api/tasks/{task_id}/todos")
async def get_todos(task_id: str):
    """获取任务的 TODO 列表"""
    s = get_store()
    task = s.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"todo_items": [t.to_dict() for t in task.todo_items]}


@app.post("/api/tasks/{task_id}/todos")
async def add_todo(task_id: str, body: dict = Body(...)):
    """添加 TODO 项"""
    s = get_store()
    task = s.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    content = body.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="TODO 内容不能为空")
    
    import uuid as _uuid
    from src.tasks.models import TodoItem
    item = TodoItem(id=_uuid.uuid4().hex[:8], content=content)
    task.todo_items.append(item)
    s.update_task(task)
    
    # 广播更新
    wsm = get_ws_manager_instance()
    await wsm.broadcast_task_status(task_id, task_to_dict(task))
    
    # 如果任务已结束且有未完成的 TODO，自动重启
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        if any(not t.completed for t in task.todo_items):
            task.progress = int(sum(1 for t in task.todo_items if t.completed) / len(task.todo_items) * 100)
            task.error_message = None
            task.update_status(TaskStatus.WAITING, force=True)
            s.update_task(task)
            _loop = asyncio.get_running_loop()
            thread = threading.Thread(
                target=run_task_in_thread,
                args=(task_id, task, s, _loop),
                daemon=True,
            )
            thread.start()
    
    return {"success": True, "item": item.to_dict()}


@app.put("/api/tasks/{task_id}/todos/{todo_id}")
async def update_todo_item(task_id: str, todo_id: str, body: dict = Body(...)):
    """更新 TODO 项内容"""
    s = get_store()
    task = s.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    for item in task.todo_items:
        if item.id == todo_id:
            content = body.get("content", "").strip()
            if content:
                item.content = content
            s.update_task(task)
            wsm = get_ws_manager_instance()
            await wsm.broadcast_task_status(task_id, task_to_dict(task))
            return {"success": True, "item": item.to_dict()}
    
    raise HTTPException(status_code=404, detail="TODO 项不存在")


@app.delete("/api/tasks/{task_id}/todos/{todo_id}")
async def delete_todo_item(task_id: str, todo_id: str):
    """删除 TODO 项"""
    s = get_store()
    task = s.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    before = len(task.todo_items)
    task.todo_items = [t for t in task.todo_items if t.id != todo_id]
    if len(task.todo_items) == before:
        raise HTTPException(status_code=404, detail="TODO 项不存在")
    
    s.update_task(task)
    wsm = get_ws_manager_instance()
    await wsm.broadcast_task_status(task_id, task_to_dict(task))
    return {"success": True}


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str):
    """重试失败的任务"""
    s = get_store()
    task = s.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.status != TaskStatus.FAILED:
        raise HTTPException(status_code=400, detail="只能重试失败的任务")
    
    # 重置状态
    task.update_status(TaskStatus.WAITING, force=True)
    task.error_message = None
    task.retry_count += 1
    s.update_task(task)
    
    # 在后台线程中执行（不直接调用 run_task 端点函数）
    _loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=run_task_in_thread,
        args=(task_id, task, s, _loop),
        daemon=True
    )
    thread.start()
    
    return {"success": True, "message": "任务重试已开始"}


# ---------- 统计信息 ----------

@app.get("/api/stats")
async def get_stats():
    """获取统计信息"""
    s = get_store()
    tasks = s.list_tasks()
    
    status_counts = {}
    for status in TaskStatus:
        status_counts[status.value] = sum(1 for t in tasks if t.status == status)
    
    # 计算今日完成数
    today = datetime.now().date().isoformat()
    today_completed = sum(
        1 for t in tasks 
        if t.status == TaskStatus.COMPLETED 
        and t.completed_at and t.completed_at.startswith(today)
    )
    
    return {
        "total": len(tasks),
        "status_counts": status_counts,
        "today_completed": today_completed,
        "active_engines": engine_manager.get_active_count()
    }


# ---------- 系统配置 ----------

@app.get("/api/config")
async def get_config():
    """获取系统配置"""
    try:
        user_cfg = {
            "workspace": {"default_working_directory": config.user.default_working_directory},
            "ui": {"auto_scroll": config.user.auto_scroll},
            "task": {"max_iterations": config.user.max_iterations},
            "api": {
                "api_key": config.user.api_key,
                "base_url": config.user.base_url,
                "model": config.user.model,
            },
        }
        return {"config": user_cfg}
    except Exception as e:
        return {"config": {}, "error": str(e)}


@app.put("/api/config")
async def update_config(cfg_data: Dict[str, Any] = Body(...)):
    """更新系统配置"""
    # 任务运行期间禁止修改
    if engine_manager.get_active_count() > 0:
        raise HTTPException(status_code=409, detail="有任务正在运行，无法修改设置")
    try:
        if "workspace" in cfg_data:
            if "default_working_directory" in cfg_data["workspace"]:
                config.user.default_working_directory = cfg_data["workspace"]["default_working_directory"]
        if "ui" in cfg_data:
            if "auto_scroll" in cfg_data["ui"]:
                config.user.auto_scroll = cfg_data["ui"]["auto_scroll"]
        if "task" in cfg_data:
            if "max_iterations" in cfg_data["task"]:
                config.user.max_iterations = cfg_data["task"]["max_iterations"]
        if "api" in cfg_data:
            api = cfg_data["api"]
            if "api_key" in api:
                config.user.api_key = api["api_key"]
            if "base_url" in api:
                config.user.base_url = api["base_url"]
            if "model" in api:
                config.user.model = api["model"]
        config.save_user_config()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/settings/locked")
async def settings_locked():
    """检查设置是否被锁定（有任务运行中）"""
    active = engine_manager.get_active_count()
    return {"locked": active > 0, "active_tasks": active}


# ---------- WebSocket ----------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)):
    """WebSocket连接端点（带 token 认证）"""
    # P0-4: WebSocket 认证 - 校验 token
    expected_token = os.environ.get("AUTOMATEX_WS_TOKEN", "")
    if expected_token and token != expected_token:
        await websocket.close(code=4001, reason="Unauthorized")
        return
    
    wsm = get_ws_manager_instance()
    conn_id = await wsm.connect(websocket)
    
    if not conn_id:
        return  # 连接被拒绝
    
    try:
        while True:
            data = await websocket.receive_text()
            # 处理客户端消息
            try:
                msg = json.loads(data)
                msg_type = msg.get("type")
                
                if msg_type == "ping":
                    await wsm.handle_ping(conn_id)
                elif msg_type == "subscribe":
                    task_id = msg.get("task_id")
                    if task_id:
                        await wsm.subscribe(conn_id, task_id)
                elif msg_type == "unsubscribe":
                    task_id = msg.get("task_id")
                    if task_id:
                        await wsm.unsubscribe(conn_id, task_id)
            except json.JSONDecodeError:
                pass
                
    except WebSocketDisconnect:
        await wsm.disconnect(conn_id)


# ============== Main ==============

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info"
    )
