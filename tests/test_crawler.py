"""爬蟲單輪流程整合測試（以假的 Steam 來源取代網路）。"""

from __future__ import annotations

import pytest
from make_fixture import build_html

from naraka.config_store import ConfigStore
from naraka.crawler import CrawlerWorker
from naraka.models import ANY, AppConfig, ItemEntry

HASH = "Star - Shadow Scent(Non-CN)"
PAGE = build_html()
SEARCH_PAYLOAD = {
    "total_count": 1,
    "results": [
        {
            "hash_name": HASH,
            "sell_listings": 12,
            "sell_price": 965000,  # cents -> NT$9,650
        }
    ],
}


class FakeBridge:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def post(self, kind: str, **payload) -> None:
        self.events.append((kind, payload))

    def of(self, kind: str) -> list[dict]:
        return [payload for name, payload in self.events if name == kind]

    def logs(self, level: str) -> list[str]:
        return [p["text"] for p in self.of("log") if p.get("level") == level]


class FakeNotifier:
    def __init__(self) -> None:
        self.telegram: list[str] = []
        self.desktop: list[tuple[str, str]] = []

    def send_telegram(self, token: str, chat_id: str, text: str) -> bool:
        self.telegram.append(text)
        return True

    def send_desktop(self, title: str, message: str) -> bool:
        self.desktop.append((title, message))
        return True


class FakeSteam:
    def __init__(self) -> None:
        self.text_calls: list[str] = []

    def apply(self, cfg) -> None:
        pass

    def in_cooldown(self) -> bool:
        return False

    @property
    def cooldown_remaining(self) -> int:
        return 0

    def get_text(self, url: str) -> str:
        self.text_calls.append(url)
        return PAGE

    def get_json(self, url: str) -> dict:
        return SEARCH_PAYLOAD


@pytest.fixture()
def worker(tmp_path):
    store = ConfigStore(tmp_path / "config.json")

    def apply(cfg: AppConfig) -> None:
        item = ItemEntry(hash_name=HASH, label="謫星·夜影浮香")
        item.criteria.max_price_ntd = 10000.0
        item.criteria.slot_count = 3
        item.criteria.slot_min = [9500, 950, 1]
        item.criteria.slot_max = [0, 0, 1]
        item.criteria.logic = "AND"
        item.criteria.min_match = 3
        cfg.items.append(item)
        cfg.crawler.delay_min_sec = 0.0
        cfg.crawler.delay_max_sec = 0.0
        cfg.crawler.interval_min_sec = 5.0
        cfg.telegram.token = "1:AA"
        cfg.telegram.chat_id = "42"

    store.mutate(apply)

    crawler = CrawlerWorker(store, FakeBridge(), FakeNotifier())
    crawler._client = FakeSteam()
    return crawler


def test_single_cycle_matches_and_notifies(worker):
    hits = worker._cycle(worker._store.snapshot())

    assert hits == 1
    hits_logs = worker._bridge.logs("hit")
    assert len(hits_logs) == 1
    assert "9650 | 950 | 1" in hits_logs[0]
    assert "NT$9,650" in hits_logs[0]

    assert len(worker._notifier.telegram) == 1
    message = worker._notifier.telegram[0]
    assert "謫星·夜影浮香" in message
    assert "9650 | 950 | 1" in message
    assert "S10000001" in message
    assert "steamcommunity.com/market/listings/1203220" in message

    assert len(worker._bridge.of("toast")) == 1
    assert worker._bridge.of("item_done")[0]["hits"] == 1


def test_second_cycle_does_not_resend(worker):
    worker._cycle(worker._store.snapshot())
    worker._bridge.events.clear()
    worker._notifier.telegram.clear()

    hits = worker._cycle(worker._store.snapshot())

    assert hits == 0
    assert worker._notifier.telegram == []
    assert worker._bridge.logs("hit") == []
    assert any("已通知過" in text for text in worker._bridge.logs("info"))


