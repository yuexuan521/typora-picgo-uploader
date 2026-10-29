"""控制台交互辅助：判断是否双击启动、确认提示、退出前暂停。"""

from __future__ import annotations

import os
import sys


def console_is_standalone() -> bool:
    """判断当前进程是否独占一个控制台窗口（即从资源管理器双击启动）。

    从 cmd / PowerShell / 脚本里调用时，控制台里至少还挂着 shell 本身，
    ``GetConsoleProcessList`` 会返回 2 个以上进程；双击启动时只有自己。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = (ctypes.c_uint32 * 8)()
        count = kernel32.GetConsoleProcessList(buffer, 8)
        return 0 < count <= 1
    except Exception:
        return False


def is_interactive() -> bool:
    """能否和用户交互（双击启动且标准输入是终端）。"""
    try:
        if sys.stdin is None or not sys.stdin.isatty():
            return False
        if sys.stdout is None or not sys.stdout.isatty():
            return False
    except (ValueError, OSError):
        return False
    return console_is_standalone()


def confirm(question: str, default: bool = True) -> bool:
    """回车确认为「是」的提问；无交互输入时返回默认值。"""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{question} ({hint}) ").strip().lower()
    except (EOFError, KeyboardInterrupt, OSError):
        print()
        return False
    if not answer:
        return default
    return answer in ("y", "yes", "是")


def pause(message: str = "按回车键退出...") -> None:
    """退出前停顿，避免双击运行时窗口一闪而过。"""
    try:
        print()
        input(message)
    except (EOFError, KeyboardInterrupt, OSError):
        print()
