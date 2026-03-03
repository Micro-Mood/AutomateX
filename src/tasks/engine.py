"""
任务引擎 V3
===========

两阶段工具调用，极致 Token 优化

工作流程:
1. Phase.SELECT: AI 选择工具
2. Phase.PARAMS: 注入工具描述，AI 填参数
3. Phase.EXEC: 执行工具，返回结果
4. 循环直到 done/fail
"""

import json
import os
import re
import asyncio
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

from .context import Context, Phase
from .tools import get_names_by_category, get_compact_desc, get_tool, get_all_names, get_names_of_category
from .mcp_client import (
    MCPClient, MCPResult,
    local_run_command, local_read_file, local_write_file, 
    local_list_dir, local_exists
)
from .models import Task, TaskStatus
from .store import TaskStore
from pathlib import Path


# ==================== 系统 Prompt ====================

# 提示词目录
PROMPT_DIR = Path(__file__).parent / "prompt"


def _load_prompt_template() -> str:
    """从文件加载提示词模板"""
    prompt_file = PROMPT_DIR / "select.md"
    if prompt_file.exists():
        return prompt_file.read_text(encoding="utf-8")
    
    # 备用默认提示词
    return """你是 AutomateX 自动化助手。

## 工作目录
{cwd}

## 可用工具
{tool_list}

## 工作流程
1. 分析任务，选择需要的工具
2. 返回: {"select": ["工具1", "工具2"]}
3. 收到工具参数说明后，调用工具
4. 返回: {"call": "工具名", ...参数}
5. 查看结果，继续或完成

## 完成任务
{"call": "done", "summary": "完成摘要"}

## 任务失败
{"call": "fail", "reason": "原因"}

## 询问用户
{"call": "ask", "question": "你的问题"}
"""


def build_system_prompt(cwd: str) -> str:
    """构建系统提示"""
    # 生成工具名称列表
    categories = get_names_by_category()
    lines = []
    for cat, names in categories.items():
        lines.append(f"- {cat}: {', '.join(names)}")
    tool_list = "\n".join(lines)
    
    template = _load_prompt_template()
    return template.replace("{cwd}", cwd).replace("{tool_list}", tool_list)


# ==================== 解析器 ====================

def parse_tool_select(response: str) -> List[str]:
    """
    解析工具选择
    
    支持:
    - {"select": ["tool1", "tool2"]}
    - {"select": ["read", "exec"]}  (分类名，自动展开)
    """
    # JSON 格式
    match = re.search(r'\{\s*"select"\s*:\s*\[([^\]]*)\]', response)
    if match:
        try:
            names = re.findall(r'"([^"]+)"', match.group(1))
            if names:
                # 展开分类名为具体工具名
                expanded = []
                for n in names:
                    cat_tools = get_names_of_category(n)
                    if cat_tools:
                        # 这是一个分类名，展开
                        expanded.extend(cat_tools)
                    else:
                        expanded.append(n)
                return expanded
        except Exception:
            pass
    
    return []


def parse_tool_call(response: str) -> Optional[Dict[str, Any]]:
    """
    解析工具调用（返回第一个）
    
    支持:
    - {"call": "tool_name", "param": "value"}
    - ```json {...} ```
    """
    calls = parse_all_tool_calls(response)
    return calls[0] if calls else None


def parse_all_tool_calls(response: str) -> List[Dict[str, Any]]:
    """
    解析响应中的所有工具调用（按出现顺序返回）
    
    支持:
    - {"call": "tool_name", "param": "value"}
    - ```json {...} ```
    - 一条消息中多个 call
    """
    results = []
    seen_positions = set()  # 去重，防止同一个 JSON 被不同 pattern 匹配多次
    
    # 提取 JSON
    patterns = [
        r'```json\s*([\s\S]*?)```',
        r'```\s*([\s\S]*?)```',
        r'(\{[^{}]*"call"[^{}]*\})',
        r'(\{"call"[\s\S]*?\})',
    ]
    
    for pattern in patterns:
        for m in re.finditer(pattern, response, re.MULTILINE):
            text = m.group(1) if m.lastindex else m.group(0)
            try:
                # 清理可能导致 JSON 解析失败的控制字符
                clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text.strip())
                data = json.loads(clean)
                if isinstance(data, dict) and "call" in data:
                    # 用起始位置去重
                    if m.start() not in seen_positions:
                        seen_positions.add(m.start())
                        results.append(data)
            except Exception:
                continue
    
    # 尝试直接解析整个响应（仅当上面没找到时）
    if not results:
        try:
            clean = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', response.strip())
            data = json.loads(clean)
            if isinstance(data, dict) and "call" in data:
                results.append(data)
        except Exception:
            pass
    
    return results


