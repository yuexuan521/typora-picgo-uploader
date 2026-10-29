# typora-picgo-uploader

把 Markdown 里的**本地图片**批量上传到 [PicGo](https://github.com/Molunerfinn/PicGo) 图床，并自动把链接替换成远端地址。

为 Typora / Obsidian 写博客的人准备的补票工具：文章是本地写完的，图片还躺在 `assets/` 里，
发布前需要一次性换成图床链接——这个程序就是干这个的。

![python](https://img.shields.io/badge/python-3.8%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

---

## 为什么不用现成脚本

网上流传的版本大致长这样：

```python
pattern = re.compile(r'!\[(.*?)\]\((.*?)\)')
new_content = pattern.sub(replace_callback, content)
```

跑十篇以内大概没事，跑一个真实博客仓库就会遇到下面这些问题——本项目逐条处理掉了：

| 问题 | 后果 | 本项目的做法 |
| --- | --- | --- |
| 代码块里的 `![](...)` 也被替换 | 文章里的示例代码被改成图床链接 | 先算出围栏代码块 / 行内代码 / HTML 注释的区间，跳过 |
| 读文件用文本模式 | CRLF 被转成 LF，整篇 diff 全红 | 二进制读入 → 只改目标字符 → 二进制写回 |
| `TARGET_DIR = "./"` | 依赖当前工作目录，从别处调用就扫错地方 | 默认取**程序所在目录**，可用参数覆盖 |
| 路径含 `)`、`]`、空格 | 正则截断，链接被改坏 | 手写状态机解析，支持 `<a b.png>` 与 `a(1).png` |
| 只认 `![]()` | `<img src>`、引用式链接被漏掉 | 四种写法全覆盖 |
| 重复上传 | 同一张图被多篇引用就传多次，产生多个 URL | 按文件内容 SHA-1 去重，缓存跨运行有效 |
| 串行 + 无重试 | 几十张图很慢，偶发失败静默留本地链接 | 线程池并发 + 指数退避重试 + 失败清单 |
| 写一半崩了 | 文件被截断 | 临时文件 + 原子替换 |

## 特性

- **四种图片写法**：`![alt](path)`、`![alt](<path with space>)`、`<img src="path">`、引用式 `![alt][id]` + `[id]: path`
- **智能跳过**：代码块、行内代码、HTML 注释、已是 http(s) 的链接一律不碰
- **内容去重**：同一张图（哪怕文件名不同、被多篇引用）只上传一次
- **保真写回**：保留 CRLF/LF、BOM、原编码（含 GB18030 兜底）；内容没变就不碰文件
- **`--dry-run`**：先看清楚要传什么再动手
- **失败不破坏**：上传失败的图片保留原链接，退出码非 0，报告里列出清单
- **零运行时依赖**：只用 Python 标准库，打包后是单个 exe
- **拖拽即用**：把文件夹拖到 exe 图标上直接跑

## 安装

### 方式一：直接用 exe（Windows）

到 [Releases](../../releases) 下载 `typora-picgo-uploader.exe`，放到任意目录。

### 方式二：源码运行

```bash
git clone https://github.com/yuexuan521/typora-picgo-uploader.git
cd typora-picgo-uploader
python main.py --help
```

需要 Python 3.8+，无第三方依赖。

### 方式三：pip 安装

```bash
pip install .
typora-picgo-uploader --help
```

### 自己打包

```bat
build.bat
```

产物在 `dist\typora-picgo-uploader.exe`。

## 前置条件：PicGo 的 Server 要开

程序通过 PicGo 的 HTTP 接口上传，不关心你用的是哪个图床插件（SM.MS / 阿里云 OSS / 腾讯云 COS / 华为云 OBS / GitHub 都行）。

1. 打开 PicGo → **设置** → **设置 Server**
2. 确认端口为 `36677`（默认），点「确定」
3. 先在 PicGo 里手动拖一张图进去，确认上传能成功

> 开启后 PicGo 会常驻托盘，程序运行时它最小化到托盘也没关系。

## 使用

### 双击（最省事）

把 exe 丢进博客目录，双击。默认处理 **exe 所在目录及其子目录**里的所有 `.md`。

```
D:\blog\
├── typora-picgo-uploader.exe   ← 放这里
├── 第一篇.md
├── assets\ *.png
└── 子目录\ 第二篇.md
```

双击运行时程序会：

1. 先扫描并列出「即将上传 N 张图片、改写 M 个文件」；
2. 问一句 `确认继续？(Y/n)`，直接回车才动手，输入 `n` 一个字节都不改；
3. 跑完停在 `按回车键退出...`，**不会一闪而过**，方便你看汇总和报错。

> 想跳过确认（比如做成定时任务）：加 `-y`。
> 在脚本里调用又不想被暂停卡住：加 `--no-pause`。

### 拖拽

把文件夹或单个 `.md` 文件拖到 exe 图标上即可。

### 命令行

```bash
# 处理指定目录（递归）
typora-picgo-uploader.exe --dir "D:\blog"

# 只处理当前这一层，不递归
typora-picgo-uploader.exe --dir "D:\blog" --no-recursive

# 只处理一个文件
typora-picgo-uploader.exe "D:\blog\post.md"

# 先看看要传什么，不动文件
typora-picgo-uploader.exe --dir "D:\blog" --dry-run

# 8 并发、写回前留 .bak 备份、生成报告
typora-picgo-uploader.exe --dir "D:\blog" -c 8 --backup --report

# PicGo 没开？让程序自己拉起来
typora-picgo-uploader.exe --dir "D:\blog" --launch-picgo
```

### 参数一览

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `path` / `--dir` | 目标目录或单个 `.md` | 程序所在目录 |
| `-c, --concurrency` | 并发上传数 | `4` |
| `-e, --endpoint` | PicGo Server 地址 | `http://127.0.0.1:36677/upload` |
| `--timeout` | 单张图片超时（秒） | `60` |
| `--retries` | 失败重试次数 | `3` |
| `--no-recursive` | 不递归子目录 | 递归 |
| `--dry-run` | 只预览，不上传不写回 | 关 |
| `--backup` | 写回前生成 `.bak` | 关 |
| `--no-cache` | 禁用内容去重缓存 | 启用 |
| `--cache` | 自定义缓存文件路径 | `~/.typora-picgo-uploader/cache.json` |
| `--report [文件]` | 生成处理报告 | 关 |
| `--launch-picgo` | 尝试自动启动 PicGo | 关 |
| `-y, --yes` | 双击运行时不再询问，直接执行 | 关 |
| `--no-pause` | 结束后不等待回车（脚本调用） | 关 |
| `--config` | 配置文件路径 | 自动查找 |
| `-q, --quiet` | 只输出摘要 | 关 |

### 配置文件

在程序所在目录（或当前目录）放一个 `typora-picgo-uploader.json`，就不必每次敲参数。
参考 [`typora-picgo-uploader.example.json`](typora-picgo-uploader.example.json)：

```json
{
  "endpoint": "http://127.0.0.1:36677/upload",
  "concurrency": 4,
  "timeout": 60,
  "retries": 3,
  "recursive": true,
  "backup": false
}
```

命令行参数的优先级高于配置文件。

### 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 全部成功，或没有需要处理的图片 |
| `1` | 有图片上传失败，或有文件写入失败（失败的链接已原样保留） |
| `2` | 致命错误：路径不存在、PicGo 连不上（**文件未被修改**） |

## 输出长什么样

```
Typora PicGo Uploader v1.0.0
目录  D:\blog
模式  上传并替换 / 递归
找到  12 个 Markdown 文件

[1/3] 扫描与解析
  引用 87 处：远端 2，待处理 79，缺失 6
  去重后需要上传 41 张图片

[2/3] 上传图片（待上传 33 张，命中缓存 8 张）
  [01/33] 成功 03_1.png -> https://cdn.example.com/20261023095351625.png
  [02/33] 成功 03_2.png -> https://cdn.example.com/20261023095352156.png
  ...

[3/3] 写回 Markdown
  BUUCTF MD5.md  +3
  BUUCTF RSA.md  +2

========================================================
汇总
========================================================
  文件     扫描 12，修改 9，替换 72 处
  图片     唯一 41 张
  上传     成功 33，缓存命中 8，失败 0
  缺失     6 处本地图片不存在
  耗时     18.4s

以下图片在磁盘上找不到（已原样保留链接）：
  BUUCTF 摩丝.md:14  assets/05_2.png
  ...
```

## 关于去重缓存

缓存在 `~/.typora-picgo-uploader/cache.json`（可用 `TYPORA_PICGO_HOME` 环境变量改位置），
key 是图片内容的 SHA-1，value 是上传后的 URL。

- 同一张图被 5 篇文章引用 → 只上传 1 次
- 重跑一遍 → 全部命中缓存，秒完成
- 换了图床想重传 → `--no-cache` 或删掉缓存文件

缓存不放在博客目录，避免被一起提交到仓库。

## 常见问题

**Q: 提示"无法连接 PicGo Server"**

PicGo 没开，或者 Server 没启用。检查 PicGo → 设置 → 设置 Server，端口默认 `36677`。
如果改了端口，用 `-e http://127.0.0.1:你的端口/upload` 指定。

**Q: 上传成功但链接打不开**

图床插件的权限/存储桶配置问题，和本工具无关。先在 PicGo 里手动拖一张图验证。

**Q: 双击后窗口一闪就没了？**

正常情况下程序会停在「按回车键退出」等你。如果连这行字都没看到，说明它在启动阶段就崩了
（多见于杀毒软件拦截解压），此时打开 `cmd`，把 exe 拖进去回车运行，就能看到完整报错。

**Q: 提示「写入失败 / 该文件可能被编辑器占用」怎么办？**

图片其实已经传上去了，只是 `.md` 被编辑器独占锁定，链接没写回去。关掉那个文件（或整个 Typora）后
**原样再跑一次**即可——已上传的图片会命中缓存，不会重复上传，也不会占用你的图床空间。

**Q: 会不会误删我的图片？**

不会。本程序**只读**本地图片，从不删除、移动、重命名任何文件。改动仅限 `.md` 里的链接文本。

**Q: 会不会改到我手写的示例代码？**

不会。围栏代码块（```` ``` ```` 和 `~~~`）、行内代码、HTML 注释里的内容全部跳过。

**Q: 为什么 exe 有 10MB？**

PyInstaller 把 Python 解释器打进去了。介意的话用 `pip install .` 装成命令，或改用源码运行。

**Q: 杀毒软件报毒？**

PyInstaller 单文件模式常见的误报，加白名单即可。也可以自行 `build.bat` 从源码打包。

**Q: 想用 Typora 实时上传，而不是事后批量？**

那是另一件事：Typora → 偏好设置 → 图像 → 上传服务选 PicGo，插入图片时就会自动上传。
本工具解决的是「历史文章批量补票」。

## 已知限制

这些是明确取舍，不是 bug：

- **只认正文里的图片语法**。YAML front matter 里的 `cover: ./assets/a.png` 这类字段不会被处理（不同主题字段名不统一，误改风险更大）。
- **只跳过围栏代码块和行内代码**。用 4 个空格缩进的代码块里如果出现 `![](...)`，仍会被当成图片处理——因为缩进代码块和嵌套列表在文本层面无法可靠区分。
- **本地路径里的 `#` 和 `?` 会被当作锚点/查询串裁掉**。Windows 文件名虽然允许这两个字符，但和 Markdown 的语法歧义无法两全。
- **不处理 `obsidian://`、`ftp://` 等非 http 协议**，遇到直接跳过并在报告里归类为 unsupported。

## 项目结构

```
typora-picgo-uploader/
├── main.py                          # PyInstaller 入口
├── typora_picgo_uploader/
│   ├── cli.py                       # 参数解析与主流程
│   ├── parser.py                    # 图片引用解析、路径解析、受保护区间
│   ├── document.py                  # 保真读写（换行符 / BOM / 编码 / 原子替换）
│   ├── picgo.py                     # PicGo HTTP 客户端、探测、启动
│   ├── cache.py                     # SHA-1 去重缓存
│   └── scanner.py                   # Markdown 文件扫描
├── tests/                           # 50 个单元 / 端到端测试
├── build.bat                        # 本地打包
└── .github/workflows/build.yml      # CI：多平台测试 + 打 tag 自动出 exe
```

## Roadmap

- [ ] `--clean`：上传成功后删除本地 `assets/` 里的图片（默认关闭，需二次确认）
- [ ] `--watch`：监听目录，Typora 保存后自动处理当前文件
- [ ] `--ignore`：按 glob 排除文件
- [ ] 支持 B站/掘金等平台的图床迁移（下载远端图 → 重新上传）

## License

[MIT](LICENSE)

---

## English

Batch-upload local images referenced by Markdown files to [PicGo](https://github.com/Molunerfinn/PicGo),
then rewrite the links to the hosted URLs.

Built for people who wrote their blog posts offline in Typora/Obsidian and need to migrate
`assets/` images to an image host before publishing.

**Highlights**: handles `![alt](path)`, `<path with spaces>`, `<img src>`, and reference-style links;
skips fenced code blocks, inline code, and HTML comments; de-duplicates by content SHA-1;
preserves CRLF/LF, BOM and encoding; atomic writes; `--dry-run`; concurrent uploads with retries.

```bash
typora-picgo-uploader.exe --dir D:\blog --dry-run   # preview
typora-picgo-uploader.exe --dir D:\blog             # do it
```

Zero runtime dependencies. Requires PicGo's HTTP server enabled (default port `36677`).
