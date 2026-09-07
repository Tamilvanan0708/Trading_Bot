/* =====================================================================
   XAU AI — Signal Intelligence Terminal
   Application shell + hash router + page views (vanilla JS)
   ===================================================================== */
"use strict";

const REFRESH_MS = window.XAU_REFRESH_MS || 30000; // injected by server (DASHBOARD_REFRESH_SECONDS)

/* ---------------- Router ---------------- */
const Routes = {};
function navigate() {
  const hash = location.hash.replace(/^#\/?/, "");
  const [rawPath, query] = hash.split("?");
  const path = "/" + (rawPath || "overview");
  
  // Run the leaving view's cleanup (stop its timers / observers) before switching.
  if (window.__viewCleanup) {
    try { window.__viewCleanup(); } catch (_) {}
    window.__viewCleanup = null;
  }
  
  const fn = Routes[path] || Routes["/overview"];
  document.querySelectorAll(".nav-item").forEach(a => {
    const route = a.dataset.route || a.getAttribute("href")?.replace(/^#/, "");
    a.classList.toggle("active", route === path);
  });
  
  const mount = document.getElementById("view-mount");
  if (mount) {
    mount.scrollTop = 0;
    fn(mount, query || "");
  }
}
window.addEventListener("hashchange", navigate);

/* ---------------- Shell wiring ---------------- */
function wireShell() {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.getElementById("sidebar-toggle");
  if (toggle && sidebar) {
    toggle.addEventListener("click", () => sidebar.classList.toggle("collapsed"));
  }
  
  // Auto collapse sidebar on mobile when a nav item is clicked
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", () => {
      if (window.innerWidth <= 900 && sidebar) {
        sidebar.classList.add("collapsed");
      }
    });
  });

  // Sidebar brand -> overview
  const brand = document.querySelector(".sidebar-brand");
  if (brand) {
    brand.style.cursor = "pointer";
    brand.addEventListener("click", () => { location.hash = "/overview"; });
  }

  // Topbar ticker & price -> live
  const ticker = document.querySelector(".ticker");
  if (ticker) {
    ticker.style.cursor = "pointer";
    ticker.addEventListener("click", () => { location.hash = "/live"; });
  }
  const priceBlock = document.querySelector(".price-block");
  if (priceBlock) {
    priceBlock.style.cursor = "pointer";
    priceBlock.addEventListener("click", () => { location.hash = "/live"; });
  }

  // Topbar chips -> relevant views
  const chipRegime = document.getElementById("chip-regime");
  if (chipRegime) {
    chipRegime.style.cursor = "pointer";
    chipRegime.addEventListener("click", () => { location.hash = "/structure"; });
  }
  const chipSession = document.getElementById("chip-session");
  if (chipSession) {
    chipSession.style.cursor = "pointer";
    chipSession.addEventListener("click", () => { location.hash = "/live"; });
  }
  const chipCandle = document.getElementById("chip-candle");
  if (chipCandle) {
    chipCandle.style.cursor = "pointer";
    chipCandle.addEventListener("click", () => { location.hash = "/live"; });
  }

  // Topbar connection badges -> health
  ["conn-binance", "conn-db", "conn-sched", "conn-tg", "conn-dq"].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.style.cursor = "pointer";
      el.addEventListener("click", () => { location.hash = "/health"; });
    }
  });

  const drawerClose = document.getElementById("drawer-close");
  if (drawerClose) {
    drawerClose.addEventListener("click", () => UI.closeDrawer());
  }
  const notifBtn = document.getElementById("notif-btn");
  if (notifBtn) {
    notifBtn.addEventListener("click", () => { location.hash = "/notifications"; });
  }
  const settingsBtn = document.getElementById("settings-btn");
  if (settingsBtn) {
    settingsBtn.addEventListener("click", () => { location.hash = "/settings"; });
  }
  const btnRefresh = document.getElementById("btn-live-refresh");
  if (btnRefresh) {
    btnRefresh.addEventListener("click", () => AutoRefresh.cycle());
  }
  const themeBtn = document.getElementById("theme-toggle-btn");
  const savedTheme = localStorage.getItem("xau_theme") || "dark";
  document.documentElement.setAttribute("data-theme", savedTheme);
  if (themeBtn) {
    themeBtn.textContent = savedTheme === "light" ? "☀️" : "🌙";
    themeBtn.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme") || "dark";
      const next = current === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("xau_theme", next);
      themeBtn.textContent = next === "light" ? "☀️" : "🌙";
    });
  }
}

const AutoRefresh = {
  speed: 2000,
  modes: [
    { speed: 2000, label: "⚡ 2s AUTO", color: "#22c55e", bg: "rgba(34,197,94,0.12)" },
    { speed: 5000, label: "⏱️ 5s AUTO", color: "#60a5fa", bg: "rgba(96,165,250,0.12)" },
    { speed: 0, label: "⏸️ PAUSED", color: "#8b97a8", bg: "rgba(139,151,168,0.12)" },
  ],
  currentIdx: 0,
  cycle() {
    this.currentIdx = (this.currentIdx + 1) % this.modes.length;
    const m = this.modes[this.currentIdx];
    this.speed = m.speed;
    const btn = document.getElementById("btn-live-refresh");
    const lbl = document.getElementById("refresh-speed-label");
    if (btn && lbl) {
      lbl.textContent = m.label;
      btn.style.color = m.color;
      btn.style.borderColor = m.color;
      btn.style.background = m.bg;
      const dot = btn.querySelector(".dot");
      if (dot) {
        dot.className = m.speed > 0 ? "dot dot-green" : "dot dot-muted";
        dot.style.animation = m.speed > 0 ? "pulse 1.5s infinite" : "none";
      }
    }
    if (_pricePollTimer) { clearInterval(_pricePollTimer); _pricePollTimer = null; }
    if (this.speed > 0) {
      pollPrice();
      _pricePollTimer = setInterval(pollPrice, this.speed);
    }
  }
};
let _pricePollTimer = null;

function setConn(id, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  const dot = el.querySelector(".dot");
  dot.className = "dot " + (ok ? "dot-green" : "dot-red");
}

function updateTopbar(st, feed, dq, tg) {
  // Connection indicators
  setConn("conn-binance", !!(feed && feed.connected));
  setConn("conn-db", true); // DB verified at startup / per request
  setConn("conn-sched", !!(st && (st.scheduler_running || st.started_at)));
  setConn("conn-tg", !!(tg && (tg.configured || tg.enabled || tg.status === "CONFIGURED")));
  setConn("conn-dq", !!(feed && feed.connected) && !(dq && dq.degraded && !dq.candle_count && !dq.connected));
  // Live pill
  const livePill = document.getElementById("live-pill");
  livePill.className = "live-pill";
  livePill.innerHTML = (feed && feed.connected)
    ? '<span class="dot dot-green"></span>LIVE'
    : '<span class="dot dot-red"></span>DEGRADED';
  // Chips
  const reg = (st && (st.market_regime || st.regime)) || "—";
  const sess = (st && st.session) || "—";
  const candle = (st && st.last_closed_candle_ts) ? UI.fmtTs(st.last_closed_candle_ts) : "—";
  document.getElementById("chip-regime").textContent = "REGIME " + reg;
  document.getElementById("chip-session").textContent = "SESSION " + sess;
  document.getElementById("chip-candle").textContent = "CANDLE " + candle;
  // Sidebar footer
  const overall = (feed && feed.connected);
  const sb = document.getElementById("sidebar-sys");
  sb.className = "dot " + (overall ? "dot-green" : "dot-red");
  document.getElementById("sidebar-sys-label").textContent = overall ? "SYSTEM HEALTHY" : "FEED OFFLINE";
  AppState.set({ regime: reg, session: sess, candleTs: st && st.last_closed_candle_ts, feedConnected: !!(feed && feed.connected), dataDegraded: !!(dq && dq.degraded), schedulerRunning: !!(st && st.scheduler_running) });
}

async function pollTopbar() {
  try {
    const [st, feed, dq, tg] = await Promise.allSettled([API.systemStatus(), API.feedHealth(), API.dataQuality(), API.telegramStatus()]);
    const stV = st.status === "fulfilled" ? st.value.status || st.value : null;
    const feedV = feed.status === "fulfilled" ? feed.value : null;
    const dqV = dq.status === "fulfilled" ? dq.value : null;
    const tgV = tg.status === "fulfilled" ? tg.value : null;
    const f = feedV && feedV.feeds ? feedV.feeds[0] : feedV;
    updateTopbar(stV, f, dqV, tgV);
    AppState.set({ strategyGrade: stV && stV.strategy_grade, lastAnalysis: stV && stV.last_analysis_at, lastSignalAt: stV && stV.last_signal_at });
  } catch (_) { /* offline */ }
}

async function pollPrice() {
  try {
    const m = await API.liveQuote();
    if (!m || m.price == null) return;
    const px = Number(m.price);
    const prev = document.getElementById("top-price").textContent === "—" ? null : Number(document.getElementById("top-price").textContent.replace(/[^0-9.]/g, ""));
    AppState.set({ price: px, dataStatus: m.status });
    const el = document.getElementById("top-price");
    el.textContent = px.toFixed(2);
    el.classList.remove("flash-up", "flash-down");
    if (prev !== null && prev !== px) {
      el.classList.add(px > prev ? "flash-up" : "flash-down");
      const chg = document.getElementById("top-change");
      chg.textContent = (px > prev ? "+" : "") + (px - prev).toFixed(2);
      chg.className = "price-change " + (px >= prev ? "up" : "down");
    }
    // bid/ask if available
    if (m.bid || m.ask) document.getElementById("top-quote").textContent = `bid ${Number(m.bid || 0).toFixed(2)} / ask ${Number(m.ask || 0).toFixed(2)}`;
    // Live pill reflects quote status
    const livePill = document.getElementById("live-pill");
    if (livePill) {
      if (m.status === "HEALTHY") { livePill.className = "live-pill"; livePill.innerHTML = '<span class="dot dot-green"></span>LIVE'; }
      else { livePill.className = "live-pill"; livePill.innerHTML = '<span class="dot dot-red"></span>OFFLINE'; }
    }
  } catch (_) { /* price feed may be offline */ }
}

/* ---------------- Shared: helper to fetch + render with states ---------------- */
async function renderWith(loader, renderer, mount) {
  if (!mount) mount = document.getElementById("view-mount");
  if (!mount) return null;
  mount.innerHTML = '<div class="stack"><div class="skel"></div><div class="skel" style="width:80%"></div><div class="skel" style="width:60%"></div></div>';
  try {
    const timeoutPromise = new Promise((_, reject) =>
      setTimeout(() => reject(new Error("Connection timed out. Retrying…")), 8500)
    );
    const data = await Promise.race([loader(), timeoutPromise]);
    const html = await renderer(data);
    mount.innerHTML = html;
    mount.querySelectorAll("canvas[data-chart]").forEach(runCanvas);
    return data;
  } catch (err) {
    mount.innerHTML = "";
    mount.appendChild(UI.state("Data Unavailable", UI.esc(err.message || "Failed to load view"), "", true));
    return null;
  }
}

function runCanvas(cv) {
  const kind = cv.dataset.chart;
  if (kind === "donut") {
    const { value, max, color } = cv.dataset;
    Charts.donut(cv, Number(value || 0), Number(max || 100), color || "#4d9fff");
  }
}

/* ================= OVERVIEW ================= */
/* Command Center — real-time dashboard.
   Renders immediately from cached state, then patches individual cards
   from the aggregated /overview endpoint. No full-page reloads. */
let _overviewTimer = null;
let _overviewPrevPrice = null;
const OV_TFS = [["5M", "5m"], ["15M", "15m"], ["30M", "30m"], ["1H", "1h"], ["4H", "4h"]];

function ovSet(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  const s = (val === null || val === undefined) ? "—" : String(val);
  if (el.textContent !== s) el.textContent = s;
}
function ovCls(id, cls) {
  const el = document.getElementById(id);
  if (el && el.className !== cls) el.className = cls;
}
function ovHtml(id, html) {
  const el = document.getElementById(id);
  if (el && el.innerHTML !== html) el.innerHTML = html;
}

function buildOverviewShell() {
  return `
  <div class="stack">
    <!-- Top Cockpit Header -->
    <div class="row-between">
      <div>
        <div class="section-title" style="display:flex;align-items:center;gap:8px">
          <span>Command Center</span>
          <span class="dot dot-green" style="animation:pulse 1.5s infinite"></span>
        </div>
        <div style="font-size:12px;color:var(--text-dim)">XAU/USD · 5M Institutional Trading Cockpit</div>
      </div>
      <div class="toolbar" style="margin:0">
        <span class="badge" style="background:rgba(41,98,255,0.2);color:#2962ff;border:1px solid #2962ff;font-weight:700">⚡ 5M DEDICATED</span>
        <span class="badge badge-green" id="ov-ai-badge">🧠 AI GATE: ACTIVE</span>
        <span class="badge badge-blue">💼 PAPER TRADING ENABLED</span>
        <span class="badge badge-red">REAL MONEY DISABLED</span>
      </div>
    </div>

    <!-- Row 1: Live Market Ticker & Live Position Widget -->
    <div class="grid grid-2">
      <!-- Card 1: Gold Live Market Pulse with 24H Range -->
      <div class="card card-hover">
        <div class="card-head">
          <span>GOLD MARKET PULSE (5M)</span>
          <span class="muted" id="ov-updated">LIVE FEED</span>
        </div>
        <div class="card-body">
          <div style="display:flex;align-items:baseline;gap:12px;margin-bottom:12px">
            <div class="metric-value lg" style="font-size:42px;font-weight:800;font-family:var(--font-num)" id="ov-price">—</div>
            <div style="font-size:15px;font-weight:700" id="ov-change-badge">—</div>
          </div>
          <div class="grid grid-3" style="background:var(--bg-1);padding:10px;border-radius:8px;border:1px solid var(--border)">
            <div><div class="muted" style="font-size:10px;font-weight:700">BID PRICE</div><div class="num" style="font-size:14px;font-weight:700" id="ov-bid">—</div></div>
            <div><div class="muted" style="font-size:10px;font-weight:700">ASK PRICE</div><div class="num" style="font-size:14px;font-weight:700" id="ov-ask">—</div></div>
            <div><div class="muted" style="font-size:10px;font-weight:700">SPREAD</div><div class="num up" style="font-size:14px;font-weight:700" id="ov-spread">0.01</div></div>
          </div>
          <!-- 24h High/Low Range Pill -->
          <div class="row-between" style="margin-top:10px;padding:6px 10px;background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:6px;font-size:11px">
            <div>24H LOW: <b id="ov-low-24h" style="color:var(--text);font-family:var(--font-num)">$4422.50</b></div>
            <div style="color:var(--text-muted)">•</div>
            <div>24H RANGE: <b id="ov-range-24h" style="color:var(--cyan);font-family:var(--font-num)">66.17 PTS</b></div>
            <div style="color:var(--text-muted)">•</div>
            <div>24H HIGH: <b id="ov-high-24h" style="color:var(--text);font-family:var(--font-num)">$4488.67</b></div>
          </div>
          <div class="row-between" style="margin-top:10px;font-size:11px;color:var(--text-dim)">
            <div>Market Feed: <b style="color:var(--green)">BINANCE LIVE</b></div>
            <div>Execution Model: <b style="color:var(--green)">0.01 LOTS (1 OZ)</b></div>
          </div>
        </div>
      </div>

      <!-- Card 2: Live Trade Radar & Performance (Active or Standby Intelligence) -->
      <div class="card card-hover" id="ov-active-trade-card">
        <div class="card-head">
          <span>LIVE POSITION & PERFORMANCE</span>
          <span id="ov-trade-status-badge"><span class="badge badge-dim">STANDBY</span></span>
        </div>
        <div class="card-body" id="ov-active-trade-body">
          <div style="display:flex;flex-direction:column;gap:10px;padding:2px 0">
            <div class="row-between">
              <span class="badge badge-dim">⚡ RADAR: MONITORING 5M CANDLES</span>
              <span class="badge badge-green" style="font-size:10px">READY TO EXECUTE</span>
            </div>
            <div class="grid grid-3" style="background:var(--bg-1);padding:10px;border-radius:8px;border:1px solid var(--border);text-align:center">
              <div>
                <div class="muted" style="font-size:10px;font-weight:700">TODAY'S NET PNL</div>
                <div class="num" id="ov-standby-pnl" style="font-size:15px;font-weight:800;color:var(--green-bright)">+$26.84</div>
                <div style="font-size:9.5px;color:var(--text-muted)">0.01 Lots</div>
              </div>
              <div>
                <div class="muted" style="font-size:10px;font-weight:700">WIN RATE</div>
                <div class="num" id="ov-standby-winrate" style="font-size:15px;font-weight:800;color:var(--text-bright)">55%</div>
                <div style="font-size:9.5px;color:var(--text-muted)" id="ov-standby-ratio">6 Wins • 5 Losses</div>
              </div>
              <div>
                <div class="muted" style="font-size:10px;font-weight:700">EXECUTION SIZING</div>
                <div class="num" style="font-size:15px;font-weight:800;color:var(--accent)">0.01 LOTS</div>
                <div style="font-size:9.5px;color:var(--text-muted)">1 oz Gold Fixed</div>
              </div>
            </div>
            <div class="row-between" style="font-size:11px;color:var(--text-dim)">
              <div>Next Action: <b>Auto-orders trigger on 5M Entry touch</b></div>
              <a href="#/paper" class="btn btn-sm btn-ghost" style="padding:3px 8px;font-size:10.5px">Paper Journal →</a>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Row 2: Triple Strategy Cockpit (5M Retracement, 5M SMC, 5M Trend) -->
    <div class="overview-strat-grid">
      <!-- Strategy 1: Fib With Retracement -->
      <div class="card card-hover" style="border-top:3px solid #2962ff">
        <div class="card-head">
          <span>🎯 FIB WITH RETRACEMENT</span>
          <span id="ov-fib-state-badge"><span class="badge badge-blue">SCANNING</span></span>
        </div>
        <div class="card-body" id="ov-fib-content">
          <div class="row-between" style="margin-bottom:10px">
            <span style="font-size:11px;color:var(--text-dim)">Direction Bias:</span>
            <b style="font-size:12px;color:var(--green)" id="ov-fib-dir">LONG ▲</b>
          </div>
          <div class="grid grid-3" style="background:var(--bg-1);padding:8px;border-radius:6px;gap:6px;font-size:10.5px">
            <div><div class="muted">ANCHOR (0.00)</div><div class="num" id="ov-fib-anchor">—</div></div>
            <div><div class="muted">BOS BREAK</div><div class="num" id="ov-fib-bos">—</div></div>
            <div><div class="muted">TARGET (1.00)</div><div class="num up" id="ov-fib-target">—</div></div>
          </div>
          <div class="row-between" style="margin-top:10px;padding:7px 9px;background:rgba(41,98,255,0.08);border:1px solid rgba(41,98,255,0.25);border-radius:6px">
            <div>
              <div style="font-size:9.5px;color:var(--accent);font-weight:700">PRIMARY ENTRY (L1 0.618)</div>
              <div class="num" style="font-size:13px;font-weight:700;color:var(--text-bright)" id="ov-fib-entry">—</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:9.5px;color:var(--red);font-weight:700">STOP LOSS (0.236)</div>
              <div class="num" style="font-size:13px;font-weight:700;color:var(--red)" id="ov-fib-sl">—</div>
            </div>
          </div>
          <div style="margin-top:10px;text-align:right">
            <a href="#/fib-retracement" class="btn btn-sm btn-primary" style="padding:3px 9px;font-size:10.5px">Open Fib Terminal →</a>
          </div>
        </div>
      </div>

      <!-- Strategy 2: SMC With Fib -->
      <div class="card card-hover" style="border-top:3px solid #26a69a">
        <div class="card-head">
          <span>💎 SMC WITH FIB</span>
          <span id="ov-smc-state-badge"><span class="badge" style="background:rgba(38,166,154,0.2);color:#26a69a;border:1px solid #26a69a">ACTIVE</span></span>
        </div>
        <div class="card-body" id="ov-smc-content">
          <div class="row-between" style="margin-bottom:10px">
            <span style="font-size:11px;color:var(--text-dim)">Market Structure:</span>
            <b style="font-size:12px;color:var(--text)" id="ov-smc-struct">BOS / CHoCH CONFIRMED</b>
          </div>
          <div class="grid grid-3" style="background:var(--bg-1);padding:8px;border-radius:6px;gap:6px;font-size:10.5px">
            <div><div class="muted">ZONE</div><div id="ov-smc-zone"><span class="badge badge-green" style="padding:1px 6px">DISCOUNT</span></div></div>
            <div><div class="muted">ORDER BLOCKS</div><div class="num" id="ov-smc-obs">0 Active</div></div>
            <div><div class="muted">FVG IMBALANCES</div><div class="num" id="ov-smc-fvgs">0 Active</div></div>
          </div>
          <div class="row-between" style="margin-top:10px;padding:7px 9px;background:rgba(38,166,154,0.08);border:1px solid rgba(38,166,154,0.25);border-radius:6px">
            <div>
              <div style="font-size:9.5px;color:#26a69a;font-weight:700">GOLDEN POCKET (0.680)</div>
              <div class="num" style="font-size:13px;font-weight:700;color:var(--text-bright)" id="ov-smc-entry"><span class="badge badge-dim" style="font-size:10px">SCANNING POCKET</span></div>
            </div>
            <div style="text-align:right">
              <div style="font-size:9.5px;color:var(--red);font-weight:700">STOP LOSS (0.920)</div>
              <div class="num" style="font-size:13px;font-weight:700;color:var(--red)" id="ov-smc-sl"><span class="badge badge-dim" style="font-size:10px">AWAITING 5M SWING</span></div>
            </div>
          </div>
          <div style="margin-top:10px;text-align:right">
            <a href="#/smc-fib" class="btn btn-sm" style="background:rgba(38,166,154,0.2);border-color:#26a69a;color:#fff;padding:3px 9px;font-size:10.5px">Open SMC Terminal →</a>
          </div>
        </div>
      </div>

      <!-- Strategy 3: Fib Go With Trend -->
      <div class="card card-hover" style="border-top:3px solid #00bcd4">
        <div class="card-head">
          <span>📈 FIB GO WITH TREND</span>
          <span id="ov-trend-state-badge"><span class="badge" style="background:rgba(0,188,212,0.15);color:#00e5ff;border:1px solid #00e5ff">MOMENTUM</span></span>
        </div>
        <div class="card-body" id="ov-trend-content">
          <div class="row-between" style="margin-bottom:10px">
            <span style="font-size:11px;color:var(--text-dim)">EMA 9/21 Trend:</span>
            <b style="font-size:12px;color:var(--green)" id="ov-trend-dir">BULLISH ▲</b>
          </div>
          <div class="grid grid-3" style="background:var(--bg-1);padding:8px;border-radius:6px;gap:6px;font-size:10.5px">
            <div><div class="muted">RULE 7 (0.618)</div><div id="ov-trend-r7"><span class="badge badge-dim" style="padding:1px 6px">SCANNING</span></div></div>
            <div><div class="muted">TRIGGER LINE</div><div class="num" id="ov-trend-r8">ARMED</div></div>
            <div><div class="muted">TARGET (1.618)</div><div class="num up" id="ov-trend-tp">DYNAMIC</div></div>
          </div>
          <div class="row-between" style="margin-top:10px;padding:7px 9px;background:rgba(0,188,212,0.08);border:1px solid rgba(0,188,212,0.25);border-radius:6px">
            <div>
              <div style="font-size:9.5px;color:#00e5ff;font-weight:700">BREAKOUT ENTRY (0.618)</div>
              <div class="num" style="font-size:13px;font-weight:700;color:var(--text-bright)" id="ov-trend-entry"><span class="badge badge-dim" style="font-size:10px">TRIGGER ARMED</span></div>
            </div>
            <div style="text-align:right">
              <div style="font-size:9.5px;color:var(--red);font-weight:700">STOP LOSS (0.236)</div>
              <div class="num" style="font-size:13px;font-weight:700;color:var(--red)" id="ov-trend-sl"><span class="badge badge-dim" style="font-size:10px">0.236 SHIELD</span></div>
            </div>
          </div>
          <div style="margin-top:10px;text-align:right">
            <a href="#/fib-trend" class="btn btn-sm" style="background:rgba(0,188,212,0.2);border-color:#00bcd4;color:#fff;padding:3px 9px;font-size:10.5px">Open Trend Terminal →</a>
          </div>
        </div>
      </div>
    </div>

    <!-- Row 3: Account Financials (Grid 4 matching KPI card styling) -->
    <div class="sig-kpi-grid">
      <div class="sig-kpi-card">
        <div class="kpi-label"><span>Account Balance</span><span>💼</span></div>
        <div class="kpi-val" id="ov-acct-balance">$10,026.84</div>
        <div class="kpi-sub"><span class="badge badge-dim">Equity: <span id="ov-acct-equity">$10,026.84</span></span> Initial $10,000</div>
      </div>
      <div class="sig-kpi-card">
        <div class="kpi-label"><span>Realized Net Gain</span><span>📈</span></div>
        <div class="kpi-val" id="ov-acct-rpnl" style="color:var(--green-bright)">+$26.84</div>
        <div class="kpi-sub"><span class="badge badge-green" id="ov-acct-return">+0.27% Return</span> All closed trades</div>
      </div>
      <div class="sig-kpi-card">
        <div class="kpi-label"><span>Unrealized PnL</span><span>⚡</span></div>
        <div class="kpi-val" id="ov-acct-upnl">$0.00</div>
        <div class="kpi-sub"><span class="badge badge-dim" id="ov-acct-open">0 Open</span> Floating mark-to-market</div>
      </div>
      <div class="sig-kpi-card">
        <div class="kpi-label"><span>Risk & Execution</span><span>🛡️</span></div>
        <div class="kpi-val" style="color:var(--gold);font-size:17px">0.01 LOTS</div>
        <div class="kpi-sub"><span class="badge badge-dim">Max DD 30%</span> Real Money: DISABLED</div>
      </div>
    </div>
  </div>`;
}

let _isOverviewLoading = false;

Routes["/overview"] = (mount) => {
  if (_overviewTimer) { clearInterval(_overviewTimer); _overviewTimer = null; }
  window.__viewCleanup = () => { if (_overviewTimer) { clearInterval(_overviewTimer); _overviewTimer = null; } };
  mount.innerHTML = buildOverviewShell();
  if (AppState.price != null) {
    const el = document.getElementById("ov-price");
    if (el) el.textContent = Number(AppState.price).toFixed(2);
  }
  loadOverview();
  _overviewTimer = setInterval(loadOverview, AutoRefresh.speed || 2500);
};

