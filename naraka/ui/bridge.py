"""背景執行緒 → UI 的橋接層。

背景執行緒 **只** 呼叫 :meth:`UiBridge.post`（thread-safe 的 queue.put）；
主執行緒以 ``after()`` 迴圈定期 drain 佇列並更新元件。
這樣可保證：

* tkinter 只在主執行緒被操作；
* 事件順序與 UI 更新順序一致；
* 關閉程式時不會有殘留的 ``after`` 回呼。
"""

from __future__ import annotations

import queue
import tkinter
import traceback
from typing import Callable, Dict, Optional


class UiBridge:
    def __init__(self, root: tkinter.Misc, interval_ms: int = 80):
        self._root = root
        self._interval_ms = interval_ms
        self._queue: "queue.Queue" = queue.Queue()
        self._handlers: Dict[str, Callable[[dict], None]] = {}
        self._job: Optional[str] = None
        self._closed = False

    # ── 註冊（在主執行緒呼叫）───────────────────────────────────
    def register(self, kind: str, handler: Callable[[dict], None]) -> None:
        self._handlers[kind] = handler

    # ── 送出事件（任何執行緒皆可）──────────────────────────────
    def post(self, kind: str, **payload) -> None:
        if self._closed:
            return
        self._queue.put((kind, payload))

    # ── 生命週期 ────────────────────────────────────────────────
    def start(self) -> None:
        if self._job is None and not self._closed:
            self._job = self._root.after(self._interval_ms, self._drain)

    def stop(self) -> None:
        self._closed = True
        if self._job is not None:
            try:
                self._root.after_cancel(self._job)
            except tkinter.TclError:
                pass
            self._job = None

    # ── 主執行緒 drain ─────────────────────────────────────────
    def _drain(self) -> None:
        self._job = None
        if self._closed:
            return
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            handler = self._handlers.get(kind)
            if handler is None:
                continue
            try:
                handler(payload)
            except Exception:
                traceback.print_exc()
        if not self._closed:
            self._job = self._root.after(self._interval_ms, self._drain)
