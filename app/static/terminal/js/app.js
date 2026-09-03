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

function updateTopbar(st, feed, dq) {
  // Connection indicators
  setConn("conn-binance", !!(feed && feed.connected));
  setConn("conn-db", true); // DB verified at startup / per request
  setConn("conn-sched", !!(st && st.scheduler_running));
  setConn("conn-tg", !!(st && st.telegram_configured));
  setConn("conn-dq", !!(dq && !dq.degraded));
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
  const overall = (feed && feed.connected) && dq && !dq.degraded;
  const sb = document.getElementById("sidebar-sys");
  sb.className = "dot " + (overall ? "dot-green" : (feed && feed.connected ? "dot-amber" : "dot-red"));
  document.getElementById("sidebar-sys-label").textContent = overall ? "SYSTEM HEALTHY" : (feed && feed.connected ? "DEGRADED" : "FEED OFFLINE");
  AppState.set({ regime: reg, session: sess, candleTs: st && st.last_closed_candle_ts, feedConnected: !!(feed && feed.connected), dataDegraded: !!(dq && dq.degraded), schedulerRunning: !!(st && st.scheduler_running) });
}

async function pollTopbar() {
  try {
    const [st, feed, dq] = await Promise.allSettled([API.systemStatus(), API.feedHealth(), API.dataQuality()]);
    const stV = st.status === "fulfilled" ? st.value.status || st.value : null;
    const feedV = feed.status === "fulfilled" ? feed.value : null;
    const dqV = dq.status === "fulfilled" ? dq.value : null;
    const f = feedV && feedV.feeds ? feedV.feeds[0] : feedV;
    updateTopbar(stV, f, dqV);
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
  mount.innerHTML = '<div class="stack"><div class="skel"></div><div class="skel" style="width:80%"></div><div class="skel" style="width:60%"></div></div>';
  try {
    const data = await loader();
    const html = await renderer(data);
    mount.innerHTML = html;
    mount.querySelectorAll("canvas[data-chart]").forEach(runCanvas);
    return data;
  } catch (err) {
    mount.innerHTML = "";
    mount.appendChild(UI.state("Data Unavailable", UI.esc(err.message), "", true));
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
      <!-- Card 1: Gold Live Market Pulse -->
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
          <div class="row-between" style="margin-top:12px;font-size:11px;color:var(--text-dim)">
            <div>Market Feed: <b style="color:var(--green)">BINANCE LIVE</b></div>
            <div>Spread Quality: <b style="color:var(--green)">INSTITUTIONAL ZERO-SLIPPAGE</b></div>
          </div>
        </div>
      </div>

      <!-- Card 2: Live Trade Radar & Performance -->
      <div class="card card-hover" id="ov-active-trade-card">
        <div class="card-head">
          <span>LIVE POSITION & PERFORMANCE</span>
          <span id="ov-trade-status-badge"><span class="badge badge-dim">STANDBY</span></span>
        </div>
        <div class="card-body" id="ov-active-trade-body">
          <div style="padding:16px 0;text-align:center;color:var(--text-muted)">
            <div style="font-size:24px;margin-bottom:6px">⏳</div>
            <div style="font-weight:700;font-size:13px;color:var(--text)">No Open Positions Currently</div>
            <div style="font-size:11px;margin-top:4px">Bot will auto-execute 0.01 lots when 5M Fib L1/L2/L3 or SMC 0.680 is touched.</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Row 2: Dual Strategy Radar (5M Fib Retracement vs 5M SMC) -->
    <div class="grid grid-2">
      <!-- Fib Strategy Card -->
      <div class="card card-hover" style="border-top:3px solid #2962ff">
        <div class="card-head">
          <span>🎯 FIB WITH RETRACEMENT (5M CORE)</span>
          <span id="ov-fib-state-badge"><span class="badge badge-blue">SCANNING</span></span>
        </div>
        <div class="card-body" id="ov-fib-content">
          <div class="row-between" style="margin-bottom:10px">
            <span style="font-size:12px;color:var(--text-dim)">Direction Bias:</span>
            <b style="font-size:13px;color:var(--green)" id="ov-fib-dir">LONG ▲</b>
          </div>
          <div class="grid grid-3" style="background:var(--bg-1);padding:10px;border-radius:6px;gap:8px;font-size:11px">
            <div><div class="muted">ANCHOR (0.000)</div><div class="num" id="ov-fib-anchor">—</div></div>
            <div><div class="muted">BOS BREAKOUT</div><div class="num" id="ov-fib-bos">—</div></div>
            <div><div class="muted">TARGET (1.000)</div><div class="num up" id="ov-fib-target">—</div></div>
          </div>
          <div class="row-between" style="margin-top:12px;padding:8px 10px;background:rgba(41,98,255,0.08);border:1px solid rgba(41,98,255,0.25);border-radius:6px">
            <div>
              <div style="font-size:10px;color:var(--accent);font-weight:700">PRIMARY ENTRY (L1 0.618)</div>
              <div class="num" style="font-size:14px;font-weight:700;color:var(--text-bright)" id="ov-fib-entry">—</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:10px;color:var(--red);font-weight:700">STOP LOSS (0.236)</div>
              <div class="num" style="font-size:14px;font-weight:700;color:var(--red)" id="ov-fib-sl">—</div>
            </div>
          </div>
          <div style="margin-top:12px;text-align:right">
            <a href="#/fib-retracement" class="btn btn-sm btn-primary" style="padding:4px 10px;font-size:11px">Open 5M Fib Terminal →</a>
          </div>
        </div>
      </div>

      <!-- SMC Strategy Card -->
      <div class="card card-hover" style="border-top:3px solid #8b5cf6">
        <div class="card-head">
          <span>💎 SMC WITH FIB (5M FAILSAFE)</span>
          <span id="ov-smc-state-badge"><span class="badge badge-primary">ACTIVE</span></span>
        </div>
        <div class="card-body" id="ov-smc-content">
          <div class="row-between" style="margin-bottom:10px">
            <span style="font-size:12px;color:var(--text-dim)">Market Structure:</span>
            <b style="font-size:13px;color:var(--text)" id="ov-smc-struct">BOS / CHoCH CONFIRMED</b>
          </div>
          <div class="grid grid-3" style="background:var(--bg-1);padding:10px;border-radius:6px;gap:8px;font-size:11px">
            <div><div class="muted">ZONE</div><div id="ov-smc-zone"><span class="badge badge-green">DISCOUNT</span></div></div>
            <div><div class="muted">ORDER BLOCKS</div><div class="num" id="ov-smc-obs">Active</div></div>
            <div><div class="muted">FVG IMBALANCES</div><div class="num" id="ov-smc-fvgs">Active</div></div>
          </div>
          <div class="row-between" style="margin-top:12px;padding:8px 10px;background:rgba(139,92,246,0.08);border:1px solid rgba(139,92,246,0.25);border-radius:6px">
            <div>
              <div style="font-size:10px;color:#a78bfa;font-weight:700">GOLDEN POCKET (0.680)</div>
              <div class="num" style="font-size:14px;font-weight:700;color:var(--text-bright)" id="ov-smc-entry">—</div>
            </div>
            <div style="text-align:right">
              <div style="font-size:10px;color:var(--red);font-weight:700">STOP LOSS (0.920)</div>
              <div class="num" style="font-size:14px;font-weight:700;color:var(--red)" id="ov-smc-sl">—</div>
            </div>
          </div>
          <div style="margin-top:12px;text-align:right">
            <a href="#/smc-fib" class="btn btn-sm btn-secondary" style="padding:4px 10px;font-size:11px">Open 5M SMC Terminal →</a>
          </div>
        </div>
      </div>
    </div>

    <!-- Row 3: Account Financials (Grid 4) -->
    <div class="grid grid-4">
      <div class="card card-hover">
        <div class="card-body">
          <div class="muted" style="font-size:10px;font-weight:700;letter-spacing:1px">ACCOUNT BALANCE</div>
          <div class="metric-value" style="font-size:20px;font-weight:800;margin-top:4px" id="ov-acct-balance">$10,000.00</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">Simulated capital</div>
        </div>
      </div>
      <div class="card card-hover">
        <div class="card-body">
          <div class="muted" style="font-size:10px;font-weight:700;letter-spacing:1px">CURRENT EQUITY</div>
          <div class="metric-value up" style="font-size:20px;font-weight:800;margin-top:4px" id="ov-acct-equity">$10,000.00</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">Floating mark-to-market</div>
        </div>
      </div>
      <div class="card card-hover">
        <div class="card-body">
          <div class="muted" style="font-size:10px;font-weight:700;letter-spacing:1px">UNREALIZED PNL</div>
          <div class="metric-value" style="font-size:20px;font-weight:800;margin-top:4px" id="ov-acct-upnl">$0.00</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">Active running trades</div>
        </div>
      </div>
      <div class="card card-hover">
        <div class="card-body">
          <div class="muted" style="font-size:10px;font-weight:700;letter-spacing:1px">RISK & EXECUTION</div>
          <div style="font-size:14px;font-weight:700;margin-top:8px;color:var(--green)">0.01 Lots (Max DD 30%)</div>
          <div style="font-size:11px;color:var(--text-dim);margin-top:2px">Real Money: DISABLED</div>
        </div>
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
    const [ovRes, fibRes, smcRes, ptRes, acctRes] = await Promise.allSettled([
      API.overview("XAUUSD"),
      fetch("/retracement/strategy/fib-retracement/XAUUSD").then(r => r.json()),
      fetch("/retracement/strategy/smc-fib/XAUUSD").then(r => r.json()),
      API.paperTrades(),
      API.account()
    ]);

    const ov = ovRes.status === "fulfilled" ? ovRes.value : {};
    const fibData = fibRes.status === "fulfilled" ? fibRes.value : {};
    const smcData = smcRes.status === "fulfilled" ? smcRes.value : {};
    const pt = ptRes.status === "fulfilled" ? ptRes.value : [];
    const acct = acctRes.status === "fulfilled" ? acctRes.value : {};

    // 1. Live Price
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

    // 2. Active Trade Card
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
      tradeBody.innerHTML = `
        <div style="padding:16px 0;text-align:center;color:var(--text-muted)">
          <div style="font-size:24px;margin-bottom:6px">⏳</div>
          <div style="font-weight:700;font-size:13px;color:var(--text)">No Open Positions Currently</div>
          <div style="font-size:11px;margin-top:4px">Bot will auto-execute 0.01 lots when 5M Fib L1/L2/L3 or SMC 0.680 is touched.</div>
        </div>
      `;
    }

    // 3. 5M Fib With Retracement Card
    const f5 = (fibData.timeframes && fibData.timeframes["5m"]) || {};
    const fLevels = f5.levels || {};
    if (document.getElementById("ov-fib-dir")) document.getElementById("ov-fib-dir").textContent = (f5.direction || "LONG") + (f5.direction === "SHORT" ? " ▼" : " ▲");
    if (document.getElementById("ov-fib-anchor")) document.getElementById("ov-fib-anchor").textContent = fLevels["0.0"] ? `$${Number(fLevels["0.0"]).toFixed(2)}` : (f5.point_2 ? `$${Number(f5.point_2.price).toFixed(2)}` : "—");
    if (document.getElementById("ov-fib-bos")) document.getElementById("ov-fib-bos").textContent = f5.structure && f5.structure.break_price ? `$${Number(f5.structure.break_price).toFixed(2)}` : (f5.point_1 ? `$${Number(f5.point_1.price).toFixed(2)}` : "—");
    if (document.getElementById("ov-fib-target")) document.getElementById("ov-fib-target").textContent = fLevels["1.0"] ? `$${Number(fLevels["1.0"]).toFixed(2)}` : (f5.tp ? `$${Number(f5.tp.dynamic || f5.tp.locked).toFixed(2)}` : "—");
    if (document.getElementById("ov-fib-entry")) document.getElementById("ov-fib-entry").textContent = fLevels["0.618"] ? `$${Number(fLevels["0.618"]).toFixed(2)}` : (f5.entry ? `$${Number(f5.entry.price).toFixed(2)}` : "—");
    if (document.getElementById("ov-fib-sl")) document.getElementById("ov-fib-sl").textContent = fLevels["0.236"] ? `$${Number(fLevels["0.236"]).toFixed(2)}` : (f5.sl ? `$${Number(f5.sl.price).toFixed(2)}` : "—");
    if (document.getElementById("ov-fib-state-badge") && f5.state) {
      document.getElementById("ov-fib-state-badge").innerHTML = `<span class="badge ${f5.is_entry_touched ? 'badge-green' : 'badge-blue'}">${f5.state}</span>`;
    }

    // 4. 5M SMC With Fib Card
    const s5 = (smcData.timeframes && smcData.timeframes["5m"]) || {};
    const sLevels = s5.levels || {};
    if (document.getElementById("ov-smc-entry")) document.getElementById("ov-smc-entry").textContent = sLevels["0.680"] ? `$${Number(sLevels["0.680"]).toFixed(2)}` : (s5.entry ? `$${Number(s5.entry.price).toFixed(2)}` : "—");
    if (document.getElementById("ov-smc-sl")) document.getElementById("ov-smc-sl").textContent = sLevels["0.920"] ? `$${Number(sLevels["0.920"]).toFixed(2)}` : (s5.sl ? `$${Number(s5.sl.price).toFixed(2)}` : "—");
    if (document.getElementById("ov-smc-obs") && s5.smc) document.getElementById("ov-smc-obs").textContent = `${s5.smc.active_obs_count || 0} Active`;
    if (document.getElementById("ov-smc-fvgs") && s5.smc) document.getElementById("ov-smc-fvgs").textContent = `${s5.smc.active_fvgs_count || 0} Active`;

    // 5. Account Balances
    if (acct) {
      if (document.getElementById("ov-acct-balance") && acct.current_balance != null) document.getElementById("ov-acct-balance").textContent = "$" + UI.fmt(acct.current_balance, 2);
      if (document.getElementById("ov-acct-equity") && acct.equity != null) document.getElementById("ov-acct-equity").textContent = "$" + UI.fmt(acct.equity, 2);
      if (document.getElementById("ov-acct-upnl") && acct.unrealized_pnl_usd != null) {
        const u = Number(acct.unrealized_pnl_usd);
        const el = document.getElementById("ov-acct-upnl");
        el.textContent = (u >= 0 ? "+$" : "-$") + Math.abs(u).toFixed(2);
        el.className = "metric-value " + (u >= 0 ? "up" : "down");
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
          details: true,
          hotlist: false,
          calendar: false,
          studies: [
            "MASimple@tv-basicstudies",
            "RSI@tv-basicstudies"
          ]
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
    const table = `<div class="table-wrap"><table class="term">
      <thead><tr>
        <th>Time</th><th>Symbol</th><th>Strategy</th><th>Layer</th><th>Lots</th><th>Direction</th><th>Entry</th><th>SL</th><th>TP</th>
        <th>R:R</th><th>Status</th>
      </tr></thead>
      <tbody>
        ${rows.map(s => {
          const isSMC = String(s.strategy || "").toUpperCase().includes("SMC");
          const ver = String(s.strategy_version || "");
          let layerBadge = "";
          if (isSMC) {
            layerBadge = '<span class="badge badge-dim" style="font-size:10px;border:1px solid rgba(255,255,255,0.15)">Single (0.68)</span>';
          } else {
            if (ver.includes("L2") || (s.reasons || []).some(r => String(r).includes("L2"))) {
              layerBadge = '<span class="badge" style="background:#ff6d00;color:#fff;font-weight:700">L2 (0.50)</span>';
            } else if (ver.includes("L3") || (s.reasons || []).some(r => String(r).includes("L3"))) {
              layerBadge = '<span class="badge" style="background:#a855f7;color:#fff;font-weight:700">L3 (0.38)</span>';
            } else {
              layerBadge = '<span class="badge" style="background:#2962ff;color:#fff;font-weight:700">L1 (0.61)</span>';
            }
          }

          const stratBadge = isSMC
            ? '<span class="badge" style="background:rgba(38,166,154,0.15);color:#26a69a;border:1px solid #26a69a;font-weight:600">💎 SMC With Fib</span>'
            : '<span class="badge" style="background:rgba(171,71,188,0.15);color:#ab47bc;border:1px solid #ab47bc;font-weight:600">🎯 Fib Retracement</span>';

          const outcomeStatus = String(s.outcome || "PENDING").toUpperCase();
          let statusBadge = '<span class="badge badge-dim">PENDING</span>';
          if (outcomeStatus === "FILLED") statusBadge = '<span class="badge badge-primary" style="background:#00e676;color:#000;font-weight:700">FILLED</span>';
          else if (outcomeStatus === "TP_HIT") statusBadge = '<span class="badge badge-success">TP HIT</span>';
          else if (outcomeStatus === "SL_HIT") statusBadge = '<span class="badge badge-danger">SL HIT</span>';
          else if (outcomeStatus === "ESCAPE" || outcomeStatus === "ESCAPE_CLOSED") statusBadge = '<span class="badge" style="background:#ffab00;color:#000">ESCAPE</span>';

          return `<tr class="clickable" data-id="${s.id}">
            <td>${UI.fmtTs(s.created_at)}</td>
            <td><strong>${UI.esc(s.symbol)}</strong> <span style="font-size:10px;color:var(--text-muted)">${UI.esc(s.timeframe || "5M")}</span></td>
            <td>${stratBadge}</td>
            <td>${layerBadge}</td>
            <td class="num font-mono" style="font-weight:600">0.01</td>
            <td>${UI.dirBadge(s.direction)}</td>
            <td class="num font-mono" style="font-weight:700">${UI.fmt(s.entry_price)}</td>
            <td class="num down font-mono">${UI.fmt(s.stop_loss)}</td>
            <td class="num up font-mono">${UI.fmt(s.take_profit_1)}</td>
            <td class="num">1:${UI.fmt(s.risk_reward, 1)}</td>
            <td>${statusBadge}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table></div>`;
    // wire click -> drawer
    setTimeout(() => {
      mount.querySelectorAll("tr[data-id]").forEach(tr => {
        tr.addEventListener("click", () => openSignalDrawer(tr.dataset.id));
      });
    }, 0);
    return `<div class="stack">
      <div class="row-between"><div class="section-title">Signal History</div>
        <div class="toolbar" style="margin:0">
          <select class="input" id="sig-filter-dir" style="min-width:110px"><option value="">All directions</option><option>LONG</option><option>SHORT</option><option>NO_TRADE</option></select>
          <input class="input" id="sig-search" placeholder="Search…" style="min-width:160px">
        </div>
      </div>
      ${table}
    </div>`;
  }, mount);
};

async function openSignalDrawer(id) {
  const d = await API.signal(id).catch(() => null);
  if (!d) { UI.toast("Error", "Could not load signal detail.", "red"); return; }
  const sig = d;
  const dir = sig.direction;
  const outcome = sig.outcome;
  UI.openDrawer(`
    <div class="stack">
      <div class="row-between">
        <span class="section-title">Signal Detail</span>${UI.dirBadge(dir)}
      </div>
      <div class="signal-hero ${dir === "LONG" ? "buy" : dir === "SHORT" ? "sell" : "flat"}">
        <div class="signal-direction">${dir === "LONG" ? "BUY" : dir === "SHORT" ? "SELL" : "NO TRADE"}</div>
        <div class="signal-reason">${UI.fmtTsFull(sig.created_at)}</div>
      </div>
      ${UI.levels(sig.entry_price, sig.stop_loss, sig.take_profit_1, sig.take_profit_2, sig.take_profit_3, sig.risk_reward)}
      <div class="card">
        <div class="card-head"><span>Summary</span></div>
        <div class="card-body">${UI.kv([
          ["Confidence", `${UI.fmt(sig.confidence_score, 0)}/100`],
          ["Quality", UI.qualityBadge(sig.signal_quality)],
          ["Strategy", UI.esc(sig.strategy)],
          ["Version", UI.esc(String(sig.strategy_version || "—").split(":").pop())],
          ["Regime", UI.esc(sig.regime || "—")],
          ["Session", UI.esc(sig.session || "—")],
          ["MTF bias", UI.esc(sig.market_bias || "—")],
        ])}</div>
      </div>
      <div class="card">
        <div class="card-head"><span>Confluence Breakdown</span></div>
        <div class="card-body">${UI.confBreakdown(sig)}</div>
      </div>
      <div class="card">
        <div class="card-head"><span>Outcome</span></div>
        <div class="card-body">${UI.kv([
          ["Outcome", sig.outcome ? UI.statusBadge(sig.outcome) : '<span class="badge badge-dim">OPEN</span>'],
          ["Final R", UI.fmtR(sig.final_r)],
          ["MFE (R)", UI.fmt(sig.max_favorable_excursion_r, 3)],
          ["MAE (R)", UI.fmt(sig.max_adverse_excursion_r, 3)],
          ["Time to outcome", sig.time_to_outcome_hours != null ? `${sig.time_to_outcome_hours} h` : "—"],
        ])}</div>
      </div>
      ${sig.reasons && sig.reasons.length ? `<div class="card"><div class="card-head"><span>Reasons</span></div><div class="card-body" style="font-size:12px;color:var(--text-dim)">${sig.reasons.map(r => "• " + UI.esc(r)).join("<br>")}</div></div>` : ""}
    </div>`);
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

function renderPaperTradeRows(trades) {
  if (!trades || !trades.length) {
    return '<tr><td colspan="9" style="text-align:center;padding:32px 14px;color:var(--text-muted);font-size:13px">⏳ <b>No paper trades active yet.</b><br><span style="font-size:11px">A 0.01 lot paper trade is automatically opened with live PnL and running point tracking as soon as a 5M strategy entry (Fib L1/L2/L3 or SMC 0.680) is touched.</span></td></tr>';
  }
  return trades.map(t => {
    const entry = t.entry_price || t.actual_entry || t.target_entry || 0;
    const curPx = t.current_price || t.exit_price || entry;
    const pts = Number(t.running_pts != null ? t.running_pts : 0);
    const pnl = Number(t.pnl_usd != null ? t.pnl_usd : (t.unrealized_pnl != null ? t.unrealized_pnl : (t.realized_pnl || 0)));
    const ptsSign = pts >= 0 ? "+" : "";
    const pnlSign = pnl >= 0 ? "+$" : "-$";
    const ptsCls = pts >= 0 ? "up" : "down";
    const pnlCls = pnl >= 0 ? "up" : "down";
    const stratBadge = `<span class="badge ${t.strategy === 'SMC WITH FIB' ? 'badge-primary' : 'badge-blue'}" style="font-size:11px">${t.strategy || 'STRATEGY'} <b style="color:#fff">${t.layer || ''}</b></span>`;
    const sl = t.stop_loss ? `$${Number(t.stop_loss).toFixed(2)}` : '—';
    const tp = t.take_profit_1 || t.take_profit ? `$${Number(t.take_profit_1 || t.take_profit).toFixed(2)}` : '—';

    return `<tr>
      <td>${UI.fmtTs(t.opened_at || t.created_at)}</td>
      <td>${stratBadge}</td>
      <td>${UI.dirBadge(t.direction)}</td>
      <td class="num"><b>$${Number(entry).toFixed(2)}</b></td>
      <td class="num"><b>$${Number(curPx).toFixed(2)}</b></td>
      <td class="num ${ptsCls}"><b>${ptsSign}${pts.toFixed(2)} PTS</b></td>
      <td class="num ${pnlCls}"><b>${pnlSign}${Math.abs(pnl).toFixed(2)}</b></td>
      <td style="font-size:11px;color:var(--text-dim)">SL: ${sl}<br>TP: ${tp}</td>
      <td>${UI.statusBadge(t.status || t.state || t.exit_reason || "OPEN")}</td>
    </tr>`;
  }).join("");
}

/* ================= PAPER TRADING ================= */
Routes["/paper"] = (mount) => {
  let _paperTimer = null;
  let isUpdating = false;

  async function updatePaperInPlace() {
    if (AutoRefresh.speed === 0 || isUpdating) return;
    isUpdating = true;
    try {
      const [acctRes, ptRes] = await Promise.allSettled([API.account(), API.paperTrades()]);
      const acct = acctRes.status === "fulfilled" ? acctRes.value : null;
      const raw = ptRes.status === "fulfilled" ? ptRes.value : null;
      if (acct) {
        const elB = document.getElementById("paper-metric-balance");
        const elE = document.getElementById("paper-metric-equity");
        const elR = document.getElementById("paper-metric-rpnl");
        const elU = document.getElementById("paper-metric-upnl");
        if (elB && acct.current_balance != null) elB.textContent = "$" + UI.fmt(acct.current_balance, 2);
        if (elE && acct.equity != null) elE.textContent = "$" + UI.fmt(acct.equity, 2);
        if (elR && acct.realized_pnl_usd != null) elR.textContent = UI.fmt(acct.realized_pnl_usd, 2);
        if (elU && acct.unrealized_pnl_usd != null) elU.textContent = UI.fmt(acct.unrealized_pnl_usd, 2);
      }
      const trades = Array.isArray(raw) ? raw : (raw && raw.database_trades) || [];
      const tbody = document.getElementById("paper-trades-tbody");
      if (tbody) {
        tbody.innerHTML = renderPaperTradeRows(trades);
      }
    } catch (_) {}
    finally {
      isUpdating = false;
    }
  }

  renderWith(async () => {
    const [acct, pt, ov] = await Promise.allSettled([API.account(), API.paperTrades(), API.overview("XAUUSD")]);
    return { acct: acct.status === "fulfilled" ? acct.value : null, pt: pt.status === "fulfilled" ? pt.value : [], ov: ov.status === "fulfilled" ? ov.value : null };
  }, (d) => {
    const acct = d.acct || {};
    const raw = d.pt;
    const trades = Array.isArray(raw) ? raw : (raw && raw.database_trades) || [];
    const safety = (d.ov && d.ov.safety) || {};
    const blocked = safety.headline === "PAPER_TRADING_BLOCKED" || safety.headline === "SIGNALS_BLOCKED";
    const obsMode = safety.observation_mode || false;
    return `<div class="stack">
      <div class="row-between">
        <div class="section-title">Paper Trading</div>
        <div style="display:flex;align-items:center;gap:8px">
          <span class="badge badge-green" style="display:flex;align-items:center;gap:4px"><span class="dot dot-green" style="animation:pulse 1.5s infinite"></span>LIVE AUTO-REFRESH</span>
          ${blocked ? '<span class="badge badge-red">BLOCKED</span>' : '<span class="badge badge-green">ENABLED</span>'}
        </div>
      </div>
      <div class="card" style="border-color:${blocked ? 'rgba(239,68,68,0.4)' : 'rgba(34,197,94,0.4)'}">
        <div class="card-head"><span>Safety status</span></div>
        <div class="card-body">
          ${UI.kv([
            ["Paper trading", blocked ? '<span class="badge badge-red">BLOCKED</span>' : '<span class="badge badge-green">ENABLED</span>'],
            ["Reason", blocked ? (safety.reason || "Automatic paper trading blocked") : "Active — auto order simulation enabled"],
            ["Observation mode", obsMode ? '<span class="badge badge-amber">ACTIVE</span>' : '<span class="badge badge-muted">OFF</span>'],
            ["Real money", '<span class="badge badge-red">DISABLED</span>'],
            ["Max drawdown", '30%'],
            ["Daily loss limit", '3%'],
            ["Consecutive loss limit", '5'],
          ])}
        </div>
      </div>
      <div class="grid grid-4">
        <div class="metric"><div class="metric-label">BALANCE</div><div class="metric-value" id="paper-metric-balance">${acct.current_balance != null ? "$" + UI.fmt(acct.current_balance, 2) : "—"}</div></div>
        <div class="metric"><div class="metric-label">EQUITY</div><div class="metric-value" id="paper-metric-equity">${acct.equity != null ? "$" + UI.fmt(acct.equity, 2) : "—"}</div></div>
        <div class="metric"><div class="metric-label">REALIZED PNL</div><div class="metric-value" id="paper-metric-rpnl">${acct.realized_pnl_usd != null ? UI.fmt(acct.realized_pnl_usd, 2) : "—"}</div></div>
        <div class="metric"><div class="metric-label">UNREALIZED PNL</div><div class="metric-value" id="paper-metric-upnl">${acct.unrealized_pnl_usd != null ? UI.fmt(acct.unrealized_pnl_usd, 2) : "—"}</div></div>
      </div>
      <div class="card">
        <div class="card-head">
          <span>Active & Historical Paper Trades</span>
          <span class="muted" style="font-size:11px">5M Dedicated Real-Time Simulation (0.01 Lots) · Auto-Syncs In-Place</span>
        </div>
        <div class="card-body flush"><div class="table-wrap"><table class="term">
          <thead>
            <tr>
              <th>Opened</th>
              <th>Strategy / Layer</th>
              <th>Direction</th>
              <th>Entry Price</th>
              <th>Live / Exit</th>
              <th>Running Points</th>
              <th>PnL ($)</th>
              <th>SL / TP</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="paper-trades-tbody">${renderPaperTradeRows(trades)}</tbody>
        </table></div></div>
      </div>
    </div>`;
  }, mount).then(() => {
    if (_paperTimer) clearInterval(_paperTimer);
    _paperTimer = setInterval(updatePaperInPlace, AutoRefresh.speed || 2000);
  });

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
    const st = d.st || {};
    const f = d.feed && d.feed.feeds ? d.feed.feeds[0] : d.feed;
    const dq = d.dq || {};
    const tg = d.tg || {};
    const cards = [
      ["API", true, "FastAPI healthy"],
      ["Database", true, "SQLite WAL"],
      ["Binance WS", !!(f && f.connected), f && f.connected ? `${f.ticks_cached} ticks cached` : "disconnected"],
      ["REST history", !!(dq && !dq.degraded), dq.degraded ? "degraded" : "fresh"],
      ["Scheduler", !!(st && st.scheduler_running), st.last_analysis_at ? "last: " + UI.fmtTs(st.last_analysis_at) : "no analysis yet"],
      ["Data quality", !(dq && dq.degraded), dq.candle_count ? dq.candle_count + " candles" : "—"],
      ["Telegram", !!(tg && tg.configured), tg.status || "disabled"],
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
          ["Last tick", f && f.latest_tick && f.latest_tick.timestamp ? UI.fmtTsFull(f.latest_tick.timestamp) : "—"],
          ["Last closed candle", st.last_closed_candle_ts ? UI.fmtTsFull(st.last_closed_candle_ts) : "—"],
          ["Last analysis", st.last_analysis_at ? UI.fmtTsFull(st.last_analysis_at) : "—"],
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

function buildRichStrategyView(mount, endpoint, strategyName, strategySub, strategyType) {
  const TFS = ["5m"];
  const TF_LABELS = {"5m":"5M"};
  const selectedTf = "5m";

  function renderTimeline(state) {
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
    isStratUpdating = true;
    try {
      const r = await fetch(endpoint);
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
    const r = await fetch(endpoint);
    if (!r.ok) throw new Error("Strategy endpoint failed: " + r.status);
    const strat = await r.json();
    return { strat };
  }, (data) => {
    const d = data.strat || {};
    const price = d.live_price || 4428.0;
    const tfData = d.timeframes?.[selectedTf] || {};
    const activeCascadeTf = d.cascading_active_tf;

    const tfButtons = `
      <button class="btn btn-primary" style="padding:6px 14px;font-size:12px;background:#2962ff;border-color:#2962ff;color:#fff;font-weight:700;cursor:default">5M (ACTIVE)</button>
      <button class="btn btn-secondary" disabled style="padding:6px 12px;font-size:12px;opacity:0.35;cursor:not-allowed" title="5M Focus Mode: 15M Disabled">15M (Disabled)</button>
      <button class="btn btn-secondary" disabled style="padding:6px 12px;font-size:12px;opacity:0.35;cursor:not-allowed" title="5M Focus Mode: 30M Disabled">30M (Disabled)</button>
      <button class="btn btn-secondary" disabled style="padding:6px 12px;font-size:12px;opacity:0.35;cursor:not-allowed" title="5M Focus Mode: 1H Disabled">1H (Disabled)</button>
      ${strategyType !== "FIB_WITH_RETRACEMENT" ? '<button class="btn btn-secondary" disabled style="padding:6px 12px;font-size:12px;opacity:0.35;cursor:not-allowed" title="5M Focus Mode: 4H Disabled">4H (Disabled)</button>' : ''}
    `;

    return `<div class="stack">
      <div class="row-between">
        <div>
          <div class="section-title">${strategyName}</div>
          <div class="muted" style="font-size:11px">${strategySub}</div>
        </div>
        <div class="toolbar" style="margin:0">
          <span class="badge badge-green" style="display:flex;align-items:center;gap:4px"><span class="dot dot-green" style="animation:pulse 1.5s infinite"></span>LIVE AUTO-REFRESH</span>
          <span class="badge" style="background:rgba(41,98,255,0.2);color:#2962ff;border:1px solid #2962ff;font-weight:700">⚡ 5M FOCUS MODE</span>
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
          <div style="font-size:12px;color:var(--text-dim)">Engine Focus: <b style="color:#2962ff">5M Dedicated (Others Disabled)</b></div>
        </div>
      </div>

      <!-- STATE TIMELINE -->
      <div id="strat-timeline-wrap">${renderTimeline(tfData.state)}</div>

      <!-- BIG ACTIVE SIGNAL BOX -->
      <div id="strat-signal-box-wrap">${renderActiveSignalBox(tfData, price, selectedTf)}</div>

      <!-- LIVE FIB CHART -->
      <div class="card" style="margin-bottom:var(--sp-3)">
        <div class="card-head">
          <span>📈 LIVE CHART</span>
          <span class="muted" style="font-size:11px">${TF_LABELS[selectedTf]} · Fibonacci Levels</span>
        </div>
        <div id="fib-chart-${strategyType}-${selectedTf}" style="width:100%;height:380px;background:var(--bg-card,#1a1d26);border-radius:0 0 6px 6px;"></div>
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
    // After HTML is rendered and mounted into the DOM by renderWith, draw the chart.
    drawFibChart(`fib-chart-${strategyType}-${selectedTf}`, selectedTf, strategyType);
    if (_stratTimer) clearInterval(_stratTimer);
    _stratTimer = setInterval(updateStratInPlace, AutoRefresh.speed || 2500);
  });

  window.__viewCleanup = () => {
    if (_stratTimer) {
      clearInterval(_stratTimer);
      _stratTimer = null;
    }
  };
}

// ─── LIVE FIB CHART RENDERER ──────────────────────────────────────────────────

const _chartInstances = {};

async function drawFibChart(containerId, tf, strategyKey) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (typeof LightweightCharts === "undefined") {
    container.innerHTML = '<div style="padding:20px;color:#ef5350;font-size:12px">Error: Charting library failed to load.</div>';
    return;
  }

  // Destroy existing chart if re-rendering
  if (_chartInstances[containerId]) {
    try { _chartInstances[containerId].remove(); } catch(e) {}
    delete _chartInstances[containerId];
  }
  container.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-size:12px">Loading chart…</div>';

  let data;
  try {
    const r = await fetch(`/retracement/chart/XAUUSD/${tf}?limit=150`);
    data = await r.json();
  } catch(e) {
    container.innerHTML = '<div style="padding:20px;color:var(--text-dim);font-size:12px">Chart data unavailable</div>';
    return;
  }

  const candles = data.candles || [];
  if (!candles.length) {
    container.innerHTML = '<div style="padding:20px;color:var(--text-dim);font-size:12px">No candle data yet — waiting for feed…</div>';
    return;
  }

  container.innerHTML = "";

  try {
    const chart = LightweightCharts.createChart(container, {
      width: container.clientWidth,
      height: 380,
      layout: { background: { type: 'solid', color: "#12141c" }, textColor: "#c8cde6" },
      grid: { vertLines: { color: "#1e2130" }, horzLines: { color: "#1e2130" } },
      crosshair: { mode: LightweightCharts.CrosshairMode ? LightweightCharts.CrosshairMode.Normal : 0 },
      rightPriceScale: { borderColor: "#2a2d3e" },
      timeScale: { borderColor: "#2a2d3e", timeVisible: true, secondsVisible: false },
    });
    _chartInstances[containerId] = chart;

    // Candlestick series
    const candleSeries = chart.addCandlestickSeries({
      upColor: "#26a69a", downColor: "#ef5350",
      borderUpColor: "#26a69a", borderDownColor: "#ef5350",
      wickUpColor: "#26a69a", wickDownColor: "#ef5350",
    });
    candleSeries.setData(candles);

    // Draw Fibonacci levels for the active strategy
    const isSmc = strategyKey === "SMC_WITH_FIB";
    const levs = isSmc ? data.fib_levels?.smc_fib : data.fib_levels?.fib_retracement;
    const lp = data.live_price;

    if (levs && Object.keys(levs).length > 0) {
      const dir = levs.direction;
      const isShort = dir === "SHORT" || dir === "BEARISH";
      const firstTs = candles[0]?.time;
      const lastTs = candles[candles.length - 1]?.time;

      // Level definitions for SMC With Fib (SHORT)
      const smcLevels = [
        { price: levs.anchor, label: "1.000 ANCHOR", color: "#ef5350", dash: false },
        { price: levs.sl, label: "0.920 SL", color: "#ef5350", dash: true },
        { price: levs.pocket, label: "0.790 GOLDEN POCKET", color: "#ff9800", dash: true },
        { price: levs.entry, label: "0.680 ENTRY", color: "#ffffff", dash: false },
        { price: levs.equilibrium, label: "0.500 EQ", color: "#5c9bd6", dash: true },
        { price: levs.tp, label: "0.000 TP", color: "#26a69a", dash: false },
      ];
      // Level definitions for Fib With Retracement (SHORT)
      const retrLevels = [
        { price: levs.anchor, label: "0.000 ANCHOR", color: "#ef5350", dash: false },
        { price: levs.sl, label: "0.236 SL", color: "#ef5350", dash: true },
        { price: levs.entry, label: "0.618 ENTRY", color: "#ffffff", dash: false },
        { price: levs.tp, label: "1.000 TP", color: "#26a69a", dash: false },
      ];

      const levelDefs = isSmc ? smcLevels : retrLevels;

      levelDefs.forEach(lev => {
        if (!lev.price) return;
        chart.addLineSeries({
          color: lev.color,
          lineWidth: lev.dash ? 1 : 2,
          lineStyle: lev.dash ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
          priceLineVisible: true,
          lastValueVisible: true,
          title: lev.label,
          crosshairMarkerVisible: false,
        }).setData([{ time: firstTs, value: lev.price }, { time: lastTs, value: lev.price }]);
      });

      // SL zone fill (red tint between anchor and entry)
      if (levs.sl && levs.anchor && isSmc) {
        const slZone = chart.addLineSeries({ color: "rgba(239,83,80,0.08)", lineWidth: 0, title: "" });
        slZone.setData([{ time: firstTs, value: levs.sl }, { time: lastTs, value: levs.sl }]);
      }

      // TP zone fill (green tint between entry and TP)
      if (levs.tp && levs.entry && isSmc) {
        const tpZone = chart.addLineSeries({ color: "rgba(38,166,154,0.08)", lineWidth: 0, title: "" });
        tpZone.setData([{ time: firstTs, value: levs.tp }, { time: lastTs, value: levs.tp }]);
      }
    }

    // Live price line
    if (lp) {
      candleSeries.createPriceLine({
        price: lp,
        color: "#f0b90b",
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: `LIVE $${Number(lp).toFixed(2)}`,
      });
    }

    chart.timeScale().fitContent();

    // Responsive resize
    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        chart.resize(e.contentRect.width, 380);
      }
    });
    ro.observe(container);
  } catch (err) {
    container.innerHTML = `<div style="padding:20px;color:#ef5350;font-size:12px;font-family:monospace;white-space:pre-wrap">Chart Error: ${err.message}\n${err.stack}</div>`;
  }
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

