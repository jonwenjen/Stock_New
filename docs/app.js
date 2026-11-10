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
  const fileOf = (sym) => sym.replace(/[\^=.]/g, "_");
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
      if (state.data) draw();
    };
    const show = (r) => {
      if (!r) { readout.replaceChildren(); return; }
      const t = typeof r[0] === "number" ? new Date(r[0] * 1000).toISOString().slice(0, 16).replace("T", " ") : r[0];
      const prev = rowsByTime.get("prev:" + r[0]);
      const chg = prev ? r[4] / prev - 1 : null;
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
      [candle, vol, ...mas].forEach((s) => s.setData([]));
      candle.setData(rows.map((r) => ({ time: r[0], open: r[1], high: r[2], low: r[3], close: r[4] })));
      vol.setData(rows.map((r) => ({ time: r[0], value: r[5], color: (r[4] >= r[1] ? up : down) + "66" })));
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
      note.textContent = `${unit}K・${rows.length} 根` +
        (fallback ? "・此標的無分時資料，改以最近 5 根日 K 呈現" : "") +
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
  function flowChart(history, series, label, unit) {
    const rows = history.filter((r) => ok(r[series]));
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
    const isLine = series === "margin_bal" || series === "short_units";
    const whole = { type: "price", precision: 0, minMove: 1 };
    const s1 = isLine ? c1.addLineSeries({ lineWidth: 2, priceLineVisible: false, priceFormat: whole })
      : c1.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, priceFormat: whole });
    const s2 = c2.addLineSeries({ lineWidth: 2, priceLineVisible: false, priceFormat: whole });
    const byDate = new Map(rows.map((r) => [r.date, r]));
    const show = (r) => {
      if (!r) return;
      readout.replaceChildren(...[h("span", {}, r.date), h("span", {}, label + " ", h("b", { class: isLine ? "" : cls(r[series]) },
        (isLine ? num(r[series], 0) : signed(r[series], 1)) + " " + unit)),
        ok(r.twii) ? h("span", {}, "加權 ", h("b", {}, num(r.twii, 0))) : null].filter(Boolean));
    };
    const applyTheme = () => { c1.applyOptions(themeOpts()); c2.applyOptions(themeOpts()); draw(); };
    function draw() {
      seg.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(+b.dataset.n === state.n)));
      const win = rows.slice(-state.n), up = css("--up"), down = css("--down");
      s1.setData(win.map((r) => (isLine ? { time: r.date, value: r[series] } : { time: r.date, value: r[series], color: r[series] >= 0 ? up : down })));
      if (isLine) s1.applyOptions({ color: css("--ma1") });
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
  function openDrawer(symbol) {
    closeDrawer();
    lastFocus = document.activeElement;
    const r = bySymbol.get(symbol), ix = S.indices.find((i) => i.symbol === symbol);
    const item = r || ix;
    if (!item) return;
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
      const kv = r.market === "TW"
        ? [["本益比", num(r.pe, 1)], ["股價淨值比", num(r.pb, 2)], ["殖利率", ok(r.yield) ? num(r.yield, 2) + "%" : "–"],
          ["月營收年增", pctRaw(r.rev_yoy)], ["累計營收年增", pctRaw(r.rev_cum_yoy)], ["最新季 EPS", num(r.eps, 2)],
          ["外資 5 日", ok(r.foreign5) ? signed(r.foreign5, 0) + " 張" : "–"], ["投信 5 日", ok(r.trust5) ? signed(r.trust5, 0) + " 張" : "–"],
          ["融資餘額", ok(r.margin) ? num(r.margin, 0) + " 張" : "–"], ["融資 5 日變化", pct(r.margin_chg5)],
          ["董監質押比", ok(r.insider_pledge) ? pct(r.insider_pledge).replace("+", "") : "–"], ["RSI(14)", num(r.rsi, 0)]]
        : [["本益比 (TTM)", num(r.pe, 1)], ["EPS (TTM)", num(r.eps_ttm, 2)], ["EPS 年增", pctRaw(r.eps_yoy, 0)],
          ["營收年增", pctRaw(r.rev_yoy, 0)], ["議員買 / 賣", `${r.congress_buys ?? 0} / ${r.congress_sells ?? 0}`],
          ["名人基金淨加碼", signed(r.guru_net, 0)], ["RSI(14)", num(r.rsi, 0)], ["年化波動", ok(r.vol_ann) ? num(r.vol_ann * 100, 0) + "%" : "–"]];
      body.push(h("div", { class: "panel" }, h("h3", {}, "關鍵數據"), h("div", { class: "kv" },
        kv.map(([k, v]) => h("div", {}, h("div", { class: "k" }, k), h("div", { class: "v num" }, v))))));
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
  const statBox = (k, v, s, c) => h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v num " + (c || "") }, v), s ? h("div", { class: "s" }, s) : null);
  const mdLite = (text) => text.split(/\n+/).filter((l) => l.trim()).map((l) =>
    (l.startsWith("#") ? h("h4", {}, l.replace(/^#+\s*/, "")) : h("p", {}, l.replace(/\*\*/g, ""))));

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
        S.ai_commentary ? h("div", { class: "panel" }, h("h3", {}, "AI 觀點（Claude 依本週數據撰寫）"), h("div", { class: "ai note" }, mdLite(S.ai_commentary))) : null,
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
      const cols = [
        { key: "n", label: "個股", left: true, cell: (r) => nameCell(r) },
        { key: "m", label: "融資餘額（張）", val: (r) => r.margin, cell: (r) => h("span", { class: "num" }, num(r.margin, 0)) },
        { key: "c", label: "5 日變化", val: (r) => r.chg, cell: (r) => numEl(pct(r.chg), r.chg) },
        { key: "p", label: "股價 5 日", val: (r) => r.price_chg5, cell: (r) => numEl(pct(r.price_chg5), r.price_chg5) },
        { key: "f", label: "訊號", left: true, cell: (r) => (r.chg > 0.05 && r.price_chg5 < -0.02 ? h("span", { class: "chip warn" }, "資增價跌")
          : r.chg < -0.03 && r.price_chg5 > 0.01 ? h("span", { class: "chip" }, "資減價漲") : "") }];
      return [
        head("融資融券", "融資是散戶槓桿的溫度計。融資增而股價跌，代表散戶接刀、籌碼凌亂；融資減而股價漲，代表籌碼沉澱。"),
        h("div", { class: "stats" },
          statBox("融資餘額", num(s.margin_bal, 0) + " 億", "集中市場"),
          statBox("近一週變化", pct(s.margin_chg5), "", cls(s.margin_chg5)),
          statBox("近一月變化", pct(s.margin_chg20), "", cls(s.margin_chg20)),
          statBox("融券餘額", num(s.short_units, 0) + " 張", "")),
        h("div", { class: "panel" }, h("h3", {}, "融資餘額走勢"), flowChart(F.history, "margin_bal", "融資餘額", "億元")),
        h("div", { class: "grid g2" },
          h("div", { class: "panel" }, h("h3", {}, "融資增幅前 15（5 日）"), table(cols, F.margin_up, { onRow: open }),
            h("p", { class: "chart-note" }, `比較基準日 ${F.margin_ref_date || "–"}，僅列融資餘額 2,000 張以上個股。`)),
          h("div", { class: "panel" }, h("h3", {}, "融資減幅前 15（5 日）"), table(cols, F.margin_down, { onRow: open }))),
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
      const rank = Object.entries(P.mentions || {}).sort((a, b) => b[1] - a[1]).slice(0, 24);
      const nm = (s) => (bySymbol.get(s) || {}).name || (P.names || {})[s] || s;
      return [
        head("Podcast 熱議", "近兩週知名財經 Podcast 的節目標題與簡介中提到的個股。這是聲量指標：被討論不等於看多，但常代表市場關注度升溫。"),
        h("div", { class: "panel" }, h("h3", {}, "被提及次數排行"), rank.length
          ? h("div", { class: "row" }, rank.map(([s, n]) => h("button", { class: "chip", type: "button", onclick: () => bySymbol.has(s) && openDrawer(s) }, `${nm(s)} × ${n}`)))
          : h("div", { class: "empty" }, "近兩週節目簡介中沒有比對到觀察名單內的個股")),
        h("div", { class: "grid g2" }, (P.shows || []).filter((s) => s.episodes.length).map((s) => h("div", { class: "panel" },
          h("h3", {}, s.name), s.episodes.map((e) => h("div", { class: "ep" },
            h("div", { class: "ep-t" }, e.link ? h("a", { href: e.link, target: "_blank", rel: "noopener noreferrer" }, e.title) : e.title),
            h("div", { class: "small muted num" }, e.date),
            e.summary ? h("div", { class: "small" }, e.summary) : null,
            e.mentions.length ? h("div", { class: "row" }, e.mentions.map((m) => h("button", { class: "chip mk", type: "button", onclick: () => bySymbol.has(m) && openDrawer(m) }, nm(m)))) : null))))),
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
    U.forEach((r) => bySymbol.set(r.symbol, r));
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
