# MoToys

Flow Launcher 插件：重启资源管理器、在当前目录开终端跑程序、翻 Kimi 会话并在它的工作目录里接着聊。

## 关键词

| 输入 | 得到 |
| --- | --- |
| `rex` | 重启资源管理器 / 重启并打开「此电脑」/ 打开新窗口 |
| `term` | 在当前（资源管理器）目录开终端，跑预设或指定程序 |
| `kimi` | 列会话 · 搜会话 · 回车在该会话的目录里恢复它 |
| `motoys` | 一屏总览上面三块 + 编辑配置 |

别名 `restart`/`rs`/`重启`、`terminal`/`终端`、`km`。前缀后面继续打字就是过滤：`rex 此电脑`、`term kimi`、`kimi 逆向`。

## 功能

* **重启资源管理器**：结束 `explorer.exe` 并自己拉起（不依赖系统的 AutoRestartShell，约 0.5–2 秒）。
* **当前目录开终端**：默认 Windows Terminal + pwsh，工作目录取最前面那个文件资源管理器窗口（取不到退主目录）。
  不带参数给三个预设（Kimi Code / DSH Agent / Node.js REPL）和「在当前目录打开终端」；
  带了文字就过滤预设，并多一项「运行「…」」。终端脱离 Flow，关掉 Flow 也不受影响。
* **Kimi 会话**：列最近 12 个（时间 · 首条提问 · 工作目录），可搜标题 / 首条提问 / 工作目录 / 会话正文；
  图标是标题的前一两个字。**回车 = 在那个会话的工作目录里开终端并 `kimi -S <会话 id>`**，原地接着聊。
* **`motoys` 总览**：上面三块的常用动作 + 编辑配置，一屏点到。

## 配置（`config.json`）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `terminal` / `shell` | `wt` / `pwsh` | 用哪个终端、终端里用哪个 shell（`none` = 直接执行） |
| `keep_open` | `true` | 程序退出后保留窗口 |
| `directory` / `fallback_directory` | `explorer` / `""` | 工作目录来源：`explorer` 当前资源管理器目录，或 `home` / `desktop` / `plugin` / 固定路径 |
| `editor` | `notepad` | 「编辑配置」用哪个编辑器打开 |
| `score` | `100000` | 结果排序权重 |
| `programs` | Kimi Code / DSH Agent / Node.js REPL | `term` 的预设，每项 `{"name": …, "command": …}` |
| `kimi.*` | `limit 12` · `new_command "kimi"` · `continue_command "kimi -c"` · `resume_command "kimi -S {id}"` · `sessions_dir ""` · `include_archived false` · `deep_search true` | 会话列表与恢复行为 |

## 安装

整个目录放进 `%APPDATA%\FlowLauncher\Plugins\`，重启 Flow Launcher。

> ⚠️ 本插件用 `*` 通配关键词，旧的 RestartExplorer / TerminalHere **必须停用**，
> 否则 `rex` / `term` 会被它们截走。这两个插件已挪到 `%APPDATA%\FlowLauncher\Plugins-disabled-20260930\`。

卸载：删掉插件目录，重启 Flow。

## 排查

* 日志：插件目录下的 `motoys.log`。
* 自测：`python main.py --selftest` / `--dir` / `--route "term kimi"` / `--sessions 关键词`。
* 只依赖 Python 标准库；`kimi`、`wt.exe`、`pwsh.exe` 需在 PATH 上。
* 会话列表空：检查 `kimi.sessions_dir`，或删掉 `sessions_cache.json` 让它重建。

## 文件

`plugin.json` · `main.py`（入口） · `motoys/`（逻辑包） · `config.json` · `images/` · `tools/make_icon.py`；
`sessions_cache.json`、`icon_cache/`、`motoys.log` 自动生成。
