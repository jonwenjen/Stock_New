# 週三選股雷達：產品與一致性審查（product 隊第一輪）

> 審查時間：2026-11-15｜資料日：2026-11-13｜實測：`.venv/bin/python` + Playwright/Chrome（`1280×800` 與 `390×844` 走完 15 頁、比較頁與 3 抽屜），截圖與 `crawl_report.json` 存於 `/Users/jonwenjen/.hermes/cache/scratch/stock_product/`。

## 1. 一句話結論

**最急迫的問題是「同一數字與規則在前端、AI 觀點與解說頁各講一套，且 `check_ai.py` 只對日期不對內容」**；改版與重跑後舊文案與過時分數照常發布，讓散戶在同一頁看到彼此打架的數據。

## 2. 錯誤與資料問題

### 2.1 BRIEF 線索查證

- **線索 1（確認）**：`docs/data/ai.json:4` 寫「`台積電 ADR 溢價 11.7%（B）`」，但 `docs/data/leading.json` `cards[?key=='adr']` 為 `evidence.grade = "C"`（`text: "無證據"`, `n: 1216`, `rho: 0.093`, `t: 1.93`）。`pipeline/check_ai.py:19-54` 未讀取 `leading.json` 與數值檔，只對 `data_date`、字數、`hot` 清單與 5 個停用詞。
- **線索 4（確認）**：
  1. `docs/data/market.json` `index_outlook` 中 TWII 有兩套上漲比率：`p_up`（5 年全體）`0.58 / 0.639 / 0.685` 與 `base`（多頭回檔，`n=141/136/136`）`0.596 / 0.706 / 0.868`，60 日差 18 pp；`docs/app.js:1549` 誤稱兩者差距代表「模型短線訊號與長期統計不一致」，實際兩欄皆為歷史統計。
  2. `docs/data/summary.json` `TWII.horizons["1q"].p_up = 0.685`、`SPX.horizons["1m"].high = 7886.5`。`pipeline/build.py:2808` 用 Python `:.0%` 與 `:,.0f`（銀行家取整）寫進 `narrative` 成 **`68%`** 與 **`7,886`**；同頁 `docs/app.js:21-24, 553-556` 用 JS `Intl.NumberFormat`（半進位）顯示 **`69%`** 與 **`7,887`**（`desktop_overview.png`）。
  3. `docs/data/history/forecasts.json` `idx` 已存 2 期（2026-11-11 起），但 `market.json` `accuracy.index_log` 為空（`0` 筆）、`accuracy.live` 1w/1m/1q `n = 0`。
- **線索 7（確認）**：`#overview` 同頁出現三種廣度：`ai.json:4` 寫「`觀察名單廣度 34`」（`market.json` `breadth.value = 34.2`）；`summary.json` `narrative["籌碼面"]`（`pipeline/build.py:2801`）寫「`觀察名單中 40% 的個股站上季線`」（`above60 = 0.4034`）；`leading.json` `breadth_tw` 寫「`月線上 38%・季線上 40%`」（`above20 = 0.3782`、`above60 = 0.4034`）。首頁未說明 `34` 是 `(37.82+40.34)/2 × 0.8 + 20% × AD調整`（`pipeline/market.py:74-77`）。

### 2.2 其他嚴重／中／輕問題

