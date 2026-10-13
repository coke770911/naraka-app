"""SQLite 持久化：監控物品條件與最後通知價格。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import ItemEntry


class MarketStore:
    """監控物品與通知狀態的 SQLite 儲存庫。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitored_items (
                    id TEXT PRIMARY KEY,
                    hash_name TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL,
                    criteria_json TEXT NOT NULL,
                    detected_slot_count INTEGER NOT NULL DEFAULT 0,
                    added_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_state (
                    item_id TEXT NOT NULL,
                    listing_number TEXT NOT NULL,
                    last_notified_price REAL,
                    PRIMARY KEY (item_id, listing_number),
                    FOREIGN KEY (item_id) REFERENCES monitored_items(id) ON DELETE CASCADE
                );
                """
            )

    def has_data(self) -> bool:
        with self._connect() as conn:
            return bool(conn.execute("SELECT 1 FROM monitored_items LIMIT 1").fetchone())

    def load_items(self) -> List[ItemEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, hash_name, label, enabled, criteria_json, detected_slot_count, added_at "
                "FROM monitored_items ORDER BY rowid"
            ).fetchall()
        items: List[ItemEntry] = []
        for row in rows:
            try:
                items.append(ItemEntry.from_dict({
                    "id": row["id"], "hash_name": row["hash_name"],
                    "label": row["label"], "enabled": bool(row["enabled"]),
                    "criteria": json.loads(row["criteria_json"]),
                    "detected_slot_count": row["detected_slot_count"], "added_at": row["added_at"],
                }))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return items

    def load_notified(self) -> Dict[str, Dict[str, Optional[float]]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT item_id, listing_number, last_notified_price "
                "FROM notification_state ORDER BY rowid"
            ).fetchall()
        result: Dict[str, Dict[str, Optional[float]]] = {}
        for row in rows:
            price = row["last_notified_price"]
            result.setdefault(row["item_id"], {})[row["listing_number"]] = (
                round(float(price), 2) if price is not None else None
            )
        return result

    def save(self, items: Iterable[ItemEntry], notified: Dict[str, Dict[str, Optional[float]]]) -> None:
        """以交易同步物品與最後通知價格；刪除物品時連同通知狀態清除。"""
        rows = list(items)
        item_ids = {item.id for item in rows}
        with self._connect() as conn:
            with conn:
                if item_ids:
                    placeholders = ",".join("?" for _ in item_ids)
                    conn.execute(f"DELETE FROM monitored_items WHERE id NOT IN ({placeholders})", tuple(item_ids))
                else:
                    conn.execute("DELETE FROM monitored_items")
                for item in rows:
                    conn.execute(
                        """
                        INSERT INTO monitored_items
                            (id, hash_name, label, enabled, criteria_json, detected_slot_count, added_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            hash_name=excluded.hash_name, label=excluded.label,
                            enabled=excluded.enabled, criteria_json=excluded.criteria_json,
                            detected_slot_count=excluded.detected_slot_count, added_at=excluded.added_at
                        """,
                        (item.id, item.hash_name, item.label, int(item.enabled),
                         json.dumps(item.criteria.to_dict(), ensure_ascii=False),
                         item.detected_slot_count, item.added_at),
                    )
                conn.execute("DELETE FROM notification_state")
                conn.executemany(
                    "INSERT INTO notification_state (item_id, listing_number, last_notified_price) VALUES (?, ?, ?)",
                    ((item_id, number, price)
                     for item_id, entries in notified.items() if item_id in item_ids
                     for number, price in entries.items()),
                )

    def clear_all(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM monitored_items")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
