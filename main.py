# -*- coding: utf-8 -*-
"""MoToys —— Flow Launcher 一体化插件（重启资源管理器 · 当前目录开终端 · Kimi 会话）

用法（前缀路由）
----------------
    rex [过滤]       重启资源管理器 / 重启并打开「此电脑」/ 打开新的资源管理器窗口
    term [内容]      在当前（资源管理器）目录打开终端，并运行预设或指定程序
    kimi [关键词]    列出 / 搜索 kimi 会话；回车在该会话的工作目录里恢复它
    motoys            一屏总览：上面三块的常用动作 + 编辑配置

为什么用前缀路由而不是注册关键词
--------------------------------
旧版 Python 插件协议下，Flow 只会把「关键词之后」的文本传给插件，插件**看不到**
命中的是哪个关键词；而 plugin.json 里 `ActionKeywords: ["*"]` 的通配插件能拿到
用户输入的完整原文（实测：`kimi` -> `["kimi"]`、`hello` -> `["hello"]`）。
所以这里用 `["*"]` + 自写前缀路由。代价是每次按键都会起一个 pythonw，
所以 main() 里第一件事就是判断前缀：不匹配的查询立刻回空列表，
不读配置、不碰 COM、不扫会话。实测冷启动 + 解析 ≈ 46 ms，可以接受。

结果排序
--------
每个结果都带 `Score`（默认 100000，config.json 可改）。实测 `*` 插件若不给
Score，`rex` 会排在 Web Search 后面；给了就稳定在第一。

数据来源
--------
Kimi 会话直接读 `%USERPROFILE%\\.kimi-code\\sessions\\<workspace>\\<session_id>\\state.json`，
不调用 `kimi session list`（那要起 node，1 秒以上）。本机全量解析 63 个
state.json 要 341 ms，所以用 sessions_cache.json 按 mtime+size 增量解析，
稳态 ≈ 10 ms。会话正文（wire.jsonl 合计 414 MB）只做有上限的浅层深搜。

命令行自测
----------
    python main.py --selftest           # 配置 / 目录 / 会话缓存 / 各预设命令行
    python main.py --dir                # 只打印解析出的当前目录
    python main.py --route "term kimi"  # 只看前缀路由结果
    python main.py --sessions [关键词]  # 只跑会话列表 / 搜索（含耗时）
"""

import ctypes
import json
import os
import sys
import time

try:
    import ctypes.wintypes as wintypes
except Exception:  # pragma: no cover
    wintypes = None

try:
    import winreg
except Exception:  # pragma: no cover
    winreg = None

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(PLUGIN_DIR, "config.json")
LOG_PATH = os.path.join(PLUGIN_DIR, "motoys.log")
CACHE_PATH = os.path.join(PLUGIN_DIR, "sessions_cache.json")

ICON = "images/icon.png"
ICON_RESTART = "images/restart.png"
ICON_TERM = "images/terminal.png"
ICON_KIMI = "images/kimi.png"
ICON_CONFIG = "images/config.png"

IS_WINDOWS = os.name == "nt"

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

# 进程创建标志
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

CLSID_SHELLWINDOWS = "{9BA05972-F6A8-11CF-A442-00A0C90A8F39}"
IID_ISHELLWINDOWS = "{85CB6900-4D95-11CF-960C-0080C7F4EE85}"

SWC_BROWSER = 1        # 文件资源管理器窗口
SWFO_NEEDDISPATCH = 1

# IShellWindows 继承自 IDispatch，所以 vtable 前 7 格是 IUnknown(3) + IDispatch(4)
IDX_SW_COUNT = 7
IDX_SW_ITEM = 8
IDX_SW_FINDWINDOW = 15

DISPATCH_PROPERTYGET = 2
VT_I4 = 3
VT_BSTR = 8

_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value

KILL_WAIT = 6.0     # 等 explorer.exe 彻底退出
SHELL_WAIT = 15.0   # 等外壳窗口重新出现
POLL = 0.01         # 探测间隔
WINDOW_DELAY = 0.8  # 外壳就绪后再开窗口的缓冲

# 当前查询使用的排序权重（handle_query 开头按配置刷新）
SCORE = 100000


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


