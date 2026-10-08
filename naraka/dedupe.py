"""已通知去重：以掛單序號及最後通知價格避免重複推送。

相同序號改價時必須再次通知，因此不能只記錄「看過這個序號」。舊版設定檔的
序號清單沒有價格，會以 ``None`` 載入；首次遇到時只建立價格基準，不重發通知。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple


class NotifyDedupe:
    """每個物品保留最近 ``max_per_item`` 筆序號與最後通知價格。"""

    def __init__(self, max_per_item: int = 500):
        self._max = max(1, int(max_per_item))
        self._lock = threading.Lock()
        self._items: Dict[str, "OrderedDict[str, Optional[float]]"] = {}

    def load(self, data: Optional[Dict[str, Any]]) -> None:
        """載入新版價格對照表，並兼容舊版 ``[listing_number]`` 清單。"""
        with self._lock:
            self._items.clear()
            for item_id, entries in (data or {}).items():
                bucket: "OrderedDict[str, Optional[float]]" = OrderedDict()
                source = entries.items() if isinstance(entries, dict) else ((n, None) for n in (entries or []))
                for number, price in source:
                    bucket[str(number)] = _price_or_none(price)
                while len(bucket) > self._max:
                    bucket.popitem(last=False)
                self._items[str(item_id)] = bucket

    def observe(self, item_id: str, listing_number: str, price: float) -> Tuple[str, Optional[float]]:
        """記錄目前價格並回傳 ``new``、``same``、``changed`` 或 ``baseline``。

        ``baseline`` 表示該序號來自舊版沒有價格的紀錄；只補上基準，不應重發通知。
        """
        current = _price_or_none(price)
        if current is None:
            raise ValueError("listing price 必須是有效數字")
        with self._lock:
            bucket = self._items.setdefault(item_id, OrderedDict())
            if listing_number not in bucket:
                bucket[listing_number] = current
                self._trim(bucket)
                return "new", None
            previous = bucket[listing_number]
            bucket.move_to_end(listing_number)
            if previous is None:
                bucket[listing_number] = current
                return "baseline", None
            if previous == current:
                return "same", previous
            bucket[listing_number] = current
            return "changed", previous

    def seen(self, item_id: str, listing_number: str) -> bool:
        with self._lock:
            bucket = self._items.get(item_id)
            return bool(bucket) and listing_number in bucket

    def mark(self, item_id: str, listing_number: str) -> bool:
        """舊 API 相容：記錄一筆沒有價格的已通知序號。"""
        with self._lock:
            bucket = self._items.setdefault(item_id, OrderedDict())
            if listing_number in bucket:
                return False
            bucket[listing_number] = None
            self._trim(bucket)
            return True

    def to_dict(self) -> Dict[str, Dict[str, Optional[float]]]:
        with self._lock:
            return {key: dict(bucket) for key, bucket in self._items.items()}

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

    def _trim(self, bucket: "OrderedDict[str, Optional[float]]") -> None:
        while len(bucket) > self._max:
            bucket.popitem(last=False)


def _price_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
