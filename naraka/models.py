"""資料模型與設定資料類別。

資料來源（已對照實際 Steam 市場頁結構驗證）：

    GET https://steamcommunity.com/market/listings/1203220/<urlencoded hash>?start=N

回傳的 HTML 內含下列任一種容器：

    window.SSR.renderContext=JSON.parse("....")
    <script id="valve-ssr-data" type="application/json">...</script>

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
    3: ((0, 9999), (0, 9999), (0, 1)),
    4: ((0, 9999), (0, 9999), (0, 9999), (0, 1)),
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


#: 條件檔案格式版本；2 = 每個物品獨立條件
CONFIG_VERSION = 2

#: 新增物品時預設的格數。實測四個商品有三個是 4 格，先猜 4 較合理；
#: 猜錯不會靜默漏報 —— 掃描時格數不符會 0 筆命中並在日誌警告。
DEFAULT_SLOT_COUNT = 4

#: 格數可選值。每個物品必須手動指定，不再有自動偵測。
SLOT_COUNT_CHOICES = (3, 4)


@dataclass
class Criteria:
    """單一物品的監控條件：價格上限 + 逐格星格門檻。

    每個物品各自持有一份，因為實測各商品的星格數與價格區間差異很大
    （例如 Shadow Scent 是 3 格 / 約 NT$4,000，Novaburst 是 4 格 / 約 NT$47,000），
    用同一份條件套所有商品會誤判。

    星格門檻以 ``None`` 表示「不關心」；因為值域全為非負（``0~9999``，
    最後一格為 ``0/1``），不需要用負數當哨兵值，設定檔裡留空即可。
    """

    #: 價格上限；<= 0 代表不限價
    max_price_ntd: float = 10000.0
    #: 星格格數，只能是 3 或 4（每個物品手動指定）
    slot_count: int = DEFAULT_SLOT_COUNT
    #: 每格下限；None 代表不關心該格。最後一格是「精確等於」的值
    slot_min: List[Optional[int]] = field(default_factory=lambda: [9500, 950, 900, 1])
    #: 每格上限；0 或負數代表不限。最後一格不使用
    slot_max: List[Optional[int]] = field(default_factory=lambda: [0, 0, 0, 0])
    #: AND = 所有中間格都必須符合；OR = 中間格符合數 >= min_match。
    #: 最後一格不受這裡影響，一律是絕對匹配。
    logic: str = "OR"
    #: OR 模式下中間格至少要符合幾格（範圍 1 ~ 格數-1）
    min_match: int = 3

    @classmethod
    def loose(cls) -> "Criteria":
        """新增物品時的寬鬆預設：不限價、不限星格，全部掛單都會命中。

        使用者先加物品、掃描看到實際資料後再自己收緊門檻。
        """
        return cls(
            max_price_ntd=0.0,
            slot_count=DEFAULT_SLOT_COUNT,
            slot_min=[None, None, None, None],
            slot_max=[0, 0, 0, 0],
            logic="OR",
            min_match=1,
        )

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "Criteria":
        d = d or {}
        try:
            max_price = float(d.get("max_price_ntd", 10000.0))
        except (TypeError, ValueError):
            max_price = 10000.0
        return cls(
            max_price_ntd=max_price,
            slot_count=_read_slot_count(d.get("slot_count")),
            slot_min=_read_thresholds(d.get("slot_min"), [9500, 950, 900, 1]),
            slot_max=_read_thresholds(d.get("slot_max"), [0, 0, 0, 0], negative=0),
            logic=str(d.get("logic", "OR")).upper(),
            min_match=int(d.get("min_match", 3) or 1),
        )

    def copy(self) -> "Criteria":
        return Criteria.from_dict(self.to_dict())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def normalise(self) -> None:
        """校正欄位值域。

        格數只有 3/4 兩種；星格門檻的負值一律視為「不關心」（舊版用 -1
        表示不關心，值域全為非負所以這個轉換無損）。補齊時用 ``None``
        當預設，而不是 0 —— 最後一格的 0 會被解讀成「精確等於 0」，
        讓尚未設定的條件看起來像有在生效。
        """
        if self.slot_count not in SLOT_COUNT_CHOICES:
            self.slot_count = DEFAULT_SLOT_COUNT
        if self.logic not in ("AND", "OR"):
            self.logic = "OR"

        size = self.slot_count
        self.slot_min = _fit(self.slot_min, size, default=None, negative=None)
        self.slot_max = _fit(self.slot_max, size, default=0, negative=0)

        if self.max_price_ntd <= 0:
            self.max_price_ntd = 0.0
        self.min_match = self.clamp_min_match()

    @property
    def mid_slot_count(self) -> int:
        """可走 AND/OR 的中間格數（不含最後一格）。"""
        return max(self.slot_count - 1, 0)

    def clamp_min_match(self) -> int:
        """把「最少符合格數」夾到中間格數範圍內。

        最後一格是絕對匹配，不列入計數，所以上限是格數減一。
        """
        return max(1, min(int(self.min_match), max(self.mid_slot_count, 1)))

    @property
    def unlimited_price(self) -> bool:
        return self.max_price_ntd <= 0

    @property
    def price_summary(self) -> str:
        return f"NT${self.max_price_ntd:,.0f}"

    @property
    def effective_min_match(self) -> int:
        return self.clamp_min_match()

    def slot_range_hint(self, index: int) -> str:
        count = self.slot_count
        limits = SLOT_LIMITS.get(count)
        if not limits or index >= len(limits):
            return ""
        lo, hi = limits[index]
        if index == count - 1:  # 最後一格是 0/1 二元位，一律精確比對
            return "精確值 0 或 1" if self.slot_min_at(index) is not None else "不限"
        return f"值域 {lo}~{hi}"

    def slot_min_at(self, index: int) -> Optional[int]:
        """取某格的下限門檻；超出範圍或未設定回傳 ``None``。"""
        if index >= len(self.slot_min):
            return None
        low = self.slot_min[index]
        return None if low is None or low < 0 else int(low)

    def slot_max_at(self, index: int) -> int:
        """取某格的上限門檻；0 或負值代表不限。"""
        if index >= len(self.slot_max):
            return 0
        high = self.slot_max[index]
        return 0 if high is None or int(high) <= 0 else int(high)


def _read_slot_count(raw: Any) -> int:
    """設定檔的格數只接受 3 / 4；其他值（含舊版的 0 = 自動）退回預設。"""
    try:
        count = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SLOT_COUNT
    return count if count in SLOT_COUNT_CHOICES else DEFAULT_SLOT_COUNT


def _read_thresholds(raw: Any, fallback: List[int], negative: Optional[int] = None) -> List[Optional[int]]:
    """讀取逐格門檻；負值（舊版用 -1 表示不關心）轉成 ``negative``。"""
    values = raw if isinstance(raw, (list, tuple)) else fallback
    out: List[Optional[int]] = []
    for value in values:
        number = _as_int(value)
        out.append(negative if number is None or number < 0 else number)
    return out


def _fit(
    values: List[Optional[int]],
    size: int,
    default: Optional[int] = None,
    negative: Optional[int] = None,
) -> List[Optional[int]]:
    """把逐格門檻補齊／截斷到指定格數。

    負值一律視為「不關心」，轉成 ``negative``；``None`` 補齊成 ``default``。
    下限用 ``default=None, negative=None``（留空 = 不關心）；
    上限用 ``default=0, negative=0``（留空 = 不限價，兩者同義）。
    """
    out: List[Optional[int]] = []
    for raw in values or []:
        value = _as_int(raw)
        if value is None or value < 0:
            out.append(negative)
        else:
            out.append(value)
    del out[size:]
    while len(out) < size:
        out.append(default)
    return out


def _as_int(raw: Any) -> Optional[int]:
    """把設定檔／UI 的值轉成 int，失敗回傳 ``None``。"""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


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
    #: 這個物品自己的條件
    criteria: Criteria = field(default_factory=Criteria.loose)
    #: 掃描時偵測到的實際星格數（0 = 尚未偵測）。不參與比對，只用於
    #: 「設定格數與實際不符」的警告與條件摘要顯示。
    detected_slot_count: int = 0
    added_at: str = field(default_factory=now_iso)

    @classmethod
    def from_dict(
        cls,
        d: Optional[Dict[str, Any]],
        legacy: Optional[Criteria] = None,
    ) -> "ItemEntry":
        """由設定檔還原。

        ``legacy`` 為 v1 的全域條件；舊檔沒有逐物品條件時沿用它，
        但格數強制改為預設值 4（見 :meth:`AppConfig.from_dict`）。
        """
        d = d or {}
        raw = d.get("criteria")
        if isinstance(raw, dict):
            criteria = Criteria.from_dict(raw)
        elif legacy is not None:
            criteria = legacy.copy()
        else:
            criteria = Criteria.loose()
        try:
            detected = int(d.get("detected_slot_count", 0) or 0)
        except (TypeError, ValueError):
            detected = 0
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:12]),
            hash_name=str(d.get("hash_name", "") or "").strip(),
            label=str(d.get("label", "") or "").strip(),
            enabled=bool(d.get("enabled", True)),
            criteria=criteria,
            detected_slot_count=detected if detected in (3, 4) else 0,
            added_at=str(d.get("added_at") or now_iso()),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def display(self) -> str:
        return self.label or self.hash_name

    @property
    def slot_count(self) -> int:
        """這個物品實際比對時使用的格數（由使用者手動指定）。"""
        return self.criteria.slot_count

    @property
    def slot_count_mismatch(self) -> bool:
        """設定格數與掃描到的實際格數不一致（尚未掃描過視為一致）。"""
        return bool(self.detected_slot_count) and self.detected_slot_count != self.slot_count

    @property
    def slot_count_text(self) -> str:
        text = f"{self.slot_count} 格"
        if self.slot_count_mismatch:
            text = f"{text}·實際 {self.detected_slot_count} 格⚠"
        return text

    def conditions_summary(self) -> str:
        """物品列上顯示的一行條件摘要。

        中間格門檻與最後一格分開呈現 —— 最後一格是絕對匹配，把它混在
        門檻串裡會讓人以為可以被 OR 稀釋。
        """
        crit = self.criteria
        parts: List[str] = []
        parts.append("不限價" if crit.unlimited_price else f"≤ {crit.price_summary}")
        parts.append(self.slot_count_text)

        size = self.slot_count
        mid: List[str] = []
        last: Optional[str] = None
        for index in range(size):
            low = crit.slot_min_at(index)
            if low is None:
                continue
            if index == size - 1:
                last = f"末格=={low}(必中)"
            elif low > 0:
                mid.append(f"第{index + 1}格≥{low}")
        if mid:
            parts.append(" ".join(mid))
        else:
            parts.append("中間格不限")
        parts.append(last or "末格不限")

        required = crit.mid_slot_count if crit.logic == "AND" else crit.effective_min_match
        parts.append(f"{crit.logic} {required}/{crit.mid_slot_count}")
        return " · ".join(parts)


@dataclass
class AppConfig:
    version: int = CONFIG_VERSION
    #: 新增物品時採用的寬鬆預設條件
    defaults: Criteria = field(default_factory=Criteria.loose)
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    desktop_notify: bool = True
    steam: SteamConfig = field(default_factory=SteamConfig)
    items: List[ItemEntry] = field(default_factory=list)
    notified: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "AppConfig":
        d = d or {}
        try:
            version = int(d.get("version", 1) or 1)
        except (TypeError, ValueError):
            version = 1

        legacy = Criteria.from_dict(d.get("criteria")) if version < CONFIG_VERSION else None
        if legacy is not None:
            # 舊檔只有一份全域條件，而各商品的星格數與價格區間實際上不同，
            # 沿用那份格數只會把錯誤延續下去，因此改用預設格數並請使用者確認。
            legacy.slot_count = DEFAULT_SLOT_COUNT

        return cls(
            version=CONFIG_VERSION,
            defaults=Criteria.from_dict(d.get("defaults")),
            crawler=CrawlerConfig.from_dict(d.get("crawler")),
            telegram=TelegramConfig.from_dict(d.get("telegram")),
            desktop_notify=bool(d.get("desktop_notify", True)),
            steam=SteamConfig.from_dict(d.get("steam")),
            items=[ItemEntry.from_dict(i, legacy) for i in (d.get("items") or [])],
            notified={
                str(k): [str(x) for x in (v or [])]
                for k, v in (d.get("notified") or {}).items()
            },
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "defaults": self.defaults.to_dict(),
            "crawler": self.crawler.to_dict(),
            "telegram": self.telegram.to_dict(),
            "desktop_notify": self.desktop_notify,
            "steam": self.steam.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "notified": self.notified,
        }

    def normalise(self) -> None:
        self.defaults.normalise()
        self.crawler.normalise()
        for item in self.items:
            item.criteria.normalise()

    def item_by_id(self, item_id: str) -> Optional[ItemEntry]:
        for item in self.items:
            if item.id == item_id:
                return item
        return None

    def enabled_items(self) -> List[ItemEntry]:
        return [i for i in self.items if i.enabled and i.hash_name]