# --------------------------------------------------------------------------- #
# COM 底座：只用 IDispatch + 两个 IShellWindows 方法（读资源管理器当前目录）
# --------------------------------------------------------------------------- #
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text):
        text = text.strip().strip("{}")
        p = text.split("-")
        self.Data1 = int(p[0], 16)
        self.Data2 = int(p[1], 16)
        self.Data3 = int(p[2], 16)
        tail = bytes.fromhex(p[3] + p[4])
        for i, b in enumerate(tail):
            self.Data4[i] = b


# IDispatch::GetIDsOfNames / Invoke 的 riid 必须是 IID_NULL 的指针（传 NULL 会 E_INVALIDARG）
IID_NULL = GUID("{00000000-0000-0000-0000-000000000000}")


class _VARIANT_VALUE(ctypes.Union):
    _fields_ = [
        ("llVal", ctypes.c_longlong),
        ("pwszVal", ctypes.c_wchar_p),
        ("pdispVal", ctypes.c_void_p),
        ("lVal", ctypes.c_long),
        ("pad", ctypes.c_byte * 16),
    ]


class VARIANT(ctypes.Structure):
    # 真正的 VARIANT 在 x64 上是 24 字节（含 DECIMAL），这里按 24 字节对齐
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("r1", ctypes.c_ushort),
        ("r2", ctypes.c_ushort),
        ("r3", ctypes.c_ushort),
        ("value", _VARIANT_VALUE),
    ]


class DISPPARAMS(ctypes.Structure):
    _fields_ = [
        ("rgvarg", ctypes.c_void_p),
        ("rgdispidNamedArgs", ctypes.c_void_p),
        ("cArgs", ctypes.c_uint),
        ("cNamedArgs", ctypes.c_uint),
    ]


def _vtable_method(ptr, index, restype, *argtypes):
    """从 COM 对象的 vtable 里取出第 index 个方法（index 含 IUnknown 的 3 个）。"""
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return proto(table[index])


def _release(ptr):
    try:
        _vtable_method(ptr, 2, ctypes.c_long)(ptr)
    except Exception:
        pass


def _disp_prop(disp, name):
    """按名字读 IDispatch 上的一个属性，返回 (vt, 值)。"""
    dispid = ctypes.c_long(0)
    names = (ctypes.c_wchar_p * 1)(name)
    get_ids = _vtable_method(
        disp, 5, ctypes.c_long,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p), ctypes.c_uint,
        ctypes.c_ulong, ctypes.POINTER(ctypes.c_long),
    )
    if get_ids(disp, ctypes.byref(IID_NULL), names, 1, 0, ctypes.byref(dispid)) != 0:
        return None, None

    params = DISPPARAMS(None, None, 0, 0)
    result = VARIANT()
    invoke = _vtable_method(
        disp, 6, ctypes.c_long,
        ctypes.c_long, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ushort,
        ctypes.POINTER(DISPPARAMS), ctypes.POINTER(VARIANT), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint),
    )
    hr = invoke(disp, dispid, ctypes.byref(IID_NULL), 0, DISPATCH_PROPERTYGET,
                ctypes.byref(params), ctypes.byref(result), None, None)
    if hr != 0:
        return None, None

    oleaut32 = ctypes.oledll.oleaut32
    if result.vt == VT_BSTR:
        addr = result.value.pdispVal
        text = ctypes.wstring_at(addr) if addr else ""
        try:
            oleaut32.SysFreeString(ctypes.c_void_p(addr))
        except Exception:
            pass
        return VT_BSTR, text
    if result.vt == VT_I4:
        return VT_I4, int(result.value.lVal)
    if result.vt == 0x13:  # VT_UI4
        return VT_I4, int(result.value.llVal & 0xFFFFFFFF)
    if result.vt == 0x14:  # VT_I8
        return VT_I4, int(result.value.llVal)
    return result.vt, None


