"""六个动作的实现（重启 / 重启并打开此电脑 / 新窗口 / 跑命令 / 恢复会话 / 编辑配置）。"""

import os
import time

from .base import CONFIG_PATH, log
from .config import load_config, resolve_directory, save_config
from .launch import build_launch, spawn, split_command
from .restart import WINDOW_DELAY, _restart_explorer
from .sessions import _read_json


def action_restart():
    return {"running": _restart_explorer()}


def action_restart_this_pc():
    pids = _restart_explorer()
    time.sleep(WINDOW_DELAY)
    spawn(["explorer.exe", "shell:MyComputerFolder"])
    return {"running": pids, "opened": "shell:MyComputerFolder"}


def action_open_new():
    spawn(["explorer.exe", "shell:MyComputerFolder"])
    return {"opened": "shell:MyComputerFolder"}


# --------------------------------------------------------------------------- #
# Kimi 会话


def action_open_session(session_dir):
    cfg = load_config()
    km = dict(cfg.get("kimi") or {})
    state = _read_json(os.path.join(session_dir, "state.json")) or {}
    cwd = str(state.get("cwd") or "").strip()
    if cwd and not os.path.isdir(cwd):
        log("会话目录不存在，退回用户主目录：%r" % (cwd,))
        cwd = ""
    if not cwd:
        cwd = os.path.expanduser("~")
    session_id = str(state.get("id") or os.path.basename(os.path.dirname(session_dir)) or "").strip()
    template = str(km.get("resume_command") or "kimi -S {id}")
    try:
        command = template.format(id=session_id, cwd=cwd,
                                  title=state.get("title") or "")
    except Exception as exc:
        log("resume_command 模板错误：%r（%r）" % (template, exc))
        command = "kimi -S " + session_id
    argv = build_launch(cfg, command, cwd)
    ok, info = spawn(argv, cwd=cwd)
    log("action open_session id=%s dir=%r cwd=%r argv=%r -> %s %s"
        % (session_id, session_dir, cwd, argv, "OK" if ok else "失败", info))


# --------------------------------------------------------------------------- #
# 结果构造


def action_run(command, directory=None):
    cfg = load_config()
    if directory and os.path.isdir(directory):
        where, source = directory, "指定目录"
    else:
        where, source = resolve_directory(cfg)
    argv = build_launch(cfg, command or "", where)
    ok, info = spawn(argv, cwd=where)
    log("action run command=%r dir=%r 来源=%s argv=%r -> %s %s"
        % (command, where, source, argv, "OK" if ok else "失败", info))


def action_edit_config():
    cfg = load_config()
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)
    editor = str(cfg.get("editor") or "").strip()
    if editor:
        argv = split_command(editor) + [CONFIG_PATH]
        ok, info = spawn(argv)
        log("打开配置：%r -> %s %s" % (editor, "OK" if ok else "失败", info))
        if ok:
            return
    try:
        os.startfile(CONFIG_PATH)
        log("打开配置（系统关联）：%s" % CONFIG_PATH)
    except Exception as exc:
        log("打开配置失败：%r" % (exc,))
