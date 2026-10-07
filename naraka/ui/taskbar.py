"""工作列注意力：命中時讓工作列按鈕閃爍。

使用 `user32!FlashWindowEx`（`FLASHW_TRAY | FLASHW_TIMERNOFG`），
閃到視窗回到前景為止；視窗本來就在前景時不閃。
非 Windows 或 API 不可用時靜默略過，不影響通知流程。
"""

from __future__ import annotations

import ctypes
import sys
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # 運行期不依賴 tkinter，測試可於無頭環境 import
    import tkinter

#: 工作列按鈕（含圖案）閃爍
FLASHW_TRAY = 0x02
#: 持續閃爍直到視窗取得焦點
FLASHW_TIMERNOFG = 0x0C
#: 單次命中的閃爍次數（未取回焦點前會不斷重來）
FLASH_COUNT = 5


class FLASHWINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_uint32),
        ("uCount", ctypes.c_uint32),
        ("dwTimeout", ctypes.c_uint32),
    )


def _load_user32() -> Optional[Any]:
    if sys.platform != "win32":
        return None
    try:
        api = ctypes.windll.user32
        # HWND 是指標，未設 restype 會被截斷成 32 位元
        api.GetParent.argtypes = [ctypes.c_void_p]
        api.GetParent.restype = ctypes.c_void_p
        api.GetForegroundWindow.restype = ctypes.c_void_p
        api.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
        api.FlashWindowEx.restype = ctypes.c_int
        return api
    except Exception:  # pragma: no cover - 桌面以外的環境
        return None


_user32 = _load_user32()


def _hwnd_of(root: "tkinter.Misc", api: Any) -> int:
    """取視窗的工作列代號（HWND）。取不到時回傳 0。"""
    try:
        root.update_idletasks()
        winfo = int(root.winfo_id())
    except Exception:
        return 0
    if not winfo:
        return 0
    try:
        parent = api.GetParent(winfo)
    except Exception:  # pragma: no cover - API 異常
        return winfo
    return int(parent) if parent else winfo


def flash_taskbar(root: "tkinter.Misc", user32: Optional[Any] = None) -> bool:
    """讓工作列按鈕閃爍。

    回傳 ``True`` 表示已啟動閃爍；視窗在前景、平台不支援或失敗時回傳
    ``False``。``user32`` 只供測試注入假實作使用。
    """
    api = user32 if user32 is not None else _user32
    if api is None:
        return False

    hwnd = _hwnd_of(root, api)
    if not hwnd:
        return False

    try:
        if int(api.GetForegroundWindow() or 0) == hwnd:
            return False
    except Exception:  # pragma: no cover - API 異常
        return False

    info = FLASHWINFO(
        ctypes.sizeof(FLASHWINFO),
        hwnd,
        FLASHW_TRAY | FLASHW_TIMERNOFG,
        FLASH_COUNT,
        0,
    )
    try:
        api.FlashWindowEx(ctypes.pointer(info))
    except Exception:  # pragma: no cover - API 異常
        return False
    return True
