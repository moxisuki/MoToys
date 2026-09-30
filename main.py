# -*- coding: utf-8 -*-
"""MoToys —— Flow Launcher 一体化插件（协议入口）

    rex [过滤]      重启资源管理器 / 重启并打开「此电脑」/ 打开新的资源管理器窗口
    term [内容]     在当前（资源管理器）目录打开终端，并运行预设或指定程序
    kimi [关键词]   列出 / 搜索 kimi 会话；回车在该会话的工作目录里恢复它
    motoys          一屏总览：上面三块的常用动作 + 编辑配置

为什么用前缀路由而不是注册关键词：旧版 Python 插件协议下，Flow 只把「关键词之后」
的文本传进来，插件**看不到**命中的是哪个关键词；而 plugin.json 里
`ActionKeywords: ["*"]` 的通配插件能拿到用户输入的完整原文。所以用 `["*"]` + 自写
前缀路由。代价是每次按键都会起一个 pythonw —— main() 里第一件事就是判断前缀，
不匹配立刻空返回（不读配置、不碰 COM、不扫会话）。

代码结构
--------
    main.py            本文件：协议入口 + 查询分发 + 动作表 + 自测
    motoys/base.py     路径、图标常量、日志
    motoys/config.py   默认配置、config.json 读写、目录解析
    motoys/winapi.py   IShellWindows COM：读资源管理器当前目录
    motoys/launch.py   终端形态与进程拉起（含脱离 Flow Job 的三档尝试）
    motoys/restart.py  重启资源管理器
    motoys/sessions.py Kimi 会话索引与搜索（含缓存）
    motoys/icons.py    会话首字图标
    motoys/items.py    结果项构造与前缀路由
    motoys/actions.py  六个动作的实现

命令行自测
----------
    python main.py --selftest           # 配置 / 目录 / COM / 会话缓存 / 各预设命令行
    python main.py --dir                # 只打印解析出的当前目录
    python main.py --route "term kimi"  # 只看前缀路由结果
    python main.py --sessions [关键词]  # 只跑会话列表 / 搜索（含耗时）
"""

import json
import os
import sys
import time

# 让 `import motoys` 无论工作目录在哪都能成立（Flow 用 argv 调 main.py）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from motoys.actions import action_edit_config, action_open_new, action_open_session, action_restart, action_restart_this_pc, action_run
from motoys.base import CACHE_PATH, PLUGIN_DIR, log
from motoys.config import load_config, resolve_directory
from motoys.items import hub_items, kimi_items, restart_items, route, term_items
from motoys.launch import build_launch, command_available
from motoys.restart import _explorer_pids, _shell_up
from motoys.sessions import _one_line, _relative_time, _sessions_root, load_sessions
from motoys.winapi import _find_browser_window, _shell_windows, _zorder_cabinet_windows, url_to_path
from motoys import config



def handle_query(parameters):
    raw = ""
    if parameters and isinstance(parameters[0], str):
        raw = parameters[0]

    mode, text = route(raw)
    if mode is None:
        return []           # 不是我们的查询：立刻空手而归，不读配置、不碰 COM

    cfg = load_config()
    try:
        config.SCORE = int(cfg.get("score") or 100000)
    except (TypeError, ValueError):
        config.SCORE = 100000

    if mode == "restart":
        return restart_items(text, cfg)
    if mode == "term":
        return term_items(text, cfg)
    if mode == "kimi":
        return kimi_items(text, cfg)
    return hub_items(cfg)


ACTIONS = {
    "restart": action_restart,
    "restart_this_pc": action_restart_this_pc,
    "open_new": action_open_new,
    "run": action_run,
    "open_session": action_open_session,
    "edit_config": action_edit_config,
}


def dispatch(method, parameters):
    if method == "query":
        return {"result": handle_query(parameters), "debugMessage": ""}
    if method == "context_menu":
        return {"result": [], "debugMessage": ""}

    action = ACTIONS.get(method)
    if action is None:
        log("未知方法：%r" % (method,))
        return None

    try:
        log("action %s -> %r" % (method, action(*parameters)))
    except Exception as exc:
        log("action %s 失败：%r" % (method, exc))
    return None  # 动作调用不回包，与官方 Python 模板一致


