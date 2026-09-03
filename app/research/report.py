"""
Professional out-of-sample validation report builder.
"""

from datetime import datetime

from app.research.classification import ClassificationResult
from app.research.metrics import compute_extended_metrics
from app.research.walkforward import WindowResult


def build_validation_report(
    symbol: str,
    candles_start: datetime,
    candles_end: datetime,
    candle_count: int,
    data_quality: dict,
    window_results: list[WindowResult],
    monte_carlo: dict,
    bootstrap: dict,
    regime_breakdown: dict,
    session_breakdown: dict,
    confluence_breakdown: dict,
    ai_effectiveness: dict,
    classification: ClassificationResult,
    lookahead_detected: bool = False,
) -> dict:
    """Assemble the complete validation report."""

    # Aggregate out-of-sample trades across all test windows.
    oos_trades = []
    for wr in window_results:
        if wr.test_result and wr.test_result.trades:
            oos_trades.extend(wr.test_result.trades)
    oos_metrics = compute_extended_metrics(oos_trades) if oos_trades else {}

    return {
        "data": {
            "label": data_quality.get("label", "REAL DATA (Binance XAUUSDT)"),
            "symbol": symbol,
            "start": candles_start.isoformat() if candles_start else None,
            "end": candles_end.isoformat() if candles_end else None,
            "candles": candle_count,
            "quality": data_quality,
        },
        "out_of_sample": {
            "trades": oos_metrics.get("total_trades", 0),
            "win_rate_pct": oos_metrics.get("win_rate_pct", 0.0),
            "profit_factor": oos_metrics.get("profit_factor", 0.0),
            "expectancy_r": oos_metrics.get("expectancy_r", 0.0),
            "max_drawdown_pct": oos_metrics.get("max_drawdown_pct", 0.0),
            "net_return_pct": round((oos_metrics.get("net_profit_usd", 0.0) / max(1.0, oos_metrics.get("initial_balance", 10000.0))) * 100.0, 2),
            "oos_metrics": oos_metrics,
        },
        "walk_forward": {
            "windows": [wr.to_dict() for wr in window_results],
            "positive_windows": sum(1 for wr in window_results if wr.test_metrics.get("expectancy_r", 0.0) > 0),
            "total_windows": len(window_results),
        },
        "monte_carlo": monte_carlo,
        "bootstrap": bootstrap,
        "regime_breakdown": regime_breakdown,
        "session_breakdown": session_breakdown,
        "confluence_breakdown": confluence_breakdown,
        "ai_effectiveness": ai_effectiveness,
        "classification": classification.to_dict(),
        "lookahead_detected": lookahead_detected,
        "generated_at": datetime.now().isoformat(),
    }


def summarize_report(report: dict) -> str:
    """Render a compact human-readable summary."""
    d = report["data"]
    oos = report["out_of_sample"]
    cls = report["classification"]
    lines = [
        "=" * 50,
        "VALIDATION SUMMARY",
        "=" * 50,
        f"Data:       {d['symbol']} ({d['label']})",
        f"Period:     {d['start']} → {d['end']}  [{d['candles']} candles]",
        f"OOS Trades: {oos['trades']}",
        f"Win Rate:   {oos['win_rate_pct']}%",
        f"Profit Factor: {oos['profit_factor']}",
        f"Expectancy: {oos['expectancy_r']:+.2f} R",
        f"Max DD:     {oos['max_drawdown_pct']}%",
        f"MC p95 DD:  {report['monte_carlo'].get('p95_drawdown_pct', 0)}%",
        "",
        f"CLASSIFICATION: {cls['grade']}",
    ]
    for r in cls.get("reasons", []):
        lines.append(f"  - {r}")
    return "\n".join(lines)