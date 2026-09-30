"""结果项构造（Title/SubTitle/IcoPath/JsonRPCAction）与前缀路由。"""

import os

from . import config
from . import icons
from .base import CONFIG_PATH, ICON, ICON_CONFIG, ICON_KIMI, ICON_RESTART, ICON_TERM
from .config import resolve_directory
from .launch import command_available
from .sessions import _one_line, _relative_time, _search_sessions_deep, _search_sessions_meta, _sessions_root, load_sessions


# 前缀 -> 模式。只有「第一个词」完全等于这里的某个键才路由。
ALIASES = {
    "rex": "restart",
    "restart": "restart",
    "rs": "restart",
    "重启": "restart",
    "term": "term",
    "terminal": "term",
    "终端": "term",
    "kimi": "kimi",
    "km": "kimi",
    "motoys": "hub",
}


def _session_icon(session):
    """标题 → 首条提问 → 工作目录名：取第一个能画出首字的文本。"""
    cwd = str(session.get("cwd") or "").replace("/", "\\").rstrip("\\")
    for text in (session.get("title"), session.get("lastPrompt"), os.path.basename(cwd)):
        if text and icons.initials(text):
            return icons.icon_for(text, ICON_KIMI)
    return ICON_KIMI


def _session_item(session, deep=False):
    title = _one_line(session.get("title"), 70) or "(无标题会话)"
    parts = [_relative_time(session.get("updatedAt"))]
    prompt = _one_line(session.get("lastPrompt"), 60)
    if prompt:
        parts.append("首条：" + prompt)
    cwd = str(session.get("cwd") or "").strip()
    if cwd:
        parts.append(os.path.normpath(cwd.replace("/", "\\")))
    if deep:
        parts.insert(0, "正文命中")
    return _item(
        title,
        "    ·    ".join(parts),
        {"method": "open_session", "parameters": [str(session.get("_path") or "")]},
        icon=_session_icon(session),
    )


def kimi_items(text, cfg):
    km = dict(cfg.get("kimi") or {})
    limit = max(1, int(km.get("limit") or 12))
    sessions = load_sessions(cfg)
    items = []

    if text:
        hits = _search_sessions_meta(sessions, text, limit)
        deep_hits = []
        if len(hits) < limit and km.get("deep_search", True):
            deep_hits = _search_sessions_deep(sessions, text, limit, km, hits)
        for session in hits[:limit]:
            items.append(_session_item(session))
        for session in deep_hits[:max(0, limit - len(items))]:
            items.append(_session_item(session, deep=True))
        if not items:
            items.append(_item(
                "没有匹配的会话",
                "已搜索 %d 个会话的标题、首条提问、工作目录%s"
                % (len(sessions), "与会话正文" if km.get("deep_search", True) else ""),
                icon=ICON_KIMI,
            ))
            directory, source = resolve_directory(cfg)
            items.append(_item(
                "在当前位置开始新的 Kimi 会话",
                "%s    ·    目录：%s  [%s]" % (km.get("new_command") or "kimi", directory, source),
                {"method": "run", "parameters": [str(km.get("new_command") or "kimi")]},
                icon=ICON_TERM,
            ))
        return items

    directory, source = resolve_directory(cfg)
    for session in sessions[:limit]:
        items.append(_session_item(session))
    if not sessions:
        items.append(_item(
            "没有找到任何 Kimi 会话",
            "会话目录：%s" % _sessions_root(cfg),
            icon=ICON_KIMI,
        ))
    new_command = str(km.get("new_command") or "kimi")
    items.append(_item(
        "在当前位置开始新的 Kimi 会话",
        "%s    ·    目录：%s  [%s]" % (new_command, directory, source),
        {"method": "run", "parameters": [new_command]},
        icon=ICON_KIMI,
    ))
    items.append(_item(
        "继续这个目录的上一个 Kimi 会话",
        "%s    ·    目录：%s  [%s]" % (km.get("continue_command") or "kimi -c", directory, source),
        {"method": "run", "parameters": [str(km.get("continue_command") or "kimi -c")]},
        icon=ICON_TERM,
    ))
    items.append(_item("编辑配置 config.json", CONFIG_PATH,
                       {"method": "edit_config", "parameters": []}, icon=ICON_CONFIG))
    return items


def _item(title, subtitle, action=None, icon=ICON, score=None):
    item = {
        "Title": title,
        "SubTitle": subtitle,
        "IcoPath": icon,
        "Score": config.SCORE if score is None else score,
    }
    if action:
        item["JsonRPCAction"] = action
    return item


def _terminal_label(cfg):
    term = str(cfg.get("terminal") or "wt").strip() or "wt"
    if term.lower() in ("wt", "wt.exe", "windowsterminal", "windowsterminal.exe"):
        shell = str(cfg.get("shell") or "cmd").strip()
        return "Windows Terminal（%s）" % (shell if shell else "默认配置")
    return term