#### 【嚴重】
1. **`ai.json` 基金分數全錯、Podcast 數值嚴重落差**：`ai.json` 撰於 `2026-11-14`，`docs/data/*.json` 於 `2026-11-15 09:59` 重建且基金改 `rescore()` 後未重寫 `ai.json`，`check_ai.py` 仍放行。
   - **基金（`ai.json:19` vs `funds.json`，`desktop_funds.png`）**：施羅德台灣樂活中小 `88.7` vs `90.1`（今年 `+109%` vs `+113.3%`）、安聯台灣科技 `88.9` vs `85.5`、富蘭克林生技 `81.7` vs `77.3`、摩根印度 `16.7` vs `6.4`、摩根美國科技 `29.7` vs `12.3`、安聯 AI 人工智慧 `30.0` vs `12.0`、群益馬拉松 `41.7` vs `28.4`，7 檔全錯（最大差 17.4）。
   - **Podcast（`ai.json:7-16` vs `universe.json`，`desktop_podcast.png`）**：`AMD` 寫週 `+44.4%`、PE `127`、「`綜合評分強勢偏多`」，實為 `+19.0%`、PE `116.1`、`+0.172`（`偏多`）；`2330.TW` 寫「`綜合評分中性`」，實為 `+0.266`（`偏多`）；`INTC` `+16.9%/-35.9%` vs `+6.0%/-33.6%`；`GOOGL` `+5.7%/-14.4%` vs `+3.8%/-13.2%`；`AMZN` 月 `+5.0%` vs `+6.5%`；`etf` `QQQ` `-4.3%` vs `-4.2%`。
2. **`#compare` 走勢圖 0px 空白**：`docs/app.js:996-1010` 建圖未設 `autoSize: true` 且未等 `chartBox` 掛進 DOM。首頁停留逾 1.2 秒讓 `chartLib()` 預載（`1936` 行）再切 `#compare?c=...` 或按「＋比較」，`clientWidth === 0` 致 `canvasW = 0`（`desktop_compare_filled.png`），且未註冊進 `charts`/`liveCharts`。
3. **校準表「期間」整欄空白**：`docs/app.js:89` `rows.push({ mk, k, ...v })`，`summary.json` `calibration` 含 `"k": 0.0396` 覆蓋 `k: "1w"`，使 93 行 `HZ[r.k]` 為 `undefined`，`#safety` 與 `#method` 6 列「期間」全空（`desktop_safety.png`、`desktop_method.png`）。
4. **基金評分規則「一國三制」**：`pipeline/funds.py:207-261` `rescore()` 為 5 面向（`peer 35%、excess_cat(同類平均) 20%、sharpe 20%、drawdown 15%、consistency 10%`）扣管理費（驗證施羅德台灣樂活中小：`0.35×97.8+0.20×100+0.20×96.7+0.15×83.3+0.10×100-6.0=90.065→90.1`）。僅 `fundDrawer`（`app.js:589`）與 `knowledge.js:504` 正確；`#funds`（`app.js:1471`）、`#method`（`1813`）與 `knowledge.js:491,496,498` 仍寫舊 4 面向（`40/20/25/15`、對比指數），`#funds` 表格只列 `excess_ann` 不列 `excess_cat`。

#### 【中】
5. **退場之「方向標籤、指令句、紅綠分數、單看外資期貨」多處殘留**：
   - `pipeline/build.py:2736-2749`（`hot_view`）仍在 `#podcast` 輸出「`同向偏多…拉回可留意`」「`數據偏空…反彈宜減碼`」（含無優勢美股 `AAPL/AMD/NVDA/GOOGL`）。
   - `pipeline/build.py:2799` 仍在 `#overview` 寫「`外資台指期淨未平倉 -100,870 口（偏空避險）`」，與同頁 `AI 觀點`、`#safety` 矛盾；`#flows`（`app.js:1051`）缺投信 `+99,923` 與合計 `-3,718`。
   - `docs/app.js:1273-1276, 1403-1406, 1497-1498, 1616-1617, 1643, 1681` 仍以 `numEl` 將分數染紅綠（`desktop_alerts.png`、`desktop_podcast.png`），違背 `2026-11-14c` 規則；`#smart`（`1203-1205`）也把議員筆數染紅綠。
   - `pipeline/scoring.py:367-377` 仍產 `基本面與籌碼同步偏多`（26 檔）；`docs/knowledge.js:79` 仍寫 `上漲機率`、`:83` 寫 `不含交易成本`（與 `#picks:961` 扣 `0.585%` 矛盾）、`:515` 寫「`紅色系偏多、綠色系偏空`」（與 `:88` 矛盾）。
