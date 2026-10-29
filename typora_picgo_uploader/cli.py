"""命令行入口：串联扫描、解析、上传、写回、报告。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from . import __version__
from .cache import Cache, file_digest
from .document import Document, read_document, write_document
from .parser import ImageRef, apply_replacements, collect_image_refs, is_remote, resolve_local_path
from .picgo import (
    DEFAULT_ENDPOINT,
    PicGoClient,
    PicGoError,
    find_picgo_executable,
    launch_picgo,
)
from .scanner import list_markdown_files

CONFIG_NAME = "typora-picgo-uploader.json"
DEFAULT_CONCURRENCY = 4

_EXTRA_HELP = """
示例:
  typora-picgo-uploader.exe                       处理程序所在目录（含子目录）
  typora-picgo-uploader.exe --dir D:\\blog -c 8   指定目录，8 并发
  typora-picgo-uploader.exe --dry-run             只预览，不上传不写回
  typora-picgo-uploader.exe D:\\blog\\post.md     只处理单个文件
"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class MissingRef:
    file: str
    line: int
    dest: str


@dataclass
class FileJob:
    path: str
    document: Document
    #: (引用, 解析到的绝对路径, 内容摘要)
    items: List[Tuple[ImageRef, str, str]] = field(default_factory=list)
    remote: int = 0
    missing: List[MissingRef] = field(default_factory=list)


@dataclass
class Stats:
    files_scanned: int = 0
    files_changed: int = 0
    refs_total: int = 0
    uploaded: int = 0
    cached: int = 0
    failed: int = 0
    missing: int = 0
    remote: int = 0
    replacements: int = 0


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
class Printer:
    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self._lock = threading.Lock()

    def say(self, message: str = "") -> None:
        if self.quiet:
            return
        with self._lock:
            print(message, flush=True)

    def always(self, message: str = "") -> None:
        with self._lock:
            print(message, flush=True)

    def warn(self, message: str) -> None:
        with self._lock:
            print(message, flush=True)


def _safe_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def _prescan(argv: Sequence[str], flag: str) -> Optional[str]:
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(flag + "="):
            return item.split("=", 1)[1]
    return None


def _default_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def _default_config_path(argv: Sequence[str]) -> Optional[str]:
    explicit = _prescan(argv, "--config")
    if explicit:
        return explicit
    for base in (_default_root(), os.getcwd()):
        candidate = os.path.join(base, CONFIG_NAME)
        if os.path.isfile(candidate):
            return candidate
    return None


def load_config(path: Optional[str]) -> Dict[str, object]:
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[警告] 配置文件读取失败，已忽略：{path}（{exc}）")
        return {}
    return data if isinstance(data, dict) else {}


