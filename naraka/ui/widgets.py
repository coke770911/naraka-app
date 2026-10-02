"""可重用 UI 元件：日誌方塊（含 tag 高亮）、監控物品列。"""

from __future__ import annotations

import webbrowser
from datetime import datetime
from typing import Callable

import customtkinter as ctk

from ..models import ItemEntry, market_url

#: 日誌等級 → 顏色
LEVEL_STYLES = {
    "info": "#98a2b3",
    "hit": {"foreground": "#7ee787", "background": "#12351f"},
    "sent": "#79c0ff",
    "warn": "#e3b341",
    "error": "#ff7b72",
}

LOG_FONT = ("Consolas", 12)


class LogBox(ctk.CTkFrame):
    """唯讀日誌區，可依等級高亮。"""

    def __init__(self, master, max_lines: int = 4000, **kwargs):
        super().__init__(master, fg_color="#161b22", **kwargs)
        self._max_lines = max_lines

        self.box = ctk.CTkTextbox(
            self,
            wrap="word",
            font=LOG_FONT,
            activate_scrollbars=True,
            fg_color="transparent",
        )
        self.box.pack(fill="both", expand=True, padx=6, pady=6)

        for level, style in LEVEL_STYLES.items():
            if isinstance(style, dict):
                self.box.tag_config(level, **style)
            else:
                self.box.tag_config(level, foreground=style)
        self.box.configure(state="disabled")

    # ── 寫入 ────────────────────────────────────────────────────
    def append(self, text: str, level: str = "info", stamp: bool = True) -> None:
        prefix = datetime.now().strftime("%H:%M:%S") + "  " if stamp else ""
        line = f"{prefix}{text}\n"
        self.box.configure(state="normal")
        self.box.insert("end", line, level if level in LEVEL_STYLES else "info")
        self._trim()
        self.box.see("end")
        self.box.configure(state="disabled")

    def clear(self) -> None:
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.configure(state="disabled")

    def _trim(self) -> None:
        try:
            total = int(self.box.index("end-1c").split(".")[0])
        except Exception:
            return
        if total > self._max_lines:
            drop = total - self._max_lines
            self.box.delete(f"1.0", f"{drop + 1}.0")


class ItemRow(ctk.CTkFrame):
    """監控清單中的一列。"""

    def __init__(
        self,
        master,
        item: ItemEntry,
        on_toggle: Callable[[str, bool], None],
        on_remove: Callable[[str], None],
        on_open: Callable[[], None],
    ):
        super().__init__(master, fg_color="#1c2128", corner_radius=8)
        self.item_id = item.id
        self._on_open = on_open
        self._url = market_url(item.hash_name)

        self.grid_columnconfigure(0, weight=1)

        title = ctk.CTkLabel(
            self,
            text=item.display,
            font=("Microsoft JhengHei", 14, "bold"),
            anchor="w",
            justify="left",
        )
        title.grid(row=0, column=0, sticky="w", padx=(12, 6), pady=(10, 0))
        title.bind("<Button-1>", lambda _e: self._on_open())

        self.meta = ctk.CTkLabel(
            self,
            text="尚未掃描",
            font=("Microsoft JhengHei", 11),
            text_color=LEVEL_STYLES["info"],
            anchor="w",
            justify="left",
        )
        self.meta.grid(row=1, column=0, columnspan=3, sticky="w", padx=(12, 6), pady=(0, 10))

        hash_label = ctk.CTkLabel(
            self,
            text=item.hash_name,
            font=("Consolas", 10),
            text_color="#6e7681",
            anchor="w",
        )
        hash_label.grid(row=2, column=0, sticky="w", padx=(12, 6), pady=(0, 10))
        hash_label.bind("<Button-1>", lambda _e: self._on_open())

        self.switch = ctk.CTkSwitch(
            self,
            text="啟用",
            font=("Microsoft JhengHei", 12),
            command=lambda: on_toggle(self.item_id, bool(self.switch.get())),
        )
        self.switch.grid(row=0, column=1, padx=6, pady=(10, 0))
        if item.enabled:
            self.switch.select()
        else:
            self.switch.deselect()

        remove = ctk.CTkButton(
            self,
            text="移除",
            width=64,
            height=28,
            font=("Microsoft JhengHei", 12),
            fg_color="#6e2c2c",
            hover_color="#8b3838",
            command=lambda: on_remove(self.item_id),
        )
        remove.grid(row=0, column=2, padx=(0, 12), pady=(10, 0))

    def set_meta(self, text: str, color: str = LEVEL_STYLES["info"]) -> None:
        self.meta.configure(text=text, text_color=color)

    def open_market(self) -> None:
        webbrowser.open(self._url)
