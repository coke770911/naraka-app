"""設定儲存與通知去重測試。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from naraka.config_store import ConfigStore
from naraka.dedupe import NotifyDedupe
from naraka.filters import evaluate_listing, match_slots
from naraka.models import (
    DEFAULT_SLOT_COUNT,
    AppConfig,
    Criteria,
    ItemEntry,
    Listing,
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
    assert data["version"] == 2
    assert data["defaults"]["max_price_ntd"] == 0.0
    assert data["defaults"]["slot_count"] == DEFAULT_SLOT_COUNT
    assert "criteria" not in data  # 全域條件已改為逐物品


def test_store_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)

    def apply(cfg: AppConfig) -> None:
        item = ItemEntry(hash_name="Star - Novaburst(Non-CN)", label="天流星輝")
        item.criteria.max_price_ntd = 4321.0
        item.criteria.slot_count = 4
        item.criteria.slot_min = [5000, 500, 1, None]
        item.criteria.slot_max = [0, 0, 0, 0]
        cfg.items.append(item)
        cfg.telegram.token = "123:ABC"

    store.mutate(apply)

    reloaded = ConfigStore(path).snapshot()
    assert reloaded.telegram.token == "123:ABC"
    assert reloaded.items[0].label == "天流星輝"
    assert reloaded.items[0].criteria.max_price_ntd == 4321.0
    assert reloaded.items[0].criteria.slot_count == 4
    assert reloaded.items[0].criteria.slot_min == [5000, 500, 1, None]
    assert reloaded.items[0].criteria.slot_max == [0, 0, 0, 0]


def test_store_roundtrip_preserves_blank_slots_and_unlimited_price(tmp_path):
    path = tmp_path / "config.json"
    store = ConfigStore(path)

    def apply(cfg: AppConfig) -> None:
        item = ItemEntry(hash_name="Star - Fading Polaris(Non-CN)")
        item.criteria.slot_min = [5000, 500, None, None]
        item.criteria.max_price_ntd = 0.0  # 不限價
        cfg.items.append(item)

    store.mutate(apply)
    reloaded = ConfigStore(path).snapshot().items[0]
    assert reloaded.criteria.slot_count == DEFAULT_SLOT_COUNT
    assert reloaded.criteria.slot_min == [5000, 500, None, None]
    assert reloaded.criteria.unlimited_price
    assert reloaded.slot_count == DEFAULT_SLOT_COUNT
    assert reloaded.slot_count_text == "4 格"
    assert not reloaded.slot_count_mismatch


def test_store_migrates_legacy_any_and_auto(tmp_path):
    """舊設定檔的 slot_count=0 與 -1 讀進來要自動收斂成新格式。"""
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "defaults": {"max_price_ntd": 0.0, "slot_count": 0},
                "items": [
                    {
                        "hash_name": "Star - Fading Polaris(Non-CN)",
                        "criteria": {
                            "max_price_ntd": 5000.0,
                            "slot_count": 0,
                            "slot_min": [5000, 500, -1, -1],
                            "slot_max": [0, 0, -1, -1],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    item = ConfigStore(path).snapshot().items[0]
    assert item.criteria.slot_count == DEFAULT_SLOT_COUNT
    assert item.criteria.slot_min == [5000, 500, None, None]
    assert item.criteria.slot_max == [0, 0, 0, 0]


def test_store_keeps_detected_slot_count(tmp_path):
    """偵測值只是警示用的附加資訊，不會改寫手動設定的格數。"""
    path = tmp_path / "config.json"
    store = ConfigStore(path)

    def apply(cfg: AppConfig) -> None:
        item = ItemEntry(hash_name="Star - Novaburst(Non-CN)")
        item.criteria.slot_count = 4
        item.detected_slot_count = 4
        cfg.items.append(item)

    store.mutate(apply)
    reloaded = ConfigStore(path).snapshot().items[0]
    assert reloaded.detected_slot_count == 4
    assert reloaded.slot_count == 4
    assert not reloaded.slot_count_mismatch
    # 相符時不需標示實際格數
    assert reloaded.slot_count_text == "4 格"


def test_store_recovers_from_broken_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    store = ConfigStore(path)
    assert store.snapshot().defaults.unlimited_price
    assert path.with_suffix(".json.broken").exists()


def test_store_keeps_file_when_read_fails(tmp_path, monkeypatch):
    """讀不到（被鎖／權限不足）不是檔案損壞，絕不能改名或丟棄。

    舊實作用 ``except Exception`` 一律當成損壞，結果設定憑空消失，
    下一次存檔還會把預設值蓋上去。
    """
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2}), encoding="utf-8")

    def boom(self, *args, **kwargs):
        raise PermissionError(13, "used by another process")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(PermissionError):
        ConfigStore(path)
    monkeypatch.undo()

    assert path.exists()  # 原檔未被改名、未被清空
    assert not path.with_suffix(".json.broken").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_store_quarantines_unusable_shape(tmp_path):
    """JSON 合法但形狀不對 → 仍改名保留，回退預設。"""
    path = tmp_path / "config.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = ConfigStore(path)
    assert store.snapshot().defaults.unlimited_price
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
    snapshot.defaults.max_price_ntd = 1.0
    assert store.snapshot().defaults.max_price_ntd != 1.0


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
def test_loose_criteria_matches_everything():
    """寬鬆預設：不限價、每格都不關心，且格數必須相符。

    格數改為手動指定後，寬鬆預設是 4 格 —— 所以 4 格掛單全命中，3 格掛單會被
    格數檢查擋下（使用者可在條件頁改成 3 格）。
    """
    criteria = Criteria.loose()
    criteria.normalise()
    assert criteria.unlimited_price
    assert criteria.slot_count == DEFAULT_SLOT_COUNT
    assert criteria.slot_min == [None] * 4  # 每格都不關心
    assert evaluate_listing(
        Listing("h", "S2", "849-842-87-1", [849, 842, 87, 1], 43479.0), criteria
    ).matched

    narrow = Criteria.loose()
    narrow.slot_count = 3
    narrow.normalise()
    assert evaluate_listing(
        Listing("h", "S1", "5310-0-0", [5310, 0, 0], 4032.0), narrow
    ).matched
    assert not evaluate_listing(
        Listing("h", "S2", "849-842-87-1", [849, 842, 87, 1], 43479.0), narrow
    ).matched


# ── 每個物品獨立條件 ───────────────────────────────────────────
def test_items_keep_separate_criteria():
    cfg = AppConfig()
    three = ItemEntry(hash_name="Star - Shadow Scent(Non-CN)", label="夜影浮香")
    four = ItemEntry(hash_name="Star - Novaburst(Non-CN)", label="天流星輝")
    three.criteria.max_price_ntd = 5000.0
    three.criteria.slot_count = 3
    three.criteria.slot_min = [5000, 500, None]
    three.criteria.slot_max = [0, 0, 0]
    four.criteria.max_price_ntd = 50000.0
    four.criteria.slot_count = 4
    four.criteria.slot_min = [800, 800, 0, None]
    four.criteria.slot_max = [0, 0, 0, 0]
    cfg.items.extend([three, four])
    cfg.normalise()

    cheap = Listing("Star - Shadow Scent(Non-CN)", "S1", "5310-0-0", [5310, 0, 0], 4032.0)
    pricey = Listing("Star - Novaburst(Non-CN)", "S2", "849-842-87-1", [849, 842, 87, 1], 43479.0)

    # 這正是原本全域條件做不到的事：3 格商品與 4 格商品各自命中
    assert evaluate_listing(cheap, three.criteria).matched
    assert evaluate_listing(pricey, four.criteria).matched

    # 價格上限彼此獨立：便宜商品放寬、昂貴商品放寬到涵蓋實際售價
    assert evaluate_listing(pricey, three.criteria).reasons[0].startswith("價格")
    assert not evaluate_listing(pricey, three.criteria).matched


def test_slot_count_mismatch_blocks_match():
    """指定格數與資料不符時不命中 —— 寧可漏報也不拿錯條件比出假結果。"""
    three = Criteria(
        max_price_ntd=5000.0, slot_count=3,
        slot_min=[5000, 500, None], slot_max=[0, 0, 0], logic="AND",
    )
    three.normalise()
    four_slot_listing = Listing("h", "S2", "849-842-87-1", [849, 842, 87, 1], 4032.0)
    result = evaluate_listing(four_slot_listing, three)
    assert not result.matched
    assert any("格數與條件不符" in r for r in result.reasons)


def test_price_caps_are_independent():
    listing = Listing("h", "S1", "9650-950-1", [9650, 950, 1], 8000.0)
    loose = Criteria.loose()
    loose.slot_count = 3
    loose.normalise()
    tight = Criteria(max_price_ntd=5000.0, slot_min=[None, None, None], slot_count=3)
    tight.normalise()
    assert evaluate_listing(listing, loose).matched
    assert not evaluate_listing(listing, tight).matched


def test_unlimited_price_never_blocks():
    criteria = Criteria(max_price_ntd=0.0, slot_min=[9500, 950, 1], slot_count=3)
    expensive = Listing("h", "S1", "9650-950-1", [9650, 950, 1], 250000.0)
    result = evaluate_listing(expensive, criteria)
    assert result.matched
    assert "不限" in result.reasons[0]


def test_four_slot_criteria_anchors_last_slot_to_fourth():
    """4 格條件的末格是第4格（0/1 二元位）；第3格是 0~999 的範圍格。"""
    criteria = Criteria(
        slot_count=4, slot_min=[950, 950, 900, 1],
        slot_max=[0, 0, 0, 0], logic="AND", min_match=4,
    )
    criteria.normalise()
    assert match_slots([960, 960, 910, 1], criteria).matched
    # 第4格（精確值）不符 → 不命中
    assert not match_slots([960, 960, 910, 0], criteria).matched
    # 第3格不符 → 不命中（4 格資料的第3格是 0~999，不做精確比對）
    assert not match_slots([960, 960, 890, 1], criteria).matched


def test_three_slot_criteria_anchors_last_slot_to_third():
    """3 格條件的末格是第3格。"""
    criteria = Criteria(
        slot_count=3, slot_min=[950, 950, 1],
        slot_max=[0, 0, 0], logic="AND", min_match=3,
    )
    criteria.normalise()
    assert match_slots([960, 960, 1], criteria).matched
    assert not match_slots([960, 960, 0], criteria).matched


def test_blank_slot_is_skipped():
    criteria = Criteria(slot_count=3, slot_min=[9500, None, 1], slot_max=[0, 0, 0])
    criteria.normalise()
    assert match_slots([9600, 0, 1], criteria).matched
    result = match_slots([9600, 999, 1], criteria)
    assert result.matched
    assert any("不限" in r for r in result.reasons)


def test_item_reports_slot_count_mismatch():
    """偵測值與手動設定不符時要能分辨出來，供 UI 顯示警示。"""
    item = ItemEntry(hash_name="h")
    item.criteria.slot_count = 4
    assert not item.slot_count_mismatch  # 尚未偵測 → 不算不符
    item.detected_slot_count = 4
    assert not item.slot_count_mismatch
    item.detected_slot_count = 3
    assert item.slot_count_mismatch


def test_conditions_summary_mentions_limits():
    item = ItemEntry(hash_name="h")
    item.criteria.max_price_ntd = 5000.0
    item.criteria.slot_count = 3
    item.criteria.slot_min = [5000, 500, None]
    item.criteria.slot_max = [0, 0, 0]
    item.criteria.logic = "AND"
    summary = item.conditions_summary()
    assert "NT$5,000" in summary
    assert "3 格" in summary
    assert "第1格≥5000" in summary
    assert "第2格≥500" in summary
    assert "不限價" not in summary


def test_conditions_summary_for_loose_item():
    summary = ItemEntry(hash_name="h").conditions_summary()
    assert "不限價" in summary
    assert "4 格" in summary
    assert "中間格不限" in summary
    assert "末格不限" in summary
    assert "OR 1/3" in summary


def test_conditions_summary_separates_last_slot():
    """末格要獨立呈現，避免看起來可以被 OR 稀釋。"""
    item = ItemEntry(hash_name="h")
    item.criteria.slot_count = 4
    item.criteria.slot_min = [950, 950, None, 1]
    summary = item.conditions_summary()
    assert "第1格≥950" in summary
    assert "第2格≥950" in summary
    assert "末格==1(必中)" in summary
    assert "第3格" not in summary
    assert "OR 1/3" in summary


# ── v1 → v2 設定遷移 ──────────────────────────────────────────
def test_v1_global_criteria_distributed_to_items(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "criteria": {
                    "max_price_ntd": 20000,
                    "slot_count": 3,
                    "slot_min": [9000, 900, 1],
                    "slot_max": [0, 0, 1],
                    "logic": "OR",
                    "min_match": 2,
                },
                "items": [
                    {"hash_name": "Star - Shadow Scent(Non-CN)", "label": "A"},
                    {"hash_name": "Star - Novaburst(Non-CN)", "label": "B"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = ConfigStore(path).snapshot()
    assert cfg.version == 2
    assert len(cfg.items) == 2
    for item in cfg.items:
        # 價格與門檻沿用舊的全域條件
        assert item.criteria.max_price_ntd == 20000.0
        assert item.criteria.slot_min[:3] == [9000, 900, 1]
        assert item.criteria.logic == "OR"
        # 舊的全域條件是 3 格，但 3 格對 4 格商品是錯的 → 統一退回預設 4 格，
        # 使用者在條件頁自行調整
        assert item.criteria.slot_count == DEFAULT_SLOT_COUNT
        # 末格不列入計數 → 最少符合格數上限為格數減一
        assert item.criteria.mid_slot_count == 3
        assert item.criteria.min_match == 2


def test_v1_criteria_is_copied_not_shared(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "criteria": {"max_price_ntd": 20000, "slot_count": 3},
                "items": [
                    {"hash_name": "A"},
                    {"hash_name": "B"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = ConfigStore(path).snapshot()
    cfg.items[0].criteria.max_price_ntd = 1.0
    assert cfg.items[1].criteria.max_price_ntd == 20000.0


def test_v2_items_keep_their_own_criteria(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "items": [
                    {
                        "hash_name": "A",
                        "criteria": {"max_price_ntd": 5000, "slot_count": 4,
                                     "slot_min": [800, 800, 0, None]},
                    },
                    {"hash_name": "B"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    cfg = ConfigStore(path).snapshot()
    assert cfg.items[0].criteria.max_price_ntd == 5000.0
    assert cfg.items[0].criteria.slot_count == 4
    assert cfg.items[1].criteria.unlimited_price  # 沒給 criteria → 寬鬆預設


def test_telegram_usable_requires_all_fields():
    cfg = AppConfig()
    assert not cfg.telegram.usable
    cfg.telegram.token = "1:AA"
    assert not cfg.telegram.usable
    cfg.telegram.chat_id = "123"
    assert cfg.telegram.usable
    cfg.telegram.enabled = False
    assert not cfg.telegram.usable
