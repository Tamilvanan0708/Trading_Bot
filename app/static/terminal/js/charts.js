/* XAU AI — lightweight canvas chart engine (no external deps)
   Professional candlestick rendering with:
   - full-width, responsive canvas sizing (actual container dimensions)
   - efficient incremental updates (only the changed region is redrawn)
   - natural candle spacing, price/time axes, volume bars, hover crosshair
*/
"use strict";

const PAD_L = 8;

const Charts = {
  /* ---- canvas sizing: reads the REAL layout box and keeps DPR-crisp ---- */
  _fit(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(rect.width, 10), h = Math.max(rect.height, 10);
    const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
    if (canvas.width !== bw || canvas.height !== bh) {
      canvas.width = bw;
      canvas.height = bh;
    }
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  },

  /* ---- donut (confluence / confidence) ---- */
  donut(canvas, value, max = 100, color = "#4d9fff", label = "") {
    if (!canvas) return;
    const { ctx, w, h } = Charts._fit(canvas);
    const cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 4;
    const pct = Math.max(0, Math.min(1, (value || 0) / (max || 1)));
    ctx.clearRect(0, 0, w, h);
    // track
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = "rgba(255,255,255,0.06)"; ctx.lineWidth = 8; ctx.stroke();
    // value arc
    const start = -Math.PI / 2;
    ctx.beginPath(); ctx.arc(cx, cy, r, start, start + pct * Math.PI * 2);
    ctx.strokeStyle = color; ctx.lineWidth = 8; ctx.lineCap = "round"; ctx.stroke();
    // center text handled by caller overlay (donut-value)
  },

  /* ---- sparkline ---- */
  sparkline(canvas, values, color = "#4d9fff") {
    if (!canvas || !values || values.length < 2) { if (canvas) { const { ctx, w, h } = Charts._fit(canvas); ctx.clearRect(0, 0, w, h); } return; }
    const { ctx, w, h } = Charts._fit(canvas);
    const min = Math.min(...values), max = Math.max(...values), span = (max - min) || 1;
    const n = values.length;
    ctx.clearRect(0, 0, w, h);
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = (i / (n - 1)) * w;
      const y = h - 3 - ((v - min) / span) * (h - 6);
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
    // fill
    ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
    ctx.fillStyle = color; ctx.globalAlpha = 0.08; ctx.fill(); ctx.globalAlpha = 1;
  },

  /* ---- line/area chart (equity, drawdown) ---- */
  lineChart(canvas, labels, series, opts = {}) {
    if (!canvas) return;
    const { ctx, w, h } = Charts._fit(canvas);
    ctx.clearRect(0, 0, w, h);
    if (!series || !series.length || !series[0].values || series[0].values.length < 2) {
      ctx.fillStyle = "rgba(255,255,255,0.25)"; ctx.font = "11px Inter"; ctx.textAlign = "center";
      ctx.fillText("No data", w / 2, h / 2);
      return;
    }
    const padL = opts.padL ?? 8, padR = opts.padR ?? 8, padT = opts.padT ?? 8, padB = opts.padB ?? 14;
    const innerW = w - padL - padR, innerH = h - padT - padB;
    let min = Infinity, max = -Infinity;
    series.forEach(s => s.values.forEach(v => { min = Math.min(min, v); max = Math.max(max, v); }));
    const span = (max - min) || 1;
    const n = labels.length;
    const X = (i) => padL + (i / Math.max(1, n - 1)) * innerW;
    const Y = (v) => padT + innerH - ((v - min) / span) * innerH;

    // grid lines
    ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
      const y = padT + (innerH / 4) * g;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    }
    // series
    series.forEach(s => {
      ctx.beginPath();
      s.values.forEach((v, i) => { i === 0 ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v)); });
      ctx.strokeStyle = s.color || "#4d9fff"; ctx.lineWidth = 1.6; ctx.stroke();
      if (s.fill !== false) {
        ctx.lineTo(X(n - 1), h - padB); ctx.lineTo(X(0), h - padB); ctx.closePath();
        ctx.fillStyle = s.color || "#4d9fff"; ctx.globalAlpha = 0.08; ctx.fill(); ctx.globalAlpha = 1;
      }
    });
    // x labels (a few)
    ctx.fillStyle = "rgba(255,255,255,0.35)"; ctx.font = "9px Inter"; ctx.textAlign = "center";
    const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) {
      ctx.fillText(String(labels[i] || ""), X(i), h - 3);
    }
  },

  /* =============================================================
     Candlestick chart — professional, full-width, incremental.
     opts:
       window     : number of candles to show (default: all data)
       volume     : boolean, render volume bars at the bottom
       levels     : [{ price, label, color }]
       lastPrice  : number, draw a dashed last-price line
       showLatest : boolean, accent the newest candle
       noMessage  : string, message when there is no data
   ============================================================= */
  candles(canvas, data, opts = {}) {
    const lay = Charts._layout(canvas, data, opts);
    Charts._drawFull(lay, data, opts);
    // BOS/CHOCH markers (from market structure data, if provided)
    const { ctx, px0, bw: bW, Y: yVal } = lay;
    const candles = data;
    if (opts.markers && Array.isArray(opts.markers)) {
      for (const m of opts.markers) {
        const idx = candles.findIndex(c => c.timestamp === m.timestamp);
        if (idx === -1) continue;
        const x = px0 + idx * bW;
        const y = yVal(m.price);
        ctx.beginPath();
        ctx.strokeStyle = m.type === 'BOS' ? '#00ff88' : '#ffaa00';
        ctx.fillStyle = m.type === 'BOS' ? '#00ff88' : '#ffaa00';
        const size = 6;
        if (m.direction === 'UP') {
          ctx.moveTo(x - size, y + size); ctx.lineTo(x, y); ctx.lineTo(x + size, y + size);
        } else {
          ctx.moveTo(x - size, y - size); ctx.lineTo(x, y); ctx.lineTo(x + size, y - size);
        }
        ctx.fill();
        ctx.font = '9px monospace';
        ctx.fillText(m.type, x + size + 2, y + 3);
      }
    }
    Charts._storeLast(canvas, lay, data, opts);
    if (typeof canvas.onmousemove !== "function") Charts._bindHover(canvas);
  },

  /* ---- incremental live update: redraw only the changed region ----
     When the visible window and price scale are unchanged (i.e. only the
     forming/latest candle changed on a live tick), only the right-most candle
     band is repainted — historical candles stay stable and no full-chart
     flicker occurs.  A full redraw happens when the window or scale changes. */
  candlesLive(canvas, data, opts = {}) {
    const lay = Charts._layout(canvas, data, opts);
    const prev = canvas._chartLast;
    const scaleUnchanged = prev && prev.count === lay.count
      && prev.windowCount === lay.windowCount
      && prev.start === lay.start
      && prev.slot === lay.slot
      && prev.min === lay.min
      && prev.max === lay.max;
    if (scaleUnchanged && lay.count >= 1) {
      Charts._drawBand(lay, data, opts, Math.max(0, lay.start + lay.count - 2));
    } else {
      Charts._drawFull(lay, data, opts);
    }
    Charts._storeLast(canvas, lay, data, opts);
  },

  /* ---- shared layout computation (also used by candlesLive) ---- */
  _layout(canvas, data, opts = {}) {
    const { ctx, w, h } = Charts._fit(canvas);
    const n = data && data.length ? data.length : 0;

    if (n < 1) {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "rgba(255,255,255,0.25)"; ctx.font = "12px Inter"; ctx.textAlign = "center";
      ctx.fillText(opts.noMessage || "Waiting for candle data…", w / 2, h / 2);
      return { ctx, w, h, n: 0, empty: true };
    }

    const showVolume = opts.volume === true && data.some(c => (c.volume || 0) > 0);
    const padR = opts.padR ?? 46;
    const padT = opts.padT ?? 10;
    const padB = opts.padB ?? 20;
    const volH = showVolume ? Math.max(40, h * 0.14) : 0;
    const innerW = w - PAD_L - padR;
    const innerH = h - padT - padB - volH;

    let count = n;
    if (opts.window && opts.window > 0) count = Math.min(n, Math.floor(opts.window));
    const maxFit = Math.max(2, Math.floor(innerW / 2.5));
    count = Math.min(count, Math.max(1, Math.min(n, maxFit)));
    const start = n - count;
    const slot = innerW / count;
    const bw = Math.max(2, Math.min(13, slot * 0.72));

    const X = (i) => PAD_L + (i - start + 0.5) * slot;
    const px0 = PAD_L;
    const px1 = w - padR;

    let min = Infinity, max = -Infinity;
    for (let i = start; i < n; i++) {
      const c = data[i];
      if (c.low < min) min = c.low;
      if (c.high > max) max = c.high;
    }
    if (opts.levels) opts.levels.forEach(l => { min = Math.min(min, l.price); max = Math.max(max, l.price); });
    if (opts.lastPrice != null) { min = Math.min(min, opts.lastPrice); max = Math.max(max, opts.lastPrice); }
    const span = (max - min) || 1;
    const padSpan = span * 0.06;
    min -= padSpan; max += padSpan;
    const pSpan = (max - min) || 1;
    const Y = (v) => padT + innerH - ((v - min) / pSpan) * innerH;

    return { ctx, w, h, n, count, start, slot, bw, X, Y, px0, px1, min, max, span, pSpan,
             padR, padT, padB, innerW, innerH, volH, showVolume, gridRows: 5, empty: false };
  },

  _storeLast(canvas, lay, data, opts) {
    canvas._chartLast = {
      count: lay.n, windowCount: lay.count, start: lay.start, slot: lay.slot,
      min: lay.min, max: lay.max, lastPrice: opts.lastPrice, data: data,
    };
  },

  _drawFull(lay, data, opts) {
    if (!lay || lay.empty) return;
    const { ctx, w, h, padT, padB } = lay;
    ctx.clearRect(0, 0, w, h);
    Charts._drawGrid(lay);
    Charts._drawVolume(lay, data);
    Charts._drawLevels(lay, opts);
    Charts._drawCandles(lay, data);
    Charts._drawLatestAccent(lay, data, opts);
    Charts._drawLastPrice(lay, opts);
    Charts._drawPriceLabels(lay);
    Charts._drawTimeLabels(lay, data, opts);
  },

  /* redraw only the right-hand band starting at candle iStart */
  _drawBand(lay, data, opts, iStart) {
    if (!lay || lay.empty) return;
    const { ctx, w, h, X, bw, px0, px1 } = lay;
    const xStart = X(iStart) - bw;
    ctx.clearRect(xStart, 0, w - xStart, h);
    // redraw grid lines only within the band
    ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
    for (let g = 0; g <= lay.gridRows; g++) {
      const y = lay.padT + (lay.innerH / lay.gridRows) * g;
      ctx.beginPath(); ctx.moveTo(Math.max(px0, xStart), y); ctx.lineTo(px1, y); ctx.stroke();
    }
    // redraw the band candles
    for (let i = iStart; i < data.length; i++) {
      const c = data[i];
      const up = c.close >= c.open;
      const color = up ? "#22c55e" : "#ef4444";
      const x = X(i);
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, lay.Y(c.high)); ctx.lineTo(x, lay.Y(c.low)); ctx.stroke();
      const yO = lay.Y(c.open), yC = lay.Y(c.close);
      const top = Math.min(yO, yC), bodyH = Math.max(1, Math.abs(yC - yO));
      ctx.fillStyle = color;
      ctx.fillRect(x - bw / 2, top, bw, bodyH);
    }
    Charts._drawLastPrice(lay, opts);
    Charts._drawPriceLabels(lay);
  },

  _drawGrid(lay) {
    const { ctx, px0, px1 } = lay;
    ctx.strokeStyle = "rgba(255,255,255,0.05)"; ctx.lineWidth = 1;
    for (let g = 0; g <= lay.gridRows; g++) {
      const y = lay.padT + (lay.innerH / lay.gridRows) * g;
      ctx.beginPath(); ctx.moveTo(px0, y); ctx.lineTo(px1, y); ctx.stroke();
    }
  },

  _drawVolume(lay, data) {
    if (!lay.showVolume) return;
    const { ctx, X, bw } = lay;
    let vMax = 0;
    for (let i = lay.start; i < lay.n; i++) vMax = Math.max(vMax, data[i].volume || 0);
    const vTop = lay.padT + lay.innerH;
    for (let i = lay.start; i < lay.n; i++) {
      const c = data[i];
      const vh = vMax > 0 ? ((c.volume || 0) / vMax) * lay.volH : 0;
      ctx.fillStyle = (c.close >= c.open) ? "rgba(34,197,94,0.32)" : "rgba(239,68,68,0.32)";
      ctx.fillRect(X(i) - bw / 2, vTop + (lay.volH - vh), bw, vh);
    }
  },

  _drawLevels(lay, opts) {
    if (!opts.levels) return;
    const { ctx, px0, px1 } = lay;
    opts.levels.forEach(l => {
      const y = lay.Y(l.price);
      if (y < lay.padT - 4 || y > lay.h - lay.padB + 4) return;
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = l.color || "#f59e0b"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px0, y); ctx.lineTo(px1, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = l.color || "#f59e0b"; ctx.font = "9px Inter"; ctx.textAlign = "left";
      ctx.fillText(`${l.label}: ${Number(l.price).toFixed(1)}`, px0 + 3, y - 2);
    });
  },

  _drawCandles(lay, data) {
    const { ctx, X, bw } = lay;
    for (let i = lay.start; i < lay.n; i++) {
      const c = data[i];
      const up = c.close >= c.open;
      const color = up ? "#22c55e" : "#ef4444";
      const x = X(i);
      ctx.strokeStyle = color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, lay.Y(c.high)); ctx.lineTo(x, lay.Y(c.low)); ctx.stroke();
      const yO = lay.Y(c.open), yC = lay.Y(c.close);
      const top = Math.min(yO, yC), bodyH = Math.max(1, Math.abs(yC - yO));
      ctx.fillStyle = color;
      ctx.fillRect(x - bw / 2, top, bw, bodyH);
    }
  },

  _drawLatestAccent(lay, data, opts) {
    if (opts.showLatest === false || lay.n <= 0) return;
    const { ctx, X, slot } = lay;
    const lc = data[lay.n - 1];
    const x = X(lay.n - 1);
    const up = lc && lc.close >= lc.open;
    ctx.strokeStyle = up ? "#34d399" : "#f87171";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(x - slot / 2, lay.padT);
    ctx.lineTo(x - slot / 2, lay.h - lay.padB - lay.volH);
    ctx.moveTo(x + slot / 2, lay.padT);
    ctx.lineTo(x + slot / 2, lay.h - lay.padB - lay.volH);
    ctx.stroke();
  },

  _drawLastPrice(lay, opts) {
    if (opts.lastPrice == null) return;
    const { ctx, px0, px1, padR } = lay;
    const y = lay.Y(opts.lastPrice);
    if (y >= lay.padT - 4 && y <= lay.h - lay.padB + 4) {
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = "rgba(255,255,255,0.35)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px0, y); ctx.lineTo(px1, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(20,27,38,0.85)";
      ctx.fillRect(px1, y - 8, padR, 16);
      ctx.fillStyle = opts.lastColor || "#d7dee8";
      ctx.font = "10px Inter"; ctx.textAlign = "right";
      ctx.fillText(Number(opts.lastPrice).toFixed(2), px1 + padR - 6, y + 3);
    }
  },

  _drawPriceLabels(lay) {
    const { ctx, w } = lay;
    ctx.fillStyle = "rgba(255,255,255,0.4)"; ctx.font = "9px Inter"; ctx.textAlign = "right";
    for (let g = 0; g <= lay.gridRows; g++) {
      const v = lay.min + (lay.pSpan / lay.gridRows) * g;
      const y = lay.padT + (lay.innerH / lay.gridRows) * g;
      ctx.fillText(Number(v).toFixed(1), w - 4, y + 8);
    }
  },

  _drawTimeLabels(lay, data, opts) {
    const { ctx, X, px1 } = lay;
    const labelCount = Math.min(6, Math.max(2, Math.floor(px1 / 90)));
    const step = Math.max(1, Math.floor(lay.count / labelCount));
    ctx.fillStyle = "rgba(255,255,255,0.4)"; ctx.font = "9px Inter"; ctx.textAlign = "center";
    for (let i = lay.start; i < lay.n; i += step) {
      const ts = data[i].timestamp;
      let label = "";
      try { label = Charts._tsLabel(ts); } catch (_) { label = String(ts || "").slice(5, 16); }
      ctx.fillText(label, X(i), lay.h - 7);
    }
  },

  /* ---- store a lightweight snapshot so partial redraw can be cheap ---- */
  _bindHover(canvas) {
    canvas.onmousemove = (e) => {
      const st = canvas._chartLast;
      if (!st || !st.data || !st.data.length) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const padLpx = PAD_L;
      const idx = Math.floor((x - padLpx) / st.slot) + st.start;
      if (idx < st.start || idx >= st.count + st.start) return;
      const c = st.data[idx];
      if (!c) return;
      const y = e.clientY - rect.top;
      const { ctx, w, h } = Charts._fit(canvas);
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(20,27,38,0.92)";
      const label = `O ${c.open.toFixed(2)}  H ${c.high.toFixed(2)}  L ${c.low.toFixed(2)}  C ${c.close.toFixed(2)}  ${Charts._tsLabel(c.timestamp)}`;
      ctx.font = "10px Inter";
      const tw = ctx.measureText(label).width + 16;
      const bx = Math.max(0, Math.min(w - tw, x - tw / 2));
      ctx.fillRect(bx, y > 26 ? y - 24 : 4, tw, 20);
      ctx.fillStyle = "#d7dee8"; ctx.textAlign = "left";
      ctx.fillText(label, bx + 8, (y > 26 ? y - 24 : 4) + 13);
    };
    canvas.onmouseleave = () => {
      const st = canvas._chartLast;
      if (!st) return;
      // re-render to clear the crosshair
      Charts.candles(canvas, st.data, { window: st.windowCount, lastPrice: st.lastPrice, showLatest: true });
    };
  },

  _tsLabel(ts) {
    if (!ts) return "—";
    let s = String(ts).trim();
    if (!s.endsWith("Z") && !s.includes("+") && !/[0-9]-[0-9]{2}:/.test(s)) {
      s = s.replace(" ", "T") + "Z";
    }
    const d = new Date(s);
    if (isNaN(d.getTime())) return String(ts).slice(5, 16);
    return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour12: true, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) + " IST";
  },
};