6. **`#alerts`（「漲跌預警」）重複欄位且誤導**：`docs/app.js:1614-1615` 並排 `rankCol` 與 `{key:"2", label:"同市場排名"}`，實見相鄰重複「同市場排名」（`desktop_alerts.png`）。此頁實為美股 60 檔與台股 119 檔前後 12 名混排，無突發警報且與 `#picks`/`#fund` 重複。
7. **`#method` 與 `knowledge.js` 公式過時**：`docs/app.js:1777` 寫「`預測區間＝現價×(1＋偏移±σ)…綜合分數＝一週30%＋一月40%＋一季30%`」，實則 `pipeline/scoring.py:345` `exp_ret = 0.0`（無偏移，與 `knowledge.js:43` 矛盾）且三期間同權重；`knowledge.js:48-50, 519` 仍稱「短線分／結構分」。
8. **手機（390px）與桌機表格溢出**：`docs/style.css:414` sticky 首欄未設 `max-width`，使 `mobile_fund.png` 第 4 欄「綜合分」被切、`mobile_method.png` 首欄占滿螢幕（手機 `#smart` 17 表、`#safety` 14 表、`#method` 7 表溢出；桌機 `#method`「進度」與 `#etf`「外資 5 日」被切）。
9. **`plan.target` 與 `+1σ 上緣` 不等**：`pipeline/scoring.py:438` `max(f1m["high"], c + 2*atr)` 使 `6274.TWO 台燿` `target = 1192.11` ≠ `1m.high = 1190.65`。

#### 【輕】
10. **Console 404 與結構缺漏**：首頁實測 1 筆 `404`（缺 favicon 且 `manifest.webmanifest` 無 `icons`）；`#overview` 缺 `<h1>`（`h1: null`）；`fundDrawer`（`app.js:618-625`）漏改 `document.title`；`app.js:1525` 警報摘要漏計 `level===0`；`app.js:1461` 基金名 `slice(0,14)` 斷尾「`- （46）`」；手機隱藏 `#gsearch`（`style.css:470`）且 `#fund` 搜尋不含 `FUNDS`。

### 2.3 散戶誤解、找不到的功能與重複區塊
- **誤解**：`#overview` 仍名「總覽與預測」、標題寫「趨勢預測」；`#picks` 混排台美股 `#1–#20`（`#13 波克夏 +0.84` > `#1 麗臺 +0.75`）且預設分 `40%` 預算給無優勢美股，12 檔台股有 9 檔標 `!先觀察` 卻仍預設配資；基金抽屜 ±1σ 易誤認成目標區間。
- **找不到**：行事曆埋在「輿情與知識 → 方法與來源」（`#method`），iCal/Atom 卻在 `#overview`；「照著精選操作的實際結果」與「過去的預測準不準」沉於頁底（桌機 5,200+ px、手機 10,000+ px）。
- **重複**：`#overview` 重複三段大盤總結；`regimePanel()`（兩張 C 級卡）在 `#etf` 與 `#funds` 重複且已見於 `#overview`；`retail/pcr/sbl` 在 `#overview` 與 `#margin` 重複；`calibNote()` 在 `#safety` 與 `#method` 重複（且全空）。

## 3. `pipeline/check_ai.py` 自動比對規則

