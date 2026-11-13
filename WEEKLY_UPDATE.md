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
- `market`：四段，各以 `### ` 開頭，合計 500–1,300 字。
- `market` 的籌碼面要帶到融資維持率：`tw_flow.maint` 是比值（1.48 = 148%，為估算值）、`maint_chg5` 是一週變化
  （0.05 = 5 個百分點）、`maint_cushion` 是整體擔保品距 130% 追繳線還能跌多少。說它是估算，不要寫成官方數字。
- digest 的 `leading` 是領先指標卡片（ADR 溢價、匯率、實質利率、VIX 期限結構、廣度、散戶小台、Put/Call、借券）。
  可在籌碼面引用 `value_text` 與 `change_text`，但它們的偏多／偏空只是市場常見解讀，未經本站資料驗證，
  要寫成「一般解讀為」而不是定論。卡片很多，挑 4–6 個與結論最相關的寫，不要逐一列舉。
- `regime` 是風險環境（`state`：risk_on 風險偏好／risk_off 風險趨避／mixed 輪動觀望，由高收益債利差 `oas`
  與半導體相對強弱 `semis_lead` 判定），在展望或 `etf` 段落交代一句。`options` 是台指選擇權下一個結算日的
  `put_wall`（下檔支撐）、`call_wall`（上檔壓力）、`max_pain`，可在一週展望引用。`trust_first` 是投信首買名單。
- `picks`、`bullish`、`bearish` 每檔有 `tactical`（短線分）、`structural`（結構分）與 `setup`（兩者的組合判讀），
  操作建議中可以用它區分「結構佳但短線弱，等轉強」與「短線強但結構弱，僅宜短打」。`picks_track` 是過去精選名單的實際表現，有資料時在操作建議中如實交代，
  落後大盤也要寫。
- `market` 也要用到 `market_safety`：「本週盤勢重點」帶出風險分數（`risk.score`、`risk.name`，並說明最高的兩三項
  `risk.parts`）與市場週期（`regime.name`、`regime.days`）；「籌碼面」帶出外資台指期（`futures.net_oi`、`futures.chg5`）；
  「操作建議與風險」依 `alerts` 中 level 2 的項目提醒，並可從 `accumulating`、`weakening` 各舉一兩檔。
  `accuracy` 顯示模型區分力有限時（`brier` 不低於 `base_brier`）要照實說，不要把機率寫得比數據更有把握。
- 上漲機率已依回測校準（digest 的 `calibration`：各期間的斜率 `k`），多半落在 47%–53%。寫展望時以標籤與分數描述
  相對強弱，機率只需帶一次並說明它接近五五波的原因；不要把 51% 與 49% 的差距寫成有把握的方向判斷。
  `picks_diff` 是與上一週精選名單的差異（`new` 新進檔數、`dropped` 移出名單），可在操作建議中交代。
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
