# 週三選股雷達：指標、功能、預測與示警審查 brief（五隊共用）

使用者要求：「5 stock agent team，review 指標以及功能、預測、示警等等，是否有可以改進的部分」。
這一輪聚焦在**網站現在顯示的每一個指標、預測、警報與功能本身是否正確、誠實、有用**，不是再審外部建議。

## 網站與程式
- Live site https://jonwenjen.github.io/Stock_New/ ，本機 checkout：`/Users/jonwenjen/Projects/Stock_New`（工作樹 = live 網站，HEAD 96e9258）。
  資料日 2026-11-13，今天 2026-11-15（日）。每週三晚上由排程的 Claude session 重建一次：`python -m pipeline.build`，
  Claude 再寫 docs/data/ai.json，由 pipeline/check_ai.py 檢查。
- Python：用 `/Users/jonwenjen/Projects/Stock_New/.venv/bin/python`（已裝 pandas 3.0、numpy、scipy、requests、pytest；89 個測試通過）。
  例：`cd /Users/jonwenjen/Projects/Stock_New && .venv/bin/python -c "from pipeline import market; ..."`。
- 前端：docs/index.html、docs/app.js（~1,970 行）、docs/style.css、docs/knowledge.js（解說條目）。
- 資料：docs/data/*.json（summary、universe、flows、market、leading、etf、funds、podcasts、smart_money、ai、tools）。
  價格：docs/data/charts/<code>.json，`d` = 約 5 年日 K [date,o,h,l,c,v]（已還原除權息），指數/總經也在這（TWII、SPX、VIX、USDTWD、US10Y…）。
  歷史：docs/data/history/（tw_derivs、tw_futures、tw_margins、pit_closes/、stock_flows_tw/、revenue/、weekly/、picks.json、forecasts.json…）。
  **注意：git-ignored 的 .cache/（twse_days 等日表快取）在這台機器上不存在**，只能用 docs/data 裡的資料。
- Pipeline：build.py（流程、records、picks）、scoring.py（tech、backtest、forecast、trade_plan）、market.py（風險分數 v2、regime、
  drawdown_odds、bucket_stats、alerts()、fit_relative、evaluate_index_log）、leading.py（19 張領先指標卡）、research.py（evidence 證據等級、
  selection_bias、portfolio_risk、stress_replay、valuation_band、PREREGISTERED）、prices.py（清理與還原）、twse/tpex/us_data/funds/media。

## 前幾輪已定案（要推翻必須有你算出的新證據）
先讀 `reviews/2026-11-14b/README.md`（五隊共識與實作狀態）與 `reviews/2026-11-14c/README.md`（色彩/元件規則）。
`reviews/2026-11-14d/`（外部 ChatGPT/Gemini 建議審查）正在進行中，**不要重複審那些建議**；你的題目是網站本身。
- 驗證前不改排名權重；新籌碼資料權重 0 直到存檔並檢驗。不加方向標籤、不把預測包裝成目標價、不寫指令句；R:R 已刪。
- 停損是風控不是預測；過熱只標示不扣分。期貨 OI 水位/百分位不能當特徵或警報。分點資料（CAPTCHA）否決。
- 領先指標卡只有證據 A/B 且方向一致才標方向；美股沒有可驗證的選股優勢。紅綠只給價格，警示琥珀加「!」。

## 已觀察到的線索（主持人初步看到的，請查證，不要照單全收）
1. AI 觀點寫「台積電 ADR 溢價 11.7%（B）」，但 leading.json 的 adr 卡 evidence.grade = "C"（rho .093, t 1.93）。check_ai 不比對證據等級。
2. 證據 C/D 的卡片 reading 仍有方向性解讀：pcr「一般解讀為上檔有壓、偏空」、basis「對後市偏樂觀」、adr「常被視為外資回補的前兆」、
   semis「須留意」、top10「大戶部位通常比散戶有參考性」、ad「賣壓普遍」（但 11-13 單日漲 812 跌 200，標題 10 日比 0.75）。
3. 警報（market.alerts）全是固定門檻，沒有證據等級、沒有歷史命中率；「廣度一個月內大幅惡化」用的廣度在風險分數裡已因「五年沒有資訊」降權到 5。
4. 指數展望：index_outlook 的 p_up（.58/.639/.685）與 base（n=141，.596/.706/.868）是兩套「上漲比率」；頁面一處寫 68%、一處 69%，
   7,886 vs 7,887。forecasts.json 的 index_log 是空的、accuracy.live n=0：指數區間的涵蓋率從未被驗證。
5. 風險分數各等級（bucket_stats）：「中等」1q 上漲 89%、「高」83%，皆高於「低」78%；偏高 n=47、高 n=42 是重疊的日資料，獨立事件數可能只有個位數。
6. 品質報告：VIX 清理刪掉了「異常跳動」2020-03-12、2020-03-16、2024-08-05（+65%）等——這些是真實的 VIX 暴衝。若屬實，波動率分項、vixts 證據、
   風險分數歷史都被汙染。
7. 同一頁出現三種廣度數字：廣度指數 34、月線上 38%、季線上 40%（另說「觀察名單中 40% 站上季線」）。
8. 美元／台幣卡「資料日 11/06（Fed 公布有數日落差）」，但頁首磚與 charts/USDTWD.json 可能已有 11/13 的值。

## 硬性限制
1. 純靜態 GitHub Pages：沒有伺服器、資料庫、登入；每週重建一次；不用 GitHub Actions、付費 API、API key。需要 LLM 的只能在每週 Claude session 內做，且要能被腳本檢查。
2. 誠實低量抓資料：單一自我表明的 User-Agent、節流、被拒就退。TWSE www 約 6 秒 1 次。新來源要算每週增加的請求數。
3. 讀者是台灣散戶，繁體中文；不構成投資建議；模型預測力弱，網站要一直誠實地這樣說。

## 禮節（五隊平行）
- **不得修改** /Users/jonwenjen/Projects/Stock_New 底下任何檔案，只能寫你自己的輸出檔 `reviews/2026-11-15/r1_<team>.md`。不 commit、不 push。
  不跑 `python -m pipeline.build`。可以 import pipeline 模組、在本機資料上呼叫純函式。暫存檔放 `/Users/jonwenjen/.hermes/cache/scratch/stock_<team>/`。
- 外部網路：總共最多 10 次請求；TWSE/TPEx/TAIFEX/MOPS 每個最多 3 次、依序、間隔 ≥ 6 秒。寫明你請求了什麼、回了什麼。優先用本機資料。
- 看網站：可用 live 網址，或 `.venv/bin/python -m http.server <你的 port> --directory docs`（只用你自己的 port）。
- 要省：只算足以決定結論的數字。

## 交付（繁體中文 Markdown，技術名詞可英文，≤ 12,000 字）
1. **一句話結論**：從你這隊的角度，這個網站最該改的一件事。
2. **錯誤與資料問題**：每項附證據（file:line、JSON 路徑與值、算出的數字）。分 嚴重／中／輕。查證上面的線索屬於你的範圍的部分：寫「確認」「部分確認」「不成立」並附證據。
3. **改進提案（最多 8 項，依優先排序）**：每項寫 名稱／類型（修錯・改善・新增・刪除）／位置（檔案與函式、頁面）／決策價值（散戶的哪個決定）／
   精確定義（可重現）／資料來源與每週請求數／目前證據（你算的數字）／優先級 P0–P2／成本 S・M・L／要加的單元測試。
4. **建議刪除或簡化**。
5. **給其他隊的問題**：最多 2 個。
數字勝過意見：用本機資料算出的數字，排在「聽起來合理」之前。

## 分工（每隊主審，其餘可以評論）
| 隊 | 主審範圍 |
|---|---|
| quant 量化 | 所有「預測」：指數一週/一月/一季 ±1σ 區間與上漲比率（scoring.forecast、build.py index_outlook、market.evaluate_index_log）、個股 p_beat 校準與 backtest、證據等級方法（research.evidence：19 卡 × 多個目標的多重檢定、樣本重疊）、風險分數各等級統計的有效樣本數、預先登錄檢驗的設計 |
| chips 籌碼 | 籌碼與衍生品指標：外資現貨分項、融資維持率估算、散戶小台、十大交易人、基差、P/C、外資選擇權、未平倉牆與 Max Pain、借券、外資法人/融資融券頁；每張卡的數值是否算對、解讀文字是否超出證據 |
| fund 基本面與資料 | 資料品質與基本面指標：prices.py 清理規則（含 VIX 線索 6）、除權息還原、資料日落差（線索 8）、財報指標頁、valuation_band、營收動能、ETF 與基金評分的算法是否正確 |
| risk 風控與示警 | 示警系統：market.alerts() 每條規則的門檻、等級、重複計分、歷史命中率（能算的就在本機資料回測：觸發後 20 日內跌逾 5% 的比例 vs 基準）、缺漏的警報；風險分數 v2 各分項與 lin() 門檻、持股水位、部位預算、停損 |
| product 產品與一致性 | 功能與呈現：逐頁走一遍（桌機＋手機寬度），同一數字在不同處是否一致（線索 4、7）、AI 觀點與資料是否一致（線索 1，並提出 check_ai 可自動比對的規則）、警報與預測的呈現是否讓散戶誤解、功能是否找得到、無用或重複的區塊 |
