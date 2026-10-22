/* =====================================================================
   Breakout Strategy Agent — Routing Configuration
   Integrates breakout agent routes seamlessly into the terminal SPA router
   ===================================================================== */
"use strict";

(function () {
  if (typeof Routes === "undefined") {
    console.error("Global Routes object not initialized before breakoutRoutes");
    return;
  }

  // 1. Main Breakout Strategy Dashboard
  Routes["/breakout-strategy"] = function (mount) {
    if (window.BreakoutStrategy && typeof window.BreakoutStrategy.renderView === "function") {
      window.BreakoutStrategy.renderView(mount);
    }
  };

  // 2. Standalone Real-Time Breakout Signals View
  Routes["/breakout-signals"] = function (mount) {
    if (window.SignalsModule && typeof window.SignalsModule.renderStandalone === "function") {
      window.SignalsModule.renderStandalone(mount);
    }
  };

  // 3. Standalone Breakout Backtest Engine View
  Routes["/breakout-backtest"] = function (mount) {
    if (window.BacktestModule && typeof window.BacktestModule.renderStandalone === "function") {
      window.BacktestModule.renderStandalone(mount);
    }
  };
})();