在 `check_ai.py` 直接讀取 `docs/data/{summary,market,leading,flows,etf,funds,universe}.json`（0 外部請求），新增四類確定性檢查：
1. **證據等級（`leading.json`）**：建立 19 張卡名稱／別名表，正則掃描 `ai["market"]` 指標名附近 25 字內的 `(?:證據\s*)?([ABCD])(?:\s*級)?` 或 `（([ABCD])）`，必須等於 `evidence["grade"]`（攔下 `ADR 溢價（B）` vs `C`）。
2. **數值比對**：
   - `ai["market"]`：比對 TWII 收盤、風險分數、外資 5/20 日與投信 5 日、期貨外資/投信/合計 OI、維持率、廣度 `breadth.value`、TWII ±1σ 區間與 half-up `58%/64%/69%`（容許 ±0.6 或取整一致）。
   - `ai["fund"]`：匹配 `funds.json` 名稱後抽出括號分數與今年/3 年報酬，要求與 `score` 差 `≤ 0.6`、報酬差 `≤ 1.0pp`（攔下 7 檔錯分）。
   - `ai["podcast"]["stocks"][sym]`：依 `sym` 比對 `universe.json` 的週/月報酬（容許 `≤ 1.5pp`）、RSI（`≤ 2`）、PE（`≤ 3`）、EPS/營收年增（攔下 `AMD` 44.4% vs 19.0% 等 5 檔）。
   - `ai["etf"]`：依代號比對 `etf.json` `chg5d` 與 `r20`（`≤ 0.5pp`）。
3. **等級名稱**：比對 `ai["market"]` 引號內風險等級與 `market.risk.name`、週期與 `regime.name`；比對 `ai["fund"]` 基金評等與 `funds.json` `grade`。
4. **退場用語／指令句**：個股段禁用 `綜合評分(強勢)?偏[多空]`（攔下 `2330.TW` 中性、`AMD` 強勢偏多），停用詞納入 `宜減碼`、`宜買進`、`目標價`。

## 4. 改進提案（8 項，依優先排序）

### product-1：擴充 `pipeline/check_ai.py` 證據等級、數值與等級名稱自動檢查
- **類型**：修錯＋改善｜**位置**：`pipeline/check_ai.py::validate, main`｜**優先級**：**P0**｜**成本**：**M**
- **決策價值**：確保 AI 解讀與頁面數據一致，不被舊分數或幻覺漲跌幅誤導。
- **精確定義**：實作第 3 節四類檢查；本週重跑產出更新 `docs/data/ai.json`。
- **來源／每週請求**：本機 `docs/data/*.json`，**0 請求**。
- **目前證據**：實測 `ai.json` ADR 錯級（B→C）、7 檔基金全錯（差 1.4–17.4 分）、5 檔 Podcast 數值不符（`AMD` 差 25.4pp）。
- **單元測試**：`tests/test_core.py` 測證據等級錯、基金分數差 >1、Podcast 誤差超限、個股出現 `綜合評分偏多` 均報錯。

### product-2：修正四捨五入分歧（68%/69%、7,886/7,887）、`calibNote` 空白欄與 `plan.target`
- **類型**：修錯｜**位置**：`docs/app.js:89, 93, 1549`；`pipeline/build.py:2808`；`pipeline/scoring.py:438`｜**優先級**：**P0**｜**成本**：**S**
- **決策價值**：消除同頁數字打架與空白校準表。
- **精確定義**：`app.js:89` 改 `{ mk, horizon: k, ...v }`（93 行用 `HZ[r.horizon]`）；`build.py:2808` 改 half-up 取整；`app.js:1549` 改稱「目前週期歷史統計與 5 年平均之差」；`scoring.py:438` `target = f1m["high"]`。
- **來源／每週請求**：本機資料，**0 請求**。
- **目前證據**：TWII `p_up=0.685`（68% vs 69%）、SPX `high=7886.5`（7,886 vs 7,887）、校準表「期間」6 列空白、`6274.TWO` `target` 差 `1.46`。
- **單元測試**：測 half-up 輸出 `69%` 與 `7,887`、`calibNote` 保留 `horizon`、`plan.target == horizons["1m"]["high"]`。

