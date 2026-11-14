# 週三選股雷達：外部建議審查 brief（五隊共用）

The user pasted 16 screenshots of reviews that ChatGPT and Gemini wrote about this site, and asked for five stock teams to review
every suggestion: **if a suggestion is not recommended, record why; if it is recommended, specify it so it can be built and added.**
The suggestions are transcribed, with IDs, in `reviews/2026-11-14d/SUGGESTIONS.md` (C1–C11 from ChatGPT, G0–G9 from Gemini). Read it first.

## The site today
Live site https://jonwenjen.github.io/Stock_New/, served from `docs/` of /home/user/Stock_New (working tree = live site, commit 1a36b98).
Today is Saturday 2026-11-14; the data date is 2026-11-13. The site is rebuilt once a week (Wednesday night) by a scheduled Claude
session on subscription quota: `python -m pipeline.build`, then Claude writes the commentary in docs/data/ai.json, which
`pipeline/check_ai.py` checks (data date, banned words) before it is published.
- Front end: docs/index.html, docs/app.js (~1,970 lines, plain JS), docs/style.css, docs/knowledge.js (103 glossary entries).
- Data: docs/data/*.json (summary, universe, flows, market, leading, etf, funds, podcasts, smart_money, ai, tools).
- Price store: docs/data/charts/<code>_<TW|TWO|US…>.json, 304 files, `d` = ~5 years of daily [date,o,h,l,c,v] adjusted for ex-rights.
- Archives (docs/data/history/): monthly shards `pit_closes/` (closes of every listed common stock, 13 months so far),
  `stock_flows_tw/`, `stock_flows_otc/` (per-stock institutional flows; T86 back-fill 60 of 240 days), `revenue/` (27 of 60 months);
  `weekly/` (one snapshot per ISO week, 1 so far); `picks.json` (one cohort so far, 2026-11-13); `podcast_mentions.json` (1 week);
  `tdcc.json` (1 week); tw_derivs, tw_futures, tw_margins, tw_qfii, tw_valuation, tw_flags, market, forecasts, tactical.
- Local caches (git-ignored): `.cache/twse_days/<date>.json` (201 days, every TWSE security {code: [o,h,l,c,vol]}),
  `.cache/tpex_days/` (260 days, OTC), `.cache/twse_margin`, `.cache/twse_qfii`, `.cache/twse_sbl`. Use these instead of downloading.
- Pipeline: pipeline/build.py (orchestration, records, picks, podcasts), scoring.py (tech_frame, tech_panel, residual_momentum,
  factor_ic, backtest, fund_tw/fund_us, trade_plan, score_stock), market.py (risk score v2, regime_states, drawdown odds),
  leading.py (cards, attention_signal, follow_track, revenue_momentum), research.py (evidence grades, selection_bias, portfolio_risk,
  stress_replay, valuation_band, PREREGISTERED), media.py (podcast RSS), twse.py, tpex.py, prices.py, us_data.py, funds.py, feeds.py.
  Tests: tests/test_core.py (89 pass). Runbook: WEEKLY_UPDATE.md. Previous review records: reviews/2026-11-14b/README.md
  (five stock teams: consensus, vetoes, implementation status) and reviews/2026-11-14c/README.md (UX colour and component rules).
- Universe: 179 stocks (120 TW, of which 30 OTC `.TWO`; 59 US), 38 ETFs, 39 funds. Picks: 12 TW + 8 US per ISO week.

## What already exists for each suggestion (verify; do not take this list on trust)
| ID | Existing pieces |
|---|---|
| C1 RS | Tech score = cross-sectional rank of `t_slow` (trend + vol-normalised 120/60/20-day returns + 250-day range position); Taiwan averages it with the rank of the 52-week-high ratio (scoring.py `tech_frame`, `tech_panel`). Records carry r20, r60, ytd, rank [k, n] within market, `imom` = residual momentum vs the market (shown as 純超額強度, weight 0; IC in summary.backtest_imom). The 財報指標 page has an industry table with median r20/r60/rank. No RS line vs TAIEX/SPY, no RS-new-high flag, no rank within industry. |
| C2 revenue | rev_yoy, rev_mom, rev_cum_yoy, 3-month vs 12-month average ratio with cross up/down (leading.revenue_momentum) — all already inside the fund score (weight .15). Monthly revenue shards 27 months. Pre-registered test `fund_weight` judges the fund weight after the 5-year back-fill; the previous quant team found no revenue edge once the publication date (10th of next month) is respected. |
| C3 divergence | foreign5/foreign20/trust5/f5_adv/foreign_streak/qfii_chg20 per TW stock; margin, margin_chg5, margin_cost, maint_est; TDCC big1000/big400/holders (chg needs a 2nd week); flows page lists incl. 投信首買. New chip data has weight 0 until archived and tested. Dealer flows exist only market-wide (dealer_5d). |
| C4 ATR | scoring.trade_plan: ATR(14), stop = min(c − 2.5·ATR, support − 0.5·ATR), position size = min(cap, 1% / stop distance); picks' stops only move up after entry (build.py); research.pick_weights. Previous risk team: ATR stops help little (66% of stopped names were above the exit a month later). |
| C5 regime | market.regime_states (bull/range/pullback/rebound/bear), risk score v2 with 20-day drawdown odds per level, EXPOSURE_PCT defaults for the budget panel. Not used to change stock weights. |
| C6 sectors | 台股產業概況 table (median revenue YoY, r20, r60, rank, number of picks); picks capped at 3 per industry; no two picks with 60-day correlation ≥ 0.5. |
| C7 tracking | Weekly pick cohorts; leading.follow_track (rules fixed in advance: enter next open, TW cost 0.585% round trip, 26-week minimum before any verdict); scoring.backtest (IC of the actual ranking score), selection_bias, calibration, 5 pre-registered tests (research.PREREGISTERED), evidence grades A–D for the 19 leading cards (research.evidence: Spearman + Newey-West t, halves agree). No per-factor IC table for stock factors. |
| C8 debate | Records carry rule-based `reasons` (top 4 positives) and `risks` (top 3 negatives) and `watch_first` flags (weight 0). Claude writes market/podcast/ETF/fund commentary only; no per-pick bull/bear text. Direction labels and imperatives were removed from the UI (check_ai bans 上漲機率, 一月目標, 風險報酬比, 建議降低持股, 建議持股). |
| C9 layers | Ranking = weighted sum (TW tech .60 / chips .25 / fund .15; US .80/.05/.15); drawer shows each block's contribution. Market layer = risk score + budget panel. Weights may not change before the pre-registered tests. |
| G0 podcast premise | media.py reads **12 shows** (6 zh, 6 en) from RSS: titles + show notes only, no audio/transcripts, last 14 days, ≤ 6 episodes per show; alias matching against the universe; mention count = episodes. **No show weights and no time decay.** ai.json `podcast.overview` + per-stock notes are written by Claude. podcasts.json is already loaded only when the podcast page opens (not preloaded). |
| G1/G5 | leading.attention_signal labels a talked-about TW stock 法人先行 / 過熱警訊 / 籌碼轉弱 from mentions, weeks talked about, f5_adv and margin_chg5. Buzz factor weight 0. Tags Podcast 熱議 / Podcast 過熱. |
| G2 | `weeks` = consecutive ISO weeks a symbol was mentioned; mention history keeps 26 weeks (1 so far). Only stock aliases are matched, no themes or keywords. |
| G4/C7 | Nothing yet for "what happened after a mention". |
| G6 | Podcast table rows and episode chips open the stock drawer; ★ buttons exist in tables and the drawer; no score/flow capsule in episode lists. |
| G8 | 5-year P/E band (pe_pct/pe_band) via a one-time FinMind back-fill (blocked this week); stop levels; watch_first flags. |
| Known problem | build.py `hot_view()` still writes direction/imperative text into podcasts.json (同向偏多、拉回可留意、反彈宜減碼、觀望為主) and it is displayed on the podcast page; trade_plan `stance` text and `target`/`rr` still sit in universe.json (not displayed). |

## Settled decisions from earlier rounds (re-open only with new evidence you computed)
- No change to ranking weights before the pre-registered tests decide; new chip data is shown with weight 0 until archived and tested.
- No direction labels (偏多／偏空／看多／看空), no predictions dressed as targets, no imperatives (建議、宜減碼、可分批布局). R:R was
  deleted (+1σ was touched only 36% of the time within 21 days). The +1σ line is labelled 「一個月 +1σ 上緣（非目標）」.
- Stops are risk control, not forecasts: fixed at entry, only move up. Overheated stocks are flagged, never penalised.
- Vetoed: futures OI level/percentile as a feature or alarm; 「千張大戶 > 70%」 as a flag; broker-branch (分點) data behind TWSE's CAPTCHA;
  buybacks/convertibles without a complete weekly source.
- Leading cards get a direction tone only with evidence grade A/B that agrees with the reading. The US has no verifiable selection edge.
- Colours: red/green only for prices; warnings amber with "!"; official flags as ink stamps; accent only for clickable/current.

## Hard constraints
1. Static GitHub Pages only: no server, no database, no login. Rebuilt ONCE A WEEK by a Claude session on subscription quota.
   No GitHub Actions, no paid APIs, no API keys. Anything that needs an LLM must run inside that weekly Claude session and be
   checkable by a script (like check_ai.py); assume it cannot listen to audio.
2. Honest, low-volume data access: one self-identifying User-Agent, throttling, back off on refusal. Never rotate User-Agents,
   use proxies, solve CAPTCHAs or otherwise evade limits. TWSE www throttles at ~1 request / 6 s (HTTP 307 when unhappy); a weekly
   build already takes 25–40 minutes. Count the weekly requests any new source adds.
3. Audience: Taiwanese retail investors; Traditional Chinese UI. Nothing on the site is investment advice; the model's predictive
   power is weak and the site must keep saying so.

## Teams and the items each one owns (you must still vote on every item)
| Team | Owns (deep review + build spec) | Port |
|---|---|---|
| quant 量化 | C1, C7, C9 (as a modelling question), C11, G1 (statistics), G2 (statistics), G4 | 8701 |
| chips 籌碼 | C3, C10c, C10d, C10e, C10f, G1 (flow side), G5 | 8702 |
| fundamental 基本面 | C2, C6, C10a, G7, G8 (valuation side) | 8703 |
| risk 風控 | C4, C5, C10b, C10g, C10h, C10i, C10j, C10k, G3 (its risk-control claim), G8 (exit side) | 8704 |
| product 產品與輿情 | C8, C9 (presentation), G0, G2 (labels), G3, G5 (UX), G6, G9a, G9b, plus an honesty audit of the podcast page | 8705 |

## Verdicts
Use exactly one of: **採納** (build as proposed) / **修改後採納** (build a changed version — say exactly what changes) /
**已有** (already on the site — say where; note any small gap worth closing) / **暫緩** (worth doing but needs data or validation
first — name the trigger, and what must be archived now so the test is possible later) / **不採納** (reject — give the reason).
A rejection reason must be concrete enough to stand in the public record: a number you computed, a settled decision it would break,
a constraint it violates, or a data source that does not exist.

## Etiquette (five reviewers run in parallel)
- Do NOT modify any file under /home/user/Stock_New except your own output file in reviews/2026-11-14d/. No commit, no push.
  Do not run `python -m pipeline.build`. You may import pipeline modules and call pure functions on local data.
- External network: at most 15 requests in total; at most 5 to any one of TWSE / TPEx / TAIFEX / MOPS, sequential, ≥ 6 s apart.
  Say exactly what you requested and what came back. Prefer local data.
- To view the site: `python3 -m http.server <your port> --directory /home/user/Stock_New/docs` and Playwright
  (`require('playwright')`, `chromium.launch()` needs no options). Use only your own port. Scratch files go in your own temp dir.
- Be economical: measure what decides a verdict, not everything.

## Deliverable
Write Traditional Chinese Markdown (technical names may stay English) to `reviews/2026-11-14d/r1_<team>.md`, under ~12,000 characters:
1. **判定總表** (first, compact): one row for EVERY item C1–C11, C10a–k, G0–G9b: | 編號 | 判定 | 一句理由 | (for items you do not own,
   「同意主審」 is fine when you have no view; disagree only with a reason).
2. **主審項目的證據**: for each item you own, the evidence behind the verdict — numbers computed on local data (say how), file:line
   pointers, or the endpoint you tested.
3. **實作規格** for each item you own that is 採納 or 修改後採納: file/function; exact, reproducible definition; data source and weekly
   request cost; where it appears in the UI and its exact Chinese wording (no direction labels or imperatives); weight (0 unless
   validated); the unit test to add; the knowledge.js entry (key + one-paragraph text); and any pre-registered test to add
   (hypothesis, rule, data needed, judgement date).
4. **不採納／暫緩的公開紀錄**: for each item you own with those verdicts, a 1–3 sentence reason written for the site's readers.
5. **給其他隊的問題**: at most 2.
