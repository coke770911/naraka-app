"""星格條件判定測試（案例沿用 Rust 版 parser.rs 的測試）。"""

from __future__ import annotations

import pytest

from naraka.filters import evaluate_listing, match_slots
from naraka.models import ANY, Criteria, Listing, parse_constellation


def make_criteria(slot_min, slot_max=(), logic="OR", min_match=1, slot_count=None):
    criteria = Criteria(
        max_price_ntd=10000.0,
        slot_count=slot_count if slot_count is not None else len(slot_min),
        slot_min=list(slot_min),
        slot_max=list(slot_max),
        logic=logic,
        min_match=min_match,
    )
    return criteria


# ── 星格字串解析 ───────────────────────────────────────────────
def test_parse_3slot():
    assert parse_constellation("9650-950-1") == [9650, 950, 1]


def test_parse_4slot():
    assert parse_constellation("950-950-900-1") == [950, 950, 900, 1]


def test_parse_invalid_part_becomes_zero():
    assert parse_constellation("abc-12-x") == [0, 12, 0]


# ── AND 邏輯 ──────────────────────────────────────────────────
def test_and_logic():
    criteria = make_criteria([9500, 950, 1], logic="AND", min_match=3)
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9400, 960, 1], criteria).matched

    criteria4 = make_criteria([950, 950, 900, 1], logic="AND", min_match=4)
    assert match_slots([960, 960, 910, 1], criteria4).matched
    assert not match_slots([960, 960, 890, 1], criteria4).matched


# ── 最後一格為精確值 ──────────────────────────────────────────
def test_last_slot_exact():
    criteria = make_criteria([9500, 950, 1], logic="AND", min_match=3)
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9600, 960, 2], criteria).matched

    criteria4 = make_criteria([950, 950, 900, 1], logic="AND", min_match=4)
    assert match_slots([960, 960, 910, 1], criteria4).matched
    assert not match_slots([960, 960, 910, 2], criteria4).matched


# ── OR 邏輯 ───────────────────────────────────────────────────
def test_or_logic():
    criteria = make_criteria([9500, 950, 1], logic="OR", min_match=2)
    assert match_slots([9600, 800, 1], criteria).matched
    assert not match_slots([9600, 800, 0], criteria).matched

    criteria4 = make_criteria([950, 950, 900, 1], logic="OR", min_match=2)
    assert match_slots([960, 800, 800, 1], criteria4).matched
    assert not match_slots([960, 800, 800, 0], criteria4).matched


# ── 區間條件 ──────────────────────────────────────────────────
def test_range_logic():
    criteria = make_criteria([9000, 900, 1], [9700, 970, 1], logic="OR", min_match=3)
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9800, 960, 1], criteria).matched
    # 指定 4 格但資料只有 3 格 → 不命中（資料不足以比對）
    assert not match_slots([9600, 960, 1], make_criteria(
        [9000, 900, 1], [9700, 970, 1], logic="OR", min_match=4, slot_count=4
    )).matched


def test_range_zero_means_unbounded():
    criteria = make_criteria([9500, 0, 0], [0, 0, 0], logic="OR", min_match=1)
    assert match_slots([9650, 10, 10], criteria).matched


# ── 格數不一致 / 解析失敗 ─────────────────────────────────────
def test_slot_count_mismatch_is_flagged_but_still_evaluated():
    """指定 3 格但資料只有 2 格：仍要標示差異並判定不命中。"""
    criteria = make_criteria([9500, 950, 1], logic="AND", min_match=3)
    result = match_slots([9650, 950], criteria)
    assert not result.matched
    assert any("星格數為" in reason for reason in result.reasons)
    assert any("格數與條件不符" in reason for reason in result.reasons)


def test_empty_slots_never_matches():
    result = match_slots([], make_criteria([9500, 950, 1]))
    assert not result.matched


# ── 價格 + 星格一起判定 ───────────────────────────────────────
def test_evaluate_listing_price_and_slots():
    criteria = make_criteria([9500, 950, 1], logic="AND", min_match=3)
    criteria.max_price_ntd = 10000.0

    ok = Listing("h", "S1", "9650-950-1", [9650, 950, 1], 9650.0)
    assert evaluate_listing(ok, criteria).matched

    expensive = Listing("h", "S2", "9650-950-1", [9650, 950, 1], 12000.0)
    result = evaluate_listing(expensive, criteria)
    assert not result.matched
    assert result.reasons[0].startswith("價格")


def test_reasons_are_readable():
    criteria = make_criteria([9500, 950, 1], logic="OR", min_match=3)
    listing = Listing("h", "S1", "9650-950-0", [9650, 950, 0], 9000.0)
    text = evaluate_listing(listing, criteria).reason_text()
    assert "第1格 9650 ≥ 9500" in text
    assert "第3格 0 == 1" in text
    assert "符合 2/3 格（需 ≥3）" in text


# ── Criteria.normalise ────────────────────────────────────────
def test_normalise_pads_and_clamps():
    criteria = Criteria(slot_count=4, slot_min=[950, 950], slot_max=[0], logic="xxx", min_match=9)
    criteria.normalise()
    # 補齊的格用 ANY(-1) 而非 0 —— 最後一格的 0 會被解讀成「精確等於 0」，
    # 使用者沒填卻看起來有在生效，會靜默漏掉尾格為 1 的商品。
    assert criteria.slot_min == [950, 950, ANY, ANY]
    assert criteria.slot_max == [0, 0, 0, 0]
    assert criteria.logic == "OR"
    assert criteria.min_match == 4
    assert criteria.slot_count == 4


