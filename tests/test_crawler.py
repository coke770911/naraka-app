"""爬蟲單輪流程整合測試（以假的 Steam 來源取代網路）。"""

from __future__ import annotations

import pytest
from make_fixture import build_html

from naraka.config_store import ConfigStore
from naraka.crawler import CrawlerWorker
from naraka.models import AppConfig, ItemEntry

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
        cfg.items.append(ItemEntry(hash_name=HASH, label="謫星·夜影浮香"))
        cfg.criteria.max_price_ntd = 10000.0
        cfg.criteria.slot_count = 3
        cfg.criteria.slot_min = [9500, 950, 1]
        cfg.criteria.slot_max = [0, 0, 1]
        cfg.criteria.logic = "AND"
        cfg.criteria.min_match = 3
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
        cfg.criteria.max_price_ntd = 5000.0

    worker._store.mutate(apply)
    hits = worker._cycle(worker._store.snapshot())

    assert hits == 0
    assert worker._notifier.telegram == []
    assert worker._client.text_calls  # 有實際抓頁面
    assert any("符合 0 筆" in text for text in worker._bridge.logs("info"))


def test_slot_condition_filters_out_wrong_values(worker):
    def apply(cfg: AppConfig) -> None:
        cfg.criteria.slot_min = [9999, 999, 1]

    worker._store.mutate(apply)
    hits = worker._cycle(worker._store.snapshot())

    assert hits == 0
    assert worker._notifier.telegram == []
    assert any("取得 4 筆" in text for text in worker._bridge.logs("info"))
    assert any("符合 0 筆" in text for text in worker._bridge.logs("info"))


def test_no_items_logs_warning(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    crawler = CrawlerWorker(store, FakeBridge(), FakeNotifier())
    crawler._client = FakeSteam()

    assert crawler._cycle(store.snapshot()) == 0
    assert any("沒有啟用中的監控物品" in text for text in crawler._bridge.logs("warn"))
