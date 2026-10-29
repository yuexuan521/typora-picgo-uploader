"""端到端测试：用本地假 PicGo Server 跑通「扫描 → 上传 → 写回」。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typora_picgo_uploader.cli import main  # noqa: E402


class _FakePicGo(BaseHTTPRequestHandler):
    received = []
    fail_names = ()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        path = payload["list"][0]
        name = os.path.basename(path)
        _FakePicGo.received.append(path)

        if name in _FakePicGo.fail_names:
            body = {"success": False, "message": "upload failed"}
        else:
            body = {
                "success": True,
                "result": [f"https://cdn.example.com/{name}"],
                "fullResult": [],
            }
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # 静音
        pass


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _FakePicGo)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.assets = os.path.join(self.root, "assets")
        os.makedirs(self.assets)
        self.cache = os.path.join(self.root, "cache.json")
        _FakePicGo.received = []
        _FakePicGo.fail_names = ()

    def tearDown(self):
        self.tmp.cleanup()

    def _image(self, name: str, data: bytes = b"png") -> None:
        with open(os.path.join(self.assets, name), "wb") as handle:
            handle.write(data)

    def _md(self, name: str, content: str) -> str:
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return path

    def _run(self, *extra):
        return main([self.root, "-e", f"http://127.0.0.1:{self.port}/upload",
                     "--cache", self.cache, "--no-recursive", *extra])

    def _text(self, path: str) -> str:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_upload_and_replace(self):
        self._image("a.png", b"aaa")
        self._image("b.png", b"bbb")
        md = self._md("post.md", "![](assets/a.png)\n![](assets/b.png)\n")

        self.assertEqual(self._run(), 0)
        self.assertEqual(self._text(md), "![](https://cdn.example.com/a.png)\n![](https://cdn.example.com/b.png)\n")
        self.assertEqual(len(_FakePicGo.received), 2)

    def test_dedup_same_content_uploads_once(self):
        self._image("a.png", b"same-bytes")
        self._image("copy-of-a.png", b"same-bytes")
        md = self._md("post.md", "![](assets/a.png)\n![](assets/copy-of-a.png)\n")

        self.assertEqual(self._run(), 0)
        self.assertEqual(len(_FakePicGo.received), 1)
        self.assertEqual(
            self._text(md),
            "![](https://cdn.example.com/a.png)\n![](https://cdn.example.com/a.png)\n",
        )

    def test_cache_reused_on_second_run(self):
        self._image("a.png", b"aaa")
        first = self._md("one.md", "![](assets/a.png)\n")
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(_FakePicGo.received), 1)

        second = self._md("two.md", "![](assets/a.png)\n")
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(_FakePicGo.received), 1, "第二次运行应命中缓存")
        self.assertIn("https://cdn.example.com/a.png", self._text(second))
        self.assertIn("https://cdn.example.com/a.png", self._text(first))

    def test_code_block_untouched(self):
        self._image("a.png", b"aaa")
        md = self._md("post.md", "```\n![](assets/a.png)\n```\n\n![](assets/a.png)\n")

        self.assertEqual(self._run(), 0)
        self.assertEqual(
            self._text(md),
            "```\n![](assets/a.png)\n```\n\n![](https://cdn.example.com/a.png)\n",
        )

    def test_remote_untouched(self):
        md = self._md("post.md", "![](https://other.site/x.png)\n")
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(_FakePicGo.received), 0)
        self.assertEqual(self._text(md), "![](https://other.site/x.png)\n")

    def test_missing_file_kept(self):
        md = self._md("post.md", "![](assets/none.png)\n")
        self.assertEqual(self._run(), 0)
        self.assertEqual(self._text(md), "![](assets/none.png)\n")

    def test_failure_keeps_link_and_exit_code(self):
        self._image("bad.png", b"bad")
        _FakePicGo.fail_names = ("bad.png",)
        md = self._md("post.md", "![](assets/bad.png)\n")

        self.assertEqual(self._run("--retries", "1"), 1)
        self.assertEqual(self._text(md), "![](assets/bad.png)\n")

    def test_dry_run_changes_nothing(self):
        self._image("a.png", b"aaa")
        md = self._md("post.md", "![](assets/a.png)\n")

        self.assertEqual(self._run("--dry-run"), 0)
        self.assertEqual(self._text(md), "![](assets/a.png)\n")
        self.assertEqual(len(_FakePicGo.received), 0)

    def test_crlf_file_stays_crlf(self):
        self._image("a.png", b"aaa")
        path = os.path.join(self.root, "post.md")
        with open(path, "wb") as handle:
            handle.write(b"# t\r\n\r\n![](assets/a.png)\r\n")

        self.assertEqual(self._run(), 0)
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), b"# t\r\n\r\n![](https://cdn.example.com/a.png)\r\n")

    def test_html_img_replaced(self):
        self._image("a.png", b"aaa")
        md = self._md("post.md", '<img alt="" src="assets/a.png" />\n')

        self.assertEqual(self._run(), 0)
        self.assertEqual(self._text(md), '<img alt="" src="https://cdn.example.com/a.png" />\n')

    def test_picgo_unreachable_aborts_without_writing(self):
        self._image("a.png", b"aaa")
        md = self._md("post.md", "![](assets/a.png)\n")
        code = main([self.root, "-e", "http://127.0.0.1:1/upload",
                     "--cache", self.cache, "--no-recursive"])
        self.assertEqual(code, 2)
        self.assertEqual(self._text(md), "![](assets/a.png)\n")


if __name__ == "__main__":
    unittest.main()
