"""Markdown 图片引用解析与本地路径解析。

设计要点
--------
1. 只替换「目标地址」这一段字符，不重建整个图片语法。
   这样内联式、尖括号式、带 title、引用式定义、`<img src>` 全部用同一套逻辑，
   且不会破坏用户原有的写法（如 `![](<my image.png> "标题")`）。
2. 解析前先算出「受保护区间」（围栏代码块、行内代码、HTML 注释），
   落在其中的 `![](...)` 一律不处理，避免把博客里的示例代码改成图片。
"""

from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

#: 已经指向远端的地址前缀，无需上传
REMOTE_PREFIXES: Tuple[str, ...] = (
    "http://",
    "https://",
    "//",
    "data:",
    "mailto:",
    "tel:",
    "#",
)

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_BACKTICK_RUN_RE = re.compile(r"`+")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_REF_DEF_RE = re.compile(r"^ {0,3}\[([^\]\n]+)\]:[ \t]*(<[^>\n]*>|\S+)", re.MULTILINE)
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC_ATTR_RE = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_REF_USE_RE = re.compile(r"!\[([^\]\n]*)\]\[([^\]\n]*)\]")
_DRIVE_RE = re.compile(r"^/[A-Za-z]:")
#: 长度大于 1 的 scheme 才视为外来协议，避免把 Windows 盘符 `C:` 当协议
_FOREIGN_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]+:")


@dataclass
class ImageRef:
    """一处待替换的图片地址。

    ``dest_start`` / ``dest_end`` 是地址文本在原文中的绝对偏移，
    替换时只动这一段，其余字符原样保留。
    """

    dest: str
    dest_start: int
    dest_end: int
    form: str  # inline | angle | html | reference
    line: int = 0

    @property
    def span(self) -> Tuple[int, int]:
        return (self.dest_start, self.dest_end)


# --------------------------------------------------------------------------- #
# 受保护区间
# --------------------------------------------------------------------------- #
def protected_ranges(text: str) -> List[Tuple[int, int]]:
    """返回不应被处理的字符区间：代码块、行内代码、HTML 注释。"""
    ranges: List[Tuple[int, int]] = []

    for match in _COMMENT_RE.finditer(text):
        ranges.append((match.start(), match.end()))

    in_fence: Optional[Tuple[str, int, int]] = None  # (字符, 长度, 起始偏移)
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if in_fence is not None:
            closing = _FENCE_RE.match(stripped)
            if (
                closing
                and closing.group(1)[0] == in_fence[0]
                and len(closing.group(1)) >= in_fence[1]
                and not closing.group(2).strip()
            ):
                ranges.append((in_fence[2], offset + len(line)))
                in_fence = None
            offset += len(line)
            continue

        opening = _FENCE_RE.match(stripped)
        if opening:
            fence = opening.group(1)
            info = opening.group(2)
            # 反引号围栏的信息串里不能再出现反引号，否则是行内代码
            if not (fence[0] == "`" and "`" in info):
                in_fence = (fence[0], len(fence), offset)
                offset += len(line)
                continue

        ranges.extend(_inline_code_ranges(line, offset))
        offset += len(line)

    if in_fence is not None:
        ranges.append((in_fence[2], len(text)))

    ranges.sort()
    return ranges


def _inline_code_ranges(line: str, base: int) -> List[Tuple[int, int]]:
    """同一行内成对的反引号围成的行内代码。"""
    runs = [(m.start(), len(m.group())) for m in _BACKTICK_RUN_RE.finditer(line)]
    ranges: List[Tuple[int, int]] = []
    i = 0
    while i < len(runs):
        start, length = runs[i]
        j = i + 1
        while j < len(runs) and runs[j][1] != length:
            j += 1
        if j < len(runs):
            ranges.append((base + start, base + runs[j][0] + runs[j][1]))
            i = j + 1
        else:
            i += 1
    return ranges


def _is_protected(pos: int, ranges: Sequence[Tuple[int, int]]) -> bool:
    for start, end in ranges:
        if start > pos:
            return False
        if pos < end:
            return True
    return False


def _is_escaped(text: str, pos: int) -> bool:
    backslashes = 0
    i = pos - 1
    while i >= 0 and text[i] == "\\":
        backslashes += 1
        i -= 1
    return backslashes % 2 == 1


# --------------------------------------------------------------------------- #
# 单条引用解析
# --------------------------------------------------------------------------- #
def _parse_inline(text: str, start: int) -> Optional[ImageRef]:
    """从 ``![`` 处解析一条内联图片，返回 ``None`` 表示不是合法图片。"""
    n = len(text)
    i = start + 2

    depth = 0
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            if depth == 0:
                break
            depth -= 1
        i += 1
    if i >= n or text[i] != "]":
        return None
    if i + 1 >= n or text[i + 1] != "(":
        return None

    i += 2
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i >= n:
        return None

    if text[i] == "<":
        close = text.find(">", i + 1)
        if close < 0:
            return None
        dest_start, dest_end = i + 1, close
        form = "angle"
        i = close + 1
    else:
        dest_start = i
        paren = 0
        while i < n:
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "(":
                paren += 1
            elif ch == ")":
                if paren == 0:
                    break
                paren -= 1
            elif ch in " \t\r\n" and paren == 0:
                break
            i += 1
        if i > n:
            return None
        dest_end = i
        form = "inline"

    # 跳过可选的 title，直到右括号
    while True:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            return None
        ch = text[i]
        if ch == ")":
            break
        if ch in "\"'":
            close = text.find(ch, i + 1)
            if close < 0:
                return None
            i = close + 1
        elif ch == "(":
            close = text.find(")", i + 1)
            if close < 0:
                return None
            i = close + 1
        else:
            return None

    dest = text[dest_start:dest_end]
    line = text.count("\n", 0, start) + 1
    return ImageRef(dest=dest, dest_start=dest_start, dest_end=dest_end, form=form, line=line)


