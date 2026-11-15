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
   git add docs/data docs/calendar.ics docs/feed.xml config/cusip_cache.json && git commit -m "data: weekly update $(date +%Y-%m-%d)" && git push origin main
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
  "digest_sha": "與 digest.json 的 digest_sha 完全相同（同一天重建後也會改變，必須重寫）",
  "written_at": "撰寫日期 YYYY-MM-DD",
  "market": "### 本週盤勢重點\n…\n### 籌碼面\n…\n### 一週／一月／一季展望\n…\n### 部位與風險\n…",
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
- digest 的 `leading` 是領先指標卡片，每張有 `evidence`（`grade` A 有支持／B 證據弱／C 無證據／D 歷史不足，
  `pct` 是目前數值高於一年中多少比例的日子）。只有 A、B 級的卡可以寫方向；C、D 級只能描述數值與它在一年中的位置，
  不可寫成偏多或偏空的訊號。卡片很多，挑 4–6 個與結論最相關的寫，不要逐一列舉。
- `regime` 是風險環境（`state`：risk_on 風險偏好／risk_off 風險趨避／mixed 輪動觀望，由高收益債利差 `oas`
  與半導體相對強弱 `semis_lead` 判定），在展望或 `etf` 段落交代一句。`options` 是台指選擇權下一個結算日的
  `put_wall`（下檔支撐）、`call_wall`（上檔壓力）、`max_pain`，可在一週展望引用。`trust_first` 是投信首買名單。
- `picks`、`bullish`、`bearish` 每檔有 `rank`（同市場排名 [名次, 總數]）、`p_beat_1m`（一月跑贏同市場中位數的機率）、
  `tactical`（籌碼分，只有台股）、`structural`（基本面分）、`setup`（兩者的組合判讀）與 `official_flags`
  （punish 處置、notice 注意、near_punish 可能處置、exdiv 即將除權息）。精選不會有處置股；有其他旗標的要在操作建議中點名。
  台股名單包含成交金額前 30 檔上櫃股（symbol 以 .TWO 結尾），提到時註明「上櫃」。
  `picks_track` 是過去精選名單的實際表現，有資料時在操作建議中如實交代，  落後大盤也要寫。
- `market` 也要用到 `market_safety`：「本週盤勢重點」帶出風險分數（`risk.score`、`risk.name`，並說明最高的兩三項
  `risk.parts`）與市場週期（`regime.name`、`regime.days`）。風險分數衡量的是短期回檔風險，`risk.buckets[].dd20`
  是各等級之後 20 日內跌逾 5% 的歷史比例；它分辨不了之後的報酬，不要寫成漲跌預測。持股水位只能寫成
  「此等級對應的經驗區間（未經驗證）」，不可寫「建議降低持股」之類的指令句。
  「籌碼面」帶出台指期法人部位：外資 `futures.net_oi`、投信 `futures.trust_oi`、三大法人合計 `futures.all_oi`。
  外資淨空單主要是投信槓桿 ETF 多單的對手，不要單獨解讀為外資看空，也不要寫「一年最空」；
  「部位與風險」依 `alerts` 中 level 2 的項目提醒，並可從 `accumulating`、`weakening` 各舉一兩檔。
  `accuracy` 顯示模型區分力有限時（`brier` 不低於 `base_brier`）要照實說，不要把機率寫得比數據更有把握。
- 個股不再有「上漲機率」。digest 的 `calibration` 是各市場各期間跑贏中位數的斜率 `k` 與 `edge`（樣本外是否有優勢）；
  美股 `edge` 為 false 時要寫明「美股排名沒有可驗證的選股優勢」。指數的 `horizons[].p_up` 是近 5 年的歷史上漲比率，
  不是預測，寫的時候要這樣稱呼。`calendar` 是接下來兩週的除權息、處置到期、結算與公布截止日，在「部位與風險」中挑相關的帶一句。
  `picks_diff` 是與上一週精選名單的差異（`new` 新進檔數、`dropped` 移出名單），可在「部位與風險」中交代。
- `weekly` 是和上一個 ISO 週快照的比較（`risk` 的變化與主要原因、`cards` 位置大幅移動的指標、`picks_in`、
  `picks_out` 與原因）。`ref` 為 null 時代表還沒有上週的快照，不要自己推算「上週」。