def test_normalise_keeps_auto_slot_count():
    criteria = Criteria(slot_count=7)  # 非法值
    criteria.normalise()
    assert criteria.slot_count == 0  # 退回自動


def test_normalise_pads_to_detected_width():
    criteria = Criteria(slot_count=0, slot_min=[9000, 900, 1], min_match=2)
    criteria.normalise(detected_slot_count=4)
    assert len(criteria.slot_min) == 4
    assert criteria.effective_min_match == 2


@pytest.mark.parametrize("count", [3, 4])
def test_slot_range_hint(count):
    """值域提示：前面各格是範圍，最後一格是 0/1 二元位（精確比對或不限）。"""
    criteria = Criteria(slot_count=count, slot_min=[ANY] * 4)
    criteria.normalise()
    assert criteria.slot_range_hint(0) == "值域 0~9999"
    last = count - 1
    assert criteria.slot_range_hint(last) == "不限"

    # 有填目標值時才顯示為精確比對
    filled = Criteria(slot_count=count, slot_min=[9500, 950, 900, 1][:count])
    filled.normalise()
    assert filled.slot_range_hint(last) == "精確值 0 或 1"


# ── 格數自動偵測 ──────────────────────────────────────────────
def test_auto_mode_anchors_last_slot_to_data_width():
    """自動模式：末格精確比對跟著「資料本身的寬度」走。

    這是自動模式的核心行為。3 格資料的第3格是 0/1 二元位，會拿 slot_min[2]
    去比；4 格資料的第4格拿 slot_min[3] 比。若固定拿第4格當末格，
    3 格資料會用第3格的門檻去比第3格、然後第4格不存在 → 永遠不會命中。
    """
    three = make_criteria([9500, 950, 1], logic="AND", min_match=3, slot_count=0)
    assert match_slots([9600, 960, 1], three).matched
    assert not match_slots([9600, 960, 0], three).matched

    four = make_criteria([9500, 950, 900, 1], logic="AND", min_match=4, slot_count=0)
    assert match_slots([9600, 960, 910, 1], four).matched
    assert not match_slots([9600, 960, 910, 0], four).matched
    # 第3格是 0~999 的範圍格，不做精確比對
    assert not match_slots([9600, 960, 890, 1], four).matched


def test_auto_mode_does_not_invent_missing_slots():
    """3 格資料不會被憑空補上第4格。"""
    criteria = make_criteria([9500, 950, 1, 1], logic="OR", min_match=2, slot_count=0)
    result = match_slots([9600, 960, 1], criteria)
    assert result.matched
    assert not any("第4格" in r for r in result.reasons)


def test_min_match_clamped_to_checked_slots():
    """有格設成 -1 時，OR 的 min_match 要夾到實際檢查的格數，否則永遠不命中。"""
    criteria = make_criteria([9500, ANY, 1], logic="OR", min_match=3, slot_count=3)
    result = match_slots([9600, 0, 1], criteria)
    assert result.matched
    assert "符合 2/2 格（需 ≥2）" in result.reasons[-1]


def test_auto_mode_honours_detected_width_argument():
    criteria = make_criteria([900, 900, 900, ANY], slot_count=0)
    # 偵測為 4 格，但資料只有 3 格 → 自動模式仍以資料為準
    assert match_slots([950, 950, 950], criteria, 4).matched


def test_manual_mode_rejects_width_mismatch():
    """手動指定格數與資料不符 → 不命中，並在理由中說明是什麼不符。

    兩種方向都要擋：資料格數比條件少（不完整）和比條件多（拿錯門檻）。
    """
    short = make_criteria([9000, 900, 1], slot_count=4)
    result = match_slots([9600, 960, 1], short)
    assert not result.matched
    assert any("格數與條件不符" in r for r in result.reasons)
    assert any("星格數為 3 格，條件為 4 格" in r for r in result.reasons)

    long = make_criteria([9000, 900, 1], slot_count=3)
    result = match_slots([9600, 960, 910, 1], long)
    assert not result.matched
    assert any("星格數為 4 格，條件為 3 格" in r for r in result.reasons)


# ── ANY(-1)：不關心 ───────────────────────────────────────────
def test_any_last_slot_is_not_checked():
    criteria = make_criteria([9000, 900, ANY])
    assert match_slots([9600, 960, 0], criteria).matched
    assert match_slots([9600, 960, 1], criteria).matched


def test_any_middle_slot_is_skipped():
    criteria = make_criteria([9000, ANY, 1])
    assert match_slots([9600, 0, 1], criteria).matched
    result = match_slots([9600, 777, 1], criteria)
    assert result.matched
    assert any("第2格 777 不限" in r for r in result.reasons)


def test_all_any_slots_means_no_star_filter():
    """全部格填 -1 代表尚未設定星格門檻 → 視為通過，讓寬鬆預設能先看到東西。"""
    result = match_slots([1, 2, 3], Criteria.loose())
    assert result.matched
    assert any("未設定星格門檻" in r for r in result.reasons)


# ── 不限價 ────────────────────────────────────────────────────
def test_zero_price_cap_means_unlimited():
    criteria = make_criteria([9000, 900, 1])
    criteria.max_price_ntd = 0.0
    cheap = Listing("h", "S1", "9650-950-1", [9650, 950, 1], 1.0)
    assert evaluate_listing(cheap, criteria).matched
    assert "不限" in evaluate_listing(cheap, criteria).reasons[0]
