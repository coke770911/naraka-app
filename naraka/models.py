"""資料模型與設定資料類別。

資料來源（已對照實際 Steam 市場頁結構驗證）：

    GET https://steamcommunity.com/market/listings/1203220/<urlencoded hash>?start=N

回傳的 HTML 內含：

    window.SSR.renderContext=JSON.parse("....")

其中 ``queryData`` 是一段巢狀的 JSON 字串，結構為::

    queries[].state.data.pages[]        # 每頁 20 筆
      pages[].total_count               # 總筆數
      pages[].listings[]                # listing 物件
        listings[].strSubtotal           # "NT$9,650"
        listings[].description.descriptions[]
            -> { "type": "bbcode", "value":
                 "Number: S12345678\\nConstellation: 9650-950-1\\n
                  Star Stats: 123\\nAvailable server: Asia" }
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

APP_ID = "1203220"
MARKET_BASE = "https://steamcommunity.com/market/listings/{app_id}/{name}"
SSR_PAGE = "https://steamcommunity.com/market/listings/{app_id}/{name}?start={start}"
LISTING_BASE = "https://steamcommunity.com/market/listings/{app_id}/{name}/render/"

#: 星格各格的理論值域（0/1 為最後一格的二元位）
SLOT_LIMITS: Dict[int, tuple] = {
    3: ((0, 9999), (0, 999), (0, 1)),
    4: ((0, 9999), (0, 999), (0, 999), (0, 1)),
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def market_url(hash_name: str) -> str:
    return MARKET_BASE.format(app_id=APP_ID, name=quote(hash_name, safe=""))


def listing_ssr_url(hash_name: str, start: int = 0) -> str:
    """新版（SSR）市場頁，每頁 20 筆。"""
    return SSR_PAGE.format(app_id=APP_ID, name=quote(hash_name, safe=""), start=start)


def listing_page_url(hash_name: str, start: int = 0, count: int = 20) -> str:
    """舊版 JSON 端點（SSR 解析失敗時的備援）。"""
    url = LISTING_BASE.format(
        app_id=APP_ID,
        name=quote(hash_name, safe=""),
    )
    return f"{url}?query=&start={start}&count={count}&country=TW&language=schinese&currency=28"


def normalize_name(name: str) -> str:
    """Steam 搜尋結果與設定值的正規化比對用。"""
    return name.strip().lower().replace("(", "").replace(")", "").replace(" ", "")


def extract_hash_name(text: str) -> str:
    """接受完整網址或直接貼 hash_name，回傳乾淨的 market_hash_name。"""
    raw = (text or "").strip().strip("\"'")
    if not raw:
        return ""
    if raw.lower().startswith("http"):
        parsed = urlparse(raw)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return ""
        return unquote(parts[-1])
    return raw


def parse_constellation(raw: str) -> List[int]:
    """``"9650-950-1"`` -> ``[9650, 950, 1]``（非數字段落以 0 代替）。"""
    slots: List[int] = []
    for part in (raw or "").strip().split("-"):
        part = part.strip()
        slots.append(int(part) if part.isdigit() else 0)
    return slots


@dataclass
class Listing:
    """單筆市場掛單。"""

    hash_name: str
    listing_number: str = ""
    constellation_raw: str = ""
    slots: List[int] = field(default_factory=list)
    price_ntd: float = 0.0
    star_stats: int = 0
    available_server: str = ""

    @property
    def slots_text(self) -> str:
        return " | ".join(str(s) for s in self.slots)

    @property
    def price_text(self) -> str:
        return f"NT${self.price_ntd:,.0f}"

    @property
    def url(self) -> str:
        return market_url(self.hash_name)

    def summary(self) -> str:
        parts = [self.listing_number or "?", self.slots_text, self.price_text]
        if self.available_server:
            parts.append(self.available_server)
        return " | ".join(p for p in parts if p)


@dataclass
class Criteria:
    """全域監控條件：單一價格上限 + 逐格星格門檻。"""

    max_price_ntd: float = 10000.0
    slot_count: int = 3
    #: 每格下限；最後一格為「精確等於」的值
    slot_min: List[int] = field(default_factory=lambda: [9500, 950, 1])
    #: 每格上限；0 或負數代表不限；最後一格不使用
    slot_max: List[int] = field(default_factory=lambda: [0, 0, 1])
    #: AND = 所有格都必須符合；OR = 符合格數 >= min_match
    logic: str = "OR"
    min_match: int = 3

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Criteria":
        d = d or {}
        return cls(
            max_price_ntd=float(d.get("max_price_ntd", 10000.0) or 0),
            slot_count=int(d.get("slot_count", 3) or 3),
            slot_min=[int(v) for v in (d.get("slot_min") or [9500, 950, 1])],
            slot_max=[int(v) for v in (d.get("slot_max") or [0, 0, 1])],
            logic=str(d.get("logic", "OR")).upper(),
            min_match=int(d.get("min_match", 3) or 1),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def normalise(self) -> None:
        if self.slot_count not in (3, 4):
            self.slot_count = 3
        if self.logic not in ("AND", "OR"):
            self.logic = "OR"
        self.slot_min = _fit(self.slot_min, self.slot_count, default=0)
        self.slot_max = _fit(self.slot_max, self.slot_count, default=0)
        self.min_match = max(1, min(int(self.min_match), self.slot_count))
        if self.max_price_ntd <= 0:
            self.max_price_ntd = 0.0

    @property
    def effective_min_match(self) -> int:
        return max(1, min(int(self.min_match), self.slot_count))

    def slot_range_hint(self, index: int) -> str:
        limits = SLOT_LIMITS.get(self.slot_count)
        if not limits or index >= len(limits):
            return ""
        lo, hi = limits[index]
        if index == len(limits) - 1:  # 最後一格是 0/1 二元位，一律精確比對
            return "精確值 0 或 1"
        return f"值域 {lo}~{hi}"


def _fit(values: List[int], size: int, default: int = 0) -> List[int]:
    out = [int(v) for v in (values or [])][:size]
    while len(out) < size:
        out.append(default)
    return out


@dataclass
class CrawlerConfig:
    interval_min_sec: float = 180.0
    interval_max_sec: float = 300.0
    delay_min_sec: float = 2.0
    delay_max_sec: float = 5.0
    timeout_sec: float = 20.0
    max_pages: int = 10
    max_retries: int = 5
    cooldown_sec: int = 600
    request_gap_sec: float = 1.5
    verbose: bool = False

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "CrawlerConfig":
        d = d or {}
        base = cls()
        for key in base.__dataclass_fields__:
            if key not in d or d[key] is None:
                continue
            current = getattr(base, key)
            try:
                if isinstance(current, bool):
                    setattr(base, key, bool(d[key]))
                else:
                    setattr(base, key, type(current)(d[key]))
            except (TypeError, ValueError):
                continue
        return base

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def normalise(self) -> None:
        self.interval_min_sec = max(5.0, float(self.interval_min_sec))
        self.interval_max_sec = max(self.interval_min_sec, float(self.interval_max_sec))
        self.delay_min_sec = max(0.0, float(self.delay_min_sec))
        self.delay_max_sec = max(self.delay_min_sec, float(self.delay_max_sec))
        self.timeout_sec = max(5.0, float(self.timeout_sec))
        self.max_pages = max(1, int(self.max_pages))
        self.max_retries = max(0, int(self.max_retries))
        self.cooldown_sec = max(0, int(self.cooldown_sec))
        self.request_gap_sec = max(0.0, float(self.request_gap_sec))


@dataclass
class TelegramConfig:
    token: str = ""
    chat_id: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "TelegramConfig":
        d = d or {}
        return cls(
            token=str(d.get("token", "") or ""),
            chat_id=str(d.get("chat_id", "") or ""),
            enabled=bool(d.get("enabled", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def usable(self) -> bool:
        return self.enabled and bool(self.token.strip()) and bool(self.chat_id.strip())


@dataclass
class SteamConfig:
    cookies: Dict[str, str] = field(default_factory=dict)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "SteamConfig":
        d = d or {}
        cookies = d.get("cookies") or {}
        return cls(
            cookies={str(k): str(v) for k, v in cookies.items() if str(v).strip()},
            user_agent=str(d.get("user_agent") or cls.user_agent),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ItemEntry:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    hash_name: str = ""
    label: str = ""
    enabled: bool = True
    added_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "ItemEntry":
        d = d or {}
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:12]),
            hash_name=str(d.get("hash_name", "") or "").strip(),
            label=str(d.get("label", "") or "").strip(),
            enabled=bool(d.get("enabled", True)),
            added_at=str(d.get("added_at") or now_iso()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def display(self) -> str:
        return self.label or self.hash_name


@dataclass
class AppConfig:
    version: int = 1
    criteria: Criteria = field(default_factory=Criteria)
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    desktop_notify: bool = True
    steam: SteamConfig = field(default_factory=SteamConfig)
    items: List[ItemEntry] = field(default_factory=list)
    notified: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "AppConfig":
        d = d or {}
        return cls(
            version=int(d.get("version", 1) or 1),
            criteria=Criteria.from_dict(d.get("criteria")),
            crawler=CrawlerConfig.from_dict(d.get("crawler")),
            telegram=TelegramConfig.from_dict(d.get("telegram")),
            desktop_notify=bool(d.get("desktop_notify", True)),
            steam=SteamConfig.from_dict(d.get("steam")),
            items=[ItemEntry.from_dict(i) for i in (d.get("items") or [])],
            notified={
                str(k): [str(x) for x in (v or [])]
                for k, v in (d.get("notified") or {}).items()
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "criteria": self.criteria.to_dict(),
            "crawler": self.crawler.to_dict(),
            "telegram": self.telegram.to_dict(),
            "desktop_notify": self.desktop_notify,
            "steam": self.steam.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "notified": self.notified,
        }

    def normalise(self) -> None:
        self.criteria.normalise()
        self.crawler.normalise()