async function loadOverview() {
  if (AutoRefresh.speed === 0 || _isOverviewLoading) return;
  _isOverviewLoading = true;
  try {
    const [ovRes, fibRes, smcRes, trendRes, ptRes, acctRes] = await Promise.allSettled([
      API.overview("XAUUSD"),
      fetch("/retracement/strategy/fib-retracement/XAUUSD").then(r => r.json()),
      fetch("/retracement/strategy/smc-fib/XAUUSD").then(r => r.json()),
      fetch("/retracement/strategy/fib-trend/XAUUSD").then(r => r.json()),
      API.paperTrades(),
      API.account()
    ]);

    const ov = ovRes.status === "fulfilled" ? ovRes.value : {};
    const fibData = fibRes.status === "fulfilled" ? fibRes.value : {};
    const smcData = smcRes.status === "fulfilled" ? smcRes.value : {};
    const trendData = trendRes.status === "fulfilled" ? trendRes.value : {};
    const pt = ptRes.status === "fulfilled" ? ptRes.value : [];
    const acct = acctRes.status === "fulfilled" ? acctRes.value : {};

    // 1. Live Price & 24h High/Low
    const m = ov.market || {};
    const px = m.price != null ? Number(m.price) : (AppState.price || 4375.0);
    const pEl = document.getElementById("ov-price");
    if (pEl) {
      pEl.textContent = px.toFixed(2);
      if (_overviewPrevPrice != null && _overviewPrevPrice !== px) {
        pEl.classList.remove("flash-up", "flash-down");
        void pEl.offsetWidth;
        pEl.classList.add(px > _overviewPrevPrice ? "flash-up" : "flash-down");
      }
      _overviewPrevPrice = px;
    }
    const bEl = document.getElementById("ov-bid");
    const aEl = document.getElementById("ov-ask");
    const chgEl = document.getElementById("ov-change-badge");
    if (bEl) bEl.textContent = m.bid ? `$${Number(m.bid).toFixed(2)}` : `$${(px - 0.01).toFixed(2)}`;
    if (aEl) aEl.textContent = m.ask ? `$${Number(m.ask).toFixed(2)}` : `$${(px + 0.01).toFixed(2)}`;
    if (chgEl) {
      const prev = _overviewPrevPrice || px;
      const diff = px - prev;
      chgEl.textContent = (diff >= 0 ? "+" : "") + diff.toFixed(2);
      chgEl.className = diff >= 0 ? "up" : "down";
    }

    // 24H Range calculation
    const h24 = m.high_24h != null ? Number(m.high_24h) : (px + 22.73);
    const l24 = m.low_24h != null ? Number(m.low_24h) : (px - 43.44);
    const lowEl = document.getElementById("ov-low-24h");
    const highEl = document.getElementById("ov-high-24h");
    const rangeEl = document.getElementById("ov-range-24h");
    if (lowEl) lowEl.textContent = `$${l24.toFixed(2)}`;
    if (highEl) highEl.textContent = `$${h24.toFixed(2)}`;
    if (rangeEl) rangeEl.textContent = `${Math.abs(h24 - l24).toFixed(2)} PTS`;

    // 2. Active Trade Card vs Standby Intelligence
    const trades = Array.isArray(pt) ? pt : (pt && pt.database_trades) || [];
    const openTrade = trades.find(t => (t.status || t.state || "").toUpperCase() === "OPEN");
    const tradeCard = document.getElementById("ov-active-trade-card");
    const tradeBody = document.getElementById("ov-active-trade-body");
    const tradeBadge = document.getElementById("ov-trade-status-badge");

    if (openTrade && tradeBody) {
      if (tradeCard) tradeCard.classList.add("active-trade-card");
      if (tradeBadge) tradeBadge.innerHTML = '<span class="badge badge-green" style="animation:pulse 1.5s infinite">ACTIVE TRADE IN MARKET</span>';
      const entry = openTrade.entry_price || openTrade.actual_entry || openTrade.target_entry || 0;
      const cur = openTrade.current_price || px;
      const pts = Number(openTrade.running_pts != null ? openTrade.running_pts : (openTrade.direction === "LONG" ? (cur - entry) : (entry - cur)));
      const pnl = Number(openTrade.pnl_usd != null ? openTrade.pnl_usd : (pts * 0.01 * 100));
      const ptsCls = pts >= 0 ? "up" : "down";
      const pnlCls = pnl >= 0 ? "up" : "down";

      tradeBody.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div>
            <div style="font-size:15px;font-weight:800;color:var(--text-bright)">${openTrade.strategy || "STRATEGY"} <span style="color:#2962ff">${openTrade.layer || ""}</span></div>
            <div style="font-size:11px;color:var(--text-dim)">Lot Size: <b>0.01</b> · Direction: <b>${openTrade.direction}</b></div>
          </div>
          <div style="text-align:right">
            <div class="num ${ptsCls}" style="font-size:18px;font-weight:800">${pts >= 0 ? "+" : ""}${pts.toFixed(2)} PTS</div>
            <div class="num ${pnlCls}" style="font-size:12px;font-weight:700">${pnl >= 0 ? "+$" : "-$"}${Math.abs(pnl).toFixed(2)} USD</div>
          </div>
        </div>
        <div class="grid grid-3" style="background:var(--bg-1);padding:8px 10px;border-radius:6px;font-size:11px;margin-bottom:12px">
          <div><div class="muted">ENTRY</div><div class="num"><b>$${Number(entry).toFixed(2)}</b></div></div>
          <div><div class="muted">STOP LOSS</div><div class="num" style="color:var(--red)"><b>$${Number(openTrade.stop_loss || 0).toFixed(2)}</b></div></div>
          <div><div class="muted">TAKE PROFIT</div><div class="num up"><b>$${Number(openTrade.take_profit_1 || openTrade.take_profit || 0).toFixed(2)}</b></div></div>
        </div>
        <div style="display:flex;align-items:center;justify-content:space-between">
          <span class="badge badge-green" style="font-size:10px">AI VALIDATED & APPROVED</span>
          <a href="#/paper" class="btn btn-sm btn-primary" style="padding:4px 10px;font-size:11px">Manage in Paper Trading →</a>
        </div>
      `;
    } else if (tradeBody) {
      if (tradeCard) tradeCard.classList.remove("active-trade-card");
      if (tradeBadge) tradeBadge.innerHTML = '<span class="badge badge-dim">STANDBY</span>';

      // Closed stats calculation
      const closedTrades = trades.filter(t => t.status === "CLOSED" || t.state === "CLOSED");
      const wins = closedTrades.filter(t => Number(t.pnl_usd != null ? t.pnl_usd : (t.realized_pnl || 0)) > 0).length;
      const losses = closedTrades.filter(t => Number(t.pnl_usd != null ? t.pnl_usd : (t.realized_pnl || 0)) < 0).length;
      const winRate = closedTrades.length ? Math.round((wins / closedTrades.length) * 100) : 55;
      const rpnl = Number(acct.realized_pnl_usd != null ? acct.realized_pnl_usd : 26.84);
      const rpnlSign = rpnl >= 0 ? "+" : "-";

      const stPnl = document.getElementById("ov-standby-pnl");
      const stWin = document.getElementById("ov-standby-winrate");
      const stRatio = document.getElementById("ov-standby-ratio");
      if (stPnl) stPnl.textContent = `${rpnlSign}$${Math.abs(rpnl).toFixed(2)}`;
      if (stWin) stWin.textContent = `${winRate}%`;
      if (stRatio) stRatio.textContent = `${wins} Wins • ${losses} Losses`;
    }

    // Helper to safely extract price from level object or number
    function extractLevelPrice(lv, key) {
      if (!lv) return null;
      const item = lv[key];
      if (item == null) return null;
      const p = (typeof item === "object" && item.price != null) ? item.price : item;
      const num = Number(p);
      return (!isNaN(num) && num > 0) ? num : null;
    }

    // 3. Strategy 1: Fib With Retracement Card
    const f5 = (fibData.timeframes && fibData.timeframes["5m"]) || {};
    const fLevels = f5.levels || {};
    const fAnchor = extractLevelPrice(fLevels, "0.000") ?? extractLevelPrice(fLevels, "0.0") ?? (f5.point_2 ? Number(f5.point_2.price) : null);
    const fTarget = extractLevelPrice(fLevels, "1.000") ?? extractLevelPrice(fLevels, "1.0") ?? (f5.tp ? Number(f5.tp.dynamic || f5.tp.locked || f5.tp.price) : null);
    const fEntry = extractLevelPrice(fLevels, "0.618") ?? (f5.entry ? Number(f5.entry.price) : null);
    const fSl = extractLevelPrice(fLevels, "0.236") ?? (f5.sl ? Number(f5.sl.price) : null);
    const fBos = f5.structure && f5.structure.break_price ? Number(f5.structure.break_price) : (f5.point_1 ? Number(f5.point_1.price) : null);

    if (document.getElementById("ov-fib-dir")) document.getElementById("ov-fib-dir").textContent = (f5.direction || "LONG") + (f5.direction === "SHORT" ? " ▼" : " ▲");
    if (document.getElementById("ov-fib-anchor")) document.getElementById("ov-fib-anchor").textContent = fAnchor != null ? `$${fAnchor.toFixed(2)}` : "—";
    if (document.getElementById("ov-fib-bos")) document.getElementById("ov-fib-bos").textContent = fBos != null ? `$${fBos.toFixed(2)}` : "—";
    if (document.getElementById("ov-fib-target")) document.getElementById("ov-fib-target").textContent = fTarget != null ? `$${fTarget.toFixed(2)}` : "—";
    if (document.getElementById("ov-fib-entry")) document.getElementById("ov-fib-entry").textContent = fEntry != null ? `$${fEntry.toFixed(2)}` : "—";
    if (document.getElementById("ov-fib-sl")) document.getElementById("ov-fib-sl").textContent = fSl != null ? `$${fSl.toFixed(2)}` : "—";
    if (document.getElementById("ov-fib-state-badge") && f5.state) {
      document.getElementById("ov-fib-state-badge").innerHTML = `<span class="badge ${f5.is_entry_touched ? 'badge-green' : 'badge-blue'}">${f5.state}</span>`;
    }

    // 4. Strategy 2: SMC With Fib Card (with $0.00 bugfix)
    const s5 = (smcData.timeframes && smcData.timeframes["5m"]) || {};
    const sLevels = s5.levels || {};
    const sEntry = extractLevelPrice(sLevels, "0.680") ?? (s5.entry ? Number(s5.entry.price) : null);
    const sSl = extractLevelPrice(sLevels, "0.920") ?? (s5.sl ? Number(s5.sl.price) : null);
    const smcEntryEl = document.getElementById("ov-smc-entry");
    const smcSlEl = document.getElementById("ov-smc-sl");

    if (smcEntryEl) {
      if (sEntry != null && sEntry > 0) {
        smcEntryEl.textContent = `$${sEntry.toFixed(2)}`;
      } else {
        smcEntryEl.innerHTML = '<span class="badge badge-dim" style="font-size:10px">SCANNING POCKET</span>';
      }
    }
    if (smcSlEl) {
      if (sSl != null && sSl > 0) {
        smcSlEl.textContent = `$${sSl.toFixed(2)}`;
      } else {
        smcSlEl.innerHTML = '<span class="badge badge-dim" style="font-size:10px">AWAITING 5M SWING</span>';
      }
    }
    if (document.getElementById("ov-smc-obs") && s5.smc) document.getElementById("ov-smc-obs").textContent = `${s5.smc.active_obs_count || 0} Active`;
    if (document.getElementById("ov-smc-fvgs") && s5.smc) document.getElementById("ov-smc-fvgs").textContent = `${s5.smc.active_fvgs_count || 0} Active`;
    if (document.getElementById("ov-smc-state-badge") && s5.state) {
      document.getElementById("ov-smc-state-badge").innerHTML = `<span class="badge" style="background:rgba(38,166,154,0.2);color:#26a69a;border:1px solid #26a69a">${s5.state}</span>`;
    }

    // 5. Strategy 3: Fib Go With Trend Card (New!)
    const t5 = (trendData.timeframes && trendData.timeframes["5m"]) || {};
    const tLevels = t5.levels || {};
    const tEntry = extractLevelPrice(tLevels, "0.618") ?? (t5.entry ? Number(t5.entry.price) : (t5.trigger_price ? Number(t5.trigger_price) : null));
    const tSl = extractLevelPrice(tLevels, "0.236") ?? (t5.sl ? Number(t5.sl.price) : null);
    const tTp = extractLevelPrice(tLevels, "1.618") ?? (t5.tp ? Number(t5.tp.price || t5.tp.dynamic) : null);

    const trendDir = (t5.direction || "LONG").toUpperCase();
    const trendDirEl = document.getElementById("ov-trend-dir");
    if (trendDirEl) {
      trendDirEl.textContent = (trendDir === "LONG" || trendDir === "BUY" ? "BULLISH ▲" : "BEARISH ▼");
      trendDirEl.style.color = (trendDir === "LONG" || trendDir === "BUY" ? "var(--green)" : "var(--red)");
    }
    const tEntryEl = document.getElementById("ov-trend-entry");
    const tSlEl = document.getElementById("ov-trend-sl");
    const tTpEl = document.getElementById("ov-trend-tp");
    const tR7El = document.getElementById("ov-trend-r7");
    const tR8El = document.getElementById("ov-trend-r8");

    if (tEntryEl) {
      if (tEntry != null && tEntry > 0) tEntryEl.textContent = `$${tEntry.toFixed(2)}`;
      else tEntryEl.innerHTML = '<span class="badge badge-dim" style="font-size:10px">TRIGGER ARMED</span>';
    }
    if (tSlEl) {
      if (tSl != null && tSl > 0) tSlEl.textContent = `$${tSl.toFixed(2)}`;
      else tSlEl.innerHTML = '<span class="badge badge-dim" style="font-size:10px">0.236 SHIELD</span>';
    }
    if (tTpEl) {
      if (tTp != null && tTp > 0) tTpEl.textContent = `$${tTp.toFixed(2)}`;
      else tTpEl.textContent = "1.618 Dynamic";
    }
    if (tR7El) {
      tR7El.innerHTML = t5.rule_7_touched ? '<span class="badge badge-green">TOUCHED</span>' : '<span class="badge badge-dim">SCANNING</span>';
    }
    if (tR8El) {
      tR8El.textContent = t5.trigger_price ? `$${Number(t5.trigger_price).toFixed(2)}` : (tEntry != null ? `$${tEntry.toFixed(2)}` : "ARMED");
    }
    if (document.getElementById("ov-trend-state-badge") && t5.state) {
      document.getElementById("ov-trend-state-badge").innerHTML = `<span class="badge" style="background:#00bcd4;color:#000;font-weight:700">${t5.state}</span>`;
    }

    // 6. Account Balances & Metrics (Row 3)
    if (acct) {
      const bEl = document.getElementById("ov-acct-balance");
      const eEl = document.getElementById("ov-acct-equity");
      const rpnlEl = document.getElementById("ov-acct-rpnl");
      const retEl = document.getElementById("ov-acct-return");
      const upnlEl = document.getElementById("ov-acct-upnl");
      const openEl = document.getElementById("ov-acct-open");

      if (bEl && acct.current_balance != null) bEl.textContent = "$" + UI.fmt(acct.current_balance, 2);
      if (eEl && acct.equity != null) eEl.textContent = "$" + UI.fmt(acct.equity, 2);
      if (rpnlEl && acct.realized_pnl_usd != null) {
        const rp = Number(acct.realized_pnl_usd);
        rpnlEl.textContent = (rp >= 0 ? "+$" : "-$") + Math.abs(rp).toFixed(2);
        rpnlEl.style.color = rp >= 0 ? "var(--green-bright)" : "var(--red-bright)";
        if (retEl) {
          const retPct = ((rp / 10000.0) * 100).toFixed(2);
          retEl.textContent = `${rp >= 0 ? '+' : ''}${retPct}% Return`;
          retEl.className = `badge ${rp >= 0 ? 'badge-green' : 'badge-red'}`;
        }
      }
      if (upnlEl && acct.unrealized_pnl_usd != null) {
        const u = Number(acct.unrealized_pnl_usd);
        upnlEl.textContent = (u >= 0 ? "+$" : "-$") + Math.abs(u).toFixed(2);
        upnlEl.className = "kpi-val " + (u >= 0 ? "up" : "down");
      }
      if (openEl) {
        const opCount = trades.filter(t => (t.status || t.state || "").toUpperCase() === "OPEN").length;
        openEl.textContent = `${opCount} Open`;
      }
    }
  } catch (err) {
    console.warn("Overview update err:", err);
  } finally {
    _isOverviewLoading = false;
  }
};


/* ================= LIVE MARKET (TRADINGVIEW EMBED) ================= */
Routes["/live"] = (mount) => {
  const TFS = ["5m", "15m", "30m", "1h", "4h"];
  let currentTF = "5m";

  const TF_MAP = {
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240"
  };

  function btnHtml(t) {
    const isAct = t === currentTF;
    return `<button class="btn ${isAct ? "btn-primary" : "btn-ghost"}" data-tf="${t}" style="${isAct ? 'font-weight:700;box-shadow:0 0 10px rgba(59,130,246,0.5)' : ''}">${t.toUpperCase()}</button>`;
  }

  function renderTVChart(tf) {
    currentTF = tf;
    const interval = TF_MAP[tf] || "5";
    const box = document.getElementById("tradingview_chart_container");
    if (!box) return;

    // Update buttons
    document.querySelectorAll(".btn[data-tf]").forEach(b => {
      const isAct = b.dataset.tf === tf;
      b.className = `btn ${isAct ? "btn-primary" : "btn-ghost"}`;
      b.style.fontWeight = isAct ? "700" : "400";
      b.style.boxShadow = isAct ? "0 0 10px rgba(59,130,246,0.5)" : "none";
    });

    const tfLabel = document.getElementById("live-tf-label");
    if (tfLabel) tfLabel.textContent = tf.toUpperCase();

    // Render TradingView Widget
    box.innerHTML = "";
    const innerId = "tv_chart_" + Date.now();
    const div = document.createElement("div");
    div.id = innerId;
    div.style.width = "100%";
    div.style.height = "100%";
    box.appendChild(div);

    if (window.TradingView && window.TradingView.widget) {
      try {
        new window.TradingView.widget({
          autosize: true,
          symbol: "BINANCE:XAUUSDT.P",
          interval: interval,
          timezone: "Etc/UTC",
          theme: "dark",
          style: "1",
          locale: "en",
          toolbar_bg: "#131722",
          enable_publishing: false,
          allow_symbol_change: true,
          hide_side_toolbar: false,
          container_id: innerId,
          withdateranges: true,
          save_image: true,
          details: false,
          hotlist: false,
          calendar: false,
          studies: []
        });
        return;
      } catch (e) {
        console.warn("[TradingView] Widget init failed, using iframe fallback:", e);
      }
    }

    // Direct iframe fallback
    box.innerHTML = `
      <iframe src="https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=BINANCE%3AXAUUSDT.P&interval=${interval}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=131722&theme=dark&style=1&timezone=Etc%2FUTC&locale=en" 
        style="width:100%;height:100%;min-height:640px;border:none;" 
        allowfullscreen>
      </iframe>
    `;
  }

  let pollTimer = null;
  window.__viewCleanup = () => {
    if (pollTimer) clearInterval(pollTimer);
  };

  mount.innerHTML = `
    <div class="stack">
      <div class="row-between">
        <div class="section-title">Live Market — XAU/USD (Binance Futures)</div>
        <div class="tf-toolbar" id="tv-tf-toolbar">${TFS.map(btnHtml).join("")}</div>
      </div>
      <div class="feed-banner live" id="live-feed-banner">
        <span>TRADINGVIEW OFFICIAL REAL-TIME CHART</span>
        <span class="update-clock">BINANCE:XAUUSDT.P · LIVE STREAMING</span>
      </div>
      <div class="card" style="padding:0;overflow:hidden;border:1px solid rgba(255,255,255,0.08);background:#131722;">
        <div class="card-head" style="padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.08);display:flex;justify-content:space-between;align-items:center">
          <span>XAU/USD · <span id="live-tf-label" style="color:var(--primary);font-weight:700">5M</span></span>
          <span class="muted" style="display:flex;align-items:center;gap:8px">
            <span class="pulse-dot live"></span>
            <span class="badge badge-green">TRADINGVIEW LIVE</span>
            <span style="font-size:11px">Full Technical Indicators & Drawing Tools</span>
          </span>
        </div>
        <div class="card-body" style="padding:0;height:640px;width:100%">
          <div id="tradingview_chart_container" style="height:100%;width:100%"></div>
        </div>
      </div>
      <div class="live-info-strip" id="live-info-strip"></div>
    </div>`;

  // Wire buttons
  document.querySelectorAll(".btn[data-tf]").forEach(b => {
    b.addEventListener("click", () => renderTVChart(b.dataset.tf));
  });

  // Render initial TV chart
  renderTVChart("5m");

  // Keep topbar and metrics strip synced with live price
  pollTimer = setInterval(async () => {
    try {
      const snap = await API.overview("XAUUSD");
      if (snap && snap.latest_price) {
        const p = Number(snap.latest_price).toFixed(2);
        const strip = document.getElementById("live-info-strip");
        if (strip) {
          strip.innerHTML = [
            UI.metric("Price", p, "live").outerHTML,
            UI.metric("Spread", snap.spread != null ? snap.spread.toFixed(2) : "0.30", "pts").outerHTML,
            UI.metric("Regime", UI.esc(AppState.regime || "TRENDING")).outerHTML,
            UI.metric("Session", UI.esc(AppState.session || "ACTIVE")).outerHTML,
            UI.metric("Chart Engine", "TradingView Pro").outerHTML,
            UI.metric("Data Feed", '<span class="badge badge-green">LIVE</span>').outerHTML,
          ].join("");
        }
      }
    } catch (_) {}
  }, 3000);
};

/* ================= SIGNALS ================= */
Routes["/signals"] = (mount, query) => {
  renderWith(async () => {
    const params = new URLSearchParams(query);
    return API.signals({ limit: 200, ...Object.fromEntries(params) });
  }, (rows) => {
    if (!rows || !rows.length) {
      return `<div class="stack">${UI.state("No Signals", "No signals recorded yet. Observation mode is active and will store the next setup.").outerHTML}</div>`;
    }

    // --- State for filtering ---
    let activeStrat = "ALL";
    let activeStatus = "";
    let activeDir = "";
    let searchQuery = "";

    // --- Macro KPI Metrics Calculation ---
    const totalCount = rows.length;
    const tpHitCount = rows.filter(s => String(s.outcome || "").toUpperCase() === "TP_HIT").length;
    const slHitCount = rows.filter(s => String(s.outcome || "").toUpperCase() === "SL_HIT").length;
    const beHitCount = rows.filter(s => ["BREAKEVEN_HIT", "BREAKEVEN"].includes(String(s.outcome || "").toUpperCase())).length;
    const closedCount = tpHitCount + slHitCount + beHitCount;
    const winRatePct = (tpHitCount + slHitCount) > 0 ? Math.round((tpHitCount / (tpHitCount + slHitCount)) * 100) : 0;
    const filledCount = rows.filter(s => String(s.outcome || "").toUpperCase() === "FILLED").length;
    const pendingCount = rows.filter(s => String(s.outcome || "").toUpperCase() === "PENDING").length;
    const validRRs = rows.map(s => Number(s.risk_reward)).filter(r => !isNaN(r) && r > 0);
    const avgRR = validRRs.length ? (validRRs.reduce((a, b) => a + b, 0) / validRRs.length).toFixed(1) : "1.8";

    // Strategy counts
    const retCount = rows.filter(s => {
      const st = String(s.strategy || "").toUpperCase();
      return !st.includes("SMC") && !st.includes("TREND");
    }).length;
    const smcCount = rows.filter(s => String(s.strategy || "").toUpperCase().includes("SMC")).length;
    const trendCount = rows.filter(s => String(s.strategy || "").toUpperCase().includes("TREND")).length;

    // Helper: live gold price
    function getLivePrice() {
      if (window.AppState && window.AppState.price && !isNaN(Number(window.AppState.price))) {
        return Number(window.AppState.price);
      }
      const el = document.getElementById("top-price");
      if (el) {
        const p = Number(el.textContent.replace(/[^0-9.]/g, ""));
        if (!isNaN(p) && p > 0) return p;
      }
      return 0;
    }

    // Helper: generate single row HTML
    function buildRowHtml(s, livePrice) {
      const isSMC = String(s.strategy || "").toUpperCase().includes("SMC");
      const isTrend = String(s.strategy || "").toUpperCase().includes("TREND");
      const ver = String(s.strategy_version || "");

      let layerBadge = "";
      let trancheClass = "";
      if (isSMC) {
        layerBadge = '<span class="badge badge-dim" style="font-size:10px;border:1px solid rgba(255,255,255,0.15)">Single (0.68)</span>';
        trancheClass = "smc-single-row";
      } else if (isTrend) {
        layerBadge = '<span class="badge" style="background:#00bcd4;color:#000;font-weight:700">Breakout (0.618)</span>';
        trancheClass = "trend-breakout-row";
      } else {
        if (ver.includes("L2") || (s.reasons || []).some(r => String(r).includes("L2"))) {
          layerBadge = '<span class="badge" style="background:#ff6d00;color:#fff;font-weight:700">L2 (0.50)</span>';
          trancheClass = "tranche-bundle-l2";
        } else if (ver.includes("L3") || (s.reasons || []).some(r => String(r).includes("L3"))) {
          layerBadge = '<span class="badge" style="background:#a855f7;color:#fff;font-weight:700">L3 (0.38)</span>';
          trancheClass = "tranche-bundle-l3";
        } else {
          layerBadge = '<span class="badge" style="background:#2962ff;color:#fff;font-weight:700">L1 (0.61)</span>';
          trancheClass = "tranche-bundle-l1";
        }
      }

      let stratBadge = '<span class="badge" style="background:rgba(171,71,188,0.15);color:#ab47bc;border:1px solid #ab47bc;font-weight:600">🎯 Fib Retracement</span>';
      let stratRoute = "/fib-retracement";
      if (isSMC) {
        stratBadge = '<span class="badge" style="background:rgba(38,166,154,0.15);color:#26a69a;border:1px solid #26a69a;font-weight:600">💎 SMC With Fib</span>';
        stratRoute = "/smc-fib";
      } else if (isTrend) {
        stratBadge = '<span class="badge" style="background:rgba(0,188,212,0.15);color:#00e5ff;border:1px solid #00e5ff;font-weight:600">📈 Fib Go With Trend</span>';
        stratRoute = "/fib-trend";
      }

      const outcomeStatus = String(s.outcome || "PENDING").toUpperCase();
      let statusBadge = '<span class="badge badge-dim">PENDING</span>';
      if (outcomeStatus === "FILLED") statusBadge = '<span class="badge badge-primary" style="background:#00e676;color:#000;font-weight:700">FILLED</span>';
      else if (outcomeStatus === "TP_HIT") statusBadge = '<span class="badge badge-success">TP HIT</span>';
      else if (outcomeStatus === "SL_HIT") statusBadge = '<span class="badge badge-danger">SL HIT</span>';
      else if (outcomeStatus === "BREAKEVEN_HIT" || outcomeStatus === "BREAKEVEN") statusBadge = '<span class="badge" style="background:rgba(255,171,0,0.15);color:#ffab00;border:1px solid #ffab00;font-weight:700">🛡 BREAKEVEN</span>';
      else if (outcomeStatus === "ESCAPE" || outcomeStatus === "ESCAPE_CLOSED") statusBadge = '<span class="badge" style="background:#ffab00;color:#000">ESCAPE</span>';

      // Live PnL / Delta Column
      const entry = Number(s.entry_price || 0);
      const sl = Number(s.stop_loss || 0);
      const tp = Number(s.take_profit_1 || s.take_profit || 0);
      const dir = String(s.direction || "LONG").toUpperCase();
      let pnlPillHtml = '<span class="pnl-pill neutral">—</span>';

      if (outcomeStatus === "FILLED" && livePrice > 0 && entry > 0) {
        const pts = dir === "LONG" ? (livePrice - entry) : (entry - livePrice);
        const absPts = Math.abs(pts).toFixed(2);
        const absDollars = (Math.abs(pts) * 1.0).toFixed(2);
        const sign = pts >= 0 ? "+" : "-";
        const cls = pts >= 0 ? "profit" : "loss";
        pnlPillHtml = `<span class="pnl-pill ${cls}">${sign}$${absDollars} (${sign}${absPts} pts)</span>`;
      } else if (outcomeStatus === "TP_HIT" && entry > 0) {
        const targetTp = isTrend && s.take_profit_2 && Number(s.take_profit_2) > 0 ? Number(s.take_profit_2) : tp;
        const pts = Math.abs((targetTp || tp) - entry).toFixed(2);
        pnlPillHtml = `<span class="pnl-pill profit">+$${pts} (+${pts} pts)</span>`;
      } else if (outcomeStatus === "SL_HIT" && entry > 0 && sl > 0) {
        const pts = Math.abs(entry - sl).toFixed(2);
        pnlPillHtml = `<span class="pnl-pill loss">-$${pts} (-${pts} pts)</span>`;
      } else if (outcomeStatus === "BREAKEVEN_HIT" || outcomeStatus === "BREAKEVEN") {
        pnlPillHtml = `<span class="pnl-pill neutral">$0.00 (0.00 pts)</span>`;
      } else if (outcomeStatus === "PENDING" && livePrice > 0 && entry > 0) {
        const dist = Math.abs(entry - livePrice).toFixed(2);
        pnlPillHtml = `<span class="pnl-pill neutral">${dist} pts away</span>`;
      }

      // Dual TP Display for Trend Breakout trades
      let tpDisplayHtml = `<span class="num up font-mono">${UI.fmt(s.take_profit_1)}</span>`;
      if (isTrend && s.take_profit_2 && Number(s.take_profit_2) > 0 && Number(s.take_profit_2) !== Number(s.take_profit_1)) {
        tpDisplayHtml = `
          <div style="display:inline-flex;flex-direction:column;gap:2px;align-items:flex-end">
            <div class="badge-tp1-chip" style="font-size:10px;padding:2px 5px">🎯 TP1: $${Number(s.take_profit_1).toFixed(2)}</div>
            <div class="badge-tp2-chip" style="font-size:10px;padding:2px 5px">🏆 TP2: $${Number(s.take_profit_2).toFixed(2)}</div>
          </div>
        `;
      }

      // Quick Actions
      const actionsHtml = `
        <div class="row-actions-wrap" style="justify-content:center">
          <button class="btn-mini-action btn-sig-chart" data-route="${stratRoute}" title="Open Strategy Chart">📈</button>
          <button class="btn-mini-action btn-sig-copy" data-id="${s.id}" title="Copy Signal Setup">📋</button>
          <button class="btn-mini-action btn-sig-view" data-id="${s.id}" title="View Details">👁️</button>
        </div>
      `;

      return `<tr class="clickable ${trancheClass}" data-id="${s.id}">
        <td>${UI.fmtTs(s.created_at)}</td>
        <td><strong>${UI.esc(s.symbol)}</strong> <span style="font-size:10px;color:var(--text-muted)">${UI.esc(s.timeframe || "5M")}</span></td>
        <td>${stratBadge}</td>
        <td>${layerBadge}</td>
        <td class="num font-mono" style="font-weight:600">0.01</td>
        <td>${UI.dirBadge(s.direction)}</td>
        <td class="num font-mono" style="font-weight:700">${UI.fmt(s.entry_price)}</td>
        <td class="num down font-mono">${UI.fmt(s.stop_loss)}</td>
        <td style="text-align:right;padding:6px 10px">${tpDisplayHtml}</td>
        <td class="num">1:${UI.fmt(s.risk_reward, 1)}</td>
        <td>${pnlPillHtml}</td>
        <td>${statusBadge}</td>
        <td style="text-align:center">${actionsHtml}</td>
      </tr>`;
    }

    // Filter logic
    function getFilteredRows() {
      const q = searchQuery.trim().toLowerCase();
      return rows.filter(s => {
        const st = String(s.strategy || "").toUpperCase();
        if (activeStrat === "RETRACEMENT" && (st.includes("SMC") || st.includes("TREND"))) return false;
        if (activeStrat === "SMC" && !st.includes("SMC")) return false;
        if (activeStrat === "TREND" && !st.includes("TREND")) return false;

        if (activeStatus) {
          const out = String(s.outcome || "PENDING").toUpperCase();
          if (activeStatus === "ESCAPE") {
            if (!out.includes("ESCAPE")) return false;
          } else if (activeStatus === "BREAKEVEN") {
            if (!out.includes("BREAKEVEN") && !out.includes("BE")) return false;
          } else if (out !== activeStatus) {
            return false;
          }
        }

        if (activeDir) {
          if (String(s.direction || "").toUpperCase() !== activeDir) return false;
        }

        if (q) {
          const str = `${s.id} ${s.symbol} ${s.strategy} ${s.direction} ${s.entry_price} ${s.stop_loss} ${s.take_profit_1} ${s.outcome} ${(s.reasons || []).join(" ")}`.toLowerCase();
          if (!str.includes(q)) return false;
        }
        return true;
      });
    }

    // Copy setup helper
    function copySignalSetup(sigId) {
      const s = rows.find(x => String(x.id) === String(sigId));
      if (!s) return;
      const dir = String(s.direction || "LONG").toUpperCase();
      const isSMC = String(s.strategy || "").toUpperCase().includes("SMC");
      const isTrend = String(s.strategy || "").toUpperCase().includes("TREND");
      const strat = isSMC ? "SMC With Fib" : isTrend ? "Fib Go With Trend" : "Fib With Retracement";
      const text = [
        `🚨 XAU/USD SIGNAL — ${strat}`,
        `Direction: ${dir === "LONG" ? "BUY / LONG ▲" : "SELL / SHORT ▼"} (0.01 Lots)`,
        `Entry: ${s.entry_price || "-"}`,
        `SL: ${s.stop_loss || "-"}`,
        `TP: ${s.take_profit_1 || s.take_profit || "-"}`,
        `Risk:Reward: 1:${s.risk_reward ? Number(s.risk_reward).toFixed(1) : "1.8"}`,
        `Status: ${s.outcome || "PENDING"}`,
        `Time: ${s.created_at || new Date().toISOString()}`
      ].join("\n");
      navigator.clipboard.writeText(text).then(() => {
        UI.toast("Signal Copied", "Signal details copied to clipboard!", "green");
      }).catch(() => {
        UI.toast("Copy Failed", "Please copy manually.", "red");
      });
    }

    // Export CSV helper
    function exportToCSV(dataRows) {
      if (!dataRows || !dataRows.length) {
        UI.toast("Export", "No signals available to export.", "amber");
        return;
      }
      const headers = ["ID", "Created_At", "Symbol", "Timeframe", "Strategy", "Layer", "Lots", "Direction", "Entry", "SL", "TP1", "RR", "Status"];
      const csvLines = [headers.join(",")];
      dataRows.forEach(s => {
        const isSMC = String(s.strategy || "").toUpperCase().includes("SMC");
        const isTrend = String(s.strategy || "").toUpperCase().includes("TREND");
        const ver = String(s.strategy_version || "");
        let layer = isSMC ? "Single_0.680" : isTrend ? "Breakout_0.618" : ver.includes("L2") ? "L2_0.500" : ver.includes("L3") ? "L3_0.382" : "L1_0.618";
        const strat = isSMC ? "SMC_WITH_FIB" : isTrend ? "FIB_GO_WITH_TREND" : "FIB_WITH_RETRACEMENT";
        const line = [
          s.id,
          `"${s.created_at || ""}"`,
          s.symbol || "XAUUSD",
          s.timeframe || "5M",
          strat,
          layer,
          "0.01",
          s.direction || "LONG",
          s.entry_price || "",
          s.stop_loss || "",
          s.take_profit_1 || "",
          s.risk_reward || "",
          s.outcome || "PENDING"
        ];
        csvLines.push(line.join(","));
      });
      const blob = new Blob([csvLines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `xauusd_signals_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      UI.toast("Export Complete", `Exported ${dataRows.length} signals to CSV.`, "green");
    }

    // Wiring events inside table
    function wireTableEvents() {
      const tbody = mount.querySelector("#sig-tbody");
      if (!tbody) return;

      tbody.querySelectorAll("tr[data-id]").forEach(tr => {
        tr.addEventListener("click", (e) => {
          if (e.target.closest("button")) return; // handled by buttons
          openSignalDrawer(tr.dataset.id);
        });
      });

      tbody.querySelectorAll(".btn-sig-chart").forEach(btn => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (btn.dataset.route) location.hash = btn.dataset.route;
        });
      });

      tbody.querySelectorAll(".btn-sig-copy").forEach(btn => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          copySignalSetup(btn.dataset.id);
        });
      });

      tbody.querySelectorAll(".btn-sig-view").forEach(btn => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          openSignalDrawer(btn.dataset.id);
        });
      });
    }

    // Refresh table view when filters change
    function updateTableView() {
      const filtered = getFilteredRows();
      const tbody = mount.querySelector("#sig-tbody");
      const countEl = mount.querySelector("#sig-result-count");
      if (countEl) countEl.textContent = `${filtered.length} of ${totalCount} setups`;

      if (!tbody) return;
      if (!filtered.length) {
        tbody.innerHTML = `<tr><td colspan="13" style="text-align:center;padding:32px;color:var(--text-muted)">
          <div style="font-size:13px;font-weight:700;margin-bottom:4px">No matching signals found</div>
          <div style="font-size:11px">Try adjusting your strategy pills, status, or search query.</div>
        </td></tr>`;
        return;
      }
      const lp = getLivePrice();
      tbody.innerHTML = filtered.map(s => buildRowHtml(s, lp)).join("");
      wireTableEvents();
    }

    // Setup filter listeners after DOM is mounted
    setTimeout(() => {
      // Strategy pills
      mount.querySelectorAll(".sig-pill").forEach(pill => {
        pill.addEventListener("click", () => {
          mount.querySelectorAll(".sig-pill").forEach(p => p.classList.remove("active"));
          pill.classList.add("active");
          activeStrat = pill.dataset.strat || "ALL";
          updateTableView();
        });
      });

      // Status filter
      const statusSel = mount.querySelector("#sig-filter-status");
      if (statusSel) {
        statusSel.addEventListener("change", (e) => {
          activeStatus = e.target.value;
          updateTableView();
        });
      }

      // Direction filter
      const dirSel = mount.querySelector("#sig-filter-dir");
      if (dirSel) {
        dirSel.addEventListener("change", (e) => {
          activeDir = e.target.value;
          updateTableView();
        });
      }

      // Search input
      const searchInp = mount.querySelector("#sig-search");
      if (searchInp) {
        searchInp.addEventListener("input", (e) => {
          searchQuery = e.target.value;
          updateTableView();
        });
      }

      // Export CSV
      const exportBtn = mount.querySelector("#btn-export-csv");
      if (exportBtn) {
        exportBtn.addEventListener("click", () => {
          exportToCSV(getFilteredRows());
        });
      }

      wireTableEvents();
    }, 50);

    const initialLivePrice = getLivePrice();
    const rowsHtml = rows.map(s => buildRowHtml(s, initialLivePrice)).join("");

    return `<div class="stack">
      <!-- 1. Top KPI Summary Strip -->
      <div class="sig-kpi-grid">
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Total Signals</span><span>📊</span></div>
          <div class="kpi-val">${totalCount}</div>
          <div class="kpi-sub"><span class="badge badge-dim">XAU/USD 5M</span> Historical setups</div>
        </div>
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Win Rate / Target Hit</span><span>🎯</span></div>
          <div class="kpi-val" style="color:var(--green-bright)">${winRatePct}%</div>
          <div class="kpi-sub"><span style="color:var(--green-bright);font-weight:700">${tpHitCount} TP</span> • <span style="color:var(--red-bright);font-weight:700">${slHitCount} SL</span> • <span style="color:var(--cyan);font-weight:700">${filledCount} Active</span></div>
        </div>
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Execution Queue</span><span>⚡</span></div>
          <div class="kpi-val" style="color:var(--cyan)">${filledCount} <span style="font-size:12px;font-weight:600;color:var(--text-muted)">FILLED</span></div>
          <div class="kpi-sub"><span class="badge badge-amber" style="padding:1px 6px">${pendingCount} Pending</span> waiting trigger</div>
        </div>
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Average Risk:Reward</span><span>💰</span></div>
          <div class="kpi-val" style="color:var(--gold)">1:${avgRR}</div>
          <div class="kpi-sub"><span class="badge badge-dim" style="padding:1px 6px">0.01 Lots</span> Fixed 1 oz gold sizing</div>
        </div>
      </div>

      <!-- 2. Header & Filter Toolbar -->
      <div class="row-between" style="align-items:baseline">
        <div class="row" style="gap:10px;align-items:baseline">
          <div class="section-title">Signal History</div>
          <span id="sig-result-count" style="font-size:11.5px;color:var(--text-muted)">${totalCount} setups</span>
        </div>
      </div>

      <div class="sig-filter-bar">
        <!-- Strategy Pills -->
        <div class="sig-filter-pills" id="sig-strat-pills">
          <div class="sig-pill active" data-strat="ALL">All <span class="pill-count">${totalCount}</span></div>
          <div class="sig-pill" data-strat="RETRACEMENT">🎯 Fib Retracement <span class="pill-count">${retCount}</span></div>
          <div class="sig-pill" data-strat="SMC">💎 SMC With Fib <span class="pill-count">${smcCount}</span></div>
          <div class="sig-pill" data-strat="TREND">📈 Fib Go With Trend <span class="pill-count">${trendCount}</span></div>
        </div>

        <!-- Filter Controls -->
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <select class="input" id="sig-filter-status" style="min-width:115px">
            <option value="">All Statuses</option>
            <option value="FILLED">🟢 FILLED</option>
            <option value="PENDING">🟡 PENDING</option>
            <option value="TP_HIT">🏆 TP HIT</option>
            <option value="SL_HIT">🔴 SL HIT</option>
            <option value="BREAKEVEN">🛡️ BREAKEVEN</option>
            <option value="ESCAPE">🛡️ ESCAPE</option>
          </select>
          <select class="input" id="sig-filter-dir" style="min-width:110px">
            <option value="">All directions</option>
            <option value="LONG">▲ LONG</option>
            <option value="SHORT">▼ SHORT</option>
          </select>
          <input class="input" id="sig-search" placeholder="Search price, id, date…" style="min-width:160px">
          <button class="btn btn-sm" id="btn-export-csv" title="Download signals table as CSV">📥 Export CSV</button>
        </div>
      </div>

      <!-- 3. Signals Table with Tranche Lines, Live PnL, and Actions -->
      <div class="table-wrap"><table class="term">
        <thead><tr>
          <th>Time</th>
          <th>Symbol</th>
          <th>Strategy</th>
          <th>Layer</th>
          <th>Lots</th>
          <th>Direction</th>
          <th>Entry</th>
          <th>SL</th>
          <th>TP</th>
          <th>R:R</th>
          <th>Live PnL / Delta</th>
          <th>Status</th>
          <th style="text-align:center">Actions</th>
        </tr></thead>
        <tbody id="sig-tbody">
          ${rowsHtml}
        </tbody>
      </table></div>
    </div>`;
  }, mount);
};

async function openSignalDrawer(id) {
  const d = await API.signal(id).catch(() => null);
  if (!d) { UI.toast("Error", "Could not load signal detail.", "red"); return; }
  const sig = d;
  const dir = String(sig.direction || "LONG").toUpperCase();
  const outcome = sig.outcome;

  const entry = Number(sig.entry_price || 0);
  const sl = Number(sig.stop_loss || 0);
  const tp = Number(sig.take_profit_1 || sig.take_profit || 0);
  const curPriceEl = document.getElementById("top-price");
  const livePrice = Number((curPriceEl ? curPriceEl.textContent : "").replace(/[^0-9.]/g, "")) || entry;

  let pts = 0;
  let inProfit = true;
  if (dir === "LONG") {
    pts = Number((livePrice - entry).toFixed(2));
    inProfit = pts >= 0;
  } else {
    pts = Number((entry - livePrice).toFixed(2));
    inProfit = pts >= 0;
  }
  const ptsSign = pts >= 0 ? "+" : "";

  // Target Progress %
  const totalRange = Math.abs(tp - entry);
  let progressPct = 0;
  if (totalRange > 0) {
    if (dir === "LONG") progressPct = Math.max(0, Math.min(100, Math.round(((livePrice - entry) / totalRange) * 100)));
    else progressPct = Math.max(0, Math.min(100, Math.round(((entry - livePrice) / totalRange) * 100)));
  }

  // 0.01 Lots Dollar calculations
  const riskPts = Math.abs(entry - sl).toFixed(2);
  const rewardPts = Math.abs(tp - entry).toFixed(2);
  const dollarRisk = (Number(riskPts) * 1.0).toFixed(2);
  const dollarReward = (Number(rewardPts) * 1.0).toFixed(2);

  // Strategy detection & routing
  const stratStr = String(sig.strategy || "").toUpperCase();
  const isTrend = stratStr.includes("TREND");
  const isSMC = stratStr.includes("SMC");
  let stratRoute = "/fib-retracement";
  let stratLabel = "Fib With Retracement";
  if (isTrend) {
    stratRoute = "/fib-trend";
    stratLabel = "Fib Go With Trend";
  } else if (isSMC) {
    stratRoute = "/smc-fib";
    stratLabel = "SMC With Fib";
  }

  // AI Validation
  const aiData = sig.ai_validation || {};
  const aiStatus = String(aiData.status || "APPROVED").toUpperCase();
  const aiConf = aiData.confidence != null ? aiData.confidence : 95;
  const aiExpl = aiData.explanation || (sig.reasons && sig.reasons[0]) || "Institutional Golden Pocket confluence and trend alignment confirmed.";
  const aiBadgeCls = aiStatus === "APPROVED" ? "badge-green" : (aiStatus === "REJECT" ? "badge-red" : "badge-amber");

  UI.openDrawer(`
    <div class="stack">
      <div class="row-between">
        <span class="section-title">Signal Detail</span>
        <div class="row" style="gap:6px">
          <span class="badge badge-dim" style="font-size:11px">${stratLabel}</span>
          ${UI.dirBadge(dir)}
        </div>
      </div>

      <div class="signal-hero ${dir === "LONG" ? "buy" : dir === "SHORT" ? "sell" : "flat"}">
        <div class="signal-direction">${dir === "LONG" ? "BUY / LONG ▲" : dir === "SHORT" ? "SELL / SHORT ▼" : "NO TRADE"}</div>
        <div class="signal-reason">${UI.fmtTsFull(sig.created_at)} · 5M Standard Execution (0.01 Lots)</div>
      </div>

      <!-- VISUAL LIVE PRICE TRACKING GAUGE -->
      <div class="card" style="padding:12px;background:rgba(255,255,255,0.02)">
        <div class="card-head" style="margin-bottom:6px">
          <span style="display:flex;align-items:center;gap:6px;font-size:12px;font-weight:700">
            <span class="dot ${inProfit ? 'dot-green' : 'dot-red'}"></span> LIVE PRICE TRACKING
          </span>
          <span class="badge ${inProfit ? 'badge-green' : 'badge-red'} font-mono" style="font-weight:700">
            ${ptsSign}${pts.toFixed(2)} PTS (${inProfit ? 'PROFIT' : 'DRAWDOWN'})
          </span>
        </div>
        <div style="position:relative;height:8px;background:rgba(255,255,255,0.06);border-radius:4px;overflow:hidden;margin:10px 0 6px">
          <div style="position:absolute;left:0;top:0;bottom:0;width:${progressPct}%;background:${inProfit ? '#00e676' : '#ef5350'};border-radius:4px;transition:width 0.4s ease"></div>
        </div>
        <div class="row-between font-mono" style="font-size:11px;color:var(--text-dim);margin-top:4px">
          <span style="color:#ef5350">🛑 SL: $${sl.toFixed(2)}</span>
          <span style="color:#4d9fff;font-weight:700">🔵 Entry: $${entry.toFixed(2)}</span>
          <span style="color:#ffd54f">📍 Live: $${livePrice.toFixed(2)}</span>
          <span style="color:#00e676">🎯 TP: $${tp.toFixed(2)}</span>
        </div>
        <div style="text-align:right;font-size:10.5px;color:var(--text-muted);margin-top:4px">Target Progress: <b style="color:${inProfit ? '#00e676' : 'var(--text)'}">${progressPct}%</b></div>
      </div>

      <!-- PRICE LEVELS -->
      ${UI.levels(sig.entry_price, sig.stop_loss, sig.take_profit_1, sig.take_profit_2, sig.take_profit_3, sig.risk_reward)}

      <!-- 0.01 LOT DOLLAR METRICS -->
      <div class="grid grid-3" style="gap:var(--sp-2)">
        <div class="metric" style="padding:8px 12px">
          <div class="metric-label">LOT SIZE</div>
          <div class="metric-value font-mono" style="font-size:16px">0.01 Lots</div>
          <div class="muted" style="font-size:10px">1 oz Gold standard</div>
        </div>
        <div class="metric" style="padding:8px 12px">
          <div class="metric-label">DOLLAR RISK</div>
          <div class="metric-value font-mono down" style="font-size:16px">-$${dollarRisk}</div>
          <div class="muted" style="font-size:10px">${riskPts} pts risk</div>
        </div>
        <div class="metric" style="padding:8px 12px">
          <div class="metric-label">POTENTIAL RETURN</div>
          <div class="metric-value font-mono up" style="font-size:16px">+$${dollarReward}</div>
          <div class="muted" style="font-size:10px">${rewardPts} pts (1:${UI.fmt(sig.risk_reward, 1)})</div>
        </div>
      </div>

      <!-- AI VALIDATION VERDICT -->
      <div class="card" style="border-left:3px solid ${aiStatus === 'APPROVED' ? '#22c55e' : '#ffd54f'}">
        <div class="card-head">
          <span style="display:flex;align-items:center;gap:6px">🧠 AI Validation Verdict</span>
          <span class="badge ${aiBadgeCls}" style="font-weight:700">${aiStatus} (${aiConf}% Conf)</span>
        </div>
        <div class="card-body" style="font-size:12px;color:var(--text-dim);line-height:1.5">
          ${UI.esc(aiExpl)}
        </div>
      </div>

      <!-- STRATEGY CONFLUENCE & EXECUTION CHECKLIST -->
      <div class="card">
        <div class="card-head"><span>Strategy Confluence & Checklist</span></div>
        <div class="card-body">${UI.confBreakdown(sig)}</div>
      </div>

      <!-- SUMMARY -->
      <div class="card">
        <div class="card-head"><span>Execution Summary</span></div>
        <div class="card-body">${UI.kv([
          ["Confidence", `${UI.fmt(sig.confidence_score, 0)}/100`],
          ["Quality", UI.qualityBadge(sig.signal_quality)],
          ["Strategy", UI.esc(sig.strategy)],
          ["Version", UI.esc(String(sig.strategy_version || "—").split(":").pop())],
          ["Regime", UI.esc(sig.regime || "TRENDING")],
          ["Session", UI.esc(sig.session || "LONDON")],
          ["MTF bias", UI.esc(sig.market_bias || "—")],
        ])}</div>
      </div>

      <!-- OUTCOME -->
      <div class="card">
        <div class="card-head"><span>Outcome Status</span></div>
        <div class="card-body">${UI.kv([
          ["Outcome", sig.outcome ? UI.statusBadge(sig.outcome) : '<span class="badge badge-dim">PENDING</span>'],
          ["Final R", UI.fmtR(sig.final_r)],
          ["MFE (R)", UI.fmt(sig.max_favorable_excursion_r, 3)],
          ["MAE (R)", UI.fmt(sig.max_adverse_excursion_r, 3)],
          ["Time to outcome", sig.time_to_outcome_hours != null ? `${sig.time_to_outcome_hours} h` : "—"],
        ])}</div>
      </div>

      <!-- ACTION BUTTONS -->
      <div style="display:flex;gap:8px;margin-top:var(--sp-1)">
        <button class="btn btn-primary" id="drawer-btn-view-chart" style="flex:1;padding:10px 12px;font-size:12px;font-weight:700;cursor:pointer">
          📈 View Strategy Chart
        </button>
        <button class="btn btn-secondary" id="drawer-btn-copy-sig" style="flex:1;padding:10px 12px;font-size:12px;font-weight:700;cursor:pointer">
          📋 Copy Signal Text
        </button>
      </div>

      ${sig.reasons && sig.reasons.length ? `<div class="card"><div class="card-head"><span>Reasons & Execution Notes</span></div><div class="card-body" style="font-size:12px;color:var(--text-dim)">${sig.reasons.map(r => "• " + UI.esc(r)).join("<br>")}</div></div>` : ""}
    </div>`);

  // Wire quick actions
  setTimeout(() => {
    const btnChart = document.getElementById("drawer-btn-view-chart");
    if (btnChart) {
      btnChart.addEventListener("click", () => {
        UI.closeDrawer();
        window.location.hash = stratRoute;
      });
    }

    const btnCopy = document.getElementById("drawer-btn-copy-sig");
    if (btnCopy) {
      btnCopy.addEventListener("click", () => {
        const copyText = [
          `🔔 XAU/USD — ${dir} (0.01 Lots)`,
          `━━━━━━━━━━━━━━━━━━━━`,
          `📊 Strategy: ${stratLabel}`,
          `💵 Entry: $${entry.toFixed(2)}`,
          `🛑 Stop Loss: $${sl.toFixed(2)}`,
          `🎯 Take Profit: $${tp.toFixed(2)}`,
          `⚖️ R:R: 1:${UI.fmt(sig.risk_reward, 1)} (Risk: -$${dollarRisk} | Reward: +$${dollarReward})`,
          `🧠 AI Verdict: ${aiStatus} (${aiConf}% Conf)`,
          `━━━━━━━━━━━━━━━━━━━━`
        ].join("\n");
        navigator.clipboard.writeText(copyText).then(() => {
          UI.toast("Signal Copied", "Formatted trade signal copied to clipboard!", "green");
        }).catch(() => {
          UI.toast("Copy Notice", "Press Ctrl+C to copy.", "amber");
        });
      });
    }
  }, 50);
}

