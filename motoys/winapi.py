"""IShellWindows COM 底座：读出「当前活动的资源管理器窗口」所在目录。

只用 IDispatch + FindWindowSW/Item/Count，不依赖 pywin32。"""

import ctypes
import os
try:
    import ctypes.wintypes as wintypes
except Exception:  # pragma: no cover
    wintypes = None

from .base import log


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
