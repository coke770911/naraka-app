"""星格條件判定測試（案例沿用 Rust 版 parser.rs 的測試）。"""

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
    assert not match_slots([9600, 960, 1], make_criteria(
        [9000, 900, 1], [9700, 970, 1], logic="OR", min_match=4, slot_count=4
    )).matched


def test_range_zero_means_unbounded():
    criteria = make_criteria([9500, 0, 0], [0, 0, 0], logic="OR", min_match=1)
    assert match_slots([9650, 10, 10], criteria).matched


# ── 格數不一致 / 解析失敗 ─────────────────────────────────────
def test_slot_count_mismatch_is_flagged_but_still_evaluated():
    criteria = make_criteria([9500, 950, 1], logic="AND", min_match=3)
    result = match_slots([9650, 950], criteria)
    assert not result.matched
    assert any("星格數為" in reason for reason in result.reasons)


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
    assert criteria.slot_min == [950, 950, 0, 0]
    assert criteria.slot_max == [0, 0, 0, 0]
    assert criteria.logic == "OR"
    assert criteria.min_match == 4
    assert criteria.slot_count == 4


@pytest.mark.parametrize("count", [3, 4])
def test_slot_range_hint(count):
    criteria = Criteria(slot_count=count)
    criteria.normalise()
    assert criteria.slot_range_hint(0) == "值域 0~9999"
    if count == 3:
        assert criteria.slot_range_hint(2) == "精確值 0 或 1"
    else:
        assert criteria.slot_range_hint(3) == "精確值 0 或 1"
