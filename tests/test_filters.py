"""星格條件判定測試。

判定規則（見 naraka/filters.py）：

* 最後一格是 0/1 二元位，**絕對匹配** —— 精確比對且失敗即不命中，
  不受 AND/OR 的「最少符合格數」稀釋。留空（``None``）代表不關心。
* 中間格走 AND/OR。3 格物品是第1、2 格，4 格物品是第1、2、3 格。
* 格數由使用者手動指定（3 或 4），資料寬度不符直接不命中。
"""

from __future__ import annotations

import pytest

from naraka.filters import evaluate_listing, match_slots
from naraka.models import Criteria, Listing, parse_constellation


def make_criteria(slot_min, slot_max=(), logic="OR", min_match=1, slot_count=None):
    criteria = Criteria(
        max_price_ntd=10000.0,
        slot_count=slot_count if slot_count is not None else len(slot_min),
        slot_min=list(slot_min),
        slot_max=list(slot_max),
        logic=logic,
        min_match=min_match,
    )
    criteria.normalise()
    return criteria


# ── 星格字串解析 ───────────────────────────────────────────────
def test_parse_3slot():
    assert parse_constellation("9650-950-1") == [9650, 950, 1]


def test_parse_4slot():
    assert parse_constellation("950-950-900-1") == [950, 950, 900, 1]


def test_parse_invalid_part_becomes_zero():
    assert parse_constellation("abc-12-x") == [0, 12, 0]


# ── 最後一格：絕對匹配 ─────────────────────────────────────────
def test_last_slot_is_absolute_under_and():
    criteria = make_criteria([9500, 950, 1], logic="AND")
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9600, 960, 0], criteria).matched


def test_last_slot_is_absolute_under_or():
    """末格失敗不能被其他格補到 min_match 就放行。

    這是末格「絕對匹配」的核心：OR 只作用在倒數第二格之前的中間格。
    """
    criteria = make_criteria([9500, 950, 1], logic="OR", min_match=1)
    assert match_slots([9600, 0, 1], criteria).matched
    # 第1格符合、末格不符 → 仍然不命中
    assert not match_slots([9600, 0, 0], criteria).matched


def test_last_slot_is_absolute_four_slot_or():
    """實境：Novaburst（4 格、OR、最少 3 格）末格 0 必須被擋下。"""
    criteria = make_criteria([950, 950, 900, 1], logic="OR", min_match=3)
    assert match_slots([960, 960, 910, 1], criteria).matched
    # 前三格全中，只差末格 → 不命中
    assert not match_slots([960, 960, 910, 0], criteria).matched


def test_last_slot_zero_is_a_real_threshold():
    """末格填 0 是真實門檻（要求資料末格為 0），不是「未設定」。"""
    criteria = make_criteria([9500, 950, 0], logic="AND")
    assert match_slots([9600, 960, 0], criteria).matched
    assert not match_slots([9600, 960, 1], criteria).matched


def test_last_slot_ignores_slot_max():
    """末格只認精確值；填了上限也不會變成區間比對。"""
    criteria = make_criteria([9500, 950, 1], [9999, 999, 99], logic="AND")
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9600, 960, 0], criteria).matched


def test_last_slot_none_is_not_mandatory():
    """末格留空 = 不關心，此時不強制，且不列入中間格計數。"""
    criteria = make_criteria([9500, 950, None], logic="AND")
    assert match_slots([9600, 960, 0], criteria).matched
    assert match_slots([9600, 960, 1], criteria).matched


def test_last_slot_failure_reason_is_explicit():
    criteria = make_criteria([9500, 950, 1], logic="OR", min_match=1)
    result = match_slots([9600, 960, 0], criteria)
    assert not result.matched
    assert any("第3格 0 == 1（必中）" in r for r in result.reasons)
    assert "末格未達絕對匹配，整筆不命中 ✗" in result.reasons


