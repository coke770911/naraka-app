"""條件篩選：每個物品各自一份條件（價格上限 + 逐格星格門檻）。

星格值域（依實際商品）::

    3 格: slot1 0~9999   slot2 0~9999   slot3 0/1（絕對匹配）
    4 格: slot1 0~9999   slot2 0~9999   slot3 0~9999   slot4 0/1（絕對匹配）

判定分成兩塊：

* **最後一格是絕對匹配** —— 只認 ``0/1`` 二元位、只做精確等於比對，而且
  失敗就是失敗，不會被 OR 的「最少符合格數」稀釋掉。留空（``None``）代表
  不關心，此時不列入判定。
* **中間格走 AND/OR** —— 3 格物品是第1、2 格，4 格物品是第1、2、3 格。
  AND 是全部符合；OR 是符合數 >= ``min_match``。

各商品的星格數與價格區間差異很大（實測 Shadow Scent 3 格 / 約 NT$4,000，
Novaburst 4 格 / 約 NT$47,000），所以條件掛在物品上而不是全域共用一份。
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
    """依每格門檻比對星格，回傳結果與逐格說明。

    最後一格與中間格分開計數：末格只做精確比對且具強制力，中間格才受
    ``criteria.logic`` 與 ``min_match`` 影響。
    """
    reasons: List[str] = []
    total = criteria.slot_count

    if not slots:
        reasons.append(f"星格資料解析失敗 {NG}")
        return MatchResult(False, reasons)

    if len(slots) != total:
        # 格數是使用者手動指定的，代表只想監測這種寬度的資料。寬度不符時
        # 寧可漏報也不比 —— 拿 3 格門檻去套 4 格資料的第3格，會得到看似
        # 合理其實比錯位置的結果。
        reasons.append(f"⚠ 星格數為 {len(slots)} 格，條件為 {total} 格")
        reasons.append(f"格數與條件不符，略過比對 {NG}")
        return MatchResult(False, reasons)

    checked_all = 0
    checked_mid = 0
    hits_mid = 0
    last_checked = False
    last_ok = False

    for index in range(total):
        value = slots[index] if index < len(slots) else 0
        is_last = index == total - 1
        low = criteria.slot_min_at(index)

        if low is None:
            reasons.append(f"第{index + 1}格 {value} 不限")
            continue

        checked_all += 1

        if is_last:
            # 最後一格是 0/1 二元位：精確比對，且失敗即不命中。
            ok = value == low
            last_checked = True
            last_ok = ok
            reasons.append(f"第{index + 1}格 {value} == {low}（必中）{OK if ok else NG}")
            continue

        checked_mid += 1
        high = criteria.slot_max_at(index)
        if high > 0:
            ok = low <= value <= high
            reasons.append(f"第{index + 1}格 {value} ∈ [{low},{high}] {OK if ok else NG}")
        else:
            ok = value >= low
            reasons.append(f"第{index + 1}格 {value} ≥ {low} {OK if ok else NG}")
        if ok:
            hits_mid += 1

    if checked_all == 0:
        # 每一格都留空＝ 使用者尚未設定星格門檻。
        # 此時不視為未命中，否則「寬鬆預設」會讓新加入的物品永遠掃不到東西。
        reasons.append(f"未設定星格門檻，全部 {total} 格略過 {OK}")
        return MatchResult(True, reasons)

    if last_checked and not last_ok:
        # 末格是絕對匹配，不論中間格符合幾格都不放行。
        reasons.append(f"末格未達絕對匹配，整筆不命中 {NG}")
        return MatchResult(False, reasons)

    if criteria.logic == "AND":
        required = checked_mid
    else:
        # 中間格有留空（不關心）時，實際檢查的格數會少於使用者填的 min_match；
        # 此時要夾到 checked_mid，否則 OR 條件永遠不可能滿足。
        required = min(criteria.effective_min_match, checked_mid)

    if checked_mid == 0:
        reasons.append(f"中間格未設門檻，僅檢查末格 {OK}")
        return MatchResult(True, reasons)

    matched = hits_mid >= required
    reasons.append(f"中間格符合 {hits_mid}/{checked_mid} 格（需 ≥{required}）{OK if matched else NG}")
    return MatchResult(matched, reasons)


def evaluate_listing(listing: Listing, criteria: Criteria) -> MatchResult:
    """價格上限 + 星格條件同時成立才算命中。"""
    slot_result = match_slots(listing.slots, criteria)
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
