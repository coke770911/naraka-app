"""Steam 市場頁解析。

優先解析新版（SSR / Next.js）市場頁：

    window.SSR.renderContext=JSON.parse("....")

若失敗則退回舊版 JSON 端點 ``/market/listings/<app>/<hash>/render/``，
其 listing 描述位於 ``assets[*].description``。

兩種來源的星格資訊都來自同一段 bbcode，但欄位名稱會隨 Steam 的語系在地化，
實測繁中回傳的是::

    編號:   S12345678
    星格:   9650-950-1
    謫星數據:   123
    適用服務器：非國服        ← 這行用的是全形冒號

英文語系則回傳 ``Number`` / ``Constellation`` / ``Star Stats`` /
``Available server``。兩種都必須支援。

星格為 ``0000-000-0``（全零）的掛單是市場上真實存在的資料，必須保留並交由
條件比對，不能當成解析失敗丟棄 —— 實測 Shadow Scent 頁面共 56 筆，其中 12 筆
星格全零，丟掉會讓掃描筆數與網頁不一致。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

from .models import Listing, parse_constellation

_SSR_RE = re.compile(r"window\.SSR\.renderContext\s*=\s*JSON\.parse\s*\(")
_TAG_RE = re.compile(r"\[/?[a-zA-Z][^\[\]]*\]|<br\s*/?>|</?[a-zA-Z][^>]*>")
_MONEY_RE = re.compile(r"[-+]?[0-9][0-9,]*(?:\.[0-9]+)?")

#: 描述欄位別名 → 標準名稱（實測繁中回傳中文欄位，英文語系回傳英文欄位）
_FIELD_ALIASES = {
    "number": ("number", "編號", "编号", "序号", "序號"),
    "constellation": ("constellation", "星格", "星座"),
    "starstats": ("starstats", "謫星數據", "谪星数据", "謫星数据"),
    # 注意 Steam 繁中使用的是「服務器」（非「伺服器」）
    "availableserver": (
        "availableserver",
        "適用服務器",
        "适用服务器",
        "可用服務器",
        "可用服务器",
    ),
}


def _norm_label(raw: str) -> str:
    return re.sub(r"\s+", "", raw or "").lower()


_FIELD_LOOKUP = {
    _norm_label(alias): canonical
    for canonical, aliases in _FIELD_ALIASES.items()
    for alias in aliases
}

#: 欄位完全對不上時的最後手段（整段 bbcode 掃描，容忍中英文與全形冒號）
_LEGACY_RE = re.compile(
    r"(?:Number|編號|编号|序号|序號)\s*[:：]\s*(S\d+).*?"
    r"(?:Constellation|星格|星座)\s*[:：]\s*([0-9\-]+)"
    r"(?:.*?(?:Star\s*Stats|謫星數據|謫星数据|谪星数据)\s*[:：]\s*(\d+))?"
    r"(?:.*?(?:Available\s*server|適用服務器|可用服務器|适用服务器|可用服务器)"
    r"\s*[:：]\s*([^\n\r\[\]]+))?",
    re.S,
)

PAGE_SIZE = 20


class SSRParseError(RuntimeError):
    """無法從 HTML 中取出 renderContext。"""


@dataclass
class PageResult:
    listings: List[Listing] = field(default_factory=list)
    total_count: int = 0
    source: str = "ssr"


# ── JS 字串 / JSON ─────────────────────────────────────────────
def unescape_js_string(text: str, quote: str = '"') -> str:
    """把 JS 字串內容（含跳脫序列）還原成真正的字元。

    ``quote`` 為包住此字串的引號字元。差異在於引號跳脫：

    * ``\\"`` 在雙引號字串中是字面 ``"`` → 還原成 ``"``
    * ``\\"`` 在單引號字串中只是字面 ``"``，但它同時是 JSON 的跳脫序列，
      因此必須保留成 ``\\"`` 交給 :func:`json.loads` 處理
    """
    out: List[str] = []
    i, n = 0, len(text)
    simple = {
        "n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v",
        "0": "\0", "\\": "\\", "/": "/", "`": "`",
    }
    while i < n:
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        esc = text[i]
        if esc == quote:            # 跳脫了自己的分隔符 → 字面引號
            out.append(quote)
            i += 1
            continue
        if esc == "'":              # \' 不管在哪種字串裡都是字面單引號
            out.append("'")
            i += 1
            continue
        if esc == '"':              # 非分隔符的 \" → 保留，交給 JSON 解析
            out.append('\\')
            out.append('"')
            i += 1
            continue
        if esc == "u":
            hex_part = text[i + 1 : i + 5]
            if len(hex_part) == 4 and _is_hex(hex_part):
                out.append(chr(int(hex_part, 16)))
                i += 5
                continue
            if i + 1 < n and text[i + 1] == "{":
                end = text.find("}", i + 2)
                if end != -1:
                    try:
                        out.append(chr(int(text[i + 2 : end], 16)))
                        i = end + 1
                        continue
                    except ValueError:
                        pass
            out.append("u")
            i += 1
            continue
        if esc == "x":
            hex_part = text[i + 1 : i + 3]
            if len(hex_part) == 2 and _is_hex(hex_part):
                out.append(chr(int(hex_part, 16)))
                i += 3
                continue
        out.append(simple.get(esc, esc))
        i += 1
    return "".join(out)


def _is_hex(text: str) -> bool:
    return all(c in "0123456789abcdefABCDEF" for c in text)


def extract_render_context(html: str) -> Dict[str, Any]:
    """從市場頁 HTML 取出 ``renderContext`` 物件。"""
    match = _SSR_RE.search(html or "")
    if not match:
        raise SSRParseError("找不到 window.SSR.renderContext（Steam 可能改版或要求登入）")

    idx = match.end()
    while idx < len(html) and html[idx] in " \t\r\n":
        idx += 1
    if idx >= len(html) or html[idx] not in "\"'":
        raise SSRParseError("renderContext 參數格式異常")

    quote = html[idx]
    idx += 1
    buf: List[str] = []
    while idx < len(html):
        ch = html[idx]
        if ch == "\\" and idx + 1 < len(html):
            buf.append(ch)
            buf.append(html[idx + 1])
            idx += 2
            continue
        if ch == quote:
            break
        buf.append(ch)
        idx += 1
    else:
        raise SSRParseError("renderContext 字串未結尾")

    try:
        context = json.loads(unescape_js_string("".join(buf), quote))
    except ValueError as exc:
        raise SSRParseError(f"renderContext 不是合法 JSON: {exc}") from exc
    if not isinstance(context, dict):
        raise SSRParseError("renderContext 格式異常")
    return context


# ── 頁面解析 ───────────────────────────────────────────────────
def parse_ssr_page(html: str, hash_name: str) -> PageResult:
    """解析 SSR 市場頁，回傳該頁的 listing 與總筆數。"""
    context = extract_render_context(html)
    listings: List[Listing] = []
    total = 0
    for page in _iter_pages(context):
        if isinstance(page.get("total_count"), int):
            total = max(total, page["total_count"])
        for raw in page.get("listings") or []:
            listing = parse_listing_object(raw, hash_name)
            if listing:
                listings.append(listing)

    if not listings:  # 結構改版時的最後手段：遞迴搜尋
        listings = _walk_for_listings(context, hash_name)
    return PageResult(listings=listings, total_count=total, source="ssr")


def parse_listings_json(payload: Dict[str, Any], hash_name: str) -> PageResult:
    """解析舊版 ``/render/`` JSON 端點。"""
    listings: List[Listing] = []
    assets: Dict[str, Any] = payload.get("assets") or {}
    listing_info: Dict[str, Any] = payload.get("listinginfo") or {}

    price_by_asset: Dict[str, float] = {}
    for info in listing_info.values():
        if not isinstance(info, dict):
            continue
        asset = info.get("asset") or {}
        asset_id = str(asset.get("id", ""))
        raw_price = info.get("converted_price", info.get("price"))
        try:
            price_by_asset[asset_id] = float(raw_price) / 100.0
        except (TypeError, ValueError):
            continue

    for asset_id, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        description = asset.get("description") or ""
        listing = _listing_from_description(str(description), hash_name)
        if listing is None:
            continue
        if asset_id in price_by_asset:
            listing.price_ntd = price_by_asset[asset_id]
        elif asset.get("market_actions"):
            listing.price_ntd = _parse_money(
                (asset["market_actions"][0] or {}).get("price")
            )
        listings.append(listing)

    return PageResult(
        listings=listings,
        total_count=int(payload.get("total_count") or 0),
        source="json",
    )


def _iter_pages(context: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    query_data = context.get("queryData")
    if isinstance(query_data, str):
        try:
            query_data = json.loads(query_data)
        except ValueError:
            query_data = None
    if not isinstance(query_data, dict):
        return
    for query in query_data.get("queries") or []:
        if not isinstance(query, dict):
            continue
        data = (query.get("state") or {}).get("data") or {}
        if not isinstance(data, dict):
            continue
        pages = data.get("pages")
        if isinstance(pages, dict):
            pages = [pages]
        for page in pages or []:
            if isinstance(page, dict):
                yield page
        direct = data.get("listings")
        if isinstance(direct, list):
            yield {"listings": direct, "total_count": data.get("total_count", 0)}


def _walk_for_listings(node: Any, hash_name: str, depth: int = 0) -> List[Listing]:
    """已知路徑失效時，遞迴找出所有看起來像 listing 的物件。"""
    if depth > 8:
        return []
    found: List[Listing] = []
    if isinstance(node, dict):
        if "strSubtotal" in node and "description" in node:
            listing = parse_listing_object(node, hash_name)
            if listing:
                found.append(listing)
                return found
        for value in node.values():
            found.extend(_walk_for_listings(value, hash_name, depth + 1))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_for_listings(value, hash_name, depth + 1))
    return found


def parse_listing_object(raw: Any, hash_name: str) -> Optional[Listing]:
    if not isinstance(raw, dict):
        return None
    listing = _listing_from_description(_description_text(raw), hash_name)
    if listing is None:
        return None
    listing.price_ntd = _parse_money(raw.get("strSubtotal")) or _minor_units(raw.get("unPrice"))
    return listing


def _minor_units(value: Any) -> float:
    """``unPrice`` 是以最小貨幣單位表示的價格（cents）。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 100.0
    return 0.0