- 精選有 `watch_first`（先觀察的原因：股東少、平穩急漲、停損過遠）時，在「部位與風險」中點名並說明原因，只寫事實。
- `selection_bias` 是用今日名單回測的高估程度（`today` 對 `pit`）；提到回測或優勢時要說明它是上限。
- `portfolio` 是精選放在一起的風險（`beta`、`vol`、`loss95_1m` 一個月 95% 虧損界線、`avg_corr`），可在「部位與風險」中帶一句。
- 下列字詞已停用，`check_ai` 會擋下：上漲機率、一月目標、風險報酬比、建議降低持股、建議持股，以及
  （2026-11-14d 起）偏多、偏空、看多、看空、觀望、看好、宜等、宜減碼、宜加碼、等止穩、追高、逢低、布局、佈局、
  上車、建議、只適合短線。只描述數據，不下方向判斷、不給操作指令；免責句「不構成投資建議」可以寫。
- **數字必須和本次數據一致。** `check_ai` 會核對 `podcast.stocks` 每一則：寫在「近一週／近一月／近一季／今年／
  EPS 年增／營收年增」後面、帶 % 的數字，以及「本益比」「RSI」後面的數字，四捨五入到你寫的位數後必須等於該檔的
  欄位值；「N 集」必須等於提及集數。`digest_sha` 不同代表數據已重建，整份 ai.json 都要依新的 digest 重寫，
  頁面在兩者相符之前不會顯示 Claude 的文字。
- `follow` 是照精選名單操作的實際結果（事先寫定的規則）；`verdict` 寫「樣本不足」時就照實寫，不要自己推算績效。
  `preregistered` 是預先登錄的檢驗與資料進度，只描述進度，判定時間到之前不要寫結論。
- 建置最後會用每週少量的額度回補 T86、日表、期交所與月營收，日誌裡的 `history backfill` 是進度，被拒絕屬正常。
- 2026-11-14d 起的存檔（都不增加請求，除非另註）：全部上市股每日成交量 `history/pit_volume/`、個股融資
  `history/margin_tw/`、全市場集保 `history/tdcc_all/`、全市場月本益比 `history/valuation/`、Podcast 提及
  `history/podcast_events.json`（第一次執行用 RSS 保留的舊集數一次回補，之後只追加新集）。每週另外重抓上次建置
  最後一天的 T86（證交所會事後更正，+1 次）與上櫃營益分析（+1 次）。每週快照會記下四種背離標記、法人與股價四格、
  產業表與精選的除權息日，供預先登錄的檢驗使用，不顯示。
- `podcast.overview`：這兩週節目在談什麼主題（依 `podcast_hot[].episodes` 的標題與摘要），
  以及被談論的標的整體是強是弱。
- `podcast.stocks`：`podcast_hot` 前 8 檔各寫一則（鍵用 `symbol` 欄位，至少要涵蓋前 5 檔）。
  提及脈絡只能引用 `episodes[].where`（`kind` 是 title 標題／chapter 章節＋時間戳／notes 簡介片段，`text`
  是原文）；標題是問句時照問句寫，不要寫成「看好」。目前數據用 `facts`（事實列）與 `signal`，`weeks` 是連續被提及
  的週數、`prior12` 是之前 12 週有幾週被提及。業配或平台連結造成的提及要指出來。
- `podcast_drift` 是被提及之後的歷史統計（`cells` 依市場與 5／20 日；`adj` 是扣除該股平常表現後的超額）。
  可以在 overview 帶一句，但要說明它是用今天的名單回補、結果偏好，不能寫成預測。
- `etf`：兩段。指出最強與最弱的類別與代表 ETF、台股 ETF 的外資動向（`foreign5`，單位張）；
  槓桿與反向型要說明每日重設的耗損（持有越久，和指數倍數的偏離越大）；反向型分數高，代表的是大盤走弱。
- `fund`：三段，合計 300–600 字，依 digest 的 `funds`（`ret`、`bench`、`beat`、`excess_ann`、`sharpe`、
  `mdd_3y`、`score`、`grade`、`trend`）。「整體觀察」說各類型（`group`）本期的強弱與原因；「值得留意」
  舉 2–3 檔評等高的基金並說明好在哪（同類排名、勝過指數、回檔控制）；「需要檢視」舉 2–3 檔長期落後
  同類或指數的基金。評的是過去績效，不是買賣建議：短期淨值趨勢偏空的高評等基金要一併說明。
  `ret`、`bench`、`excess_ann`、`mdd_3y` 是小數（0.12 = 12%），`beat` 是贏過同類的百分比。
- 文字中不使用表格與粗體，段落之間以換行分隔。
