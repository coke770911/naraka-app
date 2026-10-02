"""設定儲存與通知去重測試。"""

from __future__ import annotations

import json
import threading

from naraka.config_store import ConfigStore
from naraka.dedupe import NotifyDedupe
from naraka.models import (
    AppConfig,
    Criteria,
    ItemEntry,
    extract_hash_name,
    normalize_name,
)

REAL_URL = "https://steamcommunity.com/market/listings/1203220/Star%20-%20Shadow%20Scent%28Non-CN%29"


# ── 名稱處理 ──────────────────────────────────────────────────
def test_extract_hash_name_from_real_url():
    assert extract_hash_name(REAL_URL) == "Star - Shadow Scent(Non-CN)"


def test_extract_hash_name_from_plain_text():
    assert extract_hash_name("  Star - Novaburst(Non-CN)  ") == "Star - Novaburst(Non-CN)"
    assert extract_hash_name("") == ""
    assert extract_hash_name("   ") == ""


def test_normalize_name_ignores_case_and_parens():
    assert normalize_name("Star - Shadow Scent(Non-CN)") == normalize_name(
        "star - shadow scentnon-cn"
    )
    assert normalize_name("Star - Novaburst(Non-CN)") != normalize_name(
        "Star - Novaburst(CN)"
    )


# ── ConfigStore ───────────────────────────────────────────────
def test_store_creates_default_file(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)
    store.save()
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["criteria"]["slot_count"] == 3
    assert data["criteria"]["slot_min"] == [9500, 950, 1]


def test_store_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)

    def apply(cfg: AppConfig) -> None:
        cfg.items.append(ItemEntry(hash_name="Star - Novaburst(Non-CN)", label="天流星輝"))
        cfg.criteria.max_price_ntd = 4321.0
        cfg.criteria.slot_count = 4
        cfg.telegram.token = "123:ABC"

    store.mutate(apply)

    reloaded = ConfigStore(path).snapshot()
    assert reloaded.criteria.max_price_ntd == 4321.0
    assert reloaded.criteria.slot_count == 4
    assert reloaded.criteria.slot_min == [9500, 950, 1, 0]  # normalise 補齊
    assert reloaded.telegram.token == "123:ABC"
    assert reloaded.items[0].label == "天流星輝"


def test_store_recovers_from_broken_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    store = ConfigStore(path)
    assert store.snapshot().criteria.slot_count == 3
    assert path.with_suffix(".json.broken").exists()


def test_store_migrate_old_notified_shape(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"version": 1, "notified": {"abc": ["S1", "S2"]}}, ensure_ascii=False),
        encoding="utf-8",
    )
    cfg = ConfigStore(path).snapshot()
    assert cfg.notified == {"abc": ["S1", "S2"]}


def test_snapshot_is_isolated_copy(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    snapshot = store.snapshot()
    snapshot.criteria.max_price_ntd = 1.0
    assert store.snapshot().criteria.max_price_ntd != 1.0


def test_concurrent_mutate_does_not_corrupt(tmp_path):
    store = ConfigStore(tmp_path / "config.json")

    def worker(index: int) -> None:
        for i in range(20):
            store.mutate(
                lambda cfg: cfg.notified.setdefault(f"item{index}", []).append(f"S{i}")
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    notified = store.snapshot().notified
    assert len(notified) == 4
    assert all(len(v) == 20 for v in notified.values())


# ── NotifyDedupe ──────────────────────────────────────────────
def test_dedupe_marks_and_reports():
    dedupe = NotifyDedupe()
    assert not dedupe.seen("item", "S1")
    assert dedupe.mark("item", "S1") is True
    assert dedupe.mark("item", "S1") is False
    assert dedupe.seen("item", "S1")
    assert not dedupe.seen("other", "S1")
    assert dedupe.count("item") == 1


def test_dedupe_roundtrip():
    dedupe = NotifyDedupe()
    dedupe.mark("item", "S1")
    dedupe.mark("item", "S2")
    other = NotifyDedupe()
    other.load(dedupe.to_dict())
    assert other.seen("item", "S1") and other.seen("item", "S2")


def test_dedupe_caps_history():
    dedupe = NotifyDedupe(max_per_item=3)
    for i in range(6):
        dedupe.mark("item", f"S{i}")
    saved = dedupe.to_dict()["item"]
    assert len(saved) == 3
    assert saved == ["S3", "S4", "S5"]  # 保留最新的
    assert not dedupe.seen("item", "S0")


# ── Criteria 預設值 ───────────────────────────────────────────
def test_default_criteria_matches_reference_app():
    criteria = Criteria()
    assert criteria.max_price_ntd == 10000.0
    assert (criteria.slot_count, criteria.logic, criteria.min_match) == (3, "OR", 3)


def test_telegram_usable_requires_all_fields():
    cfg = AppConfig()
    assert not cfg.telegram.usable
    cfg.telegram.token = "1:AA"
    assert not cfg.telegram.usable
    cfg.telegram.chat_id = "123"
    assert cfg.telegram.usable
    cfg.telegram.enabled = False
    assert not cfg.telegram.usable
