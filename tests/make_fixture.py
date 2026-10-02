"""產生 tests/fixtures/ssr_page.html。

Steam 市場頁的 SSR 內容若手工撰寫，轉義序列（\\\\" 、\\\\n 、\\\\uXXXX）很容易出錯，
因此以程式組出符合真實結構的樣本，並刻意使用 ensure_ascii=True 讓中文以
\\uXXXX 形式出現，用來驗證解析器的跳脫還原。

欄位名稱、eCurrency、全形冒號、queryKey 形狀皆照實測 Steam 回應撰寫，
確保 fixture 與線上結構同步。重新產生：

    python tests/make_fixture.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

# 實測 Steam 固定回傳 eCurrency=30（NT$），currency 參數會被忽略
E_CURRENCY_TWD = 30


def listing(
    listing_id: str,
    number: str,
    constellation: str,
    subtotal: float,
    stats: int,
    server: str,
) -> dict:
    """組出單筆 listing，欄位形狀比照實測回應。

    ``subtotal`` 是買家實際支付的金額（= unPrice + unFee），Steam 以
    ``strSubtotal`` 給出字串；``$`` 符號是 Steam 自己的格式化結果。
    """
    subtotal_minor = int(round(subtotal * 100))
    fee = int(round(subtotal_minor * 0.15))
    un_price = subtotal_minor - fee
    bbcode = (
        "[img]https://av.akamai.steamstatic.com/x.png[/img]\n"
        f"編號:   {number} \n"
        f"星格:   {constellation}\n"
        f"謫星數據:   {stats}\n"
        f"適用服務器：{server}\n"
    )
    return {
        "listingid": listing_id,
        "unPrice": un_price,
        "unFee": fee,
        "unSteamFee": fee // 3,
        "unPublisherFee": fee - fee // 3,
        "unPricePerUnit": un_price,
        "publisherFeeApp": 1203220,
        "publisherFeePct": 0.10000000149011612,
        "eCurrency": E_CURRENCY_TWD,
        "strSubtotal": f"${subtotal:,.2f}",
        "enhanced_appearances": [],
        "description": {
            "appid": 1203220,
            "classid": "8719103248",
            "instanceid": "0",
            "background_color": "",
            "icon_url": "https://av.akamai.steamstatic.com/x.png",
            "icon_url_large": "https://av.akamai.steamstatic.com/x.png",
            "descriptions": [{"type": "bbcode", "value": bbcode}],
            "tradable": 1,
            "actions": [],
        },
        "bMine": False,
        "asset": {
            "id": listing_id,
            "assetid": listing_id,
            "instanceid": "0",
            "classid": "8719103248",
            "amount": "1",
            "appid": 1203220,
            "contextid": "2",
        },
    }


def build_html() -> str:
    listings = [
        listing("534395503797835129", "S10000001", "9650-950-1", 9650, 420, "亞洲"),
        listing("534395503797835130", "S10000002", "9999-999-1", 12000, 500, "亞洲"),
        listing("534395503797835131", "S10000003", "1200-300-0", 1500, 90, "歐洲"),
        listing("534395503797835132", "S10000004", "9650-950-880-1", 42000, 1200, "歐洲"),
        listing("534395503797835133", "S10000005", "0000-000-0", 3506, 0, "亞洲"),
    ]
    query_data = {
        "queries": [
            {
                "queryKey": ["AOWarningCookie"],
                "state": {"data": {}},
            },
            {
                "queryKey": [
                    "market_item_search",
                    {
                        "appid": 1203220,
                        "strItemName": "Star - Shadow Scent(Non-CN)",
                        "start": 0,
                        "filters": {},
                        "accessoryFilters": {},
                        "propertyFilters": {},
                    },
                ],
                "state": {
                    "data": {
                        "pages": [
                            {
                                "more": True,
                                "start": 0,
                                "total_count": 59,
                                "listings": listings,
                                "facets": [],
                            }
                        ],
                        "pageParams": {"count": 20, "start": 0},
                    }
                },
            },
            {
                "queryKey": ["AssetPropertySchema", 1203220, "tchinese"],
                "state": {"data": {"property_schemas": {}}},
            },
        ]
    }
    context = {
        "localizationSettings": {"locale": "zh-tw", "country": "TW", "currency": 30},
        "queryData": json.dumps(query_data, ensure_ascii=False),
        "cookiePrefs": {},
        "manifest": {"buildId": "naraka-test"},
    }
    # 交給 json.dumps 產生合法的 JS 字串常值（含 \" 、\n 、\uXXXX 轉義）
    literal = json.dumps(json.dumps(context, ensure_ascii=True))
    return (
        "<!DOCTYPE html><html lang=\"zh-tw\"><head><title>Steam 社群市場</title>"
        "</head><body><script>window.SSR = {};</script>"
        f"window.SSR.renderContext=JSON.parse({literal});"
        "</script></body></html>"
    )


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    target = FIXTURES / "ssr_page.html"
    target.write_text(build_html(), encoding="utf-8")
    print(f"wrote {target}")


if __name__ == "__main__":
    main()