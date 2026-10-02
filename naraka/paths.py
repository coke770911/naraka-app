"""應用程式資料目錄（設定檔、日誌）。"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "NarakaStarMonitor"


def data_dir() -> Path:
    """回傳可寫入的資料目錄，優先使用 %LOCALAPPDATA%。"""
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local)
    elif os.name == "nt":  # pragma: no cover - Windows 一定有 LOCALAPPDATA
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    target = base / APP_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def config_path() -> Path:
    return data_dir() / "config.json"


def file_log_path() -> Path:
    return data_dir() / "crawler.log"
