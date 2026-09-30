"""路径、图标常量、日志。这里不 import 任何同包模块，避免环形依赖。"""

import os
import time



PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 包目录的上一级 = 插件根
CONFIG_PATH = os.path.join(PLUGIN_DIR, "config.json")
LOG_PATH = os.path.join(PLUGIN_DIR, "motoys.log")
CACHE_PATH = os.path.join(PLUGIN_DIR, "sessions_cache.json")

ICON = "images/icon.png"
ICON_RESTART = "images/restart.png"
ICON_TERM = "images/terminal.png"
ICON_KIMI = "images/kimi.png"
ICON_CONFIG = "images/config.png"

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- #
# 日志
# --------------------------------------------------------------------------- #
def log(message):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 128 * 1024:
            os.remove(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass
