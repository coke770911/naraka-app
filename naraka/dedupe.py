"""已通知去重：以 listing 序號（S…）為冪等鍵，避免每輪重複推送。"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Dict, List


class NotifyDedupe:
    """每個物品只保留最近 ``max_per_item`` 個已通知序號。"""

    def __init__(self, max_per_item: int = 500):
        self._max = max(1, int(max_per_item))
        self._lock = threading.Lock()
        self._items: Dict[str, "OrderedDict[str, None]"] = {}

    def load(self, data: Optional[Dict[str, List[str]]]) -> None:
        with self._lock:
            self._items.clear()
            for item_id, numbers in (data or {}).items():
                bucket: "OrderedDict[str, None]" = OrderedDict()
                for number in numbers:
                    bucket[str(number)] = None
                while len(bucket) > self._max:
                    bucket.popitem(last=False)
                self._items[str(item_id)] = bucket

    def seen(self, item_id: str, listing_number: str) -> bool:
        with self._lock:
            bucket = self._items.get(item_id)
            return bool(bucket) and listing_number in bucket

    def mark(self, item_id: str, listing_number: str) -> bool:
        """記錄已通知；回傳是否為新紀錄。"""
        with self._lock:
            bucket = self._items.setdefault(item_id, OrderedDict())
            if listing_number in bucket:
                return False
            bucket[listing_number] = None
            while len(bucket) > self._max:
                bucket.popitem(last=False)
            return True

    def to_dict(self) -> Dict[str, List[str]]:
        with self._lock:
            return {k: list(v.keys()) for k, v in self._items.items()}

    def count(self, item_id: str) -> int:
        with self._lock:
            bucket = self._items.get(item_id)
            return len(bucket) if bucket else 0

    def clear(self, item_id: Optional[str] = None) -> None:
        with self._lock:
            if item_id is None:
                self._items.clear()
            else:
                self._items.pop(item_id, None)
