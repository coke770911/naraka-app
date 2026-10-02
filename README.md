# Naraka 星格監控工具

**版本 1.1.0**

監控 Steam 社群市場（appid `1203220`）上「謫星 / Star」系列商品的新上架，
依**每個物品各自設定**的**價格上限**與**星格（Constellation）條件**篩選，
命中時同時送出

- **Telegram 訊息**（Bot API）
- **Windows 桌面通知**（plyer Toast）

桌面程式為 Python + CustomTkinter（強制深色模式），爬蟲跑在獨立執行緒，
不會卡住介面。

---

## 目錄

- [程式用途](#程式用途)
- [快速開始](#快速開始)
- [介面](#介面)
- [條件規則](#條件規則)
- [Telegram 設定](#telegram-設定)
- [Steam 驗證](#steam-驗證選填)
- [請求節流與容錯](#請求節流與容錯)
- [程式架構](#程式架構)
- [測試](#測試)
- [打包](#打包pyinstaller)
- [疑難排解](#疑難排解)

---

## 程式用途

### 解決什麼問題

《絕區零》以外的「謫星（Star）」系列商品在 Steam 社群市場上會被低價轉手，
但挂單數動輒數十筆、價格與星格品質參差，需要有人一直盯著。這個工具做三件事：

1. **自動巡檢** — 依設定的間隔反覆抓取市場頁，比對星格條件
2. **條件篩選** — 每個物品各有一組條件，只挑符合它自己門檻的挂單
3. **即時通知** — Telegram + Windows 桌面通知，立刻通知，不會被洗版洗掉

### 適用對象

- 在收購特定星格排列的謫星商品，需要在低價出現的第一時間出手
- 已經用腳本或瀏覽器擴充功能手動查，但想要一個不佔資源的常駐小工具

### 不做的事

- 不自動購買、不模擬任何 Steam 帳號操作（純讀取公開市場頁）
- 不代理、不轉售、不繞過任何 Steam 的存取控制

---

## 快速開始

```powershell
# 1. 建立虛擬環境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. 安裝依賴
pip install -r requirements.txt

# 3. 執行
python main.py
```

使用流程：**監控清單** 輸入商品名稱（或直接貼市場網址）→ 對該物品按 **⚙ 條件**
→ 在 **條件與設定** 設定它的價格上限與星格門檻 → 左側 **▶ 啟動監控**
（或 **⟳ 單次掃描** 先試跑一次看有沒有命中）。

> 從 1.0.0 升級的話，原本的全域條件會自動分發給每個物品，但 **格數會設為
> 自動偵測** —— 舊的那份 3 格條件對 4 格商品本來就是錯的，保留只會延續錯誤。
> 價格上限與各格門檻會照原樣搬過去，但門檻值是照 3 格商品定的，
> 第一次掃描後記得依實際資料調整。

---

## 介面

| 區塊 | 內容 |
| --- | --- |
| 左側 Sidebar | 啟動 / 停止 / 單次掃描、上次掃描命中數、設定檔位置、版本號 |
| 監控清單 | 新增商品（含選填顯示名稱）、啟用/停用、移除、**每列的條件摘要**、⚙ 選取條件、點名稱開啟市場頁 |
| 條件與設定 | **選取物品的條件**（價格上限、格數來源、逐格門檻、AND/OR）、抓取頻率、Telegram、桌面通知、Steam Cookie |
| 即時日誌 | 依等級高亮（★命中 / 已通知 / 警告 / 錯誤 / 一般），可清除 |
| 底部狀態列 | 執行狀態、目前掃描物件、下次掃描倒數 |

所有設定（含商品清單與已通知紀錄）會**自動儲存**到
`%LOCALAPPDATA%\NarakaStarMonitor\config.json`，關閉程式時再保險寫入一次。
完整日誌另存於同目錄的 `crawler.log`。

---

## 條件規則

> **每個物品各自一份條件。** 各商品的星格數與價格區間差異很大 ——
> 實測 `Shadow Scent` 是 3 格、約 NT$4,000，`Novaburst` 是 4 格、
> 約 NT$47,000。用同一份條件套所有商品會誤判，所以條件是綁在物品上的。
>
> 在 **監控清單** 按物品右側的 **⚙ 條件** 選取該物品，
> 再到 **條件與設定** 上半段編輯它的條件。清單上每列都會顯示條件摘要。

### 價格

單一上限：`price ≤ 價格上限`。**填 `0` 代表不限價**。

### 格數來源

| 選項 | 行為 |
| --- | --- |
| `0`（預設） | **自動偵測** — 掃描時依實際挂單判定 3 格或 4 格，末格跟著資料寬度走 |
| `3` / `4` | 手動指定，掃描不會被自動偵測覆寫 |

指定格數與實際不符時該物品會 **0 筆命中** 並在日誌警告 —— 寧可漏報，
也不拿 3 格門檻去套 4 格資料。

### 星格（Constellation）

| 格數 | 第 1 格 | 第 2 格 | 第 3 格 | 第 4 格 |
| --- | --- | --- | --- | --- |
| 3 格 | 0~9999 | 0~999 | **0 或 1（精確）** | — |
| 4 格 | 0~9999 | 0~999 | 0~999 | **0 或 1（精確）** |

每格可填「下限」與「上限」：

- 上限留空或填 `0` → **只要 ≥ 下限**就算符合
- 上限 > 0 → 必須落在 `[下限, 上限]` 區間內
- 下限填 **`-1`** → **不關心這格**，不列入符合格數
- 最後一格是 0/1 二元位，預設為精確比對；填 `-1` 可略過

匹配邏輯：

- `AND`：所有格都要符合
- `OR`：符合的格數 ≥ 「最少符合格數」

範例（3 格、AND、需 3 格）：`9650 | 950 | 1`
→ 第1格 9650 ≥ 9500 ✓、第2格 950 ≥ 950 ✓、第3格 1 == 1 ✓

### 新增物品的預設

新加入的物品採 **寬鬆預設**：不限價、星格不設門檻（全部 `-1`），
掃描時會全部命中 —— 讓你先看到實際資料長什麼樣，再自己收緊門檻。

編輯好某個物品的條件後，按 **設為新增物品預設** 可把它設為之後新增物品的模板。

---

## Telegram 設定

1. 在 Telegram 找 [@BotFather](https://t.me/BotFather)，`/newbot` 建立 Bot，拿到 **Token**
2. **先對你的 Bot 傳一句訊息**（這是取得 Chat ID 的必要步驟）
3. 回到程式的「條件與設定 → Telegram 通知」，填入 Token，按 **取得 Chat ID**
4. 按 **發送測試訊息** 驗證

Token 與 Chat ID 會以密碼形式顯示並保存在本機 `config.json`。

---

## Steam 驗證（選填）

未登入通常仍可讀取公開市場。若遇到 HTTP 429 或被導向驗證頁，可從瀏覽器
複製以下 Cookie 填入：

- `steamLoginSecure`
- `sessionid`

---

## 請求節流與容錯

| 機制 | 行為 |
| --- | --- |
| 全域請求間隔 | 預設 1.5 秒以上才送出下一個請求 |
| 物品 / 分頁延遲 | 預設 2~5 秒隨機 |
| 429 或 5xx | 依 `Retry-After` 或 30/60/120 秒（+ jitter）退避重試，最多 5 次 |
| 退避耗盡 | 進入 10 分鐘冷卻，期間略過整輪掃描並於狀態列顯示倒數 |
| 分頁停止 | 達 `total_count`，或連續 2 頁沒有新的 listing 序號 |

價格上限直接與實際抓到的掛單價格比對，不另外預先查價。

> 請勿把請求間隔調得過小，過快容易觸發 Steam 限速。

---

## 程式架構

### 分層

```
┌─────────────────────────────────────────────────┐
│  UI 層（主執行緒）                                │
│  naraka/ui/app.py      主視窗、設定互動            │
│  naraka/ui/widgets.py  LogBox / ItemRow           │
│  naraka/ui/bridge.py   執行緒 → UI 的訊息橋接       │
└────────────────────┬────────────────────────────┘
                     │  bridge.post()  (queue.Queue)
┌────────────────────▼────────────────────────────┐
│  業務層（背景執行緒）                               │
│  naraka/crawler.py     掃描流程協調、分頁、去重       │
│  naraka/filters.py     條件判定                    │
│  naraka/notifiers.py   Telegram / 桌面通知          │
└────────────────────┬────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────┐
│  資料層                                           │
│  naraka/ssr_parser.py  Steam 頁面 → Listing         │
│  naraka/steam_client.py  Session / 節流 / 退避       │
│  naraka/config_store.py  config.json 讀寫          │
│  naraka/models.py      資料類別 / URL 組裝           │
│  naraka/dedupe.py      序號去重                     │
│  naraka/paths.py       資料目錄                     │
└─────────────────────────────────────────────────┘
```

### 檔案

| 檔案 | 行數 | 職責 |
| --- | ---: | --- |
| `main.py` | 28 | 進入點，包住例外處理 |
| `build.spec` | 62 | PyInstaller 打包設定（onedir） |
| `assets/make_icon.py` | 132 | 程式化產生 `icon.ico` / `icon.png` |
| `naraka/paths.py` | 30 | 資料目錄解析（`%LOCALAPPDATA%\NarakaStarMonitor`） |
| `naraka/models.py` | 502 | `Listing` / `Criteria`（含 `loose()`、`ANY(-1)` 不關心、v1→v2 遷移）/ 各設定資料類別、星格解析、URL 組裝 |
| `naraka/config_store.py` | 86 | `config.json` 讀寫（原子寫入 + RLock 執行緒安全） |
| `naraka/steam_client.py` | 162 | Session / Cookie / 請求間隔 / 429 退避與冷卻 |
| `naraka/ssr_parser.py` | 401 | `window.SSR.renderContext` 解析（中英欄位 + 舊版 JSON 備援） |
| `naraka/filters.py` | 123 | 價格 + 星格條件判定，回傳逐格 reasons；處理自動格數與 `-1` 略過 |
| `naraka/dedupe.py` | 59 | 以 listing 序號去重，避免每輪重複通知 |
| `naraka/notifiers.py` | 138 | Telegram Bot API + plyer 桌面通知 |
| `naraka/crawler.py` | 342 | 背景爬蟲執行緒（單輪掃描流程、格數偵測） |
| `naraka/ui/bridge.py` | 71 | `queue.Queue` + `after()` 的執行緒 → UI 橋接 |
| `naraka/ui/widgets.py` | 195 | `LogBox`（tag 高亮）、`ItemRow`（含條件摘要與選取高亮） |
| `naraka/ui/app.py` | 1084 | 主視窗與所有設定互動 |

### 執行緒模型

背景執行緒 **完全不碰 tkinter**，只呼叫 `bridge.post()`（`queue.Queue.put` 是
thread-safe）；主執行緒以 `after(80ms)` 迴圈 drain 佇列後才更新元件。
桌面通知也經由 bridge 交回主執行緒執行。
爬蟲讀設定走 `ConfigStore.snapshot()`（回傳 deep copy），寫入走
`ConfigStore.mutate()`（同一把鎖），因此不會讀到改到一半的設定。

### 資料流

```
Steam 頁面 HTML
  → extract_render_context()   取出 JSON.parse 的字串，還原跳脫
  → parse_ssr_page()           遍歷 queryData → pages[] → listings[]
  → parse_listing_object()     bbcode（中文欄位）→ Listing
  → _detect_slot_count()       本物品實際的星格格數（3 或 4）
  → evaluate_listing()         該物品自己的價格 + 星格條件 → (matched, reasons)
  → NotifyDedupe.seen()        已通知過就跳過
  → Notifier                   Telegram + 桌面通知
```

### 為什麼不做「最低價預檢」

原本設計是先打 `market/search/render` 取最低價，價格超標就整件商品跳過詳細爬取，
省下請求。實測發現該端點的 `sell_price` 與列表頁價格單位不一致（同一件商品
`search` 回 12478、列表頁顯示 `NT$4,032`），且 `currency` 參數被 Steam 忽略。
若拿它跟價格上限比較，價格上限設低時會**整批靜默漏報**，所以改為直接比對
實際抓到的掛單價格。

### 為什麼條件要掛在物品上

最初設計是全域一份條件（單一價格上限 + 逐格門檻），套用到所有監控物品。
實測各商品差異很大：

| 商品 | 星格數 | 價格區間 |
| --- | ---: | --- |
| `Star - Shadow Scent(Non-CN)` | 3 格 | NT$3,989 ~ 4,744 |
| `Star - Aegis Reckoning(Non-CN)` | 4 格 | NT$12,203 ~ 25,733 |
| `Star - Fading Polaris(Non-CN)` | 4 格 | NT$35,452 ~ 35,662 |
| `Star - Novaburst(Non-CN)` | 4 格 | NT$43,479 ~ 50,902 |

四個裡三個是 4 格、價格差到 12 倍。共用一份條件會讓 4 格商品拿 3 格門檻去比，
結果是雜訊而不是命中。條件因此改為 `ItemEntry.criteria`，由使用者在
條件頁針對每個物品設定。

格數預設為 **自動偵測**：抓到的挂單是幾格就比幾格，末格的精確比對也跟著
對齊到資料實際寬度（3 格資料的第3格是二元位，不是第4格）。需要固定寬度時
可在條件頁手動指定。

### 格數不符時為什麼不命中

手動指定 4 格但實際是 3 格時，該物品會 0 筆命中並在日誌警告。另一種做法是
「以實際格數為準繼續比」，但那等於拿 3 格門檻去套 4 格資料的第3格 ——
結果看似合理、其實比錯位置。寧可漏報也不給錯的命中。

### 版本

版本號定義於 `naraka/__init__.py` 的 `__version__`，單一來源，同時顯示在
視窗標題與 Sidebar。

| 版本 | 日期 | 內容 |
| --- | --- | --- |
| 1.0.0 | 2026-10-02 | 第一版。SSR 頁面解析（中英欄位）、價格 + 星格條件篩選、序號去重、Telegram + 桌面通知、PyInstaller 打包 |
| 1.1.0 | 2026-10-02 | **條件改為每個物品獨立**（原為全域共用一份）。新增格數自動偵測、`-1` 不關心單格、價格填 0 為不限價、條件摘要列。設定檔升版至 v2，舊的全域條件自動分發給各物品且格數改為自動 |

---

## 測試

```powershell
pip install -r requirements-dev.txt
pytest -q
```

測試涵蓋：星格 AND/OR/區間/精確值判定、SSR 三層跳脫還原與巢狀 JSON、
中文 bbcode 欄位（含全形冒號）與英文欄位解析、`unPrice` 價格後備、
舊版 JSON 端點備援、去重、設定持久化（含壞檔復原），以及用假 Steam
來源跑的爬蟲單輪整合（命中 → 通知 → 去重 → 第二輪不重複）。

`tests/make_fixture.py` 產生的 fixture 是照實測 Steam 回應的結構撰寫
（中文欄位、`eCurrency=30`、`market_item_search` queryKey），可拿來對照線上格式。

若要重新產生靜態 fixture：

```powershell
python tests/make_fixture.py
```

---

## 打包（PyInstaller）

```powershell
pip install pyinstaller
pyinstaller build.spec --noconfirm
```

輸出 `dist\NarakaStarMonitor\NarakaStarMonitor.exe`（onedir 啟動較快），
並自動套用 `assets\icon.ico`（16~256 多尺寸）為程式圖示。

### 重新產生圖示

圖示由程式繪製，不需另外準備素材。若想調色或改形狀：

```powershell
python assets/make_icon.py
```

`assets/make_icon.py` 會輸出 1024px 的 `icon.png`（可自行替換後重跑）
與多尺寸的 `icon.ico`。Pillow 只在重跑這支時才需要（列於
`requirements-dev.txt`），一般執行與打包都不需要它。

---

## 疑難排解

| 現象 | 處理方式 |
| --- | --- |
| 日誌出現「Steam 限速冷卻中」 | 正常保護行為，等冷卻結束；建議再把請求間隔調大 |
| 日誌出現「找不到 window.SSR.renderContext」 | Steam 可能改版或要求登入；先貼 Cookie，再回報 issue 附上新版頁面結構 |
| 取得 0 筆掛單 | 商品名稱可能不精確，確認網址的 `market_hash_name` 有對上 |
| 桌面通知沒出現 | 確認「啟用 Windows 桌面通知」有開；Windows 設定 → 系統 → 通知 → 允許應用程式通知 |
| 同一掛單一直重複通知 | 按「清除已通知紀錄」可重置；正常情況下已通知序號會記在 `config.json` |
| 中文顯示為方框 | 系統缺少微軟正黑體（Microsoft JhengHei），字體會自動退回預設 |

---

## 授權

MIT License，詳見 [LICENSE](LICENSE)。

僅供個人學習與使用。所有資料版權歸 Valve Corporation 所有；
本工具不代理、不轉售遊戲內容。