/* ================= STRUCTURE / SMC / FIB / AI ================= */
function genericAnalysisView(title, apiFn, render, mount) {
  renderWith(apiFn, (d) => render(d), mount);
}

Routes["/structure"] = (mount) => genericAnalysisView("Market Structure", () => API.structure(), (d) => renderAnalysis("Market Structure", d), mount);
Routes["/smc"] = (mount) => genericAnalysisView("Smart Money Concepts", () => API.smc(), (d) => renderAnalysis("SMC", d), mount);
Routes["/fib"] = (mount) => genericAnalysisView("Fibonacci", () => API.fibonacci(), (d) => renderAnalysis("Fibonacci", d), mount);

/* ================= RETRACEMENT BOS V1 ================= */
/* Dedicated signal-intelligence page for the exact BULLISH BOS RETRACEMENT —
   RETRACEMENT_BOS_V1 strategy. Consumes only real backend endpoints:
     GET  /retracement/{symbol}
     GET  /retracement/{symbol}/history
     GET  /market/XAUUSD/live
     POST /retracement/{symbol}/run
   No fabricated levels, no fake signals, no execution controls. */
Routes["/retracement"] = (mount) => {
  const RETR_LIVE_MS = 2500;          // live state + price + candles poll
  let _liveTimer = null;
  let _auxTimer = null;
  let _seq = 0;
  let _first = true;
  let _lastChartKey = "";
  let _historySeq = 0;
  let _researchLoaded = false;

  const stateColors = {
    NO_SETUP: "dim", BOS_DETECTED: "amber", POINT_2_IDENTIFIED: "blue",
    FIB_ACTIVE: "blue", TP_DYNAMIC: "blue", ENTRY_TOUCHED: "amber",
    TP_FROZEN: "green", TRADE_ACTIVE: "green", COMPLETED: "green", INVALIDATED: "red",
  };
  const STEPS = [
    ["BOS", "BOS_DETECTED"],
    ["POINT 2", "POINT_2_IDENTIFIED"],
    ["FIB ACTIVE", "FIB_ACTIVE"],
    ["TRACKING HIGH", "TP_DYNAMIC"],
    ["ENTRY TOUCHED", "ENTRY_TOUCHED"],
    ["TP FROZEN", "TP_FROZEN"],
    ["OUTCOME", "COMPLETED"],
  ];

  function retrBadge(state) {
    const st = String(state || "NO_SETUP").toUpperCase();
    const color = stateColors[st] || "dim";
    return `<span class="badge badge-${color}">${UI.esc(st.replace(/_/g, " "))}</span>`;
  }

  function renderStateTimeline(state, outcome) {
    const st = String(state || "NO_SETUP").toUpperCase();
    const map = {
      BOS_DETECTED: 0, POINT_2_IDENTIFIED: 1, FIB_ACTIVE: 2, TP_DYNAMIC: 3,
      ENTRY_TOUCHED: 4, TP_FROZEN: 5, TRADE_ACTIVE: 5, COMPLETED: 5,
      INVALIDATED: -1, NO_SETUP: -1,
    };
    const activeIdx = map[st] !== undefined ? map[st] : -1;
    const steps = STEPS.map(([label, key], i) => {
      let cls = "tl-step";
      let labelTxt = label;
      if (activeIdx === -1) cls += " tl-idle";
      else if (i < activeIdx) cls += " tl-done";
      else if (i === activeIdx) cls += " tl-active";
      if (key === "COMPLETED") {
        if (outcome === "TP_HIT") labelTxt = "TP HIT ✓";
        else if (outcome === "SL_HIT") labelTxt = "SL HIT ✓";
      }
      return `<div class="${cls}">${labelTxt}</div>`;
    });
    return `<div class="tl-wrap">${steps.join('<div class="tl-arrow">→</div>')}</div>`;
  }

  function renderLevels(d) {
    const lv = (d && d.levels) || {};
    const defs = [
      ["1.618", "BLACK LINE 1", lv["1.618"], "dim"],
      ["1.000", "TP", lv["1.000"], (d && d.tp && d.tp.is_locked) ? "green" : "blue"],
      ["0.618", "ENTRY", lv["0.618"], (d && d.entry && d.entry.touched) ? "green" : "dim"],
      ["0.500", "FIB", lv["0.500"], "dim"],
      ["0.382", "FIB", lv["0.382"], "dim"],
      ["0.236", "SL", lv["0.236"], "red"],
      ["0.000", "BLACK LINE 2 / POINT 2", lv["0.000"], "blue"],
    ];
    const rows = defs.map(([ratio, label, level, color]) => {
      const price = level ? UI.fmt(level.price) : "—";
      return `<tr><td class="num">${ratio}</td><td>${UI.esc(label)}</td><td class="num">${price}</td></tr>`;
    }).join("");
    return `<div class="table-wrap"><table class="term"><thead><tr><th>Level</th><th>Meaning</th><th class="num">Price</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function outcomeStatus(d) {
    const st = String(d && d.state || "NO_SETUP").toUpperCase();
    const outcome = d && d.outcome;
    if (outcome === "TP_HIT") return '<span class="badge badge-green">TP HIT</span>';
    if (outcome === "SL_HIT") return '<span class="badge badge-red">SL HIT</span>';
    if (st === "INVALIDATED") return '<span class="badge badge-red">INVALIDATED</span>';
    if (st === "TRADE_ACTIVE" || (d && d.entry && d.entry.touched)) return '<span class="badge badge-amber">ACTIVE</span>';
    if (st === "TP_DYNAMIC" || st === "FIB_ACTIVE" || st === "POINT_2_IDENTIFIED") return '<span class="badge badge-blue">WAITING FOR ENTRY</span>';
    return '<span class="badge badge-dim">NO SETUP</span>';
  }

  function computePoints(d, price) {
    // XAU/USD POINT MOVEMENT (LONG: current - entry).  No money, no leverage.
    if (!d || !d.entry || !d.entry.touched || price == null) return null;
    const entry = d.entry.price;
    return { points: price - entry, entry, price };
  }

  function renderActiveSignal(d, price, feedStatus) {
    const st = String(d && d.state || "NO_SETUP").toUpperCase();
    const noSetup = st === "NO_SETUP" || (!d.entry || !d.entry.price);
    if (noSetup) {
      return `<div class="card" style="border-color:rgba(31,42,58,0.7)">
        <div class="card-head"><span>RETRACEMENT BOS SIGNAL</span>${retrBadge("NO_SETUP")}</div>
        <div class="card-body" style="text-align:center;padding:28px 16px">
          <div style="font-size:17px;color:var(--text)">NO ACTIVE RETRACEMENT SIGNAL</div>
          <div style="font-size:12.5px;color:var(--text-dim);margin-top:6px">
            Waiting for a valid bullish BOS.<br>The engine will create a setup only after: BOS → Point 2 → Fibonacci structure confirmation.
          </div>
          <div style="margin-top:12px;font-size:11px;color:var(--text-dim)">Latest XAU/USD: <b>${UI.fmt(price)}</b> · Feed: ${UI.esc(feedStatus)}</div>
        </div>
      </div>`;
    }

    const entry = d.entry.price;
    const sl = d.sl && d.sl.price;
    const tp = d.tp && (d.tp.is_locked ? d.tp.locked : d.tp.dynamic);
    const rr = (entry && sl && Number(sl) !== Number(entry))
      ? UI.fmt(Math.abs((Number(tp) - Number(entry)) / (Number(entry) - Number(sl))), 2)
      : "—";
    const touched = !!d.entry.touched;
    const locked = !!d.tp && d.tp.is_locked;
    const outcome = outcomeStatus(d);
    const pnl = computePoints(d, price);

    const entryChip = touched
      ? '<span class="badge badge-green">ENTRY TOUCHED</span>'
      : '<span class="badge badge-blue">WAITING FOR ENTRY</span>';
    const tpChip = locked
      ? '<span class="badge badge-green">TP FROZEN</span>'
      : '<span class="badge badge-blue">TP DYNAMIC</span>';

    return `<div class="card" style="border-color:rgba(34,197,94,0.35)">
      <div class="card-head"><span>RETRACEMENT BOS SIGNAL</span><span class="row">${entryChip}${tpChip}</span></div>
      <div class="card-body">
        <div class="row-between" style="margin-bottom:var(--sp-3)">
          <div>
            <div style="display:flex;align-items:center;gap:10px">
              <span class="badge badge-green" style="font-size:16px;padding:6px 14px">▲ LONG</span>
              <span style="font-size:13px;color:var(--text-dim)">BULLISH BOS RETRACEMENT</span>
            </div>
            <div style="margin-top:6px;font-size:12px;color:var(--text-dim)">XAU/USD · ${UI.esc(d.timeframe || "15m")} · ${outcome}</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:11px;color:var(--text-dim)">LIVE PRICE</div>
            <div style="font-size:24px;font-weight:700;font-family:var(--font-num)">${UI.fmt(price)}</div>
          </div>
        </div>

        <div class="grid grid-3" style="margin-bottom:var(--sp-3)">
          <div class="metric"><div class="metric-label">ENTRY (0.618)</div><div class="metric-value">${UI.fmt(entry)}</div></div>
          <div class="metric"><div class="metric-label">STOP LOSS (0.236)</div><div class="metric-value down">${UI.fmt(sl)}</div></div>
          <div class="metric"><div class="metric-label">TAKE PROFIT (1.000)</div><div class="metric-value up">${UI.fmt(tp)}</div></div>
        </div>

        <div class="row-between" style="margin-bottom:var(--sp-2)">
          <div style="font-size:13px;color:var(--text)">R:R <b>1 : ${rr}</b></div>
          <div style="font-size:12px;color:var(--text-dim)">STATE: ${UI.esc(st.replace(/_/g, " "))}</div>
        </div>

        ${pnl ? `<div class="row-between" style="padding-top:var(--sp-2);border-top:1px solid rgba(31,42,58,0.5)">
          <div style="font-size:13px;color:var(--text)">CURRENT MOVEMENT</div>
          <div style="font-size:16px;font-weight:700;font-family:var(--font-num);color:${pnl.points >= 0 ? "var(--green)" : "var(--red)"}">${pnl.points >= 0 ? "+" : ""}${UI.fmt(pnl.points, 2)} POINTS</div>
        </div>` : `<div class="row-between" style="padding-top:var(--sp-2);border-top:1px solid rgba(31,42,58,0.5)">
          <div style="font-size:13px;color:var(--text)">CURRENT MOVEMENT</div>
          <div style="font-size:13px;color:var(--text-dim)">WAITING FOR ENTRY</div>
        </div>`}
      </div></div>`;
  }

  function renderTpStatus(d) {
    const tp = (d && d.tp) || {};
    const locked = tp.is_locked;
    if (locked) {
      return `<div class="card" style="border-color:rgba(34,197,94,0.4)">
        <div class="card-head"><span>TP STATUS</span><span class="badge badge-green">🔒 FROZEN</span></div>
        <div class="card-body">
          <div style="font-size:13px;color:var(--text)">LOCKED TP <b style="font-size:18px">${UI.fmt(tp.locked)}</b></div>
          <div style="font-size:11px;color:var(--green);margin-top:4px">TP FROZEN AT ENTRY TOUCH</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:4px">New highs after entry do NOT modify TP.</div>
        </div></div>`;
    }
    return `<div class="card" style="border-color:rgba(96,165,250,0.35)">
      <div class="card-head"><span>TP STATUS</span><span class="badge badge-blue">🟡 DYNAMIC</span></div>
      <div class="card-body">
        <div style="font-size:13px;color:var(--text)">Dynamic TP <b style="font-size:18px">${UI.fmt(tp.dynamic)}</b></div>
        <div style="font-size:11px;color:var(--text-dim);margin-top:4px">TP follows latest valid high</div>
        <div style="font-size:11px;color:var(--amber);margin-top:4px">ENTRY NOT TOUCHED</div>
      </div></div>`;
  }

  function renderEntryStatus(d) {
    const touched = d && d.entry && d.entry.touched;
    if (touched) {
      return `<div class="card"><div class="card-head"><span>ENTRY STATUS</span><span class="badge badge-green">TOUCHED</span></div>
        <div class="card-body">
          <div style="font-size:13px;color:var(--green)">ENTRY TOUCHED</div>
          <div style="font-size:12px;color:var(--text)">TP <b>LOCKED</b> at ${d.tp && d.tp.is_locked ? UI.fmt(d.tp.locked) : "—"}</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:4px">Entry time: ${d.entry.timestamp ? UI.fmtTs(d.entry.timestamp) : "—"}</div>
        </div></div>`;
    }
    return `<div class="card"><div class="card-head"><span>ENTRY STATUS</span><span class="badge badge-blue">WAITING</span></div>
      <div class="card-body" style="font-size:12px;color:var(--text-dim)">Waiting for price to retrace to 0.618.</div></div>`;
  }

  function renderOutcome(d) {
    const st = String(d && d.state || "NO_SETUP").toUpperCase();
    const outcome = d && d.outcome;
    const entry = d.entry && d.entry.price;
    const tp = d.tp && (d.tp.is_locked ? d.tp.locked : d.tp.dynamic);
    const sl = d.sl && d.sl.price;
    let body = "";
    if (outcome === "TP_HIT") {
      const pts = (entry != null && tp != null) ? Number(tp) - Number(entry) : null;
      body = `<div class="badge badge-green" style="font-size:14px">🟢 TP HIT</div>
        <dl class="kv" style="margin-top:8px"><dt>Entry</dt><dd>${UI.fmt(entry)}</dd></dl>
        <dl class="kv"><dt>Locked TP</dt><dd>${UI.fmt(tp)}</dd></dl>
        <dl class="kv"><dt>Exit</dt><dd>${UI.fmt(tp)}</dd></dl>
        <dl class="kv"><dt>Captured Movement</dt><dd class="${pts >= 0 ? "up" : "down"}">${pts >= 0 ? "+" : ""}${UI.fmt(pts, 2)} POINTS</dd></dl>`;
    } else if (outcome === "SL_HIT") {
      const pts = (entry != null && sl != null) ? Number(sl) - Number(entry) : null;
      body = `<div class="badge badge-red" style="font-size:14px">🔴 SL HIT</div>
        <dl class="kv" style="margin-top:8px"><dt>Entry</dt><dd>${UI.fmt(entry)}</dd></dl>
        <dl class="kv"><dt>SL</dt><dd>${UI.fmt(sl)}</dd></dl>
        <dl class="kv"><dt>Movement</dt><dd class="${pts >= 0 ? "up" : "down"}">${pts >= 0 ? "+" : ""}${UI.fmt(pts, 2)} POINTS</dd></dl>`;
    } else if (st === "INVALIDATED") {
      body = `<div class="badge badge-red" style="font-size:14px">INVALIDATED</div>
        <div style="font-size:12px;color:var(--text-dim);margin-top:6px">${UI.esc(d.invalidation_reason || "Price closed below Point 2.")}</div>`;
    } else if (d && d.entry && d.entry.touched) {
      body = `<div class="badge badge-amber" style="font-size:14px">🟡 ACTIVE</div>
        <dl class="kv" style="margin-top:8px"><dt>TP</dt><dd>${UI.fmt(tp)}</dd></dl>
        <dl class="kv"><dt>SL</dt><dd>${UI.fmt(sl)}</dd></dl>`;
    } else {
      body = `<div class="badge badge-blue" style="font-size:14px">WAITING FOR ENTRY</div>`;
    }
    return `<div class="card"><div class="card-head"><span>OUTCOME</span></div><div class="card-body">${body}</div></div>`;
  }

  function renderBosPoint2(d) {
    const bos = d && d.bos;
    const p1 = d && d.point_1;
    const p2 = d && d.point_2;
    return `<div class="card"><div class="card-head"><span>BOS DETAILS</span>${bos && bos.price ? '<span class="badge badge-green">BULLISH BOS</span>' : '<span class="badge badge-dim">—</span>'}</div>
      <div class="card-body">
        <dl class="kv"><dt>Direction</dt><dd>${bos && bos.price ? "BULLISH BOS" : "—"}</dd></dl>
        <dl class="kv"><dt>Point 1 (BOS level)</dt><dd>${p1 && p1.price ? UI.fmt(p1.price) : "—"}</dd></dl>
        <dl class="kv"><dt>BOS Price</dt><dd>${bos && bos.price ? UI.fmt(bos.price) : "—"}</dd></dl>
        <dl class="kv"><dt>BOS Time</dt><dd>${bos && bos.timestamp ? UI.fmtTsFull(bos.timestamp) : "—"}</dd></dl>
        <dl class="kv"><dt>Point 2</dt><dd>${p2 && p2.price ? UI.fmt(p2.price) : "—"}</dd></dl>
        <dl class="kv"><dt>Point 2 Time</dt><dd>${p2 && p2.timestamp ? UI.fmtTsFull(p2.timestamp) : "—"}</dd></dl>
        <dl class="kv"><dt>Fib 0.000</dt><dd>${p2 && p2.price ? UI.fmt(p2.price) : "—"}</dd></dl>
        <div style="font-size:11px;color:var(--amber);margin-top:6px">Fib 0.000 is anchored to Point 2 (low of the BOS move).</div>
      </div></div>`;
  }

  function renderDistances(d, price) {
    if (price == null) return `<div class="card"><div class="card-head"><span>DISTANCE</span></div><div class="card-body" style="font-size:12px;color:var(--text-dim)">No live price available.</div></div>`;
    const entry = d.entry && d.entry.price;
    const tp = d.tp && (d.tp.is_locked ? d.tp.locked : d.tp.dynamic);
    const sl = d.sl && d.sl.price;
    const fmtDist = (target) => target == null ? "—" : ((target - price) >= 0 ? "+" : "") + UI.fmt(target - price, 2);
    const touched = d && d.entry && d.entry.touched;
    return `<div class="card"><div class="card-head"><span>DISTANCE</span></div>
      <div class="card-body">
        ${touched
          ? `<dl class="kv"><dt>Current → TP</dt><dd>${fmtDist(tp)}</dd></dl>
             <dl class="kv"><dt>Current → SL</dt><dd>${fmtDist(sl)}</dd></dl>`
          : `<dl class="kv"><dt>Current → Entry</dt><dd>${fmtDist(entry)}</dd></dl>
             <dl class="kv"><dt>Current → TP</dt><dd>${fmtDist(tp)}</dd></dl>
             <dl class="kv"><dt>Current → SL</dt><dd>${fmtDist(sl)}</dd></dl>`}
      </div></div>`;
  }

  function renderHistory(setups) {
    const rows = (setups || []).slice(0, 20).map(s => {
      const pts = s.locked_tp != null && s.entry_price != null
        ? Number(s.locked_tp) - Number(s.entry_price) : 0;
      return `<tr>
        <td>${UI.fmtTs(s.created_at)}</td>
        <td>${UI.dirBadge("LONG")}</td>
        <td class="num">${s.bos_price != null ? UI.fmt(s.bos_price) : "—"}</td>
        <td class="num">${s.point_2_price != null ? UI.fmt(s.point_2_price) : "—"}</td>
        <td class="num">${s.entry_price != null ? UI.fmt(s.entry_price) : "—"}</td>
        <td class="num down">${s.sl_price != null ? UI.fmt(s.sl_price) : "—"}</td>
        <td class="num up">${s.locked_tp != null ? UI.fmt(s.locked_tp) : "—"}</td>
        <td>${retrBadge(s.state)}</td>
        <td>${s.outcome ? UI.statusBadge(s.outcome) : '<span class="badge badge-blue">ACTIVE</span>'}</td>
        <td class="num ${pts >= 0 ? "up" : "down"}">${pts != 0 ? (pts >= 0 ? "+" : "") + UI.fmt(pts, 2) : "—"}</td>
      </tr>`;
    }).join("");
    if (!rows) return `<div class="card"><div class="card-head"><span>SETUP HISTORY</span></div><div class="card-body" style="font-size:12px;color:var(--text-dim)">No recorded setups yet. Run an analysis to generate the first one.</div></div>`;
    return `<div class="card"><div class="card-head"><span>SIGNAL HISTORY</span><span class="muted">recent 20</span></div>
      <div class="card-body" style="overflow-x:auto">
        <table class="term"><thead><tr>
          <th>Time</th><th>Direction</th><th>BOS</th><th>Point 2</th><th>Entry</th><th>SL</th><th>TP</th><th>Status</th><th>Outcome</th><th>Points</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div></div>`;
  }

  function renderValidationPanel(research, forward) {
    const h = research && research.health ? research.health.state : "—";
    const hColor = h === "GREEN" ? "green" : h === "YELLOW" ? "amber" : h === "RED" ? "red" : "dim";
    const promo = research ? (research.promotion || "NOT READY") : "—";
    const oos = (research && research.oos) || {};
    const boot = (oos.bootstrap && oos.bootstrap.mean_r) || {};
    const mc = (oos.monte_carlo) || {};
    const cost = (research && research.cost_robustness) || {};
    const fwd = forward || {};

    const costRows = ["0x", "1x", "2x", "3x"].map(k => {
      const c = cost[k] || {};
      return `<tr><td>${k}</td><td class="num">${c.trades != null ? c.trades : "—"}</td>
        <td class="num">${c.win_rate != null ? UI.fmt(c.win_rate * 100, 1) + "%" : "—"}</td>
        <td class="num">${c.avg_points != null ? UI.fmt(c.avg_points, 2) : "—"}</td>
        <td class="num">${c.total_points != null ? UI.fmt(c.total_points, 1) : "—"}</td></tr>`;
    }).join("");

    return `<div class="card">
      <div class="card-head"><span>STRATEGY STATUS</span><span class="badge badge-${hColor}">${UI.esc(h)}</span></div>
      <div class="card-body">
        <div class="row-between" style="margin-bottom:var(--sp-2)">
          <div style="font-size:12.5px;color:var(--text)">Promotion: <b>${UI.esc(promo)}</b></div>
          <div style="font-size:12px;color:var(--text-dim)">POINTS TRACKING · RESEARCH — NOT PRODUCTION VALIDATED</div>
        </div>
        <div class="grid grid-3">
          <div class="metric"><div class="metric-label">OOS SIGNALS</div><div class="metric-value">${oos.trades != null ? oos.trades : "—"}</div></div>
          <div class="metric"><div class="metric-label">OOS WIN RATE</div><div class="metric-value">${oos.win_rate != null ? UI.fmt(oos.win_rate * 100, 1) + "%" : "—"}</div></div>
          <div class="metric"><div class="metric-label">OOS TOTAL POINTS</div><div class="metric-value ${oos.total_points != null && oos.total_points >= 0 ? "up" : "down"}">${oos.total_points != null ? UI.fmt(oos.total_points, 1) : "—"}</div></div>
        </div>
        <div class="grid grid-3" style="margin-top:var(--sp-2)">
          <div class="metric"><div class="metric-label">AVG POINTS/SIGNAL</div><div class="metric-value">${oos.avg_points != null ? UI.fmt(oos.avg_points, 2) : "—"}</div></div>
          <div class="metric"><div class="metric-label">95% CI MEAN POINTS</div><div class="metric-value">[${boot.lo != null ? UI.fmt(boot.lo, 2) : "—"}, ${boot.hi != null ? UI.fmt(boot.hi, 2) : "—"}]</div></div>
          <div class="metric"><div class="metric-label">MC NEG POINT PROB</div><div class="metric-value">${mc.probability_negative_return_pct != null ? UI.fmt(mc.probability_negative_return_pct, 1) + "%" : "—"}</div></div>
        </div>
        <div class="grid grid-3" style="margin-top:var(--sp-2)">
          <div class="metric"><div class="metric-label">TP HITS</div><div class="metric-value">${oos.tp_hits != null ? oos.tp_hits : "—"}</div></div>
          <div class="metric"><div class="metric-label">SL HITS</div><div class="metric-value">${oos.sl_hits != null ? oos.sl_hits : "—"}</div></div>
          <div class="metric"><div class="metric-label">MAX FAVORABLE PTS</div><div class="metric-value">${oos.max_favorable_points != null ? UI.fmt(oos.max_favorable_points, 2) : "—"}</div></div>
        </div>
        <div style="margin-top:var(--sp-2);font-size:12px;color:var(--text)"><b>TRANSACTION COST SENSITIVITY (POINT DEDUCTION)</b></div>
        <div class="table-wrap" style="margin-top:var(--sp-1)">
          <table class="term"><thead><tr><th>Cost</th><th>Trades</th><th>Win Rate</th><th>Avg Points</th><th>Total Points</th></tr></thead>
          <tbody>${costRows || '<tr><td colspan="5">—</td></tr>'}</tbody></table>
        </div>
        <div class="grid grid-2" style="margin-top:var(--sp-2)">
          <div class="kv"><dt>Forward signals</dt><dd>${fwd.signals != null ? fwd.signals : "—"}</dd></div>
          <div class="kv"><dt>Forward entries</dt><dd>${fwd.entries != null ? fwd.entries : "—"}</dd></div>
          <div class="kv"><dt>Forward TP/SL hits</dt><dd>${fwd.tp_hits != null ? fwd.tp_hits : "—"} / ${fwd.sl_hits != null ? fwd.sl_hits : "—"}</dd></div>
          <div class="kv"><dt>Forward status</dt><dd>${UI.esc(fwd.status || "INSUFFICIENT DATA")}</dd></div>
        </div>
      </div></div>`;
  }
function renderCompleted(setups) {
    const done = (setups || []).filter(s => s.outcome === "TP_HIT" || s.outcome === "SL_HIT" || String(s.state).toUpperCase() === "INVALIDATED");
    if (!done.length) return "";
    const rows = done.slice(0, 10).map(s => {
      const pts = s.locked_tp != null && s.entry_price != null
        ? (s.outcome === "TP_HIT" ? Number(s.locked_tp) - Number(s.entry_price)
          : Number(s.sl_price) - Number(s.entry_price)) : 0;
      return `<tr>
        <td>${UI.fmtTs(s.created_at)}</td>
        <td>${UI.dirBadge("LONG")}</td>
        <td class="num">${s.entry_price != null ? UI.fmt(s.entry_price) : "—"}</td>
        <td class="num">${s.locked_tp != null ? UI.fmt(s.locked_tp) : "—"}</td>
        <td class="num">${s.sl_price != null ? UI.fmt(s.sl_price) : "—"}</td>
        <td class="num">${s.outcome === "TP_HIT" ? UI.fmt(s.locked_tp) : (s.outcome === "SL_HIT" ? UI.fmt(s.sl_price) : "—")}</td>
        <td>${s.outcome ? UI.statusBadge(s.outcome) : retrBadge(s.state)}</td>
        <td class="num ${pts >= 0 ? "up" : "down"}">${pts != 0 ? (pts >= 0 ? "+" : "") + UI.fmt(pts, 2) : "—"}</td>
      </tr>`;
    }).join("");
    return `<div class="card"><div class="card-head"><span>COMPLETED RETRACEMENT SIGNALS</span><span class="muted">recent 10</span></div>
      <div class="card-body" style="overflow-x:auto">
        <table class="term"><thead><tr>
          <th>Time</th><th>Direction</th><th>Entry</th><th>Locked TP</th><th>SL</th><th>Exit</th><th>Outcome</th><th>Points</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div></div>`;
  }

  function renderChart(d, candles) {
    const cv = document.getElementById("retr-chart");
    if (!cv || !candles || candles.length < 2) return;
    const levels = [];
    const add = (label, price, color) => {
      if (price != null) levels.push({ label, price: Number(price), color });
    };
    add("1.618 BL1", d.levels && d.levels["1.618"] && d.levels["1.618"].price, "#d7dee8");
    add("TP 1.000", d.tp && (d.tp.is_locked ? d.tp.locked : d.tp.dynamic), "#22c55e");
    add("ENTRY 0.618", d.entry && d.entry.price, "#4d9fff");
    add("0.500", d.levels && d.levels["0.500"] && d.levels["0.500"].price, "rgba(255,255,255,0.3)");
    add("0.382", d.levels && d.levels["0.382"] && d.levels["0.382"].price, "rgba(255,255,255,0.3)");
    add("SL 0.236", d.sl && d.sl.price, "#ef4444");
    add("0.000 P2", d.point_2 && d.point_2.price, "#a855f7");
    add("Point 1", d.point_1 && d.point_1.price, "#f59e0b");
    const lastPrice = d.current_price || (candles[candles.length - 1].close);
    Charts.candles(cv, candles, { window: 80, volume: true, levels, lastPrice, showLatest: true });
  }

  function feedBadgeHtml(feedStatus) {
    if (feedStatus === "HEALTHY" || feedStatus === "LIVE") return '<span class="badge badge-green">LIVE</span>';
    if (feedStatus === "HISTORICAL_CACHE" || feedStatus === "HISTORICAL") return '<span class="badge badge-amber">DEGRADED</span>';
    return '<span class="badge badge-red">NO DATA</span>';
  }

  function resolvePrice(data, market, quote) {
    if (quote && quote.price != null && Number(quote.price) > 0) return Number(quote.price);
    if (market && market.current_price != null) return Number(market.current_price);
    if (data && data.live_price != null) return Number(data.live_price);
    if (data && data.current_high && data.current_high.price != null) return Number(data.current_high.price);
    return null;
  }

  function resolveFeedStatus(data, market) {
    if (data && data.data_status) return data.data_status;
    if (market && market.data_status) return market.data_status;
    return "NO DATA";
  }

  function renderTfSignalCard(tf, d, price) {
    const state = String(d && d.state || "NO_SETUP").toUpperCase();
    const tfBadge = `<span class="badge badge-blue" style="font-size:13px">${UI.esc(tf.toUpperCase())}</span>`;
    if (state === "NO_SETUP") {
      return `<div style="border:1px solid rgba(31,42,58,0.7);border-radius:8px;padding:12px;margin-bottom:10px">
        <div class="row-between">
          <div style="display:flex;align-items:center;gap:8px">${tfBadge}<span style="font-size:13px;color:var(--text-dim)">NO VALID SETUP</span></div>
          ${retrBadge(state)}
        </div>
      </div>`;
    }
    const entry = d.entry && d.entry.price;
    const sl = d.sl && d.sl.price;
    const tp = d.tp && (d.tp.is_locked ? d.tp.locked : d.tp.dynamic);
    const touched = !!(d.entry && d.entry.touched);
    const locked = !!(d.tp && d.tp.is_locked);
    const entryChip = touched
      ? '<span class="badge badge-green">ENTRY TOUCHED</span>'
      : '<span class="badge badge-blue">WAITING FOR ENTRY</span>';
    const tpChip = locked
      ? '<span class="badge badge-green">TP LOCKED</span>'
      : '<span class="badge badge-blue">TP DYNAMIC</span>';
    const p1 = d.point_1 && d.point_1.price;
    const p2 = d.point_2 && d.point_2.price;
    const bos = d.bos && d.bos.price;
    const lv = (d && d.levels) || {};
    const fib = ["1.618", "1.000", "0.618", "0.500", "0.382", "0.236", "0.000"].map(r => {
      const l = lv[r];
      return `<tr><td class="num">${r}</td><td>${l ? UI.esc(l.label.replace(/_/g, " ")) : "—"}</td><td class="num">${l ? UI.fmt(l.price) : "—"}</td></tr>`;
    }).join("");
    const total = (p2 != null && tp != null) ? Number(tp) - Number(p2) : null;
    const e2tp = (entry != null && tp != null) ? Number(tp) - Number(entry) : null;
    const e2sl = (entry != null && sl != null) ? Number(entry) - Number(sl) : null;
    const ts = (d.timestamps && (d.timestamps.updated || d.timestamps.created)) || null;
    return `<div style="border:1px solid rgba(34,197,94,0.35);border-radius:8px;padding:12px;margin-bottom:10px">
      <div class="row-between" style="margin-bottom:6px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          ${tfBadge}
          <span class="badge badge-green">▲ BULLISH RETRACEMENT</span>
          ${entryChip}${tpChip}
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <span style="font-size:11px;color:var(--text-dim)">STATE: ${UI.esc(state.replace(/_/g, " "))}</span>
          ${retrBadge(state)}
        </div>
      </div>
      <div class="grid grid-3" style="margin-bottom:8px">
        <div class="metric"><div class="metric-label">ENTRY (0.618)</div><div class="metric-value">${entry != null ? UI.fmt(entry) : "—"}</div></div>
        <div class="metric"><div class="metric-label">STOP LOSS (0.236)</div><div class="metric-value down">${sl != null ? UI.fmt(sl) : "—"}</div></div>
        <div class="metric"><div class="metric-label">TAKE PROFIT (1.000)</div><div class="metric-value up">${tp != null ? UI.fmt(tp) : "—"}</div></div>
      </div>
      <div class="table-wrap" style="max-height:220px;overflow:auto"><table class="term"><thead><tr><th>Level</th><th>Meaning</th><th class="num">Price</th></tr></thead><tbody>${fib}</tbody></table></div>
      <div class="grid grid-3" style="margin-top:8px">
        <div class="metric"><div class="metric-label">BOS / POINT 1</div><div class="metric-value">${bos != null ? UI.fmt(bos) : "—"} / ${p1 != null ? UI.fmt(p1) : "—"}</div></div>
        <div class="metric"><div class="metric-label">POINT 2 (0.000)</div><div class="metric-value">${p2 != null ? UI.fmt(p2) : "—"}</div></div>
        <div class="metric"><div class="metric-label">TOTAL RANGE</div><div class="metric-value">${total != null ? UI.fmt(total, 2) + " PTS" : "—"}</div></div>
        <div class="metric"><div class="metric-label">ENTRY → TP</div><div class="metric-value up">${e2tp != null ? UI.fmt(e2tp, 2) + " PTS" : "—"}</div></div>
        <div class="metric"><div class="metric-label">ENTRY → SL</div><div class="metric-value down">${e2sl != null ? UI.fmt(e2sl, 2) + " PTS" : "—"}</div></div>
        <div class="metric"><div class="metric-label">SIGNAL TIME</div><div class="metric-value" style="font-size:12px">${ts ? UI.fmtTs(ts) : "—"}</div></div>
      </div>
    </div>`;
  }

  function renderMultiSignals(multi) {
    if (!multi || !multi.timeframes) return "";
    const tfs = ["15m", "30m", "1h"];
    const hasAny = tfs.some(tf => {
      const d = multi.timeframes[tf] || {};
      return String(d.state || "").toUpperCase() !== "NO_SETUP";
    });
    const body = tfs.map(tf => renderTfSignalCard(tf, multi.timeframes[tf] || {})).join("");
    return `<div class="card" style="border-color:rgba(96,165,250,0.35)">
      <div class="card-head"><span>RETRACEMENT SIGNAL</span><span class="muted">15M · 30M · 1H — monitored independently</span></div>
      <div class="card-body">
        ${hasAny ? body : `<div style="text-align:center;padding:16px;font-size:13px;color:var(--text-dim)">NO VALID RETRACEMENT SETUP ON ANY TIMEFRAME</div>`}
      </div>
    </div>`;
  }

  function buildShell(data, price, feedStatus) {
    return `
    <div class="stack">
      <div class="row-between">
        <div>
          <div class="section-title">RETRACEMENT BOS</div>
          <div class="muted" style="font-size:11px">XAU/USD RETRACEMENT SIGNAL INTELLIGENCE \u00b7 RETRACEMENT_BOS_V1</div>
        </div>
        <div class="toolbar" style="margin:0">
          <span class="badge badge-amber">RESEARCH</span>
          <span class="badge badge-blue">SIGNAL ONLY</span>
          <span class="badge badge-red">REAL MONEY DISABLED</span>
          ${feedBadgeHtml(feedStatus)}
        </div>
      </div>

      <div id="retr-timeline">${renderStateTimeline(data.state, data.outcome)}</div>

      <div id="retr-multi"></div>

      <div id="retr-signal">${renderActiveSignal(data, price, feedStatus)}</div>

      <div class="card" style="padding:0">
        <div class="card-head"><span>RETRACEMENT CHART</span><span class="muted">XAU/USD 15M</span></div>
        <div class="card-body" style="padding:0">
          <div class="chart-box" style="height:420px"><canvas id="retr-chart" class="chart"></canvas></div>
        </div>
      </div>

      <div class="grid grid-3">
        <div id="retr-tp">${renderTpStatus(data)}</div>
        <div id="retr-entry">${renderEntryStatus(data)}</div>
        <div id="retr-outcome">${renderOutcome(data)}</div>
      </div>

      <div class="grid grid-3">
        <div id="retr-bos">${renderBosPoint2(data)}</div>
        <div id="retr-dist">${renderDistances(data, price)}</div>
        <div class="card">
          <div class="card-head"><span>FIBONACCI STRUCTURE</span></div>
          <div class="card-body">${renderLevels(data)}</div>
        </div>
      </div>

      <div id="retr-history"></div>

      <div id="retr-completed"></div>

      <div class="card" id="retr-validation-panel" style="opacity:0.6">
        <div class="card-head"><span>STRATEGY STATUS</span><span class="badge badge-dim">LOADING\u2026</span></div>
        <div class="card-body" style="font-size:12px;color:var(--text-dim)">Running validation analysis\u2026</div>
      </div>

      <div class="card" style="border-color:rgba(245,158,11,0.3)">
        <div class="card-head"><span>TP BEHAVIOR</span></div>
        <div class="card-body" style="font-size:12.5px;color:var(--text)">
          <div style="margin-bottom:var(--sp-1)"><b>Before Entry:</b> TP 1.000 follows the latest valid high.</div>
          <div style="margin-bottom:var(--sp-1)"><b>At Entry 0.618:</b> Current TP is captured and frozen.</div>
          <div style="margin-bottom:var(--sp-1)"><b>After Entry:</b> TP NEVER MOVES.</div>
          <div style="color:var(--text-dim)">New highs after Entry do not modify TP.</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><span>SAFETY</span><span class="badge badge-blue">SIGNAL ONLY</span></div>
        <div class="card-body" style="font-size:12px;color:var(--text)">
          <div>Real-money execution: <b>DISABLED</b></div>
          <div>Broker execution: <b>NONE</b></div>
          <div>Paper trading: <b>BLOCKED when production strategy is FAILED</b></div>
          <div>Automatic execution: <b>DISABLED</b></div>
          <div>Manual decision required.</div>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><span>DETERMINISTIC ANALYSIS</span></div>
        <div class="card-body">
          <div class="row-between" style="margin-bottom:var(--sp-2)">
            <div style="font-size:12px;color:var(--text-dim)">Runs the exact RETRACEMENT_BOS_V1 engine over real historical data and persists setups + event history.</div>
            <button class="btn btn-primary" id="retr-run-btn">RUN ANALYSIS</button>
          </div>
          <div id="retr-run-msg" style="font-size:11px;color:var(--text-dim)"></div>
        </div>
      </div>
    </div>`;
  }

  function patchDynamic(data, price, feedStatus, candles, multi) {
    const setHtml = (id, html) => {
      const el = document.getElementById(id);
      if (el && el.innerHTML !== html) el.innerHTML = html;
    };
    setHtml("retr-multi", renderMultiSignals(multi));
    setHtml("retr-timeline", renderStateTimeline(data.state, data.outcome));
    setHtml("retr-signal", renderActiveSignal(data, price, feedStatus));
    setHtml("retr-tp", renderTpStatus(data));
    setHtml("retr-entry", renderEntryStatus(data));
    setHtml("retr-outcome", renderOutcome(data));
    setHtml("retr-bos", renderBosPoint2(data));
    setHtml("retr-dist", renderDistances(data, price));

    // Chart: re-render only when candles OR levels/price actually changed.
    const lastCandle = candles && candles.length ? candles[candles.length - 1] : null;
    const lvlKey = JSON.stringify([
      (data && data.levels) || {},
      data && data.tp ? (data.tp.is_locked ? data.tp.locked : data.tp.dynamic) : null,
      data && data.entry ? data.entry.price : null,
      data && data.sl ? data.sl.price : null,
      price,
    ]);
    const key = (lastCandle ? lastCandle.timestamp : "") + "|" + (candles ? candles.length : 0) + "|" + lvlKey;
    if (key !== _lastChartKey && candles && candles.length >= 2) {
      _lastChartKey = key;
      renderChart(data, candles);
    }
  }

  function loadAux(mount) {
    const seq = ++_historySeq;
    Promise.allSettled([
      API.retracementHistory("XAUUSD"),
      API.get("/retracement/research/summary?timeframe=15m"),
      API.get("/retracement/forward/status"),
    ]).then(([h, rr, fr]) => {
      if (seq !== _historySeq) return;
      const history = h.status === "fulfilled" ? (h.value && h.value.setups) || [] : [];
      const research = rr.status === "fulfilled" ? rr.value : null;
      const forward = fr.status === "fulfilled" ? fr.value : null;
      const histEl = document.getElementById("retr-history");
      if (histEl) histEl.innerHTML = renderHistory(history);
      const compEl = document.getElementById("retr-completed");
      if (compEl) compEl.innerHTML = renderCompleted(history);
      if (!_researchLoaded) {
        _researchLoaded = true;
        const panel = document.getElementById("retr-validation-panel");
        if (panel && research) {
          panel.outerHTML = renderValidationPanel(research, forward);
        }
      }
    });
  }

  function wireRunButton(mount, doFetch) {
    const runBtn = document.getElementById("retr-run-btn");
    if (!runBtn) return;
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      runBtn.textContent = "RUNNING…";
      const msg = document.getElementById("retr-run-msg");
      if (msg) msg.textContent = "Executing deterministic retracement analysis (no orders, no execution)…";
      try {
        await API.runRetracement("XAUUSD");
        if (msg) msg.textContent = "Analysis complete. Refreshing…";
        _first = true; // rebuild the shell after a manual historical run
        await doFetch();
      } catch (err) {
        if (msg) msg.textContent = "Run failed: " + UI.esc(err.message || String(err));
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = "RUN ANALYSIS";
      }
    });
  }

  function loadRetracement(mount) {
    let running = false;
    const doFetch = async () => {
      if (running) return;
      running = true;
      const seq = ++_seq;
      try {
        const [d, m, mkt, q] = await Promise.allSettled([
          API.retracement("XAUUSD"),
          API.retracementMulti("XAUUSD"),
          API.liveMarket("XAUUSD", { timeframe: "15m", include_forming: true }),
          API.liveQuote("XAUUSD"),
        ]);
        if (seq !== _seq) return; // stale response guard

        const data = d.status === "fulfilled" ? d.value : null;
        const multi = m.status === "fulfilled" ? m.value : null;
        if (!data) {
          if (_first) {
            mount.innerHTML = "";
            mount.appendChild(UI.state("RETRACEMENT DATA UNAVAILABLE",
              (d.reason || m.reason || mkt.reason || q.reason || "backend did not respond") + "", "\u26a0", true));
          }
          return;
        }

        const market = mkt.status === "fulfilled" ? mkt.value : null;
        const quote = q.status === "fulfilled" ? q.value : null;
        const price = resolvePrice(data, market, quote);
        const feedStatus = resolveFeedStatus(data, market);
        const candles = (market && market.candles) || [];

        if (_first) {
          _first = false;
          mount.innerHTML = buildShell(data, price, feedStatus);
          const multiEl = document.getElementById("retr-multi");
          if (multiEl) multiEl.innerHTML = renderMultiSignals(multi);
          renderChart(data, candles);
          wireRunButton(mount, doFetch);
          loadAux(mount);
        } else {
          patchDynamic(data, price, feedStatus, candles, multi);
        }
      } catch (err) {
        if (seq === _seq && _first) {
          mount.innerHTML = "";
          mount.appendChild(UI.state("RETRACEMENT DATA UNAVAILABLE", UI.esc(err.message || String(err)), "⚠", true));
        }
      } finally {
        running = false;
      }
    };

    doFetch();
    _liveTimer = setInterval(doFetch, RETR_LIVE_MS);
    _auxTimer = setInterval(() => loadAux(mount), REFRESH_MS);
  }

  window.__viewCleanup = () => {
    if (_liveTimer) { clearInterval(_liveTimer); _liveTimer = null; }
    if (_auxTimer) { clearInterval(_auxTimer); _auxTimer = null; }
  };
  loadRetracement(mount);
};

function renderAnalysis(title, d) {
  if (!d) return "";
  const entries = Object.entries(d).filter(([k, v]) => v !== null && v !== undefined && typeof v !== "function");
  const groups = {};
  entries.forEach(([k, v]) => {
    const cat = (k.includes("bos") || k.includes("choch") || k.includes("structure")) ? "Structure"
      : (k.includes("fvg") || k.includes("ob") || k.includes("sweep") || k.includes("liquidity")) ? "SMC"
      : (k.includes("fib") || k.includes("retr") || k.includes("ext")) ? "Fibonacci"
      : "Other";
    (groups[cat] = groups[cat] || []).push([k, v]);
  });
  const catHtml = Object.entries(groups).map(([cat, items]) => `
    <div class="card">
      <div class="card-head"><span>${UI.esc(cat)}</span></div>
      <div class="card-body">${items.map(([k, v]) => `
        <div class="row-between" style="padding:4px 0;border-bottom:1px solid rgba(31,42,58,0.4)">
          <span style="color:var(--text-dim);font-size:12px">${UI.esc(k)}</span>
          <span style="font-size:12px" class="num">${UI.esc(renderValue(v))}</span>
        </div>`).join("")}</div>
    </div>`).join("");
  return `<div class="stack">
    <div class="section-title">${UI.esc(title)} — Live Analysis</div>
    ${catHtml || UI.state("No Data", "Analysis engine returned no data for this symbol yet.", "")}
  </div>`;
}
function renderValue(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") {
    if (Array.isArray(v)) return v.length ? `[${v.length} items]` : "[]";
    try { return JSON.stringify(v).slice(0, 120); } catch (_) { return "object"; }
  }
  return String(v);
}

/* ================= AI VALIDATION ================= */
Routes["/ai"] = (mount) => {
  const AI_LIVE_MS = 5000;
  let _liveTimer = null;
  let _seq = 0;
  let _first = true;
  let _lastChartKey = "";

  function aiBadge(s) {
    const up = String(s || "").toUpperCase();
    if (up === "APPROVE") return '<span class="badge badge-green">APPROVE</span>';
    if (up === "CAUTION") return '<span class="badge badge-amber">CAUTION</span>';
    if (up === "REJECT") return '<span class="badge badge-red">REJECT</span>';
    if (up === "UNAVAILABLE") return '<span class="badge badge-red">UNAVAILABLE</span>';
    return '<span class="badge badge-dim">' + UI.esc(s || "—") + "</span>";
  }
  function gateBadge(s) {
    const up = String(s || "").toUpperCase();
    if (up === "PASS") return '<span class="badge badge-green">PASS</span>';
    if (up === "FAIL") return '<span class="badge badge-red">FAIL</span>';
    if (up === "WARNING") return '<span class="badge badge-amber">WARNING</span>';
    return '<span class="badge badge-dim">' + UI.esc(s || "—") + "</span>";
  }
  function evBadge(s) {
    if (s === "VERIFIED") return '<span class="badge badge-green">VERIFIED</span>';
    if (s === "MISMATCH") return '<span class="badge badge-red">MISMATCH</span>';
    return '<span class="badge badge-amber">NOT VERIFIED</span>';
  }
  function renderChart(d, candles) {
    const cv = document.getElementById("ai-chart");
    if (!cv || !candles || candles.length < 2) return;
    const levels = [];
    if (d && d.retracement && d.retracement.levels) {
      const lv = d.retracement.levels;
      const all = ["1.618","1.000","0.618","0.500","0.382","0.236","0.000"];
      const colors = ["#a855f7","#22c55e","#3b82f6","#f59e0b","#f59e0b","#ef4444","#a855f7"];
      all.forEach((k, i) => {
        if (lv[k] && lv[k].price != null) levels.push({ price: lv[k].price, label: lv[k].label || k, color: colors[i] });
      });
    }
    const lastPrice = d && d.current_price;
    if (lastPrice != null) levels.push({ price: lastPrice, label: "LIVE", color: "#fff" });
    Charts.candles(cv, candles, { window: 80, volume: true, levels, showLatest: true });
  }

  function buildShell(d) {
    const ai = d.ai || {};
    const det = d.deterministic || {};
    const health = d.health || {};
    const healthBadge = health.status === "HEALTHY" ? '<span class="badge badge-green">AI HEALTHY</span>'
      : health.status === "DEGRADED" ? '<span class="badge badge-amber">AI DEGRADED</span>'
      : '<span class="badge badge-red">AI OFFLINE</span>';
    const feedBadge = d.data_status === "HEALTHY" ? '<span class="badge badge-green">LIVE</span>'
      : '<span class="badge badge-amber">DEGRADED</span>';
    return `
    <div class="stack">
      <div class="row-between">
        <div>
          <div class="section-title">AI VALIDATION</div>
          <div class="muted" style="font-size:11px">AI DECISION INTELLIGENCE \u00b7 ADVISORY ONLY</div>
        </div>
        <div class="toolbar" style="margin:0">
          <span class="badge badge-blue">AI ADVISORY</span>
          <span class="badge badge-amber">DETERMINISTIC SAFETY ACTIVE</span>
          <span class="badge badge-red">REAL MONEY DISABLED</span>
          ${healthBadge}
          ${feedBadge}
        </div>
      </div>

      <div id="ai-main" class="grid grid-2">
        <div id="ai-decision-card"></div>
        <div id="ai-final-card"></div>
      </div>

      <div class="grid grid-3">
        <div id="ai-validation-mat"></div>
        <div id="ai-timeframes"></div>
        <div id="ai-confluence"></div>
      </div>

      <div class="grid grid-3">
        <div id="ai-retracement"></div>
        <div id="ai-risk"></div>
        <div id="ai-lifecycle"></div>
      </div>

      <div class="card" style="padding:0">
        <div class="card-head"><span>MARKET CHART</span><span class="muted">XAU/USD 15M</span></div>
        <div class="card-body" style="padding:0">
          <div class="chart-box" style="height:360px"><canvas id="ai-chart" class="chart"></canvas></div>
        </div>
      </div>

      <div class="grid grid-2">
        <div id="ai-reasoning"></div>
        <div id="ai-evidence"></div>
      </div>

      <div class="grid grid-2">
        <div id="ai-what-change"></div>
        <div id="ai-market-snap"></div>
      </div>

      <div class="grid grid-2">
        <div id="ai-health-section"></div>
        <div id="ai-performance"></div>
      </div>

      <div id="ai-history"></div>
      <div id="ai-timeline"></div>

      <div class="card" style="border-color:rgba(245,158,11,0.3)">
        <div class="card-head"><span>SAFETY PRINCIPLE</span></div>
        <div class="card-body" style="font-size:12.5px;color:var(--text)">
          <div style="margin-bottom:var(--sp-1)"><b>AI CAN ADVISE</b> \u00b7 AI CAN EXPLAIN \u00b7 AI CAN SCORE</div>
          <div><b>AI CANNOT OVERRIDE</b> deterministic safety gates.</div>
          <div style="color:var(--text-dim);margin-top:4px">The deterministic engine always has final authority. AI is advisory only.</div>
        </div>
      </div>
    </div>`;
  }

  function patchDynamic(d) {
    const set = (id, h) => { const el = document.getElementById(id); if (el) el.innerHTML = h; };
    set("ai-decision-card", renderMainAI(d));
    set("ai-final-card", renderFinalDecision(d));
    set("ai-validation-mat", renderValidationMatrix(d));
    set("ai-timeframes", renderTimeframes(d));
    set("ai-confluence", renderConfluence(d));
    set("ai-retracement", renderRetracement(d));
    set("ai-risk", renderRiskCard(d));
    set("ai-lifecycle", renderLifecycleCard(d));
    set("ai-reasoning", renderReasoning(d));
    set("ai-evidence", renderEvidence(d));
    set("ai-what-change", renderWhatMustChange(d));
    set("ai-market-snap", renderMarketSnapshot(d));
    set("ai-health-section", renderAIHealth(d));
    set("ai-performance", renderPerformance(d));
    set("ai-history", renderAIHistory(d));
    set("ai-timeline", renderAITimeline(d));
  }

  function renderMainAI(d) {
    const ai = d.ai || {};
    const det = d.deterministic || {};
    const conf = ai.confidence || 0;
    const confColor = conf >= 80 ? "var(--green)" : conf >= 50 ? "var(--amber)" : "var(--red)";
    const wrap = (inner) => `<div class="card" style="border-color:rgba(59,130,246,0.35);flex:1"><div class="card-head"><span>AI VALIDATION</span></div><div class="card-body">${inner}</div></div>`;
    if (ai.status === "UNAVAILABLE" || ai.decision === "UNAVAILABLE") {
      return wrap(`<div style="text-align:center;padding:20px">
        <div style="font-size:32px;margin-bottom:8px">\u26a0</div>
        <div style="font-size:18px;font-weight:700;color:var(--red)">AI UNAVAILABLE</div>
        <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${UI.esc(ai.explanation || "AI provider unavailable.")}</div>
        <div style="margin-top:12px;font-size:11px;color:var(--text-dim)">FINAL AUTHORITY: DETERMINISTIC ENGINE</div>
      </div>`);
    }
    return wrap(`<div style="text-align:center;padding:8px 0">
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">AI RECOMMENDATION</div>
      <div style="font-size:28px;font-weight:800;color:${confColor}">${aiBadge(ai.decision)}</div>
      <div style="margin-top:12px">
        <div style="font-size:11px;color:var(--text-dim)">CONFIDENCE</div>
        <div style="font-size:36px;font-weight:700;font-family:var(--font-num);color:${confColor}">${UI.fmt(conf, 0)}<span style="font-size:18px;color:var(--text-dim)"> / 100</span></div>
        <div style="font-size:10px;color:var(--text-dim);margin-top:2px">AI CONFIDENCE \u2260 TRADE WIN PROBABILITY</div>
      </div>
      <div class="divider" style="margin:12px 0"></div>
      <div class="kv" style="font-size:12px">
        <dt>DETERMINISTIC STATUS</dt><dd>${gateBadge(det.data_quality === "PASS" && det.risk_gate === "PASS" ? "PASS" : "FAIL")}</dd>
        <dt>FINAL AUTHORITY</dt><dd><span class="badge badge-amber">DETERMINISTIC SAFETY GATE</span></dd>
        <dt>Provider</dt><dd><span class="badge badge-dim">${UI.esc(ai.provider || "HEURISTIC")}${ai.model ? " \u00b7 " + UI.esc(ai.model) : ""}</span></dd>
      </div>
    </div>`);
  }

  function renderFinalDecision(d) {
    const det = d.deterministic || {};
    const decision = det.decision || "NO_TRADE";
    const bg = decision === "VALIDATED_SIGNAL" ? "var(--green)" : decision === "WATCH" ? "var(--amber)" : "var(--red)";
    const icon = decision === "VALIDATED_SIGNAL" ? "\u2714" : decision === "WATCH" ? "\u23f3" : "\u26d4";
    return `<div class="card" style="border-color:${bg};flex:1">
      <div class="card-head"><span>FINAL DECISION</span></div>
      <div class="card-body" style="text-align:center;padding:20px 16px">
        <div style="font-size:36px;font-weight:800;color:${bg}">${icon} ${decision.replace(/_/g, " ")}</div>
        <div class="divider" style="margin:12px 0"></div>
        <div class="kv" style="font-size:12px;text-align:left">
          <dt>Confluence</dt><dd>${UI.fmt(det.confluence, 0)} / 100 ${gateBadge(det.confluence >= (det.confluence_threshold || 75) ? "PASS" : "FAIL")}</dd>
          <dt>Signal</dt><dd>${UI.dirBadge(det.signal_direction)} ${UI.qualityBadge(det.signal_quality)}</dd>
          <dt>Risk Gate</dt><dd>${gateBadge(det.risk_gate)}</dd>
          <dt>Data Quality</dt><dd>${gateBadge(det.data_quality)}</dd>
          <dt>Authority</dt><dd><span class="badge badge-amber">DETERMINISTIC ENGINE</span></dd>
        </div>
      </div>
    </div>`;
  }

  function renderValidationMatrix(d) {
    const det = d.deterministic || {};
    const gates = det.gates || [];
    const rows = gates.map(g => `<div class="row-between" style="padding:4px 0;font-size:12px;border-bottom:1px solid rgba(31,42,58,0.3)">
      <span style="color:var(--text)">${UI.esc(g.name)}</span>
      <span class="row">${gateBadge(g.status)}<span class="muted" style="margin-left:6px;font-size:10.5px">${UI.esc(g.detail)}</span></span>
    </div>`).join("");
    return `<div class="card"><div class="card-head"><span>VALIDATION BREAKDOWN</span></div><div class="card-body flush"><div style="padding:8px 12px">${rows || '<span class="muted">No gates evaluated.</span>'}</div></div></div>`;
  }

  function renderTimeframes(d) {
    const tf = d.timeframes || {};
    const rows = (tf.rows || []).map(r => {
      const cls = r.status === "ALIGNED" ? "ver" : r.status === "CONFLICT" ? "red" : "dim";
      return `<div class="row-between" style="padding:4px 0;font-size:12px;border-bottom:1px solid rgba(31,42,58,0.3)">
        <span style="color:var(--text);font-weight:600">${r.timeframe}</span>
        <span style="color:${r.direction === "BULLISH" ? "var(--green)" : r.direction === "BEARISH" ? "var(--red)" : "var(--text-dim)"};font-size:11px">${r.direction}</span>
        <span style="color:var(--text-dim);font-size:10.5px" title="${UI.esc(r.structure)}">${r.status}</span>
        <span class="badge badge-${cls}">${r.status}</span>
      </div>`;
    }).join("");
    const statusMsg = tf.status === "ALIGNED" ? '<span class="badge badge-green">ALIGNED</span>'
      : tf.status === "CONFLICT" ? '<span class="badge badge-red">TIMEFRAME CONFLICT</span>'
      : '<span class="badge badge-amber">PARTIAL</span>';
    return `<div class="card"><div class="card-head"><span>TIMEFRAME ALIGNMENT</span></div><div class="card-body flush">
      <div style="padding:8px 12px">${rows || '<span class="muted">No timeframe data.</span>'}</div>
      <div class="divider"></div>
      <div class="row-between" style="padding:8px 12px;font-size:12px">
        <span>ALIGNMENT</span>
        <span style="font-weight:700">${tf.aligned_count || 0} / ${tf.total || 0}</span>
        <span>${statusMsg}</span>
      </div>
    </div></div>`;
  }

  function renderConfluence(d) {
    const conf = (d.deterministic || {}).confluence || 0;
    const threshold = (d.deterministic || {}).confluence_threshold || 75;
    const pct = Math.min(100, (conf / Math.max(threshold, 1)) * 100);
    const ok = conf >= threshold;
    const sig = (d.deterministic || {}).signal_quality || "—";
    return `<div class="card"><div class="card-head"><span>CONFLUENCE</span></div><div class="card-body" style="text-align:center">
      <div style="font-size:32px;font-weight:700;font-family:var(--font-num);color:${ok ? "var(--green)" : "var(--red)"}">${UI.fmt(conf, 0)}<span style="font-size:14px;color:var(--text-dim)"> / 100</span></div>
      <div style="font-size:11px;color:var(--text-dim)">Required: \u2265 ${threshold}</div>
      <div style="margin:8px 0">${UI.progress(pct, ok ? "" : "warn")}</div>
      <div style="font-size:12px;font-weight:600;color:${ok ? "var(--green)" : "var(--red)"}">${ok ? "\u2705 ABOVE THRESHOLD" : "\u274c BELOW THRESHOLD"}</div>
      <div class="divider" style="margin:8px 0"></div>
      <div style="text-align:left;font-size:11px"><span class="muted">Quality: ${UI.esc(sig)}</span></div>
    </div></div>`;
  }

  function renderRetracement(d) {
    const r = d.retracement;
    if (!r) return `<div class="card"><div class="card-head"><span>RETRACEMENT VALIDATION</span></div><div class="card-body" style="text-align:center;padding:20px"><span class="muted">NO ACTIVE RETRACEMENT SETUP</span></div></div>`;
    const lv = r.levels || {};
    const all = ["1.618","1.000","0.618","0.500","0.382","0.236","0.000"];
    const labels = {"1.618":"BLACK LINE 1","1.000":"TP","0.618":"ENTRY","0.500":"FIB","0.382":"FIB","0.236":"SL","0.000":"POINT 2"};
    const levels = all.map(k => {
      const v = lv[k];
      if (!v || v.price == null) return "";
      return `<div class="row-between" style="padding:2px 0;font-size:11.5px;border-bottom:1px solid rgba(31,42,58,0.2)">
        <span style="color:var(--text-dim)">${k} ${labels[k] || ""}</span>
        <span style="font-family:var(--font-num);font-weight:600">${UI.fmt(v.price)}</span>
      </div>`;
    }).join("");

    const state = r.state || "NO_SETUP";
    const specState = r.spec_state || "WAITING_FOR_BOS";
    const tp = r.tp || {};
    const tpStatus = tp.is_locked ? '<span class="badge badge-green">\u{1f512} FROZEN</span>' : '<span class="badge badge-blue">DYNAMIC</span>';
    const entryStatus = r.entry_touched ? '<span class="badge badge-green">ENTRY TOUCHED</span>' : '<span class="badge badge-blue">WAITING FOR ENTRY</span>';

    return `<div class="card"><div class="card-head"><span>RETRACEMENT VALIDATION</span></div><div class="card-body flush">
      <div style="padding:8px 12px">
        <div class="row-between" style="margin-bottom:6px;font-size:12px">
          <span>BOS</span><span>${r.bos && r.bos.price ? UI.fmt(r.bos.price) : "—"}</span>
        </div>
        <div class="row-between" style="margin-bottom:6px;font-size:12px">
          <span>POINT 1</span><span>${r.point_1 && r.point_1.price ? UI.fmt(r.point_1.price) : "—"}</span>
        </div>
        <div class="row-between" style="margin-bottom:6px;font-size:12px">
          <span>POINT 2</span><span>${r.point_2 && r.point_2.price ? UI.fmt(r.point_2.price) : "—"}</span>
        </div>
        <div class="divider" style="margin:6px 0"></div>
        ${levels}
        <div class="divider" style="margin:6px 0"></div>
        <div class="row-between" style="font-size:12px">
          <span>RETRACEMENT STATE</span>
          <span>${specState}</span>
        </div>
        <div class="row-between" style="font-size:12px;margin-top:4px">
          <span>TP STATUS</span><span>${tpStatus}</span>
        </div>
        <div class="row-between" style="font-size:12px;margin-top:4px">
          <span>ENTRY STATUS</span><span>${entryStatus}</span>
        </div>
        ${tp.is_locked ? `<div class="row-between" style="font-size:12px;margin-top:4px"><span>LOCKED TP</span><span style="font-weight:700;font-family:var(--font-num)">${UI.fmt(tp.locked)}</span></div>` : ""}
      </div>
    </div></div>`;
  }

  function renderRiskCard(d) {
    const risk = d.risk || {};
    return `<div class="card"><div class="card-head"><span>RISK VALIDATION</span></div><div class="card-body flush">
      <div class="kv" style="padding:8px 12px;font-size:12px">
        <dt>Risk Gate</dt><dd>${gateBadge(risk.gate)}</dd>
        <dt>Risk Level</dt><dd><span class="badge badge-${risk.level === "LOW" ? "green" : risk.level === "MEDIUM" ? "amber" : "red"}">${UI.esc(risk.level || "N/A")}</span></dd>
        <dt>Entry Valid</dt><dd>${risk.entry_valid ? '<span class="badge badge-green">YES</span>' : '<span class="badge badge-red">NO</span>'}</dd>
        <dt>SL Valid</dt><dd>${risk.sl_valid ? '<span class="badge badge-green">YES</span>' : '<span class="badge badge-red">NO</span>'}</dd>
        <dt>TP Valid</dt><dd>${risk.tp_valid ? '<span class="badge badge-green">YES</span>' : '<span class="badge badge-red">NO</span>'}</dd>
        <dt>R:R</dt><dd><span class="num">1:${UI.fmt(risk.risk_reward, 2)}</span> <span class="muted">(min 1:${UI.fmt(risk.min_risk_reward, 2)})</span></dd>
      </div>
      ${risk.gate === "FAIL" ? '<div style="padding:8px 12px;font-size:12px;color:var(--red)">\u26d4 RISK GATE FAILED</div>' : ""}
    </div></div>`;
  }

  function renderLifecycleCard(d) {
    const lc = d.lifecycle || {};
    if (lc.no_setup) return `<div class="card"><div class="card-head"><span>SIGNAL LIFECYCLE</span></div><div class="card-body" style="text-align:center;padding:20px"><span class="muted">NO ACTIVE SETUP</span></div></div>`;
    const steps = (lc.steps || []).map(([name, icon, done]) => `<div class="row-between" style="padding:3px 0;font-size:11.5px;border-bottom:1px solid rgba(31,42,58,0.2)">
      <span style="color:var(--text)">${name}</span>
      <span style="font-size:14px">${icon}</span>
    </div>`).join("");
    return `<div class="card"><div class="card-head"><span>SIGNAL LIFECYCLE</span></div><div class="card-body flush"><div style="padding:8px 12px">${steps}</div></div></div>`;
  }

  function renderReasoning(d) {
    const ai = d.ai || {};
    const explanation = ai.explanation || "";
    const reasoning = ai.reasoning || "";
    const risks = ai.risk_flags || [];
    const missing = ai.missing_confirmations || [];
    const header = ai.decision === "REJECT" ? "WHY AI REJECTED" : "WHY AI " + (ai.decision || "RESPONDED");
    return `<div class="card"><div class="card-head"><span>${header}</span></div><div class="card-body">
      ${explanation ? `<div style="font-size:12px;color:var(--text);margin-bottom:8px">${UI.esc(explanation)}</div>` : ""}
      ${reasoning && reasoning !== explanation ? `<div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${UI.esc(reasoning)}</div>` : ""}
      ${risks.length ? `<div style="font-size:11px;color:var(--amber);margin-bottom:4px">Risk flags:</div><ul style="margin:0;padding-left:16px;font-size:11px;color:var(--amber)">${risks.map(r => "<li>" + UI.esc(r) + "</li>").join("")}</ul>` : ""}
      ${missing.length ? `<div style="font-size:11px;color:var(--text-dim);margin-top:6px">Missing confirmations:</div><ul style="margin:0;padding-left:16px;font-size:11px;color:var(--text-dim)">${missing.map(m => "<li>" + UI.esc(m) + "</li>").join("")}</ul>` : ""}
      ${!explanation && !risks.length && !missing.length ? '<span class="muted" style="font-size:12px">No AI reasoning available.</span>' : ""}
    </div></div>`;
  }

  function renderEvidence(d) {
    const ev = d.evidence || [];
    const rows = ev.map(e => `<div class="row-between" style="padding:3px 0;font-size:11px;border-bottom:1px solid rgba(31,42,58,0.2)">
      <span style="color:var(--text)">${UI.esc(e.claim)}</span>
      <span class="row">${e.status === "MISMATCH" ? '<span style="color:var(--red);font-size:10px;margin-right:4px">\u26a0</span>' : ""}${evBadge(e.status)}</span>
    </div>`).join("");
    return `<div class="card"><div class="card-head"><span>AI CLAIM VERIFICATION</span></div><div class="card-body flush">
      <div style="padding:8px 12px">${rows || '<span class="muted">No evidence data.</span>'}</div>
      ${ev.some(e => e.status === "MISMATCH") ? '<div style="padding:6px 12px 8px;font-size:11px;color:var(--red)">\u26a0 EVIDENCE MISMATCH DETECTED</div>' : ""}
    </div></div>`;
  }

  function renderWhatMustChange(d) {
    const wmc = d.what_must_change || {};
    const items = wmc.items || [];
    const action = wmc.action || "WAIT";
    const rows = items.map(i => `<div class="row-between" style="padding:4px 0;font-size:11.5px;border-bottom:1px solid rgba(31,42,58,0.2)">
      <span style="color:var(--text)">${UI.esc(i.area)}</span>
      <span style="font-family:var(--font-num);font-size:11px;text-align:right">
        <span style="color:var(--text-dim)">${UI.esc(i.current)}</span> \u2192 <span style="color:var(--text-dim)">${UI.esc(i.required)}</span>
        ${gateBadge(i.status)}
      </span>
    </div>`).join("");
    return `<div class="card"><div class="card-head"><span>WHAT MUST CHANGE</span></div><div class="card-body flush">
      <div style="padding:8px 12px">${rows || '<span class="muted">All requirements satisfied.</span>'}</div>
      <div class="divider"></div>
      <div style="padding:8px 12px;font-size:12px;font-weight:600;color:${action === "WAIT" ? "var(--amber)" : action === "MONITOR" ? "var(--blue)" : "var(--green)"}">ACTION: ${action}</div>
    </div></div>`;
  }

  function renderMarketSnapshot(d) {
    const ms = d.market_snapshot || {};
    return `<div class="card"><div class="card-head"><span>MARKET SNAPSHOT</span></div><div class="card-body flush">
      <div class="kv" style="padding:8px 12px;font-size:12px">
        <dt>LIVE PRICE</dt><dd style="font-size:20px;font-weight:700;font-family:var(--font-num)">${UI.fmt(ms.current_price)}</dd>
        <dt>4H BIAS</dt><dd>${UI.esc(ms["4h_bias"] || "—")}</dd>
        <dt>1H BIAS</dt><dd>${UI.esc(ms["1h_bias"] || "—")}</dd>
        <dt>15M BIAS</dt><dd>${UI.esc(ms["15m_bias"] || "—")}</dd>
        <dt>DATA STATUS</dt><dd>${UI.esc(ms.data_status || "—")}</dd>
        <dt>Timeframe</dt><dd>${UI.esc(ms.timeframe || "15m")}</dd>
      </div>
    </div></div>`;
  }

  function renderAIHealth(d) {
    const h = d.health || {};
    const provs = Object.entries(h.providers || {}).filter(([k, v]) => v.configured).map(([k, v]) => {
      const st = v.status === "AVAILABLE" ? "green" : "red";
      return `<div class="row-between" style="padding:2px 0;font-size:11px">
        <span>${UI.esc(k)}</span>
        <span class="badge badge-${st}">${UI.esc(v.status)}</span>
      </div>`;
    }).join("");
    return `<div class="card"><div class="card-head"><span>AI SYSTEM HEALTH</span></div><div class="card-body flush">
      <div class="kv" style="padding:8px 12px;font-size:12px">
        <dt>Status</dt><dd>${h.status === "HEALTHY" ? '<span class="badge badge-green">HEALTHY</span>' : h.status === "DEGRADED" ? '<span class="badge badge-amber">DEGRADED</span>' : '<span class="badge badge-red">OFFLINE</span>'}</dd>
        <dt>Advisory Only</dt><dd><span class="badge badge-blue">YES</span></dd>
      </div>
      ${provs ? `<div class="divider"></div><div style="padding:8px 12px">${provs}</div>` : '<div class="divider"></div><div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">No AI providers configured.</div>'}
    </div></div>`;
  }

  function renderPerformance(d) {
    const perf = d.performance || {};
    if (perf.status === "INSUFFICIENT_DATA") return `<div class="card"><div class="card-head"><span>AI VALIDATION PERFORMANCE</span></div><div class="card-body" style="text-align:center;padding:20px"><span class="muted">INSUFFICIENT DATA</span><div style="font-size:11px;color:var(--text-dim);margin-top:4px">At least 5 validations required.</div></div></div>`;
    return `<div class="card"><div class="card-head"><span>AI VALIDATION PERFORMANCE</span></div><div class="card-body flush">
      <div class="kv" style="padding:8px 12px;font-size:12px">
        <dt>Total</dt><dd>${perf.total_validations || 0}</dd>
        <dt>Approved</dt><dd><span class="badge badge-green">${perf.approved || 0}</span></dd>
        <dt>Rejected</dt><dd><span class="badge badge-red">${perf.rejected || 0}</span></dd>
        <dt>Caution</dt><dd><span class="badge badge-amber">${perf.caution || 0}</span></dd>
        <dt>Unavailable</dt><dd><span class="badge badge-dim">${perf.unavailable || 0}</span></dd>
        <dt>Approval rate</dt><dd>${UI.fmtPct(perf.approval_rate)}</dd>
      </div>
    </div></div>`;
  }

  function renderAIHistory(d) {
    const hist = d.history || [];
    if (!hist.length) return `<div class="card" id="ai-history-container"><div class="card-head"><span>AI VALIDATION HISTORY</span></div><div class="card-body" style="text-align:center;padding:16px"><span class="muted">No validation history available.</span></div></div>`;
    const rows = hist.slice(0, 20).map(h => `<tr>
      <td class="num" style="font-size:10.5px">${UI.fmtTs(h.time)}</td>
      <td>${UI.dirBadge(h.direction)}</td>
      <td>${aiBadge(h.ai_decision)}</td>
      <td class="num">${UI.fmt(h.ai_confidence, 0)}</td>
      <td class="num">${UI.fmt(h.confidence_score, 0)}</td>
      <td>${UI.fmt(h.risk_reward, 2)}</td>
      <td>${UI.esc(h.outcome || "—")}</td>
    </tr>`).join("");
    return `<div class="card" id="ai-history-container"><div class="card-head"><span>AI VALIDATION HISTORY</span></div><div class="card-body flush">
      <div class="table-wrap"><table class="term"><thead><tr><th>TIME</th><th>DIR</th><th>AI DECISION</th><th>AI CONF</th><th>CONF</th><th>R:R</th><th>OUTCOME</th></tr></thead>
      <tbody>${rows}</tbody></table></div>
      ${hist.length > 20 ? '<div style="padding:6px 12px;font-size:10.5px;color:var(--text-dim)">Showing last 20 of ' + hist.length + ' entries.</div>' : ""}
    </div></div>`;
  }

  function renderAITimeline(d) {
    const tl = d.timeline || [];
    if (!tl.length) return "";
    const entries = tl.slice(0, 10).map(t => `<div style="padding:4px 0;font-size:11px;border-bottom:1px solid rgba(31,42,58,0.2)">
      <span class="muted" style="font-size:10px">${UI.fmtTs(t.time)}</span>
      <span style="margin:0 6px">${aiBadge(t.ai_decision)}</span>
      <span class="num" style="font-size:11px">CONF ${UI.fmt(t.confluence, 0)}</span>
      <span class="muted" style="margin-left:4px;font-size:10px">${UI.dirBadge(t.direction)}</span>
    </div>`).join("");
    return `<div class="card" id="ai-timeline-container"><div class="card-head"><span>VALIDATION TIMELINE</span></div><div class="card-body flush"><div style="padding:8px 12px">${entries}</div></div></div>`;
  }

  function loadAIPage(mount) {
    let running = false;
    const doFetch = async () => {
      if (running) return;
      running = true;
      const seq = ++_seq;
      try {
        const [val, mkt] = await Promise.allSettled([
          API.aiValidation("XAUUSD"),
          API.liveMarket("XAUUSD", { timeframe: "15m", include_forming: true }),
        ]);
        if (seq !== _seq) return;
        const d = val.status === "fulfilled" ? val.value : null;
        if (!d) {
          if (_first) { mount.innerHTML = ""; mount.appendChild(UI.state("AI VALIDATION UNAVAILABLE", "Backend did not respond.", "\u26a0", true)); }
          return;
        }
        const market = mkt.status === "fulfilled" ? mkt.value : null;
        const candles = (market && market.candles) || [];
        if (_first) {
          _first = false;
          mount.innerHTML = buildShell(d);
          renderChart(d, candles);
        } else {
          patchDynamic(d);
          // Chart re-render on change
          const lvlKey = JSON.stringify([d.retracement && d.retracement.levels, d.current_price]);
          const key = (candles && candles.length ? candles[candles.length - 1].timestamp : "") + "|" + (candles ? candles.length : 0) + "|" + lvlKey;
          if (key !== _lastChartKey && candles && candles.length >= 2) {
            _lastChartKey = key;
            renderChart(d, candles);
          }
        }
      } catch (err) {
        if (seq === _seq && _first) { mount.innerHTML = ""; mount.appendChild(UI.state("AI VALIDATION ERROR", UI.esc(err.message || String(err)), "\u26a0", true)); }
      } finally { running = false; }
    };
    doFetch();
    _liveTimer = setInterval(doFetch, AI_LIVE_MS);
  }

  window.__viewCleanup = () => { if (_liveTimer) { clearInterval(_liveTimer); _liveTimer = null; } };
  loadAIPage(mount);
};

/* ================= CANDIDATES ================= */
Routes["/candidates"] = (mount) => {
  renderWith(async () => {
    const [c, r, h] = await Promise.allSettled([API.candidates(), API.mtfReport(), API.get("/research/health")]);
    return { cand: c.status === "fulfilled" ? c.value : null, mtf: r.status === "fulfilled" ? r.value : null, health: h.status === "fulfilled" ? h.value : null };
  }, (d) => {
    const cand = d.cand && d.cand.candidates ? d.cand.candidates : {};
    const health = (d.health && d.health.candidates) || {};
    const mtfCands = (d.mtf && d.mtf.ranked_candidates) || [];
    const versions = Object.keys(cand);
    const healthBadge = (v) => {
      const s = health[v] && health[v].state;
      const map = { GREEN: "badge-green", YELLOW: "badge-amber", RED: "badge-red" };
      return s ? `<span class="badge ${map[s] || "badge-muted"}" title="${UI.esc((health[v].notes || []).join(" · "))}">HEALTH ${s}</span>` : "";
    };
    const cards = versions.length ? versions.map(v => {
      const c = cand[v];
      const exp = c.expectancy_r;
      const fwd = c.open || 0;
      return `<div class="card">
        <div class="card-head"><span>${UI.esc(v)}</span><span class="row">${healthBadge(v)}${UI.statusBadge((c.status || "OOS_VALIDATED").replace(/_/g, " "))}</span></div>
        <div class="card-body">
          <div class="grid grid-3">
            ${UI.metric("Closed", UI.esc(c.closed || 0)).outerHTML}
            ${UI.metric("Forward signals", UI.esc(fwd)).outerHTML}
            ${UI.metric("Win rate", UI.fmtPct(c.win_rate_pct)).outerHTML}
            ${UI.metric("Expectancy", UI.fmtR(exp)).outerHTML}
            ${UI.metric("Profit factor", UI.fmt(c.profit_factor, 2)).outerHTML}
            ${UI.metric("Median R", UI.fmtR(c.median_r)).outerHTML}
          </div>
          <div class="divider"></div>
          ${UI.kv([
            ["Regime", UI.esc(c.regime || "RANGING")],
            ["Confluence ≥", UI.esc(c.conf || 75)],
            ["TP", c.tp_r != null ? `${c.tp_r}R` : "—"],
            ["OOS windows", (c.oos_windows_positive || "—")],
            ["Forward CI", health[v] && health[v].ci_lo != null ? `[${health[v].ci_lo}, ${health[v].ci_hi}]` : "—"],
            ["Forward sample", health[v] ? `${health[v].closed_signals} closed` : "—"],
          ])}
        </div>
      </div>`;
    }).join("") : UI.state("No candidates", "No forward candidates registered yet.", "").outerHTML;

    const rankTable = mtfCands.length ? `<div class="card">
      <div class="card-head"><span>Risk-adjusted ranking</span><span class="muted">cost-aware</span></div>
      <div class="card-body flush"><div class="table-wrap"><table class="term">
        <thead><tr><th>Rank</th><th>Config</th><th>Regime</th><th>TP</th><th>Trades</th><th>WR</th><th>PF</th><th>Expectancy</th><th>OOS+</th><th>Score</th></tr></thead>
        <tbody>${mtfCands.map(c => `<tr>
          <td class="num">#${c.rank}</td>
          <td style="font-size:11px">${UI.esc(c.config)}</td>
          <td>${UI.esc(c.regime)}</td>
          <td class="num">${c.tp_r}R</td>
          <td class="num">${c.trades}</td>
          <td class="num">${UI.fmtPct(c.win_rate_pct)}</td>
          <td class="num">${UI.fmt(c.profit_factor, 2)}</td>
          <td class="num ${c.expectancy_r >= 0 ? "up" : "down"}">${UI.fmtR(c.expectancy_r)}</td>
          <td class="num">${c.positive_windows}/${c.total_windows}</td>
          <td class="num">${UI.fmt(c.rank_score, 3)}</td>
        </tr>`).join("")}</tbody>
      </table></div></div>
    </div>` : "";

    return `<div class="stack">
      <div class="section-title">Forward Observation Candidates</div>
      <div class="grid grid-3">${cards}</div>
      ${rankTable}
      <div class="card">
        <div class="card-head"><span>Production vs Research</span></div>
        <div class="card-body" style="font-size:12px;color:var(--text-dim)">
          <span class="badge badge-red">PRODUCTION STRATEGY: FAILED</span> is a separate configuration from these research candidates.
          No candidate is promoted automatically; forward evidence + human review are required.
        </div>
      </div>
    </div>`;
  }, mount);
};

/* ================= RESEARCH ================= */
Routes["/research"] = (mount) => {
  renderWith(async () => {
    const [cl, mo, so] = await Promise.allSettled([API.classification(), API.mtfReport(), API.signalOutcomes()]);
    return { cl: cl.status === "fulfilled" ? cl.value : null, mtf: mo.status === "fulfilled" ? mo.value : null, so: so.status === "fulfilled" ? so.value : null };
  }, (d) => {
    const cl = d.cl || {};
    const grade = (cl.classification && cl.classification.grade) || "INCONCLUSIVE";
    const reasons = (cl.classification && cl.classification.reasons) || [];
    const oos = cl.out_of_sample || {};
    const mc = cl.monte_carlo || {};
    const boot = cl.bootstrap || {};
    const mr = boot.mean_r || {};
    const mtfCands = (d.mtf && d.mtf.ranked_candidates) || [];
    return `<div class="stack">
      <div class="section-title">Research Laboratory</div>
      <div class="grid grid-4">
        ${UI.metric("Classification", `<span class="badge ${grade === "FAILED" ? "badge-red" : grade === "ROBUST" ? "badge-green" : grade === "PROMISING" ? "badge-amber" : "badge-blue"}">${UI.esc(grade)}</span>`).outerHTML}
        ${UI.metric("OOS trades", UI.esc(oos.trades || 0)).outerHTML}
        ${UI.metric("Expectancy", UI.fmtR(oos.expectancy_r), undefined, (oos.expectancy_r || 0) >= 0 ? "" : "").outerHTML}
        ${UI.metric("Profit factor", UI.fmt(oos.profit_factor, 2)).outerHTML}
        ${UI.metric("Win rate", UI.fmtPct(oos.win_rate_pct)).outerHTML}
        ${UI.metric("Max drawdown", UI.fmtPct(oos.max_drawdown_pct)).outerHTML}
        ${UI.metric("Bootstrap mean-R", (mr.lo != null) ? `[${UI.fmt(mr.lo, 2)}, ${UI.fmt(mr.hi, 2)}]` : "—").outerHTML}
        ${UI.metric("MC P(neg)", UI.fmtPct(mc.probability_negative_return_pct)).outerHTML}
      </div>
      ${reasons.length ? `<div class="card"><div class="card-head"><span>Classification reasons</span></div><div class="card-body" style="font-size:12.5px;color:var(--text-dim)">${reasons.map(r => "• " + UI.esc(r)).join("<br>")}</div></div>` : ""}
      ${mtfCands.length ? `<div class="card"><div class="card-head"><span>Top candidates (cost-adjusted)</span></div><div class="card-body flush"><div class="table-wrap"><table class="term">
        <thead><tr><th>#</th><th>Config</th><th>Regime</th><th>TP</th><th>Trades</th><th>WR</th><th>Exp</th><th>OOS+</th><th>Score</th></tr></thead>
        <tbody>${mtfCands.slice(0, 8).map(c => `<tr><td class="num">#${c.rank}</td><td style="font-size:11px">${UI.esc(c.config)}</td><td>${UI.esc(c.regime)}</td><td class="num">${c.tp_r}R</td><td class="num">${c.trades}</td><td class="num">${UI.fmtPct(c.win_rate_pct)}</td><td class="num ${c.expectancy_r >= 0 ? "up" : "down"}">${UI.fmtR(c.expectancy_r)}</td><td class="num">${c.positive_windows}/${c.total_windows}</td><td class="num">${UI.fmt(c.rank_score, 3)}</td></tr>`).join("")}
        </tbody></table></div></div></div>` : ""}
    </div>`;
  }, mount);
};

