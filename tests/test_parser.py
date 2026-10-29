"""解析器测试：``python -m unittest discover -s tests``"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typora_picgo_uploader.parser import (  # noqa: E402
    apply_replacements,
    collect_image_refs,
    is_remote,
    resolve_local_path,
)


def dests(text: str):
    return [ref.dest for ref in collect_image_refs(text)]


class CollectRefsTest(unittest.TestCase):
    def test_inline_basic(self):
        self.assertEqual(dests("![](assets/a.png)"), ["assets/a.png"])

    def test_inline_with_alt_and_title(self):
        text = '![截图](assets/a.png "首页截图")'
        self.assertEqual(dests(text), ["assets/a.png"])

    def test_angle_brackets_with_spaces(self):
        self.assertEqual(dests("![](<assets/my image.png>)"), ["assets/my image.png"])

    def test_parentheses_in_filename(self):
        self.assertEqual(dests("![](assets/a(1).png)"), ["assets/a(1).png"])

    def test_windows_absolute_path(self):
        self.assertEqual(dests("![](C:/Users/me/a.png)"), ["C:/Users/me/a.png"])

    def test_encoded_space(self):
        self.assertEqual(dests("![](assets/my%20image.png)"), ["assets/my%20image.png"])

    def test_html_img_tag(self):
        html = '<img alt="" src="./assets/a.png" style="zoom:50%;" />'
        self.assertEqual(dests(html), ["./assets/a.png"])

    def test_html_img_single_quote(self):
        self.assertEqual(dests("<img src='assets/a.png'>"), ["assets/a.png"])

    def test_reference_style(self):
        text = "![图一][ref1]\n\n[ref1]: assets/a.png \"标题\"\n"
        self.assertEqual(dests(text), ["assets/a.png"])

    def test_reference_collapsed(self):
        text = "![assets/a.png][]\n\n[assets/a.png]: ./assets/a.png\n"
        self.assertEqual(dests(text), ["./assets/a.png"])

    def test_reference_defined_but_unused(self):
        text = "[ref1]: assets/a.png\n\n正文没有引用。\n"
        self.assertEqual(dests(text), [])

    def test_skip_fenced_code_block(self):
        text = "正文\n\n```markdown\n![](assets/a.png)\n```\n\n结尾\n"
        self.assertEqual(dests(text), [])

    def test_skip_tilde_code_block(self):
        text = "~~~\n![](assets/a.png)\n~~~\n"
        self.assertEqual(dests(text), [])

    def test_keep_after_code_block(self):
        text = "```\n![](assets/in-code.png)\n```\n\n![](assets/real.png)\n"
        self.assertEqual(dests(text), ["assets/real.png"])

    def test_skip_inline_code(self):
        self.assertEqual(dests("写法是 `![](assets/a.png)` 这样"), [])

    def test_skip_html_comment(self):
        self.assertEqual(dests("<!-- ![](assets/a.png) -->"), [])

    def test_escaped_bang_is_ignored(self):
        self.assertEqual(dests("\\![](assets/a.png)"), [])

    def test_multiple_in_order(self):
        text = "![](a.png)\n![](b.png)\n![](c.png)\n"
        self.assertEqual(dests(text), ["a.png", "b.png", "c.png"])

    def test_offsets_target_only_destination(self):
        text = '![alt](assets/a.png "t")'
        ref = collect_image_refs(text)[0]
        self.assertEqual(text[ref.dest_start : ref.dest_end], "assets/a.png")

    def test_unclosed_bracket_does_not_crash(self):
        self.assertEqual(dests("![alt](assets/a.png"), [])
        self.assertEqual(dests("![alt]assets/a.png"), [])


class ApplyReplacementsTest(unittest.TestCase):
    def test_replace_only_destination(self):
        text = '![alt](assets/a.png "标题")'
        ref = collect_image_refs(text)[0]
        result = apply_replacements(text, [(ref.dest_start, ref.dest_end, "https://x/a.png")])
        self.assertEqual(result, '![alt](https://x/a.png "标题")')

    def test_replace_keeps_angle_brackets(self):
        text = "![](<my image.png>)"
        ref = collect_image_refs(text)[0]
        result = apply_replacements(text, [(ref.dest_start, ref.dest_end, "https://x/a.png")])
        self.assertEqual(result, "![](<https://x/a.png>)")

    def test_replace_inside_html_attribute(self):
        text = '<img alt="" src="assets/a.png" />'
        ref = collect_image_refs(text)[0]
        result = apply_replacements(text, [(ref.dest_start, ref.dest_end, "https://x/a.png")])
        self.assertEqual(result, '<img alt="" src="https://x/a.png" />')

    def test_replace_multiple(self):
        text = "![](a.png)\n![](b.png)\n"
        refs = collect_image_refs(text)
        result = apply_replacements(
            text,
            [(refs[0].dest_start, refs[0].dest_end, "https://x/1.png"),
             (refs[1].dest_start, refs[1].dest_end, "https://x/2.png")],
        )
        self.assertEqual(result, "![](https://x/1.png)\n![](https://x/2.png)\n")


class IsRemoteTest(unittest.TestCase):
    def test_remote(self):
        for value in ("http://a/b.png", "https://a/b.png", "//cdn/a.png", "data:image/png;base64,xx", "#anchor"):
            self.assertTrue(is_remote(value), value)

    def test_local(self):
        for value in ("assets/a.png", "./assets/a.png", "C:/a.png", "../a.png"):
            self.assertFalse(is_remote(value), value)


class ResolvePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.assets = os.path.join(self.root, "assets")
        os.makedirs(self.assets)
        self.image = os.path.join(self.assets, "my image.png")
        with open(self.image, "wb") as handle:
            handle.write(b"png")

    def tearDown(self):
        self.tmp.cleanup()

    def test_relative(self):
        path, reason = resolve_local_path("assets/my image.png", self.root)
        self.assertEqual(reason, "ok")
        self.assertEqual(os.path.normcase(path), os.path.normcase(self.image))

    def test_encoded_relative(self):
        path, reason = resolve_local_path("assets/my%20image.png", self.root)
        self.assertEqual(reason, "ok")
        self.assertEqual(os.path.normcase(path), os.path.normcase(self.image))

    def test_dot_slash(self):
        path, reason = resolve_local_path("./assets/my image.png", self.root)
        self.assertEqual(reason, "ok")

    def test_remote_skipped(self):
        _, reason = resolve_local_path("https://a/b.png", self.root)
        self.assertEqual(reason, "remote")

    def test_missing(self):
        _, reason = resolve_local_path("assets/none.png", self.root)
        self.assertEqual(reason, "not-found")

    def test_unsupported_scheme(self):
        _, reason = resolve_local_path("obsidian://open?vault=x", self.root)
        self.assertEqual(reason, "unsupported")

    def test_root_fallback(self):
        nested = os.path.join(self.root, "posts")
        os.makedirs(nested)
        path, reason = resolve_local_path("assets/my image.png", nested, self.root)
        self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main()