# ── 中間格：AND / OR ───────────────────────────────────────────
def test_and_logic():
    criteria = make_criteria([9500, 950, 1], logic="AND")
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9400, 960, 1], criteria).matched
    assert not match_slots([9600, 960, 0], criteria).matched

    criteria4 = make_criteria([950, 950, 900, 1], logic="AND")
    assert match_slots([960, 960, 910, 1], criteria4).matched
    assert not match_slots([960, 960, 890, 1], criteria4).matched


def test_or_logic():
    """OR 只在倒數第二格之前的中間格生效。"""
    criteria = make_criteria([9500, 950, 1], logic="OR", min_match=1)
    # 中間格只中第1格，但 min_match=1 → 命中
    assert match_slots([9600, 800, 1], criteria).matched
    # 末格不符 → 不能被中間格的命中補回來
    assert not match_slots([9600, 800, 0], criteria).matched

    criteria4 = make_criteria([950, 950, 900, 1], logic="OR", min_match=2)
    # 中間格 2/3 命中（第3格 800 < 900 不符），達到 min_match=2
    assert match_slots([960, 960, 800, 1], criteria4).matched
    assert not match_slots([960, 960, 800, 0], criteria4).matched
    # 中間格只有 1/3 命中 → 不足
    assert not match_slots([960, 800, 800, 1], criteria4).matched


def test_or_min_match_counts_middle_slots_only():
    """4 格 OR、最少 3 格：3 格全中 + 末格中才命中（等於四格全中）。"""
    criteria = make_criteria([950, 950, 900, 1], logic="OR", min_match=3)
    assert match_slots([960, 960, 910, 1], criteria).matched
    assert not match_slots([960, 960, 890, 1], criteria).matched


def test_range_logic():
    criteria = make_criteria([9000, 900, 1], [9700, 970, 0], logic="OR", min_match=2)
    assert match_slots([9600, 960, 1], criteria).matched
    assert not match_slots([9800, 960, 1], criteria).matched


def test_range_zero_means_unbounded():
    criteria = make_criteria([9500, 0, 1], [0, 0, 0], logic="OR", min_match=1)
    assert match_slots([9650, 10, 1], criteria).matched


# ── 格數不符 ───────────────────────────────────────────────────
def test_slot_count_mismatch_both_directions():
    """指定格數與資料不符 → 不命中，並說明是什麼不符。"""
    short = make_criteria([9000, 900, 1], slot_count=4)
    result = match_slots([9600, 960, 1], short)
    assert not result.matched
    assert any("星格數為 3 格，條件為 4 格" in r for r in result.reasons)
    assert "格數與條件不符，略過比對 ✗" in result.reasons

    long = make_criteria([9000, 900, 1], slot_count=3)
    result = match_slots([9600, 960, 910, 1], long)
    assert not result.matched
    assert any("星格數為 4 格，條件為 3 格" in r for r in result.reasons)


def test_slot_count_mismatch_blocks_even_when_values_would_match():
    """寬度不符時整筆擋下，不會因為數值碰巧符合就放行。"""
    criteria = make_criteria([9600, 960, 910, 1], logic="AND", slot_count=4)
    assert match_slots([9600, 960, 910, 1], criteria).matched
    # 同一組數字但只有 3 格 → 必須擋下
    assert not match_slots([9600, 960, 910], criteria).matched


def test_empty_slots_never_matches():
    result = match_slots([], make_criteria([9500, 950, 1]))
    assert not result.matched
    assert "星格資料解析失敗 ✗" in result.reasons


# ── 不關心（None）──────────────────────────────────────────────
def test_none_middle_slot_is_skipped():
    criteria = make_criteria([9000, None, 1], logic="AND")
    assert match_slots([9600, 0, 1], criteria).matched
    result = match_slots([9600, 777, 1], criteria)
    assert result.matched
    assert any("第2格 777 不限" in r for r in result.reasons)