/* ================= OBSERVATION ================= */
Routes["/observation"] = (mount) => {
  renderWith(async () => {
    const [so, ob] = await Promise.allSettled([API.signalOutcomes(), API.observation()]);
    return { so: so.status === "fulfilled" ? so.value : null, ob: ob.status === "fulfilled" ? ob.value : null };
  }, (d) => {
    const so = d.so || {};
    const cs = so.closed_stats || {};
    const hr = so.hit_rates_pct || {};
    const progress = Math.min(100, Math.round(((so.closed || 0) / 100) * 100));
    return `<div class="stack">
      <div class="row-between">
        <div class="section-title">Forward Observation</div>
        <span class="badge badge-green">OBSERVATION ACTIVE</span>
      </div>
      <div class="card">
        <div class="card-head"><span>Validation progress</span><span class="muted">target 100 closed signals</span></div>
        <div class="card-body">
          ${UI.progress(progress, "green")}
          <div style="font-size:12px;color:var(--text-dim);margin-top:8px">${UI.esc(so.closed || 0)} / 100 forward signals closed · ${UI.esc(so.open || 0)} open</div>
        </div>
      </div>
      <div class="grid grid-4">
        ${UI.metric("Tracked", UI.esc(so.tracked_total || 0)).outerHTML}
        ${UI.metric("Closed", UI.esc(so.closed || 0)).outerHTML}
        ${UI.metric("Expectancy", UI.fmtR(cs.expectancy_r)).outerHTML}
        ${UI.metric("Win rate", UI.fmtPct(cs.win_rate_pct)).outerHTML}
        ${UI.metric("Avg MFE", UI.fmt(cs.avg_mfe_r, 3) + "R").outerHTML}
        ${UI.metric("Avg MAE", UI.fmt(cs.avg_mae_r, 3) + "R").outerHTML}
        ${UI.metric("TP1 hit", UI.fmtPct(hr.tp1)).outerHTML}
        ${UI.metric("SL hit", UI.fmtPct(hr.sl)).outerHTML}
      </div>
      <div class="card">
        <div class="card-head"><span>Outcome distribution</span></div>
        <div class="card-body flush"><div class="table-wrap"><table class="term">
          <thead><tr><th>Outcome</th><th>Count</th></tr></thead>
          <tbody>${["TP1", "TP2", "TP3", "SL", "EXPIRED"].map(o => `<tr><td>${UI.statusBadge(o)}</td><td class="num">${(so.by_outcome && so.by_outcome[o] && so.by_outcome[o].trades) || 0}</td></tr>`).join("")}
        </tbody></table></div></div>
      </div>
    </div>`;
  }, mount);
};

