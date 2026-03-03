"""
AutomateX API - 简化的接口
==========================

提供简单易用的API接口，方便集成到其他应用程序中。
使用 V3 TaskEngine 实现两阶段工具调用，极大降低 Token 消耗。

注意:
    AI 模型配置已统一由 src.config 管理，通过 user_config.json 或桌面应用设置界面配置。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .engine import TaskEngine, EngineConfig
from .store import TaskStore
from .models import Task, TaskStatus
from .config import get_mcp_host, get_mcp_port, get_max_iterations


def _get_api():
    """获取 AI API 实例（从统一配置读取）"""
    from .chat import get_api
    return get_api()


class AutomateX:
    """
    AutomateX 主接口类
    
    提供简洁的API用于：
    - 创建和管理任务
    - 运行自动化任务
    - 查询任务状态
    
    示例::
    
        from src.tasks import AutomateX
        
        # 创建实例
        ax = AutomateX()
        
        # 快速运行任务
        task = ax.run("创建一个名为test的文件夹")
        
        # 交互式运行
        task = ax.run_interactive("帮我整理当前目录的文件")
        
        # 查看所有任务
        tasks = ax.list_tasks()
    """

    def __init__(self,
                 working_directory: Optional[str] = None,
                 use_mcp: bool = True,
                 show_reasoning: bool = False,
                 model: str = None):
        """
        初始化 AutomateX
        
        Args:
            working_directory: 工作目录（默认从统一配置读取）
            use_mcp: 是否使用 MCP Server（否则使用本地执行）
            show_reasoning: 是否显示AI思考过程
            model: 已废弃，实际从 user_config.json 读取
        """
        self.working_directory = working_directory or str(Path.cwd())
        self.show_reasoning = show_reasoning
        
        self.store = TaskStore()
        self.api = _get_api()
        
        self.config = EngineConfig(
            max_history=20,
            max_iterations=get_max_iterations(),
            mcp_host=get_mcp_host(),
            mcp_port=get_mcp_port(),
            use_mcp=use_mcp,
        )
        
        self._on_output: Optional[Callable[[str], None]] = None
        self._engine: Optional[TaskEngine] = None

    def _get_engine(self) -> TaskEngine:
        """获取引擎实例"""
        return TaskEngine(
            api=self.api,
            store=self.store,
            config=self.config,
            on_output=self._on_output or (print if self.show_reasoning else lambda x: None),
        )

    def create_task(self, description: str) -> Task:
        """
        创建任务
        
        Args:
            description: 任务描述
            
        Returns:
            新创建的任务
        """
        task = Task(
            id=Task.generate_id(),
            description=description,
            working_directory=self.working_directory,
        )
        self.store.add_task(task)
        return task

    def run(self, 
            description: str, 
            max_iterations: Optional[int] = None) -> Task:
        """
        运行任务
        
        Args:
            description: 任务描述
            max_iterations: 最大迭代次数
            
        Returns:
            完成的任务对象
            
        注意:
            如果任务需要用户输入，会返回状态为 NEED_INPUT 的任务
            使用 provide_input() 方法提供输入后继续
        """
        task = self.create_task(description)
        if max_iterations is not None:
            self.config.max_iterations = max_iterations
        
        engine = self._get_engine()
        return asyncio.run(engine.run(task))

    def run_interactive(self,
                        description: str) -> Task:
        """
        交互式运行任务
        
        遇到需要输入时会从控制台获取
        
        Args:
            description: 任务描述
            
        Returns:
            完成的任务对象
        """
        task = self.create_task(description)
        engine = self._get_engine()
        
        while True:
            task = asyncio.run(engine.run(task))
            
            if task.status == TaskStatus.NEED_INPUT:
                # 从控制台获取输入
                print(f"\n❓ {task.need_input.question if task.need_input else '请输入:'}")
                if task.need_input and task.need_input.options:
                    for i, opt in enumerate(task.need_input.options, 1):
                        print(f"  {i}. {opt}")
                
                user_input = input("> ")
                task = asyncio.run(engine.continue_with_input(task, user_input))
            else:
                break
        
        return task

    def continue_task(self, task_id: str, user_input: Optional[str] = None) -> Task:
        """
        继续执行任务
        
        Args:
            task_id: 任务ID
            user_input: 用户输入（如果任务在等待输入）
            
        Returns:
            执行后的任务
        """
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError(f"任务不存在: {task_id}")
        
        # 防止重复执行已完成的任务
        if task.status == TaskStatus.COMPLETED:
            return task
        
        engine = self._get_engine()
        
        if task.status == TaskStatus.NEED_INPUT and user_input:
            return asyncio.run(engine.continue_with_input(task, user_input))
        else:
            return asyncio.run(engine.run(task))

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        获取任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务对象，不存在则返回None
        """
        return self.store.get_task(task_id)

    def list_tasks(self, status: Optional[str] = None) -> List[Task]:
        """
        列出任务
        
        Args:
            status: 按状态过滤 (waiting/running/need_input/completed/failed)
            
        Returns:
            任务列表
        """
        return self.store.list_tasks(status)

    def get_pending_tasks(self) -> List[Task]:
        """获取待处理的任务"""
        return self.store.get_pending_tasks()

    def get_tasks_awaiting_input(self) -> List[Task]:
        """获取等待用户输入的任务"""
        return self.store.get_tasks_need_input()

    def cancel_task(self, task_id: str) -> bool:
        """
        取消任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功
        """
        task = self.store.get_task(task_id)
        if task and task.status in [TaskStatus.WAITING, TaskStatus.RUNNING, TaskStatus.NEED_INPUT, TaskStatus.PAUSED]:
            task.update_status(TaskStatus.CANCELLED, force=True)
            task.error_message = "用户取消"
            self.store.update_task(task)
            return True
        return False

    def retry_task(self, task_id: str) -> bool:
        """
        重试失败的任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功
        """
        task = self.store.get_task(task_id)
        if task and task.status == TaskStatus.FAILED:
            task.update_status(TaskStatus.WAITING, force=True)
            task.error_message = None
            task.retry_count += 1
            self.store.update_task(task)
            return True
        return False

    def delete_task(self, task_id: str) -> bool:
        """
        删除任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            是否成功
        """
        return self.store.delete_task(task_id)

    def get_statistics(self) -> Dict:
        """
        获取统计信息
        
        Returns:
            统计数据字典
        """
        return self.store.get_statistics()

    def cleanup(self, days: int = 30) -> int:
        """
        清理旧任务
        
        Args:
            days: 保留天数
            
        Returns:
            清理的任务数量
        """
        return self.store.cleanup_old_tasks(days)

    def set_output_callback(self, callback: Optional[Callable[[str], None]]):
        """
        设置输出回调函数
        
        Args:
            callback: 输出回调
        """
        self._on_output = callback


# 便捷函数
def quick_run(description: str, 
              show_reasoning: bool = False) -> Task:
    """
    快速运行任务
    
    Args:
        description: 任务描述
        show_reasoning: 是否显示思考过程
        
    Returns:
        完成的任务
    """
    ax = AutomateX(show_reasoning=show_reasoning)
    return ax.run(description)


def interactive_run(description: str,
                    show_reasoning: bool = True) -> Task:
    """
    交互式运行任务
    
    Args:
        description: 任务描述
        show_reasoning: 是否显示思考过程
        
    Returns:
        完成的任务
    """
    ax = AutomateX(show_reasoning=show_reasoning)
    return ax.run_interactive(description)