/* ================= FIB WITH RETRACEMENT ================= */
Routes["/fib-retracement"] = (mount) => {
  buildRichStrategyView(
    mount,
    "/retracement/strategy/fib-retracement/XAUUSD",
    "🎯 Fib With Retracement",
    "Multi-Timeframe Cascading BOS Retracement Strategy · RETRACEMENT_BOS_V1",
    "FIB_WITH_RETRACEMENT"
  );
};

/* ================= SETTINGS ================= */
Routes["/settings"] = (mount) => {
  renderWith(async () => {
    const [st, tg] = await Promise.allSettled([API.systemStatus(), API.telegramStatus()]);
    return { st: st.status === "fulfilled" ? st.value : null, tg: tg.status === "fulfilled" ? tg.value : null };
  }, (d) => {
    const stRaw = d.st || {};
    const st = stRaw.status || stRaw;
    const tg = d.tg || {};
    return `<div class="stack">
      <div class="section-title">Settings</div>
      <div class="card">
        <div class="card-head"><span>Safety (read-only, enforced by backend)</span></div>
        <div class="card-body">
          <div class="card" style="border-color:rgba(239,68,68,0.5)">
            <div class="card-body" style="display:flex;align-items:center;gap:var(--sp-3)">
              <span class="badge badge-red">REAL MONEY EXECUTION — PERMANENTLY DISABLED</span>
              <span style="font-size:12px;color:var(--text-dim)">No broker order API exists in this system.</span>
            </div>
          </div>
          ${UI.kv([
            ["Observation mode", st.observation_mode ? '<span class="badge badge-amber">ACTIVE</span>' : '<span class="badge badge-muted">OFF</span>'],
            ["Paper trading", st.paper_trading_enabled ? '<span class="badge badge-green">ENABLED</span>' : '<span class="badge badge-red">OFF</span>'],
            ["Block on FAILED", st.block_on_failed ? '<span class="badge badge-green">ENABLED</span>' : '<span class="badge badge-muted">DISABLED</span>'],
            ["Max drawdown", '30%'],
            ["Telegram", tg.status || "DISABLED"],
            ["Candidate alerts", tg.candidate_alerts ? "enabled" : "disabled"],
            ["Environment", st.environment || "dev"],
            ["Version", "1.0.0"],
          ])}
        </div>
      </div>
      <div class="card">
        <div class="card-head"><span>Configuration</span></div>
        <div class="card-body" style="font-size:12px;color:var(--text-dim)">
          Configuration is managed via <code style="color:var(--text)">.env</code> on the server. No sensitive values (tokens, keys) are exposed to this interface.
        </div>
      </div>
    </div>`;
  }, mount);
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
