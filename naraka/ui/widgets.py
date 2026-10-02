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
    """監控清單中的一列。

    每列有自己的條件摘要，並提供「⚙ 條件」按鈕把該物品設為條件頁的編輯目標。
    """

    NORMAL_BG = "#1c2128"
    ACTIVE_BG = "#22303f"
    ACTIVE_BORDER = "#3b82f6"

    def __init__(
        self,
        master,
        item: ItemEntry,
        on_toggle: Callable[[str, bool], None],
        on_remove: Callable[[str], None],
        on_open: Callable[[], None],
        on_select: Callable[[str], None],
    ):
        super().__init__(master, fg_color=self.NORMAL_BG, corner_radius=8, border_width=0)
        self.item_id = item.id
        self._on_open = on_open
        self._on_select = on_select
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

        self.conditions = ctk.CTkLabel(
            self,
            text=item.conditions_summary(),
            font=("Consolas", 10),
            text_color="#7d8590",
            anchor="w",
            justify="left",
        )
        self.conditions.grid(row=1, column=0, sticky="w", padx=(12, 6), pady=(2, 0))

        self.meta = ctk.CTkLabel(
            self,
            text="尚未掃描",
            font=("Microsoft JhengHei", 11),
            text_color=LEVEL_STYLES["info"],
            anchor="w",
            justify="left",
        )
        self.meta.grid(row=2, column=0, sticky="w", padx=(12, 6), pady=(0, 10))

        hash_label = ctk.CTkLabel(
            self,
            text=item.hash_name,
            font=("Consolas", 10),
            text_color="#6e7681",
            anchor="w",
        )
        hash_label.grid(row=3, column=0, sticky="w", padx=(12, 6), pady=(0, 10))
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

        self.btn_conditions = ctk.CTkButton(
            self,
            text="⚙ 條件",
            width=76,
            height=28,
            font=("Microsoft JhengHei", 12),
            fg_color="#2d3b4a",
            hover_color="#3a4b5e",
            command=lambda: self._on_select(self.item_id),
        )
        self.btn_conditions.grid(row=0, column=2, padx=6, pady=(10, 0))

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
        remove.grid(row=0, column=3, padx=(0, 12), pady=(10, 0))

    def set_meta(self, text: str, color: str = LEVEL_STYLES["info"]) -> None:
        self.meta.configure(text=text, text_color=color)

    def set_conditions(self, text: str) -> None:
        self.conditions.configure(text=text)

    def set_selected(self, selected: bool) -> None:
        """標示這一列是否為條件頁目前編輯的目標。"""
        self.configure(
            fg_color=self.ACTIVE_BG if selected else self.NORMAL_BG,
            border_width=2 if selected else 0,
            border_color=self.ACTIVE_BORDER,
        )
        self.btn_conditions.configure(
            text="⚙ 編輯中" if selected else "⚙ 條件",
            fg_color="#1f6feb" if selected else "#2d3b4a",
        )

    def open_market(self) -> None:
        webbrowser.open(self._url)