### product-3：修復 `#compare` 走勢圖 0px 空白 Bug 並納入主題／釋放管理
- **類型**：修錯｜**位置**：`docs/app.js:996-1011`（`views.compare`）｜**優先級**：**P0**｜**成本**：**S**
- **決策價值**：讓從總覽或抽屜點進「比較」都能看到 2–4 檔相對走勢圖。
- **精確定義**：`createChart` 啟用 `autoSize: true`（若 `!chartBox.isConnected` 則延至 `requestAnimationFrame` 後建圖），註冊至 `charts` 與 `liveCharts`。
- **來源／每週請求**：本機資料，**0 請求**。
- **目前證據**：由 `#overview` 切 `#compare?c=2465.TW,6199.TWO` 時 `canvasW = 0`（`desktop_compare_filled.png`）。
- **單元測試**：Playwright 從 `#overview` 導航 `#compare?c=...` 斷言 `canvas.width > 0`。

### product-4：清除「紅綠分數、指令句、偏多偏空、單看外資期貨」與舊基金說明
- **類型**：修錯＋簡化｜**位置**：`docs/app.js`（多表 `numEl`、`1051`、`1447`、`1471`、`1777`、`1813`）、`docs/knowledge.js`、`pipeline/build.py:2736-2749, 2799`、`pipeline/scoring.py:367-377`｜**優先級**：**P0**｜**成本**：**M**
- **決策價值**：消除「不做預測不給指令、紅綠只給價格」承諾與實作的矛盾。
- **精確定義**：分數欄與議員筆數改中性字色；`hot_view` 與 `setup_tag` 去方向標籤與買賣指令；`build_narrative` 與 `#flows` 並列外資／投信／合計 OI；`#funds`/`#method`/`knowledge.js` 同步為 `rescore()` 5 面向與「三期間同分、無偏移」。
- **來源／每週請求**：本機資料，**0 請求**。
- **目前證據**：`docs/data/*.json` 含 `偏多`/`偏空` 1,286 次、`強勢` 704 次；6 頁分數欄仍染紅綠；基金說明差 1 維度與 5–10% 權重。
- **單元測試**：測 `hot_view`/`setup_tag` 無停用詞、`build_narrative` 含投信與合計 OI、檢查 `app.js` 分數欄不用 `numEl`。

### product-5：整併 `#alerts`（「漲跌預警」）並補強 `#safety` 警報呈現
- **類型**：刪除＋改善｜**位置**：`docs/app.js:103, 1525, 1609-1628`｜**優先級**：**P1**｜**成本**：**S**
- **決策價值**：在單一位置看懂真實警報（含改善項與證據等級），不被假預警誤導。
- **精確定義**：先刪 `#alerts` 重複排名欄，並將「漲跌預警」從頂部導覽移除（`#alerts` 導向 `#fund`）；`#safety` 警報計數補 `level===0` 並標註觸發指標證據等級（如廣度 D 級、匯率 A 級）。
- **來源／每週請求**：本機資料，**0 請求**。
- **目前證據**：`#alerts` 實為台美股混排前後 12 名（無警報），且有雙重排名欄（`desktop_alerts.png`）。
- **單元測試**：驗證 `#alerts` hash 重導與 `level 0+1+2` 計數相符。

### product-6：精簡 `#overview` 重複敘事、統一廣度口徑、改「預測」字樣並補 `<h1>`
- **類型**：改善＋簡化｜**位置**：`docs/app.js:103, 794-862`；`pipeline/build.py:2756-2823`｜**優先級**：**P1**｜**成本**：**S**
- **決策價值**：消除首頁 3 種廣度混淆與「趨勢預測」誤解。
- **精確定義**：補 `#overview` `<h1>`；「總覽與預測」「趨勢預測」改為「市場總覽」「指數波動區間與歷史統計」；廣度統一印「`廣度指數 34（月線上 38%・季線上 40%）`」；`AI.market` 有效時將 `S.narrative` 收折為 `<details>` 備援。
- **來源／每週請求**：本機資料，**0 請求**。
- **目前證據**：`#overview` `h1: null`，桌機長 `5,342 px`、手機 `11,466 px`，同頁印 3 種廣度與 2 套敘事。
- **單元測試**：Playwright 驗證 `#overview` 有唯一 `<h1>` 且不含衝突的 `68%`/`69%`。

