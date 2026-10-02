"""設定持久化：config.json 的讀寫（原子寫入 + 執行緒安全）。"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .models import AppConfig


class ConfigStore:
    """所有設定存取都必須經過 :meth:`mutate` / :meth:`snapshot`（皆有鎖保護）。

    背景爬蟲執行緒透過 :meth:`snapshot` 取得一份 deep copy，因此不會讀到
    UI 正在修改中的半成品狀態。
    """

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cfg = self._read()
        self._cfg.normalise()

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
            self._write_locked()
            return copy.deepcopy(self._cfg)

    # ── 內部 ────────────────────────────────────────────────────
    def _read(self) -> AppConfig:
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                return AppConfig.from_dict(raw)
            except Exception:
                try:  # 保留損壞檔案，避免使用者資料被直接抹除
                    self._path.replace(self._path.with_suffix(".json.broken"))
                except OSError:
                    pass
        return AppConfig()

    def _write_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix="config_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._cfg.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
