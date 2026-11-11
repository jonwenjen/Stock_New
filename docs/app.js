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
  const mkChip = (m) => h("span", { class: "chip mk" }, m === "TW" ? "台股" : "美股");
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
  let AI = null; // ai.json - commentary written by Claude during the weekly run
  const bySymbol = new Map();

  // ---------- sortable table ----------
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
        const tr = h("tr", { class: opts.onRow ? "click" : null, tabindex: opts.onRow ? 0 : null },
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
    const el = h("div", { class: "tbl-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ths)), tbody));
    el.setRows = (r) => { rows = r; draw(); };
    return el;
  }

  // ---------- price chart (candles + volume + MAs, switchable range) ----------
  const RANGES = [["1d", "天"], ["1w", "週"], ["1m", "月"], ["3m", "季"], ["6m", "2季"],
    ["ytd", "當年"], ["2y", "2年"], ["5y", "5年"], ["all", "所有"]];
  const charts = new Set();

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
    const box = h("div", { class: "chart" + (opts.small ? " sm" : "") });
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
    const root = h("div", { class: "stack" }, h("div", { class: "chart-bar" }, seg, legend), readout, box, note);

    const chart = LightweightCharts.createChart(box, { ...themeOpts(), autoSize: true,
      handleScale: { axisPressedMouseMove: false }, localization: { locale: "zh-TW" } });
    const candle = chart.addCandlestickSeries({ priceLineVisible: false });
    const navLine = chart.addLineSeries({ lineWidth: 2, priceLineVisible: false }); // funds: one NAV a day, no candles
    const vol = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, priceLineVisible: false, lastValueVisible: false });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false });
    chart.priceScale("right").applyOptions({ scaleMargins: { top: 0.06, bottom: 0.2 } });
    const mas = [20, 60, 120].map(() => chart.addLineSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }));
    let rowsByTime = new Map();

    const applyTheme = () => {
      chart.applyOptions(themeOpts());
      const up = css("--up"), down = css("--down");
      candle.applyOptions({ upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down });
      ["--ma1", "--ma2", "--ma3"].forEach((c, i) => mas[i].applyOptions({ color: css(c) }));
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
      [candle, navLine, vol, ...mas].forEach((s) => s.setData([]));
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
      chart.applyOptions({ timeScale: { timeVisible: !!intraday, secondsVisible: false } });
      fit(chart);
      show(rows[rows.length - 1]);
      const start = (state.data.w && state.data.w[0] ? state.data.w[0][0] : daily[0] && daily[0][0]) || "";
      note.textContent = (isLine ? `每日淨值・${rows.length} 筆` : `${unit}K・${rows.length} 根`) +
        (fallback ? (isLine ? "・基金每日只有一個淨值，顯示最近 5 筆" : "・此標的無分時資料，改以最近 5 根日 K 呈現") : "") +
        (intraday ? "・分時資料為更新當下的最近交易日" : "") + (start ? `・歷史資料起始 ${start}` : "");
    }
    chart.subscribeCrosshairMove((p) => {
      if (!p.time) { const { rows } = selectBars(state.data || {}, state.range); show(rows[rows.length - 1]); return; }
      const key = typeof p.time === "object" ? `${p.time.year}-${String(p.time.month).padStart(2, "0")}-${String(p.time.day).padStart(2, "0")}` : p.time;
      show(rowsByTime.get(key));
    });
    charts.add(applyTheme);
    root.destroy = () => { charts.delete(applyTheme); chart.remove(); };
    load("charts/" + fileOf(symbol)).then((d) => { state.data = d; applyTheme(); })
      .catch(() => { box.replaceChildren(h("div", { class: "empty" }, "這檔標的目前沒有圖表資料")); });
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
    applyTheme();
    return root;
  }

  // ---------- drawer ----------
  let drawerEl = null, lastFocus = null;
  function closeDrawer() {
    if (!drawerEl) return;
    drawerEl.querySelectorAll(".stack").forEach((n) => n.destroy && n.destroy());
    drawerEl.remove(); drawerEl = null;
    document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }
  function forecastGrid(horizons) {
    return h("div", { class: "fc" }, Object.entries(horizons).map(([k, f]) => [
      h("span", { class: "h" }, "未來" + HZ[k]),
      h("span", { class: "b" }, pill(f), meter(f.score),
        h("span", { class: "small" }, "上漲機率 ", h("b", { class: "num" }, nf(0).format(f.p_up * 100) + "%")),
        h("span", { class: "small muted num" }, `區間 ${px(f.low)} – ${px(f.high)}`)),
    ]));
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
        h("div", {}, h("div", { class: "t" }, r.name, h("span", { class: "chip mk" }, r.group), gradePill(r)),
          h("div", { class: "row" }, h("span", { class: "p num" }, num(r.close, r.close < 100 ? 4 : 2)), numEl(pct(r.chg1d, 2), r.chg1d),
            h("span", { class: "muted small" }, `淨值日 ${r.date}・${r.currency}`))),
        h("button", { class: "close", type: "button", onclick: closeDrawer }, "關閉")),
      h("div", { class: "panel" }, priceChart(r.symbol, { range: "2y" })),
      h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "績效評估"), scoreCell(r)),
        h("p", {}, r.verdict),
        h("div", { class: "factors" },
          part("peer", "同類排名", "近 1／3／5 年贏過同類基金的比例"), part("excess", "勝過指數", "年化報酬與對比指數的差距"),
          part("sharpe", "風險調整報酬", "Sharpe ratio"), part("drawdown", "抗跌", "三年內最大回檔")),
        h("p", { class: "chart-note" }, "四個面向各換算成 0–100 分，權重為同類排名 40%、勝過指數 20%、風險調整報酬 25%、抗跌 15%。")),
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
      h("div", { class: "panel" }, h("h3", {}, "淨值趨勢（技術面）"), forecastGrid(r.horizons),
        h("p", { class: "chart-note" }, "只看淨值走勢的趨勢與動能，與上方的績效評估是兩回事：績效看過去值不值得持有，趨勢看目前的方向。")),
    ];
  }

  function openDrawer(symbol) {
    closeDrawer();
    lastFocus = document.activeElement;
    const r = bySymbol.get(symbol), ix = S.indices.find((i) => i.symbol === symbol);
    const item = r || ix;
    if (!item) return;
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
      h("div", {}, h("div", { class: "t" }, item.name, r ? h("span", { class: "num muted small" }, r.code) : null, r ? mkChip(r.market) : null,
        r ? h("span", { class: "chip" }, r.industry) : null),
      h("div", { class: "row" }, h("span", { class: "p num" }, px(item.close)), numEl(pct(item.chg1d, 2), item.chg1d),
        h("span", { class: "muted small" }, "資料日 " + item.date))),
      h("button", { class: "close", type: "button", onclick: closeDrawer }, "關閉")));
    body.push(h("div", { class: "panel" }, priceChart(symbol, { range: "6m" })));
    if (item.horizons) body.push(h("div", { class: "panel" }, h("h3", {}, "趨勢預測"), forecastGrid(item.horizons)));
    if (r) {
      const F = [["trend", "趨勢"], ["momentum", "動能"], ["chips", "籌碼"], ["fund", "基本面"], ["buzz", "聲量"]];
      body.push(h("div", { class: "grid g2" },
        h("div", { class: "panel" }, h("h3", {}, "因子分數（−1 至 +1）"), h("div", { class: "factors" },
          F.map(([k, t]) => [h("span", {}, t), ok(r.factors[k]) ? meter(r.factors[k]) : h("span", { class: "muted small" }, "無資料"),
            h("span", { class: "num " + cls(r.factors[k]) }, ok(r.factors[k]) ? signed(r.factors[k], 2) : "–")]),
          h("span", {}, h("b", {}, "綜合")), meter(r.overall), h("b", { class: "num " + cls(r.overall) }, signed(r.overall, 2)))),
        h("div", { class: "panel" }, h("h3", {}, "多空理由"),
          h("ul", { class: "why" }, r.reasons.map((t) => h("li", { class: "pro" }, t)), r.risks.map((t) => h("li", { class: "con" }, t)),
            !r.reasons.length && !r.risks.length ? h("li", { class: "muted" }, "目前沒有明顯訊號") : null))));
      body.push(h("div", { class: "panel" }, h("h3", {}, "參考價位與操作"),
        h("div", { class: "plan" }, [["支撐", r.plan.support], ["停損", r.plan.stop], ["一月目標", r.plan.target]].map(([k, v]) =>
          h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num" }, px(v))))),
        h("p", { class: "stance", style: "margin-top:10px" }, r.plan.stance)));
      const kv = r.kind === "etf"
        ? [["類別", r.industry], ["近一月", pct(r.r20)], ["近一季", pct(r.r60)], ["今年以來", pct(r.ytd)], ["近一年", pct(r.r250)],
          ["一年最大回檔", pct(r.mdd)], ["年化波動", ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–"], ["RSI(14)", num(r.rsi, 0)],
          ["外資 5 日", ok(r.foreign5) ? signed(r.foreign5, 0) + " 張" : "–"], ["投信 5 日", ok(r.trust5) ? signed(r.trust5, 0) + " 張" : "–"]]
        : r.market === "TW"
        ? [["本益比", num(r.pe, 1)], ["股價淨值比", num(r.pb, 2)], ["殖利率", ok(r.yield) ? num(r.yield, 2) + "%" : "–"],
          ["月營收年增", pctRaw(r.rev_yoy)], ["累計營收年增", pctRaw(r.rev_cum_yoy)], ["最新季 EPS", num(r.eps, 2)],
          ["外資 5 日", ok(r.foreign5) ? signed(r.foreign5, 0) + " 張" : "–"], ["投信 5 日", ok(r.trust5) ? signed(r.trust5, 0) + " 張" : "–"],
          ["融資餘額", ok(r.margin) ? num(r.margin, 0) + " 張" : "–"], ["融資 5 日變化", pct(r.margin_chg5)],
          ["董監質押比", ok(r.insider_pledge) ? pct(r.insider_pledge).replace("+", "") : "–"], ["RSI(14)", num(r.rsi, 0)]]
        : [["本益比 (TTM)", num(r.pe, 1)], ["EPS (TTM)", num(r.eps_ttm, 2)], ["EPS 年增", pctRaw(r.eps_yoy, 0)],
          ["營收年增", pctRaw(r.rev_yoy, 0)], ["議員買 / 賣", `${r.congress_buys ?? 0} / ${r.congress_sells ?? 0}`],
          ["名人基金淨加碼", signed(r.guru_net, 0)], ["RSI(14)", num(r.rsi, 0)], ["年化波動", ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–"]];
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

  const views = {
    async overview() {
      const main = S.indices.filter((i) => i.horizons);
      return [
        h("div", { class: "tiles" }, S.indices.map((i) => h("button", { class: "tile", type: "button", onclick: () => openDrawer(i.symbol) },
          h("span", { class: "nm" }, i.name), h("span", { class: "px num" }, px(i.close)),
          h("span", { class: "chg" }, h("span", { class: "num " + cls(i.chg1d) }, "日 " + pct(i.chg1d, 2)), h("span", { class: "num " + cls(i.chg5d) }, "週 " + pct(i.chg5d))),
          spark(i.spark)))),
        h("div", { class: "grid g2" },
          h("div", { class: "panel note" }, h("h3", {}, "本週判讀"), S.narrative.map((n) => h("div", {}, h("h4", {}, n.title), h("p", {}, n.text)))),
          h("div", { class: "panel stack" }, h("h3", {}, "趨勢預測：一週／一月／一季"),
            main.map((i) => h("div", { class: "stack" }, h("div", { class: "row" }, h("b", {}, i.name), h("span", { class: "num muted small" }, px(i.close)),
              h("button", { class: "chip", type: "button", onclick: () => openDrawer(i.symbol) }, "看圖")), forecastGrid(i.horizons))),
            h("p", { class: "chart-note" }, "機率與區間由技術面＋籌碼面分數與歷史波動度推估；上漲機率刻意限制在 28%–72% 之間，避免過度自信。"))),
        aiBlock("AI 觀點", AI && AI.market),
        h("div", { class: "panel" }, h("h3", {}, "台灣加權指數"), priceChart("TWII", { range: "6m" })),
      ];
    },

    async picks() {
      const filter = { m: "ALL" };
      const grid = h("div", { class: "picks" });
      const draw = () => grid.replaceChildren(...S.picks.filter((r) => filter.m === "ALL" || r.market === filter.m).map((r, i) =>
        h("button", { class: "pick", type: "button", onclick: () => openDrawer(r.symbol) },
          h("div", { class: "pick-top" },
            h("div", { class: "pick-id" }, h("div", { class: "rk" }, `#${i + 1}・${r.industry}`),
              h("div", { class: "t" }, r.name, h("span", { class: "num muted small" }, r.code), mkChip(r.market))),
            h("div", { class: "pick-px" }, h("div", { class: "p num" }, px(r.close)), h("div", { class: "num small " + cls(r.chg5d) }, "週 " + pct(r.chg5d)))),
          h("div", { class: "scorebar" }, "綜合", meter(r.overall), h("b", { class: "num " + cls(r.overall) }, signed(r.overall, 2)), pill({ score: r.overall, label: r.label })),
          h("div", { class: "hz" }, Object.entries(r.horizons).map(([k, f]) => h("span", {}, HZ[k] + " ", pill(f)))),
          h("ul", { class: "why" }, r.reasons.slice(0, 3).map((t) => h("li", { class: "pro" }, t)), r.risks.slice(0, 2).map((t) => h("li", { class: "con" }, t))),
          h("div", { class: "plan" }, [["支撐", r.plan.support], ["停損", r.plan.stop], ["一月目標", r.plan.target]].map(([k, v]) =>
            h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num" }, px(v))))),
          h("div", { class: "stance" }, r.plan.stance))));
      const seg = h("div", { class: "seg", role: "group", "aria-label": "市場" }, [["ALL", "全部"], ["TW", "台股"], ["US", "美股"]].map(([k, t]) =>
        h("button", { type: "button", "aria-pressed": String(k === "ALL"), onclick: (e) => {
          filter.m = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); draw();
        } }, t)));
      draw();
      return [h("div", { class: "sec-head" }, head("精選 20 檔", `從 ${S.counts.tw} 檔台股與 ${S.counts.us} 檔美股中，依趨勢、動能、籌碼、基本面、聲量五類因子的綜合分數挑選，同產業最多 3 檔。點卡片看圖表與完整數據。`), seg), grid];
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
          ok(s.maint) ? h("div", { class: "stat" }, h("div", { class: "k" }, "大盤融資維持率（估算）"),
            h("div", { class: "v num" }, num(s.maint * 100, 1) + "%"),
            h("div", { class: "s" }, h("span", { class: "chip " + tone }, s.maint_status), ok(s.maint_chg5) ? ` 一週 ${signed(s.maint_chg5 * 100, 1)} 個百分點` : "")) : null,
          ok(s.maint_cushion) ? statBox("距追繳線的緩衝", num(s.maint_cushion * 100, 1) + "%", "擔保品再跌這麼多，整體就到 130%") : null,
          ok(s.maint_low20) ? statBox("近 20 日最低維持率", num(s.maint_low20 * 100, 1) + "%", "") : null,
          statBox("融資餘額", num(s.margin_bal, 0) + " 億", ok(s.margin_days) ? `約 ${num(s.margin_days, 1)} 日成交值` : "集中市場"),
          statBox("融資一週／一月", `${pct(s.margin_chg5)}／${pct(s.margin_chg20)}`, "", cls(s.margin_chg5)),
          statBox("融券餘額", num(s.short_units, 0) + " 張", ok(s.short_ratio) ? `券資比 ${num(s.short_ratio * 100, 1)}%` : "")),
        h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "走勢"), seg), holder,
          hasMaint ? h("p", { class: "chart-note" }, "融資維持率＝融資擔保品市值 ÷ 融資金額。證交所只公布各股融資張數與全市場融資金額，本站以每日收盤價估算擔保品市值，與券商實際逐戶計算的數字會有出入，適合看水位與方向。") : null),
        h("div", { class: "panel stack" }, h("h3", {}, "斷頭壓力觀察"),
          (F.margin_risk || []).length ? table(riskCols, F.margin_risk, { onRow: open })
            : h("div", { class: "empty" }, "目前沒有同時符合「融資使用率 10% 以上、近 20 日跌逾 15%、融資市值 3 億以上」的個股"),
          h("p", { class: "chart-note" }, "條件：融資使用率 10% 以上、近 20 日股價跌逾 15%、融資市值 3 億元以上。個股的融資維持率需要每戶的融資成本，交易所不公布，所以這裡用「跌多少＋融資用了多少」來找壓力大的地方。")),
        h("div", { class: "grid g2" },
          h("div", { class: "panel" }, h("h3", {}, "融資增幅前 15（5 日）"), table(cols, F.margin_up, { onRow: open }),
            h("p", { class: "chart-note" }, `比較基準日 ${F.margin_ref_date || "–"}，僅列融資餘額 2,000 張以上個股。`)),
          h("div", { class: "panel" }, h("h3", {}, "融資減幅前 15（5 日）"), table(cols, F.margin_down, { onRow: open }))),
        h("div", { class: "panel stack" }, h("h3", {}, "怎麼看這些指標"), h("div", { class: "src" }, [
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
        { key: "f", label: "申報日", cell: (r) => h("span", { class: "num muted" }, r.filed) }], C.trades.slice(0, 60));
      const q = h("input", { type: "search", id: "congress-q", placeholder: "篩選議員或代號，例如 Pelosi、NVDA", "aria-label": "篩選議員交易",
        oninput: () => { const v = q.value.trim().toLowerCase();
          tradeTbl.setRows(C.trades.filter((r) => !v || r.name.toLowerCase().includes(v) || r.ticker.toLowerCase().includes(v)).slice(0, 60)); } });
      const ACT = { new: "新建倉", add: "加碼", cut: "減碼", hold: "持平", exit: "出清" };
      return [
        head("名人・議員・董監持股", "美國國會議員依法申報的交易、知名投資人 13F 季報持股，以及台股董監事持股與質押。申報都有時間差，請當作方向參考而非即時訊號。"),
        h("div", { class: "grid g2" },
          h("div", { class: "panel" }, h("h3", {}, "議員近 90 日交易最多的股票"), table([
            { key: "t", label: "代號", left: true, cell: (r) => h("b", { class: "num" }, r.ticker) },
            { key: "b", label: "買進筆數", val: (r) => r.buys, cell: (r) => numEl(r.buys, r.buys) },
            { key: "s", label: "賣出筆數", val: (r) => r.sells, cell: (r) => numEl(r.sells, -r.sells) },
            { key: "n", label: "淨買", val: (r) => r.net, cell: (r) => numEl(signed(r.net), r.net) },
            { key: "m", label: "議員", left: true, wrap: true, cell: (r) => r.members.slice(0, 4).join("、") + (r.members.length > 4 ? ` 等 ${r.members.length} 人` : "") }],
          C.by_ticker.slice(0, 20), { onRow: (r) => bySymbol.has(r.ticker) && openDrawer(r.ticker) })),
          h("div", { class: "panel stack" }, h("div", { class: "sec-head" }, h("h3", {}, "最新議員交易申報"), q), tradeTbl,
            C.live === false ? h("p", { class: "chart-note" }, "本週來源無法連線，顯示上次成功取得的資料。") : null)),
        h("h3", {}, "名人基金 13F 持股（美國 SEC 季報）"),
        h("div", { class: "grid g2" }, (M.gurus || []).map((g) => h("div", { class: "panel" },
          h("div", { class: "guru-head" }, h("div", {}, h("b", {}, g.person), h("div", { class: "small muted" }, g.name)),
            h("div", { class: "small muted num" }, `${g.period} 季報・${num(g.total_value / 1e8, 0)} 億美元・${g.positions} 檔`)),
          table([
            { key: "n", label: "持股", left: true, cell: (r) => h("span", { class: "nm-cell" }, h("b", {}, r.ticker || r.name), r.ticker ? h("span", { class: "code" }, r.name.slice(0, 18)) : null) },
            { key: "w", label: "權重", cell: (r) => h("span", { class: "num" }, num(r.weight * 100, 1) + "%") },
            { key: "a", label: "上季動作", left: true, cell: (r) => h("span", { class: "chip " + r.action }, ACT[r.action] + (ok(r.chg) && r.action !== "hold" ? " " + pct(r.chg, 0) : "")) }],
          g.top.slice(0, 8), { onRow: (r) => bySymbol.has(r.ticker) && openDrawer(r.ticker) }),
          g.exits && g.exits.length ? h("p", { class: "small", style: "margin-top:8px" }, "上季出清：", g.exits.map((e) => h("span", { class: "chip exit", style: "margin-right:4px" }, e.ticker || e.name))) : null))),
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
        { key: "n", label: "個股", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market)) },
        { key: "i", label: "產業", left: true, cell: (r) => r.industry },
        { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        { key: "pe", label: "本益比", val: (r) => r.pe, cell: (r) => h("span", { class: "num" }, num(r.pe, 1)) },
        { key: "pb", label: "淨值比", val: (r) => r.pb, cell: (r) => h("span", { class: "num" }, num(r.pb, 2)) },
        { key: "y", label: "殖利率", val: (r) => r.yield, cell: (r) => h("span", { class: "num" }, ok(r.yield) ? num(r.yield, 1) + "%" : "–") },
        { key: "rv", label: "營收年增", tip: "台股為最新月營收，美股為最新一季", val: (r) => r.rev_yoy, cell: (r) => numEl(pctRaw(r.rev_yoy), r.rev_yoy) },
        { key: "e", label: "EPS", tip: "台股為最新一季，美股為近四季合計", val: (r) => (r.market === "US" ? r.eps_ttm : r.eps), cell: (r) => h("span", { class: "num" }, num(r.market === "US" ? r.eps_ttm : r.eps, 2)) },
        { key: "eg", label: "EPS 年增", val: (r) => r.eps_yoy, cell: (r) => numEl(pctRaw(r.eps_yoy, 0), r.eps_yoy) },
        { key: "r", label: "RSI", val: (r) => r.rsi, cell: (r) => h("span", { class: "num" }, num(r.rsi, 0)) },
        { key: "fs", label: "基本面分", val: (r) => r.factors.fund, cell: (r) => numEl(ok(r.factors.fund) ? signed(r.factors.fund, 2) : "–", r.factors.fund) },
        { key: "o", label: "綜合分", val: (r) => r.overall, cell: (r) => h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter(r.overall), numEl(signed(r.overall, 2), r.overall)) }];
      const tbl = table(cols, U, { sort: "o", onRow: (r) => openDrawer(r.symbol) });
      const apply = () => tbl.setRows(U.filter((r) => (state.m === "ALL" || r.market === state.m) &&
        (!state.q || (r.name + r.code + r.industry).toLowerCase().includes(state.q))));
      const seg = h("div", { class: "seg", role: "group", "aria-label": "市場" }, [["ALL", "全部"], ["TW", "台股"], ["US", "美股"]].map(([k, t]) =>
        h("button", { type: "button", "aria-pressed": String(k === "ALL"), onclick: (e) => {
          state.m = k; seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === e.currentTarget))); apply();
        } }, t)));
      const q = h("input", { type: "search", id: "fund-q", placeholder: "搜尋名稱、代號或產業", "aria-label": "搜尋個股",
        oninput: () => { state.q = q.value.trim().toLowerCase(); apply(); } });
      return [
        head("財報與各項指標", "觀察名單全部個股的評價、成長與技術指標。點欄位標題排序，點列看圖表。台股財報來自證交所公開資訊，美股來自 SEC XBRL 申報。"),
        h("div", { class: "panel stack" }, h("div", { class: "row" }, seg, q), tbl),
      ];
    },

    async funds() {
      const state = { g: "ALL" };
      const groups = [...new Set(FUNDS.map((r) => r.group))];
      const cols = [
        { key: "n", label: "基金", left: true, wrap: true, cell: (r) => h("span", {}, h("b", { style: "font-weight:500" }, r.name), " ", h("span", { class: "chip" }, r.currency)) },
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
        aiBlock("本週基金觀察", AI && AI.fund),
        FUNDS.length ? h("div", { class: "panel stack" }, seg, tbl,
          h("p", { class: "chart-note" }, "績效評分 0–100：同類排名 40%、年化勝過對比指數 20%、風險調整報酬（Sharpe）25%、抗跌（三年最大回檔）15%。75 以上優異、60 良好、45 普通、30 偏弱。報酬為原幣計價、含配息；點列看完整評估。"))
          : h("div", { class: "empty" }, "尚無基金資料，下次更新後會出現"),
      ];
    },

    async alerts() {
      const list = (rows, kind) => table([
        { key: "n", label: "個股", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market)) },
        { key: "c", label: "收盤", cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週漲跌", cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        { key: "1", label: "一週", left: true, cell: (r) => pill(r.horizons["1w"]) },
        { key: "2", label: "一月", left: true, cell: (r) => pill(r.horizons["1m"]) },
        { key: "y", label: kind === "up" ? "主要理由" : "主要風險", left: true, wrap: true, cell: (r) => (kind === "up" ? r.reasons : r.risks).slice(0, 2).join("；") || "–" }],
      rows, { onRow: (r) => openDrawer(r.symbol) });
      return [
        head("漲跌預警", "依一週與一月分數排序，找出短線最可能續強與最可能轉弱的個股。分數反映的是機率優勢，不是保證。"),
        h("div", { class: "panel" }, h("h3", {}, "可能上漲：短線分數最高 12 檔"), list(S.bullish, "up")),
        h("div", { class: "panel" }, h("h3", {}, "可能下跌：短線分數最低 12 檔"), list(S.bearish, "down")),
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
          { key: "n", label: "標的", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market)) },
          { key: "m", label: "提及集數", val: (r) => r.mentions, cell: (r) => h("b", { class: "num" }, r.mentions) },
          { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
          { key: "w", label: "週漲跌", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
          { key: "o", label: "綜合分", val: (r) => r.overall, cell: (r) => h("span", { class: "row", style: "justify-content:flex-end;flex-wrap:nowrap" }, meter(r.overall), numEl(signed(r.overall, 2), r.overall)) },
          { key: "1", label: "一週", left: true, cell: (r) => pill(r.horizons["1w"]) },
          { key: "2", label: "一月", left: true, cell: (r) => pill(r.horizons["1m"]) },
          { key: "v", label: "聲量對照數據", left: true, wrap: true, cell: (r) => h("div", { class: "stack", style: "gap:4px" },
            h("span", {}, r.view),
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
        { key: "n", label: "ETF", left: true, cell: (r) => h("span", {}, nameCell(r), " ", mkChip(r.market)) },
        { key: "i", label: "類別", left: true, cell: (r) => h("span", { class: "chip" + (/槓桿|反向/.test(r.industry) ? " warn" : "") }, r.industry) },
        { key: "c", label: "收盤", val: (r) => r.close, cell: (r) => h("span", { class: "num" }, px(r.close)) },
        { key: "w", label: "週", val: (r) => r.chg5d, cell: (r) => numEl(pct(r.chg5d), r.chg5d) },
        { key: "m", label: "月", val: (r) => r.r20, cell: (r) => numEl(pct(r.r20), r.r20) },
        { key: "q", label: "季", val: (r) => r.r60, cell: (r) => numEl(pct(r.r60), r.r60) },
        { key: "y", label: "今年", val: (r) => r.ytd, cell: (r) => numEl(pct(r.ytd), r.ytd) },
        { key: "1y", label: "一年", val: (r) => r.r250, cell: (r) => numEl(pct(r.r250), r.r250) },
        { key: "d", label: "一年最大回檔", val: (r) => r.mdd, cell: (r) => numEl(pct(r.mdd), r.mdd) },
        { key: "v", label: "年化波動", val: (r) => r.vol_ann, cell: (r) => h("span", { class: "num" }, ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–") },
        { key: "f", label: "外資 5 日（張）", tip: "僅台股 ETF", val: (r) => r.foreign5, cell: (r) => numEl(ok(r.foreign5) ? signed(r.foreign5, 0) : "–", r.foreign5) },
        { key: "1", label: "一週", left: true, cell: (r) => pill(r.horizons["1w"]) },
        { key: "2", label: "一月", left: true, cell: (r) => pill(r.horizons["1m"]) },
        { key: "3", label: "一季", left: true, cell: (r) => pill(r.horizons["1q"]) },
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
        aiBlock("本週 ETF 觀察", AI && AI.etf),
        E.length ? h("div", { class: "panel stack" }, h("div", { class: "row" }, seg, sel), tbl,
          h("p", { class: "chart-note" }, "槓桿型與反向型 ETF 每日重新平衡，長期持有會有耗損；反向型「偏多」代表的是看空大盤。點列看圖表與參考價位。"))
          : h("div", { class: "empty" }, "尚無 ETF 資料，下次更新後會出現"),
      ];
    },

    async method() {
      const tools = await load("tools").catch(() => []);
      const bt = S.backtest || {};
      const W = S.weights || {};
      const G = { tech: "技術面（趨勢＋動能）", chips: "籌碼面", fund: "基本面", buzz: "Podcast 聲量" };
      return [
        head("方法與資料來源", "所有分數都由公開資料以固定規則計算，沒有人工挑選。以下說明計算方式、回測結果與每個來源本週的取得狀態。"),
        h("div", { class: "grid g2" },
          h("div", { class: "panel stack" }, h("h3", {}, "因子權重"),
            table([{ key: "g", label: "因子", left: true, cell: (r) => G[r] },
              ...Object.keys(HZ).map((k) => ({ key: k, label: HZ[k], cell: (r) => h("span", { class: "num" }, nf(0).format(((W[k] || {})[r] || 0) * 100) + "%") }))],
            Object.keys(G)),
            h("p", { class: "small" }, "每個因子先壓縮到 −1 至 +1：趨勢看均線排列與季線斜率；動能看波動度調整後的報酬、MACD、RSI；台股籌碼看外資／投信買賣超占成交量比重、連續天數與融資變化；美股籌碼看議員交易與 13F 增減；基本面看營收與 EPS 成長、本益比、殖利率。缺少的因子會自動剔除並重新分配權重。"),
            h("p", { class: "small" }, "預測區間＝現價 ×（1 ＋ 分數 × 0.6σ ± σ），σ 為近 60 日波動度乘以天數平方根。綜合分數＝一週 30%＋一月 40%＋一季 30%。")),
          h("div", { class: "panel stack" }, h("h3", {}, "技術面分數回測"),
            Object.values(bt).some(Boolean) ? table([
              { key: "h", label: "期間", left: true, cell: (k) => HZ[k] },
              { key: "ic", label: "IC", tip: "分數排名與後續報酬排名的相關係數", cell: (k) => h("span", { class: "num" }, bt[k] ? signed(bt[k].ic, 3) : "–") },
              { key: "t", label: "高分組報酬", cell: (k) => numEl(bt[k] ? pct(bt[k].top_ret, 2) : "–", bt[k] && bt[k].top_ret) },
              { key: "a", label: "全體平均", cell: (k) => numEl(bt[k] ? pct(bt[k].avg_ret, 2) : "–", bt[k] && bt[k].avg_ret) },
              { key: "b", label: "低分組報酬", cell: (k) => numEl(bt[k] ? pct(bt[k].bottom_ret, 2) : "–", bt[k] && bt[k].bottom_ret) },
              { key: "w", label: "高分組勝過平均", cell: (k) => h("span", { class: "num" }, bt[k] ? nf(0).format(bt[k].top_beats_avg * 100) + "%" : "–") }],
            Object.keys(HZ)) : h("div", { class: "empty" }, "樣本不足，尚無回測結果"),
            h("p", { class: "small" }, "做法：過去約三年每 5 個交易日，用當時能取得的價格資料計算技術面分數，比較分數最高與最低各 20% 個股在之後一週／一月／一季的平均報酬。"),
            h("p", { class: "small muted" }, "限制：樣本是「現在」的熱門股，存在倖存者偏差；籌碼與基本面因子沒有足夠歷史資料，未納入回測；過去績效不代表未來。"))),
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
  async function route() {
    const name = views[location.hash.slice(1)] ? location.hash.slice(1) : "overview";
    document.querySelectorAll("#tabs a").forEach((a) => {
      const on = a.getAttribute("href") === "#" + name;
      if (on) { a.setAttribute("aria-current", "page"); a.scrollIntoView({ block: "nearest", inline: "center" }); }
      else a.removeAttribute("aria-current");
    });
    closeDrawer();
    charts.clear();
    try {
      const nodes = await views[name]();
      app.replaceChildren(...nodes.filter(Boolean));
    } catch (e) {
      app.replaceChildren(h("div", { class: "empty" }, "這個分頁的資料載入失敗：" + e.message));
    }
    window.scrollTo(0, 0);
  }

  async function boot() {
    try {
      [S, U] = await Promise.all([load("summary"), load("universe")]);
    } catch (e) {
      app.replaceChildren(h("div", { class: "empty" }, "找不到資料檔。請先執行 python -m pipeline.build，或等待每週三的自動更新。（" + e.message + "）"));
      return;
    }
    [E, FUNDS, AI] = await Promise.all([load("etf").catch(() => []), load("funds").catch(() => []), load("ai").catch(() => null)]);
    FUNDS.forEach((r) => bySymbol.set(r.symbol, r));
    U.forEach((r) => bySymbol.set(r.symbol, r));
    E.forEach((r) => bySymbol.set(r.symbol, r));
    [...S.picks, ...S.bullish, ...S.bearish].forEach((r) => bySymbol.set(r.symbol, r));
    document.getElementById("stamp").replaceChildren(
      h("span", {}, "資料日 ", h("b", { class: "num" }, S.data_date)),
      h("span", {}, "更新於 ", h("b", { class: "num" }, S.generated_at.slice(0, 16).replace("T", " "))),
      h("span", {}, "下次更新 ", h("b", { class: "num" }, S.next_update + "（三）")));
    window.addEventListener("hashchange", route);
    route();
  }

  const retheme = () => charts.forEach((fn) => fn());
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", retheme);
  new MutationObserver(retheme).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  boot();
})();
