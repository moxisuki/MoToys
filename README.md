# MoToys

Flow Launcher 插件：重启资源管理器、在当前目录开终端跑程序、翻 Kimi 会话并在它的工作目录里接着聊。

## 关键词

| 输入 | 得到 |
| --- | --- |
| `rex` | 重启资源管理器 / 重启并打开「此电脑」/ 打开新的资源管理器窗口 |
| `term` | 在当前（资源管理器）目录开终端，运行预设或指定程序 |
| `kimi` | 列出最近会话 · 搜索会话 · 回车在该会话的工作目录里恢复它 |
| `motoys` | 一屏总览：上面三块的常用动作 + 编辑配置 |

别名：`restart` `rs` `重启` = `rex`；`terminal` `终端` = `term`；`km` = `kimi`。
前缀后面可以继续打字过滤：`rex 此电脑`、`term kimi`、`kimi 逆向`。

## 功能

**`rex` 重启资源管理器**
结束 `explorer.exe` 并自己拉起（不依赖系统的 AutoRestartShell，约 0.5–2 秒）；另两项是
「重启并打开『此电脑』」和「打开新的资源管理器窗口」。

**`term` 当前目录开终端**
默认 Windows Terminal + pwsh，工作目录取最前面那个文件资源管理器窗口的路径
（取不到退主目录）。不带参数时给：三个预设（`programs`，默认 Kimi Code / DSH Agent / Node.js REPL）、
「在当前目录打开终端」、「编辑配置」；带了文字就按名字过滤预设，并多一项「运行「…」」，
可以把任意命令直接丢进去。终端脱离 Flow 的进程组，关掉 Flow 也不受影响。

**`kimi` Kimi 会话**
列出最近 12 个会话（时间 · 首条提问 · 工作目录），或用关键词搜标题 / 首条提问 / 工作目录 /
会话正文。**回车 = 在那个会话的工作目录里打开终端并 `kimi -S <会话 id>`**，原地接着聊。
另外还有「开始新的 Kimi 会话」和「继续这个目录的上一个会话」。

**`motoys` 总览**
上面三块的常用动作 + 编辑配置，一屏点到。

## 配置（`config.json`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `terminal` | `"wt"` | 终端：`wt` / `cmd` / `pwsh` / 其它可执行文件名 |
| `shell` | `"pwsh"` | 终端里承载命令的 shell；`none` = 直接执行 |
| `keep_open` | `true` | 程序退出后保留窗口 |
| `directory` | `"explorer"` | `explorer` = 当前资源管理器目录；也可 `home` / `desktop` / `plugin` / 固定路径 |
| `fallback_directory` | `""` | 取不到资源管理器目录时的兜底 |
| `editor` | `"notepad"` | 「编辑配置」用哪个编辑器打开 |
| `score` | `100000` | 结果排序权重 |
| `programs` | Kimi Code / DSH Agent / Node.js REPL | `term` 的预设，每项 `{"name": …, "command": …}` |
| `kimi.sessions_dir` | `""` | 留空 = `%USERPROFILE%\.kimi-code\sessions` |
| `kimi.limit` | `12` | 一次最多列/搜几个会话 |
| `kimi.include_archived` | `false` | 是否包含归档会话 |
| `kimi.new_command` | `"kimi"` | 「开始新会话」执行的命令 |
| `kimi.continue_command` | `"kimi -c"` | 「继续上一个会话」执行的命令 |
| `kimi.resume_command` | `"kimi -S {id}"` | 恢复指定会话的模板，可用 `{id}` `{cwd}` `{title}` |
| `kimi.deep_search` | `true` | 是否搜会话正文 |
| `kimi.deep_files` | `10` | 正文搜索最多看几个会话 |
| `kimi.deep_bytes` | `262144` | 每个会话文件只读末尾多少字节 |
| `kimi.deep_min_query` | `2` | 关键词短于此长度不搜正文 |

## 安装

整个目录放进 `%APPDATA%\FlowLauncher\Plugins\`，重启 Flow Launcher。

> ⚠️ 本插件用 `*` 通配关键词，旧的 RestartExplorer / TerminalHere **必须停用**，
> 否则 `rex` / `term` 会被它们截走。这两个插件已挪到
> `%APPDATA%\FlowLauncher\Plugins-disabled-20260930\`（改回目录名即可恢复）。

卸载：删掉插件目录，重启 Flow。

## 排查

* 日志：插件目录下的 `motoys.log`（动作、spawn 结果、目录解析）。
* 自测：`python main.py --selftest` / `--dir` / `--route "term kimi"` / `--sessions 关键词`。
* 只依赖 Python 标准库；`kimi`、`wt.exe`、`pwsh.exe` 需在 PATH 上。
* `kimi` 列表为空：检查 `kimi.sessions_dir`，或删掉 `sessions_cache.json` 让它重建。

## 文件

`plugin.json` 清单 · `main.py` 全部逻辑 · `config.json` 配置 ·
`images/` 五个图标（`icon` `restart` `terminal` `kimi` `config`） · `tools/make_icon.py` 图标生成器 ·
`sessions_cache.json` 会话缓存（自动生成） · `motoys.log` 日志（自动生成）。
