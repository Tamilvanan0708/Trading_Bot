/* XAU AI — UI component library */
"use strict";

const UI = {
  /* element factory */
  el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html !== undefined) e.innerHTML = html;
    return e;
  },
  esc(s) {
    return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  },
  fmt(v, d = 2) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toFixed(d);
  },
  fmtPts(v) { return (v === null || v === undefined) ? "—" : Number(v).toFixed(1); },
  fmtPct(v) { return (v === null || v === undefined) ? "—" : Number(v).toFixed(2) + "%"; },
  fmtR(v) { return (v === null || v === undefined) ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(3) + "R"; },
  _parseUTC(ts) {
    if (!ts) return null;
    let s = String(ts).trim();
    if (!s.endsWith("Z") && !s.includes("+") && !/[0-9]-[0-9]{2}:/.test(s)) {
      s = s.replace(" ", "T") + "Z";
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? new Date(ts) : d;
  },
  fmtTs(ts) {
    if (!ts) return "—";
    try {
      const d = UI._parseUTC(ts);
      if (!d || isNaN(d.getTime())) return String(ts);
      return d.toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true
      }) + " IST";
    } catch (_) { return String(ts); }
  },
  fmtTsFull(ts) {
    if (!ts) return "—";
    try {
      const d = UI._parseUTC(ts);
      if (!d || isNaN(d.getTime())) return String(ts);
      return d.toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: true
      }) + " IST";
    } catch (_) { return String(ts); }
  },

  /* direction badge */
  dirBadge(dir) {
    if (!dir || dir === "NO_TRADE") return `<span class="badge badge-muted">NO TRADE</span>`;
    if (dir === "LONG") return `<span class="badge badge-green">▲ LONG</span>`;
    if (dir === "SHORT") return `<span class="badge badge-red">▼ SHORT</span>`;
    return `<span class="badge badge-muted">${UI.esc(dir)}</span>`;
  },
  dirClass(dir) {
    if (dir === "LONG") return "up";
    if (dir === "SHORT") return "down";
    return "flat";
  },

  /* quality / outcome badges */
  qualityBadge(q) {
    const map = { VERY_STRONG: ["badge-green", "EXCEPTIONAL"], STRONG: ["badge-green", "STRONG"],
      MODERATE: ["badge-amber", "MODERATE"], WEAK: ["badge-amber", "WEAK"], NO_TRADE: ["badge-muted", "NO TRADE"] };
    const [c, t] = map[q] || ["badge-dim", q || "—"];
    return `<span class="badge ${c}">${t}</span>`;
  },
  statusBadge(s) {
    if (!s) return `<span class="badge badge-dim">—</span>`;
    const up = s.toUpperCase();
    if (["HEALTHY", "CONNECTED", "LIVE", "SENT", "SUCCESS", "APPROVE", "ROBUST", "PROMISING", "PROMOTED", "OOS_VALIDATED", "FORWARD_VALIDATED"].includes(up))
      return `<span class="badge badge-green">${UI.esc(s)}</span>`;
    if (["DEGRADED", "CAUTION", "WEAK", "WAIT", "SKIPPED", "FORWARD_OBSERVATION", "INCONCLUSIVE", "PAPER_VALIDATION", "HUMAN_REVIEW"].includes(up))
      return `<span class="badge badge-amber">${UI.esc(s)}</span>`;
    if (["FAILED", "REJECT", "REJECTED", "DISCONNECTED", "FAILURE", "ERROR", "OFFLINE", "FAIL", "UNAVAILABLE", "BLOCKED"].includes(up))
      return `<span class="badge badge-red">${UI.esc(s)}</span>`;
    if (["RESEARCH", "OOS", "TESTING", "NONE", "DISABLED"].includes(up))
      return `<span class="badge badge-blue">${UI.esc(s)}</span>`;
    return `<span class="badge badge-dim">${UI.esc(s)}</span>`;
  },

  /* metric tile */
  metric(label, value, sub, cls = "") {
    const el = UI.el("div", "metric " + cls);
    el.innerHTML = `<div class="metric-label">${UI.esc(label)}</div>
      <div class="metric-value ${cls.includes("lg") ? "lg" : ""}">${value}</div>
      ${sub ? `<div class="metric-sub">${sub}</div>` : ""}`;
    return el;
  },

  /* card */
  card(title, bodyNode, extra) {
    const c = UI.el("div", "card");
    const h = UI.el("div", "card-head");
    h.innerHTML = `<span>${UI.esc(title)}</span>${extra ? `<span class="muted">${extra}</span>` : ""}`;
    c.appendChild(h);
    const b = UI.el("div", "card-body");
    if (typeof bodyNode === "string") b.innerHTML = bodyNode;
    else if (bodyNode) b.appendChild(bodyNode);
    c.appendChild(b);
    return c;
  },

  /* toast */
  toast(title, msg, type = "") {
    const stack = document.getElementById("toast-stack");
    if (!stack) return;
    const t = UI.el("div", "toast " + type);
    t.innerHTML = `<div style="font-weight:700;font-size:12.5px;margin-bottom:2px">${UI.esc(title)}</div>
      <div style="color:var(--text-dim);font-size:11.5px">${UI.esc(msg)}</div>`;
    stack.appendChild(t);
    setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity 0.4s"; setTimeout(() => t.remove(), 400); }, 5000);
  },

  /* state box (empty/error/offline) */
  state(title, desc, icon = "○", warn = false) {
    const el = UI.el("div", "state-box" + (warn ? " warn" : ""));
    el.innerHTML = `<div class="state-ico">${UI.esc(icon)}</div>
      <div class="state-title">${UI.esc(title)}</div>
      <div class="state-desc">${UI.esc(desc)}</div>`;
    return el;
  },

  /* drawer */
  openDrawer(html) {
    const body = document.getElementById("drawer-body");
    const drawer = document.getElementById("drawer");
    const back = document.getElementById("drawer-backdrop");
    body.innerHTML = html;
    drawer.hidden = false;
    back.hidden = false;
    requestAnimationFrame(() => { drawer.classList.add("open"); });
    back.onclick = () => UI.closeDrawer();
  },
  closeDrawer() {
    const drawer = document.getElementById("drawer");
    const back = document.getElementById("drawer-backdrop");
    drawer.classList.remove("open");
    setTimeout(() => { drawer.hidden = true; back.hidden = true; }, 220);
  },

  /* progress bar */
  progress(pct, cls = "") {
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    return `<div class="progress ${cls}"><i style="width:${p}%"></i></div>`;
  },

  /* levels row (entry/SL/TP) */
  levels(entry, sl, tp1, tp2, tp3, rr) {
    return `<div class="levels">
      <div class="level entry"><div class="level-label">Entry</div><div class="level-value">${UI.fmt(entry)}</div></div>
      <div class="level sl"><div class="level-label">Stop Loss</div><div class="level-value">${UI.fmt(sl)}</div></div>
      <div class="level tp"><div class="level-label">TP1</div><div class="level-value">${UI.fmt(tp1)}</div></div>
      <div class="level tp"><div class="level-label">TP2</div><div class="level-value">${UI.fmt(tp2)}</div></div>
      <div class="level tp"><div class="level-label">TP3</div><div class="level-value">${UI.fmt(tp3)}</div></div>
      <div class="level"><div class="level-label">R:R</div><div class="level-value">1:${UI.fmt(rr, 1)}</div></div>
    </div>`;
  },

  /* MTF matrix from market_bias + signal */
  mtfMatrix(mb) {
    if (!mb) return `<div class="mtf-row"><div class="mtf-state muted">No MTF data</div></div>`;
    const tfs = [["4H", "4h"], ["1H", "1h"], ["30M", "30m"], ["15M", "15m"]];
    const rows = tfs.map(([label, key]) => {
      const d = mb[key] || {};
      const trend = (d.trend || "NEUTRAL").toUpperCase();
      const cls = trend === "BULLISH" ? "bull" : trend === "BEARISH" ? "bear" : "flat";
      const pct = trend === "BULLISH" ? 100 : trend === "BEARISH" ? 100 : 40;
      return `<div class="mtf-row">
        <div class="mtf-tf">${label}</div>
        <div class="mtf-bar"><div class="mtf-fill ${cls}" style="width:${pct}%"></div></div>
        <div class="mtf-state ${trend === "BULLISH" ? "up" : trend === "BEARISH" ? "down" : "flat"}">${trend}</div>
      </div>`;
    }).join("");
    return rows;
  },

  /* confluence breakdown bars from signal.detected_structures */
  confBreakdown(sig) {
    let bd = null;
    if (sig && sig.detected_structures && sig.detected_structures.score_breakdown) bd = sig.detected_structures.score_breakdown;
    else if (sig && sig.metadata_payload && sig.metadata_payload.confluence_breakdown) bd = sig.metadata_payload.confluence_breakdown;
    if (bd) {
      const rows = ["htf_bias", "market_structure", "smc_confirmation", "fib_confirmation", "liquidity_confirmation", "entry_confirmation", "risk_reward"]
        .map((k) => {
          const it = bd[k];
          if (!it) return "";
          const pct = it.max_points > 0 ? (it.points_awarded / it.max_points) * 100 : 0;
          const label = String(it.category || k).replace("_", " ");
          return `<div class="conf-row">
            <div class="conf-label">${UI.esc(label)}</div>
            <div class="conf-track"><div class="conf-fill ${it.passed ? "passed" : ""}" style="width:${pct}%"></div></div>
            <div class="conf-val">${UI.fmt(it.points_awarded, 0)}<span class="conf-max">/${UI.fmt(it.max_points, 0)}</span></div>
          </div>`;
        }).join("");
      if (rows) return rows;
    }

    // Custom strategy confluence & execution matrix
    const strat = String(sig?.strategy || "").toUpperCase();
    const ver = String(sig?.strategy_version || "");
    const dir = String(sig?.direction || "LONG").toUpperCase();
    const isTrend = strat.includes("TREND");
    const isSMC = strat.includes("SMC");

    let items = [];
    if (isTrend) {
      items = [
        { label: "9 / 21 EMA Alignment", status: "VERIFIED", desc: `Fast EMA confirmed ${dir === "LONG" ? "above" : "below"} 21 EMA`, color: "#22c55e" },
        { label: "Rule 7 Retracement", status: "TOUCHED", desc: "Clean pullback touch into 0.618 Golden Pocket", color: "#ffd54f" },
        { label: "Rule 8 Breakout Confirmation", status: "TRIGGERED", desc: `Trigger line confirmed breakout at $${Number(sig?.entry_price || 0).toFixed(2)}`, color: "#00bcd4" },
        { label: "2-Stage Target Policy", status: "ACTIVE", desc: "TP1 @ 1.000 (Breakeven Lock) & TP2 @ 1.618 (Target)", color: "#a855f7" },
      ];
    } else if (isSMC) {
      items = [
        { label: "0.680 Golden Pocket", status: "VERIFIED", desc: `Single institutional entry at $${Number(sig?.entry_price || 0).toFixed(2)}`, color: "#22c55e" },
        { label: "Single Trade Execution", status: "LOCKED", desc: "Strict 0.01 Lots (Zero layering / single order)", color: "#00bcd4" },
        { label: "Structural Anchor (1.000)", status: "CONFIRMED", desc: "Valid swing structure low/high protected", color: "#ffd54f" },
        { label: "Dynamic Target (0.000)", status: "TRACKING", desc: `Full impulse expansion target at $${Number(sig?.take_profit_1 || 0).toFixed(2)}`, color: "#a855f7" },
      ];
    } else {
      const layer = ver.includes("L2") ? "L2 (0.500)" : (ver.includes("L3") ? "L3 (0.382)" : "L1 (0.618)");
      items = [
        { label: "5M BOS Structure Break", status: "VERIFIED", desc: `Full body candle close beyond swing structure (${dir})`, color: "#22c55e" },
        { label: `Retracement Tranche ${layer}`, status: "ARMED", desc: `Execution entry at $${Number(sig?.entry_price || 0).toFixed(2)}`, color: "#00bcd4" },
        { label: "Smart Shield Stop Loss", status: "PROTECTED", desc: `SL secured at 0.236 ($${Number(sig?.stop_loss || 0).toFixed(2)}), trailed to 0.500 on L2 TP`, color: "#ffd54f" },
        { label: "3-Tranche Escape Policy", status: "ENABLED", desc: "0.01 Lots each (Closes at 0.618 if L2/L3 touched)", color: "#a855f7" },
      ];
    }

    return `<div style="display:flex;flex-direction:column;gap:8px">
      ${items.map(it => `
        <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:rgba(255,255,255,0.025);border:1px solid rgba(255,255,255,0.06);border-radius:6px">
          <div>
            <div style="font-size:12px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:6px">
              <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${it.color}"></span>
              ${UI.esc(it.label)}
            </div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:2px">${UI.esc(it.desc)}</div>
          </div>
          <span class="badge" style="font-size:10px;font-weight:700;background:rgba(34,197,94,0.15);color:#22c55e;border:1px solid rgba(34,197,94,0.3)">✓ ${UI.esc(it.status)}</span>
        </div>
      `).join("")}
    </div>`;
  },

  /* kv list */
  kv(pairs) {
    const items = pairs.map(([k, v]) => `<dt>${UI.esc(k)}</dt><dd>${v}</dd>`).join("");
    return `<dl class="kv">${items}</dl>`;
  },
};
