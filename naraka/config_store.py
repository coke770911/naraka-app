"""設定持久化：config.json 的讀寫（原子寫入 + 執行緒安全）。"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

from .market_store import MarketStore
from .models import AppConfig


class ConfigStore:
    """所有設定存取都必須經過 :meth:`mutate` / :meth:`snapshot`（皆有鎖保護）。

    背景爬蟲執行緒透過 :meth:`snapshot` 取得一份 deep copy，因此不會讀到
    UI 正在修改中的半成品狀態。
    """

    def __init__(self, path: Path, database: Optional[Path] = None):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cfg = self._read()
        self._cfg.normalise()
        self._market = MarketStore(database or self._path.with_name("monitor.db"))
        self._migrate_market_data()

    # ── 基本存取 ────────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return self._path

    def snapshot(self) -> AppConfig:
        """取得設定的獨立副本（給背景執行緒使用）。"""
        with self._lock:
            return copy.deepcopy(self._cfg)

    def mutate(self, fn: Callable[[AppConfig], None], save: bool = True) -> AppConfig:
        """在鎖內修改設定；預設會立刻寫回磁碟。"""
        with self._lock:
            fn(self._cfg)
            self._cfg.normalise()
            self._market.save(self._cfg.items, self._cfg.notified)
            if save:
                self._write_locked()
            return copy.deepcopy(self._cfg)

    def save(self) -> None:
        with self._lock:
            self._write_locked()

    def reset(self) -> AppConfig:
        with self._lock:
            self._cfg = AppConfig()
            self._cfg.normalise()
            self._market.clear_all()
            self._write_locked()
            return copy.deepcopy(self._cfg)

    # ── 內部 ────────────────────────────────────────────────────
    def _migrate_market_data(self) -> None:
        """首次啟動將 JSON 內物品與通知狀態搬入 SQLite；之後 SQLite 為唯一來源。"""
        legacy_items = self._cfg.items
        legacy_notified = self._cfg.notified
        if not self._market.has_data() and (legacy_items or legacy_notified):
            self._market.save(legacy_items, legacy_notified)
        self._cfg.items = self._market.load_items()
        self._cfg.notified = self._market.load_notified()
        # 移除過時 JSON 副本，避免兩個來源互相覆寫。
        if legacy_items or legacy_notified:
            self._write_locked()

    def _read(self) -> AppConfig:
        """讀取設定檔。

        「讀不到」與「內容壞掉」必須分開處理：前者多半是暫時性的占用
        （被鎖、權限不足、同步／防毒軟體正在掃描），這時若照樣把檔案
        改名保留，使用者的設定就憑空消失，而且下一次存檔會把預設值蓋
        上去。原子寫入（``os.replace``）保證不會讀到寫到一半的內容，
        因此讀取失敗一律往外拋錯，交由呼叫端處理。
        """
        if not self._path.exists():
            return AppConfig()

        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return AppConfig()  # 檢查後剛好被刪掉，視為尚未有設定檔

        try:
            return AppConfig.from_dict(json.loads(text))
        except (ValueError, AttributeError, TypeError):
            # 內容確實無法使用（JSON 損毀或形狀不對）：改名保留原檔，
            # 避免直接抹除使用者資料。
            self._quarantine()
            return AppConfig()

    def _quarantine(self) -> None:
        """把無法使用的設定檔改名保留；失敗就算了，不能因此中斷啟動。"""
        try:
            self._path.replace(self._path.with_suffix(".json.broken"))
        except OSError:
            pass

    def _write_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix="config_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                payload = self._cfg.to_dict()
                payload.pop("items", None)
                payload.pop("notified", None)
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
