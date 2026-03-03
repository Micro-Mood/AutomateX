"""
AutomateX - Windows智能任务自动化引擎
=====================================

命令行入口程序 (V3 - 两阶段工具调用)

使用方法:
    python -m tasks.main "任务描述"
    python -m tasks.main --interactive "任务描述"
    python -m tasks.main --list
    python -m tasks.main --status <task_id>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .engine import TaskEngine, EngineConfig
from .store import TaskStore
from .models import Task, TaskStatus


def print_banner():
    """打印横幅"""
    banner = """
 $$$$$$\\              $$\\                                        $$\\               $$\\   $$\\ 
$$  __$$\\             $$ |                                       $$ |              $$ |  $$ |
$$ /  $$ |$$\\   $$\\ $$$$$$\\    $$$$$$\\  $$$$$$\\$$$$\\   $$$$$$\\ $$$$$$\\    $$$$$$\\  \\$$\\ $$  |
$$$$$$$$ |$$ |  $$ |\\_$$  _|  $$  __$$\\ $$  _$$  _$$\\  \\____$$\\\\_$$  _|  $$  __$$\\  \\$$$$  / 
$$  __$$ |$$ |  $$ |  $$ |    $$ /  $$ |$$ / $$ / $$ | $$$$$$$ | $$ |    $$$$$$$$ | $$  $$<  
$$ |  $$ |$$ |  $$ |  $$ |$$\\ $$ |  $$ |$$ | $$ | $$ |$$  __$$ | $$ |$$\\ $$   ____|$$  /\\$$\\ 
$$ |  $$ |\\$$$$$$  |  \\$$$$  |\\$$$$$$  |$$ | $$ | $$ |\\$$$$$$$ | \\$$$$  |\\$$$$$$$\\ $$ /  $$ |
\\__|  \\__| \\______/    \\____/  \\______/ \\__| \\__| \\__| \\_______|  \\____/  \\_______|\\__|  \\__|           
                                                                     V3 - Token Optimized                                                                                                                                                                    
