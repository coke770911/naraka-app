"""通知：Telegram Bot API + Windows 桌面通知。"""

from __future__ import annotations

import html
import time
from typing import List, Optional

import requests

from .models import ItemEntry, Listing

API_BASE = "https://api.telegram.org"
APP_NAME = "Naraka 星格監控"
REQUEST_TIMEOUT = 15


def format_telegram_message(item: ItemEntry, listing: Listing) -> str:
    label = html.escape(item.display)
    lines = [
        f"🎮 <b>{label}</b> 新上架！",
        "━━━━━━━━━━━━━━━",
        f"星格: <b>{html.escape(listing.slots_text)}</b>",
        f"序號: {html.escape(listing.listing_number or '?')}",
        f"價格: <b>{listing.price_text}</b>",
    ]
    if listing.available_server:
        lines.append(f"伺服器: {html.escape(listing.available_server)}")
    if listing.star_stats:
        lines.append(f"Star Stats: {listing.star_stats}")
    lines.append(f"🔗 <a href=\"{listing.url}\">Steam 市場</a>")
    return "\n".join(lines)


class Notifier:
    """Telegram 在背景執行緒送出；桌面通知由 UI 執行緒呼叫（見 ui/app.py）。"""

    def __init__(self, log=None):
        self._log = log or (lambda level, text: None)

    # ── Telegram ────────────────────────────────────────────────
    def send_telegram(self, token: str, chat_id: str, text: str) -> bool:
        if not (token or "").strip() or not (chat_id or "").strip():
            return False
        url = f"{API_BASE}/bot{token.strip()}/sendMessage"
        payload = {
            "chat_id": chat_id.strip(),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        for attempt in (1, 2):
            try:
                resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as exc:
                self._log("error", f"Telegram 連線失敗：{exc}")
                return False
            if resp.ok:
                return True
            retry_after = _telegram_retry_after(resp)
            if retry_after is not None and attempt == 1:
                wait = min(retry_after, 30)
                self._log("warn", f"Telegram 觸發頻率限制，{wait} 秒後重試")
                time.sleep(wait)
                continue
            self._log("error", f"Telegram 發送失敗：{_telegram_error(resp)}")
            return False
        return False

    def fetch_chat_ids(self, token: str) -> List[str]:
        """列出 bot 收到訊息中的 chat id（使用者先傳一句訊息給 bot 即可）。"""
        token = (token or "").strip()
        if not token:
            return []
        try:
            resp = requests.get(
                f"{API_BASE}/bot{token}/getUpdates",
                params={"timeout": 0, "limit": 50},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            self._log("error", f"取得 Chat ID 失敗：{exc}")
            return []
        if not resp.ok:
            self._log("error", f"取得 Chat ID 失敗：{_telegram_error(resp)}")
            return []
        try:
            updates = (resp.json() or {}).get("result") or []
        except ValueError:
            return []
        chat_ids: List[str] = []
        for update in updates:
            chat = (update or {}).get("message", {}).get("chat") or (
                update or {}
            ).get("channel_post", {}).get("chat")
            if not chat:
                continue
            chat_id = str(chat.get("id", "")).strip()
            if chat_id and chat_id not in chat_ids:
                chat_ids.append(chat_id)
        return chat_ids

    # ── Windows 桌面通知 ────────────────────────────────────────
    def send_desktop(self, title: str, message: str, timeout: int = 10) -> bool:
        try:
            from plyer import notification
        except Exception as exc:  # pragma: no cover - 依賴缺失時優雅降級
            self._log("error", f"無法載入桌面通知（plyer）：{exc}")
            return False
        try:
            notification.notify(
                title=title,
                message=message,
                app_name=APP_NAME,
                timeout=timeout,
            )
            return True
        except Exception as exc:
            self._log("error", f"桌面通知失敗：{exc}")
            return False


def _telegram_retry_after(resp: requests.Response) -> Optional[int]:
    try:
        data = resp.json()
    except ValueError:
        return None
    if data.get("error_code") == 429:
        return int((data.get("parameters") or {}).get("retry_after", 1))
    return None


def _telegram_error(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return f"{data.get('error_code')} {data.get('description', '')}".strip()
    except ValueError:
        return f"HTTP {resp.status_code}"
