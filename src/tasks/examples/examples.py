"""
AutomateX 使用示例 (V3)
========================

本文件展示了 AutomateX 的各种使用方法。
与当前 V3 两阶段工具调用 API 保持一致。
"""

import asyncio
import sys
from pathlib import Path

# 确保可以导入 src 包
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.tasks.api import AutomateX, quick_run, interactive_run
from src.tasks.models import TaskStatus


def example_quick_run():
    """示例1: 快速运行"""
    print("\n" + "=" * 60)
    print("示例1: 快速运行任务")
    print("=" * 60)
    
    # 最简单的方式 —— 使用 quick_run 便捷函数
    task = quick_run("在当前目录创建一个名为hello.txt的文件，内容为'Hello, AutomateX!'")
    
    print(f"\n任务状态: {task.status.value}")
    print(f"任务进度: {task.progress}%")


def example_api_basic():
    """示例2: 使用 AutomateX API 类"""
    print("\n" + "=" * 60)
    print("示例2: 使用 AutomateX API 类")
    print("=" * 60)
    
    # 创建 AutomateX 实例（AI 模型从 user_config.json 读取）
    ax = AutomateX(show_reasoning=True)
    
    # 运行任务
    task = ax.run("列出当前目录下的所有文件和文件夹")
    
    print(f"\n任务状态: {task.status.value}")
    
    # 查看执行的命令
    if task.command_results:
        print("\n执行的命令:")
        for result in task.command_results:
            print(f"  - {result.command}")
            if result.success:
                print(f"    输出: {result.stdout[:200]}...")


def example_interactive():
    """示例3: 交互式运行"""
    print("\n" + "=" * 60)
    print("示例3: 交互式运行")
    print("=" * 60)
    
    # 交互式运行：遇到需要用户输入时会从控制台获取
    task = interactive_run("创建一个Python项目结构")
    
    print(f"\n最终状态: {task.status.value}")


def example_task_management():
    """示例4: 任务管理"""
    print("\n" + "=" * 60)
    print("示例4: 任务管理")
    print("=" * 60)
    
    ax = AutomateX()
    
    # 创建任务但不立即运行
    task = ax.create_task("这是一个测试任务")
    print(f"创建的任务ID: {task.id}")
    
    # 列出所有任务
    tasks = ax.list_tasks()
    print(f"\n所有任务数量: {len(tasks)}")
    
    # 获取统计信息
    stats = ax.get_statistics()
    print(f"\n任务统计:")
    print(f"  - 总数: {stats['total']}")
    print(f"  - 等待中: {stats['waiting']}")
    print(f"  - 已完成: {stats['completed']}")
    print(f"  - 失败: {stats['failed']}")
    
    # 获取指定任务
    fetched = ax.get_task(task.id)
    if fetched:
        print(f"\n获取到任务: {fetched.description}")


def example_continue_task():
    """示例5: 处理需要输入的任务"""
    print("\n" + "=" * 60)
    print("示例5: 处理需要输入的任务")
    print("=" * 60)
    
    ax = AutomateX()
    
    # 运行可能需要输入的任务
    task = ax.run("创建一个新的项目文件夹")
    
    # 检查是否需要输入
    if task.status == TaskStatus.NEED_INPUT:
        print(f"\n任务需要输入:")
        if task.need_input:
            print(f"  问题: {task.need_input.question}")
            if task.need_input.options:
                print(f"  选项: {task.need_input.options}")
        
        # 提供输入并继续
        task = ax.continue_task(task.id, user_input="myproject")
    
    print(f"\n最终状态: {task.status.value}")


def example_error_handling():
    """示例6: 错误处理"""
    print("\n" + "=" * 60)
    print("示例6: 错误处理")
    print("=" * 60)
    
    ax = AutomateX()
    
    # 故意执行一个可能失败的任务
    task = ax.run("执行一个不存在的命令 xyz123abc")
    
    if task.status == TaskStatus.FAILED:
        print(f"任务失败!")
        print(f"错误信息: {task.error_message}")
        
        # 可以尝试重试
        if ax.retry_task(task.id):
            print("已添加到重试队列")


def example_cleanup():
    """示例7: 清理旧任务"""
    print("\n" + "=" * 60)
    print("示例7: 清理旧任务")
    print("=" * 60)
    
    ax = AutomateX()
    
    # 清理 30 天前的已完成任务
    cleaned = ax.cleanup(days=30)
    print(f"清理了 {cleaned} 个旧任务")


def main():
    """运行所有示例"""
    print("\n" + "=" * 60)
    print(" AutomateX V3 使用示例")
    print("=" * 60)
    
    examples = [
        ("快速运行", example_quick_run),
        ("API基础", example_api_basic),
        ("交互式运行", example_interactive),
        ("任务管理", example_task_management),
        ("处理输入", example_continue_task),
        ("错误处理", example_error_handling),
        ("清理任务", example_cleanup),
    ]
    
    print("\n可用示例:")
    for i, (name, _) in enumerate(examples, 1):
        print(f"  {i}. {name}")
    print(f"  0. 运行所有示例")
    print(f"  q. 退出")
    
    while True:
        choice = input("\n请选择要运行的示例 (0-7, q退出): ").strip()
        
        if choice.lower() == 'q':
            break
        
        try:
            idx = int(choice)
            if idx == 0:
                for name, func in examples:
                    try:
                        func()
                    except Exception as e:
                        print(f"示例 {name} 出错: {e}")
            elif 1 <= idx <= len(examples):
                name, func = examples[idx - 1]
                try:
                    func()
                except Exception as e:
                    print(f"示例 {name} 出错: {e}")
            else:
                print("无效选择")
        except ValueError:
            print("请输入数字")


if __name__ == "__main__":
    main()
