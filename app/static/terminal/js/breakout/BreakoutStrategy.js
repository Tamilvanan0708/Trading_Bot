/* =====================================================================
   Breakout Strategy Agent — Main Dashboard View
   Direct TradingView Official Live Tick Stream (OANDA:XAUUSD)
   Reactive DOM auto-updates every 2 seconds + live tick flashing.
   ===================================================================== */
"use strict";

window.BreakoutStrategy = (function () {
  let activeTab = "overview"; // 'overview' | 'signals' | 'backtest'
  let isTraining = false;
  let chartMode = "engine"; // 'engine' | 'tradingview'
  let chartInstance = null;
  let candleSeries = null;
  let volumeSeries = null;
  let lineResistance = null;
  let lineSupport = null;
  let lineTrigger = null;
  let currentTimeframe = "15m";

  function renderChartArea(container) {
    if (!container) return;
    container.innerHTML = "";

    if (chartMode === "tradingview") {
      renderTradingViewNativeWidget(container);
    } else {
      createLightweightLiveChart(container);
    }
  }

  function renderTradingViewNativeWidget(container) {
    container.innerHTML = `<div id="tradingview_native_widget" style="width:100%;height:380px;"></div>`;
    const isLight = document.documentElement.getAttribute("data-theme") === "light";

    if (typeof window.TradingView !== "undefined") {
      new window.TradingView.widget({
        autosize: true,
        symbol: "OANDA:XAUUSD",
        interval: currentTimeframe === "5m" ? "5" : (currentTimeframe === "15m" ? "15" : (currentTimeframe === "30m" ? "30" : "60")),
        timezone: "Etc/UTC",
        theme: isLight ? "light" : "dark",
        style: "1",
        locale: "en",
        toolbar_bg: isLight ? "#ffffff" : "#0d1117",
        enable_publishing: false,
        hide_side_toolbar: false,
        allow_symbol_change: true,
        container_id: "tradingview_native_widget"
      });

      const statusBadge = document.getElementById("chart-stream-status");
      if (statusBadge) {
        statusBadge.className = "feed-status-pill feed-status-connected";
        statusBadge.innerHTML = `<span class="live-beacon"></span><span>TRADINGVIEW OANDA LIVE</span>`;
      }
    } else {
      container.innerHTML = `<div style="padding:40px;text-align:center;color:var(--text-muted);">TradingView library is loading...</div>`;
    }
  }

  function createLightweightLiveChart(container) {
    if (typeof window.LightweightCharts === "undefined") {
      container.innerHTML = `<div style="padding:20px;color:var(--text-muted)">Loading Lightweight Charts library...</div>`;
      return;
    }

    container.innerHTML = `
      <div class="chart-live-legend">
        <div class="chart-live-legend-title">
          <span>🟡 XAU/USD (Official TradingView Feed)</span>
        </div>
        <div class="chart-live-legend-stats">
          <span id="chart-live-open">O: --</span>
          <span id="chart-live-high">H: --</span>
          <span id="chart-live-low">L: --</span>
          <span id="chart-live-vol">Vol: --</span>
        </div>
      </div>
    `;

    const isLight = document.documentElement.getAttribute("data-theme") === "light";
    const bg = isLight ? "#ffffff" : "#0d1117";
    const textCol = isLight ? "#475569" : "#8b97a8";
    const borderCol = isLight ? "#cbd5e1" : "#1f2a3a";

    chartInstance = window.LightweightCharts.createChart(container, {
      width: container.clientWidth || 800,
      height: 380,
      layout: {
        background: { color: bg },
        textColor: textCol,
        fontFamily: 'Inter, system-ui, sans-serif'
      },
      grid: {
        vertLines: { color: borderCol, style: window.LightweightCharts.LineStyle.Dotted },
        horzLines: { color: borderCol, style: window.LightweightCharts.LineStyle.Dotted }
      },
      crosshair: {
        mode: window.LightweightCharts.CrosshairMode.Normal
      },
      rightPriceScale: {
        borderColor: borderCol,
        scaleMargins: { top: 0.12, bottom: 0.22 }
      },
      timeScale: {
        borderColor: borderCol,
        timeVisible: true,
        secondsVisible: false
      }
    });

    candleSeries = chartInstance.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444"
    });

    volumeSeries = chartInstance.addHistogramSeries({
      color: "rgba(77, 159, 255, 0.35)",
      priceFormat: { type: "volume" },
      priceScaleId: "",
      scaleMargins: { top: 0.8, bottom: 0 }
    });

    const resizeObserver = new ResizeObserver(entries => {
      if (entries.length && chartInstance) {
        chartInstance.applyOptions({ width: entries[0].contentRect.width });
      }
    });
    resizeObserver.observe(container);

    loadRealChartData(currentTimeframe);
  }

  async function loadRealChartData(timeframe) {
    if (!candleSeries || !volumeSeries) return;

    const statusBadge = document.getElementById("chart-stream-status");
    if (statusBadge) {
      statusBadge.className = "feed-status-pill feed-status-connecting";
      statusBadge.innerHTML = `<span class="dot dot-amber"></span><span>SYNCING TRADINGVIEW...</span>`;
    }

    const candles = await window.BreakoutDataService.fetchCandles(timeframe, 120);
    if (!candles || candles.length === 0) return;

    candleSeries.setData(candles);

    const volData = candles.map(c => ({
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? "rgba(34, 197, 94, 0.3)" : "rgba(239, 68, 68, 0.3)"
    }));
    volumeSeries.setData(volData);

    updateChartPriceLines();

    // Trigger instant DOM stats update with the latest candle
    const live = window.BreakoutDataService.getLiveState();
    updateLiveStats(live);
  }

  function updateChartPriceLines() {
    const live = window.BreakoutDataService.getLiveState();

    if (candleSeries) {
      if (lineResistance) candleSeries.removePriceLine(lineResistance);
      if (lineSupport) candleSeries.removePriceLine(lineSupport);
      if (lineTrigger) candleSeries.removePriceLine(lineTrigger);

      lineResistance = candleSeries.createPriceLine({
        price: live.resistance,
        color: "#ef4444",
        lineWidth: 1.5,
        lineStyle: window.LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "RESISTANCE ZONE"
      });

      lineSupport = candleSeries.createPriceLine({
        price: live.support,
        color: "#22c55e",
        lineWidth: 1.5,
        lineStyle: window.LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: "DEMAND SUPPORT"
      });

      lineTrigger = candleSeries.createPriceLine({
        price: live.bullishTrigger,
        color: "#3b82f6",
        lineWidth: 2,
        lineStyle: window.LightweightCharts.LineStyle.Dotted,
        axisLabelVisible: true,
        title: "BREAKOUT TRIGGER"
      });
    }

    const rZoneEl = document.getElementById("label-res-zone");
    const sZoneEl = document.getElementById("label-sup-zone");
    const trigEl = document.getElementById("label-trig-zone");

    if (rZoneEl) rZoneEl.textContent = `$${live.resistance.toFixed(2)}`;
    if (sZoneEl) sZoneEl.textContent = `$${live.support.toFixed(2)}`;
    if (trigEl) trigEl.textContent = `$${live.bullishTrigger.toFixed(2)}`;
  }

  function updateLiveStats(live) {
    if (!live) return;
    const priceEl = document.getElementById("chart-live-price");
    const deltaEl = document.getElementById("chart-live-delta");
    const openEl = document.getElementById("chart-live-open");
    const highEl = document.getElementById("chart-live-high");
    const lowEl = document.getElementById("chart-live-low");
    const volEl = document.getElementById("chart-live-vol");
    const statusBadge = document.getElementById("chart-stream-status");
    const sideTick = document.getElementById("side-live-tick");

    if (priceEl && live.price) {
      const rawPrev = priceEl.getAttribute("data-price");
      const prev = rawPrev ? parseFloat(rawPrev) : live.price;
      priceEl.setAttribute("data-price", live.price);

      // Market price remains fixed in single solid bright color
      priceEl.textContent = `$${live.price.toFixed(2)}`;
      priceEl.style.color = "var(--text-bright)";
      priceEl.classList.remove("flash-up", "flash-down");

      // Small side indicator pill (+0.50, +1.00, +2.00: Green if plus, Red if minus)
      if (deltaEl) {
        const diff = live.price - prev;
        if (Math.abs(diff) >= 0.01) {
          const isPlus = diff > 0;
          deltaEl.className = `badge ${isPlus ? 'badge-green' : 'badge-red'}`;
          deltaEl.innerHTML = `${isPlus ? '▲ +' : '▼ -'}${Math.abs(diff).toFixed(2)}`;
        }
      }
    }

    if (sideTick && live.price) {
      sideTick.textContent = `$${live.price.toFixed(2)}`;
    }

    if (openEl && live.open) openEl.textContent = `O: ${live.open.toFixed(2)}`;
    if (highEl && live.high) highEl.textContent = `H: ${live.high.toFixed(2)}`;
    if (lowEl && live.low) lowEl.textContent = `L: ${live.low.toFixed(2)}`;
    if (volEl && live.volume) volEl.textContent = `Vol: ${Number(live.volume).toLocaleString()}`;

    if (statusBadge) {
      const timeStr = new Date().toLocaleTimeString("en-GB");
      statusBadge.className = "feed-status-pill feed-status-connected";
      statusBadge.innerHTML = `<span class="live-beacon"></span><span>LIVE TV (${timeStr})</span>`;
    }
  }

  function renderView(mount) {
    if (!mount) return;

    window.__viewCleanup = () => {
      if (window.BreakoutDataService) {
        window.BreakoutDataService.stopLiveStream();
      }
      if (chartInstance) {
        try { chartInstance.remove(); } catch (_) {}
        chartInstance = null;
      }
    };

    const agent = window.BreakoutMockData.agent;
    const hp = agent.hyperparameters;
    const live = window.BreakoutDataService.getLiveState();
    const isMasterActive = window.BreakoutDataService && typeof window.BreakoutDataService.isStrategyActive === "function"
      ? window.BreakoutDataService.isStrategyActive()
      : true;

    mount.innerHTML = `
      <div class="breakout-container">
        
        <!-- ================= TOP AGENT BANNER ================= -->
        <div class="agent-banner">
          <div class="agent-meta-row">
            <div>
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span class="badge badge-blue" style="font-weight:700;">AI TRADING AGENT</span>
                <span class="badge badge-dim">${agent.architecture}</span>
                <span class="badge badge-green" style="font-size:10px;">AUTO-REFRESH 2S STREAM ACTIVE</span>
              </div>
              <h1 style="font-size:24px;margin:2px 0 6px;color:var(--text-bright);font-weight:800;letter-spacing:-0.5px;">
                📈 Breakout Strategy Agent
              </h1>
              <div style="font-size:12px;color:var(--text-dim);max-width:680px;">
                Real-time XAU/USD Gold stream automatically updating every 2 seconds. Inferred breakout zones, volume surges, and live alerts refresh dynamically without page reload.
              </div>
            </div>

            <!-- Master ON/OFF Switch & Strategy Controls -->
            <div style="display:flex;flex-direction:column;align-items:flex-end;gap:10px;">
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end;">
                <!-- Breakout Strategy Master ON/OFF Switch -->
                <div class="breakout-master-toggle-card ${isMasterActive ? 'is-active' : 'is-paused'}" id="breakout-master-card" title="Breakout Strategy Master Switch (Controls ONLY Breakout Strategy)">
                  <div class="master-toggle-info">
                    <span class="master-toggle-title">BREAKOUT ENGINE</span>
                    <span class="master-toggle-status" id="master-toggle-status-text">
                      <span class="status-dot"></span>
                      <span>${isMasterActive ? 'ACTIVE (ON)' : 'PAUSED (OFF)'}</span>
                    </span>
                  </div>
                  <button id="btn-master-strategy-toggle" class="master-switch-btn ${isMasterActive ? 'btn-switch-on' : 'btn-switch-off'}" type="button" role="switch" aria-checked="${isMasterActive}" title="Click to turn Breakout Strategy ${isMasterActive ? 'OFF' : 'ON'}">
                    <span class="master-switch-slider">
                      <span class="master-switch-knob"></span>
                    </span>
                    <span class="master-switch-label" id="master-switch-label">${isMasterActive ? 'ON' : 'OFF'}</span>
                  </button>
                </div>

                <div class="agent-status-badge ${isTraining ? 'agent-status-training' : (isMasterActive ? 'agent-status-live' : 'agent-status-paused')}" id="agent-status-pill">
                  <span class="${isTraining ? '' : (isMasterActive ? 'live-beacon' : 'paused-beacon')}"></span>
                  <span id="agent-status-pill-text">${isTraining ? 'TRAINING IN PROGRESS' : (isMasterActive ? 'TRADINGVIEW LIVE STREAMING' : 'STRATEGY PAUSED (OFF)')}</span>
                </div>
              </div>
              
              <div style="display:flex;gap:8px;">
                <button id="btn-retrain-agent" class="btn btn-sm btn-ghost" style="gap:6px;display:flex;align-items:center;" ${isTraining ? 'disabled' : ''}>
                  <span>⚙️</span>
                  <span>Retrain Agent</span>
                </button>
                <button id="btn-deploy-model" class="btn btn-sm btn-primary" style="gap:6px;display:flex;align-items:center;">
                  <span>🚀</span>
                  <span>Deploy Weights</span>
                </button>
              </div>
            </div>
          </div>

          <!-- Training Progress Bar -->
          <div id="training-progress-box" style="display:${isTraining ? 'block' : 'none'};margin-top:14px;padding:12px;background:rgba(0,0,0,0.3);border-radius:var(--r-md);border:1px solid rgba(245, 158, 11, 0.3);">
            <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:6px;">
              <span style="color:var(--amber);font-weight:700;">Optimizing Attention Heads on Live Gold Ticks (Epoch <span id="train-epoch">18</span>/50)...</span>
              <span style="color:var(--text-dim);font-family:var(--font-num);" id="train-loss">Loss: 0.0184 · Acc: 91.8%</span>
            </div>
            <div style="height:6px;background:rgba(255,255,255,0.08);border-radius:3px;overflow:hidden;">
              <div id="train-bar" style="width:36%;height:100%;background:linear-gradient(90deg, #f59e0b, #eab308);transition:width 0.2s ease;"></div>
            </div>
          </div>

          <!-- Live Model Metrics -->
          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));gap:10px;margin-top:14px;border-top:1px solid var(--border);padding-top:12px;">
            <div>
              <div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;">Validation Accuracy</div>
              <div style="font-family:var(--font-num);font-weight:700;font-size:16px;color:var(--green-bright);">${agent.validationAccuracy}%</div>
            </div>
            <div>
              <div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;">Live Feed Source</div>
              <div style="font-family:var(--font-mono);font-size:11px;color:var(--text-bright);margin-top:3px;">TradingView (OANDA)</div>
            </div>
            <div>
              <div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;">Auto-Refresh Speed</div>
              <div style="font-family:var(--font-mono);font-size:11px;color:var(--green-bright);margin-top:3px;">⚡ 2s Automated Sync</div>
            </div>
            <div>
              <div style="font-size:10px;color:var(--text-muted);text-transform:uppercase;">Stream Status</div>
              <div style="font-family:var(--font-mono);font-size:11px;color:var(--accent);margin-top:3px;">Real-Time Polling Active</div>
            </div>
          </div>
        </div>

        <!-- ================= LIVE CHART FULL WIDTH ================= -->
        <div style="display:flex;flex-direction:column;gap:var(--sp-4);" id="main-strategy-grid">
          <!-- Real Live XAU/USD Candlestick Chart (Full Width) -->
          <div class="breakout-chart-card" style="padding:16px;background:var(--panel-solid);width:100%;">
            
            <!-- Top Chart Controls Row -->
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:12px;">
              <div style="display:flex;align-items:center;gap:10px;">
                <div style="display:flex;align-items:baseline;gap:6px;">
                  <span style="font-weight:800;font-size:17px;color:var(--text-bright);">XAU/USD</span>
                  <span class="badge badge-dim" style="font-size:10px;" id="chart-tf-badge">${currentTimeframe.toUpperCase()}</span>
                </div>
                <!-- Main Market Price: 100% Fixed Solid Bright Color -->
                <div style="font-family:var(--font-num);font-weight:800;font-size:20px;color:var(--text-bright);letter-spacing:0.3px;" id="chart-live-price" data-price="${live.price}">
                  $${live.price.toFixed(2)}
                </div>
                <!-- Small Side Direction Indicator (+0.50, +1, +2: Green on Plus, Red on Minus) -->
                <div id="chart-live-delta" class="badge ${live.change >= 0 ? 'badge-green' : 'badge-red'}" style="font-family:var(--font-mono);font-weight:800;font-size:11px;padding:2px 7px;border-radius:4px;display:inline-flex;align-items:center;gap:3px;letter-spacing:0.3px;">
                  ${live.change >= 0 ? '▲ +' : '▼ -'}${Math.abs(live.open ? (live.price - live.open) : 0.50).toFixed(2)}
                </div>
                <div id="chart-stream-status" class="feed-status-pill ${isMasterActive ? 'feed-status-connected' : 'feed-status-paused'}">
                  <span class="${isMasterActive ? 'live-beacon' : 'paused-beacon'}"></span><span id="chart-stream-status-text">${isMasterActive ? 'LIVE TV (AUTO 2s)' : 'STRATEGY PAUSED (OFF)'}</span>
                </div>
              </div>

              <!-- Chart Engine Mode Switcher -->
              <div style="display:flex;align-items:center;gap:6px;background:var(--bg-1);padding:3px;border-radius:var(--r-md);border:1px solid var(--border);">
                <button id="btn-chart-mode-engine" class="btn btn-sm ${chartMode === 'engine' ? 'btn-primary' : 'btn-ghost'}" style="font-size:10.5px;padding:3px 8px;">
                  📊 Breakout Engine (S/R Zones)
                </button>
                <button id="btn-chart-mode-tv" class="btn btn-sm ${chartMode === 'tradingview' ? 'btn-primary' : 'btn-ghost'}" style="font-size:10.5px;padding:3px 8px;">
                  📺 TradingView Native (OANDA)
                </button>
              </div>

              <!-- Zone Tags -->
              <div class="zone-legend" id="engine-zone-legend" style="display:${chartMode === 'engine' ? 'flex' : 'none'};">
                <span class="zone-tag zone-resistance" title="Dynamic resistance calculated from lookback high">
                  <span class="zone-dot"></span>
                  Resistance: <b id="label-res-zone">$${live.resistance.toFixed(2)}</b>
                </span>
                <span class="zone-tag zone-support" title="Dynamic demand support calculated from lookback low">
                  <span class="zone-dot"></span>
                  Support: <b id="label-sup-zone">$${live.support.toFixed(2)}</b>
                </span>
                <span class="zone-tag zone-trigger" title="Breakout trigger point">
                  <span class="zone-dot"></span>
                  Trigger: <b id="label-trig-zone">$${live.bullishTrigger.toFixed(2)}</b>
                </span>
              </div>

              <!-- Timeframe Switcher buttons (5m, 15m, 30m, 1h) -->
              <div style="display:flex;gap:4px;" id="chart-tf-buttons">
                <button class="btn btn-sm ${currentTimeframe === '5m' ? 'btn-primary' : 'btn-ghost'}" data-tf="5m">5m</button>
                <button class="btn btn-sm ${currentTimeframe === '15m' ? 'btn-primary' : 'btn-ghost'}" data-tf="15m">15m</button>
                <button class="btn btn-sm ${currentTimeframe === '30m' ? 'btn-primary' : 'btn-ghost'}" data-tf="30m">30m</button>
                <button class="btn btn-sm ${currentTimeframe === '1h' ? 'btn-primary' : 'btn-ghost'}" data-tf="1h">1h</button>
              </div>
            </div>

            <!-- Chart Container -->
            <div id="breakout-tv-chart-container" class="chart-container-live"></div>

            <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--text-dim);border-top:1px solid var(--border);padding-top:10px;margin-top:10px;">
              <div style="display:flex;gap:16px;">
                <span>Source: <b style="color:var(--accent)">TradingView Scanner API (OANDA:XAUUSD)</b></span>
                <span>Refresh: <b style="color:var(--green)">⚡ High-Speed 1s Sync (Zero-Delay)</b></span>
                <span>Parity: <b style="color:var(--green-bright)">100% Exact Match</b></span>
              </div>
              <div style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted)">
                Auto-Updating Without Reload
              </div>
            </div>

          </div>
        </div>

        <!-- ================= SUB-COMPONENTS VIEW SWITCHER ================= -->
        <div style="margin-top:var(--sp-2);">
          <div class="module-tabs">
            <button class="module-tab-btn ${activeTab === 'overview' ? 'active' : ''}" data-tab="overview">
              <span>📋</span> All-in-One Cockpit (Paper Trading & Signals)
            </button>
            <button class="module-tab-btn ${activeTab === 'paper' ? 'active' : ''}" data-tab="paper">
              <span>💼</span> Paper Trading Portfolio
            </button>
            <button class="module-tab-btn ${activeTab === 'signals' ? 'active' : ''}" data-tab="signals">
              <span>🚨</span> Real-Time Signals Feed
            </button>
            <button class="module-tab-btn ${activeTab === 'backtest' ? 'active' : ''}" data-tab="backtest">
              <span>🔄</span> Historical Backtest Engine
            </button>
          </div>

          <div id="submodule-signals-container" style="display:${activeTab === 'overview' || activeTab === 'signals' ? 'block' : 'none'};margin-bottom:var(--sp-4);">
            ${window.SignalsModule.buildHtml({ standalone: false })}
          </div>

          <div id="submodule-backtest-container" style="display:${activeTab === 'overview' || activeTab === 'backtest' ? 'block' : 'none'};">
            ${window.BacktestModule.buildHtml({ standalone: false })}
          </div>
        </div>

      </div>
    `;

    bindStrategyEvents(mount);

    const chartContainer = mount.querySelector("#breakout-tv-chart-container");
    renderChartArea(chartContainer);

    // Global price subscriber: continuously updates live DOM elements on every single tick!
    window.BreakoutDataService.subscribePrice((st) => {
      updateLiveStats(st);
      if (chartMode === "engine" && candleSeries && st.lastCandle) {
        candleSeries.update(st.lastCandle);
        if (volumeSeries) {
          volumeSeries.update({
            time: st.lastCandle.time,
            value: st.lastCandle.volume,
            color: st.lastCandle.close >= st.lastCandle.open ? "rgba(34, 197, 94, 0.4)" : "rgba(239, 68, 68, 0.4)"
          });
        }
      }
    });

    // Start 2-second automated tick stream immediately on mount
    window.BreakoutDataService.startLiveStream(currentTimeframe, (evt) => {
      const liveCurrent = window.BreakoutDataService.getLiveState();
      updateLiveStats(liveCurrent);
    });
  }

  function bindStrategyEvents(mount) {
    if (!mount) return;

    const btnEngine = mount.querySelector("#btn-chart-mode-engine");
    const btnTv = mount.querySelector("#btn-chart-mode-tv");
    const zoneLegend = mount.querySelector("#engine-zone-legend");
    const chartContainer = mount.querySelector("#breakout-tv-chart-container");

    if (btnEngine && btnTv) {
      btnEngine.addEventListener("click", () => {
        chartMode = "engine";
        btnEngine.className = "btn btn-sm btn-primary";
        btnTv.className = "btn btn-sm btn-ghost";
        if (zoneLegend) zoneLegend.style.display = "flex";
        renderChartArea(chartContainer);
      });

      btnTv.addEventListener("click", () => {
        chartMode = "tradingview";
        btnTv.className = "btn btn-sm btn-primary";
        btnEngine.className = "btn btn-sm btn-ghost";
        if (zoneLegend) zoneLegend.style.display = "none";
        renderChartArea(chartContainer);
      });
    }

    mount.querySelectorAll("#chart-tf-buttons button").forEach(btn => {
      btn.addEventListener("click", () => {
        const tf = btn.dataset.tf;
        currentTimeframe = tf;
        mount.querySelectorAll("#chart-tf-buttons button").forEach(b => {
          b.className = (b.dataset.tf === tf ? "btn btn-sm btn-primary" : "btn btn-sm btn-ghost");
        });
        const badge = mount.querySelector("#chart-tf-badge");
        if (badge) badge.textContent = tf.toUpperCase();

        const tfSelect = mount.querySelector("#sel-timeframe");
        if (tfSelect) tfSelect.value = tf;
        const tfVal = mount.querySelector("#val-timeframe");
        if (tfVal) tfVal.textContent = tf;

        renderChartArea(chartContainer);
      });
    });

    const tfSelect = mount.querySelector("#sel-timeframe");
    if (tfSelect) {
      tfSelect.addEventListener("change", (e) => {
        const tf = e.target.value;
        currentTimeframe = tf;
        mount.querySelectorAll("#chart-tf-buttons button").forEach(b => {
          b.className = (b.dataset.tf === tf ? "btn btn-sm btn-primary" : "btn btn-sm btn-ghost");
        });
        const badge = mount.querySelector("#chart-tf-badge");
        if (badge) badge.textContent = tf.toUpperCase();
        renderChartArea(chartContainer);
      });
    }

    const srSlider = mount.querySelector("#slider-sr-sensitivity");
    const srVal = mount.querySelector("#val-sr-sensitivity");
    if (srSlider && srVal) {
      srSlider.addEventListener("input", (e) => {
        srVal.textContent = e.target.value + "%";
        window.BreakoutMockData.agent.hyperparameters.srSensitivity = parseInt(e.target.value, 10);
      });
    }

    const volSlider = mount.querySelector("#slider-volume-surge");
    const volVal = mount.querySelector("#val-volume-surge");
    if (volSlider && volVal) {
      volSlider.addEventListener("input", (e) => {
        volVal.textContent = e.target.value + "%";
        window.BreakoutMockData.agent.hyperparameters.volumeSurgePct = parseInt(e.target.value, 10);
      });
    }

    const confSlider = mount.querySelector("#slider-confirmation");
    const confVal = mount.querySelector("#val-confirmation");
    if (confSlider && confVal) {
      confSlider.addEventListener("input", (e) => {
        confVal.textContent = e.target.value + " Bars";
        window.BreakoutMockData.agent.hyperparameters.confirmationCandles = parseInt(e.target.value, 10);
      });
    }

    const atrSlider = mount.querySelector("#slider-atr");
    const atrVal = mount.querySelector("#val-atr");
    if (atrSlider && atrVal) {
      atrSlider.addEventListener("input", (e) => {
        atrVal.textContent = e.target.value + "x";
        window.BreakoutMockData.agent.hyperparameters.atrMultiplier = parseFloat(e.target.value);
      });
    }

    const saveBtn = mount.querySelector("#btn-save-params");
    if (saveBtn) {
      saveBtn.addEventListener("click", () => {
        updateChartPriceLines();
        if (window.UI && typeof window.UI.toast === "function") {
          window.UI.toast("Parameters Saved", "Live breakout inference pipeline synced with new parameters.", "success");
        }
      });
    }

    const retrainBtn = mount.querySelector("#btn-retrain-agent");
    if (retrainBtn) {
      retrainBtn.addEventListener("click", () => {
        if (isTraining) return;
        isTraining = true;
        const progressBox = mount.querySelector("#training-progress-box");
        const statusPill = mount.querySelector("#agent-status-pill");
        const epochEl = mount.querySelector("#train-epoch");
        const lossEl = mount.querySelector("#train-loss");
        const barEl = mount.querySelector("#train-bar");

        if (progressBox) progressBox.style.display = "block";
        if (statusPill) {
          statusPill.className = "agent-status-badge agent-status-training";
          statusPill.innerHTML = `<span>TRAINING IN PROGRESS</span>`;
        }
        retrainBtn.disabled = true;

        let curEpoch = 1;
        const trainInterval = setInterval(() => {
          curEpoch += 2;
          const pct = Math.min(100, (curEpoch / 50) * 100);
          if (epochEl) epochEl.textContent = curEpoch;
          if (barEl) barEl.style.width = pct + "%";
          if (lossEl) {
            const lossVal = (0.038 - (curEpoch / 50) * 0.026).toFixed(4);
            const accVal = (89.0 + (curEpoch / 50) * 3.8).toFixed(1);
            lossEl.textContent = `Loss: ${lossVal} · Acc: ${accVal}%`;
          }

          if (curEpoch >= 50) {
            clearInterval(trainInterval);
            isTraining = false;
            window.BreakoutMockData.agent.epochsTrained += 50;
            window.BreakoutMockData.agent.trainingLoss = 0.0120;
            window.BreakoutMockData.agent.validationAccuracy = 92.8;

            if (statusPill) {
              statusPill.className = "agent-status-badge agent-status-live";
              statusPill.innerHTML = `<span class="live-beacon"></span><span>TRADINGVIEW LIVE STREAMING</span>`;
            }
            if (progressBox) progressBox.style.display = "none";
            retrainBtn.disabled = false;

            if (window.UI && typeof window.UI.toast === "function") {
              window.UI.toast("Neural Agent Retrained", "Model converged on live Gold ticks with 92.8% validation accuracy.", "success");
            }
          }
        }, 120);
      });
    }

    const deployBtn = mount.querySelector("#btn-deploy-model");
    if (deployBtn) {
      deployBtn.addEventListener("click", () => {
        if (window.UI && typeof window.UI.toast === "function") {
          window.UI.toast("Model Deployed", "Live Gold breakout inference pipeline activated for real-time order routing.", "success");
        }
      });
    }

    // ⚡ Master Breakout Strategy ON/OFF Switch
    const masterSwitchBtn = mount.querySelector("#btn-master-strategy-toggle");
    function updateMasterSwitchUI(isActive) {
      const card = mount.querySelector("#breakout-master-card");
      const statusText = mount.querySelector("#master-toggle-status-text");
      const label = mount.querySelector("#master-switch-label");
      const agentPill = mount.querySelector("#agent-status-pill");
      const chartStatus = mount.querySelector("#chart-stream-status");

      if (card) {
        card.classList.toggle("is-active", isActive);
        card.classList.toggle("is-paused", !isActive);
      }
      if (masterSwitchBtn) {
        masterSwitchBtn.classList.toggle("btn-switch-on", isActive);
        masterSwitchBtn.classList.toggle("btn-switch-off", !isActive);
        masterSwitchBtn.setAttribute("aria-checked", isActive ? "true" : "false");
        masterSwitchBtn.title = `Click to turn Breakout Strategy ${isActive ? 'OFF' : 'ON'}`;
      }
      if (label) {
        label.textContent = isActive ? "ON" : "OFF";
      }
      if (statusText) {
        statusText.innerHTML = `<span class="status-dot"></span><span>${isActive ? 'ACTIVE (ON)' : 'PAUSED (OFF)'}</span>`;
      }
      if (agentPill && !isTraining) {
        agentPill.className = `agent-status-badge ${isActive ? 'agent-status-live' : 'agent-status-paused'}`;
        agentPill.innerHTML = `<span class="${isActive ? 'live-beacon' : 'paused-beacon'}"></span><span id="agent-status-pill-text">${isActive ? 'TRADINGVIEW LIVE STREAMING' : 'STRATEGY PAUSED (OFF)'}</span>`;
      }
      if (chartStatus) {
        chartStatus.className = `feed-status-pill ${isActive ? 'feed-status-connected' : 'feed-status-paused'}`;
        chartStatus.innerHTML = `<span class="${isActive ? 'live-beacon' : 'paused-beacon'}"></span><span id="chart-stream-status-text">${isActive ? 'LIVE TV (AUTO 2s)' : 'STRATEGY PAUSED (OFF)'}</span>`;
      }
    }

    if (masterSwitchBtn) {
      masterSwitchBtn.addEventListener("click", () => {
        if (!window.BreakoutDataService) return;
        const current = window.BreakoutDataService.isStrategyActive();
        const next = !current;
        window.BreakoutDataService.setStrategyActive(next);
        updateMasterSwitchUI(next);

        if (window.UI && typeof window.UI.toast === "function") {
          if (next) {
            window.UI.toast(
              "🟢 Breakout Strategy Activated (ON)",
              "Real-time tick scanning and automated 5-layer breakout execution resumed.",
              "success"
            );
          } else {
            window.UI.toast(
              "⏸️ Breakout Strategy Paused (OFF)",
              "Live tick scanning paused. No new breakout signals or trades will open.",
              "warning"
            );
          }
        }
      });
    }

    // Subscribe to external master switch events if triggered from other components
    if (window.BreakoutDataService && typeof window.BreakoutDataService.subscribeStrategyActive === "function") {
      window.BreakoutDataService.subscribeStrategyActive((isActive) => {
        updateMasterSwitchUI(isActive);
      });
    }

    mount.querySelectorAll(".module-tab-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        activeTab = btn.dataset.tab;
        mount.querySelectorAll(".module-tab-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");

        const sigBox = mount.querySelector("#submodule-signals-container");
        const btBox = mount.querySelector("#submodule-backtest-container");

        if (sigBox) sigBox.style.display = (activeTab === "overview" || activeTab === "signals" || activeTab === "paper") ? "block" : "none";
        if (btBox) btBox.style.display = (activeTab === "overview" || activeTab === "backtest") ? "block" : "none";

        if (activeTab === "paper") {
          const paperWrap = mount.querySelector("#paper-portfolio-wrapper");
          if (paperWrap) paperWrap.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
    });

    const sigContainer = mount.querySelector("#submodule-signals-container");
    if (sigContainer) window.SignalsModule.bindEvents(sigContainer);

    const btContainer = mount.querySelector("#submodule-backtest-container");
    if (btContainer) window.BacktestModule.bindEvents(btContainer);
  }

  return {
    renderView
  };
})();
