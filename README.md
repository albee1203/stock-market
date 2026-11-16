# 台股外資＋新聞輔助分析（tw-stock-advisor）

每天自動整理 **「建議買入」** 與 **「建議賣出／避開」** 的觀察名單：

| 清單 | 條件 |
|---|---|
| 📈 建議買入 | 外資連續買超或單日大買 **且** 當日新聞偏利多 |
| 📉 建議賣出／避開 | 外資連續賣超或單日大賣 **且** 當日新聞偏利空 |

每檔股票都附上一句推薦理由，例如：
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
| `.github/workflows/daily.yml` | 每日自動更新＋發佈 |

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
| `POST /api/run?date=` | 背景執行每日流程 |

## 📱 手機 App ＋ 每日自動更新（免費、免伺服器）

```
GitHub Actions（每個交易日 16:20、18:20 自動執行）
  ├─ 從 data 分支還原資料庫
  ├─ 抓外資、股價、新聞 → 情緒分析 → 評分
  ├─ 匯出靜態 JSON ＋ App（web/）
  ├─ 資料庫存回 data 分支
  └─ 發佈到 GitHub Pages ──▶ 手機開網址 →「加入主畫面」＝ App
```

App（`web/`）是 PWA：可以安裝到手機桌面、全螢幕開啟、離線時顯示上次的資料，並支援深色模式；配色依台股習慣「紅漲綠跌」。共有四個分頁：

| 分頁 | 內容 |
|---|---|
| ★ 清單 | 建議買入／賣出卡片：綜合分、外資分、新聞分、推薦理由、關鍵字 |
| ⇅ 外資 | 外資買超／賣超前 30 名，含張數、金額、佔成交量、連買天數 |
| ≡ 新聞 | 當日新聞與情緒，可篩選利多／利空 |
| ⌕ 個股 | 搜尋任一檔股票，看近 60 日外資買賣超圖與收盤價、相關新聞 |

### 一次性設定（約 3 分鐘）

1. **把程式碼合併到預設分支（main）**：GitHub 的排程只會在預設分支上執行。
2. **開啟 GitHub Pages**：repo → **Settings → Pages → Build and deployment → Source** 選 **GitHub Actions**。
   （免費帳號的 Pages 需要 **public repo**；private repo 需付費方案。）
3. （選用）**用 Claude 分析新聞**：**Settings → Secrets and variables → Actions → New repository secret**，名稱 `ANTHROPIC_API_KEY`。
4. **第一次手動執行**：**Actions → Daily update → Run workflow**，`backfill_days` 填 `10`。
5. 執行完成後，網址會是 `https://<你的帳號>.github.io/stock-market/`。
6. **安裝到手機**：
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
- **回測**：目前只產生每日清單，還沒有驗證歷史績效。建議下一步加上「清單出現後 1／5／20 日報酬」的回測模組，再來調整權重。
