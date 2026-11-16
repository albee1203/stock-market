# 台股外資＋新聞輔助分析（tw-stock-advisor）

每天自動整理 **「建議買入」** 與 **「建議賣出／避開」** 的觀察名單：

| 清單 | 條件 |
|---|---|
| 📈 建議買入 | 外資連續買超或單日大買 **且** 當日新聞偏利多 |
| 📉 建議賣出／避開 | 外資連續賣超或單日大賣 **且** 當日新聞偏利空 |

每檔股票都附上 **0～100 分推薦指數**、**參考停損／停利價**與一句推薦理由，例如：
`外資連續買超 6 日，今日買超 6,200 張（約 65.1 億元），佔成交量 22%｜新聞利多 1/1 則：營收創新高、調升目標價`

> ⚠️ 本工具僅為資訊整理與研究用途，**不構成投資建議**。

## 架構

```
           ┌──────────────── collectors ────────────────┐
TWSE/TPEx ─┤ foreign.py  外資及陸資買賣超（上市 TWT38U＋上櫃）│
           │ prices.py   收盤價、成交量（股數→金額、佔量比）│
新聞網站 ──┤ news.py     鉅亨網 API ＋ 經濟日報/工商時報 RSS │
           └──────────────────────┬─────────────────────┘
                                  ▼
          analysis/  stock_matcher → sentiment（詞典）/ llm（Claude，選用）
                                  ▼
                     scoring.py  外資分 × 新聞分 → 清單＋理由
                                  ▼
                   SQLite / PostgreSQL（SQLAlchemy）
                      ▼                         ▼
      FastAPI（app/api.py）   Streamlit（frontend/）   export → 手機 App（web/，GitHub Pages）
```

| 路徑 | 說明 |
|---|---|
| `app/collectors/` | 爬蟲。用**欄位名稱**而非固定索引解析，官方調整欄位順序時較不易壞；請求自動節流（證交所會封鎖過於頻繁的 IP） |
| `app/analysis/sentiment.py` | 內建約 120 個台股利多／利空詞，含權重、長詞優先、否定詞翻轉（「並未虧損」），並**逐句**評估每檔個股，避免一篇新聞多檔股票互相污染 |
| `app/analysis/llm.py` | 設定 `ANTHROPIC_API_KEY` 後改用 Claude 分析：批次送出、JSON Schema 結構化輸出、過濾不存在的代號，任何錯誤自動退回詞典 |
| `app/analysis/scoring.py` | 評分公式（見下方） |
| `app/pipeline.py` | 每日流程，含自動回補歷史資料（計算連買天數用） |
| `app/api.py` | REST API |
| `frontend/streamlit_app.py` | 電腦版分析介面（Streamlit） |
| `app/export.py` | 匯出手機 App 用的靜態 JSON |
| `web/` | 手機 App（PWA，純 HTML/JS，不需建置） |
| `.github/workflows/daily.yml` | 收盤後自動更新＋發佈 App |
| `.github/workflows/morning-notify.yml` | 隔天早上推播清單 |
| `app/backtest.py`、`app/replay.py` | 回測、用歷史資料重播清單 |
| `app/notify.py` | LINE／Telegram／ntfy 推播 |
| `app/risk.py` | 推薦指數、停損停利價、清單追蹤、持股檢查 |

## 快速開始

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 需要時再編輯
```

**1. 先用離線示範資料看畫面**（數字為虛構）

```bash
python -m app.cli demo
streamlit run frontend/streamlit_app.py
```

**2. 抓真實資料**（建議交易日 15:30 之後執行，上櫃資料較晚公布）

```bash
python -m app.cli backfill --days 10   # 第一次執行：回補 10 個交易日，連買天數才準
python -m app.cli run                  # 今日；或 --date 2026-11-13
```

**3. 啟動 API**（選用；Streamlit 直接讀資料庫，不一定需要）

```bash
uvicorn app.api:app --reload
```

打開 http://127.0.0.1:8000/docs 可看到互動式文件。

| Endpoint | 說明 |
|---|---|
| `GET /api/recommendations?date=` | 當日買入／賣出清單（不帶日期＝最新） |
| `GET /api/foreign?side=buy\|sell&limit=30` | 外資買賣超排行（含連買天數、金額、旗標） |
| `GET /api/news?code=2330` | 新聞與情緒分數 |
| `GET /api/stocks/{code}` | 個股近 30 日外資買賣超與股價 |
| `GET /api/backtest` | 回測統計與明細（含停損停利策略） |
| `GET /api/alerts?date=` | 停損停利提醒與持有中的清單部位 |
| `POST /api/run?date=` | 背景執行每日流程 |

## 📱 手機 App ＋ 每日自動更新（免費、免伺服器）

```
收盤後（daily.yml，週一至週五 16:20，18:20 補跑）
  ├─ 從 data 分支還原資料庫
  ├─ 抓外資、股價、新聞 → 情緒分析 → 評分 → 回測
  ├─ 匯出靜態 JSON ＋ App（web/）
  ├─ 資料庫存回 data 分支
  └─ 發佈到 GitHub Pages ──▶ 手機開網址 →「加入主畫面」＝ App