def _description_text(raw: Dict[str, Any]) -> str:
    description = raw.get("description")
    if isinstance(description, str):
        return description
    if isinstance(description, dict):
        for entry in description.get("descriptions") or []:
            if isinstance(entry, dict) and entry.get("type") == "bbcode":
                value = entry.get("value")
                if isinstance(value, str) and value.strip():
                    return value
    return ""


def _listing_from_description(text: str, hash_name: str) -> Optional[Listing]:
    if not text or not text.strip():
        return None
    fields = parse_bbcode_fields(text)
    constellation = fields.get("constellation", "")
    number = fields.get("number", "")
    star_stats_raw = fields.get("starstats", "")
    server = fields.get("availableserver", "")

    if not constellation:
        match = _LEGACY_RE.search(text)
        if not match:
            return None
        number = number or match.group(1)
        constellation = match.group(2)
        star_stats_raw = star_stats_raw or (match.group(3) or "")
        server = server or (match.group(4) or "")

    constellation = constellation.strip()
    slots = parse_constellation(constellation)
    if not slots:
        # 只有「星格欄位整個缺失／切不開」才丟棄。全零星格（``0000-000-0``）
        # 是市場上真實存在的掛單 —— 實測 Shadow Scent 56 筆裡有 12 筆是全零，
        # 連謫星數據都有值，只是星格尚未鑑定。把全零當解析失敗會讓筆數比
        # 網頁少（44 vs 56），而且使用者把末格設為「不關心」時會變成漏報。
        # 全零交給條件比對即可：末格絕對匹配會自然把它擋掉。
        return None

    return Listing(
        hash_name=hash_name,
        listing_number=(number or "").strip(),
        constellation_raw=constellation,
        slots=slots,
        price_ntd=0.0,
        star_stats=int(star_stats_raw) if star_stats_raw.strip().isdigit() else 0,
        available_server=server.strip(),
    )


def parse_bbcode_fields(text: str) -> Dict[str, str]:
    """把 bbcode 描述轉成 ``{標準欄位: 值}``。

    Steam 市場頁的描述是隨請求語系在地化的，實測繁中會回傳：

        編號:   S000559
        星格:   0000-000-0
        謫星數據:   0
        適用服務器：非國服      ← 注意是全形冒號

    而 ``?language=english`` 參數對 SSR 頁面無效，所以欄位名稱必須同時
    支援中英文，且要把全形冒號正規化後才切得開。
    """
    cleaned = _TAG_RE.sub("\n", text or "")
    cleaned = cleaned.replace("\uff1a", ":").replace("\u3000", " ")  # 全形冒號/空白
    fields: Dict[str, str] = {}
    for line in cleaned.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = _FIELD_LOOKUP.get(_norm_label(key))
        if not key:
            continue
        fields.setdefault(key, value.strip())
    return fields


def _parse_money(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    match = _MONEY_RE.search(value)
    if not match:
        return 0.0
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return 0.0
