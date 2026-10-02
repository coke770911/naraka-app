"""條件篩選：每個物品各自一份條件（價格上限 + 逐格星格門檻）。

星格值域（依實際商品）::

    3 格: slot1 0~9999   slot2 0~9999   slot3 0~1
    4 格: slot1 0~9999   slot2 0~9999   slot3 0~9999   slot4 0~1

最後一格是 0/1 的二元位，因此一律採「精確等於」比對；填 :data:`~naraka.models.ANY`
(-1) 則代表不關心。

各商品的星格數與價格區間差異很大（實測 Shadow Scent 3 格 / 約 NT$4,000，
Novaburst 4 格 / 約 NT$47,000），所以條件掛在物品上而不是全域共用一份。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .models import ANY, Criteria, Listing

OK = "✓"  # ✓
NG = "✗"  # ✗


@dataclass
class MatchResult:
    matched: bool
    reasons: List[str] = field(default_factory=list)

    def reason_text(self, sep: str = "  ") -> str:
        return sep.join(self.reasons)


def match_slots(
    slots: List[int],
    criteria: Criteria,
    detected_slot_count: int = 0,
) -> MatchResult:
    """依每格門檻比對星格，回傳結果與逐格說明。

    ``detected_slot_count`` 是實際抓到的格數；條件設為自動（``slot_count == 0``）
    時會用它決定要比較幾格，因此 3 格與 4 格商品可以各用各的條件。
    """
    reasons: List[str] = []
    slot_min = criteria.slot_min
    slot_max = criteria.slot_max
    if not slots:
        reasons.append(f"星格資料解析失敗 {NG}")
        return MatchResult(False, reasons)

    if criteria.slot_count:
        total = criteria.slot_count
        if len(slots) != total:
            # 手動指定格數代表使用者只想監測這種寬度的資料。寬度不符時寧可
            # 漏報也不比 —— 拿 3 格門檻去套 4 格資料的第3格，會得到看似合理
            # 其實比錯位置的結果。
            reasons.append(f"⚠ 星格數為 {len(slots)} 格，條件為 {total} 格")
            reasons.append(f"格數與條件不符，略過比對 {NG}")
            return MatchResult(False, reasons)
    else:
        # 自動模式：以每筆資料自己的格數為準，3 格與 4 格商品共用同一組條件
        total = len(slots)

    hits = 0
    checked = 0
    for i in range(total):
        value = slots[i] if i < len(slots) else 0
        low = slot_min[i] if i < len(slot_min) else ANY
        high = slot_max[i] if i < len(slot_max) else 0
        is_last = i == total - 1

        if low == ANY:
            reasons.append(f"第{i + 1}格 {value} 不限")
            continue

        checked += 1
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

    if checked == 0:
        # 每一格都填 -1（不關心）＝ 使用者尚未設定星格門檻。
        # 此時不視為未命中，否則「寬鬆預設」會讓新加入的物品永遠掃不到東西。
        reasons.append(f"未設定星格門檻，全部 {total} 格略過 {OK}")
        return MatchResult(True, reasons)

    if criteria.logic == "AND":
        required = checked
    else:
        # 有格被設成 -1（不關心）時，實際檢查的格數會少於使用者填的 min_match；
        # 此時要夾到 checked，否則 OR 條件永遠不可能滿足。
        required = min(criteria.effective_min_match, checked)
    matched = hits >= required
    reasons.append(f"符合 {hits}/{checked} 格（需 ≥{required}）{OK if matched else NG}")
    return MatchResult(matched, reasons)


def evaluate_listing(
    listing: Listing,
    criteria: Criteria,
    detected_slot_count: int = 0,
) -> MatchResult:
    """價格上限 + 星格條件同時成立才算命中。"""
    slot_result = match_slots(listing.slots, criteria, detected_slot_count)
    if criteria.unlimited_price:
        price_note = f"價格 {listing.price_text} 不限 {OK}"
        price_ok = True
    else:
        price_ok = listing.price_ntd <= criteria.max_price_ntd
        price_note = (
            f"價格 {listing.price_text} ≤ 上限 {criteria.price_summary} "
            f"{OK if price_ok else NG}"
        )
    return MatchResult(price_ok and slot_result.matched, [price_note] + slot_result.reasons)
