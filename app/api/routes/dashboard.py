"""
Developer Dashboard UI Route.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["Dashboard"])

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>XAU/USD Multi-Timeframe Trading AI Agent</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #0b0e14; color: #d1d5db; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
        .card { background-color: #151a23; border: 1px solid #232b3b; border-radius: 10px; margin-bottom: 20px; }
        .card-header { background-color: #1a2230; border-bottom: 1px solid #232b3b; font-weight: 600; }
        .badge-bullish { background-color: #059669; color: white; }
        .badge-bearish { background-color: #dc2626; color: white; }
        .badge-neutral { background-color: #4b5563; color: white; }
        .price-highlight { font-size: 2.2rem; font-weight: 700; color: #fbbf24; }
        .score-box { font-size: 2rem; font-weight: 700; color: #38bdf8; }
        .table-dark-custom { background-color: #151a23; color: #d1d5db; }
        .table-dark-custom th { color: #9ca3af; border-color: #232b3b; }
        .table-dark-custom td { border-color: #232b3b; }
        .btn-gold { background-color: #d97706; color: white; font-weight: 600; }
        .btn-gold:hover { background-color: #b45309; color: white; }
    </style>
</head>
<body class="p-3 p-md-4">
    <div class="container-fluid">
        <!-- Header -->
        <div class="d-flex justify-content-between align-items-center mb-4 pb-3 border-bottom border-secondary">
            <div>
                <h2 class="mb-0 text-white">✨ XAU/USD Multi-Timeframe Trading AI Agent</h2>
                <small class="text-secondary">Institutional Quantitative Engine & AI Sanity Validator</small>
            </div>
            <div>
                <span id="obs-mode-badge" class="badge bg-info me-2" style="display:none;">👁️ OBSERVATION MODE</span>
                <span id="paper-status-badge" class="badge bg-secondary me-2" style="display:none;">PAPER: --</span>
                <button class="btn btn-gold me-2" onclick="refreshAnalysis()">⚡ Run Analysis Now</button>
                <button class="btn btn-outline-info" onclick="runBacktest()">📊 Run Backtest</button>
            </div>
        </div>

        <!-- Live Price & Bias Row -->
        <div class="row">
            <div class="col-md-3">
                <div class="card text-center p-3">
                    <small class="text-secondary">LIVE XAU/USD SPOT PRICE</small>
                    <span id="live-mode-badge" class="badge bg-success mx-auto mt-1" style="width:fit-content;">LIVE</span>
                    <div id="live-price" class="price-highlight">--</div>
                    <div class="small text-secondary">
                        <div>Bid: <span id="live-bid">--</span> &nbsp; Ask: <span id="live-ask">--</span></div>
                        <div>Feed: <span id="live-provider">--</span></div>
                        <div>Last tick: <span id="live-tick-ts">--</span></div>
                        <div>Last closed 15M: <span id="live-candle-ts">--</span></div>
                        <div>Last analysis: <span id="live-analysis-ts">--</span></div>
                    </div>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">4H MACRO BIAS</small>
                    <h4 id="bias-4h" class="mt-2"><span class="badge badge-neutral">...</span></h4>
                    <small id="bias-4h-desc" class="text-muted">Analyzing trend...</small>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">1H MARKET STRUCTURE</small>
                    <h4 id="bias-1h" class="mt-2"><span class="badge badge-neutral">...</span></h4>
                    <small id="bias-1h-desc" class="text-muted">Analyzing BOS/CHoCH...</small>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card p-3">
                    <small class="text-secondary">30M / 15M SETUP & TRIGGER</small>
                    <h4 id="bias-15m" class="mt-2"><span class="badge badge-neutral">...</span></h4>
                    <small id="bias-15m-desc" class="text-muted">Analyzing confirmation...</small>
                </div>
            </div>
        </div>

        <!-- Live Feed Disconnect Banner (hidden by default) -->
        <div id="feed-disconnect-banner" class="alert alert-danger d-none">
            <strong>LIVE FEED DISCONNECTED</strong> — showing last known live data.
            Last live tick: <span id="disconnect-last-tick">--</span>.
            No synthetic/historical fallback is used for live monitoring.
        </div>

        <!-- Central Signal & Confluence Row -->
        <div class="row">
            <!-- Signal Card -->
            <div class="col-lg-6">
                <div class="card h-100">
                    <div class="card-header d-flex justify-content-between">
                        <span>🎯 Live Quantitative Signal <span class="badge bg-success">LIVE DATA</span></span>
                        <span id="signal-badge" class="badge bg-secondary">WAITING</span>
                    </div>
                    <div class="card-body">
                        <div class="row align-items-center mb-3">
                            <div class="col-6">
                                <h1 id="signal-direction" class="mb-0 text-secondary">NO TRADE</h1>
                                <small id="signal-strategy" class="text-info"></small>
                            </div>
                            <div class="col-6 text-end">
                                <small class="text-secondary">Confluence Score</small>
                                <div id="signal-score" class="score-box">0 / 100</div>
                            </div>
                        </div>

                        <div class="row g-2 mb-3">
                            <div class="col p-2 bg-dark rounded text-center">
                                <small class="text-secondary">ENTRY</small>
                                <div id="sig-entry" class="fw-bold">--</div>
                            </div>
                            <div class="col p-2 bg-dark rounded text-center">
                                <small class="text-secondary">SL</small>
                                <div id="sig-sl" class="text-danger fw-bold">--</div>
                            </div>
                            <div class="col p-2 bg-dark rounded text-center">
                                <small class="text-secondary">TP1</small>
                                <div id="sig-tp1" class="text-success fw-bold">--</div>
                            </div>
                            <div class="col p-2 bg-dark rounded text-center">
                                <small class="text-secondary">TP2</small>
                                <div id="sig-tp2" class="text-success fw-bold">--</div>
                            </div>
                            <div class="col p-2 bg-dark rounded text-center">
                                <small class="text-secondary">TP3</small>
                                <div id="sig-tp3" class="text-success fw-bold">--</div>
                            </div>
                            <div class="col p-2 bg-dark rounded text-center">
                                <small class="text-secondary">R:R</small>
                                <div id="sig-rr" class="text-warning fw-bold">--</div>
                            </div>
                        </div>

                        <h6>Reasons & Confirmations:</h6>
                        <ul id="signal-reasons" class="text-light small"></ul>

                        <div class="p-3 mt-3 rounded" style="background-color: #1c2433;">
                            <div class="d-flex justify-content-between">
                                <span class="fw-bold text-info">🤖 AI Sanity Layer:</span>
                                <span id="ai-status" class="badge bg-secondary">Pending</span>
                            </div>
                            <p id="ai-explanation" class="small mb-0 mt-2 text-secondary"></p>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Confluence Breakdown & SMC Info -->
            <div class="col-lg-6">
                <div class="card h-100">
                    <div class="card-header">📐 Multi-Timeframe Confluence Scoring Matrix</div>
                    <div class="card-body">
                        <table class="table table-dark-custom table-sm align-middle">
                            <thead>
                                <tr>
                                    <th>Confluence Category</th>
                                    <th>Max</th>
                                    <th>Score</th>
                                    <th>Status</th>
                                </tr>
                            </thead>
                            <tbody id="confluence-table-body">
                                <tr><td colspan="4" class="text-center">Loading matrix...</td></tr>
                            </tbody>
                        </table>

                        <div class="row mt-3 g-2">
                            <div class="col-6">
                                <div class="p-2 border border-secondary rounded">
                                    <small class="text-secondary">SMC Zone & Gaps</small>
                                    <div id="smc-zone-info" class="small mt-1 text-light">--</div>
                                </div>
                            </div>
                            <div class="col-6">
                                <div class="p-2 border border-secondary rounded">
                                    <small class="text-secondary">Fibonacci Golden Zone</small>
                                    <div id="fib-zone-info" class="small mt-1 text-light">--</div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Backtest & History Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">📈 Historical Backtest Simulation <span class="badge bg-secondary">HISTORICAL / BACKTEST</span></div>
                    <div class="card-body">
                        <div id="backtest-summary" class="row text-center">
                            <div class="col-md-2 p-2"><small class="text-secondary">Total Trades</small><h4 id="bt-trades">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">Win Rate</small><h4 id="bt-winrate" class="text-success">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">Profit Factor</small><h4 id="bt-pf">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">Net Profit</small><h4 id="bt-profit" class="text-primary">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">Max Drawdown</small><h4 id="bt-dd" class="text-danger">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">Expectancy</small><h4 id="bt-exp">--</h4></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Paper Trading & Account Performance Row -->
        <div class="row mt-2">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">💰 Paper Trading Account</div>
                    <div class="card-body">
                        <div class="row text-center">
                            <div class="col-4 p-2"><small class="text-secondary">Balance</small><h4 id="pt-balance" class="text-primary">--</h4></div>
                            <div class="col-4 p-2"><small class="text-secondary">Equity</small><h4 id="pt-equity" class="text-info">--</h4></div>
                            <div class="col-4 p-2"><small class="text-secondary">Open Trades</small><h4 id="pt-open" class="text-warning">--</h4></div>
                        </div>
                        <div class="row text-center mt-2">
                            <div class="col-3 p-2"><small class="text-secondary">Win Rate</small><h5 id="pt-wr" class="text-success">--</h5></div>
                            <div class="col-3 p-2"><small class="text-secondary">Profit Factor</small><h5 id="pt-pf">--</h5></div>
                            <div class="col-3 p-2"><small class="text-secondary">Net P&L</small><h5 id="pt-pnl" class="text-primary">--</h5></div>
                            <div class="col-3 p-2"><small class="text-secondary">Trades</small><h5 id="pt-trades">--</h5></div>
                        </div>
                        <hr>
                        <h6>Active Paper Trades</h6>
                        <div id="active-trades-body" class="small">
                            <p class="text-secondary">No active positions.</p>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">📡 System Status</div>
                    <div class="card-body" id="system-status-body">
                        <p class="text-secondary">Loading...</p>
                    </div>
                    <div class="card-header mt-2">📋 Signal Explanation</div>
                    <div class="card-body">
                        <pre id="signal-explanation" class="text-light small" style="white-space: pre-wrap;">--</pre>
                    </div>
                </div>
            </div>
        </div>

        <!-- Data Quality Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">🛡️ Live Data Quality & Safety Gate</div>
                    <div class="card-body">
                        <div class="row text-center">
                            <div class="col-md-2 p-2">
                                <small class="text-secondary">LIVE FEED</small>
                                <h5 id="dq-feed" class="text-warning">--</h5>
                            </div>
                            <div class="col-md-2 p-2">
                                <small class="text-secondary">HISTORICAL DATA</small>
                                <h5 id="dq-hist" class="text-warning">--</h5>
                            </div>
                            <div class="col-md-2 p-2">
                                <small class="text-secondary">DATA QUALITY</small>
                                <h5 id="dq-health" class="text-warning">--</h5>
                            </div>
                            <div class="col-md-2 p-2">
                                <small class="text-secondary">CANDLES</small>
                                <h5 id="dq-candles" class="text-light">--</h5>
                            </div>
                            <div class="col-md-2 p-2">
                                <small class="text-secondary">GAPS</small>
                                <h5 id="dq-gaps" class="text-light">--</h5>
                            </div>
                            <div class="col-md-2 p-2">
                                <small class="text-secondary">DUPLICATES</small>
                                <h5 id="dq-dup" class="text-light">--</h5>
                            </div>
                        </div>
                        <div class="mt-2">
                            <small class="text-secondary">Latest candle: <span id="dq-latest">--</span></small>
                        </div>
                        <div class="mt-2 border-top border-secondary pt-2">
                            <div class="row text-center">
                                <div class="col-4 p-1">
                                    <small class="text-secondary">HISTORY REFRESH</small>
                                    <h6 id="dq-refresh-status" class="text-warning">--</h6>
                                </div>
                                <div class="col-4 p-1">
                                    <small class="text-secondary">LAST REFRESH</small>
                                    <h6 id="dq-refresh-last" class="text-light small">--</h6>
                                </div>
                                <div class="col-4 p-1">
                                    <small class="text-secondary">NEXT REFRESH</small>
                                    <h6 id="dq-refresh-next" class="text-light small">--</h6>
                                </div>
                            </div>
                            <div id="dq-refresh-error" class="d-none small text-danger mt-1"></div>
                        </div>
                        <div id="dq-degraded-box" class="alert alert-danger d-none mt-2">
                            <strong>🔴 DEGRADED</strong><br>
                            Reason: <span id="dq-reason">--</span>
                            <div class="mt-2"><span class="badge bg-danger">PAPER TRADING BLOCKED</span></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Strategy Validation Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">🔬 Strategy Validation (Out-of-Sample Research)</div>
                    <div class="card-body">
                        <div class="row text-center">
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">STRATEGY STATUS</small>
                                <h4 id="rv-grade" class="text-warning">--</h4>
                            </div>
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">OOS TRADES</small>
                                <h4 id="rv-trades" class="text-light">--</h4>
                            </div>
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">OOS WIN RATE</small>
                                <h4 id="rv-winrate" class="text-light">--</h4>
                            </div>
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">OOS EXPECTANCY</small>
                                <h4 id="rv-exp" class="text-light">--</h4>
                            </div>
                        </div>
                        <div class="row text-center">
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">OOS PROFIT FACTOR</small>
                                <h4 id="rv-pf" class="text-light">--</h4>
                            </div>
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">OOS MAX DD</small>
                                <h4 id="rv-dd" class="text-danger">--</h4>
                            </div>
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">MC 95% DD</small>
                                <h4 id="rv-mcdd" class="text-danger">--</h4>
                            </div>
                            <div class="col-md-3 p-2">
                                <small class="text-secondary">DATA</small>
                                <h4 id="rv-data" class="text-info small">--</h4>
                            </div>
                        </div>
                        <div id="rv-reasons" class="small text-secondary mt-2"></div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Paper Trades Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">📋 Paper Trade History</div>
                    <div class="card-body p-0">
                        <div class="table-responsive" style="max-height:300px; overflow-y:auto;">
                            <table class="table table-dark table-sm table-striped mb-0" id="paper-trades-table">
                                <thead><tr>
                                    <th>Time</th><th>Dir</th><th>Entry</th><th>SL</th><th>TP2</th><th>State</th><th>P&L</th><th>R</th><th>Exit</th>
                                </tr></thead>
                                <tbody id="paper-trades-body"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Live Observation Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">👁️ Live Observation (Hypothetical Signals — No Trades)</div>
                    <div class="card-body">
                        <div class="row text-center">
                            <div class="col-md-3 p-2"><small class="text-secondary">SIGNALS</small><h4 id="obs-signals" class="text-light">--</h4></div>
                            <div class="col-md-3 p-2"><small class="text-secondary">CLOSED</small><h4 id="obs-closed" class="text-light">--</h4></div>
                            <div class="col-md-3 p-2"><small class="text-secondary">HYPOTHETICAL WR</small><h4 id="obs-wr" class="text-light">--</h4></div>
                            <div class="col-md-3 p-2"><small class="text-secondary">HYPOTHETICAL P&L</small><h4 id="obs-pnl" class="text-primary">--</h4></div>
                        </div>
                        <div class="row text-center mt-2">
                            <div class="col-md-4 p-2"><small class="text-secondary">LONG</small><h5 id="obs-long" class="text-success">--</h5></div>
                            <div class="col-md-4 p-2"><small class="text-secondary">SHORT</small><h5 id="obs-short" class="text-danger">--</h5></div>
                            <div class="col-md-4 p-2"><small class="text-secondary">AVG MFE / MAE (R)</small><h5 id="obs-mfe" class="text-info">--</h5></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Signal Outcomes (Forward Validation) Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">🎯 Signal Outcomes — Forward Validation</div>
                    <div class="card-body">
                        <div class="row text-center">
                            <div class="col-md-2 p-2"><small class="text-secondary">TRACKED</small><h4 id="so-tracked" class="text-light">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">CLOSED</small><h4 id="so-closed" class="text-light">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">HIT RATE TP1/TP2/TP3</small><h4 id="so-tphits" class="text-success">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">SL HIT RATE</small><h4 id="so-slhit" class="text-danger">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">AVG / MEDIAN R</small><h4 id="so-avg-med-r" class="text-info">--</h4></div>
                            <div class="col-md-2 p-2"><small class="text-secondary">SAMPLE</small><h4 id="so-sample" class="text-light">--</h4></div>
                        </div>
                        <div class="row text-center mt-2">
                            <div class="col-md-3 p-2"><small class="text-secondary">SIGNALS 24H (L/S)</small><h6 id="so-today" class="text-light">--</h6></div>
                            <div class="col-md-3 p-2"><small class="text-secondary">ADMITTED / REJECTED</small><h6 id="so-admission" class="text-light">--</h6></div>
                            <div class="col-md-3 p-2"><small class="text-secondary">STRATEGY VERSION</small><h6 id="so-version" class="text-warning small">--</h6></div>
                            <div class="col-md-3 p-2"><small class="text-secondary">OBSERVATION DURATION</small><h6 id="so-duration" class="text-light">--</h6></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Research Candidates (MTF) Row -->
        <div class="row mt-2">
            <div class="col-12">
                <div class="card">
                    <div class="card-header">🧬 Research Candidates — MTF Configs <span class="badge bg-secondary">PRODUCTION UNCHANGED</span></div>
                    <div class="card-body p-0">
                        <div class="table-responsive" style="max-height:260px; overflow-y:auto;">
                            <table class="table table-dark table-sm table-striped mb-0">
                                <thead><tr>
                                    <th>Config</th><th>Regime</th><th>Conf</th><th>Selection</th><th>TP</th><th>Trades</th><th>WR</th><th>Exp</th><th>OOS+</th>
                                </tr></thead>
                                <tbody id="mtf-candidates-body">
                                    <tr><td colspan="9" class="text-secondary text-center">Loading research candidates...</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        async function refreshAnalysis() {
            // LIVE ONLY: never falls back to the historical CSV endpoint.
            let res;
            try {
                res = await fetch('/analysis/live/XAUUSD');
            } catch (err) {
                showLiveDisconnected();
                return;
            }

            if (!res.ok) {
                // Live analysis unavailable (e.g. no live data yet / feed down).
                showLiveDisconnected();
                return;
            }

            const data = await res.json();
            const live = data.live || {};

            // Live indicator
            document.getElementById('live-price').innerText = data.current_price != null ? '$' + Number(data.current_price).toFixed(2) : '--';
            document.getElementById('live-bid').innerText = live.bid != null ? '$' + live.bid.toFixed(2) : '--';
            document.getElementById('live-ask').innerText = live.ask != null ? '$' + live.ask.toFixed(2) : '--';
            document.getElementById('live-provider').innerText = live.provider || '--';
            document.getElementById('live-tick-ts').innerText = live.last_tick_at ? new Date(live.last_tick_at).toLocaleTimeString() : '--';
            document.getElementById('live-candle-ts').innerText = new Date(data.timestamp).toLocaleTimeString();
            document.getElementById('live-analysis-ts').innerText = live.last_analysis_at ? new Date(live.last_analysis_at).toLocaleTimeString() : '--';

            if (live.feed_connected === false) {
                showLiveDisconnected();
            } else {
                hideLiveDisconnected();
            }

            // Biases
            setBias('bias-4h', 'bias-4h-desc', data.market_bias['4h'].trend, data.market_bias['4h'].summary);
            setBias('bias-1h', 'bias-1h-desc', data.market_bias['1h'].trend, data.market_bias['1h'].summary);
            setBias('bias-15m', 'bias-15m-desc', data.market_bias['15m'].trend, data.market_bias['15m'].summary);

            // Signal
            const sig = data.signal;
            document.getElementById('signal-direction').innerText = sig.direction;
            document.getElementById('signal-direction').className = sig.direction === 'LONG' ? 'mb-0 text-success' : (sig.direction === 'SHORT' ? 'mb-0 text-danger' : 'mb-0 text-secondary');
            document.getElementById('signal-strategy').innerText = sig.strategy.replace(/_/g, ' ');
            document.getElementById('signal-score').innerText = sig.confidence_score.toFixed(0) + ' / 100';

            document.getElementById('sig-entry').innerText = '$' + sig.entry.toFixed(2);
            document.getElementById('sig-sl').innerText = '$' + sig.stop_loss.toFixed(2);
            document.getElementById('sig-tp1').innerText = '$' + sig.take_profit_1.toFixed(2);
            document.getElementById('sig-tp2').innerText = '$' + sig.take_profit_2.toFixed(2);
            document.getElementById('sig-tp3').innerText = '$' + sig.take_profit_3.toFixed(2);
            document.getElementById('sig-rr').innerText = '1:' + sig.risk_reward.toFixed(1);

            const reasonsList = document.getElementById('signal-reasons');
            reasonsList.innerHTML = '';
            sig.reasons.forEach(r => {
                const li = document.createElement('li');
                li.innerText = r;
                reasonsList.appendChild(li);
            });

            // AI
            const ai = data.ai_validation;
            document.getElementById('ai-status').innerText = ai.status;
            document.getElementById('ai-status').className = ai.status === 'APPROVE' ? 'badge bg-success' : (ai.status === 'REJECT' ? 'badge bg-danger' : 'badge bg-warning');
            document.getElementById('ai-explanation').innerText = ai.explanation;

            // Confluence Table
            const conf = (data.confluence && data.confluence.breakdown) || {};
            const tbody = document.getElementById('confluence-table-body');
            tbody.innerHTML = '';
            Object.keys(conf).forEach(k => {
                const item = conf[k];
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${item.category}</strong><br><small class="text-secondary">${item.details}</small></td>
                    <td>${item.max_points}</td>
                    <td class="fw-bold text-info">${item.points_awarded}</td>
                    <td>${item.passed ? '<span class="badge bg-success">PASS</span>' : '<span class="badge bg-secondary">FAIL</span>'}</td>
                `;
                tbody.appendChild(tr);
            });

            // Signal explanation
            document.getElementById('signal-explanation').innerText = data.explanation || '--';

            // SMC & Fib Details
            const smc = data.smc_analysis;
            document.getElementById('smc-zone-info').innerText = `Zone: ${smc.current_zone} | Eq: $${smc.equilibrium_price} | FVGs: ${smc.active_fvgs.length}`;

            const fib = data.fibonacci_setup;
            document.getElementById('fib-zone-info').innerText = fib ? `Golden Pocket: $${fib.entry_zone_min} - $${fib.entry_zone_max} (In Zone: ${fib.in_golden_pocket})` : 'No active setup';
        }

        function showLiveDisconnected() {
            const banner = document.getElementById('feed-disconnect-banner');
            if (banner) banner.classList.remove('d-none');
            const lastTick = document.getElementById('live-tick-ts');
            const lastKnown = lastTick ? lastTick.innerText : '--';
            const dlt = document.getElementById('disconnect-last-tick');
            if (dlt) dlt.innerText = lastKnown;
            const badge = document.getElementById('live-mode-badge');
            if (badge) { badge.innerText = 'DISCONNECTED'; badge.className = 'badge bg-danger mx-auto mt-1'; badge.style.width = 'fit-content'; }
            const price = document.getElementById('live-price');
            if (price && price.innerText === '--') price.innerText = 'LIVE FEED DISCONNECTED';
        }

        function hideLiveDisconnected() {
            const banner = document.getElementById('feed-disconnect-banner');
            if (banner) banner.classList.add('d-none');
            const badge = document.getElementById('live-mode-badge');
            if (badge) { badge.innerText = 'LIVE'; badge.className = 'badge bg-success mx-auto mt-1'; badge.style.width = 'fit-content'; }
        }

        function setBias(id, descId, trend, summary) {
            const el = document.getElementById(id);
            const desc = document.getElementById(descId);
            const badgeClass = trend === 'BULLISH' ? 'badge badge-bullish' : (trend === 'BEARISH' ? 'badge badge-bearish' : 'badge badge-neutral');
            el.innerHTML = `<span class="${badgeClass}">${trend}</span>`;
            desc.innerText = summary;
        }

        async function runBacktest() {
            try {
                document.getElementById('bt-trades').innerText = 'Simulating...';
                const res = await fetch('/backtest', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ symbol: 'XAUUSD', initial_balance: 10000.0, risk_percent: 1.0, limit_bars: 800 })
                });
                const data = await res.json();
                const s = data.summary;
                document.getElementById('bt-trades').innerText = s.total_trades;
                document.getElementById('bt-winrate').innerText = s.win_rate_pct + '%';
                document.getElementById('bt-pf').innerText = s.profit_factor.toFixed(2);
                document.getElementById('bt-profit').innerText = '$' + s.net_profit_usd.toFixed(2);
                document.getElementById('bt-dd').innerText = '$' + s.max_drawdown_usd.toFixed(2) + ' (' + s.max_drawdown_pct + '%)';
                document.getElementById('bt-exp').innerText = s.expectancy_r + 'R';
            } catch (err) {
                console.error(err);
            }
        }

        async function refreshPaperTrading() {
            try {
                const res = await fetch('/performance/account');
                const data = await res.json();
                document.getElementById('pt-balance').innerText = '$' + data.current_balance.toFixed(2);
                document.getElementById('pt-equity').innerText = '$' + data.equity.toFixed(2);
                document.getElementById('pt-open').innerText = data.active_positions;
                document.getElementById('pt-wr').innerText = data.win_rate_pct + '%';
                document.getElementById('pt-pf').innerText = data.profit_factor.toFixed(2);
                document.getElementById('pt-pnl').innerText = '$' + data.net_profit_usd.toFixed(2);
                document.getElementById('pt-trades').innerText = data.total_trades;
            } catch (err) {
                console.error(err);
            }
        }

        async function refreshFeedHealth() {
            try {
                const res = await fetch('/market/system-status');
                const data = await res.json();
                const st = data.status;
                const body = document.getElementById('system-status-body');
                const feedOk = st.feed_connected === 'connected';
                body.innerHTML = `
                    <div class="d-flex justify-content-between mb-2"><span>Feed</span><span class="badge ${feedOk ? 'bg-success' : 'bg-warning'}">${st.feed_connected}</span></div>
                    <div class="d-flex justify-content-between mb-2"><span>Last tick</span><small class="text-secondary">${st.last_tick_at ? new Date(st.last_tick_at).toLocaleString() : '--'}</small></div>
                    <div class="d-flex justify-content-between mb-2"><span>Last candle</span><small class="text-secondary">${st.last_closed_candle_ts || '--'}</small></div>
                    <div class="d-flex justify-content-between mb-2"><span>Last analysis</span><small class="text-secondary">${st.last_analysis_at ? new Date(st.last_analysis_at).toLocaleString() : '--'}</small></div>
                    <div class="d-flex justify-content-between mb-2"><span>Next analysis</span><small class="text-secondary">${st.next_analysis_at ? new Date(st.next_analysis_at).toLocaleString() : '--'}</small></div>
                    <div class="d-flex justify-content-between mb-2"><span>Analysis runs</span><small class="text-secondary">${st.analysis_runs}</small></div>
                    <div class="d-flex justify-content-between mb-2"><span>Last score</span><small class="text-secondary">${st.last_analysis_score || '--'}</small></div>
                    <div class="d-flex justify-content-between mb-2"><span>Scheduler</span><small class="text-secondary">${st.scheduler_running ? 'RUNNING' : 'STOPPED'}</small></div>
                    ${st.last_error ? `<div class="text-danger small mt-2">⚠ ${st.last_error}</div>` : ''}`;
            } catch (err) {
                console.error(err);
            }
        }

        async function refreshActiveTrades() {
            try {
                const res = await fetch('/paper-trades');
                const data = await res.json();
                const body = document.getElementById('active-trades-body');
                const active = (data.database_trades || []).filter(t => ['PENDING','ENTRY_HIT','TP1_HIT','TP2_HIT'].includes(t.state));
                if (active.length === 0) {
                    body.innerHTML = '<p class="text-secondary">No active positions.</p>';
                    return;
                }
                body.innerHTML = active.map(t => `
                    <div class="d-flex justify-content-between align-items-center mb-2 p-2 rounded" style="background-color:#1c2433;">
                        <div>
                            <strong>${t.direction}</strong> @ $${t.actual_entry || t.target_entry}
                            <small class="text-secondary d-block">${t.state} · ${t.symbol}</small>
                        </div>
                        <span class="badge bg-info">${t.state}</span>
                    </div>`).join('');
            } catch (err) {
                console.error(err);
            }
        }

        async function refreshDataQuality() {
            try {
                const res = await fetch('/market/data-quality');
                const dq = await res.json();
                const feedOk = dq.connected;
                const histOk = dq.historical_available;
                const healthy = !dq.degraded;

                document.getElementById('dq-feed').innerHTML = feedOk ? '<span class="badge bg-success">🟢 CONNECTED</span>' : '<span class="badge bg-danger">🔴 DISCONNECTED</span>';
                document.getElementById('dq-hist').innerHTML = histOk ? '<span class="badge bg-success">🟢 AVAILABLE</span>' : '<span class="badge bg-danger">🔴 UNAVAILABLE</span>';
                document.getElementById('dq-health').innerHTML = healthy ? '<span class="badge bg-success">🟢 HEALTHY</span>' : '<span class="badge bg-danger">🔴 DEGRADED</span>';
                document.getElementById('dq-candles').innerText = dq.candle_count;
                document.getElementById('dq-gaps').innerText = dq.gap_count;
                document.getElementById('dq-dup').innerText = dq.duplicate_count;
                document.getElementById('dq-latest').innerText = dq.newest_candle ? new Date(dq.newest_candle).toLocaleString() : '--';

                const box = document.getElementById('dq-degraded-box');
                if (dq.degraded) {
                    box.classList.remove('d-none');
                    document.getElementById('dq-reason').innerText = dq.degradation_reason;
                } else {
                    box.classList.add('d-none');
                }

                // History refresh section
                const rs = dq.last_history_refresh_status || 'NONE';
                const rsEl = document.getElementById('dq-refresh-status');
                if (rs === 'SUCCESS') { rsEl.innerHTML = '<span class="badge bg-success">🟢 SUCCESS</span>'; }
                else if (rs === 'FAILED') { rsEl.innerHTML = '<span class="badge bg-danger">🔴 FAILED</span>'; }
                else if (rs === 'RUNNING') { rsEl.innerHTML = '<span class="badge bg-warning">🟡 RUNNING</span>'; }
                else { rsEl.innerHTML = '<span class="badge bg-secondary">NONE</span>'; }
                document.getElementById('dq-refresh-last').innerText = dq.last_history_refresh_at ? new Date(dq.last_history_refresh_at).toLocaleTimeString() : '--';
                document.getElementById('dq-refresh-next').innerText = dq.next_history_refresh_at ? new Date(dq.next_history_refresh_at).toLocaleTimeString() : '--';
                const errEl = document.getElementById('dq-refresh-error');
                if (dq.last_history_refresh_error) {
                    errEl.classList.remove('d-none');
                    errEl.innerText = 'Last error: ' + dq.last_history_refresh_error;
                } else {
                    errEl.classList.add('d-none');
                }
            } catch (err) {
                console.error(err);
            }
        }

        async function refreshValidation() {
            try {
                const res = await fetch('/research/classification');
                const d = await res.json();
                const grade = d.grade || 'INCONCLUSIVE';
                const gEl = document.getElementById('rv-grade');
                const color = grade === 'ROBUST' ? 'text-success' : (grade === 'FAILED' ? 'text-danger' : (grade === 'WEAK' ? 'text-warning' : 'text-secondary'));
                gEl.className = color;
                gEl.innerText = grade;
                const oos = d.out_of_sample || {};
                document.getElementById('rv-trades').innerText = oos.trades ?? '--';
                document.getElementById('rv-winrate').innerText = (oos.win_rate_pct != null ? oos.win_rate_pct + '%' : '--');
                document.getElementById('rv-exp').innerText = (oos.expectancy_r != null ? (oos.expectancy_r > 0 ? '+' : '') + oos.expectancy_r + ' R' : '--');
                document.getElementById('rv-pf').innerText = oos.profit_factor ?? '--';
                document.getElementById('rv-dd').innerText = (oos.max_drawdown_pct != null ? oos.max_drawdown_pct + '%' : '--');
                document.getElementById('rv-mcdd').innerText = d.monte_carlo ? d.monte_carlo.p95_drawdown_pct + '%' : '--';
                const data = d.data || {};
                document.getElementById('rv-data').innerText = (data.start || '') + ' → ' + (data.end || '');
                const reasons = document.getElementById('rv-reasons');
                reasons.innerHTML = '';
                (d.reasons || []).forEach(r => {
                    const div = document.createElement('div');
                    div.innerText = '• ' + r;
                    reasons.appendChild(div);
                });
            } catch (err) {
                console.error(err);
            }
        }

        async function refreshPaperTradesTable() {
            try {
                const res = await fetch('/paper-trades');
                const data = await res.json();
                const body = document.getElementById('paper-trades-body');
                const trades = data.database_trades || [];
                if (trades.length === 0) {
                    body.innerHTML = '<tr><td colspan="9" class="text-secondary text-center">No paper trades yet.</td></tr>';
                    return;
                }
                body.innerHTML = trades.slice(0, 40).map(t => {
                    const cls = (t.realized_pnl || 0) > 0 ? 'text-success' : (t.realized_pnl || 0) < 0 ? 'text-danger' : 'text-secondary';
                    return `<tr>
                        <td class="small">${t.created_at ? new Date(t.created_at).toLocaleString() : '--'}</td>
                        <td class="${t.direction === 'LONG' ? 'text-success' : 'text-danger'}">${t.direction}</td>
                        <td>$${t.actual_entry || t.target_entry}</td>
                        <td>$${t.stop_loss}</td>
                        <td>$${t.take_profit_2}</td>
                        <td><span class="badge bg-secondary">${t.state}</span></td>
                        <td class="${cls}">${t.realized_pnl != null ? '$' + t.realized_pnl.toFixed(2) : '--'}</td>
                        <td>${t.realized_r != null ? t.realized_r.toFixed(2) + 'R' : '--'}</td>
                        <td class="small">${t.exit_reason || '--'}</td>
                    </tr>`;
                }).join('');
            } catch (err) { console.error(err); }
        }

        async function refreshObservation() {
            try {
                const res = await fetch('/research/observation');
                const d = await res.json();
                if (!d.available) { return; }
                document.getElementById('obs-signals').innerText = d.total_signals;
                document.getElementById('obs-closed').innerText = d.closed;
                document.getElementById('obs-wr').innerText = (d.win_rate_pct != null ? d.win_rate_pct + '%' : '--');
                document.getElementById('obs-pnl').innerText = (d.hypothetical_pnl_r != null ? d.hypothetical_pnl_r + ' R' : '--');
                document.getElementById('obs-long').innerText = d.long;
                document.getElementById('obs-short').innerText = d.short;
                document.getElementById('obs-mfe').innerText = (d.avg_mfe_r != null ? d.avg_mfe_r + ' / ' + d.avg_mae_r : '--');
            } catch (err) { console.error(err); }
        }

        async function refreshSignalOutcomes() {
            try {
                const res = await fetch('/research/signal-outcomes');
                const d = await res.json();
                document.getElementById('so-tracked').innerText = d.tracked_total ?? '--';
                document.getElementById('so-closed').innerText = d.closed ?? '--';
                const hr = d.hit_rates_pct || {};
                document.getElementById('so-tphits').innerText = (hr.tp1 != null ? hr.tp1 + '% / ' + hr.tp2 + '% / ' + hr.tp3 + '%' : '--');
                document.getElementById('so-slhit').innerText = (hr.sl != null ? hr.sl + '%' : '--');
                const cs = d.closed_stats || {};
                document.getElementById('so-avg-med-r').innerText = (cs.avg_r != null ? cs.avg_r : cs.expectancy_r) + ' / ' + (cs.median_r ?? '--');
                document.getElementById('so-sample').innerText = d.sample_size ?? '--';
                const t = d.today || {};
                document.getElementById('so-today').innerText = (t.signals_24h != null ? t.signals_24h + ' (' + t.long + 'L / ' + t.short + 'S)' : '--');
                const ad = d.admission || {};
                document.getElementById('so-admission').innerText = (ad.admitted != null ? ad.admitted + ' / ' + ad.rejected : '--');
                const v = d.current_strategy_version || '--';
                document.getElementById('so-version').innerText = v.length > 24 ? v.substring(v.lastIndexOf(':') + 1) : v;
                document.getElementById('so-duration').innerText = (d.observation_duration_hours != null ? d.observation_duration_hours + ' h' : '--');
            } catch (err) { console.error(err); }
        }

        async function refreshSystemStatus() {
            try {
                const res = await fetch('/market/system-status');
                const st = (await res.json()).status || {};
                const obs = document.getElementById('obs-mode-badge');
                if (st.observation_mode) {
                    obs.style.display = '';
                } else {
                    obs.style.display = 'none';
                }
                const paper = document.getElementById('paper-status-badge');
                paper.style.display = '';
                const blocked = (st.strategy_grade === 'FAILED') || !st.paper_trading_enabled;
                paper.className = 'badge bg-danger me-2' + (blocked ? '' : '');
                paper.innerText = blocked ? 'PAPER: BLOCKED' : 'PAPER: ENABLED';
            } catch (err) { console.error(err); }
        }

        async function refreshMtfCandidates() {
            try {
                const res = await fetch('/research/mtf-report');
                const d = await res.json();
                const body = document.getElementById('mtf-candidates-body');
                if (!d.available) {
                    body.innerHTML = '<tr><td colspan="9" class="text-secondary text-center">No MTF research yet. Run scripts/run_mtf_research.py</td></tr>';
                    return;
                }
                const cands = d.candidates || [];
                if (cands.length === 0) {
                    body.innerHTML = '<tr><td colspan="9" class="text-secondary text-center">No candidates.</td></tr>';
                    return;
                }
                body.innerHTML = cands.slice(0, 12).map(c => `
                    <tr>
                        <td class="small">${c.config}</td>
                        <td>${c.regime}</td>
                        <td>${c.conf}</td>
                        <td class="small">${c.selection}</td>
                        <td>${c.tp_r}</td>
                        <td>${c.trades ?? '--'}</td>
                        <td>${c.win_rate_pct != null ? c.win_rate_pct + '%' : '--'}</td>
                        <td class="${c.expectancy_r > 0 ? 'text-success' : 'text-danger'}">${c.expectancy_r != null ? c.expectancy_r.toFixed(3) : '--'}</td>
                        <td>${c.positive_windows != null ? c.positive_windows + '/' + c.total_windows : '--'}</td>
                    </tr>`).join('');
            } catch (err) { console.error(err); }
        }

        // Auto load on start
        window.onload = () => {
            refreshAnalysis();
            runBacktest();
            refreshPaperTrading();
            refreshFeedHealth();
            refreshActiveTrades();
            refreshDataQuality();
            refreshValidation();
            refreshObservation();
            refreshSignalOutcomes();
            refreshSystemStatus();
            refreshMtfCandidates();
            refreshPaperTradesTable();
            setInterval(() => {
                refreshAnalysis();
                refreshPaperTrading();
                refreshFeedHealth();
                refreshActiveTrades();
                refreshDataQuality();
                refreshValidation();
                refreshObservation();
                refreshSignalOutcomes();
                refreshSystemStatus();
                refreshMtfCandidates();
                refreshPaperTradesTable();
            }, window.DASHBOARD_REFRESH_MS || 30000);
        };
    </script>
</body>
</html>
"""


@router.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    """Serves the PREMIUM XAU AI Signal Intelligence Terminal (shared with /terminal)."""
    from app.api.routes.terminal import serve_terminal_index
    return await serve_terminal_index()


@router.get("/dashboard-legacy", response_class=HTMLResponse)
async def get_dashboard_legacy():
    """Backward-compatibility route for the original developer dashboard UI."""
    from app.config.settings import get_settings
    settings = get_settings()
    refresh_seconds = max(5, int(getattr(settings, "DASHBOARD_REFRESH_SECONDS", 30)))
    inject = (
        "<script>window.DASHBOARD_REFRESH_MS = "
        f"{refresh_seconds * 1000};</script>"
    )
    return HTMLResponse(content=DASHBOARD_HTML.replace("</head>", inject + "</head>"))
