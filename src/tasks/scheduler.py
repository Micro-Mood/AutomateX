"""
任务调度模块
============

负责任务的调度和执行时机控制。
"""

from __future__ import annotations

import time
import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .models import Task, TaskStatus
from .store import TaskStore


class TaskScheduler:
    """
    任务调度器
    
    功能：
    - 管理任务队列
    - 支持定时任务
    - 处理任务暂停/恢复
    """

    def __init__(self, 
                 store: TaskStore,
                 poll_interval: float = 1.0,
                 max_concurrent: int = 1):
        """
        初始化调度器
        
        Args:
            store: 任务存储
            poll_interval: 轮询间隔（秒）
            max_concurrent: 最大并发任务数
        """
        self.store = store
        self.poll_interval = poll_interval
        self.max_concurrent = max_concurrent
        
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._current_tasks: Dict[str, Task] = {}
        
        # 回调函数
        self._on_task_ready: Optional[Callable[[Task], None]] = None
        self._on_task_complete: Optional[Callable[[Task], None]] = None
        self._on_task_error: Optional[Callable[[Task, Exception], None]] = None

    def set_callbacks(self,
                      on_ready: Optional[Callable[[Task], None]] = None,
                      on_complete: Optional[Callable[[Task], None]] = None,
                      on_error: Optional[Callable[[Task, Exception], None]] = None):
        """设置回调函数"""
        self._on_task_ready = on_ready
        self._on_task_complete = on_complete
        self._on_task_error = on_error

    def start(self):
        """启动调度器"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止调度器"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _run_loop(self):
        """调度循环"""
        while self._running:
            try:
                self._check_and_dispatch()
            except Exception as e:
                print(f"调度器错误: {e}")
            
            time.sleep(self.poll_interval)

    def _check_and_dispatch(self):
        """检查并分发任务"""
        with self._lock:
            # 检查当前运行的任务数
            running_count = len(self._current_tasks)
            if running_count >= self.max_concurrent:
                return
            
            # 获取待执行任务
            pending = self.store.get_pending_tasks()
            
            for task in pending:
                # 跳过已在运行的任务
                if task.id in self._current_tasks:
                    continue
                
                # 跳过等待输入的任务
                if task.status == TaskStatus.NEED_INPUT:
                    continue
                
                # 检查是否可以执行
                if self._can_execute(task):
                    self._dispatch_task(task)
                    break

    def _can_execute(self, task: Task) -> bool:
        """判断任务是否可以执行"""
        # 检查状态
        if task.status not in (TaskStatus.WAITING, TaskStatus.RUNNING):
            return False
        
        # 可以添加更多条件检查，如：
        # - 定时执行检查
        # - 依赖检查
        # - 资源检查
        
        return True

    def _dispatch_task(self, task: Task):
        """分发任务"""
        self._current_tasks[task.id] = task
        
        if self._on_task_ready:
            try:
                self._on_task_ready(task)
            except Exception as e:
                self._handle_task_error(task, e)

    def _handle_task_error(self, task: Task, error: Exception):
        """处理任务错误"""
        with self._lock:
            if task.id in self._current_tasks:
                del self._current_tasks[task.id]
        
        if self._on_task_error:
            self._on_task_error(task, error)

    def mark_task_complete(self, task_id: str):
        """标记任务完成"""
        with self._lock:
            if task_id in self._current_tasks:
                task = self._current_tasks.pop(task_id)
                if self._on_task_complete:
                    self._on_task_complete(task)

    def mark_task_running(self, task_id: str):
        """标记任务运行中"""
        task = self.store.get_task(task_id)
        if task:
            task.update_status(TaskStatus.RUNNING)
            self.store.update_task(task)

    def get_next_task(self) -> Optional[Task]:
        """
        获取下一个要执行的任务（同步模式使用）
        
        Returns:
            下一个任务，如果没有则返回None
        """
        pending = self.store.get_pending_tasks()
        
        for task in pending:
            if task.status == TaskStatus.NEED_INPUT:
                # 如果用户已回答，可以继续
                if task.need_input.user_response:
                    return task
                continue
            
            if task.status in (TaskStatus.WAITING, TaskStatus.RUNNING):
                return task
        
        return None

    def get_tasks_awaiting_input(self) -> List[Task]:
        """获取等待用户输入的任务"""
        return self.store.get_tasks_need_input()

    def submit_user_input(self, task_id: str, response: str) -> bool:
        """
        提交用户输入
        
        Args:
            task_id: 任务ID
            response: 用户响应
            
        Returns:
            是否成功
        """
        task = self.store.get_task(task_id)
        if not task:
            return False
        
        if task.status != TaskStatus.NEED_INPUT:
            return False
        
        # 设置用户输入
        task.set_user_input(response)
        task.update_status(TaskStatus.RUNNING)
        
        # 重置need_input状态
        task.need_input.required = False
        
        return self.store.update_task(task)

    def pause_task(self, task_id: str) -> bool:
        """暂停任务"""
        task = self.store.get_task(task_id)
        if not task:
            return False
        
        if task.status == TaskStatus.RUNNING:
            task.update_status(TaskStatus.PAUSED)
            return self.store.update_task(task)
        
        return False

    def resume_task(self, task_id: str) -> bool:
        """恢复任务"""
        task = self.store.get_task(task_id)
        if not task:
            return False
        
        if task.status == TaskStatus.PAUSED:
            task.update_status(TaskStatus.RUNNING)
            return self.store.update_task(task)
        
        return False

    def cancel_task(self, task_id: str) -> bool:
        """取消任务"""
        task = self.store.get_task(task_id)
        if not task:
            return False
        
        if task.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            task.update_status(TaskStatus.CANCELLED)
            
            with self._lock:
                if task_id in self._current_tasks:
                    del self._current_tasks[task_id]
            
            return self.store.update_task(task)
        
        return False

    def retry_task(self, task_id: str) -> bool:
        """重试失败的任务"""
        task = self.store.get_task(task_id)
        if not task:
            return False
        
        if task.status == TaskStatus.FAILED:
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.update_status(TaskStatus.WAITING)
                task.error_message = None
                return self.store.update_task(task)
        
        return False

    def get_queue_status(self) -> Dict:
        """获取队列状态"""
        stats = self.store.get_statistics()
        
        return {
            "running": self._running,
            "current_tasks": list(self._current_tasks.keys()),
            "max_concurrent": self.max_concurrent,
            **stats,
        }