def build_parser(config: Dict[str, object], config_path: Optional[str]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="typora-picgo-uploader",
        description="把 Markdown 里的本地图片批量上传到 PicGo，并自动替换为图床链接。",
        epilog=_EXTRA_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", nargs="?", help="要处理的目录或单个 .md 文件（默认：程序所在目录）")
    parser.add_argument("--dir", dest="dir_option", help="同 path，显式指定目录")
    parser.add_argument("-c", "--concurrency", type=int, default=int(config.get("concurrency", DEFAULT_CONCURRENCY)), help="并发上传数（默认 4）")
    parser.add_argument("-e", "--endpoint", default=str(config.get("endpoint", DEFAULT_ENDPOINT)), help="PicGo Server 地址")
    parser.add_argument("--timeout", type=float, default=float(config.get("timeout", 60)), help="单张图片超时秒数（默认 60）")
    parser.add_argument("--retries", type=int, default=int(config.get("retries", 3)), help="失败重试次数（默认 3）")
    parser.add_argument("--no-recursive", action="store_true", default=not bool(config.get("recursive", True)), help="不递归子目录")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要上传的图片，不上传也不修改文件")
    parser.add_argument("--backup", action="store_true", default=bool(config.get("backup", False)), help="写回前生成 .bak 备份")
    parser.add_argument("--no-cache", action="store_true", help="不使用内容去重缓存")
    parser.add_argument("--cache", help="自定义缓存文件路径")
    parser.add_argument("--report", nargs="?", const="", help="把处理报告写入指定文件（不带值为自动命名）")
    parser.add_argument("--launch-picgo", action="store_true", help="PicGo 未运行时尝试自动启动")
    parser.add_argument("--config", default=config_path, help=f"配置文件路径（默认查找 {CONFIG_NAME}）")
    parser.add_argument("-q", "--quiet", action="store_true", help="只输出结果摘要")
    parser.add_argument("-V", "--version", action="version", version=f"typora-picgo-uploader {__version__}")
    return parser


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    _safe_console()
    argv = list(sys.argv[1:] if argv is None else argv)

    config_path = _default_config_path(argv)
    config = load_config(config_path)
    args = build_parser(config, config_path).parse_args(argv)

    out = Printer(quiet=args.quiet)
    started = time.time()
    stats = Stats()

    target = args.dir_option or args.path or _default_root()
    if not os.path.exists(target):
        out.always(f"[错误] 路径不存在：{target}")
        return 2

    recursive = not args.no_recursive
    if os.path.isfile(target):
        if not target.lower().endswith((".md", ".markdown")):
            out.always(f"[错误] 不是 Markdown 文件：{target}")
            return 2
        root_dir = os.path.dirname(os.path.abspath(target))
        files = [os.path.abspath(target)]
    else:
        root_dir = os.path.abspath(target)
        files = list_markdown_files(root_dir, recursive)

    out.always(f"Typora PicGo Uploader v{__version__}")
    out.always(f"目录  {root_dir}")
    out.always(f"模式  {'仅预览（dry-run）' if args.dry_run else '上传并替换'} / {'递归' if recursive else '仅当前层'}")
    out.always(f"找到  {len(files)} 个 Markdown 文件")
    out.always()

    if not files:
        out.always("没有需要处理的文件。")
        return 0

    # ---------------- 阶段 1：解析 ---------------- #
    out.say("[1/3] 扫描与解析")
    jobs: List[FileJob] = []
    blob_paths: Dict[str, str] = {}
    blob_files: Dict[str, List[str]] = {}

    for file_path in files:
        stats.files_scanned += 1
        try:
            document = read_document(file_path)
        except OSError as exc:
            out.warn(f"  [跳过] 读取失败 {os.path.basename(file_path)}：{exc}")
            continue

        job = FileJob(path=file_path, document=document)
        md_dir = os.path.dirname(file_path)
        for ref in collect_image_refs(document.text):
            stats.refs_total += 1
            if is_remote(ref.dest):
                job.remote += 1
                stats.remote += 1
                continue
            resolved, reason = resolve_local_path(ref.dest, md_dir, root_dir)
            if resolved is None:
                if reason in ("not-found", "empty"):
                    job.missing.append(MissingRef(file=file_path, line=ref.line, dest=ref.dest))
                    stats.missing += 1
                continue
            try:
                digest = file_digest(resolved)
            except OSError as exc:
                job.missing.append(MissingRef(file=file_path, line=ref.line, dest=f"{ref.dest}（{exc}）"))
                stats.missing += 1
                continue
            job.items.append((ref, resolved, digest))
            blob_paths.setdefault(digest, resolved)
            blob_files.setdefault(digest, []).append(f"{os.path.basename(file_path)}:{ref.line}")
        jobs.append(job)

    out.say(f"  引用 {stats.refs_total} 处：远端 {stats.remote}，待处理 {sum(len(j.items) for j in jobs)}，缺失 {stats.missing}")
    out.say(f"  去重后需要上传 {len(blob_paths)} 张图片")
    out.say()

    # ---------------- 阶段 2：上传 ---------------- #
    cache = Cache(path=args.cache, enabled=not args.no_cache)
    results: Dict[str, Optional[str]] = {}
    pending: List[str] = []

    for digest, path in blob_paths.items():
        hit = cache.get(digest)
        if hit:
            results[digest] = hit
            stats.cached += 1
        else:
            results[digest] = None
            pending.append(digest)

    if args.dry_run:
        out.say("[2/3] 仅预览，跳过上传")
        for digest in pending:
            out.say(f"  待上传  {blob_paths[digest]}")
        for digest in blob_paths:
            if results.get(digest):
                out.say(f"  命中缓存 {blob_paths[digest]}")
        out.say()
        _print_report(out, stats, jobs, blob_paths, args, started, dry_run=True, pending=len(pending))
        return 0

    if pending:
        client = PicGoClient(endpoint=args.endpoint, timeout=args.timeout, retries=args.retries)
        if not client.probe():
            if args.launch_picgo:
                executable = find_picgo_executable()
                if executable:
                    out.always(f"  PicGo 未运行，正在启动：{executable}")
                    if launch_picgo(executable, args.endpoint) and client.probe():
                        out.always("  PicGo 已就绪")
                    else:
                        out.always("  [错误] 启动 PicGo 超时")
                        return 2
                else:
                    out.always("  [错误] 未找到 PicGo 安装路径，请手动启动")
                    return 2
            else:
                out.always(f"  [错误] 无法连接 PicGo Server：{args.endpoint}")
                out.always("  请确认 PicGo 已启动，并在「设置 → 上传 → 开启 Server」中启用，端口默认 36677。")
                out.always("  也可以加 --launch-picgo 让本程序尝试自动启动，或用 -e 指定其他地址。")
                return 2

        out.say(f"[2/3] 上传图片（待上传 {len(pending)} 张，命中缓存 {stats.cached} 张）")
        total = len(pending)
        done = 0
        counter_lock = threading.Lock()

        def worker(digest: str) -> Tuple[str, Optional[str], Optional[str]]:
            path = blob_paths[digest]
            try:
                return digest, client.upload(path), None
            except PicGoError as exc:
                return digest, None, str(exc)

        with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
            futures = [pool.submit(worker, digest) for digest in pending]
            for future in as_completed(futures):
                digest, url, error = future.result()
                with counter_lock:
                    done += 1
                    prefix = f"  [{done:0{len(str(total))}d}/{total}]"
                if url:
                    results[digest] = url
                    stats.uploaded += 1
                    cache.put(digest, url, blob_paths[digest])
                    out.say(f"{prefix} 成功 {os.path.basename(blob_paths[digest])} -> {url}")
                else:
                    stats.failed += 1
                    out.warn(f"{prefix} 失败 {blob_paths[digest]}：{error}")
        cache.save()
    else:
        out.say("[2/3] 全部命中缓存，无需上传")

    out.say()

    # ---------------- 阶段 3：写回 ---------------- #
    out.say("[3/3] 写回 Markdown")
    changed_files = 0
    for job in jobs:
        replacements: List[Tuple[int, int, str]] = []
        for ref, _path, digest in job.items:
            url = results.get(digest)
            if url:
                replacements.append((ref.dest_start, ref.dest_end, url))
        if not replacements:
            continue
        new_text = apply_replacements(job.document.text, replacements)
        try:
            written = write_document(job.document, new_text, backup=args.backup)
        except OSError as exc:
            out.warn(f"  [失败] 写入 {os.path.basename(job.path)}：{exc}")
            continue
        if written:
            changed_files += 1
            stats.files_changed += 1
            stats.replacements += len(replacements)
            out.say(f"  {os.path.relpath(job.path, root_dir)}  +{len(replacements)}")

    if changed_files == 0:
        out.say("  没有文件需要修改")

    out.say()
    _print_report(out, stats, jobs, blob_paths, args, started, dry_run=False)
    return 1 if stats.failed else 0


# --------------------------------------------------------------------------- #
# 报告
# --------------------------------------------------------------------------- #
def _print_report(
    out: Printer,
    stats: Stats,
    jobs: List[FileJob],
    blob_paths: Dict[str, str],
    args: argparse.Namespace,
    started: float,
    dry_run: bool,
    pending: int = 0,
) -> None:
    elapsed = time.time() - started
    lines: List[str] = []
    lines.append("=" * 56)
    lines.append("汇总" + ("（dry-run，未做任何修改）" if dry_run else ""))
    lines.append("=" * 56)
    lines.append(f"  文件     扫描 {stats.files_scanned}，修改 {stats.files_changed}，替换 {stats.replacements} 处")
    lines.append(f"  图片     唯一 {len(blob_paths)} 张")
    if dry_run:
        lines.append(f"  上传     待上传 {pending} 张，缓存命中 {stats.cached} 张")
    else:
        lines.append(f"  上传     成功 {stats.uploaded}，缓存命中 {stats.cached}，失败 {stats.failed}")
    if stats.missing:
        lines.append(f"  缺失     {stats.missing} 处本地图片不存在")
    lines.append(f"  耗时     {elapsed:.1f}s")

    if stats.missing:
        lines.append("")
        lines.append("以下图片在磁盘上找不到（已原样保留链接）：")
        shown = 0
        for job in jobs:
            for item in job.missing:
                if shown >= 30:
                    lines.append("  ...（省略）")
                    break
                lines.append(f"  {os.path.basename(item.file)}:{item.line}  {item.dest}")
                shown += 1
        lines.append("")

    text = "\n".join(lines)
    out.always(text)

    report_target = args.report
    if report_target is None:
        return
    if report_target == "":
        report_target = os.path.join(_default_root(), time.strftime("upload-report-%Y%m%d-%H%M%S.txt"))
    try:
        with open(report_target, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
            for job in jobs:
                if job.missing:
                    handle.write("\n[缺失] " + job.path + "\n")
                    for item in job.missing:
                        handle.write(f"  {item.line}: {item.dest}\n")
        out.always(f"\n报告已写入：{report_target}")
    except OSError as exc:
        out.warn(f"[警告] 报告写入失败：{exc}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
