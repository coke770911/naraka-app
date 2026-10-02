"""主視窗：Sidebar + 四個分頁（監控清單 / 物品條件 / 全域設定 / 即時日誌）。

執行緒模型
----------
* 背景 :class:`~naraka.crawler.CrawlerWorker` 只送出事件。
* 主執行緒透過 :class:`~naraka.ui.bridge.UiBridge` 的 ``after()`` 迴圈消费事件並
  更新元件 —— 任何 tkinter 操作都只發生在主執行緒。
"""

from __future__ import annotations

import time
import tkinter.messagebox as messagebox
from typing import Dict, List, Optional

import customtkinter as ctk

from .. import __version__
from ..config_store import ConfigStore
from ..crawler import CrawlerWorker
from ..models import AppConfig, Criteria, ItemEntry, extract_hash_name, normalize_name
from ..notifiers import Notifier
from ..paths import config_path, file_log_path
from .bridge import UiBridge
from .widgets import LEVEL_STYLES, ItemRow, LogBox

FONT = "Microsoft JhengHei"
MONO = "Consolas"
AUTOSAVE_MS = 800

DEFAULT_ITEMS = [
    ("Star - Shadow Scent(Non-CN)", "謫星·夜影浮香(非國服)"),
    ("Star - Novaburst(Non-CN)", "謫星·天流星輝(國服)"),
    ("Star - Fading Polaris(Non-CN)", "謫星·北辰黯影(非國服)"),
    ("Star - Aegis Reckoning(Non-CN)", "謫星·扶桑劫(非國服)"),
]


