# 週三選股雷達：三隊 UX/UI 審查 brief（網站風格與介面顏色）

Live site: https://jonwenjen.github.io/Stock_New/, served from `docs/` in the git checkout at /home/user/Stock_New
(commit 662c814 = live). Today is 2026-11-14; data date 2026-11-13. Review the local copy.

## What to look at
- `docs/style.css` (~400 lines). It defines the colour tokens on `:root`, dark mode (`prefers-color-scheme` plus the
  `data-theme` override) and the Taiwan up/down colour switch (`data-updown="us"` swaps the colours: Taiwan red = up,
  green = down by default). It also defines the risk palette `--rk0..3` (blue / grey / amber / purple) and the components:
  pills, chips (`mk`, `flag`, `warn`, `add`, `cut`), tags, tables, cards, tiles, the drawer, the decision funnel, leading
  cards and the evidence-grade chips.
- `docs/app.js` (~1,800 lines, plain JS). It renders the 14 views in 5 groups and the stock drawer. Charts use
  TradingView lightweight-charts (candles, MA lines, price lines) and inline SVG sparklines and flow charts.
- `docs/index.html` loads the system CJK fonts plus IBM Plex Mono for numbers. `docs/knowledge.js` holds 102 explanations.
- Data under `docs/data/*.json`. Previous reviews and their decisions are in `reviews/2026-11-14/README.md` and
  `reviews/2026-11-14b/README.md`. Read the latter: it lists what changed this week (evidence grades, weekly-change card,
  先觀察 flags, portfolio panel, budget table, risk score v2, honesty clean-up).
- Serve the site with `python3 -m http.server <your port> --directory /home/user/Stock_New/docs` and drive it with
  Playwright (`require('playwright')`; `chromium.launch()` needs no options). Use desktop 1280×900 and phone 390×844,
  light and dark (`colorScheme`), and both up/down conventions (the "紅漲綠跌" toggle in the header).

## Constraints
1. Static GitHub Pages, plain JS/CSS, no build step, no framework, no paid fonts or services. Rebuilt weekly.
2. Audience: Taiwanese retail investors; Traditional Chinese UI. Red = up and green = down by default, switchable.
   The colour of a price move must never be reused for something that is not a price move: risk levels, warnings,
   evidence grades and flags need their own colours.
3. The site's predictive power is weak and it says so. Styling must not make weak signals look strong, for example
   with saturated "bullish" badges.
4. Accessibility target: WCAG 2.2 AA (text contrast 4.5:1, large text and UI parts 3:1, visible focus, keyboard reachable).
   It must stay readable for colour-blind users (about 8% of men): anything that red versus green distinguishes also needs a sign
   (▲▼, + −) or a label.

## Etiquette
- Do NOT modify any file under /home/user/Stock_New except your own output file(s) in /home/user/Stock_New/reviews/2026-11-14c/.
  Do not commit or push. Do not run `python -m pipeline.build`.
- No external network except what the local page itself loads (chart library and font CDN).
- Put screenshots and scripts in your own temp directory and cite their absolute paths.
- Budget: about 25 tool calls. Write a skeleton of your report early and update it, so your progress survives an interruption.

## Deliverable (Traditional Chinese, under about 15,000 characters)
1. **一句話結論**
2. **現況問題（附證據）**: computed contrast ratios, screenshots, selectors, counts. Mark severity P0–P2.
3. **提案（最多 10 項，依優先排序）**: for each, give 名稱 / 為什麼（散戶看網站時的哪個問題）/ 具體改法 (exact CSS
   selectors and properties, token names and hex values for light and dark) / 影響範圍 / 優先級 P0–P2 / 成本 S・M・L.
4. **色票與元件規格**: the token set you recommend, as a table with columns token, light hex, dark hex and use, plus contrast checks.
   Cover at least: background, surface, line, text 1/2/3, accent/link, up/down (both conventions), risk 0–3, warning,
   evidence A/B/C/D, chart candles and MA lines.
5. **待辦 UI 項目的意見**: the following product backlog items are still open. Say whether to do each, in what form, and in what order:
   - product-3: phone first screen and navigation (the fifth nav group is off screen on phone)
   - product-6: iCal and Atom feeds for the calendar and weekly change
   - product-7: accessibility (drawer focus trap, skip link, nested interactive elements in pick cards, contrast)
   - product-8: phone list mode and column sets for wide tables
   - product-9: compare 2–4 stocks and shareable filter URLs
   - product-10: light PWA (manifest, offline cache) and deferred loading
6. **給其他兩隊的問題**: up to 3.
