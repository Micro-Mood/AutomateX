"""
精简工具定义
============

格式: 名称|描述|参数列表
参数: name:type*说明 (必填) 或 name:type=default,说明 (可选)
"""

from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class ToolParam:
    """工具参数"""
    name: str
    type: str  # str, int, bool, list
    desc: str
    required: bool = True
    default: Optional[str] = None


@dataclass
class Tool:
    """工具定义"""
    name: str
    desc: str
    params: List[ToolParam]
    category: str  # read, search, edit, exec, ctrl
    
    def to_compact(self) -> str:
        """转换为精简格式"""
        param_strs = []
        for p in self.params:
            if p.required:
                param_strs.append(f"{p.name}:{p.type}*{p.desc}")
            else:
                default = p.default or ""
                param_strs.append(f"{p.name}:{p.type}={default},{p.desc}")
        
        params_part = ";".join(param_strs) if param_strs else ""
        return f"{self.name}|{self.desc}|{params_part}"


# ==================== 工具注册表 ====================

TOOLS: Dict[str, Tool] = {}


def register(name: str, desc: str, params: List[ToolParam], category: str = "general"):
    """注册工具"""
    TOOLS[name] = Tool(name, desc, params, category)


def get_tool(name: str) -> Optional[Tool]:
    """获取工具"""
    return TOOLS.get(name)


def get_names_by_category() -> Dict[str, List[str]]:
    """按分类获取工具名称"""
    result: Dict[str, List[str]] = {}
    for name, tool in TOOLS.items():
        if tool.category not in result:
            result[tool.category] = []
        result[tool.category].append(name)
    return result


def get_compact_desc(names: List[str]) -> str:
    """获取指定工具的精简描述"""
    lines = []
    for name in names:
        tool = TOOLS.get(name)
        if tool:
            lines.append(tool.to_compact())
    return "\n".join(lines)


def get_names_of_category(category: str) -> List[str]:
    """获取某个分类下的所有工具名称"""
    return [name for name, tool in TOOLS.items() if tool.category == category]


def get_all_names() -> List[str]:
    """获取所有工具名称"""
    return list(TOOLS.keys())


# ==================== 注册所有工具 ====================

