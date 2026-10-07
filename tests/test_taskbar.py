"""工作列閃爍的純邏輯測試：注入假的 user32，不建立真的視窗。"""

from __future__ import annotations

import ctypes

import naraka.ui.taskbar as taskbar
from naraka.ui.taskbar import flash_taskbar

WINFO_ID = 0x1234
PARENT_HWND = 0x5678


class FakeRoot:
    """只回報固定的 HWND，不碰真的視窗。"""

    def __init__(self, winfo_id: int = WINFO_ID, error: bool = False) -> None:
        self._winfo_id = winfo_id
        self._error = error
        self.idle_calls = 0

    def update_idletasks(self) -> None:
        self.idle_calls += 1

    def winfo_id(self) -> int:
        if self._error:
            raise RuntimeError("no display")
        return self._winfo_id


class FakeUser32:
    """假的 user32：記錄 FlashWindowEx 的參數。"""

    def __init__(self, foreground: int = 0, parent: int = PARENT_HWND) -> None:
        self.foreground = foreground
        self.parent = parent
        self.flashed = []

    def GetForegroundWindow(self) -> int:
        return self.foreground

    def GetParent(self, hwnd: int) -> int:
        return self.parent

    def FlashWindowEx(self, info) -> int:
        self.flashed.append(info.contents)
        return 1


def test_flash_uses_tray_and_timer_flags():
    api = FakeUser32()

    assert flash_taskbar(FakeRoot(), user32=api) is True

    assert len(api.flashed) == 1
    info = api.flashed[0]
    assert info.dwFlags == taskbar.FLASHW_TRAY | taskbar.FLASHW_TIMERNOFG
    assert info.hwnd == PARENT_HWND  # 用 GetParent 取到的外層視窗代號
    assert info.uCount == taskbar.FLASH_COUNT
    assert info.cbSize == ctypes.sizeof(taskbar.FLASHWINFO)


def test_flash_updates_taskbar_before_querying_hwnd():
    root = FakeRoot()

    flash_taskbar(root, user32=FakeUser32())

    assert root.idle_calls == 1  # 視窗代號還沒算好就問會拿到 0


def test_no_flash_when_window_is_foreground():
    """視窗本來就在前景就不閃，避免打擾。"""
    api = FakeUser32(foreground=PARENT_HWND)

    assert flash_taskbar(FakeRoot(), user32=api) is False
    assert api.flashed == []


def test_no_flash_without_user32(monkeypatch):
    """非 Windows 或載入失敗 → 靜默略過。"""
    monkeypatch.setattr(taskbar, "_user32", None)

    assert flash_taskbar(FakeRoot()) is False


def test_falls_back_to_winfo_id_when_get_parent_is_zero():
    api = FakeUser32(parent=0)

    assert flash_taskbar(FakeRoot(), user32=api) is True
    assert api.flashed[0].hwnd == WINFO_ID


def test_window_error_returns_false():
    api = FakeUser32()

    assert flash_taskbar(FakeRoot(error=True), user32=api) is False
    assert api.flashed == []
