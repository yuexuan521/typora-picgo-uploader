"""上传结果缓存（按文件内容 SHA-1 去重）。

同一张图片被多篇文章引用、或同一篇里引用了两次时，只上传一次并复用 URL。
缓存跨运行有效，放在用户目录下，不会污染博客仓库。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from typing import Dict, Optional

CACHE_VERSION = 1


def default_cache_path() -> str:
    base = os.environ.get("TYPORA_PICGO_HOME")
    if not base:
        home = os.path.expanduser("~")
        base = os.path.join(home, ".typora-picgo-uploader")
    return os.path.join(base, "cache.json")


def file_digest(path: str, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Cache:
    def __init__(self, path: Optional[str] = None, enabled: bool = True):
        self.path = path or default_cache_path()
        self.enabled = enabled
        self._entries: Dict[str, Dict[str, object]] = {}
        self._dirty = False
        if self.enabled:
            self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            self._entries = data["entries"]

    def get(self, digest: str) -> Optional[str]:
        if not self.enabled:
            return None
        entry = self._entries.get(digest)
        if isinstance(entry, dict):
            url = entry.get("url")
            if isinstance(url, str) and url:
                return url
        return None

    def put(self, digest: str, url: str, source: str) -> None:
        if not self.enabled:
            return
        self._entries[digest] = {
            "url": url,
            "source": os.path.basename(source),
            "added": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._dirty = True

    def save(self) -> bool:
        if not (self.enabled and self._dirty):
            return False
        directory = os.path.dirname(self.path) or "."
        try:
            os.makedirs(directory, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".cache-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": CACHE_VERSION, "entries": self._entries},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(temp_path, self.path)
            self._dirty = False
            return True
        except OSError:
            return False

    def __len__(self) -> int:
        return len(self._entries)
