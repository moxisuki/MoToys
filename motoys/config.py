"""默认配置、config.json 读写、工作目录解析。"""

import json
import os

from .base import CONFIG_PATH, PLUGIN_DIR, log
from .winapi import active_explorer_path


DEFAULT_CONFIG = {
    "terminal": "wt",
    "shell": "pwsh",
    "keep_open": True,
    "directory": "explorer",
    "fallback_directory": "",
    # 打开 config.json 用的编辑器；留空则退回系统默认关联（本机 .json 没关联，
    # 会弹出「选取应用」对话框，所以默认写死 notepad）
    "editor": "notepad",
    # 结果排序权重：给高值才能排在 Flow 内置的 Web Search 前面
    "score": 100000,
    "programs": [
        {"name": "Kimi Code", "command": "kimi"},
        {"name": "DSH Agent", "command": "dsh"},
        {"name": "Node.js REPL", "command": "node"},
    ],
    "kimi": {
        "sessions_dir": "",            # 留空 = %USERPROFILE%\.kimi-code\sessions
        "limit": 12,                   # 一次最多列多少个会话
        "include_archived": False,
        "new_command": "kimi",
        "continue_command": "kimi -c",
        "resume_command": "kimi -S {id}",
        # 正文深搜：只在前面的元数据都没命中时才做，且三重上限
        "deep_search": True,
        "deep_files": 10,              # 最多看最近多少个会话的 wire.jsonl
        "deep_bytes": 262144,          # 每个文件只读末尾这么多字节
        "deep_min_query": 2,           # 关键词短于此长度不深搜
    },
}


# 当前查询使用的排序权重（handle_query 开头按 config.json 刷新，items 通过 config.SCORE 读取）
SCORE = 100000


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=4)
            fh.write("\n")
    except Exception as exc:
        log("写 config.json 失败：%r" % (exc,))


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as fh:
            user = json.load(fh)
    except FileNotFoundError:
        save_config(cfg)
        return cfg
    except Exception as exc:
        log("config.json 解析失败，用默认配置：%r" % (exc,))
        return cfg

    if isinstance(user, dict):
        for key, value in user.items():
            if key == "programs":
                if isinstance(value, list):
                    cfg["programs"] = [
                        p for p in value
                        if isinstance(p, dict) and str(p.get("command") or "").strip()
                    ]
            elif key == "kimi":
                if isinstance(value, dict):
                    cfg["kimi"].update(value)
            else:
                cfg[key] = value
    return cfg


def resolve_directory(cfg):
    """返回 (目录, 来源说明)。"""
    mode = str(cfg.get("directory") or "explorer").strip()
    low = mode.lower()

    if low in ("explorer", "auto", ""):
        path = active_explorer_path()
        if path:
            return path, "资源管理器"
        fallback = str(cfg.get("fallback_directory") or "").strip()
        if fallback:
            path = os.path.expandvars(os.path.expanduser(fallback))
            if os.path.isdir(path):
                return path, "兜底目录"
        return os.path.expanduser("~"), "用户主目录"

    if low in ("home", "userprofile", "~"):
        return os.path.expanduser("~"), "用户主目录"
    if low == "desktop":
        path = os.path.join(os.path.expanduser("~"), "Desktop")
        if os.path.isdir(path):
            return path, "桌面"
    if low == "plugin":
        return PLUGIN_DIR, "插件目录"

    path = os.path.expandvars(os.path.expanduser(mode))
    if os.path.isdir(path):
        return os.path.abspath(path), "配置的固定目录"
    log("配置的目录不存在：%r" % (mode,))
    return os.path.expanduser("~"), "用户主目录"