### product-7：修復手機表格溢出、補 SVG favicon/PWA、基金抽屜標題與手機搜尋
- **類型**：修錯＋改善｜**位置**：`docs/style.css:414, 470`、`docs/index.html`、`docs/manifest.webmanifest`、`docs/sw.js`、`docs/app.js:624`｜**優先級**：**P1**｜**成本**：**S**
- **決策價值**：讓手機（390px）讀得到所有表格與基金搜尋、消除 404 並保留抽屜標題。
- **精確定義**：`style.css` 手機版限制 sticky 首欄 `max-width: 44vw; white-space: normal`（長字表取消 sticky）；新增 `docs/icon.svg` 寫入 `index.html` 與 `manifest.webmanifest`，`sw.js` 補 `knowledge.js`；`fundDrawer` 設 `document.title`；手機頁首或 `#funds` 加搜尋入口。
- **來源／每週請求**：靜態檔，**0 請求**。
- **目前證據**：手機 9 頁表格遮擋次欄（`#smart` 17 表、`#method` 7 表）、首頁 `console` 404、`drawer_fund.title` 未更新。
- **單元測試**：Playwright 驗證全站 `consoleErrors === 0`、基金抽屜標題正確、手機 `#method` 首欄 `< 200px`。

### product-8：分離 `#picks` 台美股排名、`部位預算` 台股預設 100%、並置頂實績與行事曆連結
- **類型**：改善｜**位置**：`docs/app.js:874, 924-937, 965-968`｜**優先級**：**P2**｜**成本**：**S**
- **決策價值**：避免散戶把無優勢美股當高分首選並預設配置 40% 資金，並讓實績與行事曆一鍵直達。
- **精確定義**：`#picks` 分段顯示「台股精選 12 檔」與「美股相對強勢觀察 8 檔（無可驗證優勢）」；`budget_tw` 預設改 `100`；`#picks` 頂部並排「照著操作實際結果」錨點；`#overview` 漏斗支援就地展開或直跳行事曆錨點。
- **來源／每週請求**：本機資料，**0 請求**。
- **目前證據**：`#13 波克夏`（`+0.84`）高於 `#1 麗臺`（`+0.75`），預設分 `24 萬` 給美股且 `LLY`/`COST` 顯示「不足 1 股」；台股 12 檔有 9 檔標 `!先觀察`。
- **單元測試**：驗證 `budget_tw` 預設 `100` 及 `#picks` 分區存在。

## 5. 建議刪除或簡化

1. **刪除獨立分頁 `#alerts`（「漲跌預警」）**：內容重複且誤導，併入 `#fund` 並保留 hash 導向。
2. **簡化 `#overview` 雙軌敘事**：`ai.json` 有效時將 `S.narrative` 收折為備援摘要，避免同頁雙倍篇幅與小數位數打架。
3. **簡化 `#etf` 與 `#funds` 的 `regimePanel()`**：改為單行狀態提示，不再重複兩張 C 級（無證據）卡片。
4. **移除基金抽屜 ±1σ 虛線錐**：基金淨值無盤中撮合且不含配息，對持有決策無實益。
5. **清除 `pipeline/scoring.py` 廢棄欄位**：停寫前端已停用的 `stance`、`rr` 與 `label`（`偏多/偏空`）。

## 6. 給其他隊的問題

1. **給 `quant` 隊**：首頁與 `#safety` 同時出現「5 年全體上漲比率（58%/64%/69%）」與「同週期上漲比率（60%/71%/87%）」，皆為歷史統計卻差 18 pp。從統計表達上，該保留單一口徑並改稱「近 5 年收紅比例」，還是如何呈現才不致誤解？
2. **給 `risk` 隊**：台股精選 12 檔有 9 檔觸發「`!先觀察`」（如停損距離 `33.6%`、股東僅 `2,948` 人），但預算表仍預設配資給它們。從風控與產品立場，「`!先觀察`」個股在預算表應預設歸零手動勾選，還是移出主名單列候補？