隔天早上（morning-notify.yml，週一至週五 08:40）
  └─ 推播前一個交易日的清單 ──▶ LINE / Telegram / ntfy
```

App（`web/`）是 PWA：可以安裝到手機桌面、全螢幕開啟、離線時顯示上次的資料，並支援深色模式；配色依台股習慣「紅漲綠跌」。共有四個分頁：

| 分頁 | 內容 |
|---|---|
| ★ 清單 | 建議買入／賣出卡片：綜合分、外資分、新聞分、推薦理由、關鍵字 |
| ⇅ 外資 | 外資買超／賣超前 30 名，含張數、金額、佔成交量、連買天數 |
| ≡ 新聞 | 當日新聞與情緒，可篩選利多／利空 |
| ⌕ 個股 | 搜尋任一檔股票，看近 60 日外資買賣超圖與收盤價、相關新聞 |
| ↺ 回測 | 過去清單 1／5／20 個交易日後的平均報酬、勝率、超額報酬與明細 |

### 一次性設定（約 3 分鐘）

1. **把程式碼合併到預設分支（main）**：GitHub 的排程只會在預設分支上執行。
2. **開啟 GitHub Pages**：repo → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。
   （免費帳號的 Pages 需要 **public repo**；private repo 需付費方案。）
3. （選用）**用 Claude 分析新聞**：**Settings → Secrets and variables → Actions → New repository secret**，名稱 `ANTHROPIC_API_KEY`。
4. （選用）**設定早上推播**：見下方〈🔔 每天早上推播清單〉。
5. **第一次手動執行**：**Actions → Daily update → Run workflow**，`backfill_days` 填 `10`；想立刻有回測結果，`replay_days` 填 `60`（約 30～60 分鐘）。
6. 執行完成後，網址會是 `https://<你的帳號>.github.io/stock-market/`。
7. **安裝到手機**：
   - iPhone：用 Safari 開啟 → 分享 → **加入主畫面**
   - Android：用 Chrome 開啟 → 選單 → **安裝應用程式**（或點右上角的 ⤓）

之後每個交易日下午會自動更新，打開 App 就是最新清單。

### 在本機預覽 App

```bash
python -m app.cli demo                 # 或 run 抓真實資料
python -m app.cli export --out site
python -m http.server 8080 -d site     # 打開 http://127.0.0.1:8080
```

### 不用 GitHub，改在自己電腦排程

Linux / macOS（crontab，週一至週五 16:20）：

```cron
20 16 * * 1-5 cd /path/to/stock-market && .venv/bin/python -m app.cli run && .venv/bin/python -m app.cli export --out site >> data/run.log 2>&1
```

Windows 可用「工作排程器」執行同樣的指令；`site/` 可放到任何靜態網站空間（Netlify、Cloudflare Pages…）。

## 💯 推薦指數與停損停利

**推薦指數（0～100）**＝ `(綜合分數 + 1) × 50`，50 為中性：

| 分數 | 評等 |
|---|---|
| 80～100 | 強力關注 |
| 65～79 | 偏多 |
| 36～64 | 中性 |
| 21～35 | 偏空 |
| 0～20 | 強烈避開 |

清單、推播、App 個股頁（任何股票）都會顯示。

**停損／停利參考價**（以收盤價判斷，參數在 `app/config.py` 的 `RiskConfig`）：

- **停損幅度**：2 × 近 20 日日報酬標準差，限制在 4%～10% 之間；歷史資料不足時用 7%。波動大的股票停損空間較寬，避免被正常震盪洗出場。
- **停利幅度**：停損幅度 × 2，也就是風險報酬比 1:2。

**三種提醒**

