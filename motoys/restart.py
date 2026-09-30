"""重启资源管理器：枚举 explorer.exe、等外壳退出、自己拉起（不依赖 AutoRestartShell）。"""

import ctypes
import subprocess
import time
try:
    import winreg
except Exception:  # pragma: no cover
    winreg = None

from .base import IS_WINDOWS, log
from .launch import CREATE_NO_WINDOW, spawn


_TH32CS_SNAPPROCESS = 0x00000002
_INVALID_HANDLE = ctypes.c_void_p(-1).value


KILL_WAIT = 6.0     # 等 explorer.exe 彻底退出
SHELL_WAIT = 15.0   # 等外壳窗口重新出现
POLL = 0.01         # 探测间隔
WINDOW_DELAY = 0.8  # 外壳就绪后再开窗口的缓冲


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
