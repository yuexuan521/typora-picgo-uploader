"""文件读写的保真性测试：换行符、BOM、编码、无变化不落盘。"""

from __future__ import annotations

import codecs
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typora_picgo_uploader.document import read_document, write_document  # noqa: E402


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "post.md")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, data: bytes) -> None:
        with open(self.path, "wb") as handle:
            handle.write(data)

    def _read(self) -> bytes:
        with open(self.path, "rb") as handle:
            return handle.read()

    def test_crlf_preserved(self):
        self._write(b"# title\r\n\r\n![](a.png)\r\n")
        document = read_document(self.path)
        write_document(document, document.text.replace("a.png", "https://x/a.png"))
        self.assertEqual(self._read(), b"# title\r\n\r\n![](https://x/a.png)\r\n")

    def test_lf_preserved(self):
        self._write(b"# title\n\n![](a.png)\n")
        document = read_document(self.path)
        write_document(document, document.text.replace("a.png", "https://x/a.png"))
        self.assertEqual(self._read(), b"# title\n\n![](https://x/a.png)\n")

    def test_bom_preserved(self):
        self._write(codecs.BOM_UTF8 + "标题\n![](a.png)\n".encode("utf-8"))
        document = read_document(self.path)
        self.assertEqual(document.text, "标题\n![](a.png)\n")
        write_document(document, document.text.replace("a.png", "https://x/a.png"))
        self.assertTrue(self._read().startswith(codecs.BOM_UTF8))

    def test_unchanged_returns_false(self):
        self._write(b"![](a.png)\n")
        document = read_document(self.path)
        self.assertFalse(write_document(document, document.text))
        self.assertEqual(self._read(), b"![](a.png)\n")

    def test_backup_created(self):
        self._write(b"![](a.png)\n")
        document = read_document(self.path)
        write_document(document, "![](https://x/a.png)\n", backup=True)
        self.assertTrue(os.path.isfile(self.path + ".bak"))
        with open(self.path + ".bak", "rb") as handle:
            self.assertEqual(handle.read(), b"![](a.png)\n")

    def test_gb18030_fallback(self):
        self._write("中文标题\n![](a.png)\n".encode("gb18030"))
        document = read_document(self.path)
        self.assertIn("中文标题", document.text)


if __name__ == "__main__":
    unittest.main()
