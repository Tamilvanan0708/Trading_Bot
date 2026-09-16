/* XAU AI — API client (vanilla, no deps) */
"use strict";

const API = {
  base: "",
  // ---- raw fetch helper ----
  async get(path, params) {
    let url = this.base + path;
    if (params) {
      const q = new URLSearchParams();
      for (const k in params) if (params[k] !== undefined && params[k] !== null && params[k] !== "") q.set(k, params[k]);
      const s = q.toString();
      if (s) url += (url.includes("?") ? "&" : "?") + s;
    }
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), 15000);
    try {
      const res = await fetch(url, {
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      clearTimeout(tid);
      if (!res.ok) {
        let detail = res.statusText;
        try { const j = await res.json(); detail = j.detail || detail; } catch (_) {}
        throw new Error(detail || `HTTP ${res.status}`);
      }
      return res.json();
    } catch (err) {
      clearTimeout(tid);
      if (err.name === "AbortError") throw new Error("Request timed out (server busy)");
      throw err;
    }
  },
  async post(path, body) {
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), 15000);
    try {
      const res = await fetch(this.base + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
        signal: controller.signal,
      });
      clearTimeout(tid);
      if (!res.ok) {
        let detail = res.statusText;
        try { const j = await res.json(); detail = j.detail || detail; } catch (_) {}
        throw new Error(detail || `HTTP ${res.status}`);
      }
      return res.json();
    } catch (err) {
      clearTimeout(tid);
      if (err.name === "AbortError") throw new Error("Request timed out (server busy)");
      throw err;
    }
  },
  // ---- domain endpoints ----
  health: () => API.get("/health"),
  systemStatus: () => API.get("/market/system-status"),
  feedHealth: () => API.get("/market/live/health"),
  dataQuality: () => API.get("/market/data-quality"),
  liveMarket: (sym = "XAUUSD", params) => API.get(`/market/${sym}/live`, params),
  liveQuote: (sym = "XAUUSD") => API.get(`/market/${sym}/quote`),
  liveStreamURL: (sym = "XAUUSD", timeframe = "15m") => `${API.base}/market/${sym}/stream?timeframe=${encodeURIComponent(timeframe)}`,
  cachedMarket: (sym = "XAUUSD", timeframe = "15m") => API.get(`/market/${sym}/cache`, { timeframe }),
  liveAnalysis: (sym = "XAUUSD") => API.get(`/analysis/live/${sym}`),
  aiValidation: (sym = "XAUUSD") => API.get(`/ai/validation/${sym}`),
  overview: (sym = "XAUUSD") => API.get(`/overview/${sym}`),
  signals: (params) => API.get("/signals", params),
  signal: (id) => API.get(`/signals/${id}`),
  candidates: () => API.get("/research/candidates"),
  mtfReport: () => API.get("/research/mtf-report"),
  classification: () => API.get("/research/classification"),
  signalOutcomes: () => API.get("/research/signal-outcomes"),
  observation: () => API.get("/research/observation"),
  researchSummary: () => API.get("/research/summary"),
  paperTrades: () => API.get("/paper-trades"),
  account: () => API.get("/performance/account"),
  performance: () => API.get("/performance"),
  notifications: (limit = 50) => API.get("/notifications", { limit }),
  telegramStatus: () => API.get("/telegram/status"),
  structure: (sym = "XAUUSD") => API.get(`/structure/${sym}`),
  smc: (sym = "XAUUSD") => API.get(`/smc/${sym}`),
  fibonacci: (sym = "XAUUSD") => API.get(`/fibonacci/${sym}`),
  // Retracement BOS V1
  retracement: (sym = "XAUUSD") => API.get(`/retracement/${sym}`),
  retracementMulti: (sym = "XAUUSD") => API.get(`/retracement/multi/${sym}`),
  retracementHistory: (sym = "XAUUSD") => API.get(`/retracement/${sym}/history`),
  runRetracement: (sym = "XAUUSD") => API.post(`/retracement/${sym}/run`),
  // Runtime Execution Settings
  getExecutionSettings: () => API.get("/settings/execution"),
  saveExecutionSettings: (settings) => API.post("/settings/execution", settings),
  resetPaperTrades: () => API.post("/paper-trades/reset"),
  closePaperTrade: (id) => API.post(`/paper-trades/${id}/close`),
  // MT5 Bridge
  getMT5Status: () => API.get("/api/mt5/status"),
  sendMT5TestTrade: (payload) => API.post("/api/mt5/test-trade", payload),
};

/* Global app state cache (lightweight, updated by polling) */
const AppState = {
  price: null, change: null, changePct: null,
  bid: null, ask: null,
  regime: null, session: null, candleTs: null,
  feedConnected: false, dataDegraded: false,
  schedulerRunning: false, telegramOk: false,
  lastAnalysis: null, strategyGrade: null,
  lastSignalAt: null,
  set(partial) { Object.assign(this, partial); },
};