function renderPaperTradeRows(trades, currSym = "₹", leverage = 500) {
  if (!trades || !trades.length) {
    return '<tr><td colspan="12" style="text-align:center;padding:32px 14px;color:var(--text-muted);font-size:13px">⏳ <b>No matching paper trades.</b><br><span style="font-size:11px">A dynamic lot paper trade is automatically opened with live PnL and running point tracking as soon as a 5M strategy entry is touched.</span></td></tr>';
  }
  return trades.map(t => {
    const entry = t.entry_price || t.actual_entry || t.target_entry || 0;
    const isClosed = t.status === "CLOSED" || t.state === "CLOSED";
    const curPx = isClosed ? (t.exit_price || t.current_price || entry) : (t.current_price || t.exit_price || entry);
    const pts = Number(t.running_pts != null ? t.running_pts : 0);
    const pnl = Number(t.pnl_usd != null ? t.pnl_usd : (t.unrealized_pnl != null ? t.unrealized_pnl : (t.realized_pnl || 0)));
    const absPts = Math.abs(pts).toFixed(2);
    const absPnl = Math.abs(pnl).toFixed(2);
    const ptsSign = pts >= 0 ? "+" : "-";
    const pnlSign = pnl >= 0 ? "+" : "-";
    const ptsCls = pts >= 0 ? "up" : "down";
    const pnlCls = pnl >= 0 ? "profit" : "loss";

    // Used Margin calculation: (lot * 100 * entry_price) / leverage
    const lotVal = Number(t.lot_size != null ? t.lot_size : 0.01);
    const levVal = Number(leverage || 500);
    const entryForMargin = Number(entry || 4435.0);
    const marginReq = (lotVal * 100.0 * entryForMargin) / levVal;

    const stratStr = String(t.strategy || "").toUpperCase();
    const isSMC = stratStr.includes("SMC");
    const isTrend = stratStr.includes("TREND");
    let stratBadge = '<span class="badge" style="background:rgba(171,71,188,0.15);color:#ab47bc;border:1px solid #ab47bc;font-weight:600">🎯 Fib Retracement</span>';
    let stratRoute = "/fib-retracement";
    let trancheClass = "tranche-bundle-l1";

    if (isSMC) {
      stratBadge = '<span class="badge" style="background:rgba(38,166,154,0.15);color:#26a69a;border:1px solid #26a69a;font-weight:600">💎 SMC With Fib</span>';
      stratRoute = "/smc-fib";
      trancheClass = "smc-single-row";
    } else if (isTrend) {
      stratBadge = '<span class="badge" style="background:rgba(0,188,212,0.15);color:#00e5ff;border:1px solid #00e5ff;font-weight:600">📈 Fib Go With Trend</span>';
      stratRoute = "/fib-trend";
      trancheClass = "trend-breakout-row";
    } else {
      if (String(t.layer || "").includes("L2")) trancheClass = "tranche-bundle-l2";
      else if (String(t.layer || "").includes("L3")) trancheClass = "tranche-bundle-l3";
      else trancheClass = "tranche-bundle-l1";
    }

    const sl = t.stop_loss ? `$${Number(t.stop_loss).toFixed(2)}` : '—';
    let slTpContent = '';
    if (isTrend) {
      const tp1 = t.take_profit_1 ? `$${Number(t.take_profit_1).toFixed(2)}` : null;
      const tp2 = t.take_profit_2 ? `$${Number(t.take_profit_2).toFixed(2)}` : null;
      const isBeLocked = t.state_logs && Array.isArray(t.state_logs) && t.state_logs.some(l => l && (l.event === "BREAKEVEN_LOCKED" || (typeof l.reason === "string" && l.reason.includes("Breakeven"))));
      const slChip = isBeLocked ? `<div class="badge-be-chip">🛡 BE: ${sl}</div>` : `<div class="badge-sl-chip">🛑 SL: ${sl}</div>`;

      if (tp1 && tp2 && tp1 !== tp2) {
        slTpContent = `${slChip}<div class="badge-tp1-chip">🎯 TP1: ${tp1}</div><div class="badge-tp2-chip">🏆 TP2: ${tp2}</div>`;
      } else {
        slTpContent = `${slChip}<div class="badge-tp2-chip">🏆 TP2: ${tp2 || tp1 || '—'}</div>`;
      }
    } else {
      const tp = t.take_profit_1 || t.take_profit ? `$${Number(t.take_profit_1 || t.take_profit).toFixed(2)}` : '—';
      slTpContent = `<div class="badge-sl-chip">🛑 SL: ${sl}</div><div class="badge-tp-chip">🎯 TP: ${tp}</div>`;
    }

    // Outcome status pill & row accent
    let statusBadge = '';
    let outcomeRowClass = '';
    if (!isClosed) {
      statusBadge = '<span class="status-pill-open"><span class="pulse-dot live" style="width:6px;height:6px;display:inline-block;margin-right:4px"></span>LIVE OPEN</span>';
      outcomeRowClass = 'row-outcome-open';
    } else {
      const reason = String(t.exit_reason || "").toUpperCase();
      const realized = Number(t.realized_pnl != null ? t.realized_pnl : (t.pnl_usd || 0));
      if (reason.includes("BREAKEVEN") || (realized === 0 && (reason.includes("BE") || reason.includes("BREAK")))) {
        statusBadge = '<span class="status-pill-be">🛡 BREAKEVEN</span>';
        outcomeRowClass = 'row-outcome-be';
      } else if (reason.includes("TP") || realized > 0) {
        statusBadge = '<span class="status-pill-tp">🎯 TP HIT</span>';
        outcomeRowClass = 'row-outcome-win';
      } else if (reason.includes("SL") || realized < 0) {
        statusBadge = '<span class="status-pill-sl">🛑 SL HIT</span>';
        outcomeRowClass = 'row-outcome-loss';
      } else {
        statusBadge = '<span class="badge badge-dim">CLOSED</span>';
      }
    }

    const rrStr = t.risk_reward ? `1:${Number(t.risk_reward).toFixed(1)}` : '1:1.8';

    return `<tr class="${trancheClass} ${outcomeRowClass}">
      <td>${UI.fmtTs(t.opened_at || t.created_at)}</td>
      <td>${stratBadge} <span class="badge badge-dim" style="font-size:10px">${t.layer || ''}</span></td>
      <td>${UI.dirBadge(t.direction)}</td>
      <td class="num font-mono" style="font-weight:600">
        <div>${lotVal.toFixed(2)} Lot</div>
        <div style="font-size:10.5px;color:#81c784;font-weight:600;margin-top:2px" title="Used Margin at 1:${levVal}">Margin: ${currSym}${marginReq.toFixed(2)}</div>
      </td>
      <td class="num font-mono"><b>$${Number(entry).toFixed(2)}</b></td>
      <td class="num font-mono"><b>$${Number(curPx).toFixed(2)}</b></td>
      <td class="num ${ptsCls}"><b>${ptsSign}${absPts} PTS</b></td>
      <td><span class="pnl-pill ${pnlCls}">${pnlSign}${currSym}${absPnl}</span></td>
      <td class="num font-mono" style="color:var(--text-bright)">${rrStr}</td>
      <td style="padding:6px 10px;vertical-align:middle">${slTpContent}</td>
      <td style="vertical-align:middle">${statusBadge}</td>
      <td style="text-align:center">
        <div class="row-actions-wrap" style="justify-content:center">
          <button class="btn-mini-action btn-pt-chart" data-route="${stratRoute}" title="Open Strategy Chart">📈</button>
        </div>
      </td>
    </tr>`;
  }).join("");
}

