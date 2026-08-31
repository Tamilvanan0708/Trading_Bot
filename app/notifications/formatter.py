"""
Telegram Alert Message Formatter.
"""

from app.ai.models import AIValidationResult
from app.signals.models import SignalPayload


def _safe(v, default="--"):
    if v is None:
        return default
    s = str(v)
    return s if s and s != "None" else default


def _md(v, default="--") -> str:
    """Escape Telegram legacy-Markdown metacharacters in dynamic text.

    Strategy versions (e.g. ``PROD_4H_1H_30M_15M``) contain underscores which
    legacy Markdown interprets as italics, causing Telegram 400 parse errors.
    """
    s = _safe(v, default)
    return s.replace("_", r"\_").replace("*", r"\*").replace("`", r"\`").replace("[", r"\[")


def format_telegram_signal(
    signal: SignalPayload,
    ai_val: AIValidationResult | None = None,
    metadata: dict | None = None,
) -> str:
    """
    Formats signal alert into the standardized Telegram Markdown format.

    ``metadata`` optionally carries regime / session / HTF-bias fields
    (stamped by the scheduler after analysis).
    """
    metadata = metadata or {}
    strategy_label = signal.strategy.value.replace("_", " ").title()
    version_label = _md(signal.strategy_version if signal.strategy_version else strategy_label)
    reason_str = " • " + "\n • ".join(_md(r) for r in signal.reasons[:3]) if signal.reasons else "Multi-timeframe confluence confirmed."

    ai_line = ""
    if ai_val:
        status = ai_val.status.value if hasattr(ai_val.status, "value") else str(ai_val.status)
        ai_line = f"{status}"
    else:
        ai_line = "PENDING"

    mode = _md("FORWARD OBSERVATION" if str(signal.strategy_version or "").startswith("CANDIDATE") else "SIGNAL-ONLY")
    htf_bias = _md(metadata.get("htf_bias_4h"))
    struct = _md(metadata.get("structure_1h"))
    setup_tf = _md(metadata.get("setup_tf"), "15M")
    setup_state = _md(metadata.get("setup_state"), "CONFIRMED")
    trigger_tf = _md(metadata.get("trigger_tf"), "15M")
    trigger_state = _md(metadata.get("trigger_state"), "CONFIRMED")
    regime = _md(metadata.get("market_regime"))
    session = _md(metadata.get("session"))

    quality_map = {
        "VERY_STRONG": "EXCEPTIONAL",
        "STRONG": "HIGH",
        "MODERATE": "MODERATE",
        "WEAK": "LOW",
        "NO_TRADE": "NONE",
    }
    quality = quality_map.get(signal.signal_quality.value if hasattr(signal.signal_quality, "value") else str(signal.signal_quality), _md(signal.signal_quality))

    direction = signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction)

    message = (
        f"*XAU/USD SIGNAL*\n\n"
        f"*Strategy*: {version_label}\n"
        f"*Mode*: {mode}\n\n"
        f"*Direction*: {direction}\n\n"
        f"*Entry*: {signal.entry:.2f}\n"
        f"*Stop Loss*: {signal.stop_loss:.2f}\n"
        f"*TP1*: {signal.take_profit_1:.2f}\n"
        f"*TP2*: {signal.take_profit_2:.2f}\n"
        f"*TP3*: {signal.take_profit_3:.2f}\n\n"
        f"*R:R*: 1:{signal.risk_reward:.1f}\n\n"
        f"*Confluence*: {int(signal.confidence_score)}/100\n\n"
        f"*4H Bias*: {htf_bias}\n"
        f"*1H Structure*: {struct}\n"
        f"*{setup_tf} Setup*: {setup_state}\n"
        f"*{trigger_tf} Trigger*: {trigger_state}\n\n"
        f"*Regime*: {regime}\n"
        f"*Session*: {session}\n\n"
        f"*Signal Quality*: {quality}\n"
        f"*AI Validation*: {ai_line}\n\n"
        f"*Setup Evidence*:\n{reason_str}\n\n"
        f"⚠️ *Risk Warning*: RESEARCH / MANUAL EXECUTION ONLY.\n"
        f"*Execution*: MANUAL ONLY. No automatic order placement. The current "
        f"strategy is NOT statistically validated (classified FAILED). Trade at "
        f"your own risk.\n\n"
        f"*Timestamp (UTC)*: {signal.timestamp.strftime('%Y-%m-%d %H:%M') if signal.timestamp else '--'}"
    )
    return message
