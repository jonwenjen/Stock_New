# 週三選股雷達：五隊交叉審查 brief（所有審查者共用）

Live site: https://jonwenjen.github.io/Stock_New/. It is served from the `docs/` folder of the git checkout at
/home/user/Stock_New (the working tree is the live site, commit 63a1496). Today is Saturday 2026-11-14 and the data date is 2026-11-13.
Review the local copy:
- Front end: docs/index.html, docs/app.js (~1,900 lines, plain JS), docs/style.css, docs/knowledge.js (96 entries)
- Data the site shows: docs/data/*.json (summary, universe, flows, market, leading, etf, funds, podcasts, smart_money, ai)
- Price store: docs/data/charts/<code>_<TW|TWO|US...>.json. Each file holds `d` (about 5 years of daily rows
  [date, o, h, l, c, v], cleaned and adjusted for ex-rights), `w` (older weekly rows) and `applied` (ex-rights factors already applied).
  There are 304 files.
- Histories: docs/data/history/*.json. Some were back-filled and others started archiving this week:
  tw_derivs (259 days: retail mini-TX, top-10 traders, basis, put/call, foreign TXO calls/puts), tw_futures (267 days of foreign
  TX net OI), tw_universe_pit (201 days of the top 150 listed stocks by traded value), tw_stock_flows and tpex_stock_flows
  (20 days of per-stock institutional flows), tw_revenue (15 months), tdcc / tw_qfii (1 week so far), us_form4, picks, forecasts.
- Local day-table cache (git-ignored): /home/user/Stock_New/.cache/twse_days/<date>.json holds 201 days of every TWSE
  security as {code: [o, h, l, c, volume]}. .cache/tpex_days holds the same for OTC. Use these instead of re-downloading.
- Pipeline (Python 3, pandas): `python -m pipeline.build` runs pipeline/build.py, scoring.py (tech_panel, backtest, factor_ic,
  residual_momentum), market.py (risk score, regime, fit_relative calibration), leading.py, twse.py, tpex.py, prices.py, us_data.py,
  funds.py and media.py. Tests are in tests/test_core.py (78 pass). Docs: README.md, WEEKLY_UPDATE.md (the weekly runbook).
- To view the site locally, run `python3 -m http.server <your port> --directory /home/user/Stock_New/docs` and use Playwright
  (`require('playwright')`; chromium.launch() needs no options). Use only your own port (see your task).

## The previous round (read it first: reviews/2026-11-14/README.md plus the r1_/r2_ files if you need detail)
That round had four reviewers: quant, Taiwan trader, UX and data engineering. Most of its consensus is now implemented:
- Price store integrity: weekends, holidays and spikes removed; TWSE ex-rights adjustment; limit-break bridging.
- Honest probabilities: each stock gets a rank within its own market and a calibrated chance of beating that market's median
  (walk-forward fit). Taiwan has a small, verifiable edge at 1 week and 1 month. The US has none, so its p_beat is 50%.
  For indices the site shows 5-year base rates instead of a model probability.
- Single slow technical score; Taiwan adds distance to the 52-week high. Weights: TW tech .60 / chips .25 / fund .15,
  US tech .80 / chips .05 / fund .15. Podcast buzz has weight 0.
- Picks: 12 TW + 8 US, one cohort per ISO week, kept while still ranked within the top 30 (TW) or top 20 (US). At most 3 per
  industry, no two picks with 60-day correlation of 0.5 or more, disposition stocks excluded. ATR stop and position cap.
  A budget panel scales the position caps.
- Official flags (處置 disposition, 注意 attention, 除權息 ex-rights/dividend), a two-week calendar, a data-quality report,
  and risk colours separate from the up/down colours.
- Backtest (summary.json `backtest`): TW IC .037 / .064 / .074 at 1w / 1m / 1q, US .025 / .020 / .036.
  Calibration is in summary.json `calibration`.

Added since then (this week). All of these are display-only with weight 0:
- 30 OTC (TPEx) stocks: the largest by traded value, now ranked together with the listed stocks. Symbols end in .TWO.
- Foreign holding % with its 20-day change and the room left to the legal limit (MI_QFIIS and TPEx).
- TAIFEX history back-filled for one year: retail mini-TX position, top-10 traders, basis, put/call, and foreign TXO call/put net OI.
- SEC Form 4 open-market insider trades for the 60 US stocks.
- The watchlist accepts funds. The mobile masthead folds on scroll. Target, support and stop lines on the K-line chart.

The site now has 14 views in 5 groups (市場總覽: 總覽與預測 / 市場安全 / 漲跌預警; 核心選股: 精選 20 檔 / 財報指標 / 自選;
籌碼與聰明錢: 外資法人 / 融資融券 / 名人・議員・董監; 資產配置: ETF / 基金; 輿情與知識: Podcast / 指標與模型解說 / 方法與來源).
There are 19 leading-indicator cards (liq, otc, adr, fx, real, curve, hy, semis, vixts, breadth_tw, breadth_us, ad, retail, top10,
basis, pcr, fo, optwall, sbl), a 0-100 market risk score, a regime and alerts.
The universe is 179 stocks (120 TW, of which 30 are OTC; 59 US), plus 38 ETFs and 39 funds.

Settled decisions. Do not re-open these without new evidence:
- Overheated stocks are not penalised, only flagged.
- New chip data gets weight 0 until it has been archived and tested.
- The holding-level rule of thumb stays labelled as unverified.

## Hard constraints (every proposal must respect them)
1. Static GitHub Pages only: no server, no database, no login. Data is rebuilt ONCE A WEEK (Wednesday night) by a scheduled
   Claude session on subscription quota. No GitHub Actions, no paid APIs, no API keys.
2. Data access must be honest and low-volume: one self-identifying User-Agent, throttling, and backing off on refusal. Never
   propose rotating User-Agents, proxies, CAPTCHA solving or anything else that evades rate limits or bot checks.
   That rules out TWSE broker-branch (分點) data behind a CAPTCHA.
3. TWSE www endpoints throttle at about 1 request per 6 s and answer HTTP 307 when they are unhappy. A full weekly build already
   takes 25-40 minutes. Count the extra weekly requests that any new data source would add.
4. Audience: Taiwanese retail investors. Traditional Chinese UI. Red = up (switchable).
5. Nothing on the site is investment advice. The model's predictive power is weak and the site must keep saying so honestly.

## Etiquette for this review (you run in parallel with four other reviewers)
- Do NOT modify any file under /home/user/Stock_New except your own output file(s) in /home/user/Stock_New/reviews/2026-11-14b/.
  Do not commit or push. Do not run `python -m pipeline.build` (it rewrites the data and hits the network for 30+ minutes).
  You may import pipeline modules and call pure functions on local data.
- External network: at most 20 requests in total. At most 5 to any one of TWSE / TPEx / TAIFEX, sequential, 6 s or more apart.
  Prefer the local caches and histories. If you test a new endpoint, say exactly what you requested and what came back.
- Put scratch scripts in your own temporary directory, not in the repo.

## Your deliverable
Write Markdown to the file named in your task, in Traditional Chinese (technical names may stay English). Keep it under about
20,000 characters. Use this structure:
1. **一句話結論**: the single most important change for this site, from your team's point of view.
2. **發現的錯誤或資料問題**: each with evidence (file:line, a JSON path with the value, a screenshot path or a computed number).
   Write 「沒有發現」 if there are none.
3. **提案（最多 10 項，依優先排序）**: for each one give
   名稱 / 類型（新指標・新功能・改善・刪除）/ 放在哪裡 / 決策價值（具體到台股或美股散戶的哪個決定）/
   精確定義（可回測、可重現的算法）/ 資料來源與可行性（端點、免費與否、更新頻率、每週新增請求數；實測過寫「已驗證」，否則寫「未驗證」）/
   目前證據（你在本機資料上算出的數字，或為什麼現在還無法驗證）/ 優先級 P0–P2 / 實作成本 S・M・L / 信心與主要風險.
4. **建議刪除或簡化**: things that add noise, mislead, or cost more than they give.
5. **給其他隊的問題**: up to 3 questions you want another team to answer.
Evidence beats opinion. A proposal backed by a number you computed on the local data outranks a plausible idea.