def test_all_none_slots_means_no_star_filter():
    """全部格留空代表尚未設定星格門檻 → 視為通過，讓寬鬆預設能先看到東西。

    寬度仍需與設定相符（``loose()`` 預設 4 格），因為「未設定門檻」不等於
    「接受任何寬度」—— 那會讓格數警示失效。
    """
    result = match_slots([1, 2, 3, 4], Criteria.loose())
    assert result.matched
    assert any("未設定星格門檻" in r for r in result.reasons)


def test_all_none_slots_still_respects_slot_count():
    result = match_slots([1, 2, 3], Criteria.loose())
    assert not result.matched
    assert "格數與條件不符，略過比對 ✗" in result.reasons


def test_middle_all_none_with_last_failure_is_not_matched():
    """中間格全留空、只要求末格時，末格不通過就是不通過。

    迴圈裡 checked_all 包含末格，所以不會被「全部未設定」的提早返回放行。
    """
    criteria = make_criteria([None, None, 1], logic="AND")
    assert match_slots([9600, 960, 1], criteria).matched
    result = match_slots([9600, 960, 0], criteria)
    assert not result.matched
    assert any("（必中）" in r for r in result.reasons)


def test_middle_all_none_with_last_passed_is_matched():
    criteria = make_criteria([None, None, None, 1], logic="AND", slot_count=4)
    result = match_slots([9600, 960, 910, 1], criteria)
    assert result.matched
    assert any("中間格未設門檻，僅檢查末格" in r for r in result.reasons)


def test_min_match_clamped_to_checked_middle_slots():
    """有中間格留空時，OR 的 min_match 要夾到實際檢查的中間格數。"""
    criteria = make_criteria([9500, None, 1], logic="OR", min_match=2)
    result = match_slots([9600, 0, 1], criteria)
    assert result.matched
    assert any("中間格符合 1/1 格（需 ≥1）" in r for r in result.reasons)


# ── 價格 + 星格一起判定 ───────────────────────────────────────
def test_evaluate_listing_price_and_slots():
    criteria = make_criteria([9500, 950, 1], logic="AND")
    criteria.max_price_ntd = 10000.0

    ok = Listing("h", "S1", "9650-950-1", [9650, 950, 1], 9650.0)
    assert evaluate_listing(ok, criteria).matched

    expensive = Listing("h", "S2", "9650-950-1", [9650, 950, 1], 12000.0)
    result = evaluate_listing(expensive, criteria)
    assert not result.matched
    assert result.reasons[0].startswith("價格")


def test_reasons_are_readable():
    criteria = make_criteria([9500, 950, 1], logic="OR", min_match=2)
    listing = Listing("h", "S1", "9650-950-0", [9650, 950, 0], 9000.0)
    text = evaluate_listing(listing, criteria).reason_text()
    assert "第1格 9650 ≥ 9500 ✓" in text
    assert "第2格 950 ≥ 950 ✓" in text
    assert "第3格 0 == 1（必中）✗" in text
    assert "末格未達絕對匹配，整筆不命中 ✗" in text


def test_zero_price_cap_means_unlimited():
    criteria = make_criteria([9500, 950, 1], logic="AND")
    criteria.max_price_ntd = 0.0
    cheap = Listing("h", "S1", "9650-950-1", [9650, 950, 1], 1.0)
    assert evaluate_listing(cheap, criteria).matched
    assert "不限" in evaluate_listing(cheap, criteria).reasons[0]


# ── Criteria.normalise ─────────────────────────────────────────
def test_normalise_pads_unset_slots_with_none():
    """補齊的格用 None（不關心）而非 0 —— 末格的 0 會被解讀成
    「精確等於 0」，讓尚未設定的條件看起來像有在生效。"""
    criteria = Criteria(slot_count=4, slot_min=[950, 950], slot_max=[0], logic="xxx", min_match=9)
    criteria.normalise()
    assert criteria.slot_min == [950, 950, None, None]
    assert criteria.slot_max == [0, 0, 0, 0]
    assert criteria.logic == "OR"
    assert criteria.slot_count == 4