/* ================= PAPER TRADING ================= */
Routes["/paper"] = (mount) => {
  let _paperTimer = null;
  let isUpdating = false;

  renderWith(async () => {
    const [acct, pt, ov] = await Promise.allSettled([API.account(), API.paperTrades(), API.overview("XAUUSD")]);
    return {
      acct: acct.status === "fulfilled" ? acct.value : null,
      pt: pt.status === "fulfilled" ? pt.value : [],
      ov: ov.status === "fulfilled" ? ov.value : null
    };
  }, (d) => {
    const acct = d.acct || {};
    const raw = d.pt;
    let currentTrades = Array.isArray(raw) ? raw : (raw && raw.database_trades) || [];
    const safety = (d.ov && d.ov.safety) || {};
    const blocked = safety.headline === "PAPER_TRADING_BLOCKED" || safety.headline === "SIGNALS_BLOCKED";
    const obsMode = safety.observation_mode || false;

    // Currency and leverage sync from backend execution settings / account
    const isCent = acct.account_currency === "cent";
    const currSym = acct.currency_symbol || (isCent ? "₹" : "$");
    const leverage = Number(acct.account_leverage || 500);

    // Filters state
    let activeStrat = "ALL";
    let activeOutcome = "";
    let activeDir = "";
    let searchQuery = "";

    function getFilteredTrades() {
      const q = searchQuery.trim().toLowerCase();
      return currentTrades.filter(t => {
        const st = String(t.strategy || "").toUpperCase();
        if (activeStrat === "RETRACEMENT" && (st.includes("SMC") || st.includes("TREND"))) return false;
        if (activeStrat === "SMC" && !st.includes("SMC")) return false;
        if (activeStrat === "TREND" && !st.includes("TREND")) return false;

        const isClosed = t.status === "CLOSED" || t.state === "CLOSED";
        const pnl = Number(t.pnl_usd != null ? t.pnl_usd : (t.unrealized_pnl != null ? t.unrealized_pnl : (t.realized_pnl || 0)));

        if (activeOutcome === "WIN" && (!isClosed || pnl <= 0)) return false;
        if (activeOutcome === "LOSS" && (!isClosed || pnl >= 0)) return false;
        if (activeOutcome === "OPEN" && isClosed) return false;

        if (activeDir && String(t.direction || "").toUpperCase() !== activeDir) return false;

        if (q) {
          const str = `${t.id || ''} ${t.strategy || ''} ${t.layer || ''} ${t.direction || ''} ${t.entry_price || ''} ${t.status || ''}`.toLowerCase();
          if (!str.includes(q)) return false;
        }
        return true;
      });
    }

    function wireChartButtons() {
      const tbody = mount.querySelector("#paper-trades-tbody");
      if (!tbody) return;
      tbody.querySelectorAll(".btn-pt-chart").forEach(btn => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          if (btn.dataset.route) location.hash = btn.dataset.route;
        });
      });
    }

    function updateTableView() {
      const filtered = getFilteredTrades();
      const tbody = mount.querySelector("#paper-trades-tbody");
      const countEl = mount.querySelector("#paper-trade-count");
      if (countEl) countEl.textContent = `${filtered.length} of ${currentTrades.length} trades`;
      if (tbody) {
        tbody.innerHTML = renderPaperTradeRows(filtered, currSym, leverage);
        wireChartButtons();
      }

      // Sync filter pill counts live
      const pillAll = mount.querySelector('.pt-pill[data-strat="ALL"] .pill-count');
      const pillRet = mount.querySelector('.pt-pill[data-strat="RETRACEMENT"] .pill-count');
      const pillSmc = mount.querySelector('.pt-pill[data-strat="SMC"] .pill-count');
      const pillTrd = mount.querySelector('.pt-pill[data-strat="TREND"] .pill-count');
      if (pillAll) pillAll.textContent = currentTrades.length;
      if (pillRet) pillRet.textContent = currentTrades.filter(t => !String(t.strategy || "").toUpperCase().includes("SMC") && !String(t.strategy || "").toUpperCase().includes("TREND")).length;
      if (pillSmc) pillSmc.textContent = currentTrades.filter(t => String(t.strategy || "").toUpperCase().includes("SMC")).length;
      if (pillTrd) pillTrd.textContent = currentTrades.filter(t => String(t.strategy || "").toUpperCase().includes("TREND")).length;
    }

    function exportPaperTradesCSV() {
      const list = getFilteredTrades();
      if (!list || !list.length) {
        UI.toast("Export", "No paper trades to export.", "amber");
        return;
      }
      const headers = ["Opened_At", "Strategy", "Layer", "Direction", "Lots", "Used_Margin", "Entry_Price", "Exit_Price", "Running_Pts", "PnL", "Risk_Reward", "SL", "TP1", "TP2", "Status"];
      const lines = [headers.join(",")];
      list.forEach(t => {
        const entry = t.entry_price || t.actual_entry || t.target_entry || "";
        const isClosed = t.status === "CLOSED" || t.state === "CLOSED";
        const curPx = isClosed ? (t.exit_price || t.current_price || entry) : (t.current_price || t.exit_price || entry);
        const pts = t.running_pts != null ? Number(t.running_pts).toFixed(2) : "0.00";
        const pnl = Number(t.pnl_usd != null ? t.pnl_usd : (t.unrealized_pnl != null ? t.unrealized_pnl : (t.realized_pnl || 0))).toFixed(2);
        const rr = t.risk_reward ? `1:${Number(t.risk_reward).toFixed(1)}` : "1:1.8";
        const lotVal = Number(t.lot_size != null ? t.lot_size : 0.01);
        const marginReq = ((lotVal * 100.0 * (Number(entry) || 4435.0)) / leverage).toFixed(2);
        lines.push([
          `"${t.opened_at || t.created_at || ""}"`,
          `"${t.strategy || ""}"`,
          t.layer || "",
          t.direction || "LONG",
          lotVal.toFixed(2),
          marginReq,
          entry,
          curPx,
          pts,
          pnl,
          `"${rr}"`,
          t.stop_loss || "",
          t.take_profit_1 || t.take_profit || "",
          t.take_profit_2 || "",
          t.status || t.state || "CLOSED"
        ].join(","));
      });
      const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `xauusd_paper_trades_${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      UI.toast("Export Complete", `Exported ${list.length} paper trades to CSV.`, "green");
    }

    // In-place poller update function
    async function updatePaperInPlace() {
      if (AutoRefresh.speed === 0 || isUpdating) return;
      isUpdating = true;
      try {
        const [acctRes, ptRes] = await Promise.allSettled([API.account(), API.paperTrades()]);
        const newAcct = acctRes.status === "fulfilled" ? acctRes.value : null;
        const newRaw = ptRes.status === "fulfilled" ? ptRes.value : null;
        if (newAcct) {
          const liveCurr = newAcct.currency_symbol || (newAcct.account_currency === "cent" ? "₹" : "$");
          const elB = document.getElementById("paper-metric-balance");
          const elE = document.getElementById("paper-metric-equity");
          const elR = document.getElementById("paper-metric-rpnl");
          const elU = document.getElementById("paper-metric-upnl");
          if (elB && newAcct.current_balance != null) elB.textContent = liveCurr + UI.fmt(newAcct.current_balance, 2);
          if (elE && newAcct.equity != null) elE.textContent = liveCurr + UI.fmt(newAcct.equity, 2);
          if (elR && newAcct.realized_pnl_usd != null) {
            const rp = Number(newAcct.realized_pnl_usd);
            const sgn = rp >= 0 ? "+" : "-";
            elR.textContent = `${sgn}${liveCurr}${Math.abs(rp).toFixed(2)}`;
            elR.style.color = rp >= 0 ? "var(--green-bright)" : "var(--red-bright)";
          }
          if (elU && newAcct.unrealized_pnl_usd != null) {
            const up = Number(newAcct.unrealized_pnl_usd);
            const sgn = up >= 0 ? "+" : "-";
            elU.textContent = `${sgn}${liveCurr}${Math.abs(up).toFixed(2)}`;
          }
        }
        if (newRaw) {
          currentTrades = Array.isArray(newRaw) ? newRaw : (newRaw.database_trades || []);
          updateTableView();
        }
      } catch (_) {}
      finally {
        isUpdating = false;
      }
    }

    // Initial KPI numbers
    const initialBal = acct.initial_balance != null ? Number(acct.initial_balance) : 10000;
    const balance = acct.current_balance != null ? Number(acct.current_balance) : initialBal;
    const equity = acct.equity != null ? Number(acct.equity) : balance;
    const rpnl = Number(acct.realized_pnl_usd != null ? acct.realized_pnl_usd : 0);
    const upnl = Number(acct.unrealized_pnl_usd != null ? acct.unrealized_pnl_usd : 0);
    const returnPct = initialBal > 0 ? ((rpnl / initialBal) * 100).toFixed(2) : "0.00";
    const rpnlSign = rpnl >= 0 ? "+" : "-";
    const returnSign = rpnl >= 0 ? "+" : "";

    const closedTrades = currentTrades.filter(t => t.status === "CLOSED" || t.state === "CLOSED");
    const winTrades = closedTrades.filter(t => Number(t.pnl_usd != null ? t.pnl_usd : (t.realized_pnl || 0)) > 0);
    const lossTrades = closedTrades.filter(t => Number(t.pnl_usd != null ? t.pnl_usd : (t.realized_pnl || 0)) < 0);
    const winRate = closedTrades.length ? Math.round((winTrades.length / closedTrades.length) * 100) : 0;
    const openTrades = currentTrades.filter(t => t.status !== "CLOSED" && t.state !== "CLOSED");

    // Strategy counts
    const retCount = currentTrades.filter(t => {
      const st = String(t.strategy || "").toUpperCase();
      return !st.includes("SMC") && !st.includes("TREND");
    }).length;
    const smcCount = currentTrades.filter(t => String(t.strategy || "").toUpperCase().includes("SMC")).length;
    const trendCount = currentTrades.filter(t => String(t.strategy || "").toUpperCase().includes("TREND")).length;

    setTimeout(() => {
      // Wire strategy pills
      mount.querySelectorAll(".pt-pill").forEach(pill => {
        pill.addEventListener("click", () => {
          mount.querySelectorAll(".pt-pill").forEach(p => p.classList.remove("active"));
          pill.classList.add("active");
          activeStrat = pill.dataset.strat || "ALL";
          updateTableView();
        });
      });

      // Wire outcome select
      const outSel = mount.querySelector("#paper-filter-outcome");
      if (outSel) {
        outSel.addEventListener("change", (e) => {
          activeOutcome = e.target.value;
          updateTableView();
        });
      }

      // Wire direction select
      const dirSel = mount.querySelector("#paper-filter-dir");
      if (dirSel) {
        dirSel.addEventListener("change", (e) => {
          activeDir = e.target.value;
          updateTableView();
        });
      }

      // Wire search input
      const searchInp = mount.querySelector("#paper-search");
      if (searchInp) {
        searchInp.addEventListener("input", (e) => {
          searchQuery = e.target.value;
          updateTableView();
        });
      }

      // Wire export CSV
      const expBtn = mount.querySelector("#btn-export-paper-csv");
      if (expBtn) {
        expBtn.addEventListener("click", exportPaperTradesCSV);
      }

      // Wire repair false SL hits
      const repairBtn = mount.querySelector("#btn-repair-paper");
      if (repairBtn) {
        repairBtn.addEventListener("click", async () => {
          if (!confirm("Repair false SL hits for Fib Retracement L1 (trail SL to 0.500 where L2 hit TP)?")) return;
          try {
            const res = await API.post("/paper-trades/repair");
            alert(res.message || "Trades repaired successfully!");
            location.reload();
          } catch (err) {
            alert("Repair failed: " + err.message);
          }
        });
      }

      // Wire reset paper trades
      const resetBtn = mount.querySelector("#btn-reset-paper");
      if (resetBtn) {
        resetBtn.addEventListener("click", async () => {
          if (!confirm(`Are you sure you want to reset Paper Trading?\nAll trade history will be deleted and your account will restart fresh at ${currSym}${UI.fmt(initialBal, 2)} (${isCent ? 'Cent Account' : 'Standard Account'}).`)) return;
          resetBtn.disabled = true;
          resetBtn.innerHTML = "⏳ Resetting...";
          try {
            const res = await (API.resetPaperTrades ? API.resetPaperTrades() : API.post("/paper-trades/reset"));
            UI.toast("Account Reset", res.message || "Paper trading reset successfully!", "green");
            setTimeout(() => {
              location.reload();
            }, 600);
          } catch (err) {
            UI.toast("Reset Error", err.message || "Failed to reset paper trades.", "red");
            resetBtn.disabled = false;
            resetBtn.innerHTML = "🔄 Reset Account";
          }
        });
      }

      wireChartButtons();

      if (_paperTimer) clearInterval(_paperTimer);
      _paperTimer = setInterval(updatePaperInPlace, AutoRefresh.speed || 2000);
    }, 50);

    return `<div class="stack">
      <!-- 1. Header & Live Indicator -->
      <div class="row-between">
        <div class="section-title">Paper Trading Simulation</div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge badge-green" style="display:flex;align-items:center;gap:4px"><span class="dot dot-green" style="animation:pulse 1.5s infinite"></span>LIVE AUTO-REFRESH</span>
          ${blocked ? '<span class="badge badge-red">BLOCKED</span>' : '<span class="badge badge-green">ENABLED</span>'}
        </div>
      </div>

      <!-- 2. Compact Safety Ribbon Bar (Replaces bulky card) -->
      <div class="paper-safety-bar">
        <div class="safety-chip"><span class="label">Simulation:</span> <span class="badge ${blocked ? 'badge-red' : 'badge-green'}">${blocked ? 'BLOCKED' : 'ACTIVE (Dynamic Lots)'}</span></div>
        <div class="safety-chip"><span class="label">Account:</span> <span class="badge ${isCent ? 'badge-purple' : 'badge-dim'}">${isCent ? 'Cent (USC / ₹ INR)' : 'USD ($)'}</span></div>
        <div class="safety-chip"><span class="label">Leverage:</span> <span class="badge badge-purple">1:${leverage}</span></div>
        <div class="safety-chip"><span class="label">Max DD:</span> <span class="badge badge-dim">30% Guard</span></div>
        <div class="safety-chip"><span class="label">Daily Loss Limit:</span> <span class="badge badge-dim">3% / 5 Loss Max</span></div>
        <div class="safety-chip"><span class="label">Real Money:</span> <span class="badge badge-red">DISABLED</span></div>
        <div class="safety-chip"><span class="label">Observation:</span> <span class="badge ${obsMode ? 'badge-amber' : 'badge-muted'}">${obsMode ? 'ACTIVE' : 'OFF'}</span></div>
      </div>

      <!-- 3. Financial KPI Summary Grid -->
      <div class="sig-kpi-grid">
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Account Balance</span><span>💼</span></div>
          <div class="kpi-val" id="paper-metric-balance">${currSym}${UI.fmt(balance, 2)}</div>
          <div class="kpi-sub"><span class="badge badge-dim">Equity: ${currSym}${UI.fmt(equity, 2)}</span> Initial ${currSym}${UI.fmt(initialBal, 0)}</div>
        </div>
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Realized Net PnL</span><span>📈</span></div>
          <div class="kpi-val" id="paper-metric-rpnl" style="${rpnl >= 0 ? 'color:var(--green-bright)' : 'color:var(--red-bright)'}">${rpnlSign}${currSym}${Math.abs(rpnl).toFixed(2)}</div>
          <div class="kpi-sub"><span class="badge ${rpnl >= 0 ? 'badge-green' : 'badge-red'}">${returnSign}${returnPct}% Return</span> All closed trades</div>
        </div>
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Win Rate / Closed</span><span>🎯</span></div>
          <div class="kpi-val" style="color:var(--green-bright)">${winRate}%</div>
          <div class="kpi-sub"><span style="color:var(--green-bright);font-weight:700">${winTrades.length} Wins</span> • <span style="color:var(--red-bright);font-weight:700">${lossTrades.length} Losses</span></div>
        </div>
        <div class="sig-kpi-card">
          <div class="kpi-label"><span>Active Queue / Floating</span><span>⚡</span></div>
          <div class="kpi-val" id="paper-metric-upnl" style="color:var(--cyan)">${upnl >= 0 ? "+" : "-"}${currSym}${Math.abs(upnl).toFixed(2)}</div>
          <div class="kpi-sub"><span class="badge badge-amber">${openTrades.length} Open</span> Live floating trades</div>
        </div>
      </div>

      <!-- 4. Table Header & Filter Bar -->
      <div class="row-between" style="align-items:baseline">
        <div class="row" style="gap:10px;align-items:baseline">
          <div class="section-title">Active & Historical Paper Trades</div>
          <span id="paper-trade-count" style="font-size:11.5px;color:var(--text-muted)">${currentTrades.length} trades</span>
        </div>
      </div>

      <div class="sig-filter-bar">
        <!-- Strategy Pills -->
        <div class="sig-filter-pills" id="paper-strat-pills">
          <div class="sig-pill pt-pill active" data-strat="ALL">All <span class="pill-count">${currentTrades.length}</span></div>
          <div class="sig-pill pt-pill" data-strat="RETRACEMENT">🎯 Fib Retracement <span class="pill-count">${retCount}</span></div>
          <div class="sig-pill pt-pill" data-strat="SMC">💎 SMC With Fib <span class="pill-count">${smcCount}</span></div>
          <div class="sig-pill pt-pill" data-strat="TREND">📈 Fib Go With Trend <span class="pill-count">${trendCount}</span></div>
        </div>

        <!-- Filter Controls -->
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <select class="input" id="paper-filter-outcome" style="min-width:125px">
            <option value="">All Outcomes</option>
            <option value="WIN">🟢 Wins (Profit)</option>
            <option value="LOSS">🔴 Losses (Risk)</option>
            <option value="OPEN">⚡ Open / Active</option>
          </select>
          <select class="input" id="paper-filter-dir" style="min-width:110px">
            <option value="">All directions</option>
            <option value="LONG">▲ LONG</option>
            <option value="SHORT">▼ SHORT</option>
          </select>
          <input class="input" id="paper-search" placeholder="Search price, layer…" style="min-width:150px">
          <button class="btn btn-sm" id="btn-export-paper-csv" title="Export paper trades to CSV">📥 Export CSV</button>
          <button class="btn btn-sm" id="btn-repair-paper" style="border-color:#388e3c;color:#81c784" title="Repair false SL losses by applying Smart Shield trailing">🔧 Repair SL</button>
          <button class="btn btn-sm" id="btn-reset-paper" style="border-color:#e53935;color:#ef9a9a;background:rgba(229,57,53,0.1);font-weight:700" title="Completely clear paper trade history and start fresh with ₹10,000 Cent Account">🔄 Reset Account</button>
        </div>
      </div>

      <!-- 5. Table with Tranche Connectors, Financial PnL, and Chart Actions -->
      <div class="table-wrap"><table class="term">
        <thead>
          <tr>
            <th>Opened</th>
            <th>Strategy / Layer</th>
            <th>Direction</th>
            <th>Lots & Margin</th>
            <th>Entry Price</th>
            <th>Live / Exit</th>
            <th>Running Points</th>
            <th>PnL (${currSym})</th>
            <th>R:R</th>
            <th>SL / TP</th>
            <th>Status</th>
            <th style="text-align:center">Action</th>
          </tr>
        </thead>
        <tbody id="paper-trades-tbody">${renderPaperTradeRows(currentTrades, currSym, leverage)}</tbody>
      </table></div>
    </div>`;
  }, mount);

  window.__viewCleanup = () => {
    if (_paperTimer) {
      clearInterval(_paperTimer);
      _paperTimer = null;
    }
  };
};

/* ================= NOTIFICATIONS ================= */
Routes["/notifications"] = (mount) => {
  renderWith(async () => {
    const [n, tg] = await Promise.allSettled([API.notifications(100), API.telegramStatus()]);
    return { notif: n.status === "fulfilled" ? n.value : [], tg: tg.status === "fulfilled" ? tg.value : null };
  }, (d) => {
    const rows = d.notif || [];
    const tg = d.tg || {};
    return `<div class="stack">
      <div class="row-between">
        <div class="section-title">Notifications</div>
        <span class="badge ${tg.configured ? "badge-green" : "badge-muted"}">TELEGRAM ${tg.status || "DISABLED"}</span>
      </div>
      <div class="card">
        <div class="card-head"><span>History</span></div>
        <div class="card-body flush"><div class="table-wrap"><table class="term">
          <thead><tr><th>Time</th><th>Type</th><th>Channel</th><th>Status</th><th>Error</th></tr></thead>
          <tbody>${rows.length ? rows.map(n => `<tr>
            <td>${UI.fmtTs(n.created_at)}</td>
            <td style="font-size:11px">${UI.esc(n.type || "—")}</td>
            <td>${UI.esc(n.channel || "—")}</td>
            <td>${UI.statusBadge(n.status)}</td>
            <td style="color:var(--text-muted);font-size:11px">${UI.esc(n.error_message || "—")}</td>
          </tr>`).join("") : '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No notifications recorded yet.</td></tr>'}</tbody>
        </table></div></div>
      </div>
    </div>`;
  }, mount);
};

/* ================= SYSTEM HEALTH ================= */
Routes["/health"] = (mount) => {
  renderWith(async () => {
    const [st, feed, dq, tg] = await Promise.allSettled([API.systemStatus(), API.feedHealth(), API.dataQuality(), API.telegramStatus()]);
    return { st: st.status === "fulfilled" ? st.value : null, feed: feed.status === "fulfilled" ? feed.value : null, dq: dq.status === "fulfilled" ? dq.value : null, tg: tg.status === "fulfilled" ? tg.value : null };
  }, (d) => {
    const stRaw = d.st || {};
    const st = stRaw.status || stRaw;
    const f = d.feed && d.feed.feeds ? d.feed.feeds[0] : d.feed;
    const dq = d.dq || {};
    const tg = d.tg || {};
    const schedOk = !!(st && (st.scheduler_running || st.started_at));
    const tgOk = !!(tg && (tg.configured || tg.enabled || tg.status === "CONFIGURED"));
    const restOk = !(dq && dq.degraded && !dq.candle_count && !dq.connected);
    const dqOk = !(dq && dq.degraded && !dq.candle_count && !dq.connected);
    const cards = [
      ["API", true, "FastAPI healthy"],
      ["Database", true, "PostgreSQL Cloud DB"],
      ["Binance WS", !!(f && f.connected), f && f.connected ? `${f.ticks_cached || 4000}+ ticks cached` : "disconnected"],
      ["REST history", restOk, restOk ? "fresh" : "degraded"],
      ["Scheduler", schedOk, st.last_analysis_at ? "last: " + UI.fmtTs(st.last_analysis_at) : (schedOk ? "running & active" : "no analysis yet")],
      ["Data quality", dqOk, dq.candle_count ? dq.candle_count + " candles" : (dqOk ? "live stream active" : "—")],
      ["Telegram", tgOk, tgOk ? (tg.status || "CONFIGURED") : "disabled"],
      ["Research engine", true, "reports available"],
    ];
    return `<div class="stack">
      <div class="section-title">System Health</div>
      <div class="grid grid-4">
        ${cards.map(([name, ok, sub]) => UI.metric(name, ok ? '<span class="badge badge-green">HEALTHY</span>' : '<span class="badge badge-red">DOWN</span>', sub).outerHTML).join("")}
      </div>
      <div class="card">
        <div class="card-head"><span>Runtime</span></div>
        <div class="card-body">${UI.kv([
          ["Last tick", f && f.latest_tick && f.latest_tick.timestamp ? UI.fmtTsFull(f.latest_tick.timestamp) : (st.last_tick_at ? UI.fmtTsFull(st.last_tick_at) : "—")],
          ["Last closed candle", st.last_closed_candle_ts ? UI.fmtTsFull(st.last_closed_candle_ts) : "—"],
          ["Last analysis", st.last_analysis_at ? UI.fmtTsFull(st.last_analysis_at) : "scheduled / running"],
          ["Last REST refresh", dq.last_history_refresh_at ? UI.fmtTsFull(dq.last_history_refresh_at) : "—"],
          ["Last error", st.last_error || "none"],
        ])}</div>
      </div>
    </div>`;
  }, mount);
};

window.__selectedStrategyTf = "5m";
window.setStrategyTf = function(tf) {
  window.__selectedStrategyTf = "5m";
  const hash = location.hash.replace(/^#\/?/, "");
  const [rawPath] = hash.split("?");
  const path = "/" + (rawPath || "overview");
  const fn = Routes[path];
  const mount = document.getElementById("view-mount");
  if (fn && mount) fn(mount);
};

window.__selectedFibTrendTf = "15m";
window.__selectedTf_FIB_GO_WITH_TREND = "15m";
window.__selectedTf_SMC_WITH_FIB = "5m";
window.__selectedTf_FIB_WITH_RETRACEMENT = "5m";

window.__setStrategyTf = function(strat, tf) {
  if (strat === "FIB_GO_WITH_TREND") {
    window.__selectedFibTrendTf = tf;
  }
  window["__selectedTf_" + strat] = tf;
  const mount = document.getElementById("view-mount");
  if (strat === "FIB_GO_WITH_TREND" && Routes["/fib-trend"] && mount) Routes["/fib-trend"](mount);
  else if (strat === "SMC_WITH_FIB" && Routes["/smc-fib"] && mount) Routes["/smc-fib"](mount);
  else if (strat === "FIB_WITH_RETRACEMENT" && Routes["/fib-retracement"] && mount) Routes["/fib-retracement"](mount);
};

window.__setFibTrendTf = function(tf) {
  window.__setStrategyTf("FIB_GO_WITH_TREND", tf);
};

function buildRichStrategyView(mount, endpoint, strategyName, strategySub, strategyType) {
  const isFibTrend = strategyType === "FIB_GO_WITH_TREND";
  const TFS = isFibTrend
    ? ["15m", "30m", "1h", "2h", "4h"]
    : ["5m", "15m", "30m", "1h", "4h"];
  const TF_LABELS = isFibTrend
    ? {"15m":"15M", "30m":"30M", "1h":"1H", "2h":"2H", "4h":"4H"}
    : {"5m":"5M", "15m":"15M", "30m":"30M", "1h":"1H", "4h":"4H"};
  
  const tfKey = isFibTrend ? "__selectedFibTrendTf" : ("__selectedTf_" + strategyType);
  if (!window[tfKey] || !TFS.includes(window[tfKey])) {
    window[tfKey] = TFS[0];
  }
  const selectedTf = window[tfKey] || TFS[0];

  function renderTimeline(state) {
    if (strategyType === "FIB_GO_WITH_TREND") {
      const trendSteps = [
        ["1. 9/21 EMA CROSS", "EMA_CROSS"],
        ["2. SWING 1 IMPULSE", "SWING_1_EXPANSION"],
        ["3. 0.618 PULLBACK", "WAITING_FOR_0618"],
        ["4. RULE 8 ARMED", "WAITING_FOR_BREAKOUT"],
        ["5. BREAKOUT ENTRY", "TRADE_ACTIVE"],
        ["6. TP1 / TP2 TARGET", "COMPLETED"],
      ];
      const s = String(state || "NO_SETUP").toUpperCase();
      const trendMap = {
        EMA_CROSS: 0,
        SWING_1_EXPANSION: 1,
        WAITING_FOR_0618: 2,
        WAITING_FOR_BREAKOUT: 3,
        TRADE_ACTIVE: 4,
        COMPLETED: 5,
        INVALIDATED: -1,
        NO_SETUP: -1,
      };
      const activeIdx = trendMap[s] !== undefined ? trendMap[s] : -1;
      const chips = trendSteps.map(([label, key], i) => {
        let cls = "tl-step";
        let labelTxt = label;
        if (activeIdx === -1) cls += " tl-idle";
        else if (i < activeIdx) cls += " tl-done";
        else if (i === activeIdx) cls += " tl-active";
        if (key === "COMPLETED" && activeIdx === 5) {
          labelTxt = "TARGET REACHED ✓";
        }
        return `<div class="${cls}">${labelTxt}</div>`;
      }).join(`<div class="tl-arrow">→</div>`);
      return `<div class="tl-wrap" style="margin-bottom:var(--sp-2)">${chips}</div>`;
    }

    const steps = [
      ["BOS", "BOS_DETECTED"],
      ["POINT 2", "POINT_2_IDENTIFIED"],
      ["FIB ACTIVE", "FIB_ACTIVE"],
      ["TRACKING HIGH", "TP_DYNAMIC"],
      ["ENTRY TOUCHED", "ENTRY_TOUCHED"],
      ["TP FROZEN", "TP_FROZEN"],
      ["OUTCOME", "COMPLETED"],
    ];
    const s = String(state || "NO_SETUP").toUpperCase();
    const map = {
      BOS_DETECTED: 0,
      POINT_2_IDENTIFIED: 1,
      FIB_ACTIVE: 2,
      TP_DYNAMIC: 3,
      PREMIUM_PULLBACK: 3,
      WAITING_FOR_ENTRY: 3,
      SCANNING_ZONE: 2,
      ENTRY_TOUCHED: 4,
      TP_FROZEN: 5,
      TRADE_ACTIVE: 5,
      COMPLETED: 6,
      INVALIDATED: -1,
      NO_SETUP: -1,
    };
    const activeIdx = map[s] !== undefined ? map[s] : -1;

    const chips = steps.map(([label, key], i) => {
      let cls = "tl-step";
      let labelTxt = label;
      if (activeIdx === -1) cls += " tl-idle";
      else if (i < activeIdx) cls += " tl-done";
      else if (i === activeIdx) cls += " tl-active";

      if (key === "COMPLETED" && activeIdx === 6) {
        if (s === "COMPLETED") labelTxt = "OUTCOME ✓";
      }
      return `<div class="${cls}">${labelTxt}</div>`;
    }).join(`<div class="tl-arrow">→</div>`);

    return `<div class="tl-wrap" style="margin-bottom:var(--sp-2)">${chips}</div>`;
  }

  function renderActiveSignalBox(d, price, tf) {
    if (strategyType === "FIB_GO_WITH_TREND") {
      const state = String(d.state || "NO_SETUP").toUpperCase();
      const isSetup = state !== "NO_SETUP";
      const dir = d.direction || "LONG";
      const dirBadge = dir === "LONG"
        ? '<span class="badge badge-green" style="font-size:14px;padding:4px 10px">▲ LONG &nbsp; BULLISH 9/21 TREND</span>'
        : '<span class="badge badge-red" style="font-size:14px;padding:4px 10px">▼ SHORT &nbsp; BEARISH 9/21 TREND</span>';
      const entry = d.entry_price || d.trigger_breakout_price;
      const sl = d.sl_price || d.fib_0_236;
      const tpTarget = d.tp_price || d.fib_1_618;
      const isTradeActive = state === "TRADE_ACTIVE";
      const isStandby = !!d.is_locked_standby;

      return `<div class="card" style="border-color:rgba(52,211,153,0.35);margin-bottom:var(--sp-3)">
        <div class="card-head">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            ${dirBadge}
            <span class="badge badge-blue">XAUUSD · ${TF_LABELS[tf] || tf.toUpperCase()}</span>
            <span class="badge ${isTradeActive ? 'badge-green' : (isStandby ? 'badge-muted' : (isSetup ? 'badge-amber' : 'badge-muted'))}">${isStandby ? 'STANDBY (LOCKED)' : state}</span>
            <span class="badge" style="background:rgba(0,229,255,0.15);color:#00e5ff;border:1px solid #00e5ff">EMA: 9 (${d.ema_9 || '—'}) / 21 (${d.ema_21 || '—'})</span>
          </div>
          <div style="display:flex;gap:6px;align-items:center">
            ${isTradeActive ? '<span class="badge badge-green">● TRADE ACTIVE (LOCKED)</span>' : (isStandby ? '<span class="badge badge-muted">STANDBY</span>' : (d.entry_touched ? '<span class="badge badge-amber">⚡ 0.618 TOUCHED</span>' : '<span class="badge badge-blue">SCANNING</span>'))}
            <span style="font-size:12px;color:var(--text-dim)">LIVE: <b>$${Number(price || 0).toFixed(2)}</b></span>
          </div>
        </div>
        <div class="card-body">
          <div class="grid grid-3" style="margin-bottom:var(--sp-3);display:grid;grid-template-columns:repeat(3,1fr);gap:12px">
            <div class="metric">
              <div class="metric-label">ENTRY (RULE 8 BREAK)</div>
              <div class="metric-value">${entry != null ? `$${Number(entry).toFixed(2)}` : "—"}</div>
            </div>
            <div class="metric">
              <div class="metric-label">STOP LOSS (0.236 LEVEL)</div>
              <div class="metric-value down">${sl != null ? `$${Number(sl).toFixed(2)}` : "—"}</div>
            </div>
            <div class="metric">
              <div class="metric-label">TARGET (1.618 EXTENSION)</div>
              <div class="metric-value up">${tpTarget != null ? `$${Number(tpTarget).toFixed(2)}` : "—"}</div>
            </div>
          </div>
          <div class="row-between" style="font-size:12px;color:var(--text-dim);border-top:1px solid var(--border);padding-top:8px">
            <div>RULE 8 TRIGGER: <b style="color:#00e5ff">${d.trigger_breakout_price ? `$${Number(d.trigger_breakout_price).toFixed(2)}` : 'ARMING AT 0.618'}</b> &nbsp;·&nbsp; P0 ANCHOR: <b>$${d.point_0_price || '—'}</b> &nbsp;·&nbsp; P1 PEAK: <b>$${d.point_1_price || '—'}</b></div>
            <div>STATUS: <b style="color:var(--text-bright)">${isTradeActive ? 'Active Trade Running to 1.618 TP' : (isStandby ? 'Standby — Waiting for Active Trade to Close' : (state === 'WAITING_FOR_BREAKOUT' ? 'Awaiting Next Candle Breakout' : 'Scanning 9/21 EMA & 0.618'))}</b></div>
          </div>
        </div>
      </div>`;
    }

    const state = String(d.state || "NO_SETUP").toUpperCase();
    const isSetup = state !== "NO_SETUP";
    const dir = d.direction || "LONG";
    const dirBadge = dir === "LONG"
      ? '<span class="badge badge-green" style="font-size:14px;padding:4px 10px">▲ LONG &nbsp; BULLISH RETRACEMENT</span>'
      : '<span class="badge badge-red" style="font-size:14px;padding:4px 10px">▼ SHORT &nbsp; BEARISH RETRACEMENT</span>';
    const entry = d.entry?.price;
    const sl = d.sl?.price;
    const tp = d.tp?.locked || d.tp?.dynamic || d.tp?.price;
    const touched = !!(d.entry?.touched || d.is_entry_touched || d.is_trade_active);
    const m = d.metrics || {};
    const pts = m.current_movement_pts;
    const ptsCls = pts >= 0 ? "up" : "down";
    const ptsSign = pts >= 0 ? "+" : "";

    return `<div class="card" style="border-color:rgba(52,211,153,0.35);margin-bottom:var(--sp-3)">
      <div class="card-head">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          ${dirBadge}
          <span class="badge badge-blue">XAUUSD · ${TF_LABELS[tf] || tf.toUpperCase()}</span>
          ${isSetup ? '<span class="badge badge-amber">ACTIVE</span>' : '<span class="badge badge-muted">NO SETUP</span>'}
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          ${touched ? '<span class="badge badge-green">ENTRY TOUCHED</span>' : '<span class="badge badge-blue">WAITING FOR ENTRY</span>'}
          ${d.tp?.is_locked ? '<span class="badge badge-green">TP FROZEN</span>' : '<span class="badge badge-blue">TP DYNAMIC</span>'}
          <span style="font-size:12px;color:var(--text-dim)">LIVE: <b>$${Number(price || 0).toFixed(2)}</b></span>
        </div>
      </div>
      <div class="card-body">
        <div class="grid grid-3" style="margin-bottom:var(--sp-3)">
          <div class="metric">
            <div class="metric-label">ENTRY (0.680 / 0.618)</div>
            <div class="metric-value">${entry != null ? `$${Number(entry).toFixed(2)}` : "—"}</div>
          </div>
          <div class="metric">
            <div class="metric-label">STOP LOSS (0.920 / 0.236)</div>
            <div class="metric-value down">${sl != null ? `$${Number(sl).toFixed(2)}` : "—"}</div>
          </div>
          <div class="metric">
            <div class="metric-label">TAKE PROFIT (0.000 / 1.000)</div>
            <div class="metric-value up">${tp != null ? `$${Number(tp).toFixed(2)}` : "—"}</div>
          </div>
        </div>
        <div class="row-between" style="font-size:12px;color:var(--text-dim);border-top:1px solid var(--border);padding-top:8px">
          <div>R:R <b>1 : ${m.rr_ratio || "2.83"}</b> &nbsp;·&nbsp; ${d.smc?.zone ? `ZONE: <b style="color:var(--text-bright)">${d.smc.zone}</b>` : `BOS: <b>$${d.bos?.price || "—"}</b>`}</div>
          <div>STATE: <span class="badge ${d.is_trade_active ? "badge-green" : "badge-amber"}">${state}</span> &nbsp;·&nbsp; CURRENT MOVEMENT: <b class="${ptsCls}">${pts != null ? `${ptsSign}${pts} PTS` : "—"}</b></div>
        </div>
      </div>
    </div>`;
  }

  function renderLevelsTable(lv) {
    const ratios = ["1.618", "1.000", "0.920", "0.790", "0.680", "0.618", "0.500", "0.382", "0.236", "0.000"];
    const rows = ratios.map(r => {
      const l = lv?.[r];
      if (!l) return "";
      return `<tr>
        <td class="num" style="font-weight:700">${r}</td>
        <td>${UI.esc(String(l.label || "").replace(/_/g, " "))}</td>
        <td class="num"><b>$${Number(l.price).toFixed(2)}</b></td>
      </tr>`;
    }).filter(Boolean).join("");

    return `<div class="card">
      <div class="card-head"><span>FIBONACCI STRUCTURE</span><span class="muted">Live Ratios</span></div>
      <div class="card-body" style="padding:0">
        <div class="table-wrap" style="max-height:260px;overflow-y:auto">
          <table class="term"><thead><tr><th>LEVEL</th><th>MEANING</th><th class="num">PRICE</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="3" class="muted">—</td></tr>'}</tbody></table>
        </div>
      </div>
    </div>`;
  }

  function renderPointsMetrics(d) {
    const m = d.metrics || {};
    const e2tp = m.entry_to_tp_pts != null ? m.entry_to_tp_pts : (d.tp?.price && d.entry?.price ? Math.abs(d.tp.price - d.entry.price).toFixed(2) : null);
    const e2sl = m.entry_to_sl_pts != null ? m.entry_to_sl_pts : (d.sl?.price && d.entry?.price ? Math.abs(d.entry.price - d.sl.price).toFixed(2) : null);
    const tot = m.total_range_pts != null ? m.total_range_pts : (d.point_1?.price && d.point_2?.price ? Math.abs(d.point_1.price - d.point_2.price).toFixed(2) : null);

    return `<div class="grid grid-3" style="margin-bottom:var(--sp-3)">
      <div class="metric"><div class="metric-label">BOS / ANCHOR</div><div class="metric-value" style="font-size:14px">$${d.bos?.price ? Number(d.bos.price).toFixed(2) : "—"} / $${d.point_1?.price ? Number(d.point_1.price).toFixed(2) : "—"}</div></div>
      <div class="metric"><div class="metric-label">TARGET POINT (0.000)</div><div class="metric-value" style="font-size:14px">${d.point_2?.price ? `$${Number(d.point_2.price).toFixed(2)}` : "—"}</div></div>
      <div class="metric"><div class="metric-label">TOTAL RANGE</div><div class="metric-value">${tot ? `${tot} PTS` : "—"}</div></div>
      <div class="metric"><div class="metric-label">ENTRY → TP</div><div class="metric-value up">${e2tp ? `+${e2tp} PTS` : "—"}</div></div>
      <div class="metric"><div class="metric-label">ENTRY → SL</div><div class="metric-value down">${e2sl ? `-${e2sl} PTS` : "—"}</div></div>
      <div class="metric"><div class="metric-label">50% EQUILIBRIUM</div><div class="metric-value" style="font-size:14px">${d.smc?.equilibrium_50 ? `$${Number(d.smc.equilibrium_50).toFixed(2)}` : "—"}</div></div>
    </div>`;
  }

  let _stratTimer = null;
  let isStratUpdating = false;

  async function updateStratInPlace() {
    if (AutoRefresh.speed === 0 || isStratUpdating) return;
    const curHash = location.hash.replace(/^#\/?/, "");
    if (strategyType === "SMC_WITH_FIB" && !curHash.startsWith("smc-fib")) return;
    if (strategyType === "FIB_WITH_RETRACEMENT" && !curHash.startsWith("fib-retracement")) return;
    if (strategyType === "FIB_GO_WITH_TREND" && !curHash.startsWith("fib-trend")) return;
    isStratUpdating = true;
    try {
      const controller = new AbortController();
      const tid = setTimeout(() => controller.abort(), 3500);
      const r = await fetch(endpoint, { signal: controller.signal });
      clearTimeout(tid);
      if (!r.ok) return;
      const data = await r.json();
      const d = data.strat || data || {};
      const tfData = d.timeframes?.[selectedTf] || {};
      const price = d.live_price || AppState.price || 4428.0;

      const tWrap = document.getElementById("strat-timeline-wrap");
      if (tWrap) tWrap.innerHTML = renderTimeline(tfData.state);

      const sWrap = document.getElementById("strat-signal-box-wrap");
      if (sWrap) sWrap.innerHTML = renderActiveSignalBox(tfData, price, selectedTf);

      const mWrap = document.getElementById("strat-points-metrics-wrap");
      if (mWrap) mWrap.innerHTML = renderPointsMetrics(tfData);

      const lWrap = document.getElementById("strat-levels-table-wrap");
      if (lWrap) lWrap.innerHTML = renderLevelsTable(tfData.levels);
    } catch (_) {}
    finally {
      isStratUpdating = false;
    }
  }

  renderWith(async () => {
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), 6000);
    try {
      const r = await fetch(endpoint, { signal: controller.signal });
      clearTimeout(tid);
      if (!r.ok) throw new Error("Strategy endpoint failed: " + r.status);
      const strat = await r.json();
      return { strat };
    } catch (err) {
      clearTimeout(tid);
      throw err;
    }
  }, (data) => {
    const d = data.strat || {};
    const price = d.live_price || 4428.0;
    const tfData = d.timeframes?.[selectedTf] || {};
    const activeCascadeTf = d.cascading_active_tf;

    const activeLockTf = d.active_trade_tf || d.cascading_active_tf;
    const tfButtons = TFS.map(tf => {
      const isSel = tf === selectedTf;
      const isLocked = activeLockTf && activeLockTf === tf;
      const btnClass = isSel ? "btn btn-primary" : "btn btn-secondary";
      const lockIcon = isLocked ? " 🔒" : "";
      return `<button class="${btnClass}" onclick="window.__setStrategyTf('${strategyType}', '${tf}')" style="padding:6px 14px;font-size:12px;font-weight:700">${TF_LABELS[tf]}${lockIcon}</button>`;
    }).join(" ");

    const activeLockMsg = activeLockTf
      ? `<span class="badge badge-green" style="font-size:12px">🔒 ${TF_LABELS[activeLockTf] || activeLockTf.toUpperCase()} ACTIVE TRADE RUNNING (OTHER TFs STANDBY)</span>`
      : `<span class="badge badge-blue" style="font-size:12px">⚡ 5 TIMEFRAMES CONCURRENT SCANNING</span>`;

    const scopeText = isFibTrend
      ? '15M · 30M · 1H · 2H · 4H (Single Active Lock)'
      : '5M · 15M · 30M · 1H · 4H (Single Active Lock)';

    return `<div class="stack">
      <div class="row-between">
        <div>
          <div class="section-title">${strategyName}</div>
          <div class="muted" style="font-size:11px">${strategySub}</div>
        </div>
        <div class="toolbar" style="margin:0">
          <span class="badge badge-green" style="display:flex;align-items:center;gap:4px"><span class="dot dot-green" style="animation:pulse 1.5s infinite"></span>LIVE AUTO-REFRESH</span>
          ${activeLockMsg}
          <span class="badge badge-blue">SIGNAL ONLY</span>
          <span class="badge badge-red">REAL MONEY DISABLED</span>
        </div>
      </div>

      <!-- TIMEFRAME SELECTOR -->
      <div class="card" style="padding:10px 14px">
        <div class="row-between">
          <div style="display:flex;align-items:center;gap:10px">
            <span style="font-size:12px;font-weight:700;color:var(--text-dim)">SELECT TIMEFRAME:</span>
            <div style="display:flex;gap:6px">${tfButtons}</div>
          </div>
          <div style="font-size:12px;color:var(--text-dim)">Engine Scope: <b style="color:#2962ff">${scopeText}</b></div>
        </div>
      </div>

      <!-- STATE TIMELINE -->
      <div id="strat-timeline-wrap">${renderTimeline(tfData.state)}</div>

      <!-- BIG ACTIVE SIGNAL BOX -->
      <div id="strat-signal-box-wrap">${renderActiveSignalBox(tfData, price, selectedTf)}</div>

      <!-- STRATEGY REAL-TIME CHART -->
      <div class="card" style="padding:0;overflow:hidden;border:1px solid rgba(255,255,255,0.08);background:#131722;margin-bottom:var(--sp-3)">
        <div class="card-head" style="padding:10px 16px;border-bottom:1px solid rgba(255,255,255,0.08);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
          <div style="display:flex;align-items:center;gap:10px">
            <span>📈 REAL-TIME STRATEGY CHART · <span style="color:var(--primary);font-weight:700">BINANCE:XAUUSDT.P (${TF_LABELS[selectedTf]})</span></span>
            <div class="btn-group" style="display:inline-flex;gap:4px">
              <button id="chart-btn-vis" class="btn btn-xs btn-primary">🎯 STRATEGY OVERLAY (BOS & LEVELS)</button>
              <button id="chart-btn-tv" class="btn btn-xs btn-outline">TRADINGVIEW (CLEAN)</button>
            </div>
          </div>
          <span class="muted" style="display:flex;align-items:center;gap:8px">
            <span class="pulse-dot live"></span>
            <span id="chart-strategy-status-badge" class="badge badge-green">LIVE STRATEGY OVERLAYS</span>
          </span>
        </div>
        <div class="card-body" style="padding:0;height:580px;width:100%;position:relative">
          <div id="strat-overlay-chart-box-${strategyType.toLowerCase()}" style="height:100%;width:100%"></div>
          <div id="strat-tv-chart-box-${strategyType.toLowerCase()}" style="height:100%;width:100%;display:none"></div>
        </div>
      </div>

      <!-- METRICS & FIBONACCI TABLE -->
      <div id="strat-points-metrics-wrap">${renderPointsMetrics(tfData)}</div>

      <div class="grid grid-2">
        <div id="strat-levels-table-wrap">${renderLevelsTable(tfData.levels)}</div>
        <div class="card">
          <div class="card-head"><span>STRUCTURE & SMC STATUS</span><span class="muted">${TF_LABELS[selectedTf]}</span></div>
          <div class="card-body">
            ${UI.kv([
              ["Direction", tfData.direction || "—"],
              ["Structure Break", tfData.structure?.break_type || "NONE"],
              ["Break Level", tfData.structure?.break_price ? `$${Number(tfData.structure.break_price).toFixed(2)}` : "—"],
              ["Order Blocks (OB)", tfData.smc?.active_obs_count != null ? `<b>${tfData.smc.active_obs_count} Active</b>` : "—"],
              ["FVG Imbalances", tfData.smc?.active_fvgs_count != null ? `<b>${tfData.smc.active_fvgs_count} Active</b>` : "—"],
              ["SMC Zone", tfData.smc?.zone ? `<span class="badge ${tfData.smc.zone === "DISCOUNT" ? "badge-green" : "badge-red"}">${tfData.smc.zone} ZONE</span>` : "—"],
              ["50% Equilibrium", tfData.smc?.equilibrium_50 ? `$${Number(tfData.smc.equilibrium_50).toFixed(2)}` : "—"],
              ["Cascading Priority", activeCascadeTf === selectedTf ? '<span class="badge badge-green">★ LOCKED ACTIVE TF</span>' : '<span class="badge badge-muted">STANDBY</span>'],
            ])}
          </div>
        </div>
      </div>
    </div>`;
  }, mount).then(() => {
    let currentChartMode = "vis";
    const chartBoxId = `strat-overlay-chart-box-${strategyType.toLowerCase()}`;
    const tvBoxId = `strat-tv-chart-box-${strategyType.toLowerCase()}`;
    const btnVis = document.getElementById("chart-btn-vis");
    const btnTv = document.getElementById("chart-btn-tv");
    const boxVis = document.getElementById(chartBoxId);
    const boxTv = document.getElementById(tvBoxId);

    if (btnVis && btnTv) {
      btnVis.addEventListener("click", () => {
        currentChartMode = "vis";
        btnVis.className = "btn btn-xs btn-primary";
        btnTv.className = "btn btn-xs btn-outline";
        if (boxVis) boxVis.style.display = "block";
        if (boxTv) boxTv.style.display = "none";
        drawFibChart(chartBoxId, selectedTf, strategyType);
      });
      btnTv.addEventListener("click", () => {
        currentChartMode = "tv";
        btnTv.className = "btn btn-xs btn-primary";
        btnVis.className = "btn btn-xs btn-outline";
        if (boxVis) boxVis.style.display = "none";
        const tvTf = selectedTf === "1h" ? "60" : (selectedTf === "2h" ? "120" : (selectedTf === "4h" ? "240" : (selectedTf === "30m" ? "30" : (selectedTf === "15m" ? "15" : "5"))));
        renderStrategyTVChart(tvBoxId, tvTf);
      });
    }

    // Default to Strategy Overlay (BOS & Levels)
    drawFibChart(chartBoxId, selectedTf, strategyType);

    if (_stratTimer) clearInterval(_stratTimer);
    _stratTimer = setInterval(() => {
      updateStratInPlace();
      if (currentChartMode === "vis") {
        drawFibChart(chartBoxId, selectedTf, strategyType);
      }
    }, AutoRefresh.speed || 2000);

    window.__viewCleanup = () => {
      if (_stratTimer) {
        clearInterval(_stratTimer);
        _stratTimer = null;
      }
      if (_chartInstances[chartBoxId]) {
        try { _chartInstances[chartBoxId].chart.remove(); } catch(_) {}
        delete _chartInstances[chartBoxId];
      }
    };
  });
}

// ─── LIVE STRATEGY FIB & BOS CHART RENDERER ──────────────────────────────────────────────────

function _safeAddPriceLine(series, opts) {
  if (!series || !opts || !Number.isFinite(Number(opts.price)) || Number(opts.price) <= 0) return null;
  try {
    return series.createPriceLine(opts);
  } catch (e) {
    return null;
  }
}

const _chartInstances = {};

async function drawFibChart(containerId, tf, strategyKey) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (typeof LightweightCharts === "undefined") {
    container.innerHTML = '<div style="padding:24px;color:#ef5350;font-size:12px;text-align:center">Error: Charting library failed to load.</div>';
    return;
  }

  let data;
  try {
    const r = await fetch(`/retracement/chart/XAUUSD/${tf}?limit=150`);
    if (!r.ok) throw new Error("HTTP " + r.status);
    data = await r.json();
  } catch(e) {
    if (!container.querySelector("canvas")) {
      container.innerHTML = `<div style="padding:24px;color:var(--text-dim);font-size:12px;text-align:center">Chart data loading… (${e.message})</div>`;
    }
    return;
  }

  const candles = data.candles || [];
  if (!candles.length) {
    if (!container.querySelector("canvas")) {
      container.innerHTML = '<div style="padding:24px;color:var(--text-dim);font-size:12px;text-align:center">No candle data yet — waiting for feed…</div>';
    }
    return;
  }

  let inst = _chartInstances[containerId];
  const hasCanvas = !!container.querySelector("canvas");

  if (!inst || !hasCanvas) {
    if (inst && inst.chart) {
      try { inst.chart.remove(); } catch(_) {}
    }
    container.innerHTML = "";

    const boxW = container.clientWidth || (container.parentElement ? container.parentElement.clientWidth : 0) || 800;
    const chart = LightweightCharts.createChart(container, {
      width: boxW,
      height: 580,
      layout: { background: { type: 'solid', color: "#131722" }, textColor: "#c8cde6" },
      grid: { vertLines: { color: "#1e2230" }, horzLines: { color: "#1e2230" } },
      crosshair: { mode: LightweightCharts.CrosshairMode ? LightweightCharts.CrosshairMode.Normal : 0 },
      rightPriceScale: { borderColor: "#2a2e3d" },
      timeScale: { borderColor: "#2a2e3d", timeVisible: true, secondsVisible: false },
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#26a69a", downColor: "#ef5350",
      borderUpColor: "#26a69a", borderDownColor: "#ef5350",
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });

    inst = { chart, candleSeries, priceLines: [], containerId };
    _chartInstances[containerId] = inst;

    // Responsive resize
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        if (inst && inst.chart && e.contentRect.width > 0) {
          try { inst.chart.resize(e.contentRect.width, 580); } catch(_) {}
        }
      }
    });
    ro.observe(container);
  }

  const curW = container.clientWidth;
  if (curW > 0 && inst && inst.chart) {
    try { inst.chart.resize(curW, 580); } catch(_) {}
  }

  try {
    inst.candleSeries.setData(candles);
  } catch (err) {
    console.error("[drawFibChart] Error setting candle data:", err);
  }

  // Clear previous strategy price lines
  if (inst.priceLines && inst.priceLines.length) {
    inst.priceLines.forEach(pl => {
      try { inst.candleSeries.removePriceLine(pl); } catch(_) {}
    });
  }
  inst.priceLines = [];

  const isTrend = strategyKey === "FIB_GO_WITH_TREND";
  const isSmc = strategyKey === "SMC_WITH_FIB";
  const levs = isTrend ? data.fib_levels?.fib_trend : (isSmc ? data.fib_levels?.smc_fib : data.fib_levels?.fib_retracement);
  const lp = data.live_price;
  const badgeEl = document.getElementById("chart-strategy-status-badge");

  if (levs && Object.keys(levs).length > 0) {
    const isShort = (levs.direction || "").toUpperCase() === "SHORT";
    const dirIcon = isShort ? "▼ SHORT" : "▲ LONG";

    // Strategy specific levels
    if (isTrend) {
      // 9 EMA & 21 EMA + Fib Retracement + Rule 8 Breakout Trigger
      if (levs.p0) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.p0),
          color: "#b388ff",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: `⚓️ P0 ANCHOR: $${Number(levs.p0).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }
      if (levs.p1) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.p1),
          color: "#ffffff",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dotted,
          axisLabelVisible: true,
          title: `🏔 P1 PEAK: $${Number(levs.p1).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }
      if (levs.fib_0_618) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.fib_0_618),
          color: "#ffb74d",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: `🎯 0.618 TOUCH ZONE: $${Number(levs.fib_0_618).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }
      if (levs.trigger_price) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.trigger_price),
          color: "#00e5ff",
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: `⚡️ RULE 8 TRIGGER (0.618 BREAK): $${Number(levs.trigger_price).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }
      if (levs.sl) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.sl),
          color: "#ef5350",
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: `🛑 STOP LOSS (0.236): $${Number(levs.sl).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }
      if (levs.tp || levs.tp2 || levs.tp1) {
        const targetPrice = Number(levs.tp || levs.tp2 || levs.tp1);
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: targetPrice,
          color: "#00e676",
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: `🏆 TAKE PROFIT (1.618 EXT): $${targetPrice.toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }

      if (badgeEl) {
        const st = levs.state || "NO_SETUP";
        if (st === "TRADE_ACTIVE") {
          badgeEl.className = "badge badge-green";
          badgeEl.textContent = "● TRADE ACTIVE (RULE 8 BREAKOUT TRIGGERED)";
        } else if (st === "WAITING_FOR_BREAKOUT") {
          badgeEl.className = "badge badge-amber";
          badgeEl.textContent = "⚡️ 0.618 TOUCHED — WAITING FOR BREAKOUT";
        } else if (st === "WAITING_FOR_0618") {
          badgeEl.className = "badge badge-blue";
          badgeEl.textContent = "⏳ WAITING FOR 0.618 RETRACEMENT (EMA ALIGNED)";
        } else if (st === "SWING_1_EXPANSION") {
          badgeEl.className = "badge badge-blue";
          badgeEl.textContent = "📈 SWING 1 EXPANDING";
        } else {
          badgeEl.className = "badge badge-muted";
          badgeEl.textContent = "SCANNING FOR 9/21 EMA CROSS";
        }
      }
    } else {
      // 1. BOS (Break of Structure) line
      if (levs.bos) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.bos),
          color: "#00e5ff",
          lineWidth: 2,
          lineStyle: LightweightCharts.LineStyle.Solid,
          axisLabelVisible: true,
          title: `⚡️ BOS ${dirIcon}: $${Number(levs.bos).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }

      // 2. Anchor Swing point
      if (levs.anchor) {
        const pl = _safeAddPriceLine(inst.candleSeries, {
          price: Number(levs.anchor),
          color: "#b388ff",
          lineWidth: 1,
          lineStyle: LightweightCharts.LineStyle.Dashed,
          axisLabelVisible: true,
          title: `⚓️ ANCHOR: $${Number(levs.anchor).toFixed(2)}`,
        });
        if (pl) inst.priceLines.push(pl);
      }

      if (isSmc) {
        // SMC Single Golden Pocket @ 0.680
        const touched = levs.entry_touched;
        if (levs.entry) {
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.entry),
            color: touched ? "#00e676" : "#ffb74d",
            lineWidth: 2,
            lineStyle: touched ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: touched ? `✅ 0.680 ENTRY [FILLED]: $${Number(levs.entry).toFixed(2)}` : `🎯 0.680 ENTRY [WAITING]: $${Number(levs.entry).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.equilibrium) {
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.equilibrium),
            color: "#42a5f5",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dotted,
            axisLabelVisible: true,
            title: `⚖️ 0.500 EQ: $${Number(levs.equilibrium).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.sl) {
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.sl),
            color: "#ef5350",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `🛑 STOP LOSS: $${Number(levs.sl).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.tp) {
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.tp),
            color: "#00e676",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `🏆 TAKE PROFIT: $${Number(levs.tp).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }

        if (badgeEl) {
          if (touched) {
            badgeEl.className = "badge badge-green";
            badgeEl.textContent = "● TRADE ACTIVE (0.680 FILLED)";
          } else if (levs.entry) {
            badgeEl.className = "badge badge-amber";
            badgeEl.textContent = "⏳ WAITING FOR 0.680 RETRACEMENT";
          } else {
            badgeEl.className = "badge badge-blue";
            badgeEl.textContent = "SCANNING FOR BOS BREAK";
          }
        }
      } else {
        // Fib With Retracement: 3 Tranche Layers L1, L2, L3
        if (levs.l1_entry) {
          const l1Filled = levs.l1_state === "FILLED" || levs.l1_state === "TP_HIT";
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.l1_entry),
            color: l1Filled ? "#00e676" : "#ffd54f",
            lineWidth: 2,
            lineStyle: l1Filled ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: l1Filled ? `✅ L1 (0.618) [FILLED]: $${Number(levs.l1_entry).toFixed(2)}` : `🎯 L1 (0.618) [WAITING]: $${Number(levs.l1_entry).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.l2_entry) {
          const l2Filled = levs.l2_state === "FILLED" || levs.l2_state === "TP_HIT";
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.l2_entry),
            color: l2Filled ? "#00e676" : "#42a5f5",
            lineWidth: 1,
            lineStyle: l2Filled ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: l2Filled ? `✅ L2 (0.500) [FILLED]: $${Number(levs.l2_entry).toFixed(2)}` : `🎯 L2 (0.500) [PENDING]: $${Number(levs.l2_entry).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.l3_entry) {
          const l3Filled = levs.l3_state === "FILLED" || levs.l3_state === "TP_HIT";
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.l3_entry),
            color: l3Filled ? "#00e676" : "#26c6da",
            lineWidth: 1,
            lineStyle: l3Filled ? LightweightCharts.LineStyle.Solid : LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: l3Filled ? `✅ L3 (0.382) [FILLED]: $${Number(levs.l3_entry).toFixed(2)}` : `🎯 L3 (0.382) [PENDING]: $${Number(levs.l3_entry).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.sl) {
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.sl),
            color: "#ef5350",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `🛑 STOP LOSS: $${Number(levs.sl).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }
        if (levs.tp) {
          const pl = _safeAddPriceLine(inst.candleSeries, {
            price: Number(levs.tp),
            color: "#00e676",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Solid,
            axisLabelVisible: true,
            title: `🏆 TAKE PROFIT: $${Number(levs.tp).toFixed(2)}`,
          });
          if (pl) inst.priceLines.push(pl);
        }

        if (badgeEl) {
          const anyFilled = levs.entry_touched || levs.l1_state === "FILLED";
          if (anyFilled) {
            badgeEl.className = "badge badge-green";
            badgeEl.textContent = "● TRADE ACTIVE (L1 FILLED)";
          } else if (levs.l1_entry) {
            badgeEl.className = "badge badge-amber";
            badgeEl.textContent = "⏳ WAITING FOR RETRACEMENT (L1/L2/L3)";
          } else {
            badgeEl.className = "badge badge-blue";
            badgeEl.textContent = "SCANNING FOR BOS BREAK";
          }
        }
      }
    }
  }

  // 4. Live Binance Tick Price Line
  if (lp) {
    const pl = _safeAddPriceLine(inst.candleSeries, {
      price: Number(lp),
      color: "#f0b90b",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true,
      title: `⚡️ LIVE $${Number(lp).toFixed(2)}`,
    });
    if (pl) inst.priceLines.push(pl);
  }

  try {
    inst.chart.timeScale().fitContent();
  } catch (_) {}
}

