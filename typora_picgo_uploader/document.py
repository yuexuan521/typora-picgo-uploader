"""Markdown 文件的读取与保真写回。

必须做到「内容没变就不碰文件，内容变了就只改该改的字节」：
- 直接按二进制读入，保留 CRLF / LF 原样，不做 universal newlines 转换；
- 保留 BOM 与原始编码（UTF-8 / UTF-16 带 BOM / GB18030 兜底）；
- 写入走临时文件 + 原子替换，中途失败不会留下半个文件；
- 可选生成 ``.bak`` 备份。
"""

from __future__ import annotations

import codecs
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Optional, Tuple

_BOMS: Tuple[Tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)


@dataclass
class Document:
    path: str
    text: str
    encoding: str
    bom: bytes
    original: bytes

    @property
    def newline(self) -> str:
        return "\r\n" if b"\r\n" in self.original else "\n"


def read_document(path: str) -> Document:
    with open(path, "rb") as handle:
        raw = handle.read()

    for bom, encoding in _BOMS:
        if raw.startswith(bom):
            return Document(path, raw[len(bom) :].decode(encoding), encoding, bom, raw)

    for encoding in ("utf-8", "gb18030"):
        try:
            return Document(path, raw.decode(encoding), encoding, b"", raw)
        except UnicodeDecodeError:
            continue

    return Document(path, raw.decode("utf-8", "replace"), "utf-8", b"", raw)


def write_document(document: Document, text: str, backup: bool = False) -> bool:
    """写回文件；内容与原文完全一致时返回 ``False`` 且不触发任何磁盘写入。"""
    data = document.bom + text.encode(document.encoding)
    if data == document.original:
        return False

    directory = os.path.dirname(document.path) or "."
    if backup:
        shutil.copy2(document.path, document.path + ".bak")

    handle_fd: Optional[int] = None
    temp_path = ""
    try:
        handle_fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".picgo-", suffix=".md")
        with os.fdopen(handle_fd, "wb") as handle:
            handle_fd = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, document.path)
        temp_path = ""
        return True
    finally:
        if handle_fd is not None:
            os.close(handle_fd)
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