# ==================== 主引擎 ====================

@dataclass
class EngineConfig:
    """引擎配置"""
    max_history: int = 20       # FIFO 上限
    max_iterations: int = 50    # 最大迭代数
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8080
    use_mcp: bool = True        # 是否使用 MCP Server


class TaskEngine:
    """任务引擎 V3"""
    
    def __init__(
        self,
        api,  # AI API 接口，需要有 chat(messages) 方法
        store: TaskStore,
        config: Optional[EngineConfig] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_thinking: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable] = None,
        on_tool_end: Optional[Callable] = None,
    ):
        self.api = api
        self.store = store
        self.config = config or EngineConfig()
        self.on_output = on_output or print
        self.on_thinking = on_thinking  # AI 回复内容回调
        self.on_tool_start = on_tool_start  # 工具开始回调(task_id, tool, args, call_id)
        self.on_tool_end = on_tool_end      # 工具结束回调(task_id, tool, result, call_id, duration_ms)
        
        self._mcp: Optional[MCPClient] = None
        self._stop = False
    
    def _log(self, msg: str):
        """输出日志"""
        if self.on_output:
            self.on_output(msg)
    
    def _emit_thinking(self, content: str):
        """发送 AI 思考内容"""
        if self.on_thinking:
            self.on_thinking(content)
    
    async def _connect_mcp(self) -> bool:
        """连接 MCP Server"""
        if not self.config.use_mcp:
            return False
            
        if self._mcp is None:
            try:
                self._mcp = MCPClient(self.config.mcp_host, self.config.mcp_port)
                success = await self._mcp.connect()
                if success:
                    self._log("✅ MCP Server 已连接")
                    return True
                else:
                    self._log("⚠️ MCP Server 连接失败，使用本地模式")
                    self._mcp = None
                    return False
            except Exception as e:
                self._log(f"⚠️ MCP Server 连接失败: {e}，使用本地模式")
                self._mcp = None
                return False
        return self._mcp.connected
    
    def _build_context_summary(self, task_id: str, last_n: int = None) -> str:
        """
        构建历史上下文摘要，供 AI 了解之前做了什么
        
        从 store 加载全部历史消息，提取关键操作（用户请求、工具调用、执行结果），
        过滤掉系统提示和工具描述等噪音，生成简洁的摘要文本。
        """
        stored_msgs = self.store.get_messages(task_id)
        if not stored_msgs:
            return "📜 暂无历史上下文。"
        
        # 提取有意义的对话轮次
        rounds = []  # [(用户消息, AI动作, 执行结果)]
        current_round = {"user": None, "actions": [], "results": []}
        
        for msg in stored_msgs:
            role = msg.get("role", "")
            mtype = msg.get("type", "")
            content = msg.get("content", "")
            metadata = msg.get("metadata", {})
            
            # 跳过系统消息和工具描述注入
            if role == "system":
                continue
            if role == "user" and mtype == "system":
                # 系统注入的工具描述、格式提示等，跳过
                if "工具说明:" in content or "请选择需要的工具" in content or "请按格式调用" in content or "请调用工具" in content:
                    continue
            
            if role == "user" and mtype == "chat":
                # 新一轮用户消息
                if current_round["user"] or current_round["actions"]:
                    rounds.append(current_round)
                current_round = {"user": content, "actions": [], "results": []}
            elif role == "assistant" and mtype == "chat":
                # AI 的工具调用
                # 只提取有意义的调用，过滤纯文本回复
                if '"call"' in content or '"select"' in content:
                    current_round["actions"].append(content.strip())
            elif role == "execution_result" or (role == "assistant" and mtype == "execution"):
                # 执行结果
                tool_name = metadata.get("tool", "") if metadata else ""
                success = metadata.get("success", True) if metadata else True
                if tool_name:
                    # 简化结果：只保留工具名和关键数据
                    summary = f"{tool_name}({'✓' if success else '✗'})"
                    # 对关键工具提取更多信息
                    if tool_name in ("list_directory", "read_file", "search_files", "search_content"):
                        summary += f": {content[:150]}" if len(content) > 150 else f": {content}"
                    elif tool_name in ("delete_file", "delete_directory", "create_file", "write_file",
                                        "move_file", "copy_file", "replace_range", "insert_text", "delete_range"):
                        args = metadata.get("args", {})
                        path = args.get("path", args.get("source", ""))
                        summary += f" {path}"
                    current_round["results"].append(summary)
                else:
                    # done/fail/ask 的总结消息
                    current_round["results"].append(content.strip())
        
        # 别忘了最后一轮
        if current_round["user"] or current_round["actions"]:
            rounds.append(current_round)
        
        if not rounds:
            return "📜 暂无有意义的历史操作。"
        
        # 如果指定了 last_n，只取最近几轮
        if last_n and last_n > 0:
            rounds = rounds[-last_n:]
        
        # 构建摘要文本
        lines = ["📜 历史上下文摘要："]
        for i, r in enumerate(rounds, 1):
            if r["user"]:
                # 用户消息截断展示
                user_text = r["user"][:200] + "..." if len(r["user"]) > 200 else r["user"]
                lines.append(f"\n--- 第{i}轮 ---")
                lines.append(f"用户: {user_text}")
            if r["results"]:
                for res in r["results"]:
                    res_text = res[:200] + "..." if len(res) > 200 else res
                    lines.append(f"  → {res_text}")
        
        return "\n".join(lines)
    
    async def _exec_tool(self, name: str, args: Dict[str, Any], cwd: str) -> MCPResult:
        """执行工具"""
        # 控制工具
        if name == "done":
            return MCPResult(True, data={"done": True, "summary": args.get("summary", "")})
        
        if name == "fail":
            return MCPResult(False, error=args.get("reason", "任务失败"))
        
        if name == "ask":
            return MCPResult(True, data={"ask": True, "question": args.get("question", ""), "options": args.get("options", [])})
        
        if name == "update_todo":
            return MCPResult(True, data={"update_todo": True, "todo_id": args.get("todo_id", "")})
        
        if name == "get_context":
            return MCPResult(True, data={"get_context": True, "last_n": args.get("last_n")})
        
        # 工具名即 MCP 方法名（tools.py 已与 MCP 完全匹配）
        # 过滤掉值为 None 的参数
        params = {k: v for k, v in args.items() if v is not None}
        
        # 使用 MCP
        if self._mcp and self._mcp.connected:
            return await self._mcp.call(name, params)
        
        # 本地 fallback（仅支持少量基础工具）
        if name == "create_task":
            return await local_run_command(args.get("command", ""), args.get("cwd") or cwd)
        elif name == "read_file":
            return await local_read_file(args.get("path", ""))
        elif name == "write_file":
            return await local_write_file(args.get("path", ""), args.get("content", ""))
        elif name == "list_directory":
            return await local_list_dir(args.get("path", "."), args.get("pattern", "*"))
        elif name == "exists":
            return await local_exists(args.get("path", ""))
        
        return MCPResult(False, error=f"工具 {name} 不支持本地执行，请启动 MCP Server")
    
    def _sync_todo_items(self, task: Task) -> None:
        """
        同步 TODO 列表：合并 store 中的最新状态与引擎内存中的完成标记。
        
        解决竞态条件：前端可能在引擎运行期间增删 TODO 项，
        引擎的 update_task 会用陈旧的内存副本覆盖掉前端的修改。
        
        策略：
        1. 从 store 读取最新的 todo_items（包含前端的增删）
        2. 将引擎侧已标记为 completed 的状态合并到最新列表
        3. 用合并后的列表替换 task.todo_items
        """
        fresh = self.store.get_task(task.id)
        if not fresh:
            return
        
        # 收集引擎侧已完成的 todo_id
        completed_ids = {t.id for t in task.todo_items if t.completed}
        
        # 以 store 中的最新列表为基准，合并完成状态
        for item in fresh.todo_items:
            if item.id in completed_ids:
                item.completed = True
        
        task.todo_items = fresh.todo_items

    async def run(self, task: Task) -> Task:
        """
        运行任务
        
        两阶段工具调用流程:
        1. SELECT: AI 选择工具
        2. PARAMS: 注入工具描述，AI 填参数并调用
        3. 执行工具，返回结果
        4. 循环直到 done/fail
        """
        self._stop = False
        
        # 防止重复执行已完成的任务
        if task.status == TaskStatus.COMPLETED:
            self._log(f"⚠️ 任务已完成，跳过重复执行: {task.id}")
            return task
        
        # 工作目录（绝对路径）
        cwd = task.working_directory or os.getcwd()
        if cwd and cwd != ".":
            from pathlib import Path
            cwd = str(Path(cwd).resolve())
        else:
            cwd = os.getcwd()
        
        # 连接 MCP 并设置工作目录
        if await self._connect_mcp():
            if cwd and cwd != ".":
                result = await self._mcp.call("set_workspace", {"root_path": cwd, "persist": False})
                if result.success:
                    self._log(f"📂 工作目录: {cwd}")
                else:
                    self._log(f"⚠️ 设置工作目录失败: {result.error}")
        
        # 初始化上下文
        ctx = Context(max_history=self.config.max_history)
        sys_prompt = build_system_prompt(cwd)
        
        # 注入 TODO 清单到系统提示
        if task.todo_items:
            todo_lines = []
            for i, t in enumerate(task.todo_items, 1):
                mark = "x" if t.completed else " "
                todo_lines.append(f"[{mark}] {i}. {t.content}  (id={t.id})")
            todo_section = "\n\n## 当前 TODO 清单\n" + "\n".join(todo_lines)
            todo_section += "\n按select.md中的TODO规则逐个完成。"
            sys_prompt += todo_section
        
        ctx.set_system(sys_prompt)
        
        # 检查是否是从 NEED_INPUT 恢复的任务
        is_resuming = task.need_input.user_response is not None and task.need_input.user_response != ""
        
        if is_resuming:
            # 从 store 加载历史消息恢复上下文
            stored_msgs = self.store.get_messages(task.id)
            for msg in stored_msgs:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "system":
                    continue  # 系统消息已通过 set_system 设置
                elif role in ("user", "execution_result"):
                    ctx.add_user(content)
                elif role == "assistant":
                    ctx.add_assistant(content)
            
            # 添加用户回答作为新消息
            user_response = task.need_input.user_response
            ctx.add_user(f"用户回答: {user_response}")
            self.store.add_message(task.id, "user", f"用户回答: {user_response}", "chat")
            
            # 清除 need_input 状态
            task.need_input.required = False
            task.need_input.question = ""
            task.need_input.options = []
            task.need_input.user_response = ""
            
            self._log(f"📥 从用户输入恢复，回答: {user_response}")
        else:
            # 检查 store 中是否已有消息历史（追加任务/重新运行的情况）
            stored_msgs = self.store.get_messages(task.id)
            if stored_msgs:
                # 已有历史：检查是否包含之前的 done/fail 结束消息
                # 如果任务之前已完成又被重启（追加TODO），只保留最后一轮结束后的新消息
                # 避免旧历史（含done/fail）污染新一轮上下文
                last_done_idx = -1
                for i, msg in enumerate(stored_msgs):
                    role = msg.get("role", "")
                    mtype = msg.get("type", "")
                    content = msg.get("content", "")
                    # done/fail 的执行结果消息
                    if role == "assistant" and mtype == "execution":
                        # 检查是否是完成/失败的总结消息（非工具执行结果）
                        prev = stored_msgs[i-1] if i > 0 else None
                        if prev and prev.get("role") == "assistant" and prev.get("type") == "chat":
                            prev_content = prev.get("content", "").strip()
                            if '"call": "done"' in prev_content or '"call": "fail"' in prev_content:
                                last_done_idx = i

                if last_done_idx >= 0:
                    # 任务曾经结束过，这是重启。不加载旧历史，当作新任务
                    self._log(f"检测到任务重启（追加TODO），清除旧上下文")
                    pending = [t for t in task.todo_items if not t.completed]
                    completed = [t for t in task.todo_items if t.completed]
                    user_desc = ""
                    if completed:
                        user_desc += "已完成的 TODO:\n" + "\n".join(f"- {t.content}" for t in completed) + "\n\n"
                    if pending:
                        user_desc += "待完成的 TODO:\n" + "\n".join(f"- {t.content}" for t in pending)
                    ctx.add_user(user_desc)
                    self.store.add_message(task.id, "user", user_desc, "chat")
                else:
                    # 正常恢复（无 done/fail），加载全部历史
                    for msg in stored_msgs:
                        role = msg.get("role", "")
                        content = msg.get("content", "")
                        if role == "system":
                            continue
                        elif role in ("user", "execution_result"):
                            ctx.add_user(content)
                        elif role == "assistant":
                            ctx.add_assistant(content)
            else:
                # 首次运行：正常构建初始上下文
                user_desc = task.description or ""
                if task.todo_items:
                    pending = [t.content for t in task.todo_items if not t.completed]
                    if pending:
                        # 如果 description 和 TODO 第一项相同（自动填充的），只用 TODO 列表
                        if user_desc.strip() == task.todo_items[0].content.strip():
                            user_desc = "待完成的 TODO:\n" + "\n".join(f"- {c}" for c in pending)
                        else:
                            user_desc += "\n\n待完成的 TODO:\n" + "\n".join(f"- {c}" for c in pending)
                
                ctx.add_user(user_desc)
                
                # 保存初始消息
                self.store.add_message(task.id, "system", sys_prompt, "system")
                self.store.add_message(task.id, "user", user_desc, "chat")
        
        # 更新任务状态
        task.update_status(TaskStatus.RUNNING, force=True)
        self._sync_todo_items(task)
        self.store.update_task(task)
        
        iteration = 0
        
        try:
            while iteration < self.config.max_iterations and not self._stop:
                iteration += 1
                self._log(f"\n{'='*40} 迭代 {iteration} {'='*40}")
                
                # ========== 阶段 1: 工具选择 ==========
                if ctx.phase == Phase.SELECT:
                    self._log("📋 阶段1: 选择工具...")
                    
                    messages = ctx.build_messages()
                    response = self._call_ai(messages, task)
                    
                    self._log(f"🤖 AI: {response[:300]}..." if len(response) > 300 else f"🤖 AI: {response}")
                    # 广播 AI 回复作为思考内容
                    task.last_thinking = response
                    self._emit_thinking(response)
                    ctx.add_assistant(response)
                    self.store.add_message(task.id, "assistant", response, "chat")
                    
                    # 检查是否直接调用了工具（跳过选择阶段）
                    call_data = parse_tool_call(response)
                    if call_data:
                        tool_name = call_data.get("call")
                        tool_def = get_tool(tool_name)
                        if tool_def:
                            ctx.selected_tools = [tool_name]
                            # 验证参数名是否正确
                            valid_param_names = {p.name for p in tool_def.params}
                            used_params = {k for k in call_data.keys() if k != "call"}
                            invalid_params = used_params - valid_param_names
                            if invalid_params:
                                # AI 用了不存在的参数名，注入工具描述让其重新调用
                                desc = get_compact_desc([tool_name])
                                fix_msg = (f"参数错误: {', '.join(invalid_params)} 不是 {tool_name} 的有效参数。\n"
                                           f"工具说明:\n{desc}\n\n"
                                           f"请重新调用，格式: {{\"call\": \"{tool_name}\", \"参数名\": \"值\"}}")
                                ctx.add_user(fix_msg)
                                self.store.add_message(task.id, "user", fix_msg, "system")
                                ctx.phase = Phase.PARAMS
                                self._log(f"   参数纠正: {invalid_params} 无效")
                            else:
                                ctx.phase = Phase.EXEC
                                self._log(f"   直接调用: {tool_name}")
                            # 继续到下一阶段
                        else:
                            error_msg = f"工具 '{tool_name}' 不存在，请从可用工具中选择"
                            ctx.add_user(error_msg)
                            self.store.add_message(task.id, "user", error_msg, "system")
                            continue
                    else:
                        # 解析工具选择
                        selected = parse_tool_select(response)
                        
                        # 过滤有效工具
                        valid = [n for n in selected if get_tool(n)]
                        
                        if not valid:
                            # 没有检测到任何工具名或 JSON → AI 选择了直接回复用户
                            # 检查是否含有 JSON 结构（可能是格式错误的调用）
                            has_json = bool(re.search(r'\{[^}]*"(?:select|call)"', response))
                            if has_json:
                                # 有 JSON 但解析失败，提示重新格式化
                                prompt_msg = "JSON 格式有误，请重新输入。选择工具: {\"select\": [\"工具名\"]}, 调用工具: {\"call\": \"工具名\", \"参数\": \"值\"}"
                                ctx.add_user(prompt_msg)
                                self.store.add_message(task.id, "user", prompt_msg, "system")
                                continue
                            
                            # 纯自然语言回复 → 视为对话式完成
                            # 但如果有未完成的 TODO，不允许直接结束
                            pending_todos = [t for t in task.todo_items if not t.completed]
                            if pending_todos:
                                pending_list = "\n".join(f"- [{t.id}] {t.content}" for t in pending_todos)
                                reject_msg = (f"还有 {len(pending_todos)} 个 TODO 未完成，请先使用工具完成任务：\n{pending_list}")
                                ctx.add_user(reject_msg)
                                self.store.add_message(task.id, "user", reject_msg, "system")
                                continue
                            
                            self._log("💬 AI 选择直接回复用户")
                            task.update_status(TaskStatus.COMPLETED, force=True)
                            task.progress = 100
                            task.current_step = response.strip()
                            self._sync_todo_items(task)
                            self.store.update_task(task)
                            self.store.add_message(task.id, "assistant", response.strip(), "execution")
                            return task
                        
                        ctx.selected_tools = valid
                        ctx.phase = Phase.PARAMS
                        self._log(f"   选中: {valid}")
                
                # ========== 阶段 2: 参数填写 ==========
                if ctx.phase == Phase.PARAMS:
                    self._log("📝 阶段2: 填写参数...")
                    
                    # 注入工具描述
                    desc = get_compact_desc(ctx.selected_tools)
                    tool_prompt = f"工具说明:\n{desc}\n\n请调用工具，格式: {{\"call\": \"工具名\", \"参数名\": \"值\"}}"
                    ctx.add_user(tool_prompt)
                    self.store.add_message(task.id, "user", tool_prompt, "system")
                    
                    messages = ctx.build_messages()
                    response = self._call_ai(messages, task)
                    
                    self._log(f"🤖 AI: {response[:300]}..." if len(response) > 300 else f"🤖 AI: {response}")
                    # 广播 AI 回复作为思考内容
                    task.last_thinking = response
                    self._emit_thinking(response)
                    ctx.add_assistant(response)
                    self.store.add_message(task.id, "assistant", response, "chat")
                    
                    # 解析调用
                    call_data = parse_tool_call(response)
                    
                    if not call_data:
                        format_msg = "请按格式调用工具: {\"call\": \"工具名\", \"参数名\": \"值\"}"
                        ctx.add_user(format_msg)
                        self.store.add_message(task.id, "user", format_msg, "system")
                        continue
                    
                    ctx.phase = Phase.EXEC
                
                # ========== 阶段 3: 执行 ==========
                if ctx.phase == Phase.EXEC:
                    import uuid as _uuid
                    import time as _time
                    # 从最后一条助手消息中解析所有调用（支持一次返回多个工具调用）
                    last_msg = ctx.get_last_assistant_msg()
                    all_calls = parse_all_tool_calls(last_msg) if last_msg else []
                    
                    if not all_calls:
                        ctx.reset_phase()
                        continue
                    
                    self._log(f"   解析到 {len(all_calls)} 个工具调用")
                    
                    _should_return = False
                    _should_continue = False
                    
                    for _call_idx, call_data in enumerate(all_calls):
                        tool_name = call_data.get("call")
                        tool_args = {k: v for k, v in call_data.items() if k != "call"}
                        
                        self._log(f"🔧 执行 [{_call_idx+1}/{len(all_calls)}]: {tool_name}({tool_args})")
                        
                        # 生成唯一调用ID和计时
                        call_id = _uuid.uuid4().hex[:8]
                        t_start = _time.perf_counter()
                        
                        # 广播工具开始
                        if self.on_tool_start and tool_name not in ("done", "fail", "ask", "get_context"):
                            try:
                                self.on_tool_start(task.id, tool_name, tool_args, call_id)
                            except Exception:
                                pass
                        
                        result = await self._exec_tool(tool_name, tool_args, cwd)
                        
                        duration_ms = (_time.perf_counter() - t_start) * 1000
                        
                        # 广播工具结束
                        if self.on_tool_end and tool_name not in ("done", "fail", "ask", "get_context"):
                            try:
                                result_data = {"success": result.success, "error": result.error}
                                self.on_tool_end(task.id, tool_name, result_data, call_id, duration_ms)
                            except Exception:
                                pass
                        
                        # 控制工具处理
                        if tool_name == "done":
                            # 检查是否还有未完成的 TODO
                            pending_todos = [t for t in task.todo_items if not t.completed]
                            if pending_todos:
                                pending_list = "\n".join(f"- [{t.id}] {t.content}" for t in pending_todos)
                                done_count = sum(1 for t in task.todo_items if t.completed)
                                total_count = len(task.todo_items)
                                reject_msg = (f"⚠️ 还有 {len(pending_todos)} 个 TODO 未完成 ({done_count}/{total_count})，"
                                              f"不能结束任务！请继续完成以下 TODO：\n{pending_list}\n\n"
                                              f"完成每个 TODO 后调用 update_todo，全部完成后再调用 done。")
                                ctx.add_user(reject_msg)
                                self.store.add_message(task.id, "user", reject_msg, "system")
                                self._log(f"⚠️ 拒绝 done: 还有 {len(pending_todos)} 个 TODO 未完成")
                                ctx.reset_phase()
                                _should_continue = True
                                break

                            task.update_status(TaskStatus.COMPLETED, force=True)
                            task.progress = 100
                            summary = result.data.get("summary", "") if result.data else ""
                            # 清洗冗余前缀后再存储
                            clean_summary = (summary or "").strip()
                            for pfx in ("任务完成:", "完成:", "任务:"):
                                if clean_summary.startswith(pfx):
                                    clean_summary = clean_summary[len(pfx):].strip()
                            if not clean_summary:
                                clean_summary = "完成"
                            task.current_step = clean_summary
                            self._sync_todo_items(task)
                            self.store.update_task(task)
                            self.store.add_message(task.id, "assistant", clean_summary, "execution")
                            self._log(f"✅ {clean_summary}")
                            _should_return = True
                            break
                        
                        if tool_name == "fail":
                            task.update_status(TaskStatus.FAILED, force=True)
                            task.error_message = result.error
                            self._sync_todo_items(task)
                            self.store.update_task(task)
                            error_msg = (result.error or "").strip()
                            if error_msg.startswith("任务失败:"):
                                error_msg = error_msg[len("任务失败:"):].strip()
                            if not error_msg:
                                error_msg = "失败"
                            self.store.add_message(task.id, "assistant", error_msg, "execution")
                            self._log(f"❌ {error_msg}")
                            _should_return = True
                            break
                        
                        if tool_name == "ask":
                            task.update_status(TaskStatus.NEED_INPUT, force=True)
                            question = result.data.get("question", "") if result.data else ""
                            options = result.data.get("options", []) if result.data else []
                            task.need_input.required = True
                            task.need_input.question = question
                            task.need_input.options = options if isinstance(options, list) else []
                            self._sync_todo_items(task)
                            self.store.update_task(task)
                            clean_question = (question or "").strip()
                            if clean_question.startswith("等待用户输入:"):
                                clean_question = clean_question[len("等待用户输入:"):].strip()
                            if not clean_question:
                                clean_question = "等待用户输入"
                            self.store.add_message(task.id, "assistant", clean_question, "execution")
                            self._log(f"❓ {clean_question}")
                            _should_return = True
                            break
                        
                        if tool_name == "update_todo":
                            todo_id = result.data.get("todo_id", "") if result.data else ""
                            found = False
                            for item in task.todo_items:
                                if item.id == todo_id and not item.completed:
                                    item.completed = True
                                    found = True
                                    break
                            if found:
                                done_count = sum(1 for t in task.todo_items if t.completed)
                                total_count = len(task.todo_items)
                                task.progress = int(done_count / total_count * 100) if total_count > 0 else 0
                                self._sync_todo_items(task)
                                self.store.update_task(task)
                                # 列出剩余 TODO，让 AI 明确知道下一步该做什么
                                pending = [t for t in task.todo_items if not t.completed]
                                if pending:
                                    pending_list = "\n".join(f"- [{t.id}] {t.content}" for t in pending)
                                    feedback = (f"✅ TODO 已完成 ({done_count}/{total_count})\n"
                                                f"\n剩余待完成 TODO：\n{pending_list}\n"
                                                f"请继续完成下一个 TODO。")
                                else:
                                    feedback = f"✅ 所有 TODO 已完成 ({done_count}/{total_count})，现在可以调用 done 结束任务。"
                                ctx.add_user(feedback)
                                todo_meta = {"tool": "update_todo", "args": {"todo_id": todo_id}, "success": True, "call_id": call_id, "duration_ms": round(duration_ms, 1)}
                                self.store.add_message(task.id, "execution_result", f"TODO 已完成 ({done_count}/{total_count})", "execution", metadata=todo_meta)
                                self._log(f"✅ TODO {todo_id} 已完成 ({done_count}/{total_count})")
                            else:
                                ctx.add_user(f"⚠️ 未找到 TODO 项 {todo_id} 或已完成")
                                todo_meta = {"tool": "update_todo", "args": {"todo_id": todo_id}, "success": False, "call_id": call_id, "duration_ms": round(duration_ms, 1)}
                                self.store.add_message(task.id, "execution_result", f"未找到 TODO 项 {todo_id}", "execution", metadata=todo_meta)
                            # update_todo 后继续处理下一个调用
                            continue
                        
                        if tool_name == "get_context":
                            # 从 store 加载历史消息，生成摘要
                            last_n = result.data.get("last_n") if result.data else None
                            context_summary = self._build_context_summary(task.id, last_n)
                            ctx.add_user(context_summary)
                            ctx_meta = {"tool": "get_context", "args": tool_args, "success": True, "call_id": call_id, "duration_ms": round(duration_ms, 1)}
                            self.store.add_message(task.id, "execution_result", context_summary, "execution", metadata=ctx_meta)
                            self._log(f"📜 返回上下文摘要 ({len(context_summary)} 字符)")
                            continue
                        
                        # 普通工具：构建结构化元数据
                        tool_metadata = {
                            "tool": tool_name,
                            "args": tool_args,
                            "success": result.success,
                            "call_id": call_id,
                            "duration_ms": round(duration_ms, 1),
                        }
                        
                        # 添加结果到上下文
                        result_text = self._format_result(tool_name, result)
                        ctx.add_user(result_text)
                        self.store.add_message(task.id, "execution_result", result_text, "execution", metadata=tool_metadata)
                        
                        self._log(f"   结果: {result_text[:200]}..." if len(result_text) > 200 else f"   结果: {result_text}")
                        
                        # 需求8: 记录 FileOperation / CommandResult
                        FILE_TOOLS = {"create_file", "write_file", "delete_file", "move_file", "copy_file"}
                        DIR_TOOLS = {"create_directory", "delete_directory", "move_directory"}
                        EDIT_TOOLS = {"replace_range", "insert_text", "delete_range", "apply_patch"}
                        CMD_TOOLS = {"create_task"}
                        
                        if tool_name in FILE_TOOLS | DIR_TOOLS | EDIT_TOOLS:
                            from .models import FileOperation
                            op_type = tool_name.replace("_file", "").replace("_directory", "_dir")
                            path = tool_args.get("path") or tool_args.get("source") or ""
                            task.add_file_operation(FileOperation(
                                operation_type=op_type,
                                path=path,
                                success=result.success,
                                message=result_text[:200],
                            ))
                        
                        if tool_name in CMD_TOOLS:
                            from .models import CommandResult
                            cmd = tool_args.get("command", "")
                            data = result.data or {}
                            task.add_command_result(CommandResult(
                                command=cmd,
                                return_code=data.get("return_code", -1 if not result.success else 0),
                                stdout=data.get("stdout", ""),
                                stderr=data.get("stderr", ""),
                                execution_time=duration_ms / 1000,
                                success=result.success,
                            ))
                        
                        # 更新进度
                        task.progress = min(90, task.progress + 10)
                        self._sync_todo_items(task)
                        self.store.update_task(task)
                        # 普通工具执行后继续处理下一个调用
                    
                    # 循环结束后判断是否需要 return 或 continue 外层循环
                    if _should_return:
                        return task
                    if _should_continue:
                        continue
                    
                    # 所有调用处理完毕，重置到选择阶段
                    ctx.reset_phase()
            
            # 达到最大迭代
            if iteration >= self.config.max_iterations:
                task.update_status(TaskStatus.FAILED, force=True)
                task.error_message = f"达到最大迭代次数 ({self.config.max_iterations})"
                self._sync_todo_items(task)
                self.store.update_task(task)
                self._log(f"⚠️ 达到最大迭代次数")
        
        except Exception as e:
            task.update_status(TaskStatus.FAILED, force=True)
            task.error_message = str(e)
            self._sync_todo_items(task)
            self.store.update_task(task)
            self._log(f"❌ 异常: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            if self._mcp:
                await self._mcp.disconnect()
                self._mcp = None
        
        return task
    
    def _call_ai(self, messages: List[Dict], task: Optional[Task] = None) -> str:
        """调用 AI（不传 tools 参数！）"""
        # 不传 tools 参数，纯文本交互
        response = self.api.chat(messages, stream=False)
        
        # 累计 token 用量到任务
        if task is not None and hasattr(self.api, 'last_usage') and self.api.last_usage:
            usage = self.api.last_usage
            task.token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            task.token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            task.token_usage["total_tokens"] += usage.get("total_tokens", 0)
        
        if isinstance(response, dict):
            return response.get("content", "") or response.get("message", "") or str(response)
        return str(response) if response else ""
    
    def _format_result(self, tool: str, result: MCPResult) -> str:
        """格式化执行结果"""
        if result.success:
            data = result.data or {}
            # 截断长内容（read_file 使用更大阈值避免反复读取）
            data_str = json.dumps(data, ensure_ascii=False, indent=2)
            max_len = 6000 if tool == "read_file" else 1500
            if len(data_str) > max_len:
                original_size = len(data_str)
                data_str = data_str[:max_len]
                truncate_hint = (
                    f"\n...(已截断，原始大小约 {original_size} 字符)"
                )
                # 针对 read_file 给出分段读取提示
                if tool == "read_file":
                    truncate_hint += (
                        f"\n提示: 如需读取更多内容，请使用 range 参数分段读取，"
                        f"例如: {{\"call\": \"read_file\", \"path\": \"...\", \"range\": [5000, 10000]}}"
                    )
                data_str += truncate_hint
            return f"{tool} 成功:\n```\n{data_str}\n```"
        else:
            return f"{tool} 失败: {result.error}"
    
    def stop(self):
        """停止执行"""
        self._stop = True
    
    async def continue_with_input(self, task: Task, user_input: str) -> Task:
        """
        继续执行（用户提供输入后）
        
        Args:
            task: 处于 NEED_INPUT 状态的任务
            user_input: 用户的输入
        
        Returns:
            更新后的任务
        """
        if task.status != TaskStatus.NEED_INPUT:
            return task
        
        # 记录用户输入（不修改 description，由 run() 从 store 恢复上下文）
        task.set_user_input(user_input)
        task.need_input.user_response = user_input
        
        return await self.run(task)