# --------------------------------------------------------------------------- #
# 各模式的结果
# --------------------------------------------------------------------------- #
RESTART_ITEMS = (
    {
        "method": "restart",
        "title": "重启资源管理器",
        "subtitle": "结束 explorer.exe 并重新启动外壳（任务栏 / 桌面 / 文件管理器）",
        "keys": "restart 重启 重启资源管理器 explorer rex shell 资源管理器 任务栏 桌面 卡死",
    },
    {
        "method": "restart_this_pc",
        "title": "重启资源管理器并打开「此电脑」",
        "subtitle": "重启外壳，就绪后自动打开「此电脑」窗口",
        "keys": "restart 重启 此电脑 this pc open 打开 mycomputer computer",
    },
    {
        "method": "open_new",
        "title": "打开新的资源管理器窗口",
        "subtitle": "不重启，直接打开「此电脑」",
        "keys": "open 打开 新窗口 new window 此电脑 explorer",
    },
)


def restart_items(text, cfg):
    tokens = [tok for tok in text.lower().replace("\u3000", " ").split() if tok]
    if not tokens:
        chosen = list(RESTART_ITEMS)
    else:
        chosen = [item for item in RESTART_ITEMS if all(tok in item["keys"] for tok in tokens)]
        if not chosen:
            chosen = [item for item in RESTART_ITEMS if any(tok in item["keys"] for tok in tokens)]
    return [
        _item(item["title"], item["subtitle"],
              {"method": item["method"], "parameters": []}, icon=ICON_RESTART)
        for item in chosen
    ]


def term_items(text, cfg):
    directory, source = resolve_directory(cfg)
    shown = directory if os.path.isdir(directory) else directory + "（目录不存在）"
    where = "目录：%s  [%s]" % (shown, source)
    lowered = text.lower()

    items = []
    for prog in cfg.get("programs") or []:
        name = str(prog.get("name") or prog.get("command") or "").strip()
        command = str(prog.get("command") or "").strip()
        if text and lowered not in name.lower() and lowered not in command.lower():
            continue
        missing = "" if command_available(command) else "未在 PATH 中找到该命令    ·    "
        items.append(_item(
            "在当前目录运行：" + name,
            "%s%s    ·    %s" % (missing, command, where),
            {"method": "run", "parameters": [command]},
            icon=ICON_TERM,
        ))

    if text:
        items.append(_item(
            "运行「%s」" % text,
            "%s    ·    %s" % (text, where),
            {"method": "run", "parameters": [text]},
            icon=ICON_TERM,
        ))
    else:
        items.append(_item(
            "在当前目录打开终端",
            "%s    ·    %s" % (_terminal_label(cfg), where),
            {"method": "run", "parameters": [""]},
            icon=ICON_TERM,
        ))
        items.append(_item("编辑配置 config.json", CONFIG_PATH,
                           {"method": "edit_config", "parameters": []}, icon=ICON_CONFIG))
    return items


def hub_items(cfg):
    directory, source = resolve_directory(cfg)
    where = "目录：%s  [%s]" % (directory, source)
    km = dict(cfg.get("kimi") or {})
    items = []

    for item in RESTART_ITEMS:
        items.append(_item(item["title"], item["subtitle"] + "    ·    关键字：rex",
                           {"method": item["method"], "parameters": []}, icon=ICON_RESTART))

    items.append(_item(
        "在当前目录打开终端",
        "%s    ·    %s    ·    关键字：term" % (_terminal_label(cfg), where),
        {"method": "run", "parameters": [""]},
        icon=ICON_TERM,
    ))
    items.append(_item(
        "在当前位置开始新的 Kimi 会话",
        "%s    ·    %s    ·    关键字：kimi" % (km.get("new_command") or "kimi", where),
        {"method": "run", "parameters": [str(km.get("new_command") or "kimi")]},
        icon=ICON_KIMI,
    ))
    items.append(_item(
        "继续这个目录的上一个 Kimi 会话",
        "%s    ·    关键字：kimi" % (km.get("continue_command") or "kimi -c"),
        {"method": "run", "parameters": [str(km.get("continue_command") or "kimi -c")]},
        icon=ICON_TERM,
    ))

    sessions = load_sessions(cfg)
    if sessions:
        latest = sessions[0]
        items.append(_item(
            "恢复最近的 Kimi 会话：" + (_one_line(latest.get("title"), 50) or "(无标题会话)"),
            "%s    ·    %s    ·    关键字：kimi"
            % (_relative_time(latest.get("updatedAt")),
               os.path.normpath(str(latest.get("cwd") or "").replace("/", "\\"))),
            {"method": "open_session", "parameters": [str(latest.get("_path") or "")]},
            icon=ICON_KIMI,
        ))
    else:
        items.append(_item("Kimi 会话", "没有找到会话    ·    关键字：kimi",
                           icon=ICON_KIMI))

    items.append(_item("编辑配置 config.json", CONFIG_PATH,
                       {"method": "edit_config", "parameters": []}, icon=ICON_CONFIG))
    return items


def route(raw):
    """把查询原文拆成 (模式, 剩余文本)；模式为 None 表示不是给我们的。"""
    text = (raw or "").strip()
    if not text:
        return None, ""
    parts = text.split(None, 1)
    head = parts[0].lower()
    mode = ALIASES.get(head)
    if mode is None:
        return None, ""
    return mode, (parts[1].strip() if len(parts) > 1 else "")
