# 即時股價轉接站（Cloudflare Worker）

App 是放在 GitHub Pages 的靜態網頁，瀏覽器不允許它直接讀取證交所的資料。
這個 Worker 代為查詢證交所「基本市況報導」（盤中約 5 秒更新），再交給 App。

- 費用：免費方案每天 10 萬次請求，個人使用綽綽有餘
- 只允許你的 App（`https://albee1203.github.io`）呼叫，其他網站會被拒絕
- 相同查詢 10 秒內共用結果，減輕證交所負擔

## 部署步驟（網頁操作，約 10 分鐘）

1. 到 https://dash.cloudflare.com/sign-up 註冊（免費）並登入。
2. 左側選單 **Compute (Workers) → Workers & Pages**（舊版介面叫 **Workers & Pages**）→ **Create** → **Create Worker**（或 **Start with Hello World!**）。
3. 名稱填 `tw-quote` → **Deploy**。
4. 部署完成後按 **Edit code**，把編輯器裡的內容全部刪掉，貼上 [`quote-proxy.js`](quote-proxy.js) 的全部內容 → 右上角 **Deploy**。
5. 複製上方顯示的網址，例如 `https://tw-quote.你的帳號.workers.dev`。
6. 用瀏覽器打開 `網址/?q=tse_2330`，看到含 `"2330"` 與 `"price"` 的文字就成功了（非交易時間會是最後成交價）。
7. 把網址填進 repo 的 `web/config.json`：
   ```json
   { "quote_proxy": "https://tw-quote.你的帳號.workers.dev" }
   ```
   提交後，下一次排程發佈就會生效。

## 用指令部署（選用）

```bash
cd worker
npx wrangler login
npx wrangler deploy
```

## 測試

```bash
node --test worker/quote-proxy.test.mjs
```

## 設定（選用）

Worker → **Settings → Variables and Secrets** 新增 `ALLOWED_ORIGINS`（逗號分隔），可改變允許呼叫的網站。