def _shell_windows():
    """枚举 IShellWindows：返回 [(hwnd, LocationURL), ...]；失败返回 []。"""
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
    except OSError:
        pass  # 已初始化（RPC_E_CHANGED_MODE 也能继续用）

    psw = ctypes.c_void_p()
    clsid, iid = GUID(CLSID_SHELLWINDOWS), GUID(IID_ISHELLWINDOWS)
    ole32.CoCreateInstance(ctypes.byref(clsid), None, 4, ctypes.byref(iid), ctypes.byref(psw))
    if not psw:
        return []

    out = []
    try:
        count = ctypes.c_long(0)
        _vtable_method(psw, IDX_SW_COUNT, ctypes.c_long,
                       ctypes.POINTER(ctypes.c_long))(psw, ctypes.byref(count))
        item = _vtable_method(psw, IDX_SW_ITEM, ctypes.c_long, VARIANT,
                              ctypes.POINTER(ctypes.c_void_p))
        for i in range(max(0, count.value)):
            index = VARIANT()
            index.vt = VT_I4
            index.value.llVal = i
            disp = ctypes.c_void_p()
            if item(psw, index, ctypes.byref(disp)) != 0 or not disp:
                continue
            try:
                _, hwnd = _disp_prop(disp, "HWND")
                _, url = _disp_prop(disp, "LocationURL")
                out.append((int(hwnd or 0), url or ""))
            finally:
                _release(disp)
    finally:
        _release(psw)
    return out


def _find_browser_window():
    """FindWindowSW：直接拿「当前活动的那扇资源管理器窗口」。本机恒 S_FALSE，留作快路径。"""
    ole32 = ctypes.oledll.ole32
    try:
        ole32.CoInitializeEx(None, 2)
    except OSError:
        pass

    psw = ctypes.c_void_p()
    clsid, iid = GUID(CLSID_SHELLWINDOWS), GUID(IID_ISHELLWINDOWS)
    ole32.CoCreateInstance(ctypes.byref(clsid), None, 4, ctypes.byref(iid), ctypes.byref(psw))
    if not psw:
        return None

    disp = ctypes.c_void_p()
    try:
        empty = VARIANT()
        hwnd = ctypes.c_long(0)
        find = _vtable_method(
            psw, IDX_SW_FINDWINDOW, ctypes.c_long,
            ctypes.POINTER(VARIANT), ctypes.POINTER(VARIANT), ctypes.c_int,
            ctypes.POINTER(ctypes.c_long), ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
        )
        hr = find(psw, ctypes.byref(empty), ctypes.byref(empty), SWC_BROWSER,
                  ctypes.byref(hwnd), SWFO_NEEDDISPATCH, ctypes.byref(disp))
        if hr != 0 or not disp:
            return None
        _, url = _disp_prop(disp, "LocationURL")
        return (int(hwnd.value), url or "") if url else None
    finally:
        if disp:
            _release(disp)
        _release(psw)


def _zorder_cabinet_windows():
    """按 z 序（最前面优先）返回可见的 CabinetWClass 窗口句柄。"""
    user32 = ctypes.windll.user32
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == "CabinetWClass" and user32.IsWindowVisible(hwnd):
            found.append(int(hwnd))
        return True

    user32.EnumWindows(proto(callback), 0)
    return found


def url_to_path(url):
    """file:///E:/a%20b -> E:\\a b；不是本地文件夹（搜索、控制面板、此电脑 GUID）返回 None。"""
    if not url:
        return None
    import urllib.parse
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return None
    if parts.scheme.lower() != "file":
        return None
    if "::" in url:  # ::{20D04FE0-...} 之类的虚拟文件夹
        return None
    path = urllib.parse.unquote(parts.path or "")
    if not path:
        return None
    path = path.replace("/", "\\")
    if parts.netloc:
        return "\\\\%s%s" % (parts.netloc, path)
    if len(path) > 2 and path[0] == "\\" and path[2] == ":":
        path = path[1:]          # \E:\foo -> E:\foo
    return path


def active_explorer_path():
    """当前资源管理器目录；取不到返回 None。"""
    try:
        hit = _find_browser_window()
        if hit:
            path = url_to_path(hit[1])
            if path and os.path.isdir(path):
                return path
    except Exception as exc:
        log("FindWindowSW 失败：%r" % (exc,))

    try:
        windows = _shell_windows()
    except Exception as exc:
        log("IShellWindows 枚举失败：%r" % (exc,))
        return None

    # 先按 z 序挑最前面的资源管理器窗口
    for hwnd in _zorder_cabinet_windows():
        for h, url in windows:
            if h == hwnd:
                path = url_to_path(url)
                if path and os.path.isdir(path):
                    return path

    # 再退一步：任何一个真实文件夹（例如只剩桌面）
    for _, url in windows:
        path = url_to_path(url)
        if path and os.path.isdir(path):
            return path
    return None


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