class NarakaApp(ctk.CTk):
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()

        self.title(f"Naraka 星格監控工具  v{__version__}")
        self.geometry("1120x760")
        self.minsize(980, 660)

        self.store = ConfigStore(config_path())
        self.bridge = UiBridge(self)
        # Notifier 可能在背景執行緒送出日誌 → 一律經由 bridge 回到主執行緒
        self.notifier = Notifier(log=lambda lv, msg: self.bridge.post("log", level=lv, text=msg))
        self._worker: Optional[CrawlerWorker] = None
        self._autosave_job: Optional[str] = None
        self._rows: Dict[str, ItemRow] = {}
        self._selected_id: Optional[str] = None
        self._next_at: Optional[float] = None
        self._slot_min_entries: List[ctk.CTkEntry] = []
        self._slot_max_entries: List[ctk.CTkEntry] = []

        self._build_ui()
        self._load_ui()
        self._register_handlers()
        self.bridge.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(1000, self._tick)

    # ══════════════════════════════════════════════════════════
    # 版面配置
    # ══════════════════════════════════════════════════════════
    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_tabs()

        # 狀態列
        status = ctk.CTkFrame(self, height=34, corner_radius=0, fg_color="#11161d")
        status.grid(row=1, column=1, sticky="ew")
        self.lbl_status = ctk.CTkLabel(
            status, text="● 已停止", text_color=LEVEL_STYLES["info"],
            font=(FONT, 12, "bold"), width=110, anchor="w",
        )
        self.lbl_status.grid(row=0, column=0, padx=(16, 6), pady=6)
        self.lbl_current = ctk.CTkLabel(
            status, text="閒置", font=(FONT, 12), text_color=LEVEL_STYLES["info"],
            width=260, anchor="w",
        )
        self.lbl_current.grid(row=0, column=1, padx=6)
        status.grid_columnconfigure(2, weight=1)
        self.lbl_next = ctk.CTkLabel(
            status, text="下次掃描：—", font=(FONT, 12),
            text_color=LEVEL_STYLES["info"], width=200, anchor="e",
        )
        self.lbl_next.grid(row=0, column=3, padx=(6, 16))

        self.grid_rowconfigure(1, weight=0)

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, width=196, corner_radius=0, fg_color="#11161d")
        side.grid(row=0, column=0, rowspan=2, sticky="nsew")
        side.grid_propagate(False)
        side.grid_rowconfigure(7, weight=1)

        ctk.CTkLabel(
            side, text="謫星市場監控", font=(FONT, 18, "bold"), text_color="#e6edf3"
        ).grid(row=0, column=0, padx=16, pady=(20, 2))
        ctk.CTkLabel(
            side, text="Steam Community Market", font=(MONO, 10), text_color="#6e7681"
        ).grid(row=1, column=0, padx=16, pady=(0, 18), sticky="w")

        self.btn_start = ctk.CTkButton(
            side, text="▶  啟動監控", font=(FONT, 14, "bold"), height=40,
            command=self._start_monitor,
        )
        self.btn_start.grid(row=2, column=0, padx=14, pady=4, sticky="ew")
        self.btn_stop = ctk.CTkButton(
            side, text="■  停止監控", font=(FONT, 14), height=40, state="disabled",
            fg_color="#6e2c2c", hover_color="#8b3838", command=self._stop_monitor,
        )
        self.btn_stop.grid(row=3, column=0, padx=14, pady=4, sticky="ew")
        self.btn_once = ctk.CTkButton(
            side, text="⟳  單次掃描", font=(FONT, 13), height=34,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=self._scan_once,
        )
        self.btn_once.grid(row=4, column=0, padx=14, pady=4, sticky="ew")

        ctk.CTkFrame(side, height=2, fg_color="#21262d").grid(
            row=5, column=0, padx=14, pady=14, sticky="ew"
        )

        self.lbl_hits = ctk.CTkLabel(
            side, text="上次掃描：—", font=(FONT, 12), text_color="#8b949e",
            anchor="w", justify="left",
        )
        self.lbl_hits.grid(row=6, column=0, padx=16, pady=(0, 6), sticky="ew")

        ctk.CTkLabel(
            side, text=f"設定檔\n{self.store.path}", font=(MONO, 9),
            text_color="#484f58", anchor="w", justify="left", wraplength=164,
        ).grid(row=8, column=0, padx=16, pady=16, sticky="sw")

    def _build_tabs(self) -> None:
        self.tabview = ctk.CTkTabview(self, fg_color="transparent")
        self.tabview.grid(row=0, column=1, sticky="nsew", padx=0)
        self.tabview.add("監控清單")
        self.tabview.add("物品條件")
        self.tabview.add("全域設定")
        self.tabview.add("即時日誌")

        self._build_items_tab(self.tabview.tab("監控清單"))
        self._build_settings_tab(self.tabview.tab("物品條件"))
        self._build_global_tab(self.tabview.tab("全域設定"))
        self._build_log_tab(self.tabview.tab("即時日誌"))

    def _show_tab(self, name: str) -> None:
        self.tabview.set(name)

    # ── 分頁 1：監控清單 ────────────────────────────────────────
    def _build_items_tab(self, parent) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(2, weight=1)

        add = ctk.CTkFrame(parent, fg_color="transparent")
        add.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        add.grid_columnconfigure(0, weight=3)

        self.entry_hash = ctk.CTkEntry(
            add, placeholder_text="Star - Shadow Scent(Non-CN)  或直接貼市場網址",
            font=(MONO, 13), height=38,
        )
        self.entry_hash.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry_hash.bind("<Return>", lambda _e: self._add_item())
        self.entry_hash.bind("<KeyRelease>", lambda _e: self._schedule_autosave())

        self.entry_label = ctk.CTkEntry(
            add, placeholder_text="顯示名稱（選填）", font=(FONT, 13), height=38, width=190,
        )
        self.entry_label.grid(row=0, column=1, padx=(0, 8))
        self.entry_label.bind("<Return>", lambda _e: self._add_item())
        self.entry_label.bind("<KeyRelease>", lambda _e: self._schedule_autosave())

        ctk.CTkButton(
            add, text="＋ 新增", font=(FONT, 14), height=38, width=104,
            command=self._add_item,
        ).grid(row=0, column=2, padx=(0, 8))
        ctk.CTkButton(
            add, text="匯入預設", font=(FONT, 13), height=38, width=104,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=self._import_defaults,
        ).grid(row=0, column=3)

        self.scroll = ctk.CTkScrollableFrame(parent, label_text="監控中的物品", fg_color="#161b22")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 14))
        self.lbl_empty = ctk.CTkLabel(
            self.scroll,
            text="尚未加入任何物品。\n請在上方輸入 Steam 市場的商品名稱（或貼上商品網址）後按「＋ 新增」。",
            font=(FONT, 13), text_color="#6e7681", justify="left",
        )
        self.lbl_empty.grid(row=0, column=0, padx=8, pady=24, sticky="w")

    # ── 分頁 2：物品條件 ────────────────────────────────────────
    def _build_settings_tab(self, parent) -> None:
        """編輯「目前選取物品」的條件（價格上限 + 逐格星格門檻）。

        每個物品各有一份條件，切換選取物品時由 :meth:`_refresh_condition_panel`
        帶入。全域設定（抓取頻率、通知、Steam 驗證）在 :meth:`_build_global_tab`。
        """
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        wrapper = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        wrapper.grid(row=0, column=0, sticky="nsew", padx=16, pady=14)
        wrapper.grid_columnconfigure(0, weight=1)
        row = 0

        # ══ 每個物品的條件 ══════════════════════════════════════
        row = self._section(wrapper, row, "物品條件")

        self.cond_bar = ctk.CTkFrame(wrapper, fg_color="#1c2128", corner_radius=8)
        self.cond_bar.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        self.cond_bar.grid_columnconfigure(0, weight=1)
        self.lbl_cond_target = ctk.CTkLabel(
            self.cond_bar, text="尚未選取物品", font=(FONT, 13, "bold"),
            text_color="#6e7681", anchor="w", justify="left",
        )
        self.lbl_cond_target.grid(row=0, column=0, sticky="w", padx=12, pady=10)
        ctk.CTkButton(
            self.cond_bar, text="設為新增物品預設", font=(FONT, 12), width=150, height=30,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=self._save_criteria_as_default,
        ).grid(row=0, column=1, padx=(0, 12), pady=10)
        row += 1

        self.cond_body = ctk.CTkFrame(wrapper, fg_color="transparent")
        self.cond_body.grid(row=row, column=0, columnspan=2, sticky="ew")

        self.entry_price = self._labelled(
            self.cond_body, 0, "價格上限（NT$）",
            ctk.CTkEntry(self.cond_body, width=160, font=(MONO, 13), placeholder_text="0 = 不限"),
        )
        wrapper.grid_columnconfigure(1, weight=1)
        self.entry_price.grid(row=0, column=1, sticky="w", pady=5)
        self.entry_price.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        self.lbl_price_hint = ctk.CTkLabel(
            self.cond_body, text="填 0 代表不限價", font=(FONT, 11),
            text_color="#8b949e", anchor="w",
        )
        self.lbl_price_hint.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # 星格條件
        self.var_slot_count = ctk.StringVar(value="4")
        self.opt_slot_count = ctk.CTkOptionMenu(
            self.cond_body, values=["3", "4"], variable=self.var_slot_count, width=120,
            font=(FONT, 13), command=self._on_slot_count_change,
        )
        self.opt_slot_count.grid(row=2, column=1, sticky="w", pady=5)
        ctk.CTkLabel(
            self.cond_body, text="星格格數", font=(FONT, 13), width=140, anchor="w"
        ).grid(row=2, column=0, sticky="w", pady=5)
        self.lbl_detected = ctk.CTkLabel(
            self.cond_body, text="", font=(FONT, 11), text_color="#8b949e", anchor="w",
        )
        self.lbl_detected.grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 4))

        self.slot_hint = ctk.CTkLabel(
            self.cond_body, text="", font=(FONT, 11), text_color="#8b949e",
            anchor="w", justify="left",
        )
        self.slot_hint.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        self.slot_frame = ctk.CTkFrame(self.cond_body, fg_color="transparent")
        self.slot_frame.grid(row=5, column=0, columnspan=2, sticky="ew")
        self.slot_frame.grid_columnconfigure(1, weight=1)

        self.var_logic = ctk.StringVar(value="OR")
        self.opt_logic = ctk.CTkOptionMenu(
            self.cond_body, values=["AND", "OR"], variable=self.var_logic, width=120,
            font=(FONT, 13), command=lambda _v: self._schedule_autosave(),
        )
        self.opt_logic.grid(row=6, column=1, sticky="w", pady=5)
        ctk.CTkLabel(
            self.cond_body, text="匹配邏輯", font=(FONT, 13), width=140, anchor="w"
        ).grid(row=6, column=0, sticky="w", pady=5)

        self.var_min_match = ctk.StringVar(value="1")
        self.opt_min_match = ctk.CTkOptionMenu(
            self.cond_body, values=["1", "2", "3"], variable=self.var_min_match,
            width=120, font=(FONT, 13), command=lambda _v: self._schedule_autosave(),
        )
        self.opt_min_match.grid(row=7, column=1, sticky="w", pady=5)
        ctk.CTkLabel(
            self.cond_body, text="最少符合格數", font=(FONT, 13), width=140, anchor="w"
        ).grid(row=7, column=0, sticky="w", pady=5)

        ctk.CTkLabel(
            self.cond_body,
            text="AND = 中間格全部都要符合；OR = 中間格符合數達到「最少符合格數」即可。\n"
                 "每格可填「下限」與「上限」（上限填 0 或留空代表不限）；\n"
                 "最後一格是 0/1 二元位，必須精確比對且一定要中（不受 AND/OR 影響）；\n"
                 "最後一格與任何一格留空都代表「不關心」，該格不列入判定。\n"
                 "格數需手動指定；若與實際掛單不符，該物品不會有命中並在日誌警告。",
            font=(FONT, 11), text_color="#8b949e", anchor="w", justify="left",
        ).grid(row=8, column=0, columnspan=2, sticky="ew", pady=(4, 10))
        row += 2

    # ── 分頁 3：全域設定 ────────────────────────────────────────
    def _build_global_tab(self, parent) -> None:
        """設定一次即套用到所有物品的全域選項。

        抓取頻率、通知管道（Telegram / 桌面通知）與 Steam 驗證都不隨物品變動，
        因此與 :meth:`_build_settings_tab` 的逐物品條件分開，避免混淆。
        """
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        wrapper = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        wrapper.grid(row=0, column=0, sticky="nsew", padx=16, pady=14)
        wrapper.grid_columnconfigure(0, weight=1)
        wrapper.grid_columnconfigure(1, weight=1)
        row = 0

        # 抓取頻率
        row = self._section(wrapper, row, "抓取頻率")
        self.entry_interval_min, self.entry_interval_max = self._pair(
            wrapper, row, "掃描間隔（秒）", "180", "300"
        )
        row += 1
        self.entry_delay_min, self.entry_delay_max = self._pair(
            wrapper, row, "請求間隔（秒）", "2", "5"
        )
        row += 1
        self.entry_max_pages = self._labelled(
            wrapper, row, "最大分頁數", ctk.CTkEntry(wrapper, width=120, font=(MONO, 13))
        )
        self.entry_max_pages.grid(row=row, column=1, sticky="w", pady=5)
        self.entry_max_pages.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        row += 1
        self.switch_verbose = ctk.CTkSwitch(
            wrapper, text="輸出每一筆未符合條件的詳細原因", font=(FONT, 12),
            command=lambda: self._schedule_autosave(),
        )
        self.switch_verbose.grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 2

        # Telegram
        row = self._section(wrapper, row, "Telegram 通知")
        self.switch_tg = ctk.CTkSwitch(
            wrapper, text="啟用 Telegram 通知", font=(FONT, 13),
            command=lambda: self._schedule_autosave(),
        )
        self.switch_tg.grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 1
        self.entry_token = self._labelled(
            wrapper, row, "Bot Token",
            ctk.CTkEntry(wrapper, width=280, font=(MONO, 12), show="*"),
        )
        self.entry_token.grid(row=row, column=1, sticky="w", pady=5)
        self.entry_token.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        row += 1
        self.entry_chatid = self._labelled(
            wrapper, row, "Chat ID", ctk.CTkEntry(wrapper, width=200, font=(MONO, 13))
        )
        self.entry_chatid.grid(row=row, column=1, sticky="w", pady=5)
        self.entry_chatid.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        row += 1
        btns = ctk.CTkFrame(wrapper, fg_color="transparent")
        btns.grid(row=row, column=0, columnspan=2, sticky="w", pady=(2, 10))
        ctk.CTkButton(
            btns, text="取得 Chat ID", font=(FONT, 12), width=130, height=30,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=self._fetch_chat_id,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="發送測試訊息", font=(FONT, 12), width=130, height=30,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=self._test_telegram,
        ).pack(side="left")

        # 桌面通知
        row += 1
        row = self._section(wrapper, row, "桌面通知")
        self.switch_desktop = ctk.CTkSwitch(
            wrapper, text="啟用 Windows 桌面通知（plyer Toast）", font=(FONT, 13),
            command=lambda: self._schedule_autosave(),
        )
        self.switch_desktop.grid(row=row, column=0, columnspan=2, sticky="w", pady=5)
        row += 2

        # Steam
        row = self._section(wrapper, row, "Steam 驗證（選填）")
        ctk.CTkLabel(
            wrapper,
            text="未登入通常仍可讀取公開市場；若遇到 HTTP 429 或要求驗證，可貼上 Cookie 提升穩定度。",
            font=(FONT, 11), text_color="#8b949e", anchor="w", justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        row += 1
        self.entry_cookie_login = self._labelled(
            wrapper, row, "steamLoginSecure", ctk.CTkEntry(wrapper, width=320, font=(MONO, 11), show="*")
        )
        self.entry_cookie_login.grid(row=row, column=1, sticky="w", pady=5)
        self.entry_cookie_login.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        row += 1
        self.entry_cookie_session = self._labelled(
            wrapper, row, "sessionid", ctk.CTkEntry(wrapper, width=320, font=(MONO, 11), show="*")
        )
        self.entry_cookie_session.grid(row=row, column=1, sticky="w", pady=5)
        self.entry_cookie_session.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        row += 2

        # 維護
        row = self._section(wrapper, row, "維護")
        maint = ctk.CTkFrame(wrapper, fg_color="transparent")
        maint.grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        ctk.CTkButton(
            maint, text="立即儲存", font=(FONT, 12), width=110, height=32, command=self._save_now
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            maint, text="清除已通知紀錄", font=(FONT, 12), width=140, height=32,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=self._clear_notified,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            maint, text="還原預設設定", font=(FONT, 12), width=130, height=32,
            fg_color="#6e2c2c", hover_color="#8b3838", command=self._reset_config,
        ).pack(side="left")
        row += 1

    def _section(self, parent, row: int, title: str) -> int:
        ctk.CTkLabel(
            parent, text=title, font=(FONT, 15, "bold"), text_color="#e6edf3", anchor="w"
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 6))
        return row + 1

    def _labelled(self, parent, row: int, label: str, widget):
        ctk.CTkLabel(parent, text=label, font=(FONT, 13), width=140, anchor="w").grid(
            row=row, column=0, sticky="w", pady=5
        )
        return widget

    def _pair(self, parent, row: int, label: str, lo: str, hi: str):
        ctk.CTkLabel(parent, text=label, font=(FONT, 13), width=140, anchor="w").grid(
            row=row, column=0, sticky="w", pady=5
        )
        left = ctk.CTkEntry(parent, width=90, font=(MONO, 13), placeholder_text=lo)
        left.grid(row=row, column=1, sticky="w", pady=5)
        left.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        ctk.CTkLabel(parent, text="～", font=(FONT, 13)).grid(row=row, column=2, padx=6)
        right = ctk.CTkEntry(parent, width=90, font=(MONO, 13), placeholder_text=hi)
        right.grid(row=row, column=3, sticky="w", pady=5)
        right.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
        return left, right

    # ── 分頁 4：即時日誌 ────────────────────────────────────────
    def _build_log_tab(self, parent) -> None:
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(parent, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 6))
        for level, text in (
            ("hit", "★ 命中"),
            ("sent", "已通知"),
            ("warn", "警告"),
            ("error", "錯誤"),
            ("info", "一般"),
        ):
            style = LEVEL_STYLES[level]
            color = style["foreground"] if isinstance(style, dict) else style
            ctk.CTkLabel(
                bar, text=text, font=(FONT, 11), text_color=color
            ).pack(side="left", padx=(0, 14))
        ctk.CTkButton(
            bar, text="清除日誌", font=(FONT, 12), width=100, height=28,
            fg_color="#3b4a5a", hover_color="#4a5c6e", command=lambda: self.log_box.clear(),
        ).pack(side="right")

        self.log_box = LogBox(parent)
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 14))

    # ══════════════════════════════════════════════════════════
    # 設定載入 / 儲存
    # ══════════════════════════════════════════════════════════
    def _load_ui(self) -> None:
        cfg = self.store.snapshot()
        crawler = cfg.crawler

        self.entry_interval_min.insert(0, f"{crawler.interval_min_sec:g}")
        self.entry_interval_max.insert(0, f"{crawler.interval_max_sec:g}")
        self.entry_delay_min.insert(0, f"{crawler.delay_min_sec:g}")
        self.entry_delay_max.insert(0, f"{crawler.delay_max_sec:g}")
        self.entry_max_pages.insert(0, str(crawler.max_pages))
        self._set_switch(self.switch_verbose, crawler.verbose)

        self.entry_token.insert(0, cfg.telegram.token)
        self.entry_chatid.insert(0, cfg.telegram.chat_id)
        self._set_switch(self.switch_tg, cfg.telegram.enabled)
        self._set_switch(self.switch_desktop, cfg.desktop_notify)

        self.entry_cookie_login.insert(0, cfg.steam.cookies.get("steamLoginSecure", ""))
        self.entry_cookie_session.insert(0, cfg.steam.cookies.get("sessionid", ""))

        self._rebuild_items(cfg.items)
        if cfg.items:
            self._select_item(cfg.items[0].id)
        else:
            self._refresh_condition_panel()

    @staticmethod
    def _set_switch(switch: ctk.CTkSwitch, value: bool) -> None:
        if value:
            switch.select()
        else:
            switch.deselect()

    def _schedule_autosave(self) -> None:
        if self._autosave_job is not None:
            try:
                self.after_cancel(self._autosave_job)
            except Exception:
                pass
        self._autosave_job = self.after(AUTOSAVE_MS, self._autosave)

    def _autosave(self) -> None:
        self._autosave_job = None
        self._apply_settings()

    def _save_now(self) -> None:
        self._apply_settings()
        self.log_box.append("設定已儲存", "sent")

    def _apply_settings(self) -> None:
        cfg_snapshot = self.store.snapshot()
        crawler = cfg_snapshot.crawler
        target = cfg_snapshot.item_by_id(self._selected_id) if self._selected_id else None

        price = self._read_float(self.entry_price, target.criteria.max_price_ntd if target else 0.0)
        interval_min = self._read_float(self.entry_interval_min, crawler.interval_min_sec)
        interval_max = self._read_float(self.entry_interval_max, crawler.interval_max_sec)
        delay_min = self._read_float(self.entry_delay_min, crawler.delay_min_sec)
        delay_max = self._read_float(self.entry_delay_max, crawler.delay_max_sec)
        max_pages = self._read_int(self.entry_max_pages, crawler.max_pages)

        fallback = target.criteria if target else Criteria.loose()
        mins: List[Optional[int]] = []
        maxs: List[Optional[int]] = []
        for index, entry in enumerate(self._slot_min_entries):
            mins.append(self._read_int(entry, fallback.slot_min_at(index)))
        for index, entry in enumerate(self._slot_max_entries):
            maxs.append(self._read_int(entry, fallback.slot_max_at(index)))
        try:
            min_match = int(self.var_min_match.get())
            logic = self.var_logic.get()
        except ValueError:
            min_match, logic = fallback.effective_min_match, fallback.logic

        try:
            slot_count = int(self.var_slot_count.get())
        except ValueError:
            slot_count = fallback.slot_count

        token = self.entry_token.get().strip()
        chat_id = self.entry_chatid.get().strip()
        cookie_login = self.entry_cookie_login.get().strip()
        cookie_session = self.entry_cookie_session.get().strip()
        selected_id = self._selected_id

        def apply(cfg: AppConfig) -> None:
            if selected_id:
                item = cfg.item_by_id(selected_id)
                if item is not None:
                    item.criteria.max_price_ntd = price
                    item.criteria.slot_count = slot_count
                    if mins:
                        item.criteria.slot_min = mins
                    if maxs:
                        item.criteria.slot_max = maxs
                    item.criteria.logic = logic
                    item.criteria.min_match = min_match
                    item.criteria.normalise()
            cfg.crawler.interval_min_sec = interval_min
            cfg.crawler.interval_max_sec = interval_max
            cfg.crawler.delay_min_sec = delay_min
            cfg.crawler.delay_max_sec = delay_max
            cfg.crawler.max_pages = max_pages
            cfg.crawler.verbose = bool(self.switch_verbose.get())
            cfg.telegram.token = token
            cfg.telegram.chat_id = chat_id
            cfg.telegram.enabled = bool(self.switch_tg.get())
            cfg.desktop_notify = bool(self.switch_desktop.get())
            cfg.steam.cookies = {
                k: v for k, v in (("steamLoginSecure", cookie_login),
                                  ("sessionid", cookie_session)) if v
            }

        try:
            self.store.mutate(apply)
        except OSError as exc:
            self.log_box.append(f"設定寫入失敗：{exc}", "error")
            return
        self._sync_row_conditions()

    def _sync_row_conditions(self) -> None:
        """把各物品目前的條件摘要更新回清單列（條件改動或掃描偵測後呼叫）。"""
        for item in self.store.snapshot().items:
            row = self._rows.get(item.id)
            if row is not None:
                row.set_conditions(item.conditions_summary())

    @staticmethod
    def _read_float(entry: ctk.CTkEntry, fallback: float) -> float:
        text = entry.get().strip().replace(",", "")
        if not text:
            return fallback
        try:
            return float(text)
        except ValueError:
            return fallback

    @staticmethod
    def _read_int(entry: ctk.CTkEntry, fallback: Optional[int]) -> Optional[int]:
        """讀取整數欄位；留空代表「不關心」→ ``None``。

        星格門檻的值域全為非負，所以留空與填 0 意義不同：0 是真實門檻值。
        """
        text = entry.get().strip()
        if not text:
            return None if fallback is None else fallback
        try:
            return int(float(text))
        except ValueError:
            return fallback

    # ── 星格條件列 ──────────────────────────────────────────────
    def _rebuild_slot_rows(self, criteria: Optional[Criteria] = None) -> None:
        for child in self.slot_frame.winfo_children():
            child.destroy()
        self._slot_min_entries = []
        self._slot_max_entries = []

        if criteria is None:
            self.slot_hint.configure(text="")
            return

        total = criteria.slot_count
        hints = []
        for index in range(total):
            is_last = index == total - 1
            row = ctk.CTkFrame(self.slot_frame, fg_color="transparent")
            row.grid_columnconfigure(2, weight=1)
            row.grid(row=index, column=0, sticky="ew")

            hint = criteria.slot_range_hint(index)
            hints.append(f"第{index + 1}格 {hint}")
            ctk.CTkLabel(
                row, text=f"第{index + 1}格", font=(FONT, 13), width=64, anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=3)
            ctk.CTkLabel(
                row, text=hint, font=(FONT, 10), text_color="#6e7681", width=104, anchor="w"
            ).grid(row=0, column=1, sticky="w", padx=(0, 8))

            low = criteria.slot_min_at(index)
            entry = ctk.CTkEntry(
                row, width=96, font=(MONO, 13), placeholder_text="留空 = 不限"
            )
            if low is not None:
                entry.insert(0, str(low))
            entry.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
            entry.bind("<FocusOut>", lambda _e: self._schedule_autosave())
            entry.grid(row=0, column=2, sticky="w", padx=(0, 8))
            self._slot_min_entries.append(entry)

            if is_last:
                ctk.CTkLabel(
                    row, text="（必中，0 或 1；留空 = 不限）", font=(FONT, 10),
                    text_color="#6e7681", anchor="w",
                ).grid(row=0, column=3, sticky="w")
            else:
                ctk.CTkLabel(
                    row, text="上限", font=(FONT, 11), text_color="#8b949e", width=32, anchor="w"
                ).grid(row=0, column=3, sticky="w")
                high = criteria.slot_max_at(index)
                max_entry = ctk.CTkEntry(
                    row, width=96, font=(MONO, 13), placeholder_text="不限"
                )
                if high > 0:
                    max_entry.insert(0, str(high))
                max_entry.bind("<KeyRelease>", lambda _e: self._schedule_autosave())
                max_entry.bind("<FocusOut>", lambda _e: self._schedule_autosave())
                max_entry.grid(row=0, column=4, sticky="w")
                self._slot_max_entries.append(max_entry)

        self.slot_hint.configure(
            text="值域：" + "　".join(hints) + "　（上限留空或填 0 代表不限）"
        )

    def _rebuild_min_match(self, current: int, slot_count: int) -> None:
        """最少符合格數只針對中間格；末格是絕對匹配，不列入計數。"""
        options = list(range(1, max(slot_count - 1, 1) + 1))
        self.opt_min_match.configure(values=[str(n) for n in options])
        self.var_min_match.set(str(min(current, max(options))))

    def _on_slot_count_change(self, value: str) -> None:
        try:
            count = int(value)
        except ValueError:
            return
        self._apply_settings()  # 先把目前的輸入值寫回
        if self._selected_id:
            self.store.mutate(
                lambda cfg: [
                    setattr(i.criteria, "slot_count", count)
                    for i in cfg.items
                    if i.id == self._selected_id
                ]
            )
        self._refresh_condition_panel()
        self._apply_settings()
        self.log_box.append(f"格數已改為 {count} 格", "info")

    # ── 條件面板（綁定目前選取的物品）────────────────────────────
    def _select_item(self, item_id: str, switch_tab: bool = True) -> None:
        """把某個物品設為條件頁的編輯目標。"""
        self._selected_id = item_id
        for rid, row in self._rows.items():
            row.set_selected(rid == item_id)
        self._refresh_condition_panel()
        if switch_tab:
            self._show_tab("物品條件")

    def _refresh_condition_panel(self) -> None:
        """把選取物品的條件載入編輯欄位；未選取時停用整個區塊。"""
        item = None
        if self._selected_id:
            item = self.store.snapshot().item_by_id(self._selected_id)

        if item is None:
            self._selected_id = None
            self.lbl_cond_target.configure(
                text="尚未選取物品\n請於「物品管理」按 ⚙ 條件 選擇要設定的物品",
                text_color="#6e7681",
            )
            self.cond_body.grid_remove()
            self._rebuild_slot_rows(None)
            return

        criteria = item.criteria
        self.lbl_cond_target.configure(
            text=f"正在編輯：{item.display}　（{item.slot_count_text}）",
            text_color="#e6edf3",
        )
        self.cond_body.grid()

        self.entry_price.delete(0, "end")
        self.entry_price.insert(0, f"{criteria.max_price_ntd:g}")
        self.lbl_price_hint.configure(
            text="填 0 代表不限價"
            + ("" if criteria.unlimited_price else f"　目前上限 {criteria.price_summary}")
        )

        self.var_slot_count.set(str(criteria.slot_count))
        if item.detected_slot_count:
            if item.slot_count_mismatch:
                self.lbl_detected.configure(
                    text=f"⚠ 實際偵測 {item.detected_slot_count} 格，與設定不符，不會有命中",
                    text_color="#d29922",
                )
            else:
                self.lbl_detected.configure(
                    text=f"實際偵測 {item.detected_slot_count} 格，與設定相符", text_color="#8b949e"
                )
        else:
            self.lbl_detected.configure(text="尚未掃描，掃描後會顯示實際格數")

        self._rebuild_slot_rows(criteria)
        self.var_logic.set(criteria.logic)
        self._rebuild_min_match(criteria.effective_min_match, criteria.slot_count)

    def _save_criteria_as_default(self) -> None:
        """把目前編輯中的條件設為之後新增物品的預設。"""
        item = (
            self.store.snapshot().item_by_id(self._selected_id)
            if self._selected_id
            else None
        )
        if item is None:
            self.log_box.append("請先選取一個物品再設為預設", "warn")
            return
        template = item.criteria.copy()
        self.store.mutate(lambda cfg: setattr(cfg, "defaults", template))
        self.log_box.append(
            f"已把「{item.display}」的條件設為新增物品的預設", "sent"
        )

    # ── 監控清單 ────────────────────────────────────────────────
    def _rebuild_items(self, items: Optional[List[ItemEntry]] = None) -> None:
        for row in self._rows.values():
            row.destroy()
        self._rows.clear()

        items = items if items is not None else self.store.snapshot().items
        if self.lbl_empty.winfo_ismapped():
            self.lbl_empty.grid_remove()

        if not items:
            self.lbl_empty.grid()
            self._refresh_condition_panel()
            return

        for index, item in enumerate(items):
            row = ItemRow(
                self.scroll, item,
                on_toggle=self._toggle_item,
                on_remove=self._remove_item,
                on_open=lambda it=item: self._open_market(it),
                on_select=lambda iid: self._select_item(iid),
            )
            row.grid(row=index, column=0, sticky="ew", padx=4, pady=4)
            self._rows[item.id] = row
            row.set_selected(item.id == self._selected_id)

    def _add_item(self) -> None:
        raw = self.entry_hash.get().strip()
        if not raw:
            return
        hash_name = extract_hash_name(raw)
        if not hash_name:
            self.log_box.append("無法解析商品名稱，請確認輸入內容", "error")
            return

        items = self.store.snapshot().items
        if any(normalize_name(i.hash_name) == normalize_name(hash_name) for i in items):
            self.log_box.append(f"「{hash_name}」已在監控清單中", "warn")
            return

        # 新物品採寬鬆預設：先看到實際資料，再自己收緊門檻
        entry = ItemEntry(
            hash_name=hash_name,
            label=self.entry_label.get().strip(),
            criteria=self.store.snapshot().defaults.copy(),
        )

        def apply(cfg: AppConfig) -> None:
            cfg.items.append(entry)

        self.store.mutate(apply)
        self.entry_hash.delete(0, "end")
        self.entry_label.delete(0, "end")
        self._rebuild_items()
        self._select_item(entry.id, switch_tab=False)
        self.log_box.append(
            f"已加入監控：{hash_name}（條件為寬鬆預設，請按 ⚙ 條件 設定）", "sent"
        )

    def _import_defaults(self) -> None:
        existing = {normalize_name(i.hash_name) for i in self.store.snapshot().items}
        added = 0

        def apply(cfg: AppConfig) -> None:
            nonlocal added
            template = cfg.defaults.copy()
            for hash_name, label in DEFAULT_ITEMS:
                if normalize_name(hash_name) in existing:
                    continue
                cfg.items.append(
                    ItemEntry(hash_name=hash_name, label=label, criteria=template.copy())
                )
                added += 1

        self.store.mutate(apply)
        self._rebuild_items()
        self.log_box.append(f"已匯入 {added} 個預設物品", "sent")

    def _toggle_item(self, item_id: str, enabled: bool) -> None:
        self.store.mutate(
            lambda cfg: [
                setattr(i, "enabled", enabled) for i in cfg.items if i.id == item_id
            ]
        )
        row = self._rows.get(item_id)
        if row:
            row.set_meta("已停用" if not enabled else "等待下次掃描")

    def _remove_item(self, item_id: str) -> None:
        target = next((i for i in self.store.snapshot().items if i.id == item_id), None)
        if target is None:
            return
        if not messagebox.askyesno(
            "移除物品", f"確定要將「{target.display}」從監控清單移除嗎？", parent=self
        ):
            return

        def apply(cfg: AppConfig) -> None:
            cfg.items = [i for i in cfg.items if i.id != item_id]
            cfg.notified.pop(item_id, None)

        self.store.mutate(apply)
        if self._selected_id == item_id:
            self._selected_id = None
        self._rebuild_items()
        remaining = self.store.snapshot().items
        if remaining and not self._selected_id:
            self._select_item(remaining[0].id, switch_tab=False)
        else:
            self._refresh_condition_panel()
        self.log_box.append(f"已移除：{target.hash_name}", "info")

    def _open_market(self, item: ItemEntry) -> None:
        import webbrowser

        from ..models import market_url

        webbrowser.open(market_url(item.hash_name))

    # ══════════════════════════════════════════════════════════
    # 爬蟲控制
    # ══════════════════════════════════════════════════════════
    def _start_monitor(self) -> None:
        if self._worker and self._worker.is_alive():
            self.log_box.append("爬蟲已在執行中", "warn")
            return
        self._apply_settings()
        self._worker = CrawlerWorker(
            self.store, self.bridge, self.notifier, file_log=self._file_log
        )
        self._worker.start()

    def _scan_once(self) -> None:
        if self._worker and self._worker.is_alive():
            self.log_box.append("爬蟲已在執行中，將於本輪結束後自動掃描一次", "info")
            self._worker.scan_once()
            return
        self._apply_settings()
        self._worker = CrawlerWorker(
            self.store, self.bridge, self.notifier, file_log=self._file_log
        )
        self._worker.scan_once()
        self._worker.start()

    def _stop_monitor(self) -> None:
        if self._worker and self._worker.is_alive():
            self._worker.stop()
            self.log_box.append("已送出停止訊號，等待目前請求結束…", "warn")

    def _on_close(self) -> None:
        self._apply_settings()
        worker = self._worker
        if worker and worker.is_alive():
            worker.stop()
            worker.join(timeout=3.0)
        try:
            self.store.save()
        except OSError:
            pass
        self.bridge.stop()
        self.destroy()

    # ══════════════════════════════════════════════════════════
    # Bridge 事件處理（皆在主執行緒）
    # ══════════════════════════════════════════════════════════
    def _register_handlers(self) -> None:
        self.bridge.register("log", self._on_log)
        self.bridge.register("status", self._on_status)
        self.bridge.register("scan_start", self._on_scan_start)
        self.bridge.register("item_done", self._on_item_done)
        self.bridge.register("cycle_done", self._on_cycle_done)
        self.bridge.register("toast", self._on_toast)
        self.bridge.register("cooldown", self._on_cooldown)
        self.bridge.register("slots_detected", self._on_slots_detected)

    def _on_log(self, payload: dict) -> None:
        self.log_box.append(payload.get("text", ""), payload.get("level", "info"))

    def _on_status(self, payload: dict) -> None:
        running = bool(payload.get("running"))
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_once.configure(state="disabled" if running else "normal")
        self.lbl_status.configure(
            text="● 監控中" if running else "● 已停止",
            text_color="#3fb950" if running else LEVEL_STYLES["info"],
        )
        if not running:
            self._next_at = None
            self.lbl_next.configure(text="下次掃描：—")
            self.lbl_current.configure(text="閒置")

    def _on_scan_start(self, payload: dict) -> None:
        self.lbl_current.configure(text=f"掃描中（{payload.get('total', 0)} 個物品）")

    def _on_item_done(self, payload: dict) -> None:
        row = self._rows.get(payload.get("item_id", ""))
        if row is None:
            return
        stamp = time.strftime("%H:%M:%S")
        if payload.get("error"):
            row.set_meta(f"爬取失敗：{payload['error']}", LEVEL_STYLES["error"])
        elif payload.get("empty"):
            row.set_meta("取得 0 筆掛單", LEVEL_STYLES["warn"])
        else:
            total = payload.get("total", 0)
            hits = payload.get("hits", 0)
            text = f"共 {total} 筆掛單｜本次通知 {hits} 筆｜{stamp}"
            row.set_meta(text, LEVEL_STYLES["sent"] if hits else LEVEL_STYLES["info"])

    def _on_slots_detected(self, payload: dict) -> None:
        """記下掃描到的實際星格數並更新顯示。

        只寫 ``detected_slot_count``（供對照與警示），不會動
        ``criteria.slot_count`` —— 格數是使用者手動指定的。
        """
        item_id = payload.get("item_id", "")
        detected = int(payload.get("detected", 0))
        if detected not in (3, 4):
            return
        item = self.store.snapshot().item_by_id(item_id)
        if item is None or item.detected_slot_count == detected:
            return

        self.store.mutate(
            lambda cfg: [
                setattr(i, "detected_slot_count", detected)
                for i in cfg.items
                if i.id == item_id
            ]
        )
        if item_id == self._selected_id:
            self._refresh_condition_panel()
        self._sync_row_conditions()

    def _on_cycle_done(self, payload: dict) -> None:
        hits = int(payload.get("hits", 0))
        self._next_at = payload.get("next_at")
        self.lbl_hits.configure(
            text=f"上次掃描：{hits} 筆命中",
            text_color="#7ee787" if hits else LEVEL_STYLES["info"],
        )

    def _on_toast(self, payload: dict) -> None:
        # plyer 會建立 Win32 視窗，固定在主執行緒呼叫
        self.notifier.send_desktop(payload.get("title", ""), payload.get("message", ""))

    def _on_cooldown(self, payload: dict) -> None:
        seconds = int(payload.get("seconds", 0))
        self.lbl_current.configure(text=f"Steam 限速冷卻中（{seconds} 秒）")

    # ── 倒數 ────────────────────────────────────────────────────
    def _tick(self) -> None:
        if self._next_at:
            remaining = int(max(0, self._next_at - time.time()))
            if remaining > 0:
                self.lbl_next.configure(text=f"下次掃描：{remaining // 60:02d}:{remaining % 60:02d}")
            else:
                self.lbl_next.configure(text="下次掃描：準備中…")
        else:
            self.lbl_next.configure(text="下次掃描：—")
        try:
            self.after(1000, self._tick)
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════
    # Telegram 輔助
    # ══════════════════════════════════════════════════════════
    def _fetch_chat_id(self) -> None:
        token = self.entry_token.get().strip()
        if not token:
            self.log_box.append("請先填寫 Bot Token", "warn")
            return
        self.log_box.append("正在向 Telegram 查詢 Chat ID…", "info")
        chat_ids = self.notifier.fetch_chat_ids(token)
        if not chat_ids:
            self.log_box.append(
                "查無 Chat ID：請先在 Telegram 對你的 Bot 傳送一句訊息，再按一次「取得 Chat ID」",
                "warn",
            )
            return
        self.entry_chatid.delete(0, "end")
        self.entry_chatid.insert(0, chat_ids[-1])
        self._apply_settings()
        self.log_box.append(f"已填入 Chat ID：{chat_ids[-1]}（共 {len(chat_ids)} 個）", "sent")

    def _test_telegram(self) -> None:
        cfg = self.store.snapshot().telegram
        if not cfg.usable:
            self.log_box.append("Telegram 設定不完整（需 Token、Chat ID 並啟用）", "warn")
            return
        ok = self.notifier.send_telegram(
            cfg.token, cfg.chat_id, "✅ Naraka 星格監控：Telegram 通知測試成功。"
        )
        self.log_box.append("測試訊息已送出" if ok else "測試訊息發送失敗", "sent" if ok else "error")

    def _clear_notified(self) -> None:
        if not messagebox.askyesno(
            "清除已通知紀錄", "清除後，所有曾通知過的掛單都會再次通知一次。確定嗎？", parent=self
        ):
            return
        self.store.mutate(lambda cfg: cfg.notified.clear())
        self.log_box.append("已清除已通知紀錄", "info")

    def _reset_config(self) -> None:
        if not messagebox.askyesno(
            "還原預設設定", "將清空所有設定與監控物品，確定嗎？", parent=self
        ):
            return
        self.store.reset()
        for entry in (
            self.entry_hash, self.entry_label, self.entry_price, self.entry_interval_min,
            self.entry_interval_max, self.entry_delay_min, self.entry_delay_max,
            self.entry_max_pages, self.entry_token, self.entry_chatid,
            self.entry_cookie_login, self.entry_cookie_session,
        ):
            entry.delete(0, "end")
        self.log_box.append("已還原預設設定", "info")
        self._load_ui()

    # ── 檔案日誌 ────────────────────────────────────────────────
    @staticmethod
    def _file_log(line: str) -> None:
        try:
            path = file_log_path()
            with open(path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except OSError:
            pass


if __name__ == "__main__":  # pragma: no cover
    NarakaApp().mainloop()