| 提醒 | 怎麼運作 | 哪裡看得到 |
|---|---|---|
| 清單追蹤 | 個股進入「建議買入」當天，以收盤價建立虛擬部位，最長追蹤 20 個交易日。觸及停利 🎯、觸及停損 🛑、接近停損 ⚠️（2% 內）、接近停利 📈、到期 ⏱ 時提醒 | 早上推播、App 清單頁最上方、Streamlit、`GET /api/alerts` |
| 我的持股（App） | 在 App 個股頁輸入成本（可自訂停損%、停利%），資料**只存在這台手機**，不會上傳 | App 清單頁最上方、個股頁 |
| 我的持股（推播） | 在 repo 的 **Secrets** 新增 `HOLDINGS`，每行一檔：`代號,成本[,停損%,停利%]`，例如<br>`2330,980`<br>`2317,190,8,20` | 早上推播 |

> 持股請存在 **Secret**，不要提交成檔案：GitHub Pages 與公開 repo 任何人都看得到。本機可放在已加入 `.gitignore` 的 `holdings.csv`。

回測頁會多一欄「照停損停利出場」的實現報酬，可以和單純持有 N 日比較，判斷停損停利參數是否合適。

## 🔔 每天早上推播清單

每個交易日早上 **08:40**（台北時間）推播**前一個交易日**收盤後產生的清單，內容依序為：

1. 🔔 停損停利提醒（清單追蹤＋你的持股）
2. 買入／賣出前 5 名，含推薦指數、外資理由，買入另附參考停損／停利價
3. 回測摘要
4. App 連結

- 週一早上推播上週五的清單。
- 每份清單、每個管道只推播一次，連假期間不會重複送舊清單。
- 清單超過 5 天沒更新（每日排程可能中斷）就不推播，避免誤導。
- GitHub 排程常延遲 5～30 分鐘，所以排在 08:40，確保 9:00 開盤前收到。想準 9:00 發送，把 `.github/workflows/morning-notify.yml` 的 cron 改成 `"0 1 * * 1-5"`。

選一種（或多種）管道，把憑證存到 repo 的 **Settings → Secrets and variables → Actions**：

