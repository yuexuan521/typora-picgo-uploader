"""扫描目录下的 Markdown 文件。"""

from __future__ import annotations

import os
from typing import Iterator, List, Sequence

DEFAULT_IGNORED_DIRS: Sequence[str] = (
    ".git",
    ".svn",
    ".hg",
    ".obsidian",
    ".trash",
    ".idea",
    ".vscode",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
)


def iter_markdown_files(
    root: str,
    recursive: bool = True,
    ignored_dirs: Sequence[str] = DEFAULT_IGNORED_DIRS,
) -> Iterator[str]:
    root = os.path.abspath(root)
    if not recursive:
        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if name.lower().endswith((".md", ".markdown")) and os.path.isfile(path):
                yield path
        return

    ignored = {name.lower() for name in ignored_dirs}
    for current, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in ignored)
        for name in sorted(filenames):
            if name.lower().endswith((".md", ".markdown")):
                yield os.path.join(current, name)


def list_markdown_files(root: str, recursive: bool = True) -> List[str]:
    return list(iter_markdown_files(root, recursive))
