"""
MCP命令行工具
提供命令行界面来操作MCP服务器
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.syntax import Syntax
from rich.panel import Panel

from src.mcp.core.config import MCPConfig
from src.mcp.server import MCPServer, run_server

console = Console()


@click.group()
@click.version_option(version="1.0.0", prog_name="MCP")
def main():
    """Windows MCP (Model Context Protocol) 命令行工具"""
    pass


# ==================== 服务器命令 ====================

@main.group()
def server():
    """服务器管理命令"""
    pass


@server.command("start")
@click.option("--mode", "-m", type=click.Choice(["tcp", "stdio"]), default="tcp", help="运行模式")
@click.option("--config", "-c", type=click.Path(exists=True), help="配置文件路径")
@click.option("--host", "-h", default="127.0.0.1", help="监听地址")
@click.option("--port", "-p", type=int, default=8080, help="监听端口")
def server_start(mode: str, config: Optional[str], host: str, port: int):
    """启动MCP服务器"""
    console.print(f"[green]启动MCP服务器...[/green]")
    console.print(f"  模式: {mode}")
    console.print(f"  地址: {host}:{port}")
    
    if config:
        console.print(f"  配置: {config}")
    
    try:
        run_server(mode, config)
    except KeyboardInterrupt:
        console.print("\n[yellow]服务器已停止[/yellow]")


@server.command("init")
@click.option("--output", "-o", type=click.Path(), default="mcp.json", help="配置文件输出路径")
def server_init(output: str):
    """初始化配置文件"""
    config = MCPConfig()
    config.to_file(output)
    console.print(f"[green]配置文件已创建: {output}[/green]")


# ==================== 文件操作命令 ====================

@main.group()
def file():
    """文件操作命令"""
    pass


@file.command("read")
@click.argument("path")
@click.option("--encoding", "-e", default="utf-8", help="文件编码")
@click.option("--start", "-s", type=int, help="起始字节")
@click.option("--end", type=int, help="结束字节")
def file_read(path: str, encoding: str, start: Optional[int], end: Optional[int]):
    """读取文件内容"""
    from src.mcp.modules.read import read_file
    
    async def do_read():
        range_param = (start, end) if start is not None and end is not None else None
        result = await read_file(path, encoding, range_param)
        return result
    
    result = asyncio.run(do_read())
    
    if result["status"] == "success":
        content = result["data"]["content"]
        
        # 尝试语法高亮
        ext = Path(path).suffix.lstrip('.')
        if ext in ['py', 'js', 'ts', 'json', 'yaml', 'yml', 'md', 'html', 'css']:
            syntax = Syntax(content, ext, theme="monokai", line_numbers=True)
            console.print(syntax)
        else:
            console.print(content)
        
        console.print(f"\n[dim]大小: {result['data']['size']} 字节, 编码: {result['data']['encoding']}[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@file.command("list")
@click.argument("path", default=".")
@click.option("--recursive", "-r", is_flag=True, help="递归列出")
@click.option("--pattern", "-p", help="文件名模式")
@click.option("--hidden", "-a", is_flag=True, help="显示隐藏文件")
def file_list(path: str, recursive: bool, pattern: Optional[str], hidden: bool):
    """列出目录内容"""
    from src.mcp.modules.read import list_directory
    
    async def do_list():
        return await list_directory(
            path,
            recursive=recursive,
            pattern=pattern,
            include_hidden=hidden
        )
    
    result = asyncio.run(do_list())
    
    if result["status"] == "success":
        table = Table(title=f"目录: {result['data']['path']}")
        table.add_column("名称", style="cyan")
        table.add_column("类型", style="green")
        table.add_column("大小", justify="right")
        table.add_column("修改时间")
        
        for item in result["data"]["items"]:
            size = f"{item['size']:,}" if item["type"] == "file" else "-"
            table.add_row(
                item["name"],
                item["type"],
                size,
                item["modified"][:19]
            )
        
        console.print(table)
        
        pagination = result["data"]["pagination"]
        console.print(f"\n[dim]显示 {len(result['data']['items'])} / {pagination['total']} 项[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@file.command("create")
@click.argument("path")
@click.option("--content", "-c", default="", help="文件内容")
@click.option("--encoding", "-e", default="utf-8", help="文件编码")
def file_create(path: str, content: str, encoding: str):
    """创建文件"""
    from src.mcp.modules.edit import create_file
    
    async def do_create():
        return await create_file(path, content, encoding)
    
    result = asyncio.run(do_create())
    
    if result["status"] == "success":
        console.print(f"[green]文件已创建: {result['data']['path']}[/green]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@file.command("delete")
@click.argument("path")
@click.option("--backup", "-b", is_flag=True, help="创建备份")
@click.confirmation_option(prompt="确定要删除文件吗?")
def file_delete(path: str, backup: bool):
    """删除文件"""
    from src.mcp.modules.edit import delete_file
    
    async def do_delete():
        return await delete_file(path, backup=backup)
    
    result = asyncio.run(do_delete())
    
    if result["status"] == "success":
        console.print(f"[green]文件已删除: {result['data']['path']}[/green]")
        if result['data'].get('backup_path'):
            console.print(f"[dim]备份: {result['data']['backup_path']}[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


# ==================== 搜索命令 ====================

@main.group()
def search():
    """搜索命令"""
    pass


@search.command("files")
@click.argument("pattern")
@click.option("--root", "-r", default=".", help="搜索根目录")
@click.option("--max-results", "-n", type=int, default=100, help="最大结果数")
@click.option("--type", "-t", "file_types", multiple=True, help="文件类型过滤")
def search_files_cmd(pattern: str, root: str, max_results: int, file_types):
    """搜索文件"""
    from src.mcp.modules.search import search_files
    
    async def do_search():
        return await search_files(
            pattern, root,
            max_results=max_results,
            file_types=list(file_types) if file_types else None
        )
    
    result = asyncio.run(do_search())
    
    if result["status"] == "success":
        table = Table(title=f"搜索结果: {pattern}")
        table.add_column("文件", style="cyan")
        table.add_column("目录", style="dim")
        table.add_column("大小", justify="right")
        
        for item in result["data"]["results"]:
            table.add_row(
                item["name"],
                item["directory"],
                f"{item['size']:,}"
            )
        
        console.print(table)
        
        stats = result["data"]["statistics"]
        console.print(f"\n[dim]找到 {stats['total_found']} 个文件, 扫描 {stats['total_scanned']} 个文件, 耗时 {stats['time_elapsed_ms']:.0f}ms[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@search.command("content")
@click.argument("query")
@click.option("--root", "-r", default=".", help="搜索根目录")
@click.option("--pattern", "-p", help="文件模式过滤")
@click.option("--case-sensitive", "-c", is_flag=True, help="区分大小写")
@click.option("--regex", is_flag=True, help="使用正则表达式")
def search_content_cmd(query: str, root: str, pattern: Optional[str], case_sensitive: bool, regex: bool):
    """搜索文件内容"""
    from src.mcp.modules.search import search_content
    
    async def do_search():
        return await search_content(
            query, root,
            file_pattern=pattern,
            case_sensitive=case_sensitive,
            is_regex=regex
        )
    
    result = asyncio.run(do_search())
    
    if result["status"] == "success":
        for file_result in result["data"]["results"]:
            console.print(f"\n[cyan]{file_result['path']}[/cyan]")
            
            for match in file_result["matches"]:
                console.print(f"  [dim]行 {match['line']}:[/dim] {match['text']}")
        
        stats = result["data"]["statistics"]
        console.print(f"\n[dim]找到 {stats['total_matches']} 个匹配, 在 {stats['files_with_matches']} 个文件中, 耗时 {stats['time_elapsed_ms']:.0f}ms[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


# ==================== 任务命令 ====================

@main.group()
def task():
    """任务管理命令"""
    pass


@task.command("run")
@click.argument("command")
@click.option("--cwd", "-d", help="工作目录")
@click.option("--wait", "-w", is_flag=True, help="等待完成")
@click.option("--timeout", "-t", type=int, help="超时时间(毫秒)")
def task_run(command: str, cwd: Optional[str], wait: bool, timeout: Optional[int]):
    """运行命令"""
    from src.mcp.modules.execute import create_task, start_task, wait_task
    
    async def do_run():
        # 创建任务
        create_result = await create_task(command, cwd=cwd, timeout=timeout)
        if create_result["status"] != "success":
            return create_result
        
        task_id = create_result["data"]["task_id"]
        console.print(f"[dim]任务ID: {task_id}[/dim]")
        
        # 启动任务
        start_result = await start_task(task_id)
        if start_result["status"] != "success":
            return start_result
        
        console.print(f"[dim]PID: {start_result['data']['pid']}[/dim]")
        
        # 等待完成
        if wait:
            return await wait_task(task_id, timeout or 0)
        
        return start_result
    
    result = asyncio.run(do_run())
    
    if result["status"] == "success":
        if "stdout" in result["data"]:
            console.print("\n[cyan]输出:[/cyan]")
            console.print(result["data"]["stdout"])
            
            if result["data"]["stderr"]:
                console.print("\n[red]错误:[/red]")
                console.print(result["data"]["stderr"])
        
        console.print(f"\n[green]状态: {result['data']['state']}[/green]")
        if result["data"].get("exit_code") is not None:
            console.print(f"[dim]退出码: {result['data']['exit_code']}[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@task.command("list")
@click.option("--filter", "-f", type=click.Choice(["all", "active", "completed", "failed"]), default="all")
def task_list(filter: str):
    """列出任务"""
    from src.mcp.modules.execute import list_tasks
    
    async def do_list():
        return await list_tasks(filter=filter)
    
    result = asyncio.run(do_list())
    
    if result["status"] == "success":
        table = Table(title="任务列表")
        table.add_column("ID", style="cyan")
        table.add_column("命令")
        table.add_column("状态", style="green")
        table.add_column("退出码", justify="right")
        table.add_column("耗时")
        
        for task in result["data"]["tasks"]:
            duration = f"{task['duration_ms']:.0f}ms" if task.get("duration_ms") else "-"
            exit_code = str(task.get("exit_code", "-"))
            table.add_row(
                task["task_id"],
                task["command"][:40],
                task["state"],
                exit_code,
                duration
            )
        
        console.print(table)
        console.print(f"\n[dim]活动任务: {result['data']['active_count']} / 总计: {result['data']['total']}[/dim]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@task.command("stop")
@click.argument("task_id")
def task_stop(task_id: str):
    """停止任务"""
    from src.mcp.modules.execute import stop_task
    
    async def do_stop():
        return await stop_task(task_id)
    
    result = asyncio.run(do_stop())
    
    if result["status"] == "success":
        console.print(f"[green]任务已停止: {task_id}[/green]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


@task.command("kill")
@click.argument("task_id")
def task_kill(task_id: str):
    """强制终止任务"""
    from src.mcp.modules.execute import kill_task
    
    async def do_kill():
        return await kill_task(task_id)
    
    result = asyncio.run(do_kill())
    
    if result["status"] == "success":
        console.print(f"[green]任务已终止: {task_id}[/green]")
    else:
        console.print(f"[red]错误: {result['error']['message']}[/red]")


# ==================== 工具命令 ====================

@main.command()
@click.argument("request", required=False)
@click.option("--method", "-m", help="RPC方法名")
@click.option("--params", "-p", help="JSON参数")
def call(request: Optional[str], method: Optional[str], params: Optional[str]):
    """调用MCP RPC方法"""
    if request:
        # 直接传入JSON-RPC请求
        request_data = request
    elif method:
        # 构建请求
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": json.loads(params) if params else {},
            "id": 1
        }
        request_data = json.dumps(req)
    else:
        console.print("[red]请提供请求数据或方法名[/red]")
        return
    
    async def do_call():
        server = MCPServer()
        return await server.handle_request(request_data)
    
    response = asyncio.run(do_call())
    result = json.loads(response)
    
    # 美化输出
    syntax = Syntax(
        json.dumps(result, indent=2, ensure_ascii=False),
        "json",
        theme="monokai"
    )
    console.print(syntax)


@main.command()
def methods():
    """列出所有可用方法"""
    server = MCPServer()
    
    table = Table(title="可用MCP方法")
    table.add_column("模块", style="cyan")
    table.add_column("方法", style="green")
    
    modules = {
        "read": ["read_file", "list_directory", "stat_path", "exists"],
        "search": ["search_files", "search_content", "search_symbol"],
        "edit": [
            "create_directory", "delete_directory", "move_directory",
            "create_file", "write_file", "delete_file", "move_file", "copy_file",
            "replace_range", "insert_text", "delete_range", "apply_patch"
        ],
        "execute": [
            "create_task", "start_task", "stop_task", "kill_task", "get_task", "list_tasks",
            "write_stdin", "stream_stdout", "stream_stderr",
            "wait_task", "attach_task", "detach_task"
        ],
        "system": ["ping", "get_version", "get_methods", "get_config", "get_stats", "clear_cache"],
    }
    
    for module, methods in modules.items():
        for method in methods:
            table.add_row(module, method)
    
    console.print(table)


@main.command()
def version():
    """显示版本信息"""
    from src.mcp import __version__
    
    panel = Panel(
        f"""[cyan]Windows MCP (Model Context Protocol)[/cyan]
        
版本: {__version__}
协议: 1.0
Python: 3.9+

专为Windows环境设计的任务自动化协议服务器""",
        title="MCP",
        border_style="green"
    )
    console.print(panel)


if __name__ == "__main__":
    main()
