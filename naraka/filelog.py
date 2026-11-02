"""檔案日誌：依大小輪替，避免 crawler.log 無限成長。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 3

_lock = threading.Lock()


def append_log(path: Path, line: str, max_bytes: int = MAX_BYTES, backups: int = BACKUPS) -> None:
    """附加一行日誌；超過 ``max_bytes`` 時改名為 .1、.2…，最多保留 ``backups`` 份。"""
    path = Path(path)
    with _lock:
        try:
            if path.stat().st_size >= max_bytes:
                _rotate(path, backups)
        except OSError:
            pass
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def _rotate(path: Path, backups: int) -> None:
    for index in range(backups - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        if src.exists():
            os.replace(src, path.with_name(f"{path.name}.{index + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))