def selftest(argv):
    which = argv[1] if len(argv) > 1 else "--selftest"
    cfg = load_config()

    if which == "--dir":
        print(resolve_directory(cfg)[0])
        return 0

    if which == "--route":
        text = argv[2] if len(argv) > 2 else ""
        print("route(%r) -> %r" % (text, route(text)))
        return 0

    if which == "--sessions":
        text = argv[2] if len(argv) > 2 else ""
        started = time.perf_counter()
        sessions = load_sessions(cfg)
        mid = time.perf_counter()
        items = kimi_items(text, cfg)
        done = time.perf_counter()
        print("会话数 %d   根目录 %s" % (len(sessions), _sessions_root(cfg)))
        print("load_sessions %.1f ms   kimi_items %.1f ms   合计 %.1f ms"
              % ((mid - started) * 1000, (done - mid) * 1000, (done - started) * 1000))
        print("结果 %d 条：" % len(items))
        for item in items:
            print("  - %s\n      %s" % (item["Title"], item["SubTitle"]))
        return 0

    directory, source = resolve_directory(cfg)
    print("插件目录   : %s" % PLUGIN_DIR)
    print("配置       : %s" % json.dumps({k: v for k, v in cfg.items() if k != "programs"},
                                         ensure_ascii=False))
    print("当前目录   : %s  [%s]" % (directory, source))
    try:
        print("FindWindowSW : %r" % (_find_browser_window(),))
    except Exception as exc:
        print("FindWindowSW : 异常 %r" % (exc,))
    try:
        for hwnd, url in _shell_windows():
            print("ShellWindow  : hwnd=0x%X  url=%s  ->  %s" % (hwnd, url, url_to_path(url)))
    except Exception as exc:
        print("ShellWindows : 异常 %r" % (exc,))
    print("z 序窗口     : %s" % ["0x%X" % h for h in _zorder_cabinet_windows()])
    print("explorer pid : %s   shell_up=%s" % (_explorer_pids(), _shell_up()))

    started = time.perf_counter()
    sessions = load_sessions(cfg)
    cost = (time.perf_counter() - started) * 1000
    print("会话         : %d 个（%.1f ms，缓存 %s）" % (len(sessions), cost, CACHE_PATH))
    for session in sessions[:3]:
        print("               %s | %s | %s" % (_relative_time(session.get("updatedAt")),
                                              _one_line(session.get("title"), 28),
                                              session.get("cwd")))

    for prog in cfg.get("programs") or []:
        command = str(prog.get("command") or "")
        print("预设 %-12s -> %s  (可用=%s)"
              % (prog.get("name"), build_launch(cfg, command, directory), command_available(command)))
    print("纯终端       -> %s" % build_launch(cfg, "", directory))
    return 0


def _emit(payload):
    # ensure_ascii=True：输出纯 ASCII，避免宿主管道编码差异导致中文乱码
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _params(request):
    params = request.get("parameters")
    if isinstance(params, list):
        return params
    if params in (None, ""):
        return []
    return [params]


def main(argv):
    if len(argv) > 1 and argv[1] in ("--selftest", "--dir", "--route", "--sessions", "-s"):
        return selftest(argv)

    if len(argv) > 1:
        try:
            request = json.loads(argv[1])
        except Exception as exc:
            log("argv 不是 JSON：%r（%r）" % (argv[1], exc))
            request = None
        if isinstance(request, dict):
            response = dispatch(str(request.get("method") or ""), _params(request))
            if response is not None:
                _emit(response)
            return 0

    for line in sys.stdin:  # 流式兜底
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception as exc:
            log("stdin 不是 JSON：%r（%r）" % (line, exc))
            continue
        if not isinstance(request, dict):
            continue
        response = dispatch(str(request.get("method") or ""), _params(request))
        if response is not None:
            _emit(response)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
