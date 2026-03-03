"""
任务持久化存储模块
==================

负责任务数据的持久化存储和加载，使用JSON文件作为存储介质。
支持原子写入和崩溃恢复。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .models import Task


class TaskStore:
    """
    任务存储中心
    
    使用JSON文件存储任务状态，支持原子写入防止数据损坏。
    每个任务的消息历史独立存储在messages目录下。
    """
    
    # 消息历史限制配置
    MAX_MESSAGES_PER_TASK = 500  # 每个任务最大消息数
    MAX_MESSAGE_SIZE_BYTES = 50 * 1024  # 单条消息最大 50KB
    MAX_HISTORY_FILE_SIZE_MB = 5  # 历史文件最大 5MB

    def __init__(self, store_path: Optional[str] = None):
        """
        初始化任务存储
        
        Args:
            store_path: 存储文件路径，默认为 tasks/store.json
        """
        if store_path is None:
            store_path = str(Path(__file__).parent / "store.json")
        
        self.store_path = Path(store_path)
        self.messages_dir = self.store_path.parent / "messages"
        self._lock = threading.RLock()
        
        # 确保目录存在
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        
        # 初始化存储文件
        if not self.store_path.exists():
            self._save_raw({"tasks": []})

    def _load_raw(self) -> Dict:
        """加载原始JSON数据"""
        with self._lock:
            try:
                with self.store_path.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                return {"tasks": []}

    def _save_raw(self, data: Dict) -> None:
        """原子化保存JSON数据（带 Windows 重试机制）"""
        with self._lock:
            # 写入临时文件
            temp_path = self.store_path.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 原子替换（带重试）
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    if os.name == 'nt':  # Windows
                        if self.store_path.exists():
                            backup_path = self.store_path.with_suffix(".bak")
                            shutil.copy2(self.store_path, backup_path)
                        shutil.move(str(temp_path), str(self.store_path))
                    else:  # Unix
                        os.replace(temp_path, self.store_path)
                    return  # 成功，退出
                except (PermissionError, OSError) as e:
                    if attempt < max_retries - 1:
                        import time
                        time.sleep(0.1 * (2 ** attempt))  # 指数退避: 0.1s, 0.2s, 0.4s
                    else:
                        raise  # 最后一次尝试仍失败，抛出异常

    def create_task(self, description: str, 
                    working_directory: Optional[str] = None) -> Task:
        """
        创建新任务
        
        Args:
            description: 任务描述
            working_directory: 工作目录
            
        Returns:
            创建的任务对象
        """
        from .models import TaskStatus
        
        task = Task(
            id=Task.generate_id(),
            description=description,
            status=TaskStatus.WAITING,
            working_directory=working_directory,
        )
        
        # 保存到存储
        data = self._load_raw()
        data["tasks"].append(task.to_dict())
        self._save_raw(data)
        
        # 创建消息历史文件
        self._init_message_history(task.id)
        
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        获取指定任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务对象，不存在则返回None
        """
        data = self._load_raw()
        for task_data in data["tasks"]:
            if task_data["id"] == task_id:
                return Task.from_dict(task_data)
        return None

    def update_task(self, task: Task) -> bool:
        """
        更新任务
        
        Args:
            task: 任务对象
            
        Returns:
            是否更新成功
        """
        with self._lock:
            data = self._load_raw()
            for i, task_data in enumerate(data["tasks"]):
                if task_data["id"] == task.id:
                    task.updated_at = datetime.now().isoformat()
                    data["tasks"][i] = task.to_dict()
                    self._save_raw(data)
                    return True
            return False

    def delete_task(self, task_id: str) -> bool:
        """
        删除任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否删除成功
        """
        with self._lock:
            data = self._load_raw()
            original_len = len(data["tasks"])
            data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
            
            if len(data["tasks"]) < original_len:
                self._save_raw(data)
                # 删除消息历史
                msg_file = self.messages_dir / f"{task_id}.json"
                if msg_file.exists():
                    msg_file.unlink()
                return True
            return False

    def list_tasks(self, status: Optional[str] = None) -> List[Task]:
        """
        列出任务
        
        Args:
            status: 按状态过滤（可选）
            
        Returns:
            任务列表
        """
        data = self._load_raw()
        tasks = []
        for task_data in data["tasks"]:
            if status is None or task_data["status"] == status:
                tasks.append(Task.from_dict(task_data))
        return tasks

    def get_pending_tasks(self) -> List[Task]:
        """获取待执行的任务（等待中或需要继续的）"""
        from .models import TaskStatus
        
        data = self._load_raw()
        pending = []
        for task_data in data["tasks"]:
            status = task_data["status"]
            if status in (TaskStatus.WAITING.value, TaskStatus.RUNNING.value):
                pending.append(Task.from_dict(task_data))
        
        return pending

    def get_tasks_need_input(self) -> List[Task]:
        """获取等待用户输入的任务"""
        from .models import TaskStatus
        return self.list_tasks(status=TaskStatus.NEED_INPUT.value)

    # ==================== 消息历史管理 ====================

    def _init_message_history(self, task_id: str) -> None:
        """初始化任务的消息历史文件"""
        msg_file = self.messages_dir / f"{task_id}.json"
        if not msg_file.exists():
            with msg_file.open("w", encoding="utf-8") as f:
                json.dump({"messages": []}, f, ensure_ascii=False, indent=2)

    def get_messages(self, task_id: str) -> List[Dict]:
        """
        获取任务的消息历史
        
        Args:
            task_id: 任务ID
            
        Returns:
            消息列表
        """
        msg_file = self.messages_dir / f"{task_id}.json"
        if not msg_file.exists():
            return []
        
        try:
            with msg_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("messages", [])
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def update_first_message(self, task_id: str, new_content: str) -> bool:
        """
        更新任务的第一条消息（通常是 task_init 消息）
        
        当用户修改任务描述时调用此方法，确保 AI 看到的是最新的描述。
        
        Args:
            task_id: 任务ID
            new_content: 新的消息内容
            
        Returns:
            是否成功更新
        """
        msg_file = self.messages_dir / f"{task_id}.json"
        if not msg_file.exists():
            return False
        
        with self._lock:
            try:
                with msg_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                
                messages = data.get("messages", [])
                if not messages:
                    return False
                
                # 更新第一条消息的内容
                messages[0]["content"] = new_content
                messages[0]["updated_at"] = datetime.now().isoformat()
                
                with msg_file.open("w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                return True
            except (json.JSONDecodeError, FileNotFoundError):
                return False

    def add_message(self, task_id: str, role: str, content: str, 
                    message_type: str = "chat",
                    metadata: Optional[Dict] = None) -> None:
        """
        添加消息到任务历史（带大小限制）
        
        Args:
            task_id: 任务ID
            role: 角色 (user/assistant/system/execution_result)
            content: 消息内容
            message_type: 消息类型 (chat/execution/system)
            metadata: 可选的结构化元数据（工具名、参数、耗时等）
        """
        msg_file = self.messages_dir / f"{task_id}.json"
        
        # 截断过大的单条消息
        content_bytes = content.encode('utf-8') if isinstance(content, str) else content
        if len(content_bytes) > self.MAX_MESSAGE_SIZE_BYTES:
            content = content[:self.MAX_MESSAGE_SIZE_BYTES // 2] + \
                      f"\n\n... [消息被截断，原始大小: {len(content_bytes)} 字节] ..."
        
        with self._lock:
            # 加载现有消息
            if msg_file.exists():
                with msg_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {"messages": []}
            
            # 添加新消息
            msg_entry = {
                "role": role,
                "content": content,
                "type": message_type,
                "timestamp": datetime.now().isoformat(),
            }
            if metadata:
                msg_entry["metadata"] = metadata
            data["messages"].append(msg_entry)
            
            # 检查消息数量限制，保留最新的消息
            if len(data["messages"]) > self.MAX_MESSAGES_PER_TASK:
                # 保留首条消息（通常是任务描述）和最新的消息
                first_msg = data["messages"][0]
                keep_count = self.MAX_MESSAGES_PER_TASK - 1
                data["messages"] = [first_msg] + data["messages"][-keep_count:]
                data["_truncated"] = True
                data["_truncated_at"] = datetime.now().isoformat()
            
            # 保存
            with msg_file.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 检查文件大小限制
            file_size_mb = msg_file.stat().st_size / (1024 * 1024)
            if file_size_mb > self.MAX_HISTORY_FILE_SIZE_MB:
                self._compact_message_history(task_id, data)

    def _compact_message_history(self, task_id: str, data: Dict) -> None:
        """
        压缩消息历史以减小文件大小
        
        策略：
        1. 保留首条消息（任务描述）
        2. 保留最近 100 条消息
        3. 中间消息用摘要替代
        """
        msg_file = self.messages_dir / f"{task_id}.json"
        messages = data.get("messages", [])
        
        if len(messages) <= 100:
            return
        
        # 保留首条和最近 100 条
        first_msg = messages[0]
        recent_msgs = messages[-100:]
        removed_count = len(messages) - 101
        
        # 创建摘要消息
        summary_msg = {
            "role": "system",
            "content": f"[历史消息已压缩：移除了 {removed_count} 条中间消息以节省空间]",
            "type": "system",
            "timestamp": datetime.now().isoformat(),
        }
        
        data["messages"] = [first_msg, summary_msg] + recent_msgs
        data["_compacted"] = True
        data["_compacted_at"] = datetime.now().isoformat()
        data["_removed_count"] = removed_count
        
        with msg_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def clear_messages(self, task_id: str) -> None:
        """清空任务的消息历史"""
        msg_file = self.messages_dir / f"{task_id}.json"
        if msg_file.exists():
            with msg_file.open("w", encoding="utf-8") as f:
                json.dump({"messages": []}, f, ensure_ascii=False, indent=2)

    # ==================== 统计信息 ====================

    def get_statistics(self) -> Dict:
        """获取任务统计信息"""
        from .models import TaskStatus
        
        data = self._load_raw()
        stats = {
            "total": len(data["tasks"]),
            "waiting": 0,
            "running": 0,
            "need_input": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        
        for task_data in data["tasks"]:
            status = task_data["status"]
            if status in stats:
                stats[status] += 1
        
        return stats

    def cleanup_old_tasks(self, days: int = 30) -> int:
        """
        清理指定天数之前已完成的任务
        
        Args:
            days: 保留天数
            
        Returns:
            清理的任务数量
        """
        from datetime import timedelta
        from .models import TaskStatus
        
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        
        with self._lock:
            data = self._load_raw()
            original_len = len(data["tasks"])
            
            # 过滤保留的任务
            kept_tasks = []
            deleted_ids = []
            
            for task_data in data["tasks"]:
                status = task_data["status"]
                completed_at = task_data.get("completed_at")
                
                # 只清理已完成/失败/取消的旧任务
                if status in (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, 
                             TaskStatus.CANCELLED.value):
                    if completed_at and completed_at < cutoff:
                        deleted_ids.append(task_data["id"])
                        continue
                
                kept_tasks.append(task_data)
            
            data["tasks"] = kept_tasks
            self._save_raw(data)
            
            # 删除消息历史
            for task_id in deleted_ids:
                msg_file = self.messages_dir / f"{task_id}.json"
                if msg_file.exists():
                    msg_file.unlink()
            
            return original_len - len(kept_tasks)
