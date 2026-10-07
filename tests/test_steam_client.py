"""Steam HTTP 客戶端的請求標頭與安全診斷測試。"""

from __future__ import annotations

import pytest

from naraka.models import AppConfig
from naraka.steam_client import SteamClient, SteamError


class FakeResponse:
    status_code = 200
    ok = True
    reason = "OK"
    headers = {"Content-Type": "text/html; charset=UTF-8"}
    content = b"<html><title>Steam Community :: Error</title><body>captcha</body></html>"
    text = content.decode()

    def json(self):
        raise ValueError("Expecting value")


def test_json_request_uses_ajax_headers_and_logs_safe_html_hint():
    client = SteamClient()
    cfg = AppConfig()
    cfg.crawler.request_gap_sec = 0
    client.apply(cfg)
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    client._session.get = get

    with pytest.raises(SteamError) as caught:
        client.get_json("https://steamcommunity.com/market/listings/1203220/example/render/")

    message = str(caught.value)
    assert "HTTP 200" in message
    assert "Content-Type=text/html" in message
    assert "HTML title「Steam Community :: Error」" in message
    assert "驗證／CAPTCHA 頁" in message
    assert calls[0][1]["headers"]["X-Requested-With"] == "XMLHttpRequest"
    assert calls[0][1]["headers"]["Referer"] == "https://steamcommunity.com/market/"