def _init_tools():
    """初始化工具注册 - 与MCP服务器完全匹配"""
    
    # ==================== 读取类工具 (read) ====================
    register("read_file", "读取文件内容", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("range", "list", "读取字节范围[start,end]", False),
        ToolParam("max_size", "int", "最大读取字节数", False, "1048576"),
    ], "read")
    
    register("list_directory", "列出目录内容", [
        ToolParam("path", "str", "目录路径"),
        ToolParam("limit", "int", "最大返回条目数", False, "100"),
        ToolParam("offset", "int", "分页偏移量", False, "0"),
        ToolParam("pattern", "str", "文件名匹配模式", False),
        ToolParam("recursive", "bool", "是否递归列出", False, "false"),
        ToolParam("max_depth", "int", "最大递归深度", False, "3"),
        ToolParam("include_hidden", "bool", "是否包含隐藏文件", False, "false"),
        ToolParam("sort_by", "str", "排序字段(name/size/modified/created)", False, "name"),
        ToolParam("sort_order", "str", "排序顺序(asc/desc)", False, "asc"),
    ], "read")
    
    register("stat_path", "获取路径状态信息", [
        ToolParam("path", "str", "文件或目录路径"),
        ToolParam("follow_symlinks", "bool", "是否跟随符号链接", False, "true"),
    ], "read")
    
    register("exists", "检查路径是否存在", [
        ToolParam("path", "str", "要检查的路径"),
    ], "read")
    
    # ==================== 搜索类工具 (search) ====================
    register("search_files", "搜索文件", [
        ToolParam("pattern", "str", "文件名模式(通配符或正则)"),
        ToolParam("root_dir", "str", "搜索根目录"),
        ToolParam("max_results", "int", "最大结果数", False, "100"),
        ToolParam("recursive", "bool", "是否递归搜索", False, "true"),
        ToolParam("file_types", "list", "文件类型过滤", False),
        ToolParam("exclude_patterns", "list", "排除模式", False),
        ToolParam("min_size", "int", "最小文件大小", False),
        ToolParam("max_size", "int", "最大文件大小", False),
        ToolParam("modified_after", "str", "修改时间下限(ISO格式)", False),
        ToolParam("modified_before", "str", "修改时间上限(ISO格式)", False),
    ], "search")
    
    register("search_content", "搜索文件内容", [
        ToolParam("query", "str", "搜索文本或正则表达式"),
        ToolParam("root_dir", "str", "搜索根目录"),
        ToolParam("file_pattern", "str", "文件模式过滤", False),
        ToolParam("max_files", "int", "最大扫描文件数", False, "50"),
        ToolParam("max_matches_per_file", "int", "每文件最大匹配数", False, "10"),
        ToolParam("case_sensitive", "bool", "是否区分大小写", False, "false"),
        ToolParam("whole_word", "bool", "是否全词匹配", False, "false"),
        ToolParam("encoding", "str", "文件编码", False, "auto"),
        ToolParam("context_lines", "int", "上下文行数", False, "2"),
        ToolParam("is_regex", "bool", "是否正则表达式", False, "false"),
        ToolParam("multiline", "bool", "是否多行匹配", False, "false"),
    ], "search")
    
    register("search_symbol", "搜索代码符号", [
        ToolParam("symbol", "str", "符号名称"),
        ToolParam("root_dir", "str", "搜索根目录"),
        ToolParam("symbol_type", "str", "符号类型(function/class/variable/import)", False),
        ToolParam("language", "str", "编程语言", False),
        ToolParam("max_results", "int", "最大结果数", False, "50"),
        ToolParam("include_definitions", "bool", "包含定义", False, "true"),
        ToolParam("include_references", "bool", "包含引用", False, "false"),
        ToolParam("exact_match", "bool", "精确匹配", False, "true"),
    ], "search")
    
    # ==================== 编辑类工具 - 目录操作 (edit) ====================
    register("create_directory", "创建目录", [
        ToolParam("path", "str", "目录路径"),
        ToolParam("recursive", "bool", "是否创建父目录", False, "true"),
        ToolParam("mode", "str", "权限模式", False, "755"),
    ], "edit")
    
    register("delete_directory", "删除目录", [
        ToolParam("path", "str", "目录路径"),
        ToolParam("recursive", "bool", "是否递归删除", False, "false"),
        ToolParam("force", "bool", "是否强制删除只读文件", False, "false"),
    ], "edit")
    
    register("move_directory", "移动/重命名目录", [
        ToolParam("source", "str", "源目录路径"),
        ToolParam("destination", "str", "目标目录路径"),
        ToolParam("overwrite", "bool", "是否覆盖已存在目录", False, "false"),
        ToolParam("copy_permissions", "bool", "是否复制权限", False, "true"),
    ], "edit")
    
    # ==================== 编辑类工具 - 文件操作 (edit) ====================
    register("create_file", "创建文件", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("content", "str", "文件内容", False, ""),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("overwrite", "bool", "是否覆盖已存在文件", False, "false"),
    ], "edit")
    
    register("write_file", "写入文件内容(覆盖)", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("content", "str", "文件内容"),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("create_parents", "bool", "是否创建父目录", False, "true"),
    ], "edit")
    
    register("delete_file", "删除文件", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("backup", "bool", "是否创建备份", False, "false"),
        ToolParam("backup_dir", "str", "备份目录", False),
        ToolParam("permanent", "bool", "是否永久删除", False, "false"),
    ], "edit")
    
    register("move_file", "移动/重命名文件", [
        ToolParam("source", "str", "源文件路径"),
        ToolParam("destination", "str", "目标文件路径"),
        ToolParam("overwrite", "bool", "是否覆盖已存在文件", False, "false"),
        ToolParam("copy_permissions", "bool", "是否复制权限", False, "true"),
        ToolParam("preserve_timestamps", "bool", "是否保留时间戳", False, "true"),
    ], "edit")
    
    register("copy_file", "复制文件", [
        ToolParam("source", "str", "源文件路径"),
        ToolParam("destination", "str", "目标文件路径"),
        ToolParam("overwrite", "bool", "是否覆盖已存在文件", False, "false"),
    ], "edit")
    
    # ==================== 编辑类工具 - 内容编辑 (edit) ====================
    register("replace_range", "替换文本范围", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("range", "list", "替换范围[start,end]"),
        ToolParam("new_text", "str", "新文本"),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("unit", "str", "范围单位(bytes/chars)", False, "bytes"),
    ], "edit")
    
    register("insert_text", "在指定位置插入文本", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("position", "int", "插入位置(行号从1开始,或字节/字符偏移量)"),
        ToolParam("text", "str", "要插入的文本"),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("unit", "str", "位置单位(bytes/chars/line)", False, "line"),
    ], "edit")
    
    register("delete_range", "删除指定范围的文本", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("range", "list", "删除范围[start,end]"),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("unit", "str", "范围单位(bytes/chars)", False, "bytes"),
    ], "edit")
    
    register("apply_patch", "应用统一diff补丁", [
        ToolParam("path", "str", "文件路径"),
        ToolParam("patch", "str", "补丁内容"),
        ToolParam("encoding", "str", "文件编码", False, "utf-8"),
        ToolParam("dry_run", "bool", "是否试运行", False, "false"),
        ToolParam("reverse", "bool", "是否反向应用", False, "false"),
    ], "edit")
    
    # ==================== 执行类工具 - 任务管理 (exec) ====================
    register("create_task", "创建任务(Windows下避免python -c,请先create_file写脚本再执行)", [
        ToolParam("command", "str", "要执行的命令"),
        ToolParam("args", "list", "命令行参数", False),
        ToolParam("cwd", "str", "工作目录", False),
        ToolParam("env", "dict", "环境变量", False),
        ToolParam("shell", "bool", "是否在shell中执行", False, "true"),
        ToolParam("timeout", "int", "超时毫秒数", False),
        ToolParam("stdin", "str", "标准输入初始内容", False),
        ToolParam("detached", "bool", "是否分离模式", False, "false"),
        ToolParam("priority", "str", "进程优先级(low/normal/high/realtime)", False, "normal"),
    ], "exec")
    
    register("start_task", "启动任务", [
        ToolParam("task_id", "str", "任务ID"),
    ], "exec")
    
    register("stop_task", "优雅停止任务", [
        ToolParam("task_id", "str", "任务ID"),
        ToolParam("signal_name", "str", "终止信号(CTRL_C/CTRL_BREAK)", False, "CTRL_C"),
        ToolParam("timeout", "int", "等待超时(毫秒)", False, "5000"),
    ], "exec")
    
    register("kill_task", "强制终止任务", [
        ToolParam("task_id", "str", "任务ID"),
    ], "exec")
    
    register("get_task", "获取任务状态", [
        ToolParam("task_id", "str", "任务ID"),
    ], "exec")
    
    register("list_tasks", "列出所有任务", [
        ToolParam("filter", "str", "过滤条件(all/active/completed/failed)", False, "all"),
        ToolParam("limit", "int", "最大返回数", False, "50"),
    ], "exec")
    
    register("wait_task", "等待任务完成", [
        ToolParam("task_id", "str", "任务ID"),
        ToolParam("timeout", "int", "等待超时毫秒数,建议至少5000(=5秒),0为无限等待", False, "30000"),
    ], "exec")
    
    # ==================== 执行类工具 - 输入输出 (exec) ====================
    register("write_stdin", "写入标准输入", [
        ToolParam("task_id", "str", "任务ID"),
        ToolParam("data", "str", "要写入的数据"),
        ToolParam("encoding", "str", "数据编码", False, "utf-8"),
        ToolParam("eof", "bool", "是否发送EOF", False, "false"),
    ], "exec")
    
    register("stream_stdout", "流式读取标准输出", [
        ToolParam("task_id", "str", "任务ID"),
        ToolParam("max_bytes", "int", "最大读取字节数", False, "8192"),
        ToolParam("timeout", "int", "读取超时(毫秒)", False, "1000"),
        ToolParam("encoding", "str", "输出编码", False, "utf-8"),
    ], "exec")
    
    register("stream_stderr", "流式读取标准错误", [
        ToolParam("task_id", "str", "任务ID"),
        ToolParam("max_bytes", "int", "最大读取字节数", False, "8192"),
        ToolParam("timeout", "int", "读取超时(毫秒)", False, "1000"),
        ToolParam("encoding", "str", "输出编码", False, "utf-8"),
    ], "exec")
    
    # ==================== 控制类工具 (ctrl) ====================
    register("done", "完成任务", [
        ToolParam("summary", "str", "完成摘要"),
    ], "ctrl")
    
    register("fail", "任务失败", [
        ToolParam("reason", "str", "失败原因"),
    ], "ctrl")
    
    register("ask", "询问用户", [
        ToolParam("question", "str", "问题"),
        ToolParam("options", "list", "可选项列表", False),
    ], "ctrl")
    
    register("update_todo", "标记一个 TODO 项为已完成", [
        ToolParam("todo_id", "str", "TODO 项的 ID"),
    ], "ctrl")
    
    register("get_context", "查看之前的对话历史摘要(当不清楚之前做了什么操作时使用)", [
        ToolParam("last_n", "int", "查看最近N轮对话(默认全部)", False),
    ], "ctrl")


# 模块加载时初始化
_init_tools()