def _normalize_label(label: str) -> str:
    return " ".join(label.split()).casefold()


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def collect_image_refs(text: str) -> List[ImageRef]:
    """找出文本中所有需要检查的图片地址（按出现顺序、地址不重复）。"""
    guarded = protected_ranges(text)
    refs: List[ImageRef] = []
    seen: set = set()

    def add(ref: ImageRef) -> None:
        key = (ref.dest_start, ref.dest_end)
        if key in seen:
            return
        seen.add(key)
        refs.append(ref)

    # 1) 引用式定义的地址表
    definitions: Dict[str, ImageRef] = {}
    for match in _REF_DEF_RE.finditer(text):
        if _is_protected(match.start(), guarded):
            continue
        raw = match.group(2)
        if raw.startswith("<") and raw.endswith(">"):
            dest_start = match.start(2) + 1
            dest_end = match.end(2) - 1
            raw = raw[1:-1]
        else:
            dest_start = match.start(2)
            dest_end = match.end(2)
        definitions[_normalize_label(match.group(1))] = ImageRef(
            dest=raw,
            dest_start=dest_start,
            dest_end=dest_end,
            form="reference",
            line=text.count("\n", 0, match.start()) + 1,
        )

    # 2) 内联式 ![alt](path)
    i = 0
    n = len(text)
    while i < n:
        i = text.find("![", i)
        if i < 0:
            break
        if _is_protected(i, guarded) or _is_escaped(text, i):
            i += 2
            continue
        ref = _parse_inline(text, i)
        if ref is None:
            i += 2
            continue
        if ref.dest.strip():
            add(ref)
        i = max(ref.dest_end, i + 2)

    # 3) 引用式用法 ![alt][label] / ![alt][]
    for match in _REF_USE_RE.finditer(text):
        if _is_protected(match.start(), guarded) or _is_escaped(text, match.start()):
            continue
        label = match.group(2).strip() or match.group(1)
        definition = definitions.get(_normalize_label(label))
        if definition is not None:
            add(definition)

    # 4) HTML 标签 <img src="...">
    for match in _IMG_TAG_RE.finditer(text):
        if _is_protected(match.start(), guarded):
            continue
        src = _SRC_ATTR_RE.search(match.group(0))
        if not src:
            continue
        for group in (1, 2, 3):
            if src.group(group) is not None:
                add(
                    ImageRef(
                        dest=src.group(group),
                        dest_start=match.start() + src.start(group),
                        dest_end=match.start() + src.end(group),
                        form="html",
                        line=text.count("\n", 0, match.start()) + 1,
                    )
                )
                break

    refs.sort(key=lambda r: r.dest_start)
    return refs


def is_remote(dest: str) -> bool:
    return dest.strip().lower().startswith(REMOTE_PREFIXES)


def resolve_local_path(
    dest: str,
    md_dir: str,
    root_dir: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """把 Markdown 里的地址解析成磁盘上的绝对路径。

    返回 ``(路径, 原因)``，原因为 ``ok`` / ``remote`` / ``empty`` /
    ``not-found`` / ``unsupported``。
    """
    raw = dest.strip()
    if not raw:
        return None, "empty"
    lowered = raw.lower()
    if lowered.startswith(REMOTE_PREFIXES):
        return None, "remote"

    text = raw
    if lowered.startswith("file:"):
        parsed = urllib.parse.urlsplit(raw)
        text = urllib.parse.unquote(parsed.path)
        if _DRIVE_RE.match(text):
            text = text[1:]
    elif _FOREIGN_SCHEME_RE.match(raw):
        # 其它协议（如 obsidian://、ftp://）不处理；C:/ 这类盘符不算协议
        return None, "unsupported"

    # 本地路径里不可能出现 ? 和 #，这里当作查询串/锚点裁掉
    for sep in ("#", "?"):
        if sep in text:
            text = text.split(sep, 1)[0]

    candidates: List[str] = []
    for value in (urllib.parse.unquote(text), text):
        if value and value not in candidates:
            candidates.append(value)

    tried: List[str] = []
    for value in candidates:
        normalized = value.replace("\\", os.sep).replace("/", os.sep)
        paths: List[str] = []
        if os.path.isabs(normalized):
            paths.append(normalized)
        else:
            paths.append(os.path.join(md_dir, normalized))
            if root_dir:
                paths.append(os.path.join(root_dir, normalized))
        for path in paths:
            candidate = os.path.normpath(os.path.abspath(path))
            if candidate in tried:
                continue
            tried.append(candidate)
            if os.path.isfile(candidate):
                return candidate, "ok"

    return None, "not-found"


def apply_replacements(text: str, replacements: Sequence[Tuple[int, int, str]]) -> str:
    """按偏移替换地址段；调用方需保证区间互不重叠。"""
    ordered = sorted(replacements, key=lambda item: item[0], reverse=True)
    for start, end, value in ordered:
        text = text[:start] + value + text[end:]
    return text
