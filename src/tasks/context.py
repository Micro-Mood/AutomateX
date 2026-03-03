"""
上下文管理器
============

管理对话历史，实现 FIFO 滑动窗口
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum


class Phase(Enum):
    """迭代阶段"""
    SELECT = "select"    # AI 选择工具
    PARAMS = "params"    # AI 填写参数
    EXEC = "exec"        # 执行工具
    RESULT = "result"    # 返回结果


@dataclass
class Message:
    """消息"""
    role: str  # system, user, assistant
    content: str
    
    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Context:
    """任务上下文"""
    
    # 系统消息（只存一次，不计入 FIFO）
    system_msg: Optional[Message] = None
    
    # 对话历史（FIFO）
    history: List[Message] = field(default_factory=list)
    
    # 配置
    max_history: int = 20  # FIFO 上限
    
    # 当前状态
    phase: Phase = Phase.SELECT
    selected_tools: List[str] = field(default_factory=list)
    
    def set_system(self, content: str):
        """设置系统消息（只调用一次）"""
        self.system_msg = Message("system", content)
    
    def add_user(self, content: str):
        """添加用户消息"""
        self.history.append(Message("user", content))
        self._trim()
    
    def add_assistant(self, content: str):
        """添加助手消息"""
        self.history.append(Message("assistant", content))
        self._trim()
    
    def _trim(self):
        """FIFO 裁剪"""
        if len(self.history) > self.max_history:
            # 删除最早的消息
            excess = len(self.history) - self.max_history
            self.history = self.history[excess:]
    
    def build_messages(self) -> List[Dict[str, str]]:
        """构建发送给 AI 的消息列表"""
        messages = []
        
        # 系统消息（始终在最前）
        if self.system_msg:
            messages.append(self.system_msg.to_dict())
        
        # 对话历史
        for msg in self.history:
            messages.append(msg.to_dict())
        
        return messages
    
    def reset_phase(self):
        """重置到选择阶段"""
        self.phase = Phase.SELECT
        self.selected_tools = []
    
    def token_estimate(self) -> int:
        """估算当前上下文的 token 数"""
        total = 0
        if self.system_msg:
            total += len(self.system_msg.content) // 4  # 粗略估算
        for msg in self.history:
            total += len(msg.content) // 4
        return total
    
    def clear_history(self):
        """清空对话历史（保留系统消息）"""
        self.history = []
        self.reset_phase()
    
    def get_last_assistant_msg(self) -> Optional[str]:
        """获取最后一条助手消息"""
        for msg in reversed(self.history):
            if msg.role == "assistant":
                return msg.content
        return None
