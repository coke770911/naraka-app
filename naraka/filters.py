"""條件篩選：單一價格上限 + 逐格星格門檻（最後一格為精確值）。

星格值域（依實際商品）::

    3 格: slot1 0~9999   slot2 0~999   slot3 0~1
    4 格: slot1 0~9999   slot2 0~999   slot3 0~999   slot4 0~1

最後一格是 0/1 的二元位，因此一律採「精確等於」比對。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .models import Criteria, Listing

OK = "✓"  # ✓
NG = "✗"  # ✗


@dataclass
class MatchResult:
    matched: bool
    reasons: List[str] = field(default_factory=list)

    def reason_text(self, sep: str = "  ") -> str:
        return sep.join(self.reasons)


def match_slots(slots: List[int], criteria: Criteria) -> MatchResult:
    """依每格門檻比對星格，回傳結果與逐格說明。"""
    reasons: List[str] = []
    slot_min = criteria.slot_min
    slot_max = criteria.slot_max
    if not slot_min:
        return MatchResult(False, ["未設定星格條件"])

    total = min(criteria.slot_count, len(slot_min))
    if not slots:
        reasons.append(f"星格資料解析失敗（預期 {total} 格）{NG}")
        return MatchResult(False, reasons)

    if len(slots) != total:
        reasons.append(f"⚠ 星格數為 {len(slots)} 格，條件為 {total} 格")

    hits = 0
    for i in range(total):
        value = slots[i] if i < len(slots) else 0
        low = slot_min[i]
        high = slot_max[i] if i < len(slot_max) else 0
        is_last = i == total - 1
        if is_last:
            ok = value == low
            reasons.append(f"第{i + 1}格 {value} == {low} {OK if ok else NG}")
        elif high > 0:
            ok = low <= value <= high
            reasons.append(f"第{i + 1}格 {value} ∈ [{low},{high}] {OK if ok else NG}")
        else:
            ok = value >= low
            reasons.append(f"第{i + 1}格 {value} ≥ {low} {OK if ok else NG}")
        if ok:
            hits += 1

    required = total if criteria.logic == "AND" else criteria.effective_min_match
    matched = hits >= required
    reasons.append(f"符合 {hits}/{total} 格（需 ≥{required}）{OK if matched else NG}")
    return MatchResult(matched, reasons)


def evaluate_listing(listing: Listing, criteria: Criteria) -> MatchResult:
    """價格上限 + 星格條件同時成立才算命中。"""
    slot_result = match_slots(listing.slots, criteria)
    price_ok = listing.price_ntd <= criteria.max_price_ntd
    price_note = (
        f"價格 {listing.price_text} ≤ 上限 NT${criteria.max_price_ntd:,.0f} "
        f"{OK if price_ok else NG}"
    )
    return MatchResult(price_ok and slot_result.matched, [price_note] + slot_result.reasons)
