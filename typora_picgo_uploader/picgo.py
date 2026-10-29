"""PicGo HTTP Server 客户端。

PicGo 的 Server 接口（默认 ``http://127.0.0.1:36677/upload``）：

    POST /upload  {"list": ["D:\\blog\\assets\\a.png"]}
    200 {"success": true, "result": ["https://..."], "fullResult": [...]}

兼容性处理：
- ``result`` 可能是字符串而不是数组（部分插件/版本），两种都接受；
- 走无代理的 opener，避免系统代理环境变量把 127.0.0.1 也代理走；
- 失败自动重试，指数退避。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

DEFAULT_ENDPOINT = "http://127.0.0.1:36677/upload"


class PicGoError(RuntimeError):
    """PicGo 相关错误。"""


class PicGoClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, timeout: float = 60.0, retries: int = 3):
        self.endpoint = endpoint
        self.timeout = timeout
        self.retries = max(1, retries)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- 连通性 ------------------------------------------------------------
    def probe(self, timeout: float = 2.0) -> bool:
        parts = urllib.parse.urlsplit(self.endpoint)
        host = parts.hostname or "127.0.0.1"
        port = parts.port or (443 if parts.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    # -- 上传 --------------------------------------------------------------
    def upload(self, image_path: str) -> str:
        """上传单张图片，返回远端 URL；失败抛 :class:`PicGoError`。"""
        if not os.path.isfile(image_path):
            raise PicGoError(f"本地文件不存在：{image_path}")

        payload = json.dumps({"list": [os.path.abspath(image_path)]}).encode("utf-8")
        last_error: Optional[str] = None

        for attempt in range(1, self.retries + 1):
            request = urllib.request.Request(
                self.endpoint,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
                return self._extract_url(body)
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code} {exc.reason}"
            except urllib.error.URLError as exc:
                last_error = f"连接失败（{exc.reason}）"
            except (TimeoutError, socket.timeout):
                last_error = "请求超时"
            except PicGoError as exc:
                last_error = str(exc)

            if attempt < self.retries:
                time.sleep(min(2 ** (attempt - 1), 8))

        raise PicGoError(last_error or "未知错误")

    @staticmethod
    def _extract_url(body: str) -> str:
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            raise PicGoError(f"PicGo 返回了无法解析的内容：{body[:200]}") from None

        if not isinstance(data, dict):
            raise PicGoError(f"PicGo 返回格式异常：{body[:200]}")

        if not data.get("success"):
            message = data.get("message") or data.get("error") or body[:200]
            raise PicGoError(f"PicGo 处理失败：{message}")

        result = data.get("result")
        if isinstance(result, str):
            return result
        if isinstance(result, list) and result:
            first = result[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict) and isinstance(first.get("imgUrl"), str):
                return first["imgUrl"]
        raise PicGoError(f"PicGo 返回中没有可用 URL：{body[:200]}")


def find_picgo_executable() -> Optional[str]:
    """尝试定位 PicGo 客户端可执行文件。"""
    candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(os.path.join(local_appdata, "Programs", "PicGo", "PicGo.exe"))
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidates.append(os.path.join(base, "PicGo", "PicGo.exe"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def launch_picgo(executable: str, endpoint: str, wait: float = 25.0) -> bool:
    """启动 PicGo 并等待 Server 端口可用。"""
    if not os.path.isfile(executable):
        return False
    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(
            [executable],
            cwd=os.path.dirname(executable),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError:
        return False

    client = PicGoClient(endpoint)
    deadline = time.time() + wait
    while time.time() < deadline:
        if client.probe():
            time.sleep(1.5)  # 端口刚起来时插件可能还没加载完
            return True
        time.sleep(0.5)
    return False