# --------------------------------------------------------------------------- #
# 终端 / 进程
# --------------------------------------------------------------------------- #
def split_command(text):
    """把 `kimi --version "a b"` 拆成 ['kimi', '--version', 'a b']。"""
    out, cur, quote = [], "", None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur += ch
        elif ch in "\"'":
            quote = ch
        elif ch.isspace():
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _shell_argv(shell, command, keep_open):
    """在终端里用哪个 shell 承载命令。"""
    shell = (shell or "cmd").strip().lower()
    if shell in ("none", "direct", "raw", "bare"):
        return split_command(command) if command else []
    if shell.startswith("pwsh"):
        exe = "pwsh.exe"
    elif shell.startswith("powershell"):
        exe = "powershell.exe"
    else:
        if not command:
            return ["cmd.exe", "/k"] if keep_open else ["cmd.exe"]
        return ["cmd.exe", "/k", command] if keep_open else ["cmd.exe", "/c", command]
    if not command:
        return [exe, "-NoExit"] if keep_open else [exe]
    return [exe, "-NoExit", "-Command", command] if keep_open else [exe, "-Command", command]


def build_launch(cfg, command, directory):
    """返回要执行的 argv（不含 cwd）。"""
    term = str(cfg.get("terminal") or "wt").strip()
    shell = str(cfg.get("shell") or "cmd")
    keep_open = bool(cfg.get("keep_open", True))
    low = term.lower()

    if low in ("", "wt", "wt.exe", "windowsterminal", "windowsterminal.exe"):
        argv = ["wt.exe", "-d", directory]
        if command:
            argv += _shell_argv(shell, command, keep_open)
        elif str(shell).lower() not in ("none", "direct", "raw", "bare"):
            argv += _shell_argv(shell, "", keep_open)
        return argv

    if low in ("cmd", "cmd.exe"):
        if command:
            return ["cmd.exe", "/k" if keep_open else "/c", command]
        return ["cmd.exe", "/k"]

    if low in ("pwsh", "pwsh.exe", "powershell", "powershell.exe"):
        exe = "pwsh.exe" if low.startswith("pwsh") else "powershell.exe"
        if command:
            return [exe, "-NoExit", "-Command", command] if keep_open else [exe, "-Command", command]
        return [exe, "-NoExit"] if keep_open else [exe]

    # 其它终端：当成可执行文件名，命令拆成参数直接跟在后面
    return [term] + (split_command(command) if command else [])


def wmi_spawn(argv, cwd):
    """兜底：交给 WmiPrvSE 创建进程（天然不在 Flow 的 Job 里）。"""
    import subprocess
    cmdline = subprocess.list2cmdline(argv).replace("'", "''")
    args = "@{ CommandLine = '%s'" % cmdline
    if cwd:
        args += "; CurrentDirectory = '%s'" % str(cwd).replace("'", "''")
    args += " }"
    script = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments %s; "
              "'RETURNVALUE=' + $r.ReturnValue" % args)
    for exe in ("powershell.exe", "pwsh.exe"):
        try:
            done = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=40,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            continue
        except Exception as exc:
            log("wmi_spawn %s 异常：%r" % (exe, exc))
            continue
        text = (done.stdout or "") + (done.stderr or "")
        if "RETURNVALUE=0" in text:
            return True, "wmi"
        log("wmi_spawn %s 返回：%r" % (exe, text.strip()[:200]))
    return False, "wmi 失败"


def spawn(argv, cwd=None):
    """拉起终端/程序：优先脱离 Job（CREATE_BREAKAWAY_FROM_JOB），
    被 Flow 的 Job 拒绝时退到分离进程，最后交给 WMI。"""
    import subprocess
    attempts = [
        CREATE_BREAKAWAY_FROM_JOB | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        0,
    ]
    for flags in attempts:
        try:
            proc = subprocess.Popen(
                argv, cwd=cwd, creationflags=flags, close_fds=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            log("spawn ok flags=0x%X pid=%s argv=%r cwd=%r" % (flags, proc.pid, argv, cwd))
            return True, "pid=%s flags=0x%X" % (proc.pid, flags)
        except OSError as exc:
            log("spawn 失败 flags=0x%X：%r" % (flags, exc))
    return wmi_spawn(argv, cwd)


def command_available(command):
    first = (split_command(command) or [""])[0]
    if not first:
        return False
    if os.path.isabs(first) or "\\" in first or "/" in first:
        return os.path.exists(os.path.expandvars(first))
    from shutil import which
    return which(first) is not None


# --------------------------------------------------------------------------- #
# 重启资源管理器
# --------------------------------------------------------------------------- #
class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_char * 260),
    ]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.GetShellWindow.restype = ctypes.c_void_p
    _kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    _kernel32.Process32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32)]
    _kernel32.Process32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32)]
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
else:  # pragma: no cover
    _user32 = _kernel32 = None


