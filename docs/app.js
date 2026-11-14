/* 週三選股雷達 - front end. Plain JS, no build step. All data comes from data/*.json. */
(() => {
  "use strict";

  // ---------- tiny DOM + format helpers (text is always inserted as text, never as HTML) ----------
  const h = (tag, attrs, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  };
  const ok = (v) => v != null && Number.isFinite(+v);
  const nf = (d) => new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const num = (v, d = 2) => (ok(v) ? nf(d).format(v) : "–");
  const px = (v) => (ok(v) ? nf(Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 100 ? 1 : 2).format(v) : "–");
  const pct = (v, d = 1) => (ok(v) ? (v > 0 ? "+" : "") + nf(d).format(v * 100) + "%" : "–");
  const pctRaw = (v, d = 1) => (ok(v) ? (v > 0 ? "+" : "") + nf(d).format(v) + "%" : "–");
  const signed = (v, d = 0) => (ok(v) ? (v > 0 ? "+" : "") + nf(d).format(v) : "–");
  const cls = (v) => (!ok(v) || +v === 0 ? "" : v > 0 ? "up" : "down");
  const numEl = (text, v) => h("span", { class: "num " + cls(v) }, text);
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fileOf = (sym) => sym.replace(/[^A-Za-z0-9_-]/g, "_");
  const HZ = { "1w": "一週", "1m": "一月", "1q": "一季" };
  const level = (s) => (s >= 0.45 ? 2 : s >= 0.15 ? 1 : s > -0.15 ? 0 : s > -0.45 ? -1 : -2);
  const ARROW = { 2: "▲", 1: "▲", 0: "■", "-1": "▼", "-2": "▼" };

  const pill = (f) => h("span", { class: "pill s" + level(f.score) }, ARROW[level(f.score)], " ", f.label);
  const meter = (score) => {
    const w = Math.min(50, Math.abs(score) * 50);
    const bar = h("i");
    bar.style.cssText = `width:${w}%;${score >= 0 ? "left:50%" : `left:${50 - w}%`};background:var(${score >= 0 ? "--up" : "--down"})`;
    return h("span", { class: "meter", role: "img", "aria-label": `分數 ${signed(score, 2)}` }, bar);
  };
  const mkChip = (m, sym) => h("span", { class: "chip mk" }, m === "TW" ? (sym && sym.endsWith(".TWO") ? "上櫃" : "台股") : "美股");
  const nameCell = (r) => h("span", { class: "nm-cell" }, h("b", {}, r.name), h("span", { class: "code" }, r.code));

  function spark(values) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 30");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    if (values.length < 2) return svg;
    const lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${27 - ((v - lo) / span) * 24}`);
    const up = values[values.length - 1] >= values[0];
    const path = document.createElementNS(svg.namespaceURI, "polyline");
    path.setAttribute("points", pts.join(" "));
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", `var(${up ? "--up" : "--down"})`);
    path.setAttribute("stroke-width", "1.6");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    svg.append(path);
    return svg;
  }

  // ---------- data ----------
  const cache = new Map();
  const load = (name) => {
    if (!cache.has(name)) {
      cache.set(name, fetch(`data/${name}.json`).then((r) => {
        if (!r.ok) throw new Error(`data/${name}.json → HTTP ${r.status}`);
        return r.json();
      }));
    }
    return cache.get(name);
  };
  let S = null; // summary.json
  let U = []; // universe.json
  let E = []; // etf.json
  let FUNDS = []; // funds.json
  let LEAD = { cards: [], series: {} }; // leading.json
  // "?" link from a number to its entry in the knowledge page
  const help = (id, label) => h("a", { class: "help", href: "#learn?k=" + id, title: "看這個指標的解說",
    "aria-label": (label || "") + "解說", onclick: (e) => e.stopPropagation() }, "?");
  const CARD_KB = { vixts: "vix", breadth_tw: "breadth", breadth_us: "breadth" };
  const calibNote = () => {
    const C = S.calibration || {};
    const rows = [];
    for (const mk of ["TW", "US"]) for (const [k, v] of Object.entries(C[mk] || {})) rows.push({ mk, k, ...v });
    if (!rows.length) return null;
    return h("div", { class: "stack" }, table([
      { key: "m", label: "市場", left: true, cell: (r) => mkChip(r.mk) },
      { key: "h", label: "期間", left: true, cell: (r) => HZ[r.k] },
      { key: "k", label: "斜率", tip: "跑贏中位數機率＝50% ＋ 斜率 × 排名分數（−1 至 +1）", cell: (r) => h("b", { class: "num" }, num(r.k, 3)) },
      { key: "b", label: "Brier（樣本外）", cell: (r) => h("span", { class: "num" }, num(r.brier, 4)) },
      { key: "c", label: "一律 50%", cell: (r) => h("span", { class: "num" }, num(r.brier_coin, 4)) },
      { key: "e", label: "有優勢", left: true, cell: (r) => (r.edge ? "是" : h("span", { class: "muted" }, "否")) }], rows, { csv: false }),
      h("p", { class: "small" }, "網站不再顯示「上漲機率」，因為股價漲跌大半取決於大盤，而分數只能比較股票之間的強弱。這裡衡量的是：排名較前的股票，跑贏同市場中位數股票的機率高多少。樣本外是把約三年的資料切成五段，每一段只用之前的資料估計再預測。沒有優勢的市場，網站一律顯示 50%。",
        help("pup", "相對勝率")));
  };
  // Two-level navigation: five groups, each holding the views that are read together.
  const NAV = [
    ["市場總覽", [["overview", "總覽與預測"], ["safety", "市場安全"], ["alerts", "漲跌預警"]]],
    ["核心選股", [["picks", "精選 20 檔"], ["fund", "財報指標"], ["watch", "自選"]]],
    ["籌碼與聰明錢", [["flows", "外資法人"], ["margin", "融資融券"], ["smart", "名人・議員・董監"]]],
    ["資產配置", [["etf", "ETF"], ["funds", "基金"]]],
    ["輿情與知識", [["podcast", "Podcast"], ["learn", "指標與模型解說"], ["method", "方法與來源"]]]];
  let AI = null; // ai.json - commentary written by Claude during the weekly run
  let MK = null; // market.json - risk score, regime, alerts (optional)
  // Personal watchlist, kept in this browser only.
  const WATCH = new Set();
  try { JSON.parse(localStorage.getItem("watch") || "[]").forEach((x) => WATCH.add(x)); } catch (e) { /* storage unavailable */ }
  const paintWatch = () => { const el = document.getElementById("watch-chip"); if (el) el.textContent = `★ 自選 ${WATCH.size}`; };
  const toggleWatch = (sym) => {
    if (WATCH.has(sym)) WATCH.delete(sym); else WATCH.add(sym);
    try { localStorage.setItem("watch", JSON.stringify([...WATCH])); } catch (e) { /* storage unavailable */ }
    paintWatch();
  };
  // A star that works inside a card that is itself a button (so it cannot be a <button>).
  const star = (sym) => {
    const el = h("span", { class: "star", role: "button", tabindex: 0, "aria-pressed": String(WATCH.has(sym)), title: "加入或移出自選股" }, WATCH.has(sym) ? "★" : "☆");
    const flip = (e) => { e.stopPropagation(); e.preventDefault(); toggleWatch(sym);
      el.textContent = WATCH.has(sym) ? "★" : "☆"; el.setAttribute("aria-pressed", String(WATCH.has(sym))); };
    el.addEventListener("click", flip);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") flip(e); });
    return el;
  };
  // Official trading flags (處置, 注意, 除權息) come first: they change what can actually be traded.
  const OFFICIAL = /處置|注意|除息|除權/;
  const tagChips = (r, max = 99) => {
    const tags = [...(r.tags || [])].sort((a, b) => OFFICIAL.test(b[0]) - OFFICIAL.test(a[0]));
    return tags.length ? h("div", { class: "tags" }, tags.slice(0, max).map(([t, tone]) =>
      h("span", { class: "chip " + (OFFICIAL.test(t) ? "flag" : tone > 0 ? "add" : tone < 0 ? "cut" : "") }, t)),
      tags.length > max ? h("span", { class: "small muted" }, `＋${tags.length - max}`) : null) : null;
  };
  // Rank within the stock's own market, and the chance of beating that market's median stock.
  const rankText = (r) => (r.rank ? `排名 ${r.rank[0]}／${r.rank[1]}` : "");
  // A yield's change reads in basis points (+2.41% of 4.9% is +12bp, not +2.41 points).
  const chgText = (i, c, d) => (/10Y/.test(i.symbol) && ok(c) ? signed(i.close * c / (1 + c) * 100, 0) + "bp" : pct(c, d));
  const rankCol = { key: "1", label: "同市場排名", left: true, val: (r) => (r.rank ? r.rank[0] / r.rank[1] : null), cell: (r) => h("span", { class: "num small" }, r.rank ? rankText(r) : "–") };
  const musd = (v) => (ok(v) ? "US" + "\u0024" + num(v / 1e6, 1) + "M" : "–");
  const relText = (r, h1 = "1m") => {
    const f = (r.horizons || {})[h1];
    if (!f || f.p_beat == null) return null;
    if (r.market === "US" && !r.edge) return "美股：無可驗證優勢";
    return `${HZ[h1]}跑贏同市場中位數 ${nf(0).format(f.p_beat * 100)}%`;
  };
  // Risk levels get their own colours: they must not borrow the up/down colours, which swap.
  const RISK_CLS = { s1: "rk0", s0: "rk1", "s-1": "rk2", "s-2": "rk3" };
  const diffBadges = (r) => {
    const d = r.diff;
    if (!d) return null;
    return [d.new ? h("span", { class: "chip mk" }, "NEW") : d.move ? h("span", { class: "chip " + (d.move > 0 ? "add" : "cut") }, (d.move > 0 ? "↑" : "↓") + Math.abs(d.move)) : null,
      d.weeks >= 2 ? h("span", { class: "chip" }, `連續 ${d.weeks} 週`) : null];
  };
  const bySymbol = new Map();

  // ---------- sortable table ----------
  // Rows and cards that open a chart carry data-sym; hovering or touching one starts fetching its chart file.
  const rowSym = (r) => (r && typeof r === "object" ? (r.symbol && bySymbol.has(r.symbol) ? r.symbol
    : r.code && bySymbol.has(r.code + ".TW") ? r.code + ".TW" : r.ticker && bySymbol.has(r.ticker) ? r.ticker : null) : null);
  const preload = (e) => { const el = e.target.closest && e.target.closest("[data-sym]"); if (el) load("charts/" + fileOf(el.dataset.sym)).catch(() => {}); };
  document.addEventListener("pointerover", preload, { passive: true });
  document.addEventListener("focusin", preload);
  document.addEventListener("touchstart", preload, { passive: true });

  function table(cols, rows, opts = {}) {
    let sortKey = opts.sort || null, dir = opts.dir || -1;
    const tbody = h("tbody");
    const ths = cols.map((c) => h("th", { class: c.left ? "l" : null, "data-sort": c.val ? c.key : null,
      scope: "col", title: c.tip || null }, c.label));
    const draw = () => {
      const col = cols.find((c) => c.key === sortKey);
      const data = col ? [...rows].sort((a, b) => {
        const x = col.val(a), y = col.val(b);
        if (!ok(x) && !ok(y)) return 0;
        if (!ok(x)) return 1;
        if (!ok(y)) return -1;
        return (x - y) * dir;
      }) : rows;
      tbody.replaceChildren(...data.map((r) => {
        const tr = h("tr", { class: opts.onRow ? "click" : null, tabindex: opts.onRow ? 0 : null, "data-sym": rowSym(r) },
          cols.map((c) => h("td", { class: [c.left ? "l" : "", c.wrap ? "wrap" : ""].join(" ").trim() || null }, c.cell(r))));
        if (opts.onRow) {
          tr.addEventListener("click", () => opts.onRow(r));
          tr.addEventListener("keydown", (e) => { if (e.key === "Enter") opts.onRow(r); });
        }
        return tr;
      }));
      ths.forEach((th, i) => th.setAttribute("aria-sort",
        cols[i].key === sortKey && cols[i].val ? (dir < 0 ? "descending" : "ascending") : "none"));
    };
    ths.forEach((th, i) => {
      if (!cols[i].val) return;
      th.addEventListener("click", () => {
        if (sortKey === cols[i].key) dir = -dir; else { sortKey = cols[i].key; dir = -1; }
        draw();
      });
    });
    draw();
    const wrap = h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ths)), tbody));
    // Export what is on screen: the current filter and sort order, as text.
    const exportCsv = () => downloadCsv(opts.csv || "table",
      [cols.map((c) => c.label), ...[...tbody.rows].map((tr) => [...tr.cells].map((td) => td.textContent.replace(/\s+/g, " ").trim()))]);
    const el = h("div", { class: "tbl" }, wrap, opts.csv === false || (!opts.csv && rows.length < 6) ? null : h("div", { class: "tbl-tools" },
      h("button", { class: "linkish", type: "button", onclick: exportCsv }, "匯出 CSV")));
    el.setRows = (r) => { rows = r; draw(); };
    return el;
  }
  function downloadCsv(name, lines) {
    const esc = (v) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
    // The byte-order mark makes Excel read the file as UTF-8, so Chinese text is not garbled.
    const blob = new Blob(["\uFEFF" + lines.map((l) => l.map(esc).join(",")).join("\r\n")], { type: "text/csv;charset=utf-8" });
    const a = h("a", { href: URL.createObjectURL(blob), download: `${name}-${S ? S.data_date : "data"}.csv` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  // ---------- price chart (candles + volume + MAs, switchable range) ----------
  const RANGES = [["1d", "天"], ["1w", "週"], ["1m", "月"], ["3m", "季"], ["6m", "2季"],
    ["ytd", "當年"], ["2y", "2年"], ["5y", "5年"], ["all", "所有"]];
  const charts = new Set();      // re-theme callbacks of the charts on screen
  const liveCharts = new Set();  // their disposers: a view's charts are destroyed when the view is left

  function themeOpts() {
    return {
      layout: { background: { color: "transparent" }, textColor: css("--ink-2"), fontFamily: css("--mono"), fontSize: 11 },
      grid: { vertLines: { color: css("--line-soft") }, horzLines: { color: css("--line-soft") } },
      rightPriceScale: { borderColor: css("--line") },
      timeScale: { borderColor: css("--line"), rightOffset: 2 },
      crosshair: { mode: 0, vertLine: { color: css("--ink-3"), labelBackgroundColor: css("--ink-2") },
        horzLine: { color: css("--ink-3"), labelBackgroundColor: css("--ink-2") } },
    };
  }
  const toWeekly = (rows) => {
    const out = [];
    let cur = null, key = null;
    for (const r of rows) {
      const d = new Date(r[0] + "T00:00:00Z");
      const k = Math.floor((d.getTime() / 864e5 + 3) / 7); // Monday-based week number
      if (k !== key) { cur = [r[0], r[1], r[2], r[3], r[4], r[5]]; out.push(cur); key = k; }
      else { cur[2] = Math.max(cur[2], r[2]); cur[3] = Math.min(cur[3], r[3]); cur[4] = r[4]; cur[5] += r[5]; }
    }
    return out;
  };
  const sma = (rows, n) => {
    const out = [];
    let sum = 0;
    for (let i = 0; i < rows.length; i++) {
      sum += rows[i][4];
      if (i >= n) sum -= rows[i - n][4];
      if (i >= n - 1) out.push({ time: rows[i][0], value: sum / n });
    }
    return out;
  };
  // autoSize measures the container asynchronously; fitting before that squeezes the bars to one edge.
  const fit = (chart) => requestAnimationFrame(() => requestAnimationFrame(() => chart.timeScale().fitContent()));
  const isoBack = (iso, days) => new Date(Date.parse(iso + "T00:00:00Z") - days * 864e5).toISOString().slice(0, 10);

  function selectBars(data, range) {
    const d = data.d || [], w = data.w || [], intr = data.i || [];
    const last = d.length ? d[d.length - 1][0] : null;
    if (!last) return { rows: [], unit: "日" };
    if (range === "1d" || range === "1w") {
      if (intr.length) {
        const dayOf = (t) => Math.floor(t / 86400);
        const days = [...new Set(intr.map((b) => dayOf(b[0])))];
        const keep = new Set(range === "1d" ? days.slice(-1) : days.slice(-5));
        if (range === "1d" || days.length >= 3) {
          return { rows: intr.filter((b) => keep.has(dayOf(b[0]))), unit: "5 分", intraday: true };
        }
      }
      return { rows: d.slice(range === "1d" ? -5 : -5), unit: "日", fallback: true };
    }
    const from = { "1m": isoBack(last, 31), "3m": isoBack(last, 92), "6m": isoBack(last, 183),
      ytd: last.slice(0, 4) + "-01-01", "2y": isoBack(last, 731), "5y": isoBack(last, 1827) }[range];
    if (range === "5y") return { rows: toWeekly(d.filter((r) => r[0] >= from)), unit: "週" };
    if (range === "all") {
      const recent = toWeekly(d);
      const first = recent.length ? recent[0][0] : "9999";
      return { rows: [...w.filter((r) => r[0] < first), ...recent], unit: "週" };
    }
    return { rows: d.filter((r) => r[0] >= from), unit: "日" };
  }

  function priceChart(symbol, opts = {}) {
    const state = { range: opts.range || "6m", data: null };
    const readout = h("div", { class: "readout", "aria-live": "off" });
    const box = h("div", { class: "chart loading" + (opts.small ? " sm" : "") });
    const note = h("div", { class: "chart-note" });
    const seg = h("div", { class: "seg", role: "group", "aria-label": "圖表區間" },
      RANGES.map(([k, label]) => h("button", { type: "button", "data-r": k, "aria-pressed": String(k === state.range),
        onclick: () => { state.range = k; draw(); } }, label)));
    const legend = h("div", { class: "readout" },
      [["--ma1", "MA20"], ["--ma2", "MA60"], ["--ma3", "MA120"]].map(([c, t]) => {
        const k = h("span", { class: "key" });
        k.style.background = `var(${c})`;
        return h("span", {}, k, t);
      }));
    const coneKey = h("span", { hidden: true }, h("span", { class: "key dash" }), "預測區間（±1σ）");
    legend.append(coneKey);
    const root = h("div", { class: "stack" }, h("div", { class: "chart-bar" }, seg, legend), readout, box, note);

    const chart = LightweightCharts.createChart(box, { ...themeOpts(), autoSize: true,
      handleScale: { axisPressedMouseMove: false }, localization: { locale: "zh-TW" } });
    const candle = chart.addCandlestickSeries({ priceLineVisible: false });
    const navLine = chart.addLineSeries({ lineWidth: 2, priceLineVisible: false }); // funds: one NAV a day, no candles
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.2 } });
    const mas = [20, 60, 120].map(() => chart.addLineSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }));
    // Forecast cone: the model's one-standard-deviation range, drawn into the days after the last bar.
    const cone = [0, 1, 2].map((i) => chart.addLineSeries({ lineWidth: 1, lineStyle: i === 2 ? 3 : 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }));
    const fcOf = () => { const it = bySymbol.get(symbol) || (S && S.indices.find((x) => x.symbol === symbol)); return it && it.horizons; };
    const nextDays = (iso, n) => { const out = []; let t = Date.parse(iso + "T00:00:00Z");
      while (out.length < n) { t += 864e5; const d = new Date(t).getUTCDay(); if (d !== 0 && d !== 6) out.push(new Date(t).toISOString().slice(0, 10)); } return out; };
    let rowsByTime = new Map();
    // Support, stop and the one-month reference target as labelled lines (stocks and ETFs).
    const planLines = opts.plan ? [["support", "支撐", "--ma1"], ["stop", "停損", "--down"], ["target", "+1σ 上緣", "--up"]]
      .filter(([k]) => ok(opts.plan[k])).map(([k, t, c]) => [candle.createPriceLine({ price: opts.plan[k], title: t, lineWidth: 1, lineStyle: 2, axisLabelVisible: true }), c]) : [];

    const applyTheme = () => {
      chart.applyOptions(themeOpts());
      const up = css("--up"), down = css("--down");
      candle.applyOptions({ upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down });
      ["--ma1", "--ma2", "--ma3"].forEach((c, i) => mas[i].applyOptions({ color: css(c) }));
      cone.forEach((c) => c.applyOptions({ color: css("--ink-3") }));
      planLines.forEach(([l, c]) => l.applyOptions({ color: css(c) }));
      navLine.applyOptions({ color: css("--ink") });
      if (state.data) draw();
    };
    const show = (r) => {
      if (!r) { readout.replaceChildren(); return; }
      const t = typeof r[0] === "number" ? new Date(r[0] * 1000).toISOString().slice(0, 16).replace("T", " ") : r[0];
      const prev = rowsByTime.get("prev:" + r[0]);
      const chg = prev ? r[4] / prev - 1 : null;
      if (state.data && state.data.line) {
        readout.replaceChildren(...[h("span", {}, t), h("span", {}, "淨值 ", h("b", {}, num(r[4], r[4] < 100 ? 4 : 2))),
          chg != null ? h("span", {}, "較前一筆 ", h("b", { class: cls(chg) }, pct(chg, 2))) : null].filter(Boolean));
        return;
      }
      // replaceChildren() would print a null argument as the text "null", hence the filter.
      readout.replaceChildren(...[h("span", {}, t),
        ...[["開", r[1]], ["高", r[2]], ["低", r[3]], ["收", r[4]]].map(([k, v]) => h("span", {}, k + " ", h("b", {}, px(v)))),
        chg != null ? h("span", {}, "漲跌 ", h("b", { class: cls(chg) }, pct(chg, 2))) : null,
        r[5] ? h("span", {}, "量 ", h("b", {}, nf(0).format(r[5]))) : null].filter(Boolean));
    };
    function draw() {
      const { rows, unit, intraday, fallback } = selectBars(state.data, state.range);
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.r === state.range)));
      const up = css("--up"), down = css("--down");
      rowsByTime = new Map();
      rows.forEach((r, i) => { rowsByTime.set(r[0], r); if (i) rowsByTime.set("prev:" + r[0], rows[i - 1][4]); });
      // Intraday bars use numeric timestamps, daily bars use date strings; the library cannot hold
      // both at once, so every series is emptied before the new window is loaded.
      [candle, navLine, vol, ...mas, ...cone].forEach((s) => s.setData([]));
      const isLine = !!state.data.line;
      if (isLine) navLine.setData(rows.map((r) => ({ time: r[0], value: r[4] })));
      else {
        candle.setData(rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] })));
        vol.setData(rows.map((r) => ({ time: r[0], value: r[5], color: (r[4] >= r[1] ? up : down) + "66" })));
      }
      chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: isLine ? 0.06 : 0.2 } });
      // Moving averages are computed on the full daily history, then clipped to the window.
      const daily = state.data.d || [];
      const first = rows.length ? rows[0][0] : null;
      const showMA = !intraday && unit === "日" && rows.length > 5;
      mas.forEach((s, i) => s.setData(showMA ? sma(daily, [20, 60, 120][i]).filter((p) => p.time >= first) : []));
      legend.hidden = !showMA;
      const fc = fcOf();
      const steps = { "1m": 21, "3m": 21, "6m": 63, ytd: 63, "2y": 63 }[state.range];
      const hasCone = !!(fc && steps && !intraday && unit === "日" && rows.length > 5 && rows[rows.length - 1][0] === daily[daily.length - 1][0]);
      if (hasCone) {
        const last = rows[rows.length - 1], days = nextDays(last[0], steps);
        const anchors = [[0, "low", "high"], [5, fc["1w"]], [21, fc["1m"]], [63, fc["1q"]]].filter(([d]) => d <= steps);
        const at = (d, key) => { // straight lines between the horizons the model actually forecasts
          for (let i = 1; i < anchors.length; i++) {
            const [d0, f0] = [anchors[i - 1][0], anchors[i - 1][1]], [d1, f1] = anchors[i];
            if (d <= d1) { const v0 = d0 === 0 ? last[4] : (key === "mid" ? (f0.low + f0.high) / 2 : f0[key]);
              const v1 = key === "mid" ? (f1.low + f1.high) / 2 : f1[key]; return v0 + ((v1 - v0) * (d - d0)) / (d1 - d0); }
          }
          return null;
        };
        ["high", "low", "mid"].forEach((key, i) => cone[i].setData([{ time: last[0], value: last[4] },
          ...days.map((t, j) => ({ time: t, value: at(j + 1, key) }))]));
      }
      coneKey.hidden = !hasCone;
      legend.hidden = !showMA && !hasCone;
      chart.applyOptions({ timeScale: { timeVisible: !!intraday, secondsVisible: false } });
      fit(chart);
      show(rows[rows.length - 1]);
      const start = (state.data.w && state.data.w[0] ? state.data.w[0][0] : daily[0] && daily[0][0]) || "";
      note.textContent = (isLine ? `每日淨值・${rows.length} 筆` : `${unit}K・${rows.length} 根`) +
        (fallback ? (isLine ? "・基金每日只有一個淨值，顯示最近 5 筆" : "・此標的無分時資料，改以最近 5 根日 K 呈現") : "") +
        (intraday ? "・分時資料為更新當下的最近交易日" : "") + (start ? `・歷史資料起始 ${start}` : "") +
        (hasCone ? "・右側虛線是模型對未來的一個標準差區間（約三分之二的情況落在其中），不是目標價" : "");
    }
    chart.subscribeCrosshairMove((p) => {
      if (!p.time) { const { rows } = selectBars(state.data || {}, state.range); show(rows[rows.length - 1]); return; }
      const key = typeof p.time === "object" ? `${p.time.year}-${String(p.time.month).padStart(2, "0")}-${String(p.time.day).padStart(2, "0")}` : p.time;
      show(rowsByTime.get(key));
    });
    charts.add(applyTheme);
    let alive = true;
    root.destroy = () => { if (!alive) return; alive = false; charts.delete(applyTheme); liveCharts.delete(root.destroy); chart.remove(); };
    liveCharts.add(root.destroy);
    load("charts/" + fileOf(symbol)).then((d) => { if (!alive) return; state.data = d; box.classList.remove("loading"); applyTheme(); })
      .catch(() => { if (alive) { box.classList.remove("loading"); box.replaceChildren(h("div", { class: "empty" }, "這檔標的目前沒有圖表資料")); } });
    return root;
  }

  // ---------- flow chart (bars with sign colour + TAIEX line in its own panel) ----------
  // opts: { line, scale, digits, marks: [[value, title], ...] } - marks draw labelled reference lines.
  function flowChart(history, series, label, unit, opts = {}) {
    const scale = opts.scale || 1, digits = opts.digits || 0;
    const rows = history.filter((r) => ok(r[series])).map((r) => ({ ...r, [series]: r[series] * scale }));
    const readout = h("div", { class: "readout" });
    const top = h("div", { class: "chart sm" }), bottom = h("div", { class: "chart sm" });
    const state = { n: 60 };
    const seg = h("div", { class: "seg", role: "group", "aria-label": "區間" },
      [[20, "月"], [60, "季"], [120, "2季"], [9999, "所有"]].map(([n, t]) =>
        h("button", { type: "button", "aria-pressed": String(n === state.n), "data-n": n, onclick: () => { state.n = n; draw(); } }, t)));
    const root = h("div", { class: "stack" }, h("div", { class: "chart-bar" }, seg, readout),
      h("h4", {}, `${label}（${unit}）`), top, h("h4", {}, "加權指數"), bottom);
    const mk = (el) => LightweightCharts.createChart(el, { ...themeOpts(), autoSize: true, handleScroll: false, handleScale: false });
    const c1 = mk(top), c2 = mk(bottom);
    const isLine = !!opts.line || series === "margin_bal" || series === "short_units";
    const whole = { type: "price", precision: 0, minMove: 1 };
    const fmt = { type: "price", precision: digits, minMove: 1 / 10 ** digits };
    const s1 = isLine ? c1.addLineSeries({ lineWidth: 2, priceLineVisible: false, priceFormat: fmt })
      : c1.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, priceFormat: whole });
    const markLines = (opts.marks || []).map(([price, title]) =>
      s1.createPriceLine({ price, title, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, color: "#888" }));
    if (markLines.length) {  // keep the reference levels in view: the distance to them is the point of the chart
      const lv = opts.marks.map(([p]) => p);
      s1.applyOptions({ autoscaleInfoProvider: (orig) => {
        const r = orig();
        if (r && r.priceRange) {
          r.priceRange.minValue = Math.min(r.priceRange.minValue, Math.min(...lv) - 3);
          r.priceRange.maxValue = Math.max(r.priceRange.maxValue, Math.max(...lv) + 3);
        }
        return r;
      } });
    }
    const s2 = c2.addLineSeries({ lineWidth: 2, priceLineVisible: false, priceFormat: whole });
    const byDate = new Map(rows.map((r) => [r.date, r]));
    const show = (r) => {
      if (!r) return;
      readout.replaceChildren(...[h("span", {}, r.date), h("span", {}, label + " ", h("b", { class: isLine ? "" : cls(r[series]) },
        (isLine ? num(r[series], digits) : signed(r[series], 1)) + " " + unit)),
        ok(r.twii) ? h("span", {}, "加權 ", h("b", {}, num(r.twii, 0))) : null].filter(Boolean));
    };
    const applyTheme = () => { c1.applyOptions(themeOpts()); c2.applyOptions(themeOpts()); draw(); };
    function draw() {
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.n === state.n)));
      const win = rows.slice(-state.n), up = css("--up"), down = css("--down");
      s1.setData(win.map((r) => (isLine ? { time: r.date, value: r[series] } : { time: r.date, value: r[series], color: r[series] >= 0 ? up : down })));
      if (isLine) s1.applyOptions({ color: css("--ma1") });
      markLines.forEach((l) => l.applyOptions({ color: css("--ink-3") }));
      s2.applyOptions({ color: css("--ink-2") });
      s2.setData(win.filter((r) => ok(r.twii)).map((r) => ({ time: r.date, value: r.twii })));
      fit(c1); fit(c2);
      show(win[win.length - 1]);
    }
    const onMove = (p) => {
      if (!p.time) return show(rows[rows.length - 1]);
      const key = typeof p.time === "object" ? `${p.time.year}-${String(p.time.month).padStart(2, "0")}-${String(p.time.day).padStart(2, "0")}` : p.time;
      show(byDate.get(key));
    };
    c1.subscribeCrosshairMove(onMove); c2.subscribeCrosshairMove(onMove);
    charts.add(applyTheme);
    const dispose = () => { charts.delete(applyTheme); liveCharts.delete(dispose); c1.remove(); c2.remove(); };
    root.destroy = dispose;
    liveCharts.add(dispose);
    applyTheme();
    return root;
  }

  // ---------- drawer ----------
  // The open drawer is part of the URL (#view?s=SYMBOL), so a reload, the Back button and a
  // shared link all land on the same thing. openDrawer/closeDrawer only change the hash;
  // route() then calls showDrawer/hideDrawer.
  let drawerEl = null, drawerSym = null, lastFocus = null, current = null;
  const parseHash = () => {
    const [n, q] = location.hash.slice(1).split("?");
    return { name: views[n] ? n : "overview", p: new URLSearchParams(q || "") };
  };
  const setHash = (name, params) => {
    const q = new URLSearchParams(params).toString();
    const next = "#" + name + (q ? "?" + q : "");
    if (location.hash === next) route(); else location.hash = next;
  };
  function hideDrawer() {
    if (!drawerEl) return;
    drawerEl.querySelectorAll(".stack").forEach((n) => n.destroy && n.destroy());
    drawerEl.remove(); drawerEl = null; drawerSym = null;
    document.body.style.overflow = "";
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }
  const closeDrawer = () => { if (drawerEl) setHash(current || "overview", {}); };
  function openDrawer(symbol) {
    if (!bySymbol.has(symbol) && !S.indices.some((i) => i.symbol === symbol)) return;
    lastFocus = document.activeElement;
    setHash(current || "overview", { s: symbol });
  }
  // One score for every horizon; what differs per horizon is the width of the range and the
  // base rates. Stocks show the chance of beating their market's median stock; indices show how
  // often they have risen over that span in the last five years.
  function forecastGrid(horizons, item) {
    const stock = item && item.rank;
    const rows = Object.entries(horizons).map(([k, f]) => [
      h("span", { class: "h" }, "未來" + HZ[k]),
      h("span", { class: "b" },
        h("span", { class: "small muted num" }, `一個標準差區間 ${px(f.low)} – ${px(f.high)}`),
        stock ? h("span", { class: "small" }, item.market === "US" && !item.edge ? "跑贏同市場中位數：無可驗證優勢"
          : ["同排名股票跑贏同市場中位數的比例 ", h("b", { class: "num" }, nf(0).format(f.p_beat * 100) + "%"), h("span", { class: "muted" }, "（擲硬幣 50%；屬上限）")]) : null,
        ok(f.p_up) && !stock ? h("span", { class: "small muted" }, "近 5 年上漲比率 " + nf(0).format(f.p_up * 100) + "%") : null)]);
    return h("div", { class: "stack", style: "gap:8px" },
      stock ? h("div", { class: "row" }, h("span", { class: "small" }, rankText(item) + "（同市場）")) : null,
      h("div", { class: "fc" }, rows));
  }
  const GRADE = { 優異: 2, 良好: 1, 普通: 0, 偏弱: -1, 落後: -2 };
  const gradePill = (r) => (r.grade ? h("span", { class: "pill s" + GRADE[r.grade] }, r.grade) : h("span", { class: "muted small" }, "未滿一年"));
  const scoreCell = (r) => (ok(r.score)
    ? h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter((r.score - 50) / 50), h("b", { class: "num" }, num(r.score, 0)))
    : h("span", { class: "muted" }, "–"));
  const beatText = (v) => (ok(v) ? `贏過 ${num(v, 0)}%` : "–");
  const FP = [["1m", "1 個月"], ["3m", "3 個月"], ["6m", "6 個月"], ["ytd", "今年以來"], ["1y", "1 年"], ["3y", "3 年累積"], ["5y", "5 年累積"], ["10y", "10 年累積"]];

  function fundDrawer(r) {
    const part = (k, label, hint) => [h("span", {}, label), ok(r.parts[k]) ? meter((r.parts[k] - 50) / 50) : h("span", { class: "muted small" }, "無資料"),
      h("span", { class: "num", title: hint }, ok(r.parts[k]) ? num(r.parts[k], 0) : "–")];
    const info = [["基金公司", r.company], ["類別", r.industry], ["計價幣別", r.currency], ["風險等級", r.rr ? "RR" + r.rr : null],
      ["晨星星等", r.stars ? "★".repeat(r.stars) : null], ["經理費", ok(r.fee) ? num(r.fee, 2) + "%" : null], ["成立日", r.inception],
      ["基金規模", ok(r.aum) ? num(r.aum / 1e8, 0) + " 億 " + (r.aum_currency || "") : null], ["配息", r.dist || "不配息／累積"],
      ["經理人", r.manager], ["對比指數", r.bench_name]].filter(([, v]) => v);
    return [
      h("div", { class: "drawer-head" },
        h("div", {}, h("div", { class: "t" }, r.name, h("span", { class: "chip mk" }, r.group), gradePill(r), star(r.symbol)),
          h("div", { class: "row" }, h("span", { class: "p num" }, num(r.close, r.close < 100 ? 4 : 2)), numEl(pct(r.chg1d, 2), r.chg1d),
            h("span", { class: "muted small" }, `淨值日 ${r.date}・${r.currency}`))),
        h("button", { class: "close", type: "button", onclick: closeDrawer }, "關閉")),
      h("div", { class: "panel" }, priceChart(r.symbol, { range: "2y" })),
      h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "績效評估"), scoreCell(r)),
        h("p", {}, r.verdict),
        h("div", { class: "factors" },
          part("peer", "同類排名", "近 1／3／5 年贏過同類基金的比例"), part("excess", "勝過同類平均", "年化報酬與同類平均的差距"),
          part("sharpe", "風險調整報酬", "Sharpe ratio 在同組基金中的位置"), part("drawdown", "抗跌", "三年最大回檔在同組基金中的位置"),
          part("consistency", "穩定度", "6 個月、1 年、3 年、5 年中贏過一半同類的期數比例")),
        h("p", { class: "chart-note" }, "五個面向各換算成 0–100 分，權重為同類排名 35%、勝過同類平均 20%、風險調整報酬 20%、抗跌 15%、穩定度 10%；風險調整報酬與抗跌是在同組基金裡比。管理費超過 1% 的部分，每 0.5 個百分點扣 5 分" + (r.fee_penalty ? `（本基金扣 ${num(r.fee_penalty, 0)} 分）` : "") + "。這樣比的是「同一類裡挑哪一檔」，而不是哪個市場過去漲最多。")),
      h("div", { class: "panel" }, h("h3", {}, "各期間報酬與同類排名"), table([
        { key: "p", label: "期間", left: true, cell: ([, label]) => label },
        { key: "f", label: "本基金", cell: ([k]) => numEl(pct(r.ret[k]), r.ret[k]) },
        { key: "b", label: "對比指數", cell: ([k]) => numEl(pct(r.bench[k]), r.bench[k]) },
        { key: "c", label: "同類平均", cell: ([k]) => numEl(pct(r.cat[k]), r.cat[k]) },
        { key: "r", label: "同類排名", cell: ([k]) => h("span", { class: "num" }, r.rank[k] ? `${r.rank[k].rank} / ${r.rank[k].total}` : "–") },
        { key: "w", label: "贏過同類", cell: ([k]) => h("span", { class: "num" }, r.rank[k] ? num(r.rank[k].beat, 0) + "%" : "–") }],
      FP.filter(([k]) => ok(r.ret[k]))),
        h("p", { class: "chart-note" }, `報酬為${r.currency}計價、含配息，來源為鉅亨網（晨星資料）。` + (ok(r.ret_1y_twd) && r.currency !== "新台幣" ? `換算新台幣的近一年報酬為 ${pct(r.ret_1y_twd)}。` : ""))),
      h("div", { class: "grid g2" },
        h("div", { class: "panel" }, h("h3", {}, "風險"), h("div", { class: "kv" }, [
          ["年化波動（3 年）", ok(r.vol_3y) ? num(r.vol_3y * 100, 1) + "%" : "–"], ["年化波動（1 年）", ok(r.vol_1y) ? num(r.vol_1y * 100, 1) + "%" : "–"],
          ["最大回檔（3 年）", pct(r.mdd_3y)], ["最大回檔（1 年）", pct(r.mdd_1y)], ["Sharpe（3 年）", num(r.sharpe, 2)],
          ["正報酬月份比例", ok(r.win_months) ? num(r.win_months * 100, 0) + "%" : "–"]].map(([k, v]) =>
          h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num" }, v)))),
          h("p", { class: "chart-note", style: "margin-top:10px" }, "風險數字由每日淨值計算，淨值不含配息還原。")),
        h("div", { class: "panel" }, h("h3", {}, "基金資料"), h("div", { class: "src" }, info.map(([k, v]) => [h("span", { class: "muted" }, k), h("span", {}, v)])))),
      h("div", { class: "panel" }, h("h3", {}, "淨值趨勢（技術面）"), forecastGrid(r.horizons, r),
        h("p", { class: "chart-note" }, "只看淨值走勢的趨勢與動能，與上方的績效評估是兩回事：績效看過去值不值得持有，趨勢看目前的方向。")),
    ];
  }

  function showDrawer(symbol) {
    hideDrawer();
    const r = bySymbol.get(symbol), ix = S.indices.find((i) => i.symbol === symbol);
    const item = r || ix;
    if (!item) return;
    drawerSym = symbol;
    if (r && r.kind === "fund") {
      const fd = h("div", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": r.name + " 績效評估" }, fundDrawer(r));
      drawerEl = h("div", { class: "scrim", onclick: (e) => { if (e.target === drawerEl) closeDrawer(); } }, fd);
      document.body.append(drawerEl);
      document.body.style.overflow = "hidden";
      fd.querySelector(".close").focus();
      return;
    }
    const body = [];
    body.push(h("div", { class: "drawer-head" },
      h("div", {}, h("div", { class: "t" }, item.name, r ? h("span", { class: "num muted small" }, r.code) : null, r ? mkChip(r.market, r.symbol) : null,
        r ? h("span", { class: "chip" }, r.industry) : null, r ? star(symbol) : null),
      h("div", { class: "row" }, h("span", { class: "p num" }, px(item.close)), numEl(pct(item.chg1d, 2), item.chg1d),
        h("span", { class: "muted small" }, "資料日 " + item.date)),
      r ? tagChips(r) : null),
      h("button", { class: "close", type: "button", onclick: closeDrawer }, "關閉")));
    body.push(h("div", { class: "panel" }, priceChart(symbol, { range: "6m", plan: r && r.plan })));
    if (item.horizons) body.push(h("div", { class: "panel" }, h("h3", {}, "相對強弱與區間", help("labels", "預測標籤")), forecastGrid(item.horizons, r)));
    if (r) {
      const F = [["tech", "技術（慢速分數＋距 52 週高點）"], ["chips", "籌碼"], ["fund", "基本面"]];
      const W = ((r.market === "US" ? S.weights_us : S.weights) || {})["1m"] || {};
      const C = r.contrib || {};
      body.push(h("div", { class: "grid g2" },
        h("div", { class: "panel" }, h("h3", {}, "排名從哪裡來（−1 至 +1）", help("score", "分數")), h("div", { class: "factors" },
          F.map(([k, t]) => [h("span", {}, t), ok(r.factors[k]) ? meter(r.factors[k]) : h("span", { class: "muted small" }, "無資料"),
            h("span", { class: "num " + cls(r.factors[k]) }, ok(r.factors[k]) ? signed(r.factors[k], 2) : "–")]),
          h("span", {}, h("b", {}, "綜合")), meter(r.overall), h("b", { class: "num " + cls(r.overall) }, signed(r.overall, 2))),
          h("p", { class: "small", style: "margin-top:8px" }, `綜合 ${signed(r.overall, 2)} ＝ ` + F.filter(([k]) => ok(C[k])).map(([k, t]) => `${t.replace(/（.*）/, "")} ${signed(C[k], 2)}`).join(" ＋ ")
            + `（權重 技術 ${num(W.tech || 0, 2)}・籌碼 ${num(W.chips || 0, 2)}・基本面 ${num(W.fund || 0, 2)}，缺資料的項目不計）`),
          h("p", { class: "small muted" }, `參考（不直接計分）：趨勢 ${ok(r.factors.trend) ? signed(r.factors.trend, 2) : "–"}・動能 ${ok(r.factors.momentum) ? signed(r.factors.momentum, 2) : "–"}・Podcast 聲量 ${ok(r.factors.buzz) ? signed(r.factors.buzz, 2) : "–"}（權重 0）`),
          r.setup ? h("p", { style: "margin-top:10px" }, h("span", { class: "chip mk" }, r.setup)) : null,
          h("p", { class: "chart-note", style: "margin-top:8px" }, "籌碼與基本面的權重都還沒有用足夠歷史驗證，實際影響也不同：拿掉籌碼，台股前 12 名會換 4 檔；拿掉基本面只換 1 檔。本站從 2026 年 11 月起保存個股法人與收盤資料，累積後再依事先寫好的規則決定權重。", help("tactical", "籌碼分與基本面分"))),
        h("div", { class: "panel" }, h("h3", {}, "多空理由"),
          h("ul", { class: "why" }, r.reasons.map((t) => h("li", { class: "pro" }, t)), r.risks.map((t) => h("li", { class: "con" }, t)),
            !r.reasons.length && !r.risks.length ? h("li", { class: "muted" }, "目前沒有明顯訊號") : null))));
      body.push(h("div", { class: "panel" }, h("h3", {}, "參考價位與操作", help("size", "建議部位上限")),
        h("div", { class: "plan" }, planCells(r)),
        h("p", { class: "stance", style: "margin-top:10px" }, r.plan.stance),
        r.watch_first ? h("p", { class: "small" }, h("span", { class: "chip warn" }, "先觀察"), " " + r.watch_first.join("；") + "。這些不代表會漲或會跌，是買進前要先想清楚的事實：股東少、漲得平穩又急的股票，反轉時可能連續跳空穿過停損；停損很遠代表同樣的虧損預算只能買很少。") : null,
        r.plan.stop_note || r.plan.stop_hit ? h("p", { class: "small muted" }, r.plan.stop_hit ? `收盤已低於先前的停損 ${px(r.plan.stop_hit)}。` : r.plan.stop_note) : null,
        h("p", { class: "chart-note" }, "+1σ 上緣只是波動區間的上緣，不是目標價：排名前段的股票一個月內觸及它的比例約 36%。停損用來控制虧損與波動，不是預測：回測中被停損的股票，一個月後有 66% 價格高於出場價。部位大小比停損更重要。" +
          (ok(r.plan.size) ? `部位上限＝1% ÷ 停損距離：就算停損出場，這一檔也只讓整體資金虧 1%。以 100 萬資金為例，這檔最多約 ${num(r.plan.size * 100, 0)} 萬。` : ""))));
      const kv = r.kind === "etf"
        ? [["類別", r.industry], ["近一月", pct(r.r20)], ["近一季", pct(r.r60)], ["今年以來", pct(r.ytd)], ["近一年" + (r.market === "TW" ? "（含息）" : "（不含息）"), pct(r.r250)],
          ["近 12 月配息率", ok(r.dist12) ? num(r.dist12 * 100, 1) + "%" : "–"], ["折溢價", ok(r.prem) ? signed(r.prem, 2) + "%" + (r.nav_time ? `（${r.nav_time.slice(0, 5)}）` : "") : "–"],
          ["規模", ok(r.aum) ? nf(0).format(r.aum / 1e8) + " 億" : "–"], ["單日申購贖回", ok(r.flow_day) ? signed(r.flow_day / 1e8, 1) + " 億" : "–"],
          ["一年最大回檔", pct(r.mdd)], ["年化波動", ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–"], ["RSI(14)", num(r.rsi, 0)],
          ["外資 5 日", ok(r.foreign5) ? signed(r.foreign5, 0) + " 張" : "–"], ["投信 5 日", ok(r.trust5) ? signed(r.trust5, 0) + " 張" : "–"]]
        : r.market === "TW"
        ? [["本益比", num(r.pe, 1)], ["股價淨值比", num(r.pb, 2)], ["殖利率", ok(r.yield) ? num(r.yield, 2) + "%" + (r.yield_odd ? "（異常，疑似一次性配發，不計分）" : "") : "–"],
          ["月營收年增", pctRaw(r.rev_yoy)], ["累計營收年增", pctRaw(r.rev_cum_yoy)],
          ["3 月／12 月營收", ok(r.rev_ratio) ? pct(r.rev_ratio - 1, 0) : "–"], [`今年累計 EPS${r.eps_period ? "（" + r.eps_period.replace("Q", " 年前 ") + " 季）" : ""}`, num(r.eps, 2)], ["近四季 EPS", num(r.eps_ttm, 2)],
          ["外資 5 日", ok(r.foreign5) ? signed(r.foreign5, 0) + " 張" : "–"], ["投信 5 日", ok(r.trust5) ? signed(r.trust5, 0) + " 張" : "–"],
          ["融資餘額", ok(r.margin) ? num(r.margin, 0) + " 張" : "–"], ["融資 5 日變化", pct(r.margin_chg5)],
          ["估算融資維持率", ok(r.maint_est) ? num(r.maint_est * 100, 0) + "%" : "–"],
          ["合約負債季增", ok(r.cl_qoq) && !/金融|保險/.test(r.industry) ? pct(r.cl_qoq, 0) : "–"],
          ["董監質押比", ok(r.insider_pledge) ? pct(r.insider_pledge).replace("+", "") : "–"], ["RSI(14)", num(r.rsi, 0)],
          ["純超額強度", ok(r.imom) ? signed(r.imom, 1) : "–"],
          ["距 52 週高點", ok(r.hi52) ? pct(r.hi52 - 1) : "–"],
          ["千張大戶持股", r.tdcc ? num(r.tdcc.big1000, 1) + "%" + (ok(r.tdcc.chg1000) ? `（週 ${signed(r.tdcc.chg1000, 2)}）` : "") : "–"],
          ["股東人數週變化", r.tdcc && ok(r.tdcc.chg_holders) ? pct(r.tdcc.chg_holders) : "–"],
          ["當沖比率（最新一日）", ok(r.daytrade) ? num(r.daytrade * 100, 0) + "%" : "–"],
          ["毛利率／營益率（今年累計）", ok(r.gm) ? `${num(r.gm, 1)}%／${ok(r.om) ? num(r.om, 1) + "%" : "–"}` : "–"],
          ["稅後純益率（今年累計）", ok(r.nm) ? num(r.nm, 1) + "%" : "–"],
          ["配發率（殖利率 × 本益比）", ok(r.payout) ? num(r.payout * 100, 0) + "%" + (r.payout > 1 ? "（高於獲利）" : "") : "–"],
          ["外資持股比例", ok(r.qfii) ? num(r.qfii, 1) + "%" : "–"],
          ["外資持股 20 日", ok(r.qfii_chg20) ? signed(r.qfii_chg20, 2) + " pp" : "–"],
          ["外資尚可投資", ok(r.qfii_room) ? num(r.qfii_room, 1) + "%" : "–"]]
        : [["本益比 (TTM)", num(r.pe, 1)], ["EPS (TTM)", num(r.eps_ttm, 2)], ["EPS 年增", pctRaw(r.eps_yoy, 0)],
          ["營收年增", pctRaw(r.rev_yoy, 0)], ["議員買 / 賣", `${r.congress_buys ?? 0} / ${r.congress_sells ?? 0}`],
          ["名人基金淨加碼", signed(r.guru_net, 0)], ["RSI(14)", num(r.rsi, 0)], ["年化波動", ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–"],
          ["內部人 90 日買進", r.insider_us ? r.insider_us.buys + " 筆・" + musd(r.insider_us.buy_value) : "–"],
          ["內部人 90 日賣出", r.insider_us ? r.insider_us.sells + " 筆・" + musd(r.insider_us.sell_value) : "–"],
          ["純超額強度", ok(r.imom) ? signed(r.imom, 1) : "–"]];
      body.push(h("div", { class: "panel" }, h("h3", {}, "關鍵數據"), h("div", { class: "kv" },
        kv.map(([k, v]) => h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num " + (/^[+-][\d.,]+%$/.test(v) ? cls(parseFloat(v)) : "") }, v))))));
    }
    const drawer = h("div", { class: "drawer", role: "dialog", "aria-modal": "true", "aria-label": item.name + " 詳細資料" }, body);
    drawerEl = h("div", { class: "scrim", onclick: (e) => { if (e.target === drawerEl) closeDrawer(); } }, drawer);
    document.body.append(drawerEl);
    document.body.style.overflow = "hidden";
    drawer.querySelector(".close").focus();
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  // ---------- views ----------
  const head = (title, lead) => h("div", { class: "stack" }, h("h2", {}, title), lead ? h("p", { class: "lead" }, lead) : null);
  const statBox = (k, v, s, c) => h("div", { class: "stat" }, h("div", { class: "k" }, k),
    h("div", { class: "v " + (/[\u4e00-\u9fff]{2}/.test(v) ? "txt " : "num ") + (c || "") }, v), s ? h("div", { class: "s" }, s) : null);
  const mdLite = (text) => text.split(/\n+/).filter((l) => l.trim()).map((l) =>
    (l.startsWith("#") ? h("h4", {}, l.replace(/^#+\s*/, "")) : h("p", {}, l.replace(/\*\*/g, ""))));

  // Commentary is written from a given data date; say so when it lags the numbers on screen.
  const aiBlock = (title, text) => {
    if (!AI || !text) return null;
    const stale = AI.data_date !== S.data_date;
    return h("div", { class: "panel" }, h("div", { class: "sec-head" }, h("h3", {}, title),
      h("span", { class: "chip " + (stale ? "warn" : "mk") }, stale ? `撰寫於資料日 ${AI.data_date}，尚未更新` : "Claude 依本週數據撰寫")),
      h("div", { class: "ai note" }, mdLite(text)));
  };

  const planCells = (r) => [["支撐", px(r.plan.support)],
    ["停損" + (ok(r.plan.stop_pct) ? `（−${num(r.plan.stop_pct * 100, 1)}%）` : ""), px(r.plan.stop)],
    ["一個月 +1σ 上緣（非目標）", px(r.plan.target)],
    ["ATR%（日均波幅）", ok(r.plan.atr_pct) ? num(r.plan.atr_pct * 100, 1) + "%" : "–"],
    ["部位上限", ok(r.plan.size) ? num(r.plan.size * 100, 0) + "% 資金" : "–"]].map(([k, v]) =>
    h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num" }, v)));
  const dual = (r) => (ok(r.tactical) || ok(r.structural) ? h("div", { class: "dual" },
    [["tactical", "籌碼"], ["structural", "基本面"]].map(([k, t]) => h("span", {}, t, ok(r[k]) ? meter(r[k]) : null,
      h("b", { class: "num " + cls(r[k]) }, ok(r[k]) ? signed(r[k], 2) : "–"))),
    r.setup ? h("span", { class: "chip mk" }, r.setup) : null) : null);
  const TONE = { 1: ["s1", "偏多訊號"], 0: ["s0", "中性"], "-1": ["s-1", "偏空訊號"] };
  // Evidence grade (tested weekly on this site's data) and where today's value sits in its own year.
  const EV_TIP = { A: "五年以上資料、兩段期間方向一致", B: "有統計關係，但樣本短或前後不一致", C: "和加權指數之後的漲跌、回檔沒有可測的關係", D: "歷史資料不足一百二十個交易日" };
  const evidenceRow = (e) => (e ? h("div", { class: "row small", style: "gap:8px" },
    h("span", { class: "chip " + (e.grade === "A" || e.grade === "B" ? "mk" : "flag"), title: EV_TIP[e.grade] }, `證據 ${e.grade}・${e.text}`),
    ok(e.pct) ? h("span", { class: "muted num" }, `高於一年 ${num(e.pct * 100, 0)}% 的日子`) : null) : null);
  const leadCards = (keys) => {
    const cards = LEAD.cards.filter((c) => !keys || keys.includes(c.key));
    return cards.length ? h("div", { class: "leads" }, cards.map((c) => h("div", { class: "lead-card" },
      h("div", { class: "row", style: "justify-content:space-between" }, h("span", { class: "nm" }, c.name, help(CARD_KB[c.key] || c.key, c.name)),
        c.tone ? h("span", { class: "pill " + TONE[c.tone][0] }, TONE[c.tone][1]) : null),
      h("div", { class: "row" }, h("b", { class: "num lv" }, c.value_text), h("span", { class: "small muted num" }, c.change_text)),
      c.spark && c.spark.length > 2 ? spark(c.spark) : null,
      evidenceRow(c.evidence),
      h("p", { class: "small" }, c.reading)))) : null;
  };

  const leadGroups = () => [...new Set(LEAD.cards.map((c) => c.group || "其他"))].map((g) =>
    h("div", { class: "stack" }, h("h4", {}, g), leadCards(LEAD.cards.filter((c) => (c.group || "其他") === g).map((c) => c.key))));
  // Open interest by strike: puts to the left (support below the index), calls to the right (resistance above).
  function optionLadder(o) {
    const top = Math.max(...o.ladder.map((r) => Math.max(r[1], r[2]))) || 1;
    const bar = (v, side) => { const b = h("i", { class: side }); b.style.width = (v / top) * 100 + "%"; return b; };
    const rows = [];
    let spotDone = false;
    [...o.ladder].reverse().forEach(([k, c, pv]) => {
      if (!spotDone && k < o.spot) { rows.push(h("div", { class: "spot" }, `加權指數 ${num(o.spot, 0)}`)); spotDone = true; }
      const tag = k === o.call_wall ? "壓力" : k === o.put_wall ? "支撐" : k === o.max_pain ? "Max Pain" : null;
      rows.push(h("div", { class: "lr" }, h("span", { class: "p" }, h("span", { class: "num small" }, pv ? num(pv, 0) : ""), bar(pv, "put")),
        h("span", { class: "k num" }, num(k, 0), tag ? h("span", { class: "chip" + (tag === "Max Pain" ? "" : " mk") }, tag) : null),
        h("span", { class: "c" }, bar(c, "call"), h("span", { class: "num small" }, c ? num(c, 0) : ""))));
    });
    return h("div", { class: "stack" },
      h("div", { class: "row" }, h("b", {}, `${o.expiry.slice(5).replace("-", "/")} 結算${o.monthly ? "（月選）" : "（週選）"}`),
        h("span", { class: "small muted" }, `下檔支撐 ${num(o.put_wall, 0)}・上檔壓力 ${num(o.call_wall, 0)}・Max Pain ${num(o.max_pain, 0)}`)),
      h("div", { class: "ladder" }, h("div", { class: "lr head" }, h("span", { class: "p" }, "賣權未平倉（口）"), h("span", { class: "k" }, "履約價"), h("span", { class: "c" }, "買權未平倉（口）")), rows));
  }
  const REGIME = { risk_on: ["風險偏好", "s1", "信用利差仍窄、半導體領漲：股市拉回較可能是買點，可維持股票型與科技型部位。"],
    risk_off: ["風險趨避", "s-1", "信用利差偏高或快速擴大，且半導體落後：宜提高債券、多重資產或現金比重，不急著攤平股票型部位。"],
    mixed: ["輪動／觀望", "s0", "信用市場與半導體領導地位的訊號不一致：問題較可能出在類股輪動，而不是整體資金面。分散配置、避免單押科技。"] };
  const regimePanel = () => {
    const g = LEAD.regime;
    if (!g) return null;
    const [name, tone, text] = REGIME[g.state];
    return h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "目前的風險環境", help("regime", "風險環境判讀")), h("span", { class: "pill " + tone }, name)),
      h("p", {}, text), leadCards(["hy", "semis"]),
      h("p", { class: "chart-note" }, "由美國高收益債利差與半導體相對強弱（SOXX ÷ SPY）合併判讀，切點為市場常用的經驗值，尚未用本站資料驗證。"));
  };

  const views = {
    async overview() {
      const main = S.indices.filter((i) => i.horizons);
      const funnel = () => {
        if (!MK || !MK.risk) return null;
        const R = MK.risk, G = MK.regime, D = S.picks_diff || {}, warn = MK.alerts.filter((a) => a.level === 2);
        const mark = h("i"); mark.style.left = Math.max(0, Math.min(100, R.score)) + "%";
        const step = (n, title, body, href, link) => h("div", { class: "fn" }, h("div", { class: "fn-h" }, h("span", { class: "fn-n" }, n), h("b", {}, title)), body,
          h("a", { class: "small", href }, link));
        return h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "本週決策順序"), h("span", { class: "small muted" }, "先看能放多少部位，再看放在哪裡")),
          h("div", { class: "funnel" },
            step("1", "短期回檔風險與持股水位", h("div", { class: "stack", style: "gap:6px" },
              h("div", { class: "row" }, h("b", { class: "num", style: "font-size:22px" }, num(R.score, 0)), h("span", { class: "pill " + RISK_CLS[R.tone] }, R.name + "風險"),
                ok(MK.risk_prev) ? h("span", { class: "small muted num" }, `較 ${(MK.risk_prev_date || "").slice(5).replace("-", "/")} ${signed(R.score - MK.risk_prev, 0)}`) : h("span", { class: "small muted" }, "上週尚無資料")),
              h("div", { class: "riskbar", role: "img", "aria-label": `風險分數 ${num(R.score, 0)}，滿分 100` }, mark),
              h("div", {}, "參考持股水位 ", h("b", {}, R.exposure), h("span", { class: "small muted" }, "（經驗法則，未經驗證）")),
              h("div", { class: "small muted" }, `市場週期：${G.name}（第 ${G.days} 個交易日）`)), "#safety", "看組成與歷史回檔機率"),
            step("2", "要先留意的警報", warn.length ? h("ul", { class: "why" }, warn.slice(0, 4).map((a) => h("li", { class: "con" }, a.title)),
              warn.length > 4 ? h("li", { class: "muted" }, `另有 ${warn.length - 4} 項`) : null) : h("p", { class: "muted" }, `目前沒有警戒等級的警報（留意 ${MK.alerts.filter((a) => a.level === 1).length} 項）。`), "#safety", `全部 ${MK.alerts.length} 項警報`),
            step("3", "本週名單的變化", h("div", { class: "stack", style: "gap:6px" },
              h("div", {}, S.picks.slice(0, 3).map((r) => r.name).join("、"), h("span", { class: "muted small" }, " 等 20 檔")),
              D.ref_date ? h("div", { class: "small" }, `較 ${D.ref_date.slice(5)} 名單：新進 ${D.new} 檔` + (D.dropped.length ? `，移出 ${D.dropped.map((x) => x.name).join("、")}` : "，沒有移出")) : h("div", { class: "small muted" }, "下次更新起顯示與上期名單的差異"),
              h("div", { class: "small muted" }, "每檔附部位上限：風險高時，總部位先縮，再談選股。")), "#picks", "看精選 20 檔"),
            step("4", "接下來兩週的事件", (S.calendar || []).length ? h("ul", { class: "cal" }, (() => {
              const mine = (S.calendar || []).filter((c) => c.symbol && (WATCH.has(c.symbol) || S.picks.some((p) => p.symbol === c.symbol)));
              const rest = (S.calendar || []).filter((c) => !mine.includes(c) && !c.symbol);
              return [...mine, ...rest].slice(0, 5).map((c) => h("li", {}, h("b", { class: "num" }, c.date.slice(5).replace("-", "/")), " ", c.text,
                mine.includes(c) ? h("span", { class: "chip flag", style: "margin-left:4px" }, WATCH.has(c.symbol) ? "自選" : "精選") : null));
            })()) : h("p", { class: "muted" }, "沒有已知事件。"), "#method", "完整行事曆在「方法與來源」")),
          h("p", { class: "chart-note" }, "持股水位是依風險等級給的一般性區間，不是個人化建議；風險分數的切點與歷史表現見「市場安全」。"));
      };
      const weeklyCard = () => {
        const W = S.weekly;
        if (!W) return null;
        if (!W.ref) return h("div", { class: "panel stack" }, h("h3", {}, "本週變化"),
          h("p", { class: "muted small" }, `本站從 ${W.week} 起每週保存一份快照。下週起這裡會列出和上週相比的變化：短期回檔風險與主要原因、位置大幅移動的指標、精選名單的進出與原因。`));
        const items = [];
        if (W.risk) items.push(h("li", {}, `短期回檔風險 ${num(W.risk.prev, 0)} → ${num(W.risk.now, 0)}（${W.risk.prev_name || "–"} → ${W.risk.name}）`
          + (W.risk.drivers.length ? "，主要來自 " + W.risk.drivers.map((d) => `${d.name} ${num(d.from, 0)}→${num(d.to, 0)}`).join("、") : "")));
        if ((W.cards || []).length) items.push(h("li", {}, "位置大幅移動的指標：" + W.cards.map((c) => `${c.name}（一年百分位 ${num(c.from * 100, 0)} → ${num(c.to * 100, 0)}）`).join("、")));
        if ((W.picks_in || []).length) items.push(h("li", {}, "新進精選：" + W.picks_in.map((x) => x.name).join("、")));
        if ((W.picks_out || []).length) items.push(h("li", {}, "移出精選：" + W.picks_out.map((x) => `${x.name}（${x.why}）`).join("、")));
        return h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "本週變化"), h("span", { class: "small muted num" }, `較 ${W.ref_date}（${W.ref}）`)),
          items.length ? h("ul", { class: "why" }, items) : h("p", { class: "muted" }, "和上週相比沒有明顯變化。"));
      };
      return [
        weeklyCard(),
        funnel(),
        h("div", { class: "tiles" }, S.indices.map((i) => h("button", { class: "tile", type: "button", onclick: () => openDrawer(i.symbol) },
          h("span", { class: "nm" }, i.name), h("span", { class: "px num" }, px(i.close)),
          h("span", { class: "chg" }, h("span", { class: "num " + cls(i.chg1d) }, "日 " + chgText(i, i.chg1d, 2)), h("span", { class: "num " + cls(i.chg5d) }, "週 " + chgText(i, i.chg5d))),
          spark(i.spark)))),
        h("div", { class: "grid g2" },
          h("div", { class: "panel note" }, h("h3", {}, "本週判讀"), S.narrative.map((n) => h("div", {}, h("h4", {}, n.title), h("p", {}, n.text)))),
          h("div", { class: "panel stack" }, h("h3", {}, "趨勢預測：一週／一月／一季"),
            main.map((i) => h("div", { class: "stack" }, h("div", { class: "row" }, h("b", {}, i.name), h("span", { class: "num muted small" }, px(i.close)),
              h("button", { class: "chip", type: "button", onclick: () => openDrawer(i.symbol) }, "看圖")), forecastGrid(i.horizons))),
            h("p", { class: "chart-note" }, "區間是一個標準差（約三分之二的情況落在其中），中心是現價，不是方向預測；上漲比率是近 5 年的歷史統計。",
              h("a", { href: "#learn?k=labels" }, "各種標籤與機率代表什麼？")))),
        aiBlock("AI 觀點", AI && AI.market),
        LEAD.cards.length ? h("div", { class: "panel stack" }, h("h3", {}, "領先指標"), leadGroups(),
          h("p", { class: "chart-note" }, "每張卡每週都用本站資料檢驗：它顯示的數值，和加權指數之後 5 日、20 日的報酬及 20 日內的最大回檔有沒有關係（落後一日，避免用到收盤後才知道的資訊）。A 有支持、B 證據弱、C 無證據、D 歷史不足。只有 A、B 級而且方向和解讀一致的卡才標偏多／偏空，其餘只顯示目前數值在自己一年歷史中的位置。這些指標都不計入任何分數。")) : null,
        (LEAD.options || []).length ? h("div", { class: "panel stack" }, h("h3", {}, "台指選擇權未平倉分布", help("optwall", "選擇權未平倉")),
          h("div", { class: "grid g2" }, LEAD.options.map(optionLadder)),
          h("p", { class: "chart-note" }, `${LEAD.options[0].date} 的未平倉量，各列出未平倉最多的履約價。賣方通常在最大未平倉的價位防守，結算前指數常被壓縮在支撐與壓力之間。`)) : null,
        h("div", { class: "panel" }, h("h3", {}, "台灣加權指數"), priceChart("TWII", { range: "6m" })),
      ];
    },

    async picks() {
      // Portfolio budget: the per-stock caps assume one stock at a time; this scales them to a total.
      const budgetPanel = () => {
        const ex = (MK && MK.risk && MK.risk.exposure_pct) || 50;
        const load = (k, d) => { try { const v = localStorage.getItem(k); return v === null || v === "" ? d : +v; } catch (e) { return d; } };
        const save = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } };
        const fxRate = (S.indices.find((x) => x.symbol === "USDTWD") || {}).close || 32;
        const field = (key, def, attrs, label) => { const el = h("input", { type: "number", value: load(key, def), ...attrs, "aria-label": label, oninput: () => { save(key, el.value); draw(); } }); return el; };
        const cash = field("budget_cash", 1000000, { min: 0, step: 10000, style: "width:8em" }, "總資金（台幣）");
        const share = field("budget_share", ex, { min: 0, max: 100, step: 5, style: "width:4.5em" }, "股票比例（%）");
        const tw = field("budget_tw", 60, { min: 0, max: 100, step: 10, style: "width:4.5em" }, "股票中台股的比例（%）");
        const out = h("div", { class: "stack", style: "gap:6px" });
        const lots = (n) => (n >= 1000 ? `${Math.floor(n / 1000)} 張` + (n % 1000 ? ` ${n % 1000} 股` : "") : `${n} 股（零股）`);
        const draw = () => {
          const A = Math.max(0, +cash.value || 0), B = Math.min(100, Math.max(0, +share.value || 0)) / 100, T = Math.min(100, Math.max(0, +tw.value || 0)) / 100;
          const rows = [];
          let loss = 0;
          [["TW", T], ["US", 1 - T]].forEach(([mk, part]) => {
            const ps = S.picks.filter((r) => r.market === mk);
            const raw = ps.map((r) => Math.min(0.25, 0.01 / Math.max((r.plan && r.plan.stop_pct) || 0.05, 0.01)));
            const sum = raw.reduce((a, x) => a + x, 0) || 1;
            ps.forEach((r, k) => {
              const amt = A * B * part * raw[k] / sum, price = mk === "US" ? r.close * fxRate : r.close;
              loss += amt * ((r.plan && r.plan.stop_pct) || 0);
              rows.push({ r, amt, shares: price ? Math.floor(amt / price) : 0 });
            });
          });
          const small = rows.filter((x) => x.r.market === "TW" && x.amt > 0 && x.amt < 23400).length;
          out.replaceChildren(
            h("p", { class: "small" }, `股票部位 ${nf(0).format(A * B)} 元：台股 ${num(T * 100, 0)}%、美股 ${num((1 - T) * 100, 0)}%。每個市場內依停損距離分配：停損越近可以放越多，單檔最多 25%。如果全部同時停損，約虧 ${nf(0).format(loss)} 元（總資金的 ${num(A ? loss / A * 100 : 0, 1)}%）。`),
            table([{ key: "n", label: "精選", left: true, cell: (x) => h("span", {}, x.r.name, " ", mkChip(x.r.market, x.r.symbol)) },
              { key: "s", label: "停損距離", val: (x) => x.r.plan && x.r.plan.stop_pct, cell: (x) => h("span", { class: "num" }, x.r.plan && ok(x.r.plan.stop_pct) ? num(x.r.plan.stop_pct * 100, 1) + "%" : "–") },
              { key: "a", label: "建議金額（台幣）", val: (x) => x.amt, cell: (x) => h("span", { class: "num" }, nf(0).format(x.amt)) },
              { key: "q", label: "約當股數", val: (x) => x.shares, cell: (x) => h("span", { class: "num" }, x.r.market === "TW" ? (x.shares ? lots(x.shares) : "不足 1 股") : (x.shares ? `${x.shares} 股` : "不足 1 股（可買零碎股）")) }],
              rows, { csv: "budget", sort: "a" }),
            small ? h("p", { class: "chart-note" }, `有 ${small} 檔台股單筆金額低於約 2.34 萬元：多數券商最低手續費 20 元，實際費率會比牌告高。`) : null);
        };
        draw();
        return h("div", { class: "panel stack" }, h("div", { class: "row", style: "flex-wrap:wrap;gap:8px" }, h("b", {}, "部位預算"), "總資金", cash, "元・股票比例", share, "%・其中台股", tw, "%"), out,
          h("p", { class: "chart-note" }, `股票比例預設 ${ex}%，取自短期回檔風險等級（${MK && MK.risk ? MK.risk.name : "–"}）；這是控制總部位的經驗法則，未經驗證，可自行調整。美股精選沒有可驗證的選股優勢，美股比例可以設成 0。台股與美股預設 60／40，只是中性的起點。數字只存在這個瀏覽器。`));
      };
      const portfolioPanel = () => {
        const P = S.portfolio;
        if (!P || !P.TW) return null;
        const box = (mk, label) => {
          const x = P[mk];
          if (!x) return null;
          return h("div", { class: "stack", style: "gap:4px" }, h("b", {}, label),
            h("div", { class: "small" }, `β ${ok(x.beta) ? num(x.beta, 2) : "–"}・年化波動 ${num(x.vol * 100, 0)}%・平均相關 ${ok(x.avg_corr) ? num(x.avg_corr, 2) : "–"}`),
            h("div", { class: "small" }, `一個月虧損超過 ${num(-x.loss95_1m * 100, 1)}%：依過去波動估計，大約每 20 個月會遇到一次。`),
            ok(x.hi52) ? h("div", { class: "small muted" }, `距 52 週高點中位數 ${pct(x.hi52 - 1)}（全體 ${pct(x.hi52_all - 1)}）・近 60 日漲幅中位數 ${pct(x.r60)}（全體 ${pct(x.r60_all)}）`) : null,
            (x.stress || []).length ? h("div", { class: "small" }, h("b", {}, "歷史重演"), `（近 5 年 ${mk === "US" ? "SPY" : "0050"} 最差的 20 日）：`,
              x.stress.map((t) => `${t.to.slice(0, 7)} ${pct(t.bench, 0)}，名單 ${pct(t.picks, 0)}`).join("；")) : null);
        };
        return h("div", { class: "panel stack" }, h("h3", {}, "這 20 檔放在一起的風險"),
          h("div", { class: "grid g3" }, box("TW", `台股 ${P.TW.n} 檔`), box("US", P.US ? `美股 ${P.US.n} 檔（美元計）` : "美股"), box("ALL", "合計（台幣計，台股 60%／美股 40%）")),
          h("p", { class: "chart-note" }, "依預設權重（停損越近權重越大）與近 60 日波動推估；急跌初期，近期波動會低估之後的風險，所以這不是最壞情況。名單集中在股價接近一年高點、近期大漲的股票，本質上是押動能延續。歷史重演是把今天的名單放回過去最差的幾段行情（當時還沒上市的股票用 β 換算），不含最近 60 個交易日，因為名單正是依那段表現選出來的；它是重演，不是預測。"));
      };
      const filter = { m: "ALL" };
      const grid = h("div", { class: "picks" });
      const draw = () => grid.replaceChildren(...S.picks.filter((r) => filter.m === "ALL" || r.market === filter.m).map((r, i) =>
        h("button", { class: "pick", type: "button", "data-sym": r.symbol, onclick: () => openDrawer(r.symbol) },
          h("div", { class: "pick-top" },
            h("div", { class: "pick-id" }, h("div", { class: "rk" }, `#${i + 1}・${r.industry}`, diffBadges(r)),
              h("div", { class: "t" }, r.name, h("span", { class: "num muted small" }, r.code), mkChip(r.market, r.symbol), star(r.symbol))),
            h("div", { class: "pick-px" }, h("div", { class: "p num" }, px(r.close)), h("div", { class: "num small " + cls(r.chg5d) }, "週 " + pct(r.chg5d)))),
          h("div", { class: "scorebar" }, "綜合", meter(r.overall), h("b", { class: "num " + cls(r.overall) }, signed(r.overall, 2)),
            h("span", { class: "chip mk" }, rankText(r)), r.kept ? h("span", { class: "chip", title: "排名仍在緩衝範圍內，續留名單" }, "續留") : null),
          r.watch_first ? h("div", { class: "tags" }, h("span", { class: "chip warn", style: "white-space:normal;text-align:left", title: "不代表會漲或會跌：買進前要先想清楚的事實" }, "先觀察：" + r.watch_first.join("・"))) : null,
          tagChips(r, r.watch_first ? 2 : 3),
          h("div", { class: "hz" }, relText(r) || "", dual(r)),
          h("ul", { class: "why" }, r.reasons.slice(0, 3).map((t) => h("li", { class: "pro" }, t)), r.risks.slice(0, 2).map((t) => h("li", { class: "con" }, t))),
          h("div", { class: "plan" }, planCells(r)),
          h("div", { class: "stance" }, r.plan.stance))));
      const seg = h("div", { class: "seg", role: "group", "aria-label": "市場" }, [["ALL", "全部"], ["TW", "台股"], ["US", "美股"]].map(([k, t]) =>
        h("button", { type: "button", "aria-pressed": String(k === "ALL"), onclick: (e) => {
          filter.m = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); draw();
        } }, t)));
      draw();
      const FW = S.follow || { rows: [], summary: {} };
      const trackRows = [...FW.rows].reverse();
      const track = h("div", { class: "panel stack" }, h("h3", {}, "照著精選操作的實際結果", help("track", "實際追蹤")),
        h("div", { class: "row", style: "gap:16px;flex-wrap:wrap" }, ["TW", "US"].map((m) => {
          const x = FW.summary[m];
          return x ? h("div", { class: "small" }, mkChip(m), " ", h("b", {}, x.verdict),
            x.weeks ? `・已計 ${x.weeks} 週，平均每週超額 ${pct(x.mean_excess, 2)}，累積 ${pct(x.growth, 1)}（同期 ${m === "TW" ? "0050" : "SPY"} ${pct(x.bench_growth, 1)}）` : "") : null;
        })),
        trackRows.length ? table([
          { key: "d", label: "名單日", left: true, cell: (r) => h("span", { class: "num" }, r.date) },
          { key: "m", label: "市場", left: true, cell: (r) => mkChip(r.market) },
          { key: "e", label: "賣出日", cell: (r) => h("span", { class: "num" }, r.open ? `${r.exit}（持有中）` : r.exit) },
          { key: "n", label: "檔數", cell: (r) => h("span", { class: "num" }, r.n) },
          { key: "r", label: "扣成本報酬", val: (r) => r.net, cell: (r) => numEl(pct(r.net, 2), r.net) },
          { key: "b", label: "同期 0050／SPY", val: (r) => r.bench, cell: (r) => numEl(pct(r.bench, 2), r.bench) },
          { key: "x", label: "超額報酬", val: (r) => r.excess, cell: (r) => numEl(pct(r.excess, 2), r.excess) },
          { key: "w", label: "跑贏同市場中位數的檔數", val: (r) => r.beat_median, cell: (r) => h("span", { class: "num" }, ok(r.beat_median) ? num(r.beat_median * 100, 0) + "%" : "–") }], trackRows, { csv: "follow" })
          : h("div", { class: "empty" }, "從下週開始累積：每週的名單會在下一個交易日開盤買進、下一份名單公布後的下一個交易日開盤賣出。"),
        h("p", { class: "chart-note" }, "規則事先寫定、不再更改：名單公布後的下一個交易日開盤等權買進，下一份名單公布後的下一個交易日開盤賣出；台股扣來回成本 0.585%（手續費兩次加證交稅），美股扣 0.1%。對照同市場的 0050／SPY 與同市場個股中位數。累積滿 26 週之前一律標「樣本不足」；之後若平均每週超額的 t 值低於 1，就寫「實際追蹤尚未顯示優勢」。"));
      return [h("div", { class: "sec-head" }, head("精選 20 檔", `從 ${S.counts.tw} 檔台股與 ${S.counts.us} 檔美股中，依同市場排名挑選：處置股不選、上週精選排名仍在前段就續留、同產業最多 3 檔、走勢高度相關的不重複挑。台股在前、美股在後。點卡片看圖表與完整數據。`), seg),
        h("p", { class: "small muted" }, "排名是同一市場內的相對強弱（台股 1 名最強），不是漲跌預測；百分比是過去約三年同排名的股票跑贏同市場中位數的比例。美股沒有測到可驗證的選股優勢，照實標示。部位上限＝願意承擔 1% 資金虧損時，這檔最多買多少。",
          h("a", { href: "#learn?k=picks" }, "選股規則")),
        portfolioPanel(),
        budgetPanel(),
        (S.picks_diff || {}).ref_date ? h("p", { class: "small" }, `與 ${S.picks_diff.ref_date} 的名單相比：新進 ${S.picks_diff.new} 檔（標示 NEW）` +
          (S.picks_diff.dropped.length ? `；移出 ${S.picks_diff.dropped.map((x) => x.name).join("、")}。` : "；沒有移出。") + "↑↓ 是名次變化，「連續 n 週」是連續上榜的週數。") : null,
        grid, track];
    },

    async flows() {
      const F = await load("flows"), s = F.summary || {};
      const state = { key: "foreign" };
      const SER = { foreign: "外資買賣超", trust: "投信買賣超", dealer: "自營商買賣超", total: "三大法人合計" };
      const holder = h("div");
      const drawChart = () => holder.replaceChildren(flowChart(F.history, state.key, SER[state.key], "億元"));
      const seg = h("div", { class: "seg", role: "group", "aria-label": "法人別" }, Object.entries(SER).map(([k, t]) =>
        h("button", { type: "button", "aria-pressed": String(k === state.key), onclick: (e) => {
          state.key = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); drawChart();
        } }, t.replace("買賣超", ""))));
      drawChart();
      const cols = (unitLabel) => [
        { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
        { key: "v", label: "5 日金額（億）", val: (r) => r.value, cell: (r) => numEl(signed(r.value, 1), r.value) },
        { key: "l", label: unitLabel, val: (r) => r.lots, cell: (r) => numEl(signed(r.lots, 0), r.lots) },
        { key: "s", label: "連續天數", val: (r) => r.streak, cell: (r) => numEl(r.streak ? `${r.streak > 0 ? "連買" : "連賣"} ${Math.abs(r.streak)} 日` : "–", r.streak) }];
      const open = (r) => bySymbol.has(r.code + ".TW") && openDrawer(r.code + ".TW");
      const recent = F.history.filter((r) => ok(r.foreign)).slice(-10).reverse();
      const fut = s.futures;
      return [
        head("外資與三大法人", `集中市場三大法人買賣超（資料至 ${s.last_date || "–"}）。外資連續性與金額是台股中短線最重要的籌碼指標。`),
        h("div", { class: "stats" },
          statBox("外資最新一日", signed(s.foreign_1d, 0) + " 億", s.foreign_streak ? `${s.foreign_streak > 0 ? "連買" : "連賣"} ${Math.abs(s.foreign_streak)} 日` : "", cls(s.foreign_1d)),
          statBox("外資近 5 日", signed(s.foreign_5d, 0) + " 億", "", cls(s.foreign_5d)),
          statBox("外資近 20 日", signed(s.foreign_20d, 0) + " 億", "", cls(s.foreign_20d)),
          statBox("投信近 5 日", signed(s.trust_5d, 0) + " 億", "", cls(s.trust_5d)),
          statBox("自營商近 5 日", signed(s.dealer_5d, 0) + " 億", "", cls(s.dealer_5d)),
          fut ? statBox("外資台指期淨未平倉", signed(fut.net_oi, 0) + " 口", fut.date, cls(fut.net_oi)) : null),
        h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "每日買賣超"), seg), holder),
        h("div", { class: "grid g2" },
          h("div", { class: "panel" }, h("h3", {}, "外資近 5 日買超前 15"), table(cols("張數"), F.foreign_buy, { onRow: open })),
          h("div", { class: "panel" }, h("h3", {}, "外資近 5 日賣超前 15"), table(cols("張數"), F.foreign_sell, { onRow: open })),
          h("div", { class: "panel" }, h("h3", {}, "投信近 5 日買超前 15"), table(cols("張數"), F.trust_buy, { onRow: open })),
          h("div", { class: "panel" }, h("h3", {}, "投信近 5 日賣超前 15"), table(cols("張數"), F.trust_sell, { onRow: open }))),
        (() => {
          const rows = U.filter((r) => r.market === "TW" && ok(r.qfii_chg20));
          if (rows.length < 10) return null;
          const qcols = [
            { key: "n", label: "個股", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market, r.symbol)) },
            { key: "p", label: "外資持股", val: (r) => r.qfii, cell: (r) => h("span", { class: "num" }, num(r.qfii, 1) + "%") },
            { key: "c", label: "20 日變化", val: (r) => r.qfii_chg20, cell: (r) => numEl(signed(r.qfii_chg20, 2) + " pp", r.qfii_chg20) },
            { key: "r", label: "尚可投資", tip: "距法令上限還能買的比例", val: (r) => r.qfii_room, cell: (r) => h("span", { class: "num" }, ok(r.qfii_room) ? num(r.qfii_room, 1) + "%" : "–") },
            { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) }];
          const sorted = [...rows].sort((a, b) => b.qfii_chg20 - a.qfii_chg20);
          return h("div", { class: "grid g2" },
            h("div", { class: "panel stack" }, h("h3", {}, "外資持股比例增加最多（20 日）", help("qfii", "外資持股比例")), table(qcols, sorted.slice(0, 15), { csv: false, onRow: (r) => openDrawer(r.symbol) })),
            h("div", { class: "panel stack" }, h("h3", {}, "外資持股比例減少最多（20 日）"), table(qcols, sorted.slice(-15).reverse(), { csv: false, onRow: (r) => openDrawer(r.symbol) }),
              h("p", { class: "chart-note" }, "買賣超是流量，持股比例是存量：比例已在高檔的股票，再賣的空間比再買大。上櫃股的 20 日變化要累積幾週存檔後才會出現。")));
        })(),
        h("div", { class: "panel stack" }, h("h3", {}, "投信首買：安靜一段時間後開始連買", help("trust", "投信首買")),
          (F.trust_first || []).length ? table([
            { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
            { key: "s", label: "連買天數", val: (r) => r.streak, cell: (r) => h("span", { class: "num" }, r.streak + " 日") },
            { key: "q", label: "先前無買超", val: (r) => r.quiet, cell: (r) => h("span", { class: "num" }, r.quiet + " 日") },
            { key: "v", label: "買超金額（億）", val: (r) => r.value, cell: (r) => numEl(signed(r.value, 1), r.value) },
            { key: "l", label: "張數", val: (r) => r.lots, cell: (r) => h("span", { class: "num" }, num(r.lots, 0)) },
            { key: "f", label: "同期外資（億）", val: (r) => r.foreign_value, cell: (r) => numEl(signed(r.foreign_value, 1), r.foreign_value) }],
          F.trust_first, { sort: "v", onRow: open })
            : h("div", { class: "empty" }, "目前沒有符合條件的個股"),
          h("p", { class: "chart-note" }, "條件：先前至少 10 個交易日投信沒有任何買超日，最近連續 2–6 日買超且合計 5,000 萬元以上。用來區分「剛被納入研究名單」與「已經被買很久」的股票；同期外資也買超的更值得留意。未計入分數。")),
        h("div", { class: "panel" }, h("h3", {}, "近 10 個交易日明細（億元）"), table([
          { key: "d", label: "日期", left: true, cell: (r) => h("span", { class: "num" }, r.date) },
          { key: "f", label: "外資", cell: (r) => numEl(signed(r.foreign, 1), r.foreign) },
          { key: "t", label: "投信", cell: (r) => numEl(signed(r.trust, 1), r.trust) },
          { key: "de", label: "自營商", cell: (r) => numEl(signed(r.dealer, 1), r.dealer) },
          { key: "to", label: "合計", cell: (r) => numEl(signed(r.total, 1), r.total) },
          { key: "x", label: "加權指數", cell: (r) => h("span", { class: "num" }, num(r.twii, 0)) }], recent)),
      ];
    },

    async margin() {
      const F = await load("flows"), s = F.summary || {};
      const open = (r) => bySymbol.has(r.code + ".TW") && openDrawer(r.code + ".TW");
      const usage = { key: "u", label: "融資使用率", tip: "融資餘額 ÷ 融資限額", val: (r) => r.usage, cell: (r) => h("span", { class: "num" }, ok(r.usage) ? num(r.usage * 100, 1) + "%" : "–") };
      const shortR = { key: "sr", label: "券資比", tip: "融券餘額 ÷ 融資餘額", val: (r) => r.short_ratio, cell: (r) => h("span", { class: "num" }, ok(r.short_ratio) ? num(r.short_ratio * 100, 1) + "%" : "–") };
      const cols = [
        { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
        { key: "m", label: "融資餘額（張）", val: (r) => r.margin, cell: (r) => h("span", { class: "num" }, num(r.margin, 0)) },
        { key: "c", label: "5 日變化", val: (r) => r.chg, cell: (r) => numEl(pct(r.chg), r.chg) },
        { key: "p", label: "股價 5 日", val: (r) => r.price_chg5, cell: (r) => numEl(pct(r.price_chg5), r.price_chg5) },
        usage, shortR,
        { key: "f", label: "訊號", left: true, cell: (r) => (r.chg > 0.05 && r.price_chg5 < -0.02 ? h("span", { class: "chip warn" }, "資增價跌")
          : r.chg < -0.03 && r.price_chg5 > 0.01 ? h("span", { class: "chip" }, "資減價漲") : "") }];
      const riskCols = [
        { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
        { key: "me", label: "估算維持率", tip: "收盤價 ÷（估算融資成本 × 0.6）", val: (r) => r.maint_est,
          cell: (r) => (ok(r.maint_est) ? h("b", { class: "num" + (r.maint_est < 1.4 ? " warn-t" : "") }, num(r.maint_est * 100, 0) + "%") : "–") },
        { key: "co", label: "估算融資成本", val: (r) => r.cost, cell: (r) => h("span", { class: "num" }, px(r.cost)) },
        { key: "cl", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "r", label: "近 20 日股價", val: (r) => r.r20, cell: (r) => numEl(pct(r.r20), r.r20) },
        usage,
        { key: "v", label: "融資市值（億）", val: (r) => r.value, cell: (r) => h("span", { class: "num" }, num(r.value, 1)) },
        { key: "c", label: "融資 5 日變化", val: (r) => r.chg, cell: (r) => numEl(pct(r.chg), r.chg) },
        shortR,
        { key: "f", label: "狀態", left: true, cell: (r) => (ok(r.chg) && r.chg > 0.02 ? h("span", { class: "chip warn" }, "仍在加碼融資")
          : ok(r.chg) && r.chg < -0.1 ? h("span", { class: "chip" }, "融資大減，可能已斷頭") : "") }];
      const state = { key: "maint" };
      const SER = {
        maint: ["融資維持率", () => flowChart(F.history, "maint", "大盤融資維持率（估算）", "%",
          { line: true, scale: 100, digits: 1, marks: [[130, "追繳線 130%"], [166.7, "新倉起始 167%"]] })],
        margin_bal: ["融資餘額", () => flowChart(F.history, "margin_bal", "融資餘額", "億元")],
        short_units: ["融券餘額", () => flowChart(F.history, "short_units", "融券餘額", "張")] };
      const hasMaint = F.history.some((r) => ok(r.maint));
      if (!hasMaint) state.key = "margin_bal";
      const holder = h("div");
      const drawChart = () => holder.replaceChildren(SER[state.key][1]());
      const seg = h("div", { class: "seg", role: "group", "aria-label": "指標" }, Object.entries(SER).filter(([k]) => k !== "maint" || hasMaint).map(([k, [label]]) =>
        h("button", { type: "button", "aria-pressed": String(k === state.key), onclick: (e) => {
          state.key = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); drawChart();
        } }, label)));
      drawChart();
      const tone = { 充裕: "mk", 正常: "", 偏低: "warn", 斷頭壓力大: "warn" }[s.maint_status] || "";
      return [
        head("融資融券", "融資是散戶槓桿的溫度計。融資維持率看的是這些槓桿離被迫賣出還有多遠：低於 130% 會被追繳，補不出錢就斷頭，賣壓會再把股價往下推。"),
        h("div", { class: "stats" },
          ok(s.maint) ? h("div", { class: "stat" }, h("div", { class: "k" }, "大盤融資維持率（估算）", help("maint", "融資維持率")),
            h("div", { class: "v num" }, num(s.maint * 100, 1) + "%"),
            h("div", { class: "s" }, h("span", { class: "chip " + tone }, s.maint_status), ok(s.maint_chg5) ? ` 一週 ${signed(s.maint_chg5 * 100, 1)} 個百分點` : "")) : null,
          ok(s.maint_cushion) ? statBox("距追繳線的緩衝", num(s.maint_cushion * 100, 1) + "%", "擔保品再跌這麼多，整體就到 130%") : null,
          ok(s.maint_low20) ? statBox("近 20 日最低維持率", num(s.maint_low20 * 100, 1) + "%", "") : null,
          statBox("融資餘額", num(s.margin_bal, 0) + " 億", ok(s.margin_days) ? `約 ${num(s.margin_days, 1)} 日成交值` : "集中市場"),
          statBox("融資一週／一月", `${pct(s.margin_chg5)}／${pct(s.margin_chg20)}`, "", cls(s.margin_chg5)),
          statBox("融券餘額", num(s.short_units, 0) + " 張", ok(s.short_ratio) ? `券資比 ${num(s.short_ratio * 100, 1)}%` : "")),
        h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "走勢"), seg), holder,
          hasMaint ? h("p", { class: "chart-note" }, "融資維持率＝融資擔保品市值 ÷ 融資金額。證交所只公布各股融資張數與全市場融資金額，本站以每日收盤價估算擔保品市值，與券商實際逐戶計算的數字會有出入，適合看水位與方向。") : null),
        leadCards(["retail", "pcr", "sbl"]) ? h("div", { class: "panel stack" }, h("h3", {}, "期貨、選擇權與借券部位"), leadCards(["retail", "pcr", "sbl"]),
          h("p", { class: "chart-note" }, "融資融券是現股的散戶槓桿；期貨與借券反應更快。偏多／偏空為市場常見解讀，未計入分數。")) : null,
        F.sbl ? h("div", { class: "grid g2" }, [["up", "借券賣出餘額增幅前 15（5 日）"], ["down", "借券賣出餘額減幅前 15（5 日）"]].map(([k, title]) =>
          h("div", { class: "panel" }, h("h3", {}, title), table([
            { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
            { key: "c", label: "借券餘額變化", val: (r) => r.chg, cell: (r) => numEl(pct(r.chg), r.chg) },
            { key: "v", label: "餘額市值（億）", val: (r) => r.value, cell: (r) => h("span", { class: "num" }, num(r.value, 1)) },
            { key: "s", label: "融券變化", tip: "同期散戶融券餘額的變化", val: (r) => r.short_chg, cell: (r) => numEl(pct(r.short_chg), r.short_chg) },
            { key: "x", label: "借券÷融券", val: (r) => r.sbl_vs_short, cell: (r) => h("span", { class: "num" }, ok(r.sbl_vs_short) ? num(r.sbl_vs_short, 0) + " 倍" : "–") }],
          F.sbl[k], { onRow: open }),
          k === "up" ? h("p", { class: "chart-note" }, (F.sbl.stale ? "本次更新未取得借券資料，以下沿用上次的結果。" : "") + `${F.sbl.ref_date} 至 ${F.sbl.date}，僅列借券餘額市值 5 億元以上個股。借券大增而融券持平，代表放空來自法人而非散戶。`) : null))) : null,
        h("div", { class: "panel stack" }, h("h3", {}, "斷頭壓力觀察：個股融資維持率（估算）", help("maint_stock", "個股融資維持率")),
          (F.margin_risk || []).length ? table(riskCols, F.margin_risk, { onRow: open })
            : h("div", { class: "empty" }, "目前沒有估算維持率低於 145%、或「融資使用率 10% 以上且近 20 日跌逾 15%」的個股"),
          h("p", { class: "chart-note" }, "列出融資市值 3 億元以上、估算維持率低於 145%，或融資使用率 10% 以上且近 20 日跌逾 15% 的個股。交易所不公布融資戶的買進成本，這裡假設融資餘額增加當天以收盤價買進、逐日推估平均成本，並校正到加總等於大盤維持率，所以是粗略的估算。低於 130% 的部位視為已被追繳，因此最低顯示 130%；130–145% 代表再跌幾個百分點就會有新一批追繳。")),
        h("div", { class: "grid g2" },
          h("div", { class: "panel" }, h("h3", {}, "融資增幅前 15（5 日）"), table(cols, F.margin_up, { onRow: open }),
            h("p", { class: "chart-note" }, `比較基準日 ${F.margin_ref_date || "–"}，僅列融資餘額 2,000 張以上個股。`)),
          h("div", { class: "panel" }, h("h3", {}, "融資減幅前 15（5 日）"), table(cols, F.margin_down, { onRow: open }))),
        h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "怎麼看這些指標"), h("a", { class: "small", href: "#learn?k=maint" }, "完整解說")), h("div", { class: "src" }, [
          ["融資維持率", "167% 左右是新融資部位的起點（上市股票融資六成）。150% 以上屬正常；跌到 140% 附近代表多數融資戶已虧損；逼近 130% 時追繳與斷頭會形成連鎖賣壓，但也常是急跌的尾聲。"],
          ["融資使用率", "融資餘額占該股融資限額的比例。越高代表散戶槓桿越集中，下跌時賣壓越重。"],
          ["券資比", "融券餘額 ÷ 融資餘額。偏高代表放空的人多，股價轉強時容易出現回補買盤（軋空）。"],
          ["融資相當於幾日成交值", "融資餘額 ÷ 當日成交值。數字越大，融資一旦集中出場，市場越難消化。"]]
          .map(([k, v]) => [h("b", {}, k), h("span", { class: "small" }, v)]))),
      ];
    },

    async smart() {
      const M = await load("smart_money");
      const C = M.congress || { trades: [], by_ticker: [] };
      const tradeTbl = table([
        { key: "d", label: "交易日", left: true, cell: (r) => h("span", { class: "num" }, r.traded) },
        { key: "n", label: "議員", left: true, cell: (r) => `${r.name}（${r.party}・${r.chamber === "Senate" ? "參" : "眾"}）` },
        { key: "t", label: "代號", left: true, cell: (r) => h("b", { class: "num" }, r.ticker) },
        { key: "k", label: "買賣", left: true, cell: (r) => h("span", { class: "chip " + (r.type === "buy" ? "add" : r.type === "sell" ? "cut" : "") }, r.type === "buy" ? "買進" : r.type === "sell" ? "賣出" : "其他") },
        { key: "a", label: "金額區間", left: true, cell: (r) => h("span", { class: "num" }, r.amount) },
        { key: "f", label: "申報日", cell: (r) => h("span", { class: "num muted" }, r.filed) }], C.trades.slice(0, 20), { csv: "congress-trades" });
      // Twenty rows keep this panel as tall as its neighbour; the rest is one click away.
      const cap = { n: 20 };
      const filtered = () => { const v = q.value.trim().toLowerCase();
        return C.trades.filter((r) => !v || r.name.toLowerCase().includes(v) || r.ticker.toLowerCase().includes(v)); };
      const more = h("button", { class: "linkish", type: "button", onclick: () => { cap.n = cap.n === 20 ? 200 : 20; redraw(); } });
      const redraw = () => { const all = filtered(); tradeTbl.setRows(all.slice(0, cap.n));
        more.textContent = cap.n === 20 ? `顯示全部 ${Math.min(all.length, 200)} 筆` : "只顯示 20 筆"; more.hidden = all.length <= 20; };
      const q = h("input", { type: "search", id: "congress-q", placeholder: "篩選議員或代號，例如 Pelosi、NVDA", "aria-label": "篩選議員交易",
        oninput: () => redraw() });
      redraw();
      const ACT = { new: "新建倉", add: "加碼", cut: "減碼", hold: "持平", exit: "出清" };
      return [
        head("名人・議員・董監持股", "美國國會議員依法申報的交易、知名投資人 13F 季報持股，以及台股董監事持股與質押。申報都有時間差，請當作方向參考而非即時訊號。"),
        h("div", { class: "grid g2 top" },
          h("div", { class: "panel" }, h("h3", {}, "議員近 90 日交易最多的股票"), table([
            { key: "t", label: "代號", left: true, cell: (r) => h("b", { class: "num" }, r.ticker) },
            { key: "b", label: "買進筆數", val: (r) => r.buys, cell: (r) => numEl(r.buys, r.buys) },
            { key: "s", label: "賣出筆數", val: (r) => r.sells, cell: (r) => numEl(r.sells, -r.sells) },
            { key: "n", label: "淨買", val: (r) => r.net, cell: (r) => numEl(signed(r.net), r.net) },
            { key: "m", label: "議員", left: true, wrap: true, cell: (r) => r.members.slice(0, 4).join("、") + (r.members.length > 4 ? ` 等 ${r.members.length} 人` : "") }],
          C.by_ticker.slice(0, 20), { onRow: (r) => bySymbol.has(r.ticker) && openDrawer(r.ticker) })),
          h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "最新議員交易申報"), q), tradeTbl, more,
            C.live === false ? h("p", { class: "chart-note" }, "本週來源無法連線，顯示上次成功取得的資料。") : null)),
        h("h3", {}, "名人基金 13F 持股（美國 SEC 季報）"),
        h("div", { class: "grid g2 top" }, (M.gurus || []).map((g) => h("div", { class: "panel" },
          h("div", { class: "guru-head" }, h("div", {}, h("b", {}, g.person), h("div", { class: "small muted" }, g.name)),
            h("div", { class: "small muted num" }, `${g.period} 季報・${num(g.total_value / 1e8, 0)} 億美元・${g.positions} 檔`)),
          table([
            { key: "n", label: "持股", left: true, cell: (r) => h("span", { class: "nm-cell" }, h("b", {}, r.ticker || r.name), r.ticker ? h("span", { class: "code" }, r.name.slice(0, 18)) : null) },
            { key: "w", label: "權重", cell: (r) => h("span", { class: "num" }, num(r.weight * 100, 1) + "%") },
            { key: "a", label: "上季動作", left: true, cell: (r) => h("span", { class: "chip " + r.action }, ACT[r.action] + (ok(r.chg) && r.action !== "hold" ? " " + pct(r.chg, 0) : "")) }],
          g.top.slice(0, 8), { onRow: (r) => bySymbol.has(r.ticker) && openDrawer(r.ticker) }),
          g.exits && g.exits.length ? h("p", { class: "small", style: "margin-top:8px" }, "上季出清：", g.exits.map((e) => h("span", { class: "chip exit", style: "margin-right:4px" }, e.ticker || e.name))) : null))),
        (M.us_insiders && M.us_insiders.trades.length) ? h("div", { class: "grid g2 top" },
          h("div", { class: "panel stack" }, h("h3", {}, "美股內部人 90 日買賣（SEC Form 4）", help("form4", "內部人交易")), table([
            { key: "t", label: "代號", left: true, cell: (r) => h("span", {}, h("b", { class: "num" }, r.ticker), " ", h("span", { class: "small muted" }, r.name)) },
            { key: "b", label: "買進", val: (r) => r.buy_value, cell: (r) => numEl(r.buys ? r.buys + " 筆 " + musd(r.buy_value) : "–", r.buys ? 1 : 0) },
            { key: "s", label: "賣出", val: (r) => r.sell_value, cell: (r) => numEl(r.sells ? r.sells + " 筆 " + musd(r.sell_value) : "–", r.sells ? -1 : 0) },
            { key: "u", label: "非預定賣出", tip: "不在 10b5-1 預定計畫內的賣出金額", val: (r) => r.sell_unplanned, cell: (r) => h("span", { class: "num" }, r.sell_unplanned ? musd(r.sell_unplanned) : "–") },
            { key: "l", label: "最近一筆", cell: (r) => h("span", { class: "num small" }, r.last || "–") }], M.us_insiders.by_ticker, { sort: "b", onRow: (r) => bySymbol.has(r.ticker) && openDrawer(r.ticker) })),
          h("div", { class: "panel stack" }, h("h3", {}, "最新內部人交易"), table([
            { key: "d", label: "交易日", left: true, cell: (r) => h("span", { class: "num" }, r.date) },
            { key: "t", label: "代號", left: true, cell: (r) => h("b", { class: "num" }, r.ticker) },
            { key: "o", label: "內部人", left: true, wrap: true, cell: (r) => r.owner + "（" + r.role + "）" },
            { key: "k", label: "買賣", left: true, cell: (r) => h("span", { class: "chip " + (r.type === "buy" ? "add" : "cut") }, (r.type === "buy" ? "買進" : "賣出") + (r.planned ? "・預定" : "")) },
            { key: "v", label: "金額", cell: (r) => h("span", { class: "num" }, musd(r.value)) }],
            [...M.us_insiders.trades].sort((a, b) => (b.type === "buy") - (a.type === "buy") || (b.date > a.date ? 1 : -1)).slice(0, 30), { csv: "us-insiders" }),
            h("p", { class: "chart-note" }, "只列公開市場的買進與賣出，不含股票獎酬、贈與、行使選擇權與扣稅。「預定」是 10b5-1 預先排定的交易，參考性較低。內部人賣股的理由很多，買進通常只有一個：認為股價被低估。"))) : null,
        h("div", { class: "panel" }, h("h3", {}, "台股董監持股與質押（觀察名單）"), table([
          { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
          { key: "p", label: "質押比", val: (r) => r.pledge, cell: (r) => h("span", { class: "num" }, num(r.pledge * 100, 1) + "%") },
          { key: "c", label: "持股較選任時", val: (r) => r.chg, cell: (r) => numEl(pct(r.chg), r.chg) },
          { key: "h", label: "董監持股（張）", val: (r) => r.held, cell: (r) => h("span", { class: "num" }, num(r.held / 1000, 0)) },
          { key: "t", label: "主要持股人", left: true, wrap: true, cell: (r) => r.top.map((t) => t.name).join("、") }],
        M.tw_insiders || [], { sort: "p", onRow: (r) => openDrawer(r.code + ".TW") }),
          h("p", { class: "chart-note" }, "質押比偏高代表大股東以持股借款，股價下跌時有追繳與賣壓風險。")),
      ];
    },

    async fund() {
      const state = { m: "ALL", q: "" };
      const cols = [
        { key: "n", label: "個股", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market, r.symbol)) },
        { key: "i", label: "產業", left: true, cell: (r) => r.industry },
        { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        { key: "pe", label: "本益比", val: (r) => r.pe, cell: (r) => h("span", { class: "num" }, num(r.pe, 1)) },
        { key: "pb", label: "淨值比", val: (r) => r.pb, cell: (r) => h("span", { class: "num" }, num(r.pb, 2)) },
        { key: "y", label: "殖利率", tip: "超過 15%，或隱含配發率超過 150% 的標「異常」，不計分", val: (r) => (r.yield_odd ? null : r.yield), cell: (r) => h("span", { class: "num" }, ok(r.yield) ? num(r.yield, 1) + "%" + (r.yield_odd ? "（異常）" : "") : "–") },
        { key: "rv", label: "營收年增", tip: "台股為最新月營收，美股為最新一季", val: (r) => r.rev_yoy, cell: (r) => numEl(pctRaw(r.rev_yoy), r.rev_yoy) },
        { key: "rm", label: "營收動能", tip: "近 3 個月平均營收 ÷ 近 12 個月平均營收 − 1（僅台股）", val: (r) => (ok(r.rev_ratio) ? r.rev_ratio - 1 : null),
          cell: (r) => (ok(r.rev_ratio) ? h("span", {}, numEl(pct(r.rev_ratio - 1, 0), r.rev_ratio - 1),
            r.rev_cross === "up" ? h("span", { class: "chip add", style: "margin-left:4px" }, "剛轉強") : r.rev_cross === "down" ? h("span", { class: "chip cut", style: "margin-left:4px" }, "剛轉弱") : null) : "–") },
        { key: "cl", label: "合約負債季增", tip: "客戶預付款的季增率（僅台股；占資產比重低的公司意義不大）", val: (r) => (/金融|保險/.test(r.industry) ? null : r.cl_qoq),
          cell: (r) => (ok(r.cl_qoq) && !/金融|保險/.test(r.industry) ? h("span", {}, numEl(pct(r.cl_qoq, 0), r.cl_qoq), ok(r.cl_share) ? h("span", { class: "small muted num" }, ` 占資產 ${num(r.cl_share, 0)}%`) : null) : "–") },
        { key: "e", label: "近四季 EPS", tip: "台股以收盤價 ÷ 本益比推算，美股為近四季合計；虧損股沒有本益比，顯示 –", val: (r) => r.eps_ttm, cell: (r) => h("span", { class: "num" }, num(r.eps_ttm, 2)) },
        { key: "eg", label: "EPS 年增", val: (r) => r.eps_yoy, cell: (r) => numEl(pctRaw(r.eps_yoy, 0), r.eps_yoy) },
        { key: "r", label: "RSI", val: (r) => r.rsi, cell: (r) => h("span", { class: "num" }, num(r.rsi, 0)) },
        { key: "im", label: "純超額強度", tip: "扣掉大盤漲跌後，個股自己的動能（約為標準差倍數）", val: (r) => r.imom, cell: (r) => numEl(ok(r.imom) ? signed(r.imom, 1) : "–", r.imom) },
        { key: "tc", label: "籌碼分", tip: "外資、投信、融資（只有台股）", val: (r) => r.tactical, cell: (r) => numEl(ok(r.tactical) ? signed(r.tactical, 2) : "–", r.tactical) },
        { key: "st", label: "基本面分", tip: "財報＋申報持股", val: (r) => r.structural, cell: (r) => numEl(ok(r.structural) ? signed(r.structural, 2) : "–", r.structural) },
        { key: "o", label: "綜合分", val: (r) => r.overall, cell: (r) => h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter(r.overall), numEl(signed(r.overall, 2), r.overall)) }];
      const tbl = table(cols, U, { sort: "o", csv: "fundamentals", onRow: (r) => openDrawer(r.symbol) });
      const QUAD = { ALL: ["全部", () => true], pp: ["基本面佳・籌碼強", (r) => r.structural >= 0 && r.tactical >= 0], pn: ["基本面佳・籌碼弱", (r) => r.structural >= 0 && r.tactical < 0],
        np: ["基本面弱・籌碼強", (r) => r.structural < 0 && r.tactical >= 0], nn: ["基本面弱・籌碼弱", (r) => r.structural < 0 && r.tactical < 0] };
      state.quad = "ALL";
      const shown = () => U.filter((r) => (state.m === "ALL" || r.market === state.m) &&
        (!state.q || (r.name + r.code + r.industry).toLowerCase().includes(state.q)) &&
        (state.quad === "ALL" || (ok(r.tactical) && ok(r.structural) && QUAD[state.quad][1](r))));
      // Scatter: x = short-term read, y = structural read. Click a dot for the stock, a corner label to filter the table.
      const NS = "http://www.w3.org/2000/svg", W = 640, H = 340, P = 34;
      const sx = (v) => P + ((v + 1) / 2) * (W - 2 * P), sy = (v) => H - P - ((v + 1) / 2) * (H - 2 * P);
      const svg = document.createElementNS(NS, "svg");
      svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "scatter"); svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", "籌碼分與基本面分的象限散佈圖");
      const el = (tag, attrs, text) => { const n = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v)); if (text != null) n.textContent = text; return n; };
      const quadBtns = h("div", { class: "seg", role: "group", "aria-label": "象限" }, Object.entries(QUAD).map(([k, [t]]) =>
        h("button", { type: "button", "data-q": k, "aria-pressed": String(k === "ALL"), onclick: () => { state.quad = k; apply(); } }, t)));
      const drawScatter = () => {
        const rows = shown().filter((r) => ok(r.tactical) && ok(r.structural));
        svg.replaceChildren(
          el("rect", { x: P, y: P, width: W - 2 * P, height: H - 2 * P, class: "sc-frame" }),
          el("line", { x1: sx(0), y1: P, x2: sx(0), y2: H - P, class: "sc-axis" }), el("line", { x1: P, y1: sy(0), x2: W - P, y2: sy(0), class: "sc-axis" }),
          el("text", { x: W - P - 6, y: P + 14, "text-anchor": "end", class: "sc-q" }, "基本面佳・籌碼強"), el("text", { x: P + 6, y: P + 14, class: "sc-q" }, "基本面佳・籌碼弱（等轉強）"),
          el("text", { x: W - P - 6, y: H - P - 6, "text-anchor": "end", class: "sc-q" }, "基本面弱・籌碼強（僅宜短打）"), el("text", { x: P + 6, y: H - P - 6, class: "sc-q" }, "基本面弱・籌碼弱"),
          el("text", { x: W / 2, y: H - 8, "text-anchor": "middle", class: "sc-l" }, "籌碼分 →"), el("text", { x: 12, y: H / 2, "text-anchor": "middle", class: "sc-l", transform: `rotate(-90 12 ${H / 2})` }, "基本面分 →"),
          ...rows.map((r) => { const c = el("circle", { cx: sx(r.tactical), cy: sy(r.structural), r: S.picks.some((x) => x.symbol === r.symbol) ? 5.5 : 4,
            class: "sc-dot " + (r.market === "TW" ? "tw" : "us") + (S.picks.some((x) => x.symbol === r.symbol) ? " pick" : ""), "data-sym": r.symbol, tabindex: 0 });
            c.append(el("title", {}, `${r.name} ${r.code}｜籌碼 ${signed(r.tactical, 2)}・基本面 ${signed(r.structural, 2)}・綜合 ${signed(r.overall, 2)}`));
            c.addEventListener("click", () => openDrawer(r.symbol));
            c.addEventListener("keydown", (e) => { if (e.key === "Enter") openDrawer(r.symbol); });
            return c; }));
      };
      const apply = () => { tbl.setRows(shown()); drawScatter();
        quadBtns.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.q === state.quad))); };
      // table row <-> dot: hovering a row rings its dot
      tbl.addEventListener("pointerover", (e) => { const tr = e.target.closest("tr[data-sym]");
        svg.querySelectorAll(".sc-dot.hl").forEach((n) => n.classList.remove("hl"));
        if (tr) { const dot = svg.querySelector(`.sc-dot[data-sym="${CSS.escape(tr.dataset.sym)}"]`); if (dot) { dot.classList.add("hl"); svg.append(dot); } } });
      drawScatter();
      const seg = h("div", { class: "seg", role: "group", "aria-label": "市場" }, [["ALL", "全部"], ["TW", "台股"], ["US", "美股"]].map(([k, t]) =>
        h("button", { type: "button", "aria-pressed": String(k === "ALL"), onclick: (e) => {
          state.m = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); apply();
        } }, t)));
      const q = h("input", { type: "search", id: "fund-q", placeholder: "搜尋名稱、代號或產業", "aria-label": "搜尋個股",
        oninput: () => { state.q = q.value.trim().toLowerCase(); apply(); } });
      return [
        head("財報與各項指標", "觀察名單全部個股的評價、成長與技術指標。點欄位標題排序，點列看圖表。台股財報來自證交所公開資訊，美股來自 SEC XBRL 申報。"),
        h("p", { class: "small muted" }, "每個欄位的意義與本站的計分方式：", h("a", { href: "#learn?k=pe" }, "基本面與財報解說"), "。合約負債（客戶預付款）季增常領先營收一到兩季，", h("a", { href: "#learn?k=cl" }, "說明在此"), "。"),
        h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "因子象限圖：籌碼 × 基本面", help("tactical", "籌碼分與基本面分")), quadBtns),
          svg, h("p", { class: "chart-note" }, "每個點是一檔股票（藍＝台股、灰＝美股，較大的點是本週精選）。點一下看個股，按象限按鈕會同時篩選下方表格；滑過表格的列，對應的點會被圈出來。")),
        h("div", { class: "panel stack" }, h("div", { class: "row" }, seg, q), tbl),
        (() => {
          const tw = U.filter((r) => r.market === "TW" && r.kind !== "etf");
          const med = (a) => { const v = a.filter(ok).sort((x, y) => x - y); return v.length ? v[Math.floor(v.length / 2)] : null; };
          const groups = Object.entries(tw.reduce((m, r) => { (m[r.industry] = m[r.industry] || []).push(r); return m; }, {}))
            .filter(([, rs]) => rs.length >= 3)
            .map(([name, rs]) => ({ name, n: rs.length, rev: med(rs.map((r) => r.rev_yoy)), r20: med(rs.map((r) => r.r20)), r60: med(rs.map((r) => r.r60)),
              rank: med(rs.map((r) => (r.rank ? r.rank[0] / r.rank[1] : null))), picks: rs.filter((r) => S.picks.some((p) => p.symbol === r.symbol)).length }));
          if (groups.length < 3) return null;
          return h("div", { class: "panel stack" }, h("h3", {}, "台股產業概況（觀察名單，3 檔以上的產業）"), table([
            { key: "n", label: "產業", left: true, cell: (g) => g.name },
            { key: "c", label: "檔數", val: (g) => g.n, cell: (g) => h("span", { class: "num" }, g.n) },
            { key: "v", label: "月營收年增中位數", val: (g) => g.rev, cell: (g) => numEl(pctRaw(g.rev), g.rev) },
            { key: "m", label: "近一月中位數", val: (g) => g.r20, cell: (g) => numEl(pct(g.r20), g.r20) },
            { key: "q", label: "近一季中位數", val: (g) => g.r60, cell: (g) => numEl(pct(g.r60), g.r60) },
            { key: "r", label: "排名中位數", tip: "同市場排名的百分位，越小越前面", val: (g) => (ok(g.rank) ? -g.rank : null), cell: (g) => h("span", { class: "num" }, ok(g.rank) ? "前 " + num(g.rank * 100, 0) + "%" : "–") },
            { key: "p", label: "精選檔數", val: (g) => g.picks, cell: (g) => h("span", { class: "num" }, g.picks) }], groups, { sort: "r", csv: "industries" }),
            h("p", { class: "chart-note" }, "用來看資金與營收集中在哪些產業。中位數比平均數不受單一個股影響；檔數少的產業波動大，僅供參考。"));
        })(),
      ];
    },

    async watch() {
      const issues = (r) => [r.overall <= -0.15 ? "綜合評分偏空" : null, ok(r.tactical) && r.tactical <= -0.3 ? "籌碼明顯轉弱" : null,
        ok(r.rsi) && r.rsi >= 75 ? "RSI 過熱" : null, ok(r.maint_est) && r.maint_est < 1.4 ? "融資維持率偏低" : null,
        ...(r.tags || []).filter(([, tone]) => tone < 0).map(([t]) => t)].filter(Boolean);
      // A fund's warning signs are about whether it still earns its place, not about next week.
      const fundIssues = (r) => [["偏弱", "落後"].includes(r.grade) ? `績效評等${r.grade}` : null,
        ok(r.beat && r.beat["3y"]) && r.beat["3y"] < 40 ? `3 年同類排名只贏 ${num(r.beat["3y"], 0)}%` : null,
        ok(r.excess_ann) && r.excess_ann <= -0.03 ? `年化落後指數 ${num(-r.excess_ann * 100, 1)} 個百分點` : null,
        ok(r.mdd_3y) && r.mdd_3y <= -0.35 ? `3 年最大回檔 ${pct(r.mdd_3y, 0)}` : null,
        r.horizons && r.horizons["1m"].score <= -0.45 ? "淨值趨勢強勢偏空" : null].filter(Boolean);
      const holder = h("div", { class: "stack", style: "gap:20px" });
      // Add anything the site covers - stocks, ETFs and funds - by name or code.
      const pool = [...bySymbol.values()].filter((r) => r && r.symbol && r.name);
      const sugg = h("div", { class: "sugg" });
      const q = h("input", { type: "search", id: "watch-q", placeholder: "輸入名稱或代號加入自選，例如 台積電、0050、安聯台灣科技", "aria-label": "搜尋要加入自選的股票、ETF 或基金",
        oninput: () => {
          const v = q.value.trim().toLowerCase();
          const hits = v ? pool.filter((r) => (r.name + " " + (r.code || "") + " " + r.symbol).toLowerCase().includes(v)).slice(0, 8) : [];
          sugg.replaceChildren(...hits.map((r) => h("button", { type: "button", class: "sugg-item", onclick: () => {
            if (!WATCH.has(r.symbol)) toggleWatch(r.symbol);
            q.value = ""; sugg.replaceChildren(); draw();
          } }, WATCH.has(r.symbol) ? "★ " : "☆ ", h("b", {}, r.name), " ", h("span", { class: "muted small num" }, r.code || r.symbol), " ",
            h("span", { class: "chip" }, r.kind === "fund" ? "基金" : r.kind === "etf" ? "ETF" : r.market === "US" ? "美股" : "台股"))));
        } });
      const draw = () => {
        const rows = [...WATCH].map((s) => bySymbol.get(s) || { symbol: s, gone: true });
        const live = rows.filter((r) => !r.gone && r.kind !== "fund"), gone = rows.filter((r) => r.gone), funds = rows.filter((r) => r.kind === "fund");
        const rm = (r) => h("button", { class: "linkish", type: "button", onclick: (e) => { e.stopPropagation(); toggleWatch(r.symbol); draw(); } }, "移除");
        holder.replaceChildren(...[
          !rows.length ? h("div", { class: "panel empty stack", style: "align-items:center" }, h("div", {}, "還沒有自選標的"),
            h("div", { class: "small" }, "用上方搜尋框加入，或在任何個股、ETF、基金的詳細頁與表格上按 ☆。名單只存在這台裝置的瀏覽器，不會上傳。"),
            h("div", { class: "row" }, h("a", { href: "#picks" }, "從精選 20 檔挑"), h("a", { href: "#funds" }, "從基金評比挑"))) : null,
          rows.length ? h("div", { class: "stats" },
            statBox("自選股票／ETF", String(live.length), live.length ? `偏多 ${live.filter((r) => r.overall >= 0.15).length}・偏空 ${live.filter((r) => r.overall <= -0.15).length}` : ""),
            statBox("自選基金", String(funds.length), funds.length ? `優異或良好 ${funds.filter((r) => ["優異", "良好"].includes(r.grade)).length} 檔` : ""),
            statBox("有警訊", String(live.filter((r) => issues(r).length).length + funds.filter((r) => fundIssues(r).length).length), "見「需要留意」欄"),
            MK && MK.risk ? statBox("目前市場風險", `${num(MK.risk.score, 0)}・${MK.risk.name}`, `參考持股 ${MK.risk.exposure}`) : null) : null,
          live.length ? h("div", { class: "panel stack" }, h("h3", {}, "股票與 ETF 健檢"), table([
            { key: "n", label: "標的", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market, r.symbol)) },
            { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
            { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
            { key: "o", label: "綜合分", val: (r) => r.overall, cell: (r) => h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter(r.overall), numEl(signed(r.overall, 2), r.overall)) },
            { key: "2", label: "同市場排名", val: (r) => r.rank && -r.rank[0], cell: (r) => h("span", { class: "num" }, r.rank ? `${r.rank[0]}／${r.rank[1]}` : "–") },
            { key: "t", label: "籌碼分", val: (r) => r.tactical, cell: (r) => numEl(ok(r.tactical) ? signed(r.tactical, 2) : "–", r.tactical) },
            { key: "s", label: "基本面分", val: (r) => r.structural, cell: (r) => numEl(ok(r.structural) ? signed(r.structural, 2) : "–", r.structural) },
            { key: "sp", label: "停損價（距離）", val: (r) => r.plan && r.plan.stop_pct, cell: (r) => h("span", { class: "num" }, r.plan ? `${px(r.plan.stop)}（−${num((r.plan.stop_pct || 0) * 100, 1)}%）` : "–") },
            { key: "ps", label: "部位上限", val: (r) => r.plan && r.plan.size, cell: (r) => h("span", { class: "num" }, r.plan && ok(r.plan.size) ? num(r.plan.size * 100, 0) + "%" : "–") },
            { key: "i", label: "需要留意", left: true, wrap: true, cell: (r) => { const x = issues(r); return x.length ? x.map((t) => h("span", { class: "chip cut", style: "margin:0 4px 2px 0" }, t)) : h("span", { class: "muted" }, "沒有警訊"); } },
            { key: "x", label: "", cell: rm }], live, { sort: "o", csv: "watchlist", onRow: (r) => openDrawer(r.symbol) }),
            h("p", { class: "chart-note" }, "「需要留意」彙整各分頁的負面訊號：評分偏空、籌碼轉弱、過熱、融資壓力，以及處置、注意股、外資賣超、借券大增等標籤。沒有警訊不代表安全，只代表規則沒有觸發。")) : null,
          funds.length ? h("div", { class: "panel stack" }, h("h3", {}, "基金健檢"), table([
            { key: "n", label: "基金", left: true, wrap: true, cell: (r) => h("span", {}, h("b", { style: "font-weight:500" }, r.name), " ", h("span", { class: "chip" }, r.currency)) },
            { key: "e", label: "績效評等", left: true, val: (r) => r.score, cell: (r) => gradePill(r) },
            { key: "s", label: "評分", val: (r) => r.score, cell: (r) => scoreCell(r) },
            { key: "1m", label: "1 月", val: (r) => r.ret["1m"], cell: (r) => numEl(pct(r.ret["1m"]), r.ret["1m"]) },
            { key: "1y", label: "1 年", val: (r) => r.ret["1y"], cell: (r) => numEl(pct(r.ret["1y"]), r.ret["1y"]) },
            { key: "3y", label: "3 年累積", val: (r) => r.ret["3y"], cell: (r) => numEl(pct(r.ret["3y"], 0), r.ret["3y"]) },
            { key: "x", label: "年化勝過指數", val: (r) => r.excess_ann, cell: (r) => numEl(ok(r.excess_ann) ? signed(r.excess_ann * 100, 1) + " pp" : "–", r.excess_ann) },
            { key: "b3", label: "同類 3 年", val: (r) => r.beat["3y"], cell: (r) => h("span", { class: "num" }, beatText(r.beat["3y"])) },
            { key: "d", label: "3 年最大回檔", val: (r) => r.mdd_3y, cell: (r) => numEl(pct(r.mdd_3y, 0), r.mdd_3y) },
            { key: "t", label: "淨值趨勢", left: true, cell: (r) => (r.horizons ? pill(r.horizons["1m"]) : "–") },
            { key: "i", label: "需要留意", left: true, wrap: true, cell: (r) => { const x = fundIssues(r); return x.length ? x.map((t) => h("span", { class: "chip cut", style: "margin:0 4px 2px 0" }, t)) : h("span", { class: "muted" }, "沒有警訊"); } },
            { key: "xx", label: "", cell: rm }], funds, { sort: "s", csv: "watchlist-funds", onRow: (r) => openDrawer(r.symbol) }),
            h("p", { class: "chart-note" }, "基金看的是「還值不值得持有」：評等偏弱或落後、3 年同類排名在後段、長期落後對比指數、回檔過深都會列出。淨值趨勢只看最近的方向，與績效評等是兩回事。")) : null,
          gone.length ? h("div", { class: "panel stack" }, h("h3", {}, "已不在本週資料中"),
            h("p", { class: "small" }, "這些標的本週沒有資料（台股觀察名單取成交金額前段，會隨成交量進出；基金若來源暫時取不到也會出現在這裡）。"),
            h("div", { class: "row" }, gone.map((r) => h("span", { class: "chip" }, r.symbol, " ", rm(r))))) : null].filter(Boolean));
      };
      draw();
      return [head("自選股與基金", "把想追蹤的股票、ETF 與基金集中在一頁，每週更新後一次看完評分、停損距離、基金績效與各分頁的警訊。"),
        h("div", { class: "panel stack" }, q, sugg), holder];
    },

    async funds() {
      const state = { g: "ALL" };
      const groups = [...new Set(FUNDS.map((r) => r.group))];
      const cols = [
        { key: "n", label: "基金", left: true, wrap: true, cell: (r) => h("span", {}, star(r.symbol), " ", h("b", { style: "font-weight:500" }, r.name), " ", h("span", { class: "chip" }, r.currency)) },
        { key: "g", label: "類型", left: true, cell: (r) => h("span", { class: "chip mk" }, r.group) },
        { key: "e", label: "績效評等", left: true, val: (r) => r.score, cell: (r) => gradePill(r) },
        { key: "s", label: "評分", val: (r) => r.score, cell: (r) => scoreCell(r) },
        { key: "1m", label: "1 月", val: (r) => r.ret["1m"], cell: (r) => numEl(pct(r.ret["1m"]), r.ret["1m"]) },
        { key: "3m", label: "3 月", val: (r) => r.ret["3m"], cell: (r) => numEl(pct(r.ret["3m"]), r.ret["3m"]) },
        { key: "1y", label: "1 年", val: (r) => r.ret["1y"], cell: (r) => numEl(pct(r.ret["1y"]), r.ret["1y"]) },
        { key: "3y", label: "3 年累積", val: (r) => r.ret["3y"], cell: (r) => numEl(pct(r.ret["3y"], 0), r.ret["3y"]) },
        { key: "x", label: "年化勝過指數", tip: "年化報酬減對比指數年化報酬（百分點）", val: (r) => r.excess_ann, cell: (r) => numEl(ok(r.excess_ann) ? signed(r.excess_ann * 100, 1) + " pp" : "–", r.excess_ann) },
        { key: "b1", label: "同類 1 年", val: (r) => r.beat["1y"], cell: (r) => h("span", { class: "num" }, beatText(r.beat["1y"])) },
        { key: "b3", label: "同類 3 年", val: (r) => r.beat["3y"], cell: (r) => h("span", { class: "num" }, beatText(r.beat["3y"])) },
        { key: "sh", label: "Sharpe", val: (r) => r.sharpe, cell: (r) => h("span", { class: "num" }, num(r.sharpe, 2)) },
        { key: "v", label: "年化波動", val: (r) => r.vol_3y, cell: (r) => h("span", { class: "num" }, ok(r.vol_3y) ? num(r.vol_3y * 100, 0) + "%" : "–") },
        { key: "d", label: "最大回檔", val: (r) => r.mdd_3y, cell: (r) => numEl(pct(r.mdd_3y, 0), r.mdd_3y) },
        { key: "t", label: "淨值趨勢", left: true, cell: (r) => (r.horizons ? pill(r.horizons["1m"]) : "–") }];
      const tbl = table(cols, FUNDS, { sort: "s", onRow: (r) => openDrawer(r.symbol) });
      const seg = h("div", { class: "seg", role: "group", "aria-label": "基金類型" }, [["ALL", "全部"], ...groups.map((g) => [g, g])].map(([k, v]) =>
        h("button", { type: "button", "aria-pressed": String(k === "ALL"), onclick: (e) => {
          state.g = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget)));
          tbl.setRows(FUNDS.filter((r) => state.g === "ALL" || r.group === state.g));
        } }, v)));
      const graded = FUNDS.filter((r) => ok(r.score));
      const top = (rows) => rows.slice(0, 2).map((r) => `${r.name.slice(0, 14)}（${num(r.score, 0)}）`).join("、") || "–";
      return [
        head("基金", "台灣投資人常買的境內與境外基金。除了報酬，更重要的是這份報酬是怎麼來的：有沒有贏過同類基金與對比指數、承擔了多少波動與回檔。"),
        FUNDS.length ? h("div", { class: "stats" }, groups.map((g) => {
          const rows = graded.filter((r) => r.group === g);
          return statBox(`${g}・評分最高`, top(rows), `${rows.length} 檔有評等`);
        })) : null,
        regimePanel(),
        aiBlock("本週基金觀察", AI && AI.fund),
        FUNDS.length ? h("div", { class: "panel stack" }, seg, tbl,
          h("p", { class: "chart-note" }, "績效評分 0–100：同類排名 40%、年化勝過對比指數 20%、風險調整報酬（Sharpe）25%、抗跌（三年最大回檔）15%。75 以上優異、60 良好、45 普通、30 偏弱。報酬為原幣計價、含配息；點列看完整評估。"))
          : h("div", { class: "empty" }, "尚無基金資料，下次更新後會出現"),
      ];
    },

    // Is the market safe, what is gathering or losing momentum, and how good were past calls (market.json).
    async safety() {
      const M = await load("market");
      const R = M.risk, G = M.regime, B = M.breadth, F = M.futures, L = M.linkage, A = M.accuracy, X = M.stress;
      const HD = { "1w": "5 日", "1m": "20 日", "1q": "60 日" };
      const riskBar = (v) => {
        const bar = h("i");
        bar.style.cssText = `left:0;width:${Math.max(2, Math.min(100, v))}%;background:var(${v >= 50 ? "--warn" : "--ink-3"});opacity:${v >= 70 || v < 30 ? 1 : 0.6}`;
        return h("span", { class: "meter plain", role: "img", "aria-label": `風險 ${num(v, 0)}` }, bar);
      };
      const LV = { 2: ["rk3", "警戒"], 1: ["rk2", "留意"], 0: ["rk0", "改善"] };
      const fwd = (c) => (c ? h("span", { class: "num" }, num(c.up * 100, 0) + "%", h("span", { class: "muted small" }, `（${pct(c.avg)}）`)) : "–");
      const fwdCols = Object.entries(HD).map(([k, t]) => ({ key: k, label: `之後 ${t}上漲比率（平均報酬）`, val: (r) => r[k] && r[k].up, cell: (r) => fwd(r[k]) }));
      const O = M.index_outlook;
      const bo = B.breakout || {};
      const BO = { confirmed: ["真突破：指數創高、廣度同步", "s1"], false_risk: ["假突破風險：指數創高、廣度沒跟上", "s-2"],
        washout: ["超賣：指數破底、廣度極低", "s-1"], divergence_up: ["底部背離：指數破底、廣度回升", "s1"], none: ["指數不在近 20 日高低點，沒有突破要驗證", "s0"] };
      const momTable = (rows) => table([
        { key: "n", label: "個股", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market, r.symbol)) },
        { key: "c", label: "收盤", cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        { key: "t", label: "籌碼分", val: (r) => r.tactical, cell: (r) => numEl(signed(r.tactical, 2), r.tactical) },
        { key: "d", label: "較上週", val: (r) => r.tactical_chg, cell: (r) => numEl(ok(r.tactical_chg) ? signed(r.tactical_chg, 2) : "–", r.tactical_chg) },
        { key: "rk", label: "同市場排名", val: (r) => r.rank && -r.rank[0], cell: (r) => h("span", { class: "num" }, r.rank ? `${r.rank[0]}／${r.rank[1]}` : "–") },
        { key: "pb", label: "一月跑贏中位數", val: (r) => r.p_beat && r.p_beat["1m"], cell: (r) => h("span", { class: "num" }, r.p_beat && ok(r.p_beat["1m"]) ? num(r.p_beat["1m"] * 100, 0) + "%" : "–") },
        { key: "g", label: "符合條件", left: true, wrap: true, cell: (r) => r.tags.map((t) => h("span", { class: "chip", style: "margin:0 4px 2px 0" }, t)) }],
      rows, { onRow: (r) => openDrawer(r.symbol) });
      const calib = (k, src) => {
        const c = (src || A.backtest)[k];
        if (!c) return null;
        return h("div", { class: "stack" }, h("h4", {}, `${HD[k]}：${nf(0).format(c.n)} 筆`),
          table([{ key: "l", label: "評分等級", left: true, cell: (r) => r.label },
            { key: "n", label: "筆數", cell: (r) => h("span", { class: "num" }, nf(0).format(r.n)) },
            { key: "p", label: "模型：跑贏中位數", cell: (r) => h("span", { class: "num" }, ok(r.p) ? num(r.p * 100, 0) + "%" : "–") },
            { key: "b", label: "實際跑贏中位數", cell: (r) => h("span", { class: "num" }, ok(r.beat) ? num(r.beat * 100, 0) + "%" : "–") },
            { key: "u", label: "實際上漲", cell: (r) => h("span", { class: "num muted" }, ok(r.up) ? num(r.up * 100, 0) + "%" : "–") }], c.bins, { csv: false }),
          h("p", { class: "small" }, `樣本外 Brier ${num(c.brier, 4)}，一律回答 50% 是 ${num(c.brier_coin, 4)}：` +
            (c.edge ? "模型略好，排名有一點參考價值。" : "沒有比較好，排名沒有可驗證的優勢。") + `這段期間個股上漲的比例是 ${num(c.base_up * 100, 0)}%，那是行情，不是模型的功勞。`));
      };
      const live = Object.entries(HD).map(([k, t]) => ({ k, t, ...(A.live[k] || { n: 0 }) }));
      return [
        head("市場安全", "把分散在各頁的訊號合成五個問題的答案：現在大盤是否安全、哪些股票正在累積上漲動能、未來 5／20／60 日的歷史上漲比率、哪些股票可能轉弱、過去的判斷準不準。所有切點都是固定規則，並附上它們過去的實際表現。"),
        h("div", { class: "stats" },
          statBox("市場風險分數（0–100）", num(R.score, 0), `${R.name}風險・參考持股 ${R.exposure}` + (ok(M.risk_prev) ? `・較 ${(M.risk_prev_date || "").slice(5).replace("-", "/")} ${signed(R.score - M.risk_prev, 0)}` : "")),
          statBox("市場週期", G.name, `自 ${G.since} 起第 ${G.days} 個交易日` + (G.prev_name ? `・前一階段 ${G.prev_name}` : "")),
          statBox("廣度指數（0–100）", num(B.value, 0), `月線上 ${num(B.above20 * 100, 0)}%・季線上 ${num(B.above60 * 100, 0)}%`),
          F ? statBox("台指期淨未平倉（外資／投信）", `${signed(F.net_oi, 0)}／${ok(F.trust_oi) ? signed(F.trust_oi, 0) : "–"}`, ok(F.all_oi) ? `三大法人合計 ${signed(F.all_oi, 0)} 口・不計入風險分數` : "不計入風險分數") : null,
          O ? statBox("加權指數近 5 年上漲比率", Object.entries(HD).map(([k]) => (ok(O[k].p_up) ? num(O[k].p_up * 100, 0) : "–") + "%").join(" / "), "5 / 20 / 60 個交易日之後；是歷史比例，不是預測") : null),
        h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "風險警報", help("alerts", "自動風險警報")),
          h("span", { class: "small muted" }, `${M.alerts.filter((a) => a.level === 2).length} 項警戒・${M.alerts.filter((a) => a.level === 1).length} 項留意`)),
          M.alerts.length ? h("div", { class: "alert-list" }, M.alerts.map((a) => h("div", { class: "alert lv" + a.level },
            h("span", { class: "pill " + LV[a.level][0] }, LV[a.level][1]), h("div", {}, h("b", {}, a.title), h("div", { class: "small" }, a.text)))))
            : h("p", { class: "muted" }, "目前沒有觸發任何警報。"),
          h("p", { class: "chart-note" }, "警報由固定規則在每次更新時自動檢查（風險分數、週期轉變、廣度、外資現貨與期貨、融資維持率、VIX、信用利差、半導體、匯率、金融壓力）。本站每週更新一次，警報反映的是資料日當天的狀態。")),
        h("div", { class: "grid g2" },
          h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "風險分數的組成", help("riskscore", "市場風險分數")), h("span", { class: "pill " + RISK_CLS[R.tone] }, `${num(R.score, 0)}・${R.name}風險`)),
            h("div", { class: "risk-parts" }, R.parts.map((p) => h("div", { class: "rp" },
              h("div", { class: "row", style: "justify-content:space-between" }, h("b", {}, p.name, h("span", { class: "muted small" }, ` 權重 ${p.weight}%`)), h("span", { class: "num" }, num(p.risk, 0))),
              riskBar(p.risk), h("div", { class: "small" }, p.reading)))),
            h("p", { class: "chart-note" }, "每項 0 代表安全、100 代表危險，依權重加權平均；缺資料的項目會被剔除。30 以下低風險、30–50 中等、50–70 偏高、70 以上高風險。參考持股水位是依風險等級給的一般性區間，不是個人化建議。")),
          h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "市場週期", help("cycle", "市場週期判斷")), h("span", { class: "pill " + (G.state === "bull" ? "s1" : G.state === "bear" ? "s-2" : G.state === "range" ? "s0" : "s-1") }, G.name)),
            h("div", { class: "kv" }, [["加權指數", px(G.close)], ["季線（60 日）", px(G.ma60)], ["年線（200 日）", px(G.ma200)], ["距一年高點", pct(G.drawdown)]].map(([k, v]) =>
              h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num" }, v)))),
            table([{ key: "s", label: "階段", left: true, cell: (r) => h("span", {}, r.name, r.cur ? h("span", { class: "chip mk", style: "margin-left:6px" }, "目前") : null) },
              { key: "sh", label: "占比", cell: (r) => h("span", { class: "num" }, num(r.share * 100, 0) + "%") }, ...fwdCols],
            ["bull", "range", "pullback", "rebound", "bear"].filter((k) => G.stats[k]).map((k) => ({ name: { bull: "多頭趨勢", range: "高檔盤整", pullback: "多頭回檔", rebound: "空頭反彈", bear: "空頭趨勢" }[k], cur: k === G.state, ...G.stats[k] }))),
            h("p", { class: "chart-note" }, `依收盤價與季線、年線的位置分成五個階段，新階段須連續 3 日成立才算轉變。各欄為該階段之後 5／20／60 個交易日的上漲比率，括號內為平均報酬。統計自 ${G.sample_from} 起的每一個交易日，樣本彼此重疊，且這段期間台股整體大漲，各階段之後的上漲比率都偏高，請比較階段之間的差異而不是絕對數字。`))),
        O ? h("div", { class: "panel stack" }, h("h3", {}, "加權指數：未來 5／20／60 日", help("horizons", "近 5 年上漲比率")),
          table([{ key: "h", label: "期間", left: true, cell: (r) => r.t },
            { key: "p", label: "近 5 年上漲比率", cell: (r) => h("span", { class: "num" }, ok(r.p_up) ? num(r.p_up * 100, 0) + "%" : "–") },
            { key: "r", label: "預估區間", cell: (r) => h("span", { class: "num" }, `${px(r.low)} – ${px(r.high)}`) },
            { key: "b", label: `歷史上「${G.name}」階段之後的上漲比率`, cell: (r) => (r.base ? h("span", { class: "num" }, `${num(r.base.up * 100, 0)}%（平均 ${pct(r.base.avg)}，${r.base.n} 日）`) : "–") }],
          Object.entries(HD).map(([k, t]) => ({ t, ...O[k] }))),
          h("p", { class: "chart-note" }, "兩欄都是歷史統計：近 5 年上漲比率看全部日子；週期階段比率只看指數處在同一週期階段時之後的實際結果。兩者差距大時，代表模型的短線訊號與長期統計不一致，判斷應更保守。")) : null,
        R.series.length ? h("div", { class: "panel stack" }, h("h3", {}, "風險分數的歷史與驗證"),
          flowChart(R.series, "v", "風險分數（可回溯版）", "0–100", { line: true, digits: 0, marks: [[70, "高風險"], [30, "低風險"]] }),
          table([{ key: "l", label: "當時的風險等級", left: true, cell: (r) => r.level }, { key: "s", label: "占比", cell: (r) => h("span", { class: "num" }, num(r.share * 100, 0) + "%") },
            { key: "d", label: "之後 20 日內跌逾 5% 的比例", cell: (r) => h("span", { class: "num" }, r.dd20 ? `${num(r.dd20.dd * 100, 0)}%（${r.dd20.n} 日）` : "–") }, ...fwdCols], R.buckets),
          h("p", { class: "chart-note" }, `歷史分數只用能回溯多年的項目（${R.history_parts.join("、")}），所以與上方的今日分數不完全相同。統計自 ${R.sample_from} 起；樣本重疊、期間以多頭為主，高風險等級的天數很少，結論只能當參考。風險分數分辨得出之後的回檔機率，分辨不了之後的報酬；持股水位規則未經驗證，初步回測（單一多頭期、樣本重疊）顯示在相同平均持股下回檔較小，但報酬低於全額持有。`)) : null,
        h("div", { class: "grid g2" },
          h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "市場廣度指數", help("breadthidx", "廣度指數")), h("span", { class: "pill " + (BO[bo.kind] || BO.none)[1] }, (BO[bo.kind] || BO.none)[0])),
            flowChart(B.series, "v", "廣度指數", "0–100", { line: true, digits: 0, marks: [[80, "過熱"], [20, "超賣"]] }),
            h("p", { class: "chart-note" }, `台股觀察名單中站上月線與季線的個股比例各半，最新一日另以近 10 日漲跌家數比（${ok(B.ad_ratio) ? num(B.ad_ratio, 2) : "–"}）微調。指數創 20 日新高時廣度在 60 以上且沒有下降，才算有多數個股支持的真突破。`)),
          F ? h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "台指期法人淨未平倉", help("fut", "外資台指期")), h("span", { class: "small muted num" }, F.date)),
            flowChart(F.series, "v", "淨未平倉", "口", { line: true, digits: 0 }),
            h("p", { class: "chart-note" }, `外資淨未平倉（圖）：過去一年 ${signed(F.min_250, 0)} 至 ${signed(F.max_250, 0)} 口。外資的淨空單有很大一部分是投信槓桿 ETF 多單的對手：最新一日外資 ${signed(F.net_oi, 0)}、投信 ${ok(F.trust_oi) ? signed(F.trust_oi, 0) : "–"}、三大法人合計 ${ok(F.all_oi) ? signed(F.all_oi, 0) : "–"} 口${ok(F.all_chg20) ? `，合計 20 日變化 ${signed(F.all_chg20, 0)} 口` : ""}。外資淨未平倉與指數水位的相關約 −0.9，單看它會一直顯示「偏空」，所以 2026 年 11 月起不再計入風險分數，只供參考。`)) : null),
        h("div", { class: "grid g2" },
          L ? h("div", { class: "panel stack" }, h("h3", {}, "國際市場連動", help("linkage", "國際連動")),
            table([{ key: "n", label: "市場", left: true, cell: (r) => r.name },
              { key: "5", label: "近 5 日", cell: (r) => numEl(pct(r.r5), r.r5) }, { key: "20", label: "近 20 日", cell: (r) => numEl(pct(r.r20), r.r20) },
              { key: "c", label: "相關係數", tip: `近 ${L.n} 個交易日`, cell: (r) => h("span", { class: "num" }, signed(r.corr, 2)) },
              { key: "b", label: "敏感度", tip: "該市場變動 1% 時，加權指數平均變動幾 %", cell: (r) => h("span", { class: "num" }, signed(r.beta, 2)) },
              { key: "d", label: "資料日", cell: (r) => h("span", { class: "num small" }, r.date.slice(5)) }], L.drivers),
            h("p", { class: "small" }, `三者合計可解釋加權指數近 ${L.n} 日每日漲跌的 ${num(L.r2 * 100, 0)}%。` + (L.has_pending
              ? `在最新台股收盤之後，這些市場又有變動，依敏感度推算對下一個交易日的影響約 ${pct(L.implied_next, 2)}。` : "美股最新收盤已反映在台股最近一個交易日，目前沒有尚未反映的變動。")),
            h("p", { class: "chart-note" }, "美股以前一晚收盤對應台股當日（美股先收盤），匯率以當日對應。相關係數為正代表同向，美元／台幣為負代表台幣貶值時台股傾向下跌。相關不等於因果，且關係會隨時間改變。匯率資料來自 Fed，有數日落差。")) : null,
          X ? h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "全球金融壓力（OFR FSI）", help("fsi", "金融壓力指數")), h("span", { class: "small muted num" }, X.date)),
            h("div", { class: "row" }, h("b", { class: "num", style: "font-size:22px" }, signed(X.value, 2)), h("span", { class: "small muted" }, `20 日 ${signed(X.chg20, 2)}・高於過去五年 ${num(X.pct_5y * 100, 0)}% 的日子`)),
            h("div", { class: "spark-wide" }, spark(X.series.slice(-250).map((r) => r[1]))),
            h("div", { class: "kv" }, X.parts.map((p) => h("div", {}, h("div", { class: "k" }, p.name), h("div", { class: "v num" }, signed(p.value, 2), h("span", { class: "small muted" }, `（${signed(p.chg20, 2)}）`))))),
            h("p", { class: "chart-note" }, "美國財政部金融研究辦公室（OFR）每日公布，由 33 個金融市場變數合成，分為信用、股票估值、融資、避險資產、波動率五類。0 代表長期平均，正值代表壓力高於平均；括號內為 20 日變化。適合判斷全球資金面是否出問題。")) : null),
        h("div", { class: "panel stack" }, h("h3", {}, "正在累積上漲動能", help("accum", "動能累積與轉弱")),
          M.accumulating.length ? momTable(M.accumulating) : h("p", { class: "muted" }, "本週沒有符合條件的個股。"),
          h("p", { class: "chart-note" }, "籌碼分數為正，且六項條件至少符合四項：站上月線、月漲幅未過熱（0–15%）、RSI 45–68、法人連買、短線分數較上週上升、結構面佳。找的是「剛開始轉強、還沒漲多」的股票，不是已經漲最多的。")),
        h("div", { class: "panel stack" }, h("h3", {}, "可能即將轉弱", help("accum", "動能累積與轉弱")),
          M.weakening.length ? momTable(M.weakening) : h("p", { class: "muted" }, "本週沒有符合條件的個股。"),
          h("p", { class: "chart-note" }, "股價仍在季線之上或綜合評分尚未轉空，但六項警訊至少出現三項：籌碼分數下滑、跌破月線、超買後回落、外資連賣、融資增而股價跌、動能落後趨勢。已經明顯走空的股票請看「漲跌預警」。")),
        h("div", { class: "panel stack" }, h("h3", {}, "過去的預測準不準", help("accuracy", "預測準確度")),
          h("h4", {}, "一、本站實際發布過的預測"),
          table([{ key: "t", label: "期間", left: true, cell: (r) => r.t },
            { key: "n", label: "已到期筆數", cell: (r) => h("span", { class: "num" }, r.n ? `${nf(0).format(r.n)}（${r.snapshots} 次發布）` : "尚未到期") },
            { key: "h", label: "方向命中率", cell: (r) => h("span", { class: "num" }, ok(r.hit) ? num(r.hit * 100, 0) + "%" : "–") },
            { key: "u", label: "偏多組平均報酬", cell: (r) => numEl(pct(r.bull_ret), r.bull_ret) },
            { key: "d", label: "偏空組平均報酬", cell: (r) => numEl(pct(r.bear_ret), r.bear_ret) },
], live),
          h("p", { class: "small" }, `自 ${A.first_snapshot} 起，每次更新都會把所有個股與指數的預測存檔（目前 ${A.snapshots} 次），期間走完後自動與實際結果比對。累積的次數還少時，這張表的數字不具代表性。`),
          A.index_log.length ? table([{ key: "d", label: "發布日", left: true, cell: (r) => h("span", { class: "num" }, r.date) },
            { key: "s", label: "指數", left: true, cell: (r) => (bySymbol.get(r.symbol) || S.indices.find((i) => i.symbol === r.symbol) || { name: r.symbol }).name },
            { key: "h", label: "期間", left: true, cell: (r) => HD[r.h] },
            { key: "p", label: "當時顯示的上漲比率", cell: (r) => h("span", { class: "num" }, num(r.p_up * 100, 0) + "%") },
            { key: "r", label: "實際漲跌", cell: (r) => numEl(pct(r.ret), r.ret) },
            { key: "o", label: "方向", cell: (r) => (r.dir_ok == null ? "中性" : r.dir_ok ? "✓" : "✗") },
            { key: "i", label: "落在區間內", cell: (r) => (r.in_range ? "✓" : "✗") }], [...A.index_log].reverse()) : null,
          calibNote() ? h("h4", {}, "二、跑贏中位數機率的校準（樣本外）") : null, calibNote(),
          h("h4", {}, calibNote() ? "三、技術面分數的歷史回測（約三年）" : "二、技術面分數的歷史回測（約三年）"),
          h("div", { class: "grid g2" }, Object.keys(HD).map((k) => calib(k))),
          A.backtest_us && Object.keys(A.backtest_us).length ? h("details", {}, h("summary", { class: "small" }, "美股的同一張表"),
            h("div", { class: "grid g2" }, Object.keys(HD).map((k) => calib(k, A.backtest_us)))) : null,
          h("p", { class: "small" }, "讀法：「模型：跑贏中位數」是網站依排名給的機率，「實際跑贏中位數」是之後真的跑贏同市場中位數股票的比例。兩欄越接近、且由強勢偏空往強勢偏多逐步升高，排名越可信。「實際上漲」主要反映這段期間的大盤行情。"),
          h("p", { class: "chart-note" }, `回測只能重建技術面分數（籌碼與基本面沒有歷史快照），樣本是現在的熱門股（倖存者偏差），且期間以多頭為主${S.selection_bias ? `：${S.selection_bias.from} 到 ${S.selection_bias.to}（${S.selection_bias.days} 個交易日），今日名單等權上漲 ${pct(S.selection_bias.today, 0)}，當時成交值前 90 名只有 ${pct(S.selection_bias.pit, 0)}，所以所有優勢數字都只是上限` : ""}。若高分組的實際上漲比率沒有明顯高於低分組，就表示分數的區分力有限：請把它當成篩選與排序工具，搭配上方的市場風險與部位控制使用。精選個股的實際表現見「精選 20 檔」頁。`)),
      ];
    },

    async alerts() {
      const list = (rows, kind) => table([
        { key: "n", label: "個股", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market, r.symbol)) },
        { key: "c", label: "收盤", cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週漲跌", cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        rankCol,
        { key: "2", label: "同市場排名", val: (r) => r.rank && -r.rank[0], cell: (r) => h("span", { class: "num" }, r.rank ? `${r.rank[0]}／${r.rank[1]}` : "–") },
        { key: "t", label: "籌碼分", tip: "外資、投信、融資（只有台股）", val: (r) => r.tactical, cell: (r) => numEl(ok(r.tactical) ? signed(r.tactical, 2) : "–", r.tactical) },
        { key: "s", label: "基本面分", tip: "財報與申報持股", val: (r) => r.structural, cell: (r) => numEl(ok(r.structural) ? signed(r.structural, 2) : "–", r.structural) },
        { key: "a", label: "ATR%", tip: "近 14 日平均每日波動幅度", val: (r) => r.plan.atr_pct, cell: (r) => h("span", { class: "num" }, ok(r.plan.atr_pct) ? num(r.plan.atr_pct * 100, 1) + "%" : "–") },
        { key: "p", label: "部位上限", tip: "願意承擔 1% 資金虧損時的最大部位", val: (r) => r.plan.size, cell: (r) => h("span", { class: "num" }, ok(r.plan.size) ? num(r.plan.size * 100, 0) + "%" : "–") },
        { key: "y", label: kind === "up" ? "主要理由" : "主要風險", left: true, wrap: true, cell: (r) => h("span", {}, (kind === "up" ? r.reasons : r.risks).slice(0, 2).join("；") || "–",
          r.setup ? h("span", { class: "chip", style: "margin-left:6px" }, r.setup) : null) }],
      rows, { onRow: (r) => openDrawer(r.symbol) });
      return [
        head("漲跌預警", "依同市場排名，列出目前相對最強與最弱的個股。排名反映的是相對強弱，不是保證。ATR% 越高的股票波動越大，同樣的風險預算下部位要越小。"),
        h("div", { class: "panel" }, h("h3", {}, "相對最強：同市場排名最前的 12 檔"), list(S.bullish, "up")),
        h("div", { class: "panel" }, h("h3", {}, "相對最弱：同市場排名最後的 12 檔"), list(S.bearish, "down")),
      ];
    },

    async podcast() {
      const P = await load("podcasts");
      const hot = P.hot || [];
      const notes = (AI && AI.data_date === S.data_date && AI.podcast && AI.podcast.stocks) || {};
      const nm = (s) => (bySymbol.get(s) || {}).name || (P.names || {})[s] || s;
      return [
        head("Podcast 熱議", "近兩週知名財經 Podcast 的節目標題與簡介中提到的個股與 ETF，並對照它們目前的數據。被討論不等於看多：聲量高而數據轉弱，往往是該小心的時候。"),
        aiBlock("本週 Podcast 話題解讀", AI && AI.podcast && AI.podcast.overview),
        h("div", { class: "panel stack" }, h("h3", {}, "熱議個股分析"), hot.length ? table([
          { key: "n", label: "標的", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market, r.symbol)) },
          { key: "m", label: "提及集數", val: (r) => r.mentions, cell: (r) => h("b", { class: "num" }, r.mentions) },
          { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
          { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
          { key: "o", label: "綜合分", val: (r) => r.overall, cell: (r) => h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter(r.overall), numEl(signed(r.overall, 2), r.overall)) },
          rankCol,
          { key: "v", label: "聲量對照數據", left: true, wrap: true, cell: (r) => h("div", { class: "stack", style: "gap:4px" },
            h("span", {}, r.view),
            r.signal ? h("span", { class: "chip " + (r.signal.startsWith("法人先行") ? "add" : "warn") }, r.signal) : null,
            r.weeks > 1 ? h("span", { class: "small muted" }, `已連續 ${r.weeks} 週被提及`) : null,
            notes[r.symbol] ? h("span", { class: "small muted" }, "Claude：" + notes[r.symbol]) : null,
            h("span", { class: "small muted" }, "出處：" + [...new Set(r.episodes.map((e) => e.show))].join("、"))) }],
        hot, { sort: "m", onRow: (r) => openDrawer(r.symbol) })
          : h("div", { class: "empty" }, "近兩週節目簡介中沒有比對到觀察名單內的標的"),
        hot.length ? h("p", { class: "chart-note" }, "「聲量對照數據」由規則產生：比較提及次數與該標的的綜合分數、RSI、近一週漲跌。只分析節目標題與簡介，未轉錄音訊。") : null),
        h("div", { class: "grid g2" }, (P.shows || []).filter((s) => s.episodes.length).map((s) => h("div", { class: "panel" },
          h("h3", {}, s.name), s.episodes.map((e) => h("div", { class: "ep" },
            h("div", { class: "ep-t" }, e.link ? h("a", { href: e.link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title),
            h("div", { class: "small muted num" }, e.date),
            e.summary ? h("div", { class: "small" }, e.summary) : null,
            e.mentions.length ? h("div", { class: "row" }, e.mentions.map((m) => h("button", { class: "chip mk", type: "button", onclick: () => bySymbol.has(m) && openDrawer(m) }, nm(m)))) : null))))),
      ];
    },

    async etf() {
      const state = { m: "ALL", c: "ALL" };
      const cats = [...new Set(E.map((r) => r.industry))];
      const cols = [
        { key: "n", label: "ETF", left: true, cell: (r) => h("span", {}, star(r.symbol), " ", nameCell(r), " ", mkChip(r.market, r.symbol)) },
        { key: "i", label: "類別", left: true, cell: (r) => h("span", { class: "chip" + (/槓桿|反向/.test(r.industry) ? " warn" : "") }, r.industry) },
        { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        { key: "m", label: "月", val: (r) => r.r20, cell: (r) => numEl(pct(r.r20), r.r20) },
        { key: "q", label: "季", val: (r) => r.r60, cell: (r) => numEl(pct(r.r60), r.r60) },
        { key: "y", label: "今年", val: (r) => r.ytd, cell: (r) => numEl(pct(r.ytd), r.ytd) },
        { key: "1y", label: "一年", tip: "台股 ETF 含配息還原；美股 ETF 是價格報酬（不含配息），兩者不宜直接比", val: (r) => r.r250, cell: (r) => numEl(pct(r.r250), r.r250) },
        { key: "dv", label: "近 12 月配息率", tip: "僅台股 ETF：由除息參考價推算", val: (r) => r.dist12, cell: (r) => h("span", { class: "num" }, ok(r.dist12) ? num(r.dist12 * 100, 1) + "%" : "–") },
        { key: "pm", label: "折溢價", tip: "市價相對預估淨值；溢價 1% 以上等於多付錢", val: (r) => r.prem, cell: (r) => h("span", { class: "num" + (ok(r.prem) && Math.abs(r.prem) >= 1 ? " warn-text" : "") }, ok(r.prem) ? signed(r.prem, 2) + "%" : "–") },
        { key: "au", label: "規模（億）", val: (r) => r.aum, cell: (r) => h("span", { class: "num" }, ok(r.aum) ? nf(0).format(r.aum / 1e8) : "–") },
        { key: "d", label: "一年最大回檔", val: (r) => r.mdd, cell: (r) => numEl(pct(r.mdd), r.mdd) },
        { key: "v", label: "年化波動", val: (r) => r.vol_ann, cell: (r) => h("span", { class: "num" }, ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–") },
        { key: "f", label: "外資 5 日（張）", tip: "僅台股 ETF", val: (r) => r.foreign5, cell: (r) => numEl(ok(r.foreign5) ? signed(r.foreign5, 0) : "–", r.foreign5) },
        { key: "o", label: "綜合分", val: (r) => r.overall, cell: (r) => h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter(r.overall), numEl(signed(r.overall, 2), r.overall)) }];
      const tbl = table(cols, E, { sort: "o", onRow: (r) => openDrawer(r.symbol) });
      const apply = () => tbl.setRows(E.filter((r) => (state.m === "ALL" || r.market === state.m) && (state.c === "ALL" || r.industry === state.c)));
      const seg = h("div", { class: "seg", role: "group", "aria-label": "市場" }, [["ALL", "全部"], ["TW", "台股"], ["US", "美股"]].map(([k, v]) =>
        h("button", { type: "button", "aria-pressed": String(k === "ALL"), onclick: (e) => {
          state.m = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); apply();
        } }, v)));
      const sel = h("select", { id: "etf-cat", "aria-label": "ETF 類別", onchange: () => { state.c = sel.value; apply(); } },
        h("option", { value: "ALL" }, "所有類別"), cats.map((c) => h("option", { value: c }, c)));
      const best = (rows) => rows.slice(0, 3).map((r) => `${r.name}（${signed(r.overall, 2)}）`).join("、") || "–";
      const tw = E.filter((r) => r.market === "TW"), us = E.filter((r) => r.market === "US");
      return [
        head("ETF", "台股熱門與核心 ETF、美股主要市值型／產業／債券／商品 ETF。ETF 沒有個別財報，分數只看價格趨勢與動能，台股 ETF 另計外資與投信買賣超。"),
        E.length ? h("div", { class: "stats" },
          statBox("台股 ETF 最強", best(tw), `共 ${tw.length} 檔`), statBox("台股 ETF 最弱", best([...tw].reverse()), ""),
          statBox("美股 ETF 最強", best(us), `共 ${us.length} 檔`), statBox("美股 ETF 最弱", best([...us].reverse()), "")) : null,
        regimePanel(),
        aiBlock("本週 ETF 觀察", AI && AI.etf),
        E.length ? h("div", { class: "panel stack" }, h("div", { class: "row" }, seg, sel), tbl,
          h("p", { class: "chart-note" }, "槓桿型與反向型 ETF 每日重新平衡，長期持有會有耗損；反向型「偏多」代表的是看空大盤。點列看圖表與參考價位。"))
          : h("div", { class: "empty" }, "尚無 ETF 資料，下次更新後會出現"),
      ];
    },

    async learn() {
      const KB = window.KB || [];
      const USE = { score: ["mk", "計入分數"], context: ["", "僅供判讀"], plan: ["warn", "操作參考"], info: ["", "觀念"] };
      const live = (it) => {
        if (!it.live) return null;
        const [kind, key] = it.live.split(":");
        const f = S.tw_flow || {};
        if (kind === "card") { const c = LEAD.cards.find((x) => x.key === key); return c ? `${c.value_text}（${c.change_text}）` : null; }
        if (kind === "fut") return f.futures ? `${signed(f.futures.net_oi, 0)} 口` : null;
        if (kind === "calib") { const c = (S.calibration || {}).TW || {};
          return Object.keys(c).length ? "台股斜率 " + Object.entries(c).map(([k, v]) => `${HZ[k]} ${num(v.k, 2)}`).join("・") : null; }
        if (key === "foreign_5d") return ok(f.foreign_5d) ? `近 5 日 ${signed(f.foreign_5d, 0)} 億元` : null;
        if (key === "margin_bal") return ok(f.margin_bal) ? `${num(f.margin_bal, 0)} 億元（一週 ${pct(f.margin_chg5)}）` : null;
        if (key === "maint") return ok(f.maint) ? `${num(f.maint * 100, 1)}%（${f.maint_status}）` : null;
        return null;
      };
      const body = h("div", { class: "stack", style: "gap:24px" });
      const count = h("span", { class: "small muted" });
      const draw = (q) => {
        let n = 0;
        const groups = KB.map((g) => {
          const items = g.items.filter((it) => !q || [it.t, it.what, it.how, it.read, it.note].join(" ").toLowerCase().includes(q));
          n += items.length;
          return items.length ? h("section", { class: "stack", id: "kbg-" + KB.indexOf(g) }, h("h3", {}, g.group), !q ? h("p", { class: "lead" }, g.lead) : null,
            h("div", { class: "kbs" }, items.map((it) => { const lv = live(it); return h("article", { class: "kb", id: "kb-" + it.id },
              h("div", { class: "kb-h" }, h("b", {}, it.t), h("span", { class: "chip " + USE[it.use][0] }, USE[it.use][1]),
                lv ? h("span", { class: "chip", title: "本週數值" }, "目前 " + lv) : null),
              h("p", {}, it.what),
              h("dl", {}, [["本站怎麼算", it.how], ["怎麼解讀", it.read], ["留意", it.note]].filter(([, v]) => v).map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]))); }))) : null;
        }).filter(Boolean);
        count.textContent = q ? `找到 ${n} 則` : `共 ${n} 則`;
        body.replaceChildren(...(groups.length ? groups : [h("div", { class: "empty" }, "沒有符合的項目")]));
      };
      const q = h("input", { type: "search", id: "kb-q", placeholder: "搜尋指標，例如 融資維持率、RSI、停損", "aria-label": "搜尋指標解說",
        oninput: () => draw(q.value.trim().toLowerCase()) });
      draw("");
      return [
        head("指標與模型解說", "本站每個數字是什麼、怎麼算、背後代表什麼，以及各種預測標籤該怎麼解讀。標示「計入分數」的會影響預測，「僅供判讀」的只顯示給你參考，「操作參考」是價位與部位的計算方式。"),
        h("div", { class: "panel stack kb-nav" }, h("div", { class: "row" }, q, count),
          h("div", { class: "row" }, KB.map((g, i) => h("button", { class: "chip", type: "button", onclick: () => {
            q.value = ""; draw(""); document.getElementById("kbg-" + i).scrollIntoView({ block: "start" }); } }, g.group)))),
        body,
      ];
    },

    async method() {
      const tools = await load("tools").catch(() => []);
      const BT = S.backtest || {};
      const bt = BT.TW || BT;
      const W = S.weights || {};
      const G = { tech: "技術面（趨勢＋動能）", chips: "籌碼面", fund: "基本面", buzz: "Podcast 聲量（只顯示，不計分）" };
      return [
        head("方法與資料來源", "所有分數都由公開資料以固定規則計算，沒有人工挑選。以下說明計算方式、回測結果與每個來源本週的取得狀態。"),
        h("div", { class: "grid g2" },
          h("div", { class: "panel stack" }, h("h3", {}, "因子權重"),
            table([{ key: "g", label: "因子", left: true, cell: (r) => G[r] },
              ...Object.keys(HZ).map((k) => ({ key: k, label: HZ[k], cell: (r) => h("span", { class: "num" }, nf(0).format(((W[k] || {})[r] || 0) * 100) + "%") }))],
            Object.keys(G)),
            S.weights_us ? h("p", { class: "small" }, "上表為台股。美股的籌碼來自議員交易與 13F 申報，落後數週，所以權重較低：" +
              Object.keys(HZ).map((k) => `${HZ[k]} 技術 ${nf(0).format(S.weights_us[k].tech * 100)}%／籌碼 ${nf(0).format(S.weights_us[k].chips * 100)}%／基本面 ${nf(0).format(S.weights_us[k].fund * 100)}%`).join("；") + "。") : null,
            h("p", { class: "small" }, h("a", { href: "#learn" }, "每個指標與預測的原理，見「指標與模型解說」")),
            h("p", { class: "small" }, "每個因子先壓縮到 −1 至 +1：趨勢看均線排列與季線斜率；動能看波動度調整後的報酬、MACD、RSI；台股籌碼看外資／投信買賣超占成交量比重、連續天數與融資變化；美股籌碼看議員交易與 13F 增減；基本面看營收與 EPS 成長、近 3 月對 12 月的營收動能、本益比、殖利率。缺少的因子會自動剔除並重新分配權重。"),
            h("p", { class: "small" }, "預測區間＝現價 ×（1 ＋ 偏移 ± σ），σ 為近 60 日波動度乘以天數平方根，偏移隨機率斜率縮放。綜合分數＝一週 30%＋一月 40%＋一季 30%。Podcast 聲量的權重為 0，只顯示不計分。")),
          h("div", { class: "panel stack" }, h("h3", {}, "技術面分數回測"),
            Object.values(bt).some(Boolean) ? table([
              { key: "h", label: "期間", left: true, cell: (k) => HZ[k] },
              { key: "ic", label: "IC", tip: "分數排名與後續報酬排名的相關係數", cell: (k) => h("span", { class: "num" }, bt[k] ? signed(bt[k].ic, 3) : "–") },
              { key: "t", label: "高分組報酬", cell: (k) => numEl(bt[k] ? pct(bt[k].top_ret, 2) : "–", bt[k] && bt[k].top_ret) },
              { key: "a", label: "全體平均", cell: (k) => numEl(bt[k] ? pct(bt[k].avg_ret, 2) : "–", bt[k] && bt[k].avg_ret) },
              { key: "b", label: "低分組報酬", cell: (k) => numEl(bt[k] ? pct(bt[k].bottom_ret, 2) : "–", bt[k] && bt[k].bottom_ret) },
              { key: "w", label: "高分組勝過平均", cell: (k) => h("span", { class: "num" }, bt[k] ? nf(0).format(bt[k].top_beats_avg * 100) + "%" : "–") }],
            Object.keys(HZ)) : h("div", { class: "empty" }, "樣本不足，尚無回測結果"),
            BT.US ? h("p", { class: "small" }, "上表為台股。美股的 IC：" + Object.keys(HZ).map((k) => `${HZ[k]} ${BT.US[k] ? signed(BT.US[k].ic, 3) : "–"}`).join("、") +
              "，接近 0，代表美股的分數沒有可驗證的選股能力，所以美股的相對勝率一律顯示「無可驗證優勢」。") : null,
            h("p", { class: "small" }, "做法：過去約三年每 5 個交易日，用當時能取得的價格資料計算技術面分數，比較分數最高與最低各 20% 個股在之後一週／一月／一季的平均報酬。"),
            S.backtest_imom && Object.values(S.backtest_imom).some(Boolean) ? h("p", { class: "small" }, "對照：純超額強度（扣掉大盤後的個股動能）的 IC 為 " +
              Object.keys(HZ).map((k) => `${HZ[k]} ${S.backtest_imom[k] ? signed(S.backtest_imom[k].ic, 3) : "–"}`).join("、") + "。目前只列為參考欄位，未計入分數。", help("imom", "純超額強度")) : null,
            h("p", { class: "small muted" }, "限制：樣本是「現在」的熱門股，存在倖存者偏差" + (S.selection_bias ? `（${S.selection_bias.from} 到 ${S.selection_bias.to} 的 ${S.selection_bias.days} 個交易日，今日名單等權 ${pct(S.selection_bias.today, 0)}，當時成交值前 90 名只有 ${pct(S.selection_bias.pit, 0)}，所以 IC 與跑贏比例都只是上限）` : "") + "；籌碼與基本面因子沒有足夠歷史資料，未納入回測；過去績效不代表未來。"))),
        calibNote() ? h("div", { class: "panel stack" }, h("h3", {}, "跑贏中位數機率的校準"), calibNote()) : null,
        (S.calendar || []).length ? h("div", { class: "panel stack" }, h("h3", {}, "接下來兩週的行事曆"), table([
          { key: "d", label: "日期", left: true, cell: (r) => h("span", { class: "num" }, r.date) },
          { key: "k", label: "類別", left: true, cell: (r) => h("span", { class: "chip" }, r.kind) },
          { key: "t", label: "內容", left: true, wrap: true, cell: (r) => r.text }], S.calendar, { csv: "calendar", onRow: (r) => r.symbol && openDrawer(r.symbol) }),
          h("p", { class: "chart-note" }, "除權息與處置資料來自證交所公告，只列本站追蹤的台股與 ETF；結算日與公布截止日依規則推算。")) : null,
        S.quality ? h("details", { class: "panel" }, h("summary", {}, h("b", {}, "資料品質報告"), h("span", { class: "small muted" }, "（點開）")),
          h("div", { class: "stack", style: "margin-top:10px" },
            h("p", { class: "small" }, "建置時會自動刪除不可能的 K 棒（週末、休市日、單日跳動超過漲跌幅後又回到原價），並依證交所「除權除息計算結果表」把除權息前的價格等比例還原，讓均線、報酬與停損不會把配息當成下跌。下表列出本次被處理的序列。"),
            table([{ key: "s", label: "標的", left: true, cell: ([sym]) => (bySymbol.get(sym) || { name: sym }).name + " " + sym },
              { key: "n", label: "處理內容", left: true, wrap: true, cell: ([, n]) => n.slice(0, 4).join("；") + (n.length > 4 ? `；另 ${n.length - 4} 項` : "") }],
            Object.entries(S.quality.cleaned || {}), { csv: "quality" }),
            h("p", { class: "small" }, "欄位覆蓋率：" + Object.entries(S.quality.coverage || {}).map(([mk, c]) => `${mk === "TW" ? "台股" : "美股"} ` +
              Object.entries(c).map(([k, v]) => `${k} ${num(v * 100, 0)}%`).join("、")).join("；") + "。"),
            ...["TW", "US"].filter((mk) => (S.quality.behind || {})[mk] && S.quality.behind[mk].length).map((mk) =>
              h("p", { class: "small" }, `${mk === "TW" ? "台股" : "美股"}最後一根 K 棒比同市場晚的標的：${S.quality.behind[mk].join("、")}（照常評分，週漲跌可能差一天）。`)))) : null,
        h("div", { class: "panel" }, h("h3", {}, "本週資料來源狀態"), h("div", { class: "src" }, S.sources.map((s) => [
          h("span", { class: "dot " + (s.ok ? "ok" : "bad") }, s.ok ? "✓ 成功" : "✕ 未取得"),
          h("span", {}, s.url ? h("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.name) : s.name, s.note ? h("span", { class: "muted" }, "　" + s.note) : null)]))),
        h("div", { class: "panel stack" }, h("h3", {}, "基金績效怎麼評"),
          h("p", { class: "small" }, "報酬、對比指數報酬、同類平均與同類排名採用鉅亨網基金頁提供的晨星資料（含配息）；年化波動、最大回檔、Sharpe、正報酬月份比例由本站用每日淨值自行計算。評分把四個面向各換算成 0–100 分再加權：同類排名 40%（近 1／3／5 年贏過同類的比例，3 年權重最高）、年化報酬勝過對比指數 20%（±10 個百分點對應 0／100 分）、Sharpe 25%（0.5 為中點）、三年最大回檔 15%。成立未滿一年不評等。"),
          h("p", { class: "small muted" }, "限制：淨值不含配息還原，配息級別的回檔會被高估，所以名單優先選累積級別；Sharpe 的無風險利率依計價幣別取固定值；過去績效不代表未來。")),
        h("div", { class: "panel stack" }, h("h3", {}, "更新方式與 AI 觀點"),
          h("p", { class: "small" }, "每週三由 Claude 排程自動執行：抓取資料、計算分數、撰寫觀點，然後直接推送到 GitHub。「AI 觀點」「Podcast 話題解讀」「ETF 觀察」「基金觀察」四段文字由當次執行的 Claude 只根據本頁同一批數據撰寫，並經過程式檢查資料日期一致才會發布；其餘數字與判讀全部由固定規則計算。")),
        tools.length ? h("div", { class: "panel" }, h("h3", {}, "參考的開源投資與 AI 工具（GitHub 星數排序）"), table([
          { key: "n", label: "專案", left: true, cell: (r) => h("a", { href: r.url, target: "_blank", rel: "noopener noreferrer" }, r.name) },
          { key: "s", label: "星數", cell: (r) => h("span", { class: "num" }, nf(0).format(r.stars)) },
          { key: "d", label: "說明", left: true, wrap: true, cell: (r) => r.desc }], tools),
          h("p", { class: "chart-note" }, "本站的因子設計參考了這類專案常見的做法（多因子評分、技術指標、籌碼追蹤、LLM 摘要），每週自動更新清單。")) : null,
      ];
    },
  };

  // ---------- router ----------
  const app = document.getElementById("app");
  function drawNav(name) {
    const gi = Math.max(0, NAV.findIndex(([, vs]) => vs.some(([v]) => v === name)));
    document.getElementById("groups").replaceChildren(...NAV.map(([label, vs], i) =>
      h("a", { href: "#" + vs[0][0], "aria-current": i === gi ? "page" : null }, label)));
    const sub = NAV[gi][1].map(([v, label]) => h("a", { href: "#" + v, "aria-current": v === name ? "page" : null }, label));
    document.getElementById("tabs").replaceChildren(...sub);
    const cur = document.querySelector('#groups a[aria-current="page"]');
    if (cur) cur.scrollIntoView({ block: "nearest", inline: "center" });
    measureMast();
  }
  // Anchored scrolling (knowledge entries) has to clear the sticky masthead, whose height depends on wrapping.
  const measureMast = () => document.documentElement.style.setProperty("--mast-h", document.querySelector(".mast").offsetHeight + "px");
  // On a phone the masthead takes a quarter of the screen: fold the title row away while reading.
  let folded = false;
  window.addEventListener("scroll", () => {
    const f = window.scrollY > 120;
    if (f !== folded) { folded = f; document.body.classList.toggle("scrolled", f); measureMast(); }
  }, { passive: true });
  window.addEventListener("resize", measureMast);
  // Unmount: destroy every chart of the view being left, so nothing of it can draw into the next one.
  function unmount() {
    hideDrawer();
    [...liveCharts].forEach((dispose) => dispose());
    liveCharts.clear();
    charts.clear();
  }
  let routeSeq = 0;
  async function route() {
    const seq = ++routeSeq;
    const { name, p } = parseHash();
    drawNav(name);
    if (name !== current) {
      unmount();
      current = null;
      let nodes;
      try {
        nodes = (await views[name]()).filter(Boolean);
      } catch (e) {
        nodes = [h("div", { class: "empty" }, "這個分頁的資料載入失敗：" + e.message)];
      }
      // A newer navigation started while this view was loading: drop this result, or two views
      // would end up on the page at once.
      if (seq !== routeSeq) { nodes.forEach((n) => n.querySelectorAll && n.querySelectorAll(".stack").forEach((x) => x.destroy && x.destroy())); return; }
      app.replaceChildren(...nodes);
      current = name;
      window.scrollTo(0, 0);
    }
    if (seq !== routeSeq) return;
    const sym = p.get("s");
    if (!sym) hideDrawer(); else if (sym !== drawerSym) showDrawer(sym);
    const target = p.get("k") && document.getElementById("kb-" + p.get("k"));
    if (target) {
      document.querySelectorAll(".kb.hit").forEach((n) => n.classList.remove("hit"));
      target.classList.add("hit");
      requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
    }
  }

  // A stalled or failed load must end in something the reader can act on, never an endless spinner.
  const timeout = (ms) => new Promise((_, rej) => setTimeout(() => rej(new Error(`超過 ${ms / 1000} 秒沒有回應`)), ms));
  function loadFailed(e) {
    app.replaceChildren(h("div", { class: "empty stack", style: "align-items:center" },
      h("div", {}, "資料載入失敗：" + e.message),
      h("div", { class: "small" }, "可能是網路不穩，或本週資料正在更新。"),
      h("button", { class: "close", type: "button", onclick: () => { cache.clear(); app.replaceChildren(h("div", { class: "empty" }, "重新載入中…")); boot(); } }, "重新載入")));
  }
  let booted = false;
  async function boot() {
    try {
      [S, U] = await Promise.race([Promise.all([load("summary"), load("universe")]), timeout(15000)]);
    } catch (e) {
      loadFailed(e);
      return;
    }
    [E, FUNDS, AI, LEAD, MK] = await Promise.all([load("etf").catch(() => []), load("funds").catch(() => []),
      load("ai").catch(() => null), load("leading").catch(() => ({ cards: [], series: {} })), load("market").catch(() => null)]);
    paintWatch();
    FUNDS.forEach((r) => bySymbol.set(r.symbol, r));
    U.forEach((r) => bySymbol.set(r.symbol, r));
    E.forEach((r) => bySymbol.set(r.symbol, r));
    [...S.picks, ...S.bullish, ...S.bearish].forEach((r) => bySymbol.set(r.symbol, r));
    document.getElementById("stamp").replaceChildren(
      h("span", {}, "資料日 ", h("b", { class: "num" }, S.data_date)),
      h("span", {}, "更新於 ", h("b", { class: "num" }, S.generated_at.slice(0, 16).replace("T", " "))),
      h("span", {}, "下次更新 ", h("b", { class: "num" }, S.next_update + "（三）")),
      (() => {
        const bad = S.sources.filter((x) => !x.ok);
        const fixed = Object.keys((S.quality || {}).cleaned || {}).length;
        return h("a", { class: "health" + (bad.length ? " warn" : ""), href: "#method", title: bad.length ? "未取得：" + bad.map((x) => x.name).join("、") : "本次更新所有資料來源都成功取得" },
          (bad.length ? `資料來源 ${S.sources.length - bad.length}/${S.sources.length}，${bad.length} 項沿用舊資料` : `資料來源 ${S.sources.length} 項正常`) +
          (fixed ? `・${fixed} 檔價格經清理還原` : ""));
      })());
    if (!booted) window.addEventListener("hashchange", route);
    booted = true;
    current = null;
    route();
  }

  // Up/down colours: Taiwan convention (red up) by default, switchable to the US one (green up).
  const root = document.documentElement, toggle = document.getElementById("updown");
  const paintToggle = () => {
    const us = root.dataset.updown === "us";
    toggle.textContent = us ? "綠漲紅跌" : "紅漲綠跌";
    toggle.setAttribute("aria-pressed", String(us));
    document.getElementById("legend").replaceChildren("顏色目前為",
      h("span", { class: "up" }, us ? "綠＝上漲／買超" : "紅＝上漲／買超"), "、", h("span", { class: "down" }, us ? "紅＝下跌／賣超" : "綠＝下跌／賣超"),
      "，可在頁首切換。");
  };
  try { if (localStorage.getItem("updown") === "us") root.dataset.updown = "us"; } catch (e) { /* storage unavailable */ }
  toggle.addEventListener("click", () => {
    if (root.dataset.updown === "us") delete root.dataset.updown; else root.dataset.updown = "us";
    try { localStorage.setItem("updown", root.dataset.updown || "tw"); } catch (e) { /* storage unavailable */ }
    paintToggle();
    retheme();
  });
  paintToggle();
  drawNav(location.hash.slice(1).split("?")[0]);

  const retheme = () => charts.forEach((fn) => fn());
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);
  new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  boot();
})();