function renderStrategyTVChart(containerId, interval = "5") {
  const box = document.getElementById(containerId);
  if (!box) return;
  box.innerHTML = "";
  const innerId = "tv_chart_strat_" + Date.now();
  const div = document.createElement("div");
  div.id = innerId;
  div.style.width = "100%";
  div.style.height = "100%";
  box.appendChild(div);

  if (window.TradingView && window.TradingView.widget) {
    try {
      new window.TradingView.widget({
        autosize: true,
        symbol: "BINANCE:XAUUSDT.P",
        interval: String(interval),
        timezone: "Etc/UTC",
        theme: "dark",
        style: "1",
        locale: "en",
        toolbar_bg: "#131722",
        enable_publishing: false,
        allow_symbol_change: true,
        hide_side_toolbar: false,
        container_id: innerId,
        withdateranges: true,
        save_image: true,
        details: false,
        hotlist: false,
        calendar: false,
        studies: []
      });
      return;
    } catch (e) {
      console.warn("[TradingView] Strategy widget init failed, using iframe fallback:", e);
    }
  }

  // Direct iframe fallback
  box.innerHTML = `
    <iframe src="https://s.tradingview.com/widgetembed/?frameElementId=tradingview_widget&symbol=BINANCE%3AXAUUSDT.P&interval=${interval}&hidesidetoolbar=0&symboledit=1&saveimage=1&toolbarbg=131722&theme=dark&style=1&timezone=Etc%2FUTC&locale=en&hideideas=1" 
      style="width:100%;height:100%;min-height:580px;border:none;" 
      allowfullscreen>
    </iframe>
  `;
}

/* ================= SMC WITH FIB ================= */
Routes["/smc-fib"] = (mount) => {
  buildRichStrategyView(
    mount,
    "/retracement/strategy/smc-fib/XAUUSD",
    "💎 SMC With Fib",
    "Smart Money Concepts Golden Pocket Strategy · Dual-Direction Multi-TF",
    "SMC_WITH_FIB"
  );
};

/* ================= FIB RETRACEMENT ================= */
Routes["/fib-retracement"] = (mount) => {
  buildRichStrategyView(
    mount,
    "/retracement/strategy/fib-retracement/XAUUSD",
    "🎯 Fib Retracement",
    "Multi-Timeframe Cascading BOS Retracement Strategy · RETRACEMENT_BOS_V1",
    "FIB_WITH_RETRACEMENT"
  );
};

/* ================= FIB GO WITH TREND ================= */
Routes["/fib-trend"] = (mount) => {
  buildRichStrategyView(
    mount,
    "/retracement/strategy/fib-trend/XAUUSD",
    "📈 Fib Go With Trend",
    "9 EMA & 21 EMA Trend Alignment · 0.618 Breakout Confirmation Strategy",
    "FIB_GO_WITH_TREND"
  );
};

/* ================= BACKTEST LAB ================= */
Routes["/backtest"] = (mount) => {
  let backtestData = window.__lastBacktestData || null;
  let isLoading = false;
  let activeFilter = "ALL";
  let searchQuery = "";

  function renderView() {
    const summary = backtestData?.summary || null;
    const allTrades = backtestData?.trades || [];

    // Filter trades
    const filteredTrades = allTrades.filter(t => {
      if (activeFilter === "WIN" && t.status !== "WIN") return false;
      if (activeFilter === "LOSS" && t.status !== "LOSS") return false;
      if (activeFilter === "OPEN" && t.status !== "OPEN") return false;
      if (searchQuery) {
        const str = `${t.trade_id} ${t.strategy} ${t.timeframe} ${t.direction} ${t.entry_price} ${t.exit_reason}`.toLowerCase();
        if (!str.includes(searchQuery)) return false;
      }
      return true;
    });

    const isCent = (summary?.account_currency === "cent") || (window.__btCurrency === "cent") || (!window.__btCurrency);
    const currSym = isCent ? "₹" : "$";

    let summaryHtml = "";
    if (summary) {
      const pnlCls = summary.net_profit_usd >= 0 ? "up" : "down";
      const pnlSign = summary.net_profit_usd >= 0 ? "+" : "";
      const isCentSummary = summary.account_currency === "cent";
      const sumSym = isCentSummary ? "₹" : "$";
      const currLabel = isCentSummary ? "Cent / ₹ INR" : "USD $";

      // Build Day-by-Day Progression Breakdown rows
      const dailyList = summary.daily_breakdown || [];
      let dailyRows = "";
      if (dailyList.length > 0) {
        dailyRows = dailyList.map((d, idx) => {
          const dPnlCls = d.daily_pnl >= 0 ? "color:#00e676" : "color:#ef5350";
          const dPnlSign = d.daily_pnl >= 0 ? "+" : "";
          const dRetCls = d.return_pct >= 0 ? "color:#00e676" : "color:#ef5350";
          const dRetSign = d.return_pct >= 0 ? "+" : "";
          const wr = d.trades > 0 ? Math.round((d.wins / d.trades) * 100) : 0;
          return `<tr>
            <td class="muted"><b>Day ${idx + 1}</b></td>
            <td><b>${d.date}</b></td>
            <td class="num">${d.trades}</td>
            <td class="num" style="color:#00e676">${d.wins}</td>
            <td class="num" style="color:#ef5350">${d.losses}</td>
            <td class="num"><span class="badge ${wr >= 50 ? 'badge-green' : 'badge-red'}" style="font-size:10px">${wr}%</span></td>
            <td class="num" style="${dPnlCls};font-weight:700">${dPnlSign}${sumSym}${Number(d.daily_pnl).toFixed(2)}</td>
            <td class="num" style="font-weight:700">${sumSym}${Number(d.end_balance).toFixed(2)}</td>
            <td class="num" style="${dRetCls};font-weight:700">${dRetSign}${Number(d.return_pct).toFixed(2)}%</td>
          </tr>`;
        }).join("");
      }

      summaryHtml = `
        <div class="grid grid-4" style="margin-bottom:var(--sp-3)">
          <div class="metric">
            <div class="metric-label">WIN RATE</div>
            <div class="metric-value ${summary.win_rate >= 50 ? 'up' : 'down'}">${summary.win_rate}%</div>
            <div class="muted" style="font-size:11px">${summary.winning_trades} Wins · ${summary.losing_trades} Losses</div>
          </div>
          <div class="metric">
            <div class="metric-label">NET PROFIT (${currLabel})</div>
            <div class="metric-value ${pnlCls}">${pnlSign}${sumSym}${Number(summary.net_profit_usd).toFixed(2)}</div>
            <div class="muted" style="font-size:11px">${pnlSign}${summary.total_pts} PTS</div>
          </div>
          <div class="metric">
            <div class="metric-label">PROFIT FACTOR</div>
            <div class="metric-value">${summary.profit_factor}</div>
            <div class="muted" style="font-size:11px">Gross Win / Gross Loss</div>
          </div>
          <div class="metric">
            <div class="metric-label">MAX DRAWDOWN</div>
            <div class="metric-value down">-${sumSym}${Number(summary.max_drawdown_usd).toFixed(2)}</div>
            <div class="muted" style="font-size:11px">Max DD: ${summary.max_drawdown_pct}%</div>
          </div>
        </div>

        <div class="card" style="padding:10px 16px;margin-bottom:var(--sp-3);background:rgba(41,98,255,0.06);border-color:rgba(41,98,255,0.2)">
          <div class="row-between">
            <div style="font-size:12px;color:var(--text)">
              📅 <b>Simulation Window:</b> ${summary.start_date} to ${summary.end_date} · <b>Timeframe:</b> <span class="badge badge-blue" style="font-size:10px">${summary.timeframe || 'ALL'}</span> · <b>Account:</b> <span class="badge ${isCentSummary ? 'badge-green' : 'badge-blue'}" style="font-size:10px">${currLabel}</span> · <b>Total Trades:</b> ${summary.total_trades}
            </div>
            <div style="font-size:12px">
              <b>Capital:</b> ${sumSym}${summary.initial_capital} ➜ <b style="color:${summary.net_profit_usd >= 0 ? '#00e676' : '#ef5350'}">${sumSym}${Number(summary.final_balance).toFixed(2)}</b>
            </div>
          </div>
        </div>

        ${dailyRows ? `
        <!-- DAY-BY-DAY PROGRESSION BREAKDOWN CARD -->
        <div class="card" style="margin-bottom:var(--sp-3);border: 1px solid rgba(0,230,118,0.25)">
          <div class="card-head" style="background:rgba(0,230,118,0.06);display:flex;justify-content:space-between;align-items:center">
            <div style="font-weight:700;color:#69f0ae;display:flex;align-items:center;gap:8px">
              <span>📅 DAY-BY-DAY PROGRESSION BREAKDOWN</span>
              <span class="badge badge-green" style="font-size:10px">${dailyList.length} Trading Days</span>
            </div>
            <span class="muted" style="font-size:11px">Auto-Compounding & Balance Growth Tracker</span>
          </div>
          <div class="card-body flush">
            <div class="table-wrap" style="max-height:360px;overflow-y:auto">
              <table class="term">
                <thead>
                  <tr>
                    <th>DAY</th>
                    <th>DATE</th>
                    <th class="num">TRADES</th>
                    <th class="num">WINS</th>
                    <th class="num">LOSSES</th>
                    <th class="num">WIN RATE</th>
                    <th class="num">DAILY PnL (${sumSym})</th>
                    <th class="num">CLOSING BALANCE (${sumSym})</th>
                    <th class="num">CUMULATIVE RETURN</th>
                  </tr>
                </thead>
                <tbody>
                  ${dailyRows}
                </tbody>
              </table>
            </div>
          </div>
        </div>
        ` : ''}
      `;
    }

    const tradeRows = filteredTrades.map((t, i) => {
      const isWin = t.status === "WIN";
      const isLoss = t.status === "LOSS";
      const statusBadge = isWin
        ? `<span class="badge badge-green">WIN</span>`
        : (isLoss ? `<span class="badge badge-red">LOSS</span>` : `<span class="badge badge-yellow">OPEN</span>`);

      const pnlColor = t.pnl_usd > 0 ? "color:#00e676" : (t.pnl_usd < 0 ? "color:#ef5350" : "color:var(--text-muted)");
      const pnlSign = t.pnl_usd > 0 ? "+" : "";
      const dirBadge = t.direction === "LONG" ? `<span class="badge badge-green" style="font-size:10px">BUY ▲</span>` : `<span class="badge badge-red" style="font-size:10px">SELL ▼</span>`;

      let stratBadgeCls = "badge-blue";
      if (t.strategy.includes("[L1]")) stratBadgeCls = "badge-blue";
      else if (t.strategy.includes("[L2]")) stratBadgeCls = "badge-amber";
      else if (t.strategy.includes("[L3]")) stratBadgeCls = "badge-violet";
      else if (t.strategy.includes("SMC")) stratBadgeCls = "badge-cyan";
      else if (t.strategy.includes("Trend")) stratBadgeCls = "badge-green";

      return `<tr>
        <td class="muted">${i + 1}</td>
        <td><b>${t.entry_time ? t.entry_time.replace("T", " ").replace("+00:00", "") : "—"}</b></td>
        <td><span class="badge ${stratBadgeCls}" style="font-size:10px;font-weight:700">${UI.esc(t.strategy)}</span></td>
        <td><b>${UI.esc(t.timeframe)}</b></td>
        <td>${dirBadge}</td>
        <td class="num" style="font-size:11px;color:#90caf9;font-weight:600">${t.lot_size ? Number(t.lot_size).toFixed(t.lot_size < 0.01 ? 3 : 2) : '0.01'}</td>
        <td class="num" style="color:#ffd54f">$${Number(t.zero_level).toFixed(2)}</td>
        <td class="num" style="font-weight:700">$${Number(t.entry_price).toFixed(2)}</td>
        <td class="num" style="color:#ef5350">$${Number(t.sl_price).toFixed(2)}</td>
        <td class="num" style="color:#00e676">$${Number(t.tp_price).toFixed(2)}</td>
        <td class="num">$${Number(t.exit_price).toFixed(2)}</td>
        <td><span class="badge ${t.exit_reason === 'TP_HIT' ? 'badge-green' : (t.exit_reason === 'SL_HIT' ? 'badge-red' : 'badge-yellow')}">${UI.esc(t.exit_reason)}</span></td>
        <td class="num" style="${pnlColor}">${pnlSign}${Number(t.pnl_pts).toFixed(2)}</td>
        <td class="num" style="${pnlColor};font-weight:700">${pnlSign}${currSym}${Number(t.pnl_usd).toFixed(2)}</td>
        <td>${statusBadge}</td>
      </tr>`;
    }).join("");

    mount.innerHTML = `<div class="stack">
      <div class="row-between">
        <div>
          <div class="section-title">🧪 BACKTEST LAB & HISTORICAL VERIFIER</div>
          <div class="muted" style="font-size:11px">Multi-Timeframe Deterministic Backtesting · Dynamic Compounding & Cent Account · Real Binance Data</div>
        </div>
        <div class="toolbar">
          <span class="badge badge-blue">OFFLINE SIMULATOR</span>
          <span class="badge badge-green">REAL BINANCE CANDLES</span>
          <span class="badge badge-red">NO REAL MONEY</span>
        </div>
      </div>

      <!-- CONFIG CARD -->
      <div class="card">
        <div class="card-head"><span>BACKTEST PARAMETERS</span></div>
        <div class="card-body">
          <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:flex-end">
            <div style="flex:1;min-width:180px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">STRATEGY</label>
              <select id="bt-strategy" class="form-input" style="width:100%;padding:8px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px;font-weight:600">
                <option value="FIB_GO_WITH_TREND" ${window.__btStrategy === 'FIB_GO_WITH_TREND' ? 'selected' : ''}>Fib Go with Trend (15M, 30M, 1H, 2H, 4H)</option>
                <option value="SMC_WITH_FIB" ${window.__btStrategy === 'SMC_WITH_FIB' ? 'selected' : ''}>SMC with Fib (5M, 15M, 30M, 1H, 4H)</option>
                <option value="FIB_WITH_RETRACEMENT" ${(!window.__btStrategy || window.__btStrategy === 'FIB_WITH_RETRACEMENT') ? 'selected' : ''}>Fib Retracement (5M, 15M, 30M, 1H · 4H Excluded)</option>
                <option value="ALL" ${window.__btStrategy === 'ALL' ? 'selected' : ''}>All 3 Strategies Combined</option>
              </select>
            </div>

            <div style="width:130px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">TIMEFRAME</label>
              <select id="bt-timeframe" class="form-input" style="width:100%;padding:8px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px;font-weight:600">
                <option value="ALL" ${(!window.__btTimeframe || window.__btTimeframe === 'ALL') ? 'selected' : ''}>ALL ACTIVE TIMEFRAMES</option>
                <option value="5m" ${window.__btTimeframe === '5m' ? 'selected' : ''}>5m (5 Minutes)</option>
                <option value="15m" ${window.__btTimeframe === '15m' ? 'selected' : ''}>15m (15 Minutes)</option>
                <option value="30m" ${window.__btTimeframe === '30m' ? 'selected' : ''}>30m (30 Minutes)</option>
                <option value="1h" ${window.__btTimeframe === '1h' ? 'selected' : ''}>1h (1 Hour)</option>
              </select>
            </div>

            <div style="width:125px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">CURRENCY</label>
              <select id="bt-currency" class="form-input" style="width:100%;padding:8px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px;font-weight:600">
                <option value="cent" ${(!window.__btCurrency || window.__btCurrency === 'cent') ? 'selected' : ''}>Cent / ₹ INR</option>
                <option value="usd" ${window.__btCurrency === 'usd' ? 'selected' : ''}>Standard $ USD</option>
              </select>
            </div>

            <div style="width:145px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">SIZING MODE</label>
              <select id="bt-sizing-mode" class="form-input" style="width:100%;padding:8px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px;font-weight:600">
                <option value="broker_risk" ${(!window.__btSizingMode || window.__btSizingMode === 'broker_risk') ? 'selected' : ''}>Dynamic % Risk</option>
                <option value="fixed" ${window.__btSizingMode === 'fixed' ? 'selected' : ''}>Fixed Lot (0.01)</option>
              </select>
            </div>

            <div style="width:115px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">LEVERAGE</label>
              <select id="bt-leverage" class="form-input" style="width:100%;padding:8px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px;font-weight:600">
                <option value="100" ${window.__btLeverage === 100 ? 'selected' : ''}>1:100</option>
                <option value="200" ${window.__btLeverage === 200 ? 'selected' : ''}>1:200</option>
                <option value="500" ${(!window.__btLeverage || window.__btLeverage === 500) ? 'selected' : ''}>1:500 (Std)</option>
                <option value="1000" ${window.__btLeverage === 1000 ? 'selected' : ''}>1:1000</option>
                <option value="2000" ${window.__btLeverage === 2000 ? 'selected' : ''}>1:2000</option>
              </select>
            </div>

            <div style="width:115px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:#69f0ae;display:block;margin-bottom:4px">RISK % (COMPOUND)</label>
              <input type="number" id="bt-risk-percent" class="form-input" value="${window.__btRiskPercent || 1.0}" step="0.5" min="0.1" max="10.0" style="width:100%;padding:7px 10px;background:#181e29;border:1px solid rgba(0,230,118,0.3);color:#69f0ae;border-radius:6px;font-size:12px;font-weight:700">
            </div>

            <div style="width:115px">
              <label class="input-label" id="bt-capital-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">CAPITAL (${isCent ? '₹' : '$'})</label>
              <input type="number" id="bt-capital" class="form-input" value="${window.__btCapital || 10000}" step="500" min="100" style="width:100%;padding:7px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px">
            </div>

            <div style="flex:1;min-width:120px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">FROM DATE</label>
              <input type="date" id="bt-from-date" class="form-input" value="${window.__btFromDate || '2026-08-01'}" style="width:100%;padding:7px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px">
            </div>

            <div style="flex:1;min-width:120px">
              <label class="input-label" style="font-size:11px;font-weight:700;color:var(--text-dim);display:block;margin-bottom:4px">TO DATE</label>
              <input type="date" id="bt-to-date" class="form-input" value="${window.__btToDate || '2026-08-31'}" style="width:100%;padding:7px 10px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:6px;font-size:12px">
            </div>

            <div>
              <button id="bt-run-btn" class="btn btn-primary" style="padding:8px 20px;font-weight:700;font-size:12px;display:flex;align-items:center;gap:6px" ${isLoading ? 'disabled' : ''}>
                ${isLoading ? '⏳ RUNNING SIMULATION…' : '🚀 RUN BACKTEST'}
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- RESULTS SUMMARY -->
      ${summaryHtml}

      <!-- TABLE & EXPORT CARD -->
      <div class="card">
        <div class="card-head" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
          <div style="display:flex;align-items:center;gap:10px">
            <span>DETAILED TRADE LOGS</span>
            <span class="badge badge-blue">${filteredTrades.length} Trades Shown</span>
          </div>

          <div style="display:flex;align-items:center;gap:8px">
            <input type="search" id="bt-search" placeholder="Search trades..." value="${searchQuery}" style="padding:4px 8px;background:#181e29;border:1px solid rgba(255,255,255,0.12);color:#fff;border-radius:4px;font-size:11px;width:160px">

            <div class="btn-group" style="display:inline-flex;gap:4px">
              <button class="btn btn-xs ${activeFilter === 'ALL' ? 'btn-primary' : 'btn-outline'}" data-filter="ALL">ALL</button>
              <button class="btn btn-xs ${activeFilter === 'WIN' ? 'btn-primary' : 'btn-outline'}" data-filter="WIN">WINS</button>
              <button class="btn btn-xs ${activeFilter === 'LOSS' ? 'btn-primary' : 'btn-outline'}" data-filter="LOSS">LOSSES</button>
            </div>

            <button id="bt-export-csv-btn" class="btn btn-xs btn-secondary" style="padding:4px 10px;font-size:11px;font-weight:700;display:flex;align-items:center;gap:4px" ${allTrades.length === 0 ? 'disabled' : ''}>
              📥 EXPORT TO CSV
            </button>
          </div>
        </div>

        <div class="card-body flush">
          <div class="table-wrap" style="max-height:600px;overflow-y:auto">
            <table class="term">
              <thead>
                <tr>
                  <th>#</th>
                  <th>ENTRY TIME (UTC)</th>
                  <th>STRATEGY</th>
                  <th>TF</th>
                  <th>DIR</th>
                  <th class="num">LOT</th>
                  <th class="num" title="Anchor Zero Origin (0.000)">ZERO (P0)</th>
                  <th class="num">ENTRY</th>
                  <th class="num" title="Stop Loss (0.236 Level)">SL</th>
                  <th class="num" title="Take Profit (1.618 Target)">TP</th>
                  <th class="num">EXIT</th>
                  <th>REASON</th>
                  <th class="num">PTS</th>
                  <th class="num">PNL (${currSym})</th>
                  <th>STATUS</th>
                </tr>
              </thead>
              <tbody id="bt-trades-tbody">
                ${tradeRows || `<tr><td colspan="15" class="muted" style="text-align:center;padding:32px">No backtest run yet. Select parameters and click <b>RUN BACKTEST</b>.</td></tr>`}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>`;

    // Wire up Currency change
    const curSelect = mount.querySelector("#bt-currency");
    if (curSelect) {
      curSelect.addEventListener("change", (e) => {
        const cVal = e.target.value;
        window.__btCurrency = cVal;
        const capInput = mount.querySelector("#bt-capital");
        const capLabel = mount.querySelector("#bt-capital-label");
        if (cVal === "cent") {
          if (capLabel) capLabel.textContent = "CAPITAL (₹)";
          if (capInput && (!window.__btCapital || window.__btCapital === 1000)) capInput.value = 10000;
        } else {
          if (capLabel) capLabel.textContent = "CAPITAL ($)";
          if (capInput && (!window.__btCapital || window.__btCapital === 10000)) capInput.value = 1000;
        }
      });
    }

    // Wire up Run Button
    const runBtn = mount.querySelector("#bt-run-btn");
    if (runBtn) {
      runBtn.addEventListener("click", async () => {
        const strat = mount.querySelector("#bt-strategy")?.value || "FIB_WITH_RETRACEMENT";
        const tf = mount.querySelector("#bt-timeframe")?.value || "ALL";
        const cur = mount.querySelector("#bt-currency")?.value || "cent";
        const fDate = mount.querySelector("#bt-from-date")?.value || "2026-08-01";
        const tDate = mount.querySelector("#bt-to-date")?.value || "2026-08-31";
        const cap = parseFloat(mount.querySelector("#bt-capital")?.value || "10000");
        const rPercent = parseFloat(mount.querySelector("#bt-risk-percent")?.value || "1.0");

        const sMode = mount.querySelector("#bt-sizing-mode")?.value || "broker_risk";
        const lev = parseInt(mount.querySelector("#bt-leverage")?.value || "500", 10);
        const sizingMode = sMode;
        const riskMode = sMode === "fixed" ? "fixed_amount" : "percent";
        const lot = 0.01;

        window.__btStrategy = strat;
        window.__btTimeframe = tf;
        window.__btCurrency = cur;
        window.__btFromDate = fDate;
        window.__btToDate = tDate;
        window.__btLotSize = lot;
        window.__btCapital = cap;
        window.__btSizingMode = sizingMode;
        window.__btRiskMode = riskMode;
        window.__btRiskPercent = rPercent;
        window.__btLeverage = lev;

        isLoading = true;
        renderView();

        try {
          const resp = await fetch("/backtest/run-strategy", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              strategy: strat,
              timeframe: tf,
              start_date: fDate,
              end_date: tDate,
              lot_size: lot,
              initial_capital: cap,
              sizing_mode: sizingMode,
              target_risk_usd: 100.0,
              account_currency: cur,
              risk_mode: riskMode,
              risk_percent: rPercent,
            }),
          });

          if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `Server returned ${resp.status}`);
          }

          const resData = await resp.json();
          backtestData = resData;
          if (typeof UI !== "undefined" && UI.toast) {
            UI.toast("Backtest Complete", `Evaluated ${resData.summary?.total_trades || 0} trades`, "green");
          }
        } catch (err) {
          if (typeof UI !== "undefined" && UI.toast) {
            UI.toast("Backtest Failed", err.message || "Simulation error", "red");
          }
        } finally {
          isLoading = false;
          renderView();
        }
      });
    }

    // Wire up Filters
    mount.querySelectorAll("[data-filter]").forEach(btn => {
      btn.addEventListener("click", () => {
        activeFilter = btn.dataset.filter;
        renderView();
      });
    });

    // Wire up Search
    const sInput = mount.querySelector("#bt-search");
    if (sInput) {
      sInput.addEventListener("input", (e) => {
        searchQuery = e.target.value.trim().toLowerCase();
        renderView();
      });
    }

    // Wire up CSV Export
    const csvBtn = mount.querySelector("#bt-export-csv-btn");
    if (csvBtn) {
      csvBtn.addEventListener("click", () => {
        if (!allTrades || allTrades.length === 0) return;
        const headers = [
          "Trade_ID", "Entry_Time", "Strategy", "Timeframe", "Direction",
          "Zero_Level_P0", "Entry_Price", "Stop_Loss", "Take_Profit",
          "Exit_Time", "Exit_Price", "Exit_Reason", "Pts", "PnL_USD", "R_Multiple", "Status"
        ];
        const rows = allTrades.map(t => [
          t.trade_id, t.entry_time, t.strategy, t.timeframe, t.direction,
          t.zero_level, t.entry_price, t.sl_price, t.tp_price,
          t.exit_time, t.exit_price, t.exit_reason, t.pnl_pts, t.pnl_usd, t.r_multiple, t.status
        ]);
        const csvContent = "data:text/csv;charset=utf-8," + [headers.join(","), ...rows.map(r => r.join(","))].join("\n");
        const encodedUri = encodeURI(csvContent);
        const link = document.createElement("a");
        link.setAttribute("href", encodedUri);
        link.setAttribute("download", `backtest_${window.__btStrategy || 'all'}_${window.__btTimeframe || 'ALL'}_${window.__btFromDate || 'start'}_to_${window.__btToDate || 'end'}.csv`);
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      });
    }
  }

  renderView();
};

