/* =====================================================================
   Backtest Module — Breakout Strategy Agent (Real Historical XAU/USD)
   Authentic historical OHLCV simulation, metrics, and equity curve
   ===================================================================== */
"use strict";

window.BacktestModule = (function () {
  let isRunning = false;
  let activeAsset = "XAU/USD";
  let activeRange = "90D"; // '30D' | '90D' | '1Y'
  let activeTimeframe = "15m"; // '5m' | '15m' | '30m' | '1h'
  let hasRunInitialBacktest = false;

  function renderEquityCurveSvg(points) {
    if (!points || points.length === 0) {
      return `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:12px;">No historical equity data yet. Click Run Backtest.</div>`;
    }

    const width = 800;
    const height = 220;
    const padding = { top: 25, right: 30, bottom: 35, left: 70 };

    const minEq = Math.min(...points.map(p => p.equity)) * 0.98;
    const maxEq = Math.max(...points.map(p => p.equity)) * 1.02;

    const scaleX = (i) => padding.left + (i / Math.max(1, points.length - 1)) * (width - padding.left - padding.right);
    const scaleY = (val) => padding.top + (1 - (val - minEq) / Math.max(1, maxEq - minEq)) * (height - padding.top - padding.bottom);

    let pathD = "";
    let areaD = "";
    points.forEach((p, idx) => {
      const x = scaleX(idx);
      const y = scaleY(p.equity);
      if (idx === 0) {
        pathD += `M ${x} ${y}`;
        areaD += `M ${x} ${y}`;
      } else {
        pathD += ` L ${x} ${y}`;
        areaD += ` L ${x} ${y}`;
      }
    });

    const bottomY = height - padding.bottom;
    areaD += ` L ${scaleX(points.length - 1)} ${bottomY} L ${scaleX(0)} ${bottomY} Z`;

    const gridTicks = [minEq, minEq + (maxEq - minEq) * 0.33, minEq + (maxEq - minEq) * 0.66, maxEq];
    const gridLines = gridTicks.map(val => {
      const y = scaleY(val);
      return `
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="var(--border)" stroke-dasharray="3,3" stroke-width="1" />
        <text x="${padding.left - 8}" y="${y + 4}" fill="var(--text-muted)" font-size="10" text-anchor="end" font-family="var(--font-num)">$${Math.round(val).toLocaleString()}</text>
      `;
    }).join("");

    const step = Math.max(1, Math.floor(points.length / 4));
    const dateTicks = points.filter((_, idx) => idx % step === 0 || idx === points.length - 1).map((p, _, arr) => {
      const origIdx = points.indexOf(p);
      const x = scaleX(origIdx);
      return `<text x="${x}" y="${height - 12}" fill="var(--text-muted)" font-size="10" text-anchor="middle" font-family="var(--font-mono)">${p.date}</text>`;
    }).join("");

    return `
      <svg viewBox="0 0 ${width} ${height}" style="width:100%;height:100%;display:block;overflow:visible;">
        <defs>
          <linearGradient id="eqGradientReal" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#4d9fff" stop-opacity="0.35"/>
            <stop offset="100%" stop-color="#4d9fff" stop-opacity="0.0"/>
          </linearGradient>
        </defs>
        ${gridLines}
        ${dateTicks}
        <path d="${areaD}" fill="url(#eqGradientReal)" />
        <path d="${pathD}" fill="none" stroke="#4d9fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
        ${points.map((p, idx) => {
          const x = scaleX(idx);
          const y = scaleY(p.equity);
          return `<circle cx="${x}" cy="${y}" r="3" fill="#141b26" stroke="#4d9fff" stroke-width="2" class="equity-dot"><title>${p.date}: $${p.equity.toLocaleString()}</title></circle>`;
        }).join("")}
      </svg>
    `;
  }

  function renderMetricsHtml(m) {
    const pnlPrefix = m.totalPnL >= 0 ? "+$" : "-$";
    const pnlColor = m.totalPnL >= 0 ? "var(--green-bright)" : "var(--red-bright)";
    const pnlVal = `${pnlPrefix}${Math.abs(m.totalPnL).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const pctPrefix = m.totalPnLPct >= 0 ? "+" : "";

    return `
      <div class="backtest-metric-card accent-green">
        <div class="metric-label">Win Rate</div>
        <div class="metric-value lg" style="color:var(--green-bright);">${m.winRate}%</div>
        <div class="metric-sub">${m.winningTrades} Wins / ${m.losingTrades} Losses</div>
      </div>
      <div class="backtest-metric-card accent-blue">
        <div class="metric-label">Total Net PnL</div>
        <div class="metric-value lg" style="color:${pnlColor};">${pnlVal}</div>
        <div class="metric-sub" style="color:${pnlColor}">${pctPrefix}${m.totalPnLPct}% on Capital</div>
      </div>
      <div class="backtest-metric-card accent-amber">
        <div class="metric-label">Max Drawdown</div>
        <div class="metric-value lg" style="color:var(--amber);">${m.maxDrawdown}%</div>
        <div class="metric-sub">Peak-to-Trough Variance</div>
      </div>
      <div class="backtest-metric-card accent-violet">
        <div class="metric-label">Profit Factor</div>
        <div class="metric-value lg" style="color:var(--violet);">${m.profitFactor}</div>
        <div class="metric-sub">Sharpe Ratio: ${m.sharpeRatio}</div>
      </div>
    `;
  }

  function buildHtml(options = {}) {
    const isStandalone = options.standalone === true;
    const bt = window.BreakoutMockData.backtest || {};
    const m = bt.metrics || { winRate: 0, totalPnL: 0, totalPnLPct: 0, maxDrawdown: 0, profitFactor: 0, sharpeRatio: 0, totalTrades: 0, winningTrades: 0, losingTrades: 0 };

    return `
      <div class="card breakout-backtest-module" style="box-shadow:var(--shadow);">
        <div class="card-head" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
          <div style="display:flex;align-items:center;gap:10px;">
            <span style="font-size:16px;">🔄</span>
            <div style="font-weight:700;font-size:14px;letter-spacing:0.5px;color:var(--text-bright);">
              ${isStandalone ? "XAU/USD HISTORICAL BACKTEST LAB" : "HISTORICAL BACKTEST SIMULATION (REAL MARKET DATA)"}
            </div>
            <span class="badge badge-green" style="font-size:10px;padding:2px 8px;">PUBLIC OHLCV FEED</span>
          </div>

          <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-dim);" id="bt-head-stats">
            <span>Sample: <b style="color:var(--text-bright)" id="bt-trades-count-label">${m.totalTrades} Real Trades</b></span>
            <span>·</span>
            <span>Dataset: <b style="color:var(--accent)" id="bt-dataset-label">Real XAU/USD (${activeRange})</b></span>
          </div>
        </div>

        <div class="card-body" style="padding:var(--sp-4);">
          <!-- Config Controls Row -->
          <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));gap:12px;margin-bottom:var(--sp-4);background:rgba(255,255,255,0.02);padding:14px;border-radius:var(--r-md);border:1px solid var(--border);">
            <div>
              <label style="display:block;font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Trading Pair / Asset</label>
              <select id="bt-asset-select" class="btn btn-sm btn-ghost" style="width:100%;padding:6px 10px;background:var(--bg-1);border-radius:var(--r-sm);">
                <option value="XAU/USD" selected>XAU/USD (Gold Spot)</option>
              </select>
            </div>

            <div>
              <label style="display:block;font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Timeframe</label>
              <select id="bt-timeframe-select" class="btn btn-sm btn-ghost" style="width:100%;padding:6px 10px;background:var(--bg-1);border-radius:var(--r-sm);">
                <option value="5m" ${activeTimeframe === "5m" ? "selected" : ""}>5 Minutes (Scalp)</option>
                <option value="15m" ${activeTimeframe === "15m" ? "selected" : ""}>15 Minutes (Fast Breakout)</option>
                <option value="30m" ${activeTimeframe === "30m" ? "selected" : ""}>30 Minutes (Momentum)</option>
                <option value="1h" ${activeTimeframe === "1h" ? "selected" : ""}>1 Hour (Intraday Trend)</option>
              </select>
            </div>

            <div>
              <label style="display:block;font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Historical Range</label>
              <select id="bt-range-select" class="btn btn-sm btn-ghost" style="width:100%;padding:6px 10px;background:var(--bg-1);border-radius:var(--r-sm);">
                <option value="30D" ${activeRange === "30D" ? "selected" : ""}>Past 30 Days</option>
                <option value="90D" ${activeRange === "90D" ? "selected" : ""}>Past 90 Days</option>
                <option value="1Y" ${activeRange === "1Y" ? "selected" : ""}>Past 1 Year</option>
              </select>
            </div>

            <div>
              <label style="display:block;font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;margin-bottom:6px;">Initial Portfolio Capital ($)</label>
              <input type="number" id="bt-capital" value="${bt.initialCapital || 100000}" step="1000"
                style="width:100%;padding:5px 8px;font-size:11px;background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r-sm);color:var(--text);" />
            </div>

            <div style="display:flex;align-items:flex-end;">
              <button id="bt-run-btn" class="btn btn-primary" style="width:100%;height:32px;font-weight:700;font-size:12px;gap:6px;display:flex;align-items:center;justify-content:center;">
                <span id="bt-btn-spinner" style="display:none;">⏳</span>
                <span id="bt-btn-text">⚡ Run Real Backtest</span>
              </button>
            </div>
          </div>

          <!-- Key Metrics Grid -->
          <div class="grid grid-4" id="bt-metrics-container" style="margin-bottom:var(--sp-4);">
            ${renderMetricsHtml(m)}
          </div>

          <!-- Equity Curve Section -->
          <div style="background:var(--bg-1);border:1px solid var(--border);border-radius:var(--r-md);padding:16px;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-weight:700;font-size:13px;color:var(--text-bright);">Authentic Equity Growth Curve</span>
                <span class="badge badge-dim" style="font-size:10px;">Simulated against real historical market data</span>
              </div>
              <div style="display:flex;align-items:center;gap:12px;font-size:11px;">
                <span style="display:inline-flex;align-items:center;gap:4px;">
                  <span style="width:10px;height:2px;background:#4d9fff;display:inline-block;"></span>
                  <span style="color:var(--text-dim)">Portfolio Balance</span>
                </span>
                <span style="font-family:var(--font-num);font-weight:700;color:var(--text-bright);" id="bt-final-cap-label">
                  End: $${Number(bt.finalCapital || 100000).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                </span>
              </div>
            </div>

            <div class="equity-chart-box" id="equity-chart-container">
              ${renderEquityCurveSvg(bt.equityCurve)}
            </div>
          </div>

          <!-- Trade Log Snapshot -->
          <div style="margin-top:var(--sp-4);">
            <div style="font-weight:700;font-size:12px;color:var(--text-muted);text-transform:uppercase;margin-bottom:8px;letter-spacing:0.5px;">
              Recent Backtested Trade Executions (Actual Gold Prices)
            </div>
            <div class="table-wrap">
              <table class="table" style="width:100%;font-size:11px;">
                <thead>
                  <tr style="background:rgba(255,255,255,0.02);border-bottom:1px solid var(--border);">
                    <th style="padding:8px 12px;color:var(--text-muted);">Trade ID</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">Date</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">Pair</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">Type</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">Entry</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">Exit</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">PnL</th>
                    <th style="padding:8px 12px;color:var(--text-muted);">Outcome</th>
                  </tr>
                </thead>
                <tbody id="bt-trades-tbody">
                  ${(bt.recentTrades && bt.recentTrades.length > 0) ? bt.recentTrades.map(tr => `
                    <tr>
                      <td style="font-family:var(--font-mono);color:var(--text-dim);">${tr.id}</td>
                      <td>${tr.date}</td>
                      <td style="font-weight:700;">${tr.asset}</td>
                      <td><span class="badge ${tr.type === 'BUY' ? 'badge-green' : 'badge-red'}">${tr.type}</span></td>
                      <td style="font-family:var(--font-num);">$${tr.entry}</td>
                      <td style="font-family:var(--font-num);">$${tr.exit}</td>
                      <td style="font-family:var(--font-num);font-weight:700;color:${tr.outcome === 'WIN' ? 'var(--green-bright)' : 'var(--red-bright)'};">${tr.pnl}</td>
                      <td><span class="badge ${tr.outcome === 'WIN' ? 'badge-green' : 'badge-red'}">${tr.outcome}</span></td>
                    </tr>
                  `).join("") : `
                    <tr><td colspan="8" style="text-align:center;padding:24px;color:var(--text-muted)">Click "Run Real Backtest" above to simulate historical trades.</td></tr>
                  `}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function bindEvents(container) {
    if (!container) return;

    if (!hasRunInitialBacktest) {
      executeRealBacktest(container);
    }

    const tfSel = container.querySelector("#bt-timeframe-select");
    if (tfSel) {
      tfSel.addEventListener("change", (e) => {
        activeTimeframe = e.target.value;
      });
    }

    const rangeSel = container.querySelector("#bt-range-select");
    if (rangeSel) {
      rangeSel.addEventListener("change", (e) => {
        activeRange = e.target.value;
      });
    }

    const runBtn = container.querySelector("#bt-run-btn");
    if (runBtn) {
      runBtn.addEventListener("click", () => {
        executeRealBacktest(container);
      });
    }
  }

  async function executeRealBacktest(container) {
    if (isRunning || !window.BreakoutDataService) return;
    isRunning = true;

    const runBtn = container.querySelector("#bt-run-btn");
    const spinner = container.querySelector("#bt-btn-spinner");
    const btnText = container.querySelector("#bt-btn-text");
    const capitalInput = container.querySelector("#bt-capital");

    if (spinner) spinner.style.display = "inline-block";
    if (btnText) btnText.textContent = "Fetching Real OHLCV Data...";
    if (runBtn) runBtn.disabled = true;

    try {
      const capital = capitalInput ? parseFloat(capitalInput.value) || 100000 : 100000;
      const hp = window.BreakoutMockData.agent.hyperparameters || {};

      if (btnText) btnText.textContent = "Simulating Real Breakouts...";

      const results = await window.BreakoutDataService.runHistoricalBacktest({
        asset: activeAsset,
        timeframe: activeTimeframe,
        range: activeRange,
        capital: capital,
        srSensitivity: hp.srSensitivity,
        volumeSurgePct: hp.volumeSurgePct,
        atrMultiplier: hp.atrMultiplier,
        riskRewardRatio: hp.riskRewardRatio
      });

      hasRunInitialBacktest = true;

      // Update UI elements
      const m = results.metrics;
      const metricsContainer = container.querySelector("#bt-metrics-container");
      if (metricsContainer) {
        metricsContainer.innerHTML = renderMetricsHtml(m);
      }

      const countLabel = container.querySelector("#bt-trades-count-label");
      if (countLabel) countLabel.textContent = `${m.totalTrades} Real Trades`;

      const datasetLabel = container.querySelector("#bt-dataset-label");
      if (datasetLabel) datasetLabel.textContent = `Real XAU/USD (${activeTimeframe.toUpperCase()} - ${activeRange})`;

      const chartBox = container.querySelector("#equity-chart-container");
      if (chartBox) {
        chartBox.innerHTML = renderEquityCurveSvg(results.equityCurve);
      }

      const finalCapLabel = container.querySelector("#bt-final-cap-label");
      if (finalCapLabel) {
        finalCapLabel.textContent = `End: $${results.finalCapital.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
      }

      const tbody = container.querySelector("#bt-trades-tbody");
      if (tbody) {
        if (results.recentTrades && results.recentTrades.length > 0) {
          tbody.innerHTML = results.recentTrades.map(tr => `
            <tr>
              <td style="font-family:var(--font-mono);color:var(--text-dim);">${tr.id}</td>
              <td>${tr.date}</td>
              <td style="font-weight:700;">${tr.asset}</td>
              <td><span class="badge ${tr.type === 'BUY' ? 'badge-green' : 'badge-red'}">${tr.type}</span></td>
              <td style="font-family:var(--font-num);">$${tr.entry}</td>
              <td style="font-family:var(--font-num);">$${tr.exit}</td>
              <td style="font-family:var(--font-num);font-weight:700;color:${tr.outcome === 'WIN' ? 'var(--green-bright)' : 'var(--red-bright)'};">${tr.pnl}</td>
              <td><span class="badge ${tr.outcome === 'WIN' ? 'badge-green' : 'badge-red'}">${tr.outcome}</span></td>
            </tr>
          `).join("");
        } else {
          tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--text-muted)">No breakout trades triggered in this window. Adjust range or sensitivity.</td></tr>`;
        }
      }

      if (window.UI && typeof window.UI.toast === "function") {
        window.UI.toast("Backtest Complete", `Processed ${results.metrics.totalTrades} authentic XAU/USD setups. Win rate: ${results.metrics.winRate}%`, "success");
      }
    } catch (err) {
      console.error("Backtest failed:", err);
      if (window.UI && typeof window.UI.toast === "function") {
        window.UI.toast("Backtest Error", err.message || "Failed to execute backtest", "red");
      }
    } finally {
      isRunning = false;
      if (spinner) spinner.style.display = "none";
      if (btnText) btnText.textContent = "⚡ Run Real Backtest";
      if (runBtn) runBtn.disabled = false;
    }
  }

  function renderStandalone(mount) {
    if (!mount) return;
    mount.innerHTML = `
      <div class="breakout-container">
        <div class="agent-banner">
          <div class="agent-meta-row">
            <div>
              <div style="font-size:11px;font-weight:700;color:var(--accent);letter-spacing:1px;text-transform:uppercase;">
                HISTORICAL SIMULATION & RISK ENGINE
              </div>
              <h1 style="font-size:22px;margin:4px 0 2px;color:var(--text-bright);font-weight:800;">
                XAU/USD Historical Backtest Lab
              </h1>
              <div style="font-size:12px;color:var(--text-dim);">
                Validate breakout strategies on authentic historical Gold (XAU/USD) candlestick data
              </div>
            </div>

            <div style="display:flex;align-items:center;gap:10px;">
              <a href="#/breakout-strategy" class="btn btn-ghost" style="font-size:12px;gap:6px;display:flex;align-items:center;">
                📈 Breakout Strategy Main
              </a>
            </div>
          </div>
        </div>

        <div id="standalone-backtest-wrapper">
          ${buildHtml({ standalone: true })}
        </div>
      </div>
    `;

    bindEvents(mount.querySelector("#standalone-backtest-wrapper"));
  }

  return {
    buildHtml,
    bindEvents,
    renderStandalone
  };
})();
