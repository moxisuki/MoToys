"""终端形态与进程拉起：把命令包成 `wt.exe -d <目录> pwsh.exe -NoExit -Command …`，
并三档尝试脱离 Flow 的 Job（前两档带 BREAKAWAY/DETACHED，全失败退 WMI）。"""

import os
import subprocess

from .base import log


# 进程创建标志
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


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
