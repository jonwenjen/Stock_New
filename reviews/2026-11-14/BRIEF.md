# 週三選股雷達 — review brief (shared by all reviewers)

Live site: https://jonwenjen.github.io/Stock_New/  — it is served from the `docs/` folder of the git
checkout at /home/user/Stock_New (branch state == live site, commit ba3aa0b). Review the local copy:
- Front end: docs/index.html, docs/app.js (~1,600 lines, plain JS), docs/style.css, docs/knowledge.js (88 knowledge entries)
- Data the site shows: docs/data/*.json (summary, universe, flows, market, leading, etf, funds, podcasts, smart_money, ai),
  docs/data/charts/<symbol>.json (price store: ~5y daily `d` rows [date,o,h,l,c,v] + older weekly `w`),
  docs/data/history/*.json (accumulating histories: tw_flows, picks, forecasts, tactical, tw_futures, tpex, tw_margin_cost ...)
- Pipeline (Python, `python -m pipeline.build`): pipeline/build.py, scoring.py, market.py, leading.py, twse.py, prices.py,
  us_data.py, funds.py, media.py. Tests: tests/test_core.py. Docs: README.md, WEEKLY_UPDATE.md.
- To view the site locally: `python3 -m http.server <port> --directory /home/user/Stock_New/docs` and use Playwright
  (`require('playwright')`, chromium.launch() needs no options). Pick a port in 8800-8899 unique to you.

## Hard constraints (any proposal must respect them)
1. Static GitHub Pages only (no server, no database). Data is rebuilt ONCE A WEEK (Wednesday) by a scheduled Claude
   session on subscription quota — no GitHub Actions, no paid APIs, no API keys.
2. Data access must be honest and low-volume: one self-identifying User-Agent, throttling, back off on refusal.
   Never propose rotating User-Agents, proxies or anything that evades rate limits/bot checks.
3. TWSE www (rwd) endpoints throttle at ~1 request / 6 s; a full weekly build already takes ~10-25 minutes.
4. Audience: Taiwanese retail investors, Traditional Chinese UI. Taiwan colour convention (red = up), switchable.
5. Nothing on the site is investment advice; the model's predictive power is weak and the site says so.

## What already exists (do not re-propose these as new; you may propose improving them)
- 5 nav groups / 14 views: 總覽與預測(decision funnel, 16 leading-indicator cards, option OI ladder), 市場安全(0-100 risk score
  of 9 parts, regime, breadth index, alerts, linkage, forecast accuracy, accumulating/weakening lists), 漲跌預警,
  精選20檔 (12 TW + 8 US, NEW/↑↓/weeks badges, cross-page tags, short-term vs structural score, ATR stop, position cap),
  財報指標 (table + quadrant scatter), 自選股 (localStorage), 外資法人 (+投信首買), 融資融券 (maintenance ratio estimate,
  per-stock margin cost estimate, SBL tables), 名人・議員・董監 (Congress trades, 13F, TW insider pledge), ETF (+risk regime),
  基金 (39 funds, 0-100 score), Podcast, 指標與模型解說 (knowledge), 方法與來源 (weights, backtest, calibration, sources).
- Scoring: 5 factor groups (trend, momentum, chips, fundamentals; podcast buzz weight 0) squashed to [-1,1]; horizon weights
  1w/1m/1q; probability = 50% + slope*score, slope calibrated each build (currently ~0.03 => probabilities ~47-53%,
  because the technical score has almost no directional skill; cross-sectional IC ~0.01 (1w) .. 0.06 (1q)).
- Leading cards: TSMC ADR premium, USD/TWD, US real yield, 10y-2y curve, HY OAS, SOXX/SPY, VIX/VIX3M, breadth TW/US,
  advance/decline, retail mini-TX L/S, top-10 traders, futures basis, TXO put/call, TXO OI walls + max pain, SBL balance,
  Fed net liquidity, OTC/TAIEX relative strength.
- Universe: TW = top ~90 listed (TWSE) stocks by turnover (no OTC/TPEx stocks), US = 60 fixed large caps.

## Previously done reviews (already implemented): leading indicators batch 1 & 2, knowledge page, 2-tier nav, deep links,
   tactical/structural split, ATR sizing, option walls, margin maintenance, trust first-buys, contract liabilities,
   HY OAS + SOXX/SPY regime, probability calibration, Fed net liquidity, watchlist, CSV export, quadrant chart.

## Output format
Write your result as Markdown to the file path given in your task. Traditional Chinese for content (technical names may stay
English). For every proposal give: 名稱 / 放在哪裡 / 為什麼有用（具體到台股情境）/ 資料來源與可行性（免費? 頻率?）/
優先級 P0-P2 / 你對它的信心與主要風險. Be concrete and evidence-based; mark anything you did not verify as 未驗證.
Do NOT modify any file under /home/user/Stock_New. Work only in your own scratchpad output file.
