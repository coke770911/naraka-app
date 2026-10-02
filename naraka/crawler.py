"""背景爬蟲執行緒。

執行緒安全原則（重要）：

* 本執行緒 **完全不會** 操作任何 tkinter widget，只透過 ``bridge.post()``
  送出事件，由主執行緒的 ``after()`` 迴圈更新介面。
* 讀寫設定一律透過 ``ConfigStore``（內含 RLock），讀取使用 deep copy。
"""

from __future__ import annotations

import random
import threading
import time
from typing import Callable, List, Optional, Tuple

from .config_store import ConfigStore
from .dedupe import NotifyDedupe
from .filters import evaluate_listing
from .models import AppConfig, ItemEntry, Listing, listing_page_url, listing_ssr_url
from .notifiers import Notifier, format_telegram_message
from .ssr_parser import PAGE_SIZE, SSRParseError, parse_listings_json, parse_ssr_page
from .steam_client import RateLimitedError, SteamClient, SteamError


class CrawlerWorker(threading.Thread):
    """掃描所有啟用中的物品，比對條件後發送通知。"""

    def __init__(
        self,
        store: ConfigStore,
        bridge,
        notifier: Notifier,
        file_log: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(daemon=True, name="naraka-crawler")
        self._store = store
        self._bridge = bridge
        self._notifier = notifier
        self._file_log = file_log
        self._stop = threading.Event()
        self._once = threading.Event()
        self._dedupe = NotifyDedupe()
        self._client = SteamClient(log=self._log, stop_event=self._stop)

    # ── 控制 ────────────────────────────────────────────────────
    def stop(self) -> None:
        self._stop.set()

    def scan_once(self) -> None:
        self._once.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # ── 主迴圈 ──────────────────────────────────────────────────
    def run(self) -> None:
        self._log("info", "爬蟲啟動")
        self._bridge.post("status", running=True)
        while not self._stop.is_set():
            cfg = self._store.snapshot()
            self._dedupe.load(cfg.notified)
            hits = 0
            try:
                hits = self._cycle(cfg)
            except RateLimitedError as exc:
                self._log("warn", f"{exc}；暫停到冷卻結束")
                self._bridge.post("cooldown", seconds=self._client.cooldown_remaining)
            except SteamError as exc:
                self._log("error", f"Steam 錯誤：{exc}")
            except Exception as exc:  # 任何單輪錯誤都不能讓執行緒死掉
                self._log("error", f"未預期錯誤：{exc!r}")

            if self._once.is_set():
                self._once.clear()
                self._log("info", "單次掃描完成")
                self._bridge.post("cycle_done", hits=hits, next_at=None)
                break

            delay = random.uniform(
                cfg.crawler.interval_min_sec, cfg.crawler.interval_max_sec
            )
            # 限速冷卻尚未結束時，把等待時間拉到冷卻之後，避免整輪只換來錯誤
            cooldown = self._client.cooldown_remaining
            if cooldown > 0:
                delay = max(delay, float(cooldown) + 1.0)
            self._log("info", f"等待 {delay:.0f} 秒後進行下一次掃描…")
            self._bridge.post("cycle_done", hits=hits, next_at=time.time() + delay)
            if self._stop.wait(delay):
                break

        self._log("info", "爬蟲已停止")
        self._bridge.post("status", running=False, next_at=None)

    # ── 單輪掃描 ────────────────────────────────────────────────
    def _cycle(self, cfg: AppConfig) -> int:
        items = [i for i in cfg.items if i.enabled and i.hash_name]
        if not items:
            self._log("warn", "沒有啟用中的監控物品，請先於「監控清單」新增物品")
            return 0

        self._client.apply(cfg)
        started = time.time()
        self._log(
            "info",
            f"── 掃描開始（{len(items)} 個物品，價格上限 "
            f"NT${cfg.criteria.max_price_ntd:,.0f}，{cfg.criteria.logic} 需符合 "
            f"{cfg.criteria.effective_min_match}/{cfg.criteria.slot_count} 格）──",
        )
        self._bridge.post("scan_start", total=len(items))

        hits = 0
        for item in items:
            if self._stop.is_set():
                break
            if self._client.in_cooldown():  # 限速 → 直接中止本輪，不再打任何請求
                self._log(
                    "warn",
                    f"Steam 限速冷卻中（剩 {self._client.cooldown_remaining} 秒），本輪中止",
                )
                self._bridge.post(
                    "cooldown", seconds=self._client.cooldown_remaining
                )
                return hits
            hits += self._process_item(cfg, item)
            delay = random.uniform(cfg.crawler.delay_min_sec, cfg.crawler.delay_max_sec)
            if self._stop.wait(delay):
                break

        self._log("info", f"── 掃描結束（{time.time() - started:.1f} 秒）──")
        return hits

    def _process_item(self, cfg: AppConfig, item: ItemEntry) -> int:
        label = item.display

        # 註：原本這裡先用 market/search/render 做「最低價預檢」以省請求，
        # 但實測該端點的 sell_price 與列表頁價格單位不一致（回傳約 124.78，
        # 列表頁同一件商品顯示 NT$4,032），且 currency 參數被 Steam 忽略。
        # 若拿它跟價格上限比較，會在使用者設定低價上限時整批靜默漏報，
        # 因此已移除；改以實際抓到的掛單計算本輪最低價。

        # ① 詳細爬取
        try:
            listings, truncated = self._scrape(cfg, item)
        except RateLimitedError:
            raise
        except SteamError as exc:
            self._log("error", f"{label}｜爬取失敗：{exc}")
            self._bridge.post("item_done", item_id=item.id, hits=0, total=0, error=str(exc))
            return 0

        if not listings:
            self._log("warn", f"{label}｜取得 0 筆掛單")
            self._bridge.post("item_done", item_id=item.id, hits=0, total=0, empty=True)
            return 0

        # ② 條件篩選
        matched: List[Tuple[Listing, object]] = []
        for listing in listings:
            result = evaluate_listing(listing, cfg.criteria)
            if result.matched:
                matched.append((listing, result))
            elif cfg.crawler.verbose:
                self._log(
                    "info", f"    {listing.summary()}｜{result.reason_text()}"
                )

        cheapest = min(listings, key=lambda x: x.price_ntd)
        note = "（已達分頁上限，結果可能不完整）" if truncated else ""
        self._log(
            "info",
            f"{label}｜取得 {len(listings)} 筆（最低 {cheapest.price_text} "
            f"· {cheapest.slots_text}），符合 {len(matched)} 筆{note}",
        )

        if not matched:
            self._bridge.post("item_done", item_id=item.id, hits=0, total=len(listings))
            return 0

        # ③ 通知（依 listing 序號去重，避免每輪重複轟炸）
        notified = 0
        for listing, result in matched:
            if self._dedupe.seen(item.id, listing.listing_number):
                # 仍然符合條件，但不是新上架 → 用 info 等級，不要混淆成新命中
                self._log(
                    "info",
                    f"    ↷ {listing.summary()}｜符合條件但已通知過，略過",
                )
                continue
            self._log("hit", f"★ 命中 {label}｜{listing.summary()}｜{result.reason_text()}")
            self._notify(cfg, item, listing)
            notified += 1

        self._bridge.post(
            "item_done", item_id=item.id, hits=notified, total=len(listings)
        )
        return notified

    def _notify(self, cfg: AppConfig, item: ItemEntry, listing: Listing) -> None:
        title = f"🎯 {item.display} 新上架！"
        message = (
            f"星格: {listing.slots_text}\n"
            f"價格: {listing.price_text}\n"
            f"序號: {listing.listing_number or '?'}\n"
            f"伺服器: {listing.available_server or '?'}\n{listing.url}"
        )
        if cfg.desktop_notify:
            # 桌面通知會建立 Win32 視窗，交回主執行緒執行較安全
            self._bridge.post("toast", title=title, message=message)

        if cfg.telegram.usable:
            ok = self._notifier.send_telegram(
                cfg.telegram.token,
                cfg.telegram.chat_id,
                format_telegram_message(item, listing),
            )
            self._log(
                "sent" if ok else "error",
                f"    ↪ Telegram {'已通知' if ok else '通知失敗'}"
                f"：{listing.listing_number}",
            )
        else:
            self._log("info", "    ↪ Telegram 未啟用或設定不完整，略過")

        self._dedupe.mark(item.id, listing.listing_number)
        notified = self._dedupe.to_dict()
        self._store.mutate(lambda c: c.notified.update(notified))

    # ── 分頁爬取 ────────────────────────────────────────────────
    def _scrape(self, cfg: AppConfig, item: ItemEntry) -> Tuple[List[Listing], bool]:
        listings: List[Listing] = []
        seen: set = set()
        total = 0
        start = 0
        empty_streak = 0
        truncated = False

        for page in range(cfg.crawler.max_pages):
            if self._stop.is_set():
                break

            html = self._client.get_text(listing_ssr_url(item.hash_name, start=start))
            try:
                result = parse_ssr_page(html, item.hash_name)
            except SSRParseError as exc:
                result = self._fallback_json(item, start)
                if result is None:
                    self._log("warn", f"{item.display}｜頁面解析失敗：{exc}")
                    break

            if result.total_count:
                total = max(total, result.total_count)

            fresh = []
            for listing in result.listings:
                if listing.listing_number and listing.listing_number in seen:
                    continue
                if listing.listing_number:
                    seen.add(listing.listing_number)
                fresh.append(listing)
            listings.extend(fresh)
            empty_streak = 0 if fresh else empty_streak + 1

            if total and start + PAGE_SIZE >= total:
                break
            if not result.listings or empty_streak >= 2:
                break
            if page + 1 >= cfg.crawler.max_pages:
                truncated = total > len(listings)
                break

            start += PAGE_SIZE
            delay = random.uniform(cfg.crawler.delay_min_sec, cfg.crawler.delay_max_sec)
            if self._stop.wait(delay):
                break

        return listings, truncated

    def _fallback_json(self, item: ItemEntry, start: int):
        try:
            payload = self._client.get_json(listing_page_url(item.hash_name, start=start))
        except RateLimitedError:
            raise
        except SteamError:
            return None
        if not isinstance(payload, dict):
            return None
        if not (payload.get("assets") or payload.get("listinginfo")):
            return None
        result = parse_listings_json(payload, item.hash_name)
        return result if result.listings else None

    # ── 日誌 ────────────────────────────────────────────────────
    def _log(self, level: str, text: str) -> None:
        self._bridge.post("log", level=level, text=text)
        if self._file_log is not None:
            try:
                self._file_log(f"[{level}] {text}")
            except Exception:
                pass