def _shell_up():
    """外壳窗口存在即认为资源管理器（任务栏/桌面）在跑。"""
    if _user32 is None:
        return False
    try:
        return bool(_user32.GetShellWindow())
    except Exception:
        return False


def _explorer_pids():
    """当前 explorer.exe 的 PID 列表（进程快照，毫秒级）。"""
    if _kernel32 is None:
        return []
    snapshot = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        return []
    pids = []
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not _kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return []
        while True:
            if entry.szExeFile.decode("mbcs", "replace").strip().lower() == "explorer.exe":
                pids.append(int(entry.th32ProcessID))
            if not _kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    except Exception:
        return pids
    finally:
        _kernel32.CloseHandle(snapshot)
    return pids


def _run(args, timeout=30):
    import subprocess
    try:
        proc = subprocess.run(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
        return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:
        log("run %s failed: %r" % (args, exc))
        return -1, ""


def _auto_restart_shell():
    """Winlogon\\AutoRestartShell —— 仅用于日志，不参与决策（本机实测不生效）。"""
    if winreg is None:
        return True
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AutoRestartShell")
        return int(value) != 0
    except Exception:
        return True


def _wait_until(predicate, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(POLL)
    return bool(predicate())


def _restart_explorer():
    """结束并重新启动资源管理器，返回新的 PID 列表。"""
    before = _explorer_pids()
    was_up = _shell_up()

    if before:
        # 不用 /T：explorer 的子进程可能正是她自己的程序，不能连坐。
        _run(["taskkill", "/F", "/IM", "explorer.exe"])
    else:
        log("explorer.exe 不在运行，直接拉起外壳")

    # 等外壳彻底下去（进程没了 + 外壳窗口没了），避免新旧外壳抢同一个角色
    _wait_until(lambda: not _explorer_pids() and not _shell_up(), KILL_WAIT)

    started = _spawn_explorer()
    _wait_until(_shell_up, SHELL_WAIT)

    after = _explorer_pids()
    log("restart: before=%s was_up=%s -> after=%s shell_up=%s auto=%s spawned=%s"
        % (before, was_up, after, _shell_up(), _auto_restart_shell(), started))
    return after


def _spawn_explorer():
    ok, info = spawn(["explorer.exe"])
    return ok


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
# --------------------------------------------------------------------------- #
def _sessions_root(cfg):
    km = cfg.get("kimi") or {}
    root = str(km.get("sessions_dir") or "").strip()
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".kimi-code", "sessions")
    return os.path.expandvars(os.path.expanduser(root))


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def _load_cache():
    cache = _read_json(CACHE_PATH)
    return cache if isinstance(cache, dict) else {}


def _save_cache(cache):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
    except Exception as exc:
        log("写 sessions_cache.json 失败：%r" % (exc,))


def load_sessions(cfg):
    """列出会话（按 updatedAt 倒序）。

    稳态只做「scandir + stat + 读缓存」，不重新解析没变过的 state.json
    （本机全量解析 63 个要 341 ms，缓存后 ≈ 10 ms）。
    """
    root = _sessions_root(cfg)
    km = cfg.get("kimi") or {}
    include_archived = bool(km.get("include_archived", False))

    entries = []
    try:
        workspaces = list(os.scandir(root))
    except OSError as exc:
        log("会话目录不可读：%r（%r）" % (root, exc))
        return []

    for ws in workspaces:
        if not ws.is_dir():
            continue
        try:
            session_dirs = list(os.scandir(ws.path))
        except OSError:
            continue
        for sd in session_dirs:
            if not sd.is_dir():
                continue
            state_path = os.path.join(sd.path, "state.json")
            try:
                stat = os.stat(state_path)
            except OSError:
                continue
            entries.append((state_path, sd.path, stat.st_mtime, stat.st_size))

    cache = _load_cache()
    files = cache.get("files") if isinstance(cache.get("files"), dict) else {}
    changed = False
    out = []

    for state_path, session_dir, mtime, size in entries:
        record = files.get(state_path)
        if (isinstance(record, dict) and record.get("size") == size
                and abs(float(record.get("mtime") or 0) - mtime) < 1e-6):
            data = record.get("data")
        else:
            data = _read_json(state_path)
            files[state_path] = {"mtime": mtime, "size": size, "data": data}
            changed = True
        if not isinstance(data, dict):
            continue
        if data.get("archived") and not include_archived:
            continue
        if not (data.get("title") or data.get("lastPrompt") or data.get("cwd")):
            continue
        item = dict(data)
        item["_path"] = session_dir
        out.append(item)

    live = {e[0] for e in entries}
    for gone in [key for key in files if key not in live]:
        files.pop(gone, None)
        changed = True

    if changed:
        _save_cache({"version": 1, "files": files})

    out.sort(key=lambda r: r.get("updatedAt") or 0, reverse=True)
    return out


def _one_line(text, limit=90):
    text = " ".join(str(text or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _relative_time(ms):
    try:
        stamp = float(ms) / 1000.0
    except (TypeError, ValueError):
        return "时间未知"
    if stamp <= 0:
        return "时间未知"
    delta = time.time() - stamp
    if delta < 0:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return "%d 分钟前" % (delta // 60)
    if delta < 86400:
        return "%d 小时前" % (delta // 3600)
    if delta < 86400 * 7:
        return "%d 天前" % (delta // 86400)
    return time.strftime("%Y-%m-%d", time.localtime(stamp))


def _session_blob(session):
    return " ".join(str(session.get(key) or "") for key in
                    ("title", "lastPrompt", "cwd", "id", "titleKind")).lower()


def _search_sessions_meta(sessions, text, limit):
    needle = text.lower()
    return [s for s in sessions if needle in _session_blob(s)]


def _search_sessions_deep(sessions, text, limit, km, already):
    """有上限的正文深搜：只看最近 N 个会话的 wire.jsonl 末尾若干字节。"""
    needle = text.lower()
    if len(needle) < int(km.get("deep_min_query") or 2):
        return []
    max_files = int(km.get("deep_files") or 10)
    max_bytes = int(km.get("deep_bytes") or 262144)
    seen = {s.get("_path") for s in already}
    hits = []
    for session in sessions:
        if len(hits) + len(already) >= limit:
            break
        if session.get("_path") in seen:
            continue
        wire = os.path.join(str(session.get("_path") or ""), "agents", "main", "wire.jsonl")
        try:
            size = os.path.getsize(wire)
            with open(wire, "rb") as fh:
                if size > max_bytes:
                    fh.seek(size - max_bytes)
                blob = fh.read(max_bytes)
        except OSError:
            continue
        if needle in blob.decode("utf-8", "replace").lower():
            hits.append(session)
        max_files -= 1
        if max_files <= 0:
            break
    return hits


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
        icon=ICON_KIMI,
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
# --------------------------------------------------------------------------- #
def _item(title, subtitle, action=None, icon=ICON, score=None):
    item = {
        "Title": title,
        "SubTitle": subtitle,
        "IcoPath": icon,
        "Score": SCORE if score is None else score,
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


def handle_query(parameters):
    global SCORE
    raw = ""
    if parameters and isinstance(parameters[0], str):
        raw = parameters[0]

    mode, text = route(raw)
    if mode is None:
        return []           # 不是我们的查询：立刻空手而归，不读配置、不碰 COM

    cfg = load_config()
    try:
        SCORE = int(cfg.get("score") or 100000)
    except (TypeError, ValueError):
        SCORE = 100000

    if mode == "restart":
        return restart_items(text, cfg)
    if mode == "term":
        return term_items(text, cfg)
    if mode == "kimi":
        return kimi_items(text, cfg)
    return hub_items(cfg)


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# 自测
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# 协议入口
# --------------------------------------------------------------------------- #
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