def test_normalise_converts_negative_thresholds_to_none():
    """舊版用 -1 表示不關心，讀入後一律轉成 None。"""
    criteria = Criteria(slot_count=3, slot_min=[9500, -1, 1], slot_max=[0, -1, 0])
    criteria.normalise()
    assert criteria.slot_min == [9500, None, 1]
    # 上限沒有「未設定」語意：None 與負值都收斂成 0（不限價）
    assert criteria.slot_max == [0, 0, 0]


def test_normalise_rejects_invalid_slot_count():
    """格數只接受 3 / 4；舊版的 0（自動）與亂值都退回 4。"""
    for bad in (0, 7, -1):
        criteria = Criteria(slot_count=bad)
        criteria.normalise()
        assert criteria.slot_count == 4

    ok = Criteria(slot_count=3)
    ok.normalise()
    assert ok.slot_count == 3


def test_normalise_clamps_min_match_to_middle_slots():
    """最少符合格數只針對中間格，上限是格數減一。"""
    three = Criteria(slot_count=3, slot_min=[None] * 3, min_match=3)
    three.normalise()
    assert three.mid_slot_count == 2
    assert three.min_match == 2

    four = Criteria(slot_count=4, slot_min=[None] * 4, min_match=9)
    four.normalise()
    assert four.mid_slot_count == 3
    assert four.min_match == 3


def test_normalise_truncates_to_slot_count():
    criteria = Criteria(slot_count=3, slot_min=[9000, 900, 900, 900])
    criteria.normalise()
    assert criteria.slot_min == [9000, 900, 900]


def test_slot_min_at_and_slot_max_at():
    criteria = Criteria(slot_count=3, slot_min=[9500, None, 1], slot_max=[9999, 0, 0])
    criteria.normalise()
    assert criteria.slot_min_at(0) == 9500
    assert criteria.slot_min_at(1) is None
    assert criteria.slot_min_at(2) == 1
    assert criteria.slot_min_at(9) is None
    assert criteria.slot_max_at(0) == 9999
    assert criteria.slot_max_at(1) == 0
    assert criteria.slot_max_at(9) == 0


# ── 設定檔遷移 ─────────────────────────────────────────────────
def test_from_dict_migrates_negative_thresholds():
    """舊設定檔裡的 -1 讀進來變成 None，行為與舊版的「不關心」一致。"""
    criteria = Criteria.from_dict(
        {"slot_count": 4, "slot_min": [5000, 500, -1, -1], "slot_max": [0, 0, 0, 0]}
    )
    assert criteria.slot_min == [5000, 500, None, None]


def test_from_dict_migrates_auto_slot_count():
    """舊版的 0（自動偵測）已移除，一律退回 4 格。"""
    assert Criteria.from_dict({"slot_count": 0}).slot_count == 4
    assert Criteria.from_dict({}).slot_count == 4


# ── 值域提示 ───────────────────────────────────────────────────
@pytest.mark.parametrize("count", [3, 4])
def test_slot_range_hint(count):
    """值域提示：前面各格是範圍，最後一格是 0/1 二元位（精確比對或不限）。"""
    blank = Criteria(slot_count=count, slot_min=[None] * 4)
    blank.normalise()
    assert blank.slot_range_hint(0) == "值域 0~9999"
    # 非最後一格一律是 0~9999 的完整範圍
    for mid in range(1, count - 1):
        assert blank.slot_range_hint(mid) == "值域 0~9999"
    last = count - 1
    assert blank.slot_range_hint(last) == "不限"

    # 有填目標值時才顯示為精確比對
    mins = [9500, 950, 900, 1][:count]
    mins[-1] = 1
    filled = Criteria(slot_count=count, slot_min=list(mins))
    filled.normalise()
    assert filled.slot_range_hint(last) == "精確值 0 或 1"
