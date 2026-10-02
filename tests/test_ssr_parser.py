"""SSR 市場頁解析測試。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from naraka.ssr_parser import (
    SSRParseError,
    extract_render_context,
    parse_bbcode_fields,
    parse_listings_json,
    parse_ssr_page,
    unescape_js_string,
)
from make_fixture import build_html

HASH = "Star - Shadow Scent(Non-CN)"
FIXTURE = Path(__file__).parent / "fixtures" / "ssr_page.html"


# ── JS 字串還原 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        (r"a\nb", "a\nb"),
        (r"a\\b", "a\\b"),
        (r"quote\"x", 'quote"x'),
        (r"\u4e9e\u6d32", "\u4e9e\u6d32"),
        (r"\x41", "A"),
        (r"\/", "/"),
        (r"keep\q", "keepq"),
    ],
)
def test_unescape_js_string(raw, expected):
    assert unescape_js_string(raw) == expected


def test_unescape_brace_unicode_escape():
    assert unescape_js_string(r"a\u{1F600}b") == "a\U0001F600b"


# ── 主要流程 ──────────────────────────────────────────────────
def test_parse_ssr_page_returns_listings():
    result = parse_ssr_page(build_html(), HASH)
    assert result.source == "ssr"
    assert result.total_count == 59  # 頁面回報的是全體掛單數，不是本頁筆數
    assert len(result.listings) == 4  # 全零星格那筆被略過

    first = result.listings[0]
    assert first.listing_number == "S10000001"
    assert first.constellation_raw == "9650-950-1"
    assert first.slots == [9650, 950, 1]
    assert first.price_ntd == 9650.0
    assert first.star_stats == 420
    assert first.available_server == "亞洲"
    assert first.hash_name == HASH
    assert first.url.endswith("/market/listings/1203220/Star%20-%20Shadow%20Scent%28Non-CN%29")


def test_parse_ssr_page_reads_four_slot_constellation():
    listings = parse_ssr_page(build_html(), HASH).listings
    assert listings[3].slots == [9650, 950, 880, 1]
    assert listings[1].slots == [9999, 999, 1]
    assert listings[1].price_ntd == 12000.0
    assert listings[2].slots == [1200, 300, 0]


def test_static_fixture_matches_builder():
    if not FIXTURE.exists():
        pytest.skip("尚未產生靜態 fixture，請執行 python tests/make_fixture.py")
    from_file = parse_ssr_page(FIXTURE.read_text(encoding="utf-8"), HASH)
    from_builder = parse_ssr_page(build_html(), HASH)
    assert [x.slots for x in from_file.listings] == [x.slots for x in from_builder.listings]
    assert [x.listing_number for x in from_file.listings] == [
        x.listing_number for x in from_builder.listings
    ]
    assert [x.price_ntd for x in from_file.listings] == [
        x.price_ntd for x in from_builder.listings
    ]


def test_missing_marker_raises():
    with pytest.raises(SSRParseError):
        parse_ssr_page("<html><body>login required</body></html>", HASH)


def test_truncated_literal_raises():
    with pytest.raises(SSRParseError):
        extract_render_context('window.SSR.renderContext=JSON.parse("{\\"a\\":1}')


def test_single_quoted_literal_supported():
    # 真實頁面若用單引號包住 JSON.parse 的參數，內層 JSON 的雙引號不會被跳脫
    inner = json.dumps({"queries": []})
    outer = '{"queryData":' + json.dumps(inner) + "}"
    html = "<script>window.SSR.renderContext=JSON.parse('" + outer + "');</script>"
    assert extract_render_context(html)["queryData"] == inner


def test_unescape_keeps_double_escape_inside_single_quotes():
    assert unescape_js_string(r'{\"a\":1}', quote="'") == r'{\"a\":1}'
    assert unescape_js_string(r"{\"a\":1}") == '{"a":1}'
    assert unescape_js_string(r"\'x\'", quote="'") == "'x'"


# ── 舊版 JSON 端點（備援）────────────────────────────────────
def test_parse_listings_json_fallback():
    payload = {
        "success": True,
        "start": 0,
        "pagesize": 10,
        "total_count": 1,
        "assets": {
            "999": {
                "market_hash_name": HASH,
                "description": (
                    "[img]x[/img]\nNumber: S777\nConstellation: 950-950-900-1\n"
                    "Star Stats: 1\nAvailable server: Asia\n"
                ),
            }
        },
        "listinginfo": {
            "12345": {
                "listingid": "12345",
                "converted_price": 950000,
                "asset": {"id": "999", "contextid": "2", "amount": "1"},
            }
        },
    }
    result = parse_listings_json(payload, HASH)
    assert result.source == "json"
    assert result.total_count == 1
    listing = result.listings[0]
    assert listing.listing_number == "S777"
    assert listing.slots == [950, 950, 900, 1]
    assert listing.price_ntd == 9500.0


# ── bbcode 欄位 ───────────────────────────────────────────────
def test_parse_bbcode_fields_strips_tags():
    fields = parse_bbcode_fields(
        "[img]http://a/b.png[/img]<br/>Number: S1\n"
        "Constellation: 1-2-1\nStar Stats: 7\nAvailable server: Asia\n"
    )
    assert fields["number"] == "S1"
    assert fields["constellation"] == "1-2-1"
    assert fields["starstats"] == "7"
    assert fields["availableserver"] == "Asia"


def test_parse_bbcode_fields_handles_real_chinese_labels():
    """實測 Steam 繁中頁面回傳的就是這組欄位（伺服器那行是全形冒號）。"""
    fields = parse_bbcode_fields(
        "[img]https://av.akamai.steamstatic.com/x.png[/img]\n"
        "編號:   S000559 \n"
        "星格:   5310-000-0\n"
        "謫星數據:   15414\n"
        "適用服務器：非國服"
    )
    assert fields["number"] == "S000559"
    assert fields["constellation"] == "5310-000-0"
    assert fields["starstats"] == "15414"
    assert fields["availableserver"] == "非國服"


def test_parse_bbcode_fields_ignores_unknown_labels():
    fields = parse_bbcode_fields("Some random: value\nName: 影子\n")
    assert fields == {}


def test_listing_from_real_chinese_payload():
    """端到端：中文 bbcode + 全形冒號 → Listing（價格取 strSubtotal）。"""
    payload = {
        "queryData": json.dumps(
            {
                "queries": [
                    {
                        "state": {
                            "data": {
                                "pages": [
                                    {
                                        "total_count": 2,
                                        "listings": [
                                            {
                                                "unPrice": 350600,
                                                "unFee": 52600,
                                                "eCurrency": 30,
                                                "strSubtotal": "$4,032.00",
                                                "description": {
                                                    "descriptions": [
                                                        {
                                                            "type": "bbcode",
                                                            "value": (
                                                                "[img]x.png[/img]\n編號:   S000190 \n"
                                                                "星格:   5310-000-0\n謫星數據:   15414\n"
                                                                "適用服務器：非國服"
                                                            ),
                                                        }
                                                    ]
                                                },
                                            },
                                            # 全零星格 → 非有效商品，必須略過
                                            {
                                                "unPrice": 350600,
                                                "strSubtotal": "$4,032.00",
                                                "description": {
                                                    "descriptions": [
                                                        {
                                                            "type": "bbcode",
                                                            "value": (
                                                                "編號:   S000559 \n星格:   0000-000-0\n"
                                                                "謫星數據:   0\n適用服務器：非國服"
                                                            ),
                                                        }
                                                    ]
                                                },
                                            },
                                        ],
                                    }
                                ]
                            }
                        }
                    }
                ]
            }
        )
    }
    html = "<script>window.SSR.renderContext=JSON.parse(" + json.dumps(json.dumps(payload)) + ");</script>"
    result = parse_ssr_page(html, HASH)
    assert len(result.listings) == 1  # 全零那筆被丟掉
    listing = result.listings[0]
    assert listing.listing_number == "S000190"
    assert listing.slots == [5310, 0, 0]
    assert listing.price_ntd == 4032.0
    assert listing.star_stats == 15414
    assert listing.available_server == "非國服"


def test_price_falls_back_to_un_price():
    """沒有 strSubtotal 時退回 unPrice / 100（最小貨幣單位）。"""
    payload = {
        "queryData": json.dumps(
            {
                "queries": [
                    {
                        "state": {
                            "data": {
                                "pages": [
                                    {
                                        "total_count": 1,
                                        "listings": [
                                            {
                                                "unPrice": 350600,
                                                "eCurrency": 30,
                                                "description": {
                                                    "descriptions": [
                                                        {
                                                            "type": "bbcode",
                                                            "value": "編號: S1\n星格: 1-2-1",
                                                        }
                                                    ]
                                                },
                                            }
                                        ],
                                    }
                                ]
                            }
                        }
                    }
                ]
            }
        )
    }
    html = "<script>window.SSR.renderContext=JSON.parse(" + json.dumps(json.dumps(payload)) + ");</script>"
    assert parse_ssr_page(html, HASH).listings[0].price_ntd == 3506.0


def test_listing_without_constellation_is_skipped():
    payload = {
        "queryData": json.dumps(
            {
                "queries": [
                    {
                        "state": {
                            "data": {
                                "pages": [
                                    {
                                        "total_count": 1,
                                        "listings": [
                                            {
                                                "strSubtotal": "NT$100",
                                                "description": {
                                                    "descriptions": [
                                                        {"type": "bbcode", "value": "Number: S1"}
                                                    ]
                                                },
                                            }
                                        ],
                                    }
                                ]
                            }
                        }
                    }
                ]
            }
        )
    }
    html = "<script>window.SSR.renderContext=JSON.parse(" + json.dumps(json.dumps(payload)) + ");</script>"
    assert parse_ssr_page(html, HASH).listings == []