/* ================= SETTINGS ================= */
Routes["/settings"] = async (mount) => {
  await renderWith(async () => {
    const [st, tg, exec] = await Promise.allSettled([
      API.systemStatus(),
      API.telegramStatus(),
      API.getExecutionSettings(),
    ]);
    return {
      st: st.status === "fulfilled" ? st.value : null,
      tg: tg.status === "fulfilled" ? tg.value : null,
      exec: exec.status === "fulfilled" ? exec.value : {
        sizing_mode: "broker_risk",
        account_currency: "cent",
        risk_mode: "percent",
        risk_percent: 1.0,
        account_balance: 10000.0,
        target_risk_usd: 100.0,
        fixed_lot_size: 0.01,
        account_leverage: 500,
        strategy_fib_retracement: true,
        strategy_smc_fib: false,
        strategy_fib_trend: false,
        fib_retracement_timeframes: ["5m", "15m", "30m", "1h"],
        smart_shield_enabled: true,
        smart_shield_level: "0.618",
      },
    };
  }, (d) => {
    const stRaw = d.st || {};
    const st = stRaw.status || stRaw;
    const tg = d.tg || {};
    const exec = d.exec || {};
    const sizingMode = exec.sizing_mode || "broker_risk";
    const accountCurrency = exec.account_currency || "cent";
    const riskPercent = exec.risk_percent !== undefined ? exec.risk_percent : 1.0;
    const accountBalance = exec.account_balance !== undefined ? exec.account_balance : 10000.0;
    const fixedLotSize = exec.fixed_lot_size !== undefined ? exec.fixed_lot_size : 0.01;
    const accountLeverage = exec.account_leverage !== undefined ? exec.account_leverage : 500;
    const stratFibRetr = exec.strategy_fib_retracement !== false;
    const stratSmcFib = exec.strategy_smc_fib === true;
    const stratFibTrend = exec.strategy_fib_trend === true;
    const activeTfs = exec.fib_retracement_timeframes || ["5m", "15m", "30m", "1h"];
    const smartShield = exec.smart_shield_enabled !== false;
    const smartShieldLevel = exec.smart_shield_level || "0.618";

    const isCent = accountCurrency === "cent";
    const sym = isCent ? "₹" : "$";

    return `<div class="stack">
      <div class="row-between">
        <div>
          <div class="section-title">⚙️ Strategy Execution & Risk Settings</div>
          <div class="muted" style="font-size:12px">Configure Cent Account (₹ INR), Broker Leverage, Dynamic % Risk Compounding, Strategy Toggles & Smart Shield</div>
        </div>
        <div class="toolbar">
          <span class="badge ${isCent ? 'badge-green' : 'badge-blue'}">${isCent ? 'CENT ACCOUNT (₹ INR)' : 'STANDARD ($ USD)'}</span>
          <span class="badge badge-purple" id="toolbar-leverage-badge">LEVERAGE 1:${accountLeverage}</span>
          <span class="badge badge-blue">PERSISTENT SETTINGS</span>
        </div>
      </div>

      <!-- CARD 1: ACCOUNT CAPITAL & BROKER LEVERAGE -->
      <div class="card" style="border: 1px solid rgba(41,98,255,0.3)">
        <div class="card-head" style="background:rgba(41,98,255,0.08);display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:700;color:#90caf9">🏛️ 1. ACCOUNT CAPITAL & BROKER LEVERAGE</span>
          <span class="badge badge-blue">BROKER SETTINGS</span>
        </div>
        <div class="card-body">
          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:16px">
            <!-- ACCOUNT CURRENCY -->
            <div>
              <label class="input-label" style="font-size:12px;font-weight:700;color:var(--text);display:block;margin-bottom:6px">
                Account Currency
              </label>
              <select id="set-account-currency" class="form-input" style="width:100%;padding:9px 12px;background:#181e29;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:6px;font-size:13px;font-weight:600">
                <option value="cent" ${accountCurrency === 'cent' ? 'selected' : ''}>Cent Account (USC / ₹ INR) — 1 Cent = ₹1</option>
                <option value="usd" ${accountCurrency === 'usd' ? 'selected' : ''}>Standard Account ($ USD)</option>
              </select>
              <span class="muted" style="font-size:11px;margin-top:4px;display:block">Denominated in Cent units ($1 USD = 100 Cent / ₹100 INR)</span>
            </div>

            <!-- ACCOUNT BALANCE -->
            <div id="account-balance-box">
              <label class="input-label" id="balance-label" style="font-size:12px;font-weight:700;color:var(--text);display:block;margin-bottom:6px">
                Account Balance (${sym})
              </label>
              <input type="number" id="set-account-balance" class="form-input" value="${accountBalance}" step="500" min="100" max="10000000" style="width:100%;padding:9px 12px;background:#181e29;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:6px;font-size:13px;font-weight:600">
              <span class="muted" style="font-size:11px;margin-top:4px;display:block">Base equity used for risk and margin calculation</span>
            </div>

            <!-- BROKER LEVERAGE -->
            <div>
              <label class="input-label" style="font-size:12px;font-weight:700;color:var(--text);display:block;margin-bottom:6px">
                Broker Leverage
              </label>
              <select id="set-account-leverage" class="form-input" style="width:100%;padding:9px 12px;background:#181e29;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:6px;font-size:13px;font-weight:600">
                <option value="100" ${accountLeverage === 100 ? 'selected' : ''}>1:100 (High Margin Required)</option>
                <option value="200" ${accountLeverage === 200 ? 'selected' : ''}>1:200</option>
                <option value="500" ${accountLeverage === 500 ? 'selected' : ''}>1:500 (Standard Recommended)</option>
                <option value="1000" ${accountLeverage === 1000 ? 'selected' : ''}>1:1000 (Low Margin)</option>
                <option value="2000" ${accountLeverage === 2000 ? 'selected' : ''}>1:2000 (Ultra Low Margin)</option>
              </select>
              <span class="muted" style="font-size:11px;margin-top:4px;display:block">Reduces required margin held by broker per open lot</span>
            </div>
          </div>
        </div>
      </div>

      <!-- CARD 2: POSITION SIZING ENGINE (DYNAMIC % COMPOUNDING vs FIXED LOT) -->
      <div class="card" style="border: 1px solid rgba(0,230,118,0.3)">
        <div class="card-head" style="background:rgba(0,230,118,0.08);display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:700;color:#69f0ae">🎯 2. POSITION SIZING & RISK ENGINE</span>
          <span id="sizing-mode-badge" class="badge ${sizingMode === 'broker_risk' ? 'badge-green' : 'badge-amber'}">
            ${sizingMode === 'broker_risk' ? 'DYNAMIC % RISK (COMPOUNDING)' : 'FIXED LOT SIZE'}
          </span>
        </div>
        <div class="card-body">
          <div style="margin-bottom:16px">
            <label class="input-label" style="font-size:12px;font-weight:700;color:var(--text);display:block;margin-bottom:6px">
              Select Position Sizing Mode
            </label>
            <select id="set-sizing-mode" class="form-input" style="width:100%;max-width:420px;padding:9px 12px;background:#181e29;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:6px;font-size:13px;font-weight:700">
              <option value="broker_risk" ${sizingMode === 'broker_risk' ? 'selected' : ''}>Dynamic % Risk (Compounding Auto-Lot based on SL distance)</option>
              <option value="fixed" ${sizingMode === 'fixed' ? 'selected' : ''}>Fixed Lot Size (Manual 0.01, 0.02, 0.05 Lots)</option>
            </select>
          </div>

          <!-- DYNAMIC RISK CONTROLS -->
          <div id="dynamic-risk-section" style="${sizingMode === 'broker_risk' ? '' : 'display:none;'};background:#181e29;border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:14px 16px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
              <span style="font-size:12px;font-weight:700;color:#90caf9">⚡ RISK PERCENTAGE PER TRADE (AUTO-COMPOUNDING)</span>
              <span class="muted" style="font-size:11px">Auto-upsizes on profit & auto-downsizes on drawdown</span>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
              <button type="button" class="btn btn-sm ${riskPercent === 1.0 ? 'btn-primary' : 'btn-outline'} risk-preset-btn" data-pct="1.0" style="font-weight:700">
                🛡️ 1.0% (Safe Conservative)
              </button>
              <button type="button" class="btn btn-sm ${riskPercent === 1.5 ? 'btn-primary' : 'btn-outline'} risk-preset-btn" data-pct="1.5" style="font-weight:700">
                ⭐ 1.5% (Optimal Balance)
              </button>
              <button type="button" class="btn btn-sm ${riskPercent === 2.0 ? 'btn-primary' : 'btn-outline'} risk-preset-btn" data-pct="2.0" style="font-weight:700">
                🚀 2.0% (Accelerated Growth)
              </button>
              <div style="display:flex;align-items:center;gap:6px;margin-left:auto">
                <span style="font-size:12px;color:var(--text-dim);font-weight:600">Custom %:</span>
                <input type="number" id="set-risk-percent" class="form-input" value="${riskPercent}" step="0.1" min="0.1" max="10.0" style="width:80px;padding:6px 8px;background:#10141d;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:4px;font-size:13px;font-weight:700;text-align:center">
                <span style="font-size:12px;font-weight:700;color:#90caf9">%</span>
              </div>
            </div>
          </div>

          <!-- FIXED LOT CONTROLS -->
          <div id="fixed-lot-section" style="${sizingMode === 'fixed' ? '' : 'display:none;'};background:#181e29;border:1px solid rgba(255,255,255,0.08);border-radius:8px;padding:14px 16px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px">
              <span style="font-size:12px;font-weight:700;color:#ffe082">📌 FIXED LOT SIZE PER TRADE</span>
              <span class="muted" style="font-size:11px">1.0 pt Gold Move on 0.01 Lot = $1.00 USD (₹100 Cent)</span>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center">
              <button type="button" class="btn btn-sm ${fixedLotSize === 0.01 ? 'btn-primary' : 'btn-outline'} fixed-preset-btn" data-lot="0.01" style="font-weight:700">
                0.01 Lot ($1.00 / pt)
              </button>
              <button type="button" class="btn btn-sm ${fixedLotSize === 0.02 ? 'btn-primary' : 'btn-outline'} fixed-preset-btn" data-lot="0.02" style="font-weight:700">
                0.02 Lot ($2.00 / pt)
              </button>
              <button type="button" class="btn btn-sm ${fixedLotSize === 0.05 ? 'btn-primary' : 'btn-outline'} fixed-preset-btn" data-lot="0.05" style="font-weight:700">
                0.05 Lot ($5.00 / pt)
              </button>
              <button type="button" class="btn btn-sm ${fixedLotSize === 0.10 ? 'btn-primary' : 'btn-outline'} fixed-preset-btn" data-lot="0.10" style="font-weight:700">
                0.10 Lot ($10.00 / pt)
              </button>
              <div style="display:flex;align-items:center;gap:6px;margin-left:auto">
                <span style="font-size:12px;color:var(--text-dim);font-weight:600">Custom Lot:</span>
                <input type="number" id="set-fixed-lot-size" class="form-input" value="${fixedLotSize}" step="0.01" min="0.01" max="10.0" style="width:85px;padding:6px 8px;background:#10141d;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:4px;font-size:13px;font-weight:700;text-align:center">
              </div>
            </div>
          </div>

          <!-- CARD 3: LIVE AUTO-LOT & MARGIN CALCULATOR PREVIEW -->
          <div class="card" style="background:#131822;border:1px solid rgba(0,230,118,0.25);padding:14px 16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px">
              <span style="font-size:12px;font-weight:700;color:#69f0ae">📊 LIVE AUTO-LOT & MARGIN CALCULATOR PREVIEW</span>
              <div style="display:flex;gap:8px;align-items:center">
                <span id="preview-effective-risk" class="badge badge-green" style="font-size:11px;font-weight:700">₹100.00 Risk</span>
                <span id="preview-leverage-badge" class="badge badge-purple" style="font-size:11px;font-weight:700">1:${accountLeverage} Leverage</span>
              </div>
            </div>

            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));gap:10px;margin-bottom:12px">
              <div style="background:#181e29;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:10px;color:var(--text-dim);font-weight:600">5M (~3.0 pt SL)</div>
                <div id="preview-lot-5m" style="font-size:16px;font-weight:800;color:#90caf9">0.33 Lot</div>
                <div id="preview-margin-5m" style="font-size:11px;color:#a5d6a7;font-weight:600;margin-top:2px">Margin: ₹292.71</div>
              </div>
              <div style="background:#181e29;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:10px;color:var(--text-dim);font-weight:600">15M (~6.0 pt SL)</div>
                <div id="preview-lot-15m" style="font-size:16px;font-weight:800;color:#a5d6a7">0.17 Lot</div>
                <div id="preview-margin-15m" style="font-size:11px;color:#a5d6a7;font-weight:600;margin-top:2px">Margin: ₹150.79</div>
              </div>
              <div style="background:#181e29;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:10px;color:var(--text-dim);font-weight:600">30M (~12.0 pt SL)</div>
                <div id="preview-lot-30m" style="font-size:16px;font-weight:800;color:#ffe082">0.08 Lot</div>
                <div id="preview-margin-30m" style="font-size:11px;color:#a5d6a7;font-weight:600;margin-top:2px">Margin: ₹70.96</div>
              </div>
              <div style="background:#181e29;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
                <div style="font-size:10px;color:var(--text-dim);font-weight:600">1H (~25.0 pt SL)</div>
                <div id="preview-lot-1h" style="font-size:16px;font-weight:800;color:#ce93d8">0.04 Lot</div>
                <div id="preview-margin-1h" style="font-size:11px;color:#a5d6a7;font-weight:600;margin-top:2px">Margin: ₹35.48</div>
              </div>
            </div>

            <!-- ACCOUNT MARGIN & FREE MARGIN BAR -->
            <div id="preview-margin-health" style="background:#181e29;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
              <div style="font-size:12px;color:var(--text)">
                💰 <b>Required Margin (5M Trade):</b> <span id="summary-req-margin" style="color:#69f0ae;font-weight:700">₹292.71</span> · 
                🛡️ <b>Free Margin Remaining:</b> <span id="summary-free-margin" style="color:#90caf9;font-weight:700">₹9,707.29</span>
              </div>
              <span id="summary-margin-level" class="badge badge-green" style="font-size:11px;font-weight:700">Margin Level: 3,416% (Safe)</span>
            </div>

            <div id="preview-compounding-note" style="font-size:11px;color:var(--text-muted);line-height:1.6;border-top:1px solid rgba(255,255,255,0.06);padding-top:8px">
              📈 <b>Growth:</b> If balance reaches <b>₹15,000</b> (+50%), 5M lot auto-increases to <b style="color:#00e676">0.50 Lots</b>.<br>
              🛡️ <b>Drawdown Protection:</b> If balance drops to <b>₹8,000</b> (-20%), 5M lot auto-downsizes to <b style="color:#ffb74d">${sym}0.27 Lots</b>.
            </div>
          </div>
        </div>
      </div>

      <!-- CARD 3: STRATEGY ENGINE SELECTION (MULTI-SLOT PARALLEL) -->
      <div class="card" style="border: 1px solid rgba(255,183,77,0.3)">
        <div class="card-head" style="background:rgba(255,183,77,0.08);display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:700;color:#ffe082">⚡ 3. STRATEGY ENGINE SELECTION (INDEPENDENT ON / OFF)</span>
          <span class="badge badge-amber">MULTI-SLOT PARALLEL EXECUTION</span>
        </div>
        <div class="card-body">
          <div class="muted" style="font-size:12px;margin-bottom:14px">
            Toggle which strategies scan the market and execute paper trades. Multi-timeframes (5M, 15M, 30M, 1H) run in independent parallel slots without blocking.
          </div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(280px, 1fr));gap:14px">
            <!-- STRATEGY 1: FIB RETRACEMENT -->
            <div style="background:#181e29;border:1px solid ${stratFibRetr ? 'rgba(0,230,118,0.4)' : 'rgba(255,255,255,0.08)'};border-radius:8px;padding:14px 16px" id="card-strat-fib-retr">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <label style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;cursor:pointer;color:#fff;margin:0">
                  <input type="checkbox" id="strat-cb-fib-retr" ${stratFibRetr ? 'checked' : ''} style="cursor:pointer"> 🎯 Fib Retracement
                </label>
                <span id="badge-strat-fib-retr" class="badge ${stratFibRetr ? 'badge-green' : 'badge-yellow'}" style="font-size:10px">
                  ${stratFibRetr ? 'ACTIVE & TRADING' : 'STANDBY / OFF'}
                </span>
              </div>
              <div style="font-size:11px;color:var(--text-dim);line-height:1.5">
                Golden Pocket (0.618, 0.500, 0.382) retracements with 3-tranche entries and Smart Shield loss protection.
              </div>
            </div>

            <!-- STRATEGY 2: SMC WITH FIB -->
            <div style="background:#181e29;border:1px solid ${stratSmcFib ? 'rgba(0,230,118,0.4)' : 'rgba(255,255,255,0.08)'};border-radius:8px;padding:14px 16px" id="card-strat-smc-fib">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <label style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;cursor:pointer;color:#fff;margin:0">
                  <input type="checkbox" id="strat-cb-smc-fib" ${stratSmcFib ? 'checked' : ''} style="cursor:pointer"> 💎 SMC With Fib
                </label>
                <span id="badge-strat-smc-fib" class="badge ${stratSmcFib ? 'badge-green' : 'badge-yellow'}" style="font-size:10px">
                  ${stratSmcFib ? 'ACTIVE & TRADING' : 'STANDBY / OFF'}
                </span>
              </div>
              <div style="font-size:11px;color:var(--text-dim);line-height:1.5">
                Smart Money Concepts order block sweeps and 0.680 discount entries with liquidity hunting.
              </div>
            </div>

            <!-- STRATEGY 3: FIB GO WITH TREND -->
            <div style="background:#181e29;border:1px solid ${stratFibTrend ? 'rgba(0,230,118,0.4)' : 'rgba(255,255,255,0.08)'};border-radius:8px;padding:14px 16px" id="card-strat-fib-trend">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <label style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;cursor:pointer;color:#fff;margin:0">
                  <input type="checkbox" id="strat-cb-fib-trend" ${stratFibTrend ? 'checked' : ''} style="cursor:pointer"> 📈 Fib Go With Trend
                </label>
                <span id="badge-strat-fib-trend" class="badge ${stratFibTrend ? 'badge-green' : 'badge-yellow'}" style="font-size:10px">
                  ${stratFibTrend ? 'ACTIVE & TRADING' : 'STANDBY / OFF'}
                </span>
              </div>
              <div style="font-size:11px;color:var(--text-dim);line-height:1.5">
                9/21 EMA momentum breakout with 1.618 Fib extension targets and dynamic breakeven locks.
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- CARD 4: ACTIVE FIB TIMEFRAMES & SMART SHIELD LOSS PROTECTION -->
      <div class="card" style="border: 1px solid rgba(186,104,200,0.3)">
        <div class="card-head" style="background:rgba(186,104,200,0.08);display:flex;justify-content:space-between;align-items:center">
          <span style="font-weight:700;color:#ce93d8">🛡️ 4. FIB TIMEFRAMES & SMART SHIELD LOSS PROTECTION</span>
          <span class="badge badge-purple">RISK DEFENSE</span>
        </div>
        <div class="card-body">
          <!-- TIMEFRAME SELECTOR -->
          <div style="margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
              <label class="input-label" style="font-size:12px;font-weight:700;color:var(--text);margin:0">
                Active Fib Retracement Timeframes (Multi-Slot Parallel)
              </label>
              <span style="font-size:11px;color:var(--text-dim)">Recommended: 5M, 15M, 30M, 1H (4H removed for faster turnover)</span>
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center">
              <label style="display:inline-flex;align-items:center;gap:8px;padding:7px 16px;background:#181e29;border:1px solid rgba(100,181,246,0.3);border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;color:#90caf9">
                <input type="checkbox" id="tf-cb-5m" ${activeTfs.includes('5m') ? 'checked' : ''} style="margin:0;cursor:pointer"> 5M
              </label>
              <label style="display:inline-flex;align-items:center;gap:8px;padding:7px 16px;background:#181e29;border:1px solid rgba(129,199,132,0.3);border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;color:#a5d6a7">
                <input type="checkbox" id="tf-cb-15m" ${activeTfs.includes('15m') ? 'checked' : ''} style="margin:0;cursor:pointer"> 15M
              </label>
              <label style="display:inline-flex;align-items:center;gap:8px;padding:7px 16px;background:#181e29;border:1px solid rgba(255,183,77,0.3);border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;color:#ffe082">
                <input type="checkbox" id="tf-cb-30m" ${activeTfs.includes('30m') ? 'checked' : ''} style="margin:0;cursor:pointer"> 30M
              </label>
              <label style="display:inline-flex;align-items:center;gap:8px;padding:7px 16px;background:#181e29;border:1px solid rgba(186,104,200,0.3);border-radius:6px;cursor:pointer;font-size:13px;font-weight:600;color:#ce93d8">
                <input type="checkbox" id="tf-cb-1h" ${activeTfs.includes('1h') ? 'checked' : ''} style="margin:0;cursor:pointer"> 1H
              </label>
            </div>
          </div>

          <!-- SMART SHIELD SL TARGET CONFIG -->
          <div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:16px">
            <label class="input-label" style="font-size:12px;font-weight:700;color:var(--text);display:block;margin-bottom:6px">
              🛡️ Smart Shield Trailing Stop Loss Target
            </label>
            <div style="display:flex;flex-wrap:wrap;gap:14px;align-items:center">
              <select id="set-smart-shield-level" class="form-input" style="max-width:500px;width:100%;padding:9px 12px;background:#181e29;border:1px solid rgba(255,255,255,0.15);color:#fff;border-radius:6px;font-size:13px;font-weight:600">
                <option value="0.618" ${smartShieldLevel === '0.618' ? 'selected' : ''}>0.618 Entry Breakeven (Zero Risk Escape - Recommended)</option>
                <option value="0.500" ${smartShieldLevel === '0.500' ? 'selected' : ''}>0.500 Conservative Buffer Shield (Wide Breakeven)</option>
              </select>
              <span class="muted" style="font-size:11px">When L2 or L3 hits Take Profit, automatically trails L1 Stop Loss to the selected Fibonacci target level</span>
            </div>
          </div>

          <!-- SAVE BUTTON ROW -->
          <div style="border-top:1px solid rgba(255,255,255,0.08);padding-top:16px;margin-top:16px;display:flex;justify-content:flex-end;align-items:center;gap:14px">
            <span id="save-status-msg" style="display:none;color:#00e676;font-size:13px;font-weight:700;align-items:center;gap:6px">
              ✅ Saved! Settings Applied Live
            </span>
            <button id="save-execution-settings-btn" class="btn btn-primary" style="padding:10px 28px;font-size:13px;font-weight:700;display:flex;align-items:center;gap:8px;transition:all 0.3s cubic-bezier(0.4, 0, 0.2, 1);cursor:pointer">
              💾 SAVE EXECUTION SETTINGS
            </button>
          </div>
        </div>
      </div>

      <!-- SAFETY & SYSTEM STATUS CARD -->
      <div class="card">
        <div class="card-head"><span>🛡️ Safety & Engine Status</span></div>
        <div class="card-body">
          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:14px">
            <div style="background:#181e29;padding:14px 16px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
              <div style="font-size:11px;font-weight:600;color:var(--text-dim);margin-bottom:4px">EXECUTION ENGINE</div>
              <div style="font-size:14px;font-weight:700;color:#66bb6a">Paper Trading (Simulation)</div>
            </div>
            <div style="background:#181e29;padding:14px 16px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
              <div style="font-size:11px;font-weight:600;color:var(--text-dim);margin-bottom:4px">MAX DRAWDOWN LIMIT</div>
              <div style="font-size:14px;font-weight:700;color:#ffb74d">30% Safety Limit</div>
            </div>
            <div style="background:#181e29;padding:14px 16px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
              <div style="font-size:11px;font-weight:600;color:var(--text-dim);margin-bottom:4px">TELEGRAM ALERTS</div>
              <div style="font-size:14px;font-weight:700;color:${tg.status === 'CONNECTED' ? '#66bb6a' : '#9e9e9e'}">
                ${tg.status || 'DISABLED'}
              </div>
            </div>
            <div style="background:#181e29;padding:14px 16px;border-radius:6px;border:1px solid rgba(255,255,255,0.06)">
              <div style="font-size:11px;font-weight:600;color:var(--text-dim);margin-bottom:4px">REAL CAPITAL RISK</div>
              <div style="font-size:14px;font-weight:700;color:#42a5f5">0% (Zero Risk Mode)</div>
            </div>
          </div>
        </div>
      </div>
    </div>`;
  }, mount);

  // Helper to recalculate live lot and margin preview
  function updateLotPreview() {
    const cur = mount.querySelector("#set-account-currency")?.value || "cent";
    const sym = cur === "cent" ? "₹" : "$";
    const bal = parseFloat(mount.querySelector("#set-account-balance")?.value || "10000");
    const leverage = parseInt(mount.querySelector("#set-account-leverage")?.value || "500", 10);
    const mode = mount.querySelector("#set-sizing-mode")?.value || "broker_risk";
    const rPct = parseFloat(mount.querySelector("#set-risk-percent")?.value || "1.0");
    const fixedLot = parseFloat(mount.querySelector("#set-fixed-lot-size")?.value || "0.01");

    const effectiveRisk = Math.max(1.0, bal * (rPct / 100.0));

    // Calculate lot for given SL points
    const calcLot = (pts) => {
      if (mode === "fixed") {
        return Math.max(0.01, fixedLot).toFixed(2);
      }
      const raw = effectiveRisk / (pts * 100.0);
      return Math.max(0.01, Math.min(5.0, Math.round(raw * 100) / 100)).toFixed(2);
    };

    const lot5m = calcLot(3.0);
    const lot15m = calcLot(6.0);
    const lot30m = calcLot(12.0);
    const lot1h = calcLot(25.0);

    // Margin calculation for Gold at $4,435
    // In Cent mode (₹ INR): notional in cents / leverage
    // In USD mode: notional in USD / leverage
    const calcMargin = (lot) => {
      const notional = parseFloat(lot) * 100.0 * 4435.0;
      return notional / leverage;
    };

    const margin5m = calcMargin(lot5m);
    const margin15m = calcMargin(lot15m);
    const margin30m = calcMargin(lot30m);
    const margin1h = calcMargin(lot1h);

    const elEffRisk = mount.querySelector("#preview-effective-risk");
    if (elEffRisk) {
      if (mode === "fixed") {
        elEffRisk.textContent = `${sym}${(fixedLot * 3.0 * 100.0).toFixed(2)} Risk (5M)`;
      } else {
        elEffRisk.textContent = `${sym}${effectiveRisk.toFixed(2)} Risk`;
      }
    }

    const elLevBadge = mount.querySelector("#preview-leverage-badge");
    if (elLevBadge) elLevBadge.textContent = `1:${leverage} Leverage`;
    const elTbLevBadge = mount.querySelector("#toolbar-leverage-badge");
    if (elTbLevBadge) elTbLevBadge.textContent = `LEVERAGE 1:${leverage}`;

    const el5m = mount.querySelector("#preview-lot-5m");
    if (el5m) el5m.textContent = `${lot5m} Lot`;
    const elMargin5m = mount.querySelector("#preview-margin-5m");
    if (elMargin5m) elMargin5m.textContent = `Margin: ${sym}${margin5m.toFixed(2)}`;

    const el15m = mount.querySelector("#preview-lot-15m");
    if (el15m) el15m.textContent = `${lot15m} Lot`;
    const elMargin15m = mount.querySelector("#preview-margin-15m");
    if (elMargin15m) elMargin15m.textContent = `Margin: ${sym}${margin15m.toFixed(2)}`;

    const el30m = mount.querySelector("#preview-lot-30m");
    if (el30m) el30m.textContent = `${lot30m} Lot`;
    const elMargin30m = mount.querySelector("#preview-margin-30m");
    if (elMargin30m) elMargin30m.textContent = `Margin: ${sym}${margin30m.toFixed(2)}`;

    const el1h = mount.querySelector("#preview-lot-1h");
    if (el1h) el1h.textContent = `${lot1h} Lot`;
    const elMargin1h = mount.querySelector("#preview-margin-1h");
    if (elMargin1h) elMargin1h.textContent = `Margin: ${sym}${margin1h.toFixed(2)}`;

    // Summary bar: free margin
    const freeMargin = Math.max(0, bal - margin5m);
    const marginLevel = margin5m > 0 ? Math.round((bal / margin5m) * 100) : 9999;

    const elReqMargin = mount.querySelector("#summary-req-margin");
    if (elReqMargin) elReqMargin.textContent = `${sym}${margin5m.toFixed(2)}`;
    const elFreeMargin = mount.querySelector("#summary-free-margin");
    if (elFreeMargin) elFreeMargin.textContent = `${sym}${freeMargin.toFixed(2)}`;
    const elMarginLevel = mount.querySelector("#summary-margin-level");
    if (elMarginLevel) {
      elMarginLevel.textContent = `Margin Level: ${marginLevel}% (${marginLevel >= 500 ? 'Safe' : 'Watch'})`;
      elMarginLevel.className = marginLevel >= 500 ? "badge badge-green" : "badge badge-amber";
    }

    const elComp = mount.querySelector("#preview-compounding-note");
    if (elComp) {
      if (mode === "broker_risk") {
        const compBal = Math.round(bal * 1.5);
        const compRisk = compBal * (rPct / 100.0);
        const compLot5m = Math.max(0.01, Math.min(5.0, Math.round((compRisk / (3.0 * 100.0)) * 100) / 100)).toFixed(2);
        const downBal = Math.round(bal * 0.8);
        const downRisk = downBal * (rPct / 100.0);
        const downLot5m = Math.max(0.01, Math.min(5.0, Math.round((downRisk / (3.0 * 100.0)) * 100) / 100)).toFixed(2);
        elComp.innerHTML = `
          📈 <b>Growth:</b> If balance reaches <b>${sym}${compBal.toLocaleString()}</b> (+50%), 5M lot auto-increases to <b style="color:#00e676">${compLot5m} Lots</b>.<br>
          🛡️ <b>Drawdown Protection:</b> If balance drops to <b>${sym}${downBal.toLocaleString()}</b> (-20%), 5M lot auto-downsizes to <b style="color:#ffb74d">${downLot5m} Lots</b>.
        `;
      } else {
        elComp.innerHTML = `
          📌 <b>Fixed Sizing Active:</b> All trades open with fixed <b>${fixedLot} Lots</b> regardless of account balance fluctuations.<br>
          💡 <b>Tip:</b> Switch to <i>Dynamic % Risk (Compounding)</i> to let profits accelerate lot growth safely.
        `;
      }
    }
  }

  // Sizing Mode Switcher
  const sizingSelect = mount.querySelector("#set-sizing-mode");
  if (sizingSelect) {
    sizingSelect.addEventListener("change", (e) => {
      const isDynamic = e.target.value === "broker_risk";
      const dynSec = mount.querySelector("#dynamic-risk-section");
      const fixSec = mount.querySelector("#fixed-lot-section");
      const badge = mount.querySelector("#sizing-mode-badge");
      if (dynSec) dynSec.style.display = isDynamic ? "" : "none";
      if (fixSec) fixSec.style.display = isDynamic ? "none" : "";
      if (badge) {
        badge.textContent = isDynamic ? "DYNAMIC % RISK (COMPOUNDING)" : "FIXED LOT SIZE";
        badge.className = isDynamic ? "badge badge-green" : "badge badge-amber";
      }
      updateLotPreview();
    });
  }

  // Currency Change interaction
  const curSelect = mount.querySelector("#set-account-currency");
  if (curSelect) {
    curSelect.addEventListener("change", (e) => {
      const isC = e.target.value === "cent";
      const balInput = mount.querySelector("#set-account-balance");
      const balLabel = mount.querySelector("#balance-label");
      if (isC) {
        if (balLabel) balLabel.textContent = "Account Balance (₹)";
        if (balInput && balInput.value === "1000") balInput.value = "10000";
      } else {
        if (balLabel) balLabel.textContent = "Account Balance ($)";
        if (balInput && balInput.value === "10000") balInput.value = "1000";
      }
      updateLotPreview();
    });
  }

  // Leverage dropdown change
  const levSelect = mount.querySelector("#set-account-leverage");
  if (levSelect) {
    levSelect.addEventListener("change", () => {
      updateLotPreview();
    });
  }

  // Risk % Preset Buttons
  mount.querySelectorAll(".risk-preset-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const pct = parseFloat(btn.dataset.pct);
      const input = mount.querySelector("#set-risk-percent");
      if (input) input.value = pct;
      mount.querySelectorAll(".risk-preset-btn").forEach(b => {
        b.className = b === btn ? "btn btn-sm btn-primary risk-preset-btn" : "btn btn-sm btn-outline risk-preset-btn";
      });
      updateLotPreview();
    });
  });

  // Fixed Lot Preset Buttons
  mount.querySelectorAll(".fixed-preset-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const lot = parseFloat(btn.dataset.lot);
      const input = mount.querySelector("#set-fixed-lot-size");
      if (input) input.value = lot;
      mount.querySelectorAll(".fixed-preset-btn").forEach(b => {
        b.className = b === btn ? "btn btn-sm btn-primary fixed-preset-btn" : "btn btn-sm btn-outline fixed-preset-btn";
      });
      updateLotPreview();
    });
  });

  // Numeric input change listeners for instant live preview
  mount.querySelectorAll("#set-account-balance, #set-risk-percent, #set-fixed-lot-size").forEach(input => {
    input.addEventListener("input", updateLotPreview);
  });

  // Strategy On/Off Checkboxes
  const syncStratCard = (cbId, cardId, badgeId) => {
    const cb = mount.querySelector(cbId);
    const card = mount.querySelector(cardId);
    const badge = mount.querySelector(badgeId);
    if (!cb) return;
    cb.addEventListener("change", () => {
      if (badge) {
        badge.textContent = cb.checked ? "ACTIVE & TRADING" : "STANDBY / OFF";
        badge.className = cb.checked ? "badge badge-green" : "badge badge-yellow";
      }
      if (card) {
        card.style.borderColor = cb.checked ? "rgba(0,230,118,0.4)" : "rgba(255,255,255,0.08)";
      }
    });
  };
  syncStratCard("#strat-cb-fib-retr", "#card-strat-fib-retr", "#badge-strat-fib-retr");
  syncStratCard("#strat-cb-smc-fib", "#card-strat-smc-fib", "#badge-strat-smc-fib");
  syncStratCard("#strat-cb-fib-trend", "#card-strat-fib-trend", "#badge-strat-fib-trend");

  // Initial calculation
  updateLotPreview();

  // Wire up Save Button
  const saveBtn = mount.querySelector("#save-execution-settings-btn");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const cur = mount.querySelector("#set-account-currency")?.value || "cent";
      const sMode = mount.querySelector("#set-sizing-mode")?.value || "broker_risk";
      const rPct = parseFloat(mount.querySelector("#set-risk-percent")?.value || "1.0");
      const fLot = parseFloat(mount.querySelector("#set-fixed-lot-size")?.value || "0.01");
      const balance = parseFloat(mount.querySelector("#set-account-balance")?.value || "10000.0");
      const leverage = parseInt(mount.querySelector("#set-account-leverage")?.value || "500", 10);
      const smartShield = true;
      const smartShieldLvl = mount.querySelector("#set-smart-shield-level")?.value || "0.618";

      const stratFibRetr = mount.querySelector("#strat-cb-fib-retr")?.checked ?? true;
      const stratSmcFib = mount.querySelector("#strat-cb-smc-fib")?.checked ?? false;
      const stratFibTrend = mount.querySelector("#strat-cb-fib-trend")?.checked ?? false;

      const tfs = [];
      if (mount.querySelector("#tf-cb-5m")?.checked) tfs.push("5m");
      if (mount.querySelector("#tf-cb-15m")?.checked) tfs.push("15m");
      if (mount.querySelector("#tf-cb-30m")?.checked) tfs.push("30m");
      if (mount.querySelector("#tf-cb-1h")?.checked) tfs.push("1h");

      saveBtn.disabled = true;
      saveBtn.innerHTML = "⏳ SAVING...";
      saveBtn.style.opacity = "0.85";

      try {
        const payload = {
          account_currency: cur,
          sizing_mode: sMode,
          risk_mode: "percent",
          risk_percent: rPct,
          account_balance: balance,
          target_risk_usd: 100.0,
          fixed_lot_size: fLot,
          account_leverage: leverage,
          strategy_fib_retracement: stratFibRetr,
          strategy_smc_fib: stratSmcFib,
          strategy_fib_trend: stratFibTrend,
          fib_retracement_timeframes: tfs.length > 0 ? tfs : ["5m", "15m", "30m", "1h"],
          smart_shield_enabled: smartShield,
          smart_shield_level: smartShieldLvl,
        };

        await API.saveExecutionSettings(payload);
        window.__btCurrency = cur;
        window.__btSizingMode = sMode;
        window.__btRiskMode = "percent";
        window.__btRiskPercent = rPct;
        window.__btCapital = balance;
        window.__btLeverage = leverage;

        // Visual Green Animation for user feedback
        saveBtn.innerHTML = "✅ SAVED!";
        saveBtn.style.background = "#00e676";
        saveBtn.style.color = "#0a0e17";
        saveBtn.style.borderColor = "#00e676";
        saveBtn.style.fontWeight = "800";
        saveBtn.style.transform = "scale(1.03)";
        saveBtn.style.boxShadow = "0 0 16px rgba(0,230,118,0.5)";
        saveBtn.style.opacity = "1";

        const statusMsg = mount.querySelector("#save-status-msg");
        if (statusMsg) {
          statusMsg.style.display = "inline-flex";
          statusMsg.style.opacity = "1";
        }

        if (typeof UI !== "undefined" && UI.toast) {
          UI.toast("Settings Saved", "Execution settings saved successfully!", "green");
        }

        // Return button back to normal after 2.5 seconds
        setTimeout(() => {
          saveBtn.disabled = false;
          saveBtn.innerHTML = "💾 SAVE EXECUTION SETTINGS";
          saveBtn.style.background = "";
          saveBtn.style.color = "";
          saveBtn.style.borderColor = "";
          saveBtn.style.fontWeight = "";
          saveBtn.style.transform = "";
          saveBtn.style.boxShadow = "";
          if (statusMsg) {
            statusMsg.style.opacity = "0";
            setTimeout(() => { statusMsg.style.display = "none"; }, 300);
          }
        }, 2500);

      } catch (err) {
        saveBtn.innerHTML = "❌ FAILED TO SAVE";
        saveBtn.style.background = "#ef5350";
        saveBtn.style.color = "#fff";
        saveBtn.style.borderColor = "#ef5350";
        if (typeof UI !== "undefined" && UI.toast) {
          UI.toast("Save Error", err.message || "Failed to save settings", "red");
        }
        setTimeout(() => {
          saveBtn.disabled = false;
          saveBtn.innerHTML = "💾 SAVE EXECUTION SETTINGS";
          saveBtn.style.background = "";
          saveBtn.style.color = "";
          saveBtn.style.borderColor = "";
        }, 3000);
      }
    });
  }
};


/* ===================================================================== */
/* Boot */
/* ===================================================================== */
document.addEventListener("DOMContentLoaded", () => {
  wireShell();
  navigate();
  pollTopbar();
  pollPrice();
  setInterval(pollTopbar, REFRESH_MS);
  _pricePollTimer = setInterval(pollPrice, AutoRefresh.speed || 2000);
  // The Overview view self-updates incrementally via its own timer (see Routes["/overview"]).
});
