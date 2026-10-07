"""Steam HTTP 客戶端：Session、Cookie、請求間隔、429 退避與冷卻。"""

from __future__ import annotations

import random
import re
import threading
import time
from typing import Callable, Optional

import requests

from .models import AppConfig

BACKOFF_BASE = (30, 60, 120)
JSON_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://steamcommunity.com/market/",
    "X-Requested-With": "XMLHttpRequest",
}
LogFn = Callable[[str, str], None]


class SteamError(RuntimeError):
    """Steam 回應失敗。"""


class RateLimitedError(SteamError):
    """被 Steam 限速（429），已進入冷卻。"""


class SteamClient:
    """背景執行緒使用的 HTTP 客戶端（本身可安全重複設定）。"""

    def __init__(
        self,
        log: Optional[LogFn] = None,
        stop_event: Optional[threading.Event] = None,
    ):
        self._log = log or (lambda level, text: None)
        self._stop = stop_event or threading.Event()
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._cfg: Optional[AppConfig] = None
        self._last_request: float = 0.0
        self._cooldown_until: float = 0.0

    # ── 設定 ────────────────────────────────────────────────────
    def apply(self, cfg: AppConfig) -> None:
        with self._lock:
            if self._cfg is not None and self._cfg.steam.to_dict() == cfg.steam.to_dict():
                self._cfg = cfg
                return
            session = requests.Session()
            session.headers.update(
                {
                    "User-Agent": cfg.steam.user_agent,
                    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                }
            )
            session.cookies.update(cfg.steam.cookies)
            self._session = session
            self._cfg = cfg

    # ── 狀態 ────────────────────────────────────────────────────
    @property
    def cooldown_remaining(self) -> int:
        return max(0, int(round(self._cooldown_until - time.monotonic())))

    def in_cooldown(self) -> bool:
        return self.cooldown_remaining > 0

    def set_cooldown(self, seconds: float) -> None:
        with self._lock:
            self._cooldown_until = max(
                self._cooldown_until, time.monotonic() + max(0.0, seconds)
            )

    def aborted(self) -> bool:
        return self._stop.is_set()

    # ── 請求 ────────────────────────────────────────────────────
    def get_text(self, url: str) -> str:
        return self._get(url).text

    def get_json(self, url: str) -> dict:
        """取得舊版市場 JSON，帶上瀏覽器 AJAX 標頭以降低被回傳一般 HTML 的機率。"""
        resp = self._get(url, headers=JSON_HEADERS)
        try:
            return resp.json()
        except ValueError as exc:
            raise SteamError(f"回應不是合法 JSON：{_json_response_hint(resp)}") from exc

    def _get(self, url: str, headers: Optional[dict] = None) -> requests.Response:
        cfg = self._cfg or AppConfig()
        attempt = 0
        while True:
            if self._stop.is_set():
                raise SteamError("已停止")
            if self.in_cooldown():
                raise RateLimitedError(
                    f"Steam 限速冷卻中，剩 {self.cooldown_remaining} 秒"
                )
            self._wait_gap(cfg)
            try:
                resp = self._session.get(
                    url, timeout=cfg.crawler.timeout_sec, headers=headers
                )
            except requests.RequestException as exc:
                if attempt >= cfg.crawler.max_retries:
                    raise SteamError(f"連線失敗: {exc}") from exc
                wait = _backoff_seconds(attempt)
                self._log("warn", f"連線失敗（{exc}），{wait} 秒後重試 {attempt + 1}")
                if self._sleep(wait):
                    raise SteamError("已停止")
                attempt += 1
                continue

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt >= cfg.crawler.max_retries:
                    self.set_cooldown(cfg.crawler.cooldown_sec)
                    raise RateLimitedError(
                        f"Steam 限速（HTTP {resp.status_code}），"
                        f"進入 {cfg.crawler.cooldown_sec // 60} 分鐘冷卻"
                    )
                wait = _retry_after_seconds(resp) or _backoff_seconds(attempt)
                self._log(
                    "warn",
                    f"Steam 回應 HTTP {resp.status_code}，{wait} 秒後重試"
                    f"（{attempt + 1}/{cfg.crawler.max_retries}）",
                )
                if self._sleep(wait):
                    raise SteamError("已停止")
                attempt += 1
                continue

            if not resp.ok:
                raise SteamError(f"HTTP {resp.status_code} {resp.reason or ''}".strip())

            return resp


    # ── 節流 ────────────────────────────────────────────────────
    def _wait_gap(self, cfg: AppConfig) -> None:
        gap = cfg.crawler.request_gap_sec
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            wait = gap - elapsed
        if wait > 0:
            self._sleep(wait)

    def _sleep(self, seconds: float) -> bool:
        """可被停止訊號中斷的 sleep；回傳 True 代表被中止。"""
        with self._lock:
            self._last_request = time.monotonic()
        return self._stop.wait(max(0.0, seconds))


def _json_response_hint(resp: requests.Response) -> str:
    """以不含完整回應內容的資訊描述非 JSON 回應，避免在日誌洩漏 Cookie 或頁面資料。"""
    content_type = resp.headers.get("Content-Type", "未提供").split(";", 1)[0].strip()
    size = len(resp.content or b"")
    text = (resp.text or "")[:4000]
    lowered = text.lower()
    details = []

    if not text.strip():
        details.append("空白回應")
    elif "html" in content_type.lower() or "<html" in lowered:
        title = re.search(r"<title[^>]*>\s*(.*?)\s*</title>", text, re.I | re.S)
        if title:
            safe_title = re.sub(r"\s+", " ", title.group(1)).strip()[:120]
            details.append(f"HTML title「{safe_title}」")
        else:
            details.append("HTML 頁面")
    else:
        details.append("非 JSON 內容")

    markers = (
        (("captcha", "recaptcha", "verify you are human", "cf-chl"), "驗證／CAPTCHA 頁"),
        (("sign in", "log in", "login", "登入"), "登入頁"),
        (("access denied", "forbidden", "denied"), "存取遭拒頁"),
        (("rate limit", "too many requests", "請求過於頻繁"), "限流頁"),
    )
    for needles, label in markers:
        if any(needle in lowered for needle in needles):
            details.append(label)
            break

    return (
        f"HTTP {resp.status_code}；Content-Type={content_type or '未提供'}；"
        f"{size} bytes；{'、'.join(details)}"
    )


def _backoff_seconds(attempt: int) -> int:
    base = BACKOFF_BASE[min(attempt, len(BACKOFF_BASE) - 1)]
    return base + random.randint(0, 10)


def _retry_after_seconds(resp: requests.Response) -> Optional[int]:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0, min(300, int(float(raw))))
    except (TypeError, ValueError):
        return None
