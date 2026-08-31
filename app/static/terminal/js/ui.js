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
  fmtTs(ts) {
    if (!ts) return "—";
    try { const d = new Date(ts); return d.toLocaleString(undefined, { month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
    catch (_) { return String(ts); }
  },
  fmtTsFull(ts) {
    if (!ts) return "—";
    try { return new Date(ts).toLocaleString(); } catch (_) { return String(ts); }
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
    if (!bd) return `<div class="muted" style="font-size:12px">Confluence breakdown unavailable.</div>`;
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
    return rows || `<div class="muted">No breakdown</div>`;
  },

  /* kv list */
  kv(pairs) {
    const items = pairs.map(([k, v]) => `<dt>${UI.esc(k)}</dt><dd>${v}</dd>`).join("");
    return `<dl class="kv">${items}</dl>`;
  },
};
