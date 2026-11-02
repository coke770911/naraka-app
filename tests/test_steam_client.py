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


def test_request_gap_applies_between_back_to_back_requests(monkeypatch):
    client = SteamClient()
    cfg = AppConfig()
    cfg.crawler.request_gap_sec = 1.5
    client.apply(cfg)
    waits = []
    monkeypatch.setattr(client._stop, "wait", lambda seconds: waits.append(seconds) or False)
    client._session.get = lambda url, **kwargs: type("R", (), {"status_code": 200, "ok": True, "text": ""})()

    client.get_text("https://example.invalid/a")
    client.get_text("https://example.invalid/b")

    assert len(waits) == 1
    assert 1.0 < waits[0] <= 1.5


def test_telegram_connection_error_log_redacts_token(monkeypatch):
    import requests

    from naraka import notifiers

    logs = []

    def boom(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(notifiers.requests, "post", boom)
    monkeypatch.setattr(notifiers.requests, "get", boom)
    notifier = notifiers.Notifier(log=lambda level, text: logs.append(text))

    assert notifier.send_telegram("123:SECRET", "42", "hi") is False
    assert notifier.fetch_chat_ids("123:SECRET") == []
    assert len(logs) == 2
    assert all("SECRET" not in text and "/bot***/" in text for text in logs)