def test_price_cap_filters_matched_listing(worker):
    """價格上限改由實際抓到的掛單比對（不再靠 search/render 預檢）。"""
    def apply(cfg: AppConfig) -> None:
        cfg.items[0].criteria.max_price_ntd = 5000.0

    worker._store.mutate(apply)
    hits = worker._cycle(worker._store.snapshot())

    assert hits == 0
    assert worker._notifier.telegram == []
    assert worker._client.text_calls  # 有實際抓頁面
    assert any("符合 0 筆" in text for text in worker._bridge.logs("info"))


def test_slot_condition_filters_out_wrong_values(worker):
    def apply(cfg: AppConfig) -> None:
        cfg.items[0].criteria.slot_min = [9999, 999, 1]

    worker._store.mutate(apply)
    hits = worker._cycle(worker._store.snapshot())

    assert hits == 0
    assert worker._notifier.telegram == []
    assert any("取得 4 筆" in text for text in worker._bridge.logs("info"))
    assert any("符合 0 筆" in text for text in worker._bridge.logs("info"))


def test_crawler_reports_detected_slot_count(worker):
    """自動模式下要把實際格數回報給 UI。"""
    def apply(cfg: AppConfig) -> None:
        cfg.items[0].criteria.slot_count = 0

    worker._store.mutate(apply)
    worker._cycle(worker._store.snapshot())

    events = worker._bridge.of("slots_detected")
    assert events and events[0]["detected"] == 3
    assert any("3 格" in text for text in worker._bridge.logs("info"))


def test_manual_slot_count_conflict_warns(worker):
    """手動指定 4 格但實際是 3 格 → 警告，但仍以實際格數比對。"""
    def apply(cfg: AppConfig) -> None:
        cfg.items[0].criteria.slot_count = 4

    worker._store.mutate(apply)
    hits = worker._cycle(worker._store.snapshot())

    assert any("實際掛單是 3 格" in text for text in worker._bridge.logs("warn"))
    assert hits == 0  # 格數不符 → 不命中，避免拿錯條件比出假結果


def test_each_item_uses_own_criteria(tmp_path):
    """兩個物品各用自己的條件：3 格與 4 格商品同時命中。"""
    store = ConfigStore(tmp_path / "config.json")

    def apply(cfg: AppConfig) -> None:
        three = ItemEntry(hash_name=HASH, label="夜影浮香")
        three.criteria.max_price_ntd = 10000.0
        three.criteria.slot_count = 3
        three.criteria.slot_min = [9000, 900, ANY]
        three.criteria.slot_max = [0, 0, 0]
        three.criteria.logic = "AND"

        four = ItemEntry(hash_name="Star - Novaburst(Non-CN)", label="天流星輝")
        four.criteria.max_price_ntd = 50000.0
        four.criteria.slot_count = 4
        four.criteria.slot_min = [900, 900, ANY, ANY]
        four.criteria.slot_max = [0, 0, 0, 0]
        four.criteria.logic = "AND"

        cfg.items.extend([three, four])
        cfg.crawler.delay_min_sec = 0.0
        cfg.crawler.delay_max_sec = 0.0

    store.mutate(apply)
    crawler = CrawlerWorker(store, FakeBridge(), FakeNotifier())
    crawler._client = FakeSteam()
    hits = crawler._cycle(store.snapshot())

    # 3 格物品命中 1 筆（9650-950-1）；4 格物品拿到同一份 fixture 也命中 1 筆
    assert hits == 2
    labels = " ".join(crawler._bridge.logs("hit"))
    assert "夜影浮香" in labels and "天流星輝" in labels


def test_no_items_logs_warning(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    crawler = CrawlerWorker(store, FakeBridge(), FakeNotifier())
    crawler._client = FakeSteam()

    assert crawler._cycle(store.snapshot()) == 0
    assert any("沒有啟用中的監控物品" in text for text in crawler._bridge.logs("warn"))
