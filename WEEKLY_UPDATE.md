# 每週更新作業手冊

這份文件是給每週三自動執行的 Claude 工作階段看的。照順序做，全部在 `main` 分支上進行，
完成後直接推送到 `origin main`（repo 擁有者已授權，不需要開 PR）。

## 步驟

1. 取得最新程式與資料

   ```bash
   git checkout main && git pull origin main
   ```

2. 安裝套件並跑測試。測試失敗就停下來，不要建置、不要推送，回報失敗內容。

   ```bash
   pip install -r requirements.txt && python -m pytest -q
   ```

3. 建置資料（約 10–25 分鐘；證交所限流時程式會自己暫停與略過，屬正常）。
   指令結束碼不是 0 就停下來，不要推送。

   ```bash
   python -m pipeline.build
   ```

4. 讀 `.cache/digest.json`（本次建置的數據摘要），撰寫 `docs/data/ai.json`，格式與規則見下節。

5. 檢查觀點檔，有 `FAIL` 就修正到通過為止。

   ```bash
   python -m pipeline.check_ai
   ```

6. 提交並推送。只提交資料，不要改程式。

   ```bash
   git add docs/data config/cusip_cache.json && git commit -m "data: weekly update $(date +%Y-%m-%d)" && git push origin main
   ```

   推送因網路失敗時最多重試 4 次（間隔 2、4、8、16 秒）。若被拒絕是因為遠端較新，
   `git pull --rebase origin main` 後再推一次。

7. 用三四句話回報：資料日、哪些資料來源失敗（`docs/data/summary.json` 的 `sources`）、
   精選前五檔、是否已推送。

## 不要做的事

- 不要修改 `pipeline/`、`docs/app.js` 等程式；發現程式錯誤時在回報中說明即可。
- 不要為了通過檢查而捏造數據，也不要引用 `.cache/digest.json` 以外的數字、新聞或事件。
- 不要推送到 `main` 以外的分支，不要改寫歷史（不可 force push）。
- 不要更動排程本身。

## `docs/data/ai.json` 格式

```json
{
  "data_date": "與 digest.json 的 data_date 完全相同",
  "written_at": "撰寫日期 YYYY-MM-DD",
  "market": "### 本週盤勢重點\n…\n### 籌碼面\n…\n### 一週／一月／一季展望\n…\n### 操作建議與風險\n…",
  "podcast": {
    "overview": "本週 Podcast 話題總覽，80–200 字",
    "stocks": { "NVDA": "60–160 字", "2330.TW": "60–160 字" }
  },
  "etf": "### 台股 ETF\n…\n### 美股 ETF\n…",
  "fund": "### 整體觀察\n…\n### 值得留意\n…\n### 需要檢視\n…"
}
```

## 撰寫規則

讀者是台灣的一般投資人，使用繁體中文與台灣用語。

- **只用 digest 裡的數據。** 每個論點都要能對應到 digest 的某個數字；引用時寫出具體數字
  （例如外資近 5 日買賣超金額、融資餘額變化、指數與季線的相對位置）。
- **先講結論。** 每段 2–4 句，不寫客套話，不寫免責聲明（網站已有）。
- **展望是機率，不是保證。** 明說不確定的地方，以及什麼情況會讓判斷失效。
  digest 的 `backtest` 顯示模型預測力有限，語氣要與此相稱。
- `market`：四段，各以 `### ` 開頭，合計 450–750 字。
- `market` 的籌碼面要帶到融資維持率：`tw_flow.maint` 是比值（1.48 = 148%，為估算值）、`maint_chg5` 是一週變化
  （0.05 = 5 個百分點）、`maint_cushion` 是整體擔保品距 130% 追繳線還能跌多少。說它是估算，不要寫成官方數字。
- `podcast.overview`：這兩週節目在談什麼主題（依 `podcast_hot[].episodes` 的標題與摘要），
  以及被談論的標的整體是強是弱。
- `podcast.stocks`：`podcast_hot` 前 8 檔各寫一則（鍵用 `symbol` 欄位，至少要涵蓋前 5 檔）。
  說明節目在什麼脈絡下提到它、目前數據如何、兩者是否一致。業配或工商內容造成的提及要指出來，
  不要當成看多訊號。
- `etf`：兩段。指出最強與最弱的類別與代表 ETF、台股 ETF 的外資動向（`foreign5`，單位張）；
  槓桿與反向型要提醒只適合短線，反向型偏多代表的是看空大盤。
- `fund`：三段，合計 300–600 字，依 digest 的 `funds`（`ret`、`bench`、`beat`、`excess_ann`、`sharpe`、
  `mdd_3y`、`score`、`grade`、`trend`）。「整體觀察」說各類型（`group`）本期的強弱與原因；「值得留意」
  舉 2–3 檔評等高的基金並說明好在哪（同類排名、勝過指數、回檔控制）；「需要檢視」舉 2–3 檔長期落後
  同類或指數的基金。評的是過去績效，不是買賣建議：短期淨值趨勢偏空的高評等基金要一併說明。
  `ret`、`bench`、`excess_ann`、`mdd_3y` 是小數（0.12 = 12%），`beat` 是贏過同類的百分比。
- 文字中不使用表格與粗體，段落之間以換行分隔。