"""
    print(banner)


def list_tasks(store: TaskStore):
    """列出所有任务"""
    tasks = store.list_tasks()
    
    if not tasks:
        print("📋 暂无任务")
        return
    
    print(f"\n📋 任务列表 (共 {len(tasks)} 个):\n")
    print("-" * 80)
    print(f"{'ID':<30} {'状态':<12} {'进度':<8} {'描述'}")
    print("-" * 80)
    
    for task in tasks:
        status_icons = {
            TaskStatus.WAITING: "⏳",
            TaskStatus.RUNNING: "🔄",
            TaskStatus.NEED_INPUT: "❓",
            TaskStatus.COMPLETED: "✅",
            TaskStatus.FAILED: "❌",
            TaskStatus.CANCELLED: "🚫",
            TaskStatus.PAUSED: "⏸️",
        }
        icon = status_icons.get(task.status, "•")
        desc = task.description[:30] + "..." if len(task.description) > 30 else task.description
        print(f"{task.id:<30} {icon} {task.status.value:<10} {task.progress:>3}%    {desc}")
    
    print("-" * 80)


def show_task_status(store: TaskStore, task_id: str):
    """显示任务详细状态"""
    task = store.get_task(task_id)
    
    if not task:
        print(f"❌ 任务不存在: {task_id}")
        return
    
    print(f"\n{'='*60}")
    print(f"📋 任务详情")
    print(f"{'='*60}")
    print(f"ID: {task.id}")
    print(f"描述: {task.description}")
    print(f"状态: {task.status.value}")
    print(f"进度: {task.progress}%")
    print(f"创建时间: {task.created_at}")
    print(f"更新时间: {task.updated_at}")
    
    if task.started_at:
        print(f"开始时间: {task.started_at}")
    if task.completed_at:
        print(f"完成时间: {task.completed_at}")
    
    if task.current_step:
        print(f"\n当前步骤: {task.current_step}")
    
    if task.error_message:
        print(f"\n❌ 错误: {task.error_message}")
    
    if task.need_input and task.need_input.required:
        print(f"\n❓ 等待输入: {task.need_input.question}")
        if task.need_input.options:
            print(f"   选项: {', '.join(task.need_input.options)}")
    
    print(f"{'='*60}\n")


def get_api(model_name: str):
    """获取 AI API"""
    from src.tasks.chat import get_api
    return get_api(model_name)


async def run_task_async(task: Task, engine: TaskEngine) -> Task:
    """异步运行任务"""
    return await engine.run(task)


def run_task(store: TaskStore, description: str, model: str, workdir: str, 
             interactive: bool = False, show_reasoning: bool = True):
    """运行任务"""
    # 创建任务
    task = Task(
        id=Task.generate_id(),
        description=description,
        working_directory=workdir,
    )
    store.add_task(task)
    
    # 获取 API
    api = get_api(model)
    
    # 配置引擎
    config = EngineConfig(
        max_history=20,
        max_iterations=50,
        use_mcp=True,
    )
    
    # 输出回调
    def on_output(msg: str):
        if show_reasoning:
            print(msg)
    
    engine = TaskEngine(api=api, store=store, config=config, on_output=on_output)
    
    if interactive:
        # 交互模式
        while True:
            task = asyncio.run(run_task_async(task, engine))
            
            if task.status == TaskStatus.NEED_INPUT:
                print(f"\n❓ {task.need_input.question if task.need_input else '请输入:'}")
                if task.need_input and task.need_input.options:
                    for i, opt in enumerate(task.need_input.options, 1):
                        print(f"  {i}. {opt}")
                user_input = input("> ")
                task = asyncio.run(engine.continue_with_input(task, user_input))
            else:
                break
    else:
        task = asyncio.run(run_task_async(task, engine))
    
    # 打印结果
    if task.status == TaskStatus.COMPLETED:
        print(f"\n✅ 任务完成!")
        if task.current_step:
            print(f"   {task.current_step}")
    elif task.status == TaskStatus.FAILED:
        print(f"\n❌ 任务失败: {task.error_message}")
    elif task.status == TaskStatus.NEED_INPUT:
        print(f"\n❓ 任务等待输入: {task.need_input.question if task.need_input else ''}")
        print(f"   使用 --input {task.id} \"你的回答\" 继续")


def continue_task(store: TaskStore, task_id: str, model: str, user_input: str = None):
    """继续执行任务"""
    task = store.get_task(task_id)
    
    if not task:
        print(f"❌ 任务不存在: {task_id}")
        return
    
    # 防止重复执行已完成的任务
    if task.status == TaskStatus.COMPLETED:
        print(f"⚠️ 任务已完成，无需重复执行: {task_id}")
        show_task_status(store, task_id)
        return
    
    api = get_api(model)
    config = EngineConfig(use_mcp=True)
    engine = TaskEngine(api=api, store=store, config=config, on_output=print)
    
    if task.status == TaskStatus.NEED_INPUT and user_input:
        task = asyncio.run(engine.continue_with_input(task, user_input))
    else:
        task = asyncio.run(engine.run(task))
    
    if task.status == TaskStatus.COMPLETED:
        print(f"\n✅ 任务完成!")
    elif task.status == TaskStatus.FAILED:
        print(f"\n❌ 任务失败: {task.error_message}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="AutomateX V3 - Windows智能任务自动化引擎 (Token 优化版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    %(prog)s "创建一个Python项目"
    %(prog)s --interactive "帮我整理桌面文件"
    %(prog)s --list
    %(prog)s --status task_20240101_120000_abc12345
    %(prog)s --continue task_20240101_120000_abc12345
    %(prog)s --input task_20240101_120000_abc12345 "是的，确认执行"
        """
    )
    
    parser.add_argument("task", nargs="?", help="任务描述")
    parser.add_argument("-i", "--interactive", action="store_true", 
                        help="交互模式（遇到需要输入时从控制台获取）")
    parser.add_argument("-l", "--list", action="store_true", help="列出所有任务")
    parser.add_argument("-s", "--status", metavar="TASK_ID", help="查看任务状态")
    parser.add_argument("-c", "--continue", dest="continue_task", metavar="TASK_ID",
                        help="继续执行任务")
    parser.add_argument("--input", nargs=2, metavar=("TASK_ID", "INPUT"),
                        help="为等待输入的任务提供输入")
    parser.add_argument("-m", "--model", default="kimi",
                        help="使用的AI模型 (默认: kimi)")
    parser.add_argument("-d", "--workdir", default=".",
                        help="工作目录 (默认: 当前目录)")
    parser.add_argument("--no-reasoning", action="store_true",
                        help="不显示AI思考过程")
    parser.add_argument("--quiet", action="store_true", help="静默模式")
    
    args = parser.parse_args()
    
    # 初始化存储
    store = TaskStore()
    
    # 处理各种命令
    if args.list:
        list_tasks(store)
        return
    
    if args.status:
        show_task_status(store, args.status)
        return
    
    if args.input:
        task_id, user_input = args.input
        continue_task(store, task_id, args.model, user_input)
        return
    
    if args.continue_task:
        if not args.quiet:
            print_banner()
        continue_task(store, args.continue_task, args.model)
        return
    
    # 需要任务描述
    if not args.task:
        parser.print_help()
        return
    
    # 创建并运行任务
    if not args.quiet:
        print_banner()
    
    # 设置工作目录
    workdir = str(Path(args.workdir).resolve())
    
    run_task(
        store=store,
        description=args.task,
        model=args.model,
        workdir=workdir,
        interactive=args.interactive,
        show_reasoning=not args.no_reasoning,
    )


if __name__ == "__main__":
    main()