| 管道 | 需要的 Secrets | 設定方式 |
|---|---|---|
| **LINE** | `LINE_CHANNEL_ACCESS_TOKEN`（必要）<br>`LINE_TO`（選用） | 1. 到 [LINE Developers](https://developers.line.biz/) 建立 Provider → **Messaging API channel**（會同時建立一個 LINE 官方帳號）<br>2. Messaging API 分頁最下方發行 **Channel access token (long-lived)**<br>3. 用手機掃 channel 的 QR code 加官方帳號好友<br>4. 不設 `LINE_TO` ＝廣播給所有好友（自己用最簡單）；要指定一人，填 Basic settings 頁的 **Your user ID**（U 開頭）<br>※ 免費方案每月 200 則，每天 1 則足夠。LINE Notify 已於 2025-03-31 停止服務，故使用 Messaging API |
| **Telegram** | `TELEGRAM_BOT_TOKEN`<br>`TELEGRAM_CHAT_ID` | 1. 找 @BotFather 輸入 `/newbot` 取得 token<br>2. 先傳任意訊息給你的 bot，再找 @userinfobot 取得你的 chat id |
| **ntfy** | `NTFY_TOPIC` | 1. 手機安裝 ntfy App（iOS／Android）<br>2. 訂閱一個**難猜**的主題名稱，例如 `twstock-8f3k2q`（知道名稱的人都能看到訊息）<br>3. 把同樣的名稱存成 secret |

設定好後到 **Actions → Morning notify → Run workflow**，勾選 `force` 測試一次。

本機測試：

```bash
python -m app.cli notify --dry-run     # 只印出訊息內容
python -m app.cli notify               # 依 .env 的設定實際發送
```

## 🔁 回測

```bash
python -m app.cli replay --days 60     # 用過去 60 個交易日的資料重新產生清單（一次性，約 30～60 分鐘）
python -m app.cli backtest             # 印出統計
```

每天的清單會自動累積進回測；App 的「回測」分頁、Streamlit 的「🔁 回測」分頁和 `GET /api/backtest` 都看得到。

| 指標 | 定義 |
|---|---|
| N 日後報酬 | 第 N 個交易日收盤價 ÷ 清單日收盤價 − 1 |
| 勝率 | 買入清單：報酬 > 0 的比例；賣出清單：報酬 < 0 的比例（避開成功率） |
| 超額報酬 | 個股報酬 − 同期全市場等權平均報酬 |

**請注意回測的限制：**
- 清單在收盤後才產生，最快隔天才能進場。用「清單日收盤價」當成本會**高估**實際績效，尤其是 1 日報酬。
- 股價未還原除權息，除息日附近的報酬會被低估。
- 重播的歷史新聞只來自鉅亨網（RSS 只有最近幾天），所以歷史清單會比即時清單少一些新聞來源。
- 重播預設只用詞典分析。`--llm` 會用 Claude 分析數千則歷史新聞，請求次數多，請先評估費用。
- 樣本少時（例如只有幾週）統計意義有限，**過去績效不代表未來表現**。

## 評分邏輯

**外資分數**（-1 ~ 1）

```
0.5 × tanh(買賣超金額 / 3 億)          規模：用金額而非張數，避免低價股灌水
0.3 × min(連續天數, 5) / 5 × 方向       持續性
0.2 × tanh(買賣超股數佔成交量 / 10%)    相對強度：對中小型股較公平
```

**外資候選**：連續買／賣超 ≥ 3 日，或單日 ≥ 5 億元，或當日買／賣超金額前 30 名。

**新聞分數**（-1 ~ 1）：`tanh(Σ 權重 × 個股情緒 / 1.5)`，標題就提到該股的新聞權重 1.5。

**綜合分數**：`0.6 × 外資分 + 0.4 × 新聞分`，兩者同向才會列入清單。

所有參數都在 `app/config.py` 的 `ScoringConfig`；改完執行 `python -m app.cli rescore` 就能重算，不必重抓資料。

## 使用 Claude 分析新聞（選用）

在 `.env` 設定 `ANTHROPIC_API_KEY` 後，`run` 會自動改用 Claude（也可用 `--llm` / `--no-llm` 強制切換）。

- 預設模型 `claude-opus-5-5`，effort 設為 `low`，適合這種分類工作。想降低成本，可在 `.env` 設 `ANTHROPIC_MODEL=claude-haiku-5-5`。
- 每批 10 則新聞送一次請求；一天約 100 則新聞就是約 10 次請求。
- 已開啟伺服器端備援模型（`fallbacks: "default"`）：主模型拒答時會自動換模型重跑。
- 相較於詞典，Claude 更能判斷語意，例如「虧損縮小」偏利多、「利多出盡」偏利空，也會為每檔股票寫一句理由。

## 換成 PostgreSQL

```bash
pip install "psycopg[binary]"
# .env
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/twstock
```

## 測試

```bash
pytest -q
```

測試全部離線，使用依官方 JSON 格式撰寫的 fixture；Claude 分析器的測試使用假 client，不會呼叫真實 API。

## 已知限制與注意事項

- **資料來源格式可能變動**：證交所 `TWT38U`、櫃買 `insti/dailyTrade`、鉅亨網 API 都不是正式公開的 API。解析器已依欄位名稱找欄位，並保留櫃買舊版 API 作為備援；若抓取失敗，請用 `-v` 看日誌並更新 `app/collectors/` 與 `tests/test_collectors.py`。
- **工商時報 RSS 網址**未經驗證，抓不到時會被略過，也可在 `.env` 用 `RSS_CTEE=` 換成其他網址。
- **GitHub 排程**：可能延遲數分鐘到數十分鐘，偶爾會被略過，所以設了 18:20 補跑。repo 連續 60 天沒有任何活動時，GitHub 會自動停用排程，需要到 Actions 頁面重新啟用。
- **海外 IP**：GitHub Actions 主機在美國，證交所偶爾會限制海外連線。若 Actions 日誌顯示抓取失敗，可改用上面「在自己電腦排程」的方式。
- **流量限制**：證交所短時間內請求過多會暫時封鎖 IP，預設每次請求間隔 3 秒（`REQUEST_INTERVAL`）。
- **詞典法的限制**：無法理解複雜語意或反諷；需要更好的準確度時請改用 Claude。
- **停損停利以收盤價判斷**：盤中觸及但收盤拉回不會觸發；實際下單請搭配券商的觸價單。跳空跌破時，實際成交價可能比停損價差。
- **權重調整**：回測累積足夠樣本後，可以調整 `ScoringConfig` 的權重與門檻，再用 `rescore`、`backtest` 比較結果。
