"""
Deterministic Signal Quality Score (Phase 10).

Produces a 0-100 QUALITY_SCORE and a STRONG/GOOD/NEUTRAL/WEAK/INVALID
classification from the signal's deterministic evidence (confluence, MTF
alignment, R:R, regime, SMC/Fib confirmation).

This is ADVISORY metadata only.  It NEVER overrides safety gates and a high
score never implies guaranteed profit.
"""

from dataclasses import dataclass, field


@dataclass
class QualityScoreResult:
    score: float
    classification: str          # STRONG | GOOD | NEUTRAL | WEAK | INVALID
    components: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "quality_score": round(self.score, 1),
            "quality_class": self.classification,
            "components": self.components,
            "notes": self.notes,
        }


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def classify(score: float) -> str:
    if score >= 80:
        return "STRONG"
    if score >= 65:
        return "GOOD"
    if score >= 50:
        return "NEUTRAL"
    if score >= 30:
        return "WEAK"
    return "INVALID"


def _mtf_alignment(sig: dict) -> float:
    """Fraction of available HTF biases aligned with the signal direction."""
    dirn = (sig.get("direction") or "").upper()
    if dirn not in ("LONG", "SHORT"):
        return 0.0
    mb = sig.get("market_bias")
    if isinstance(mb, str):
        return 1.0 if (dirn == "LONG" and mb.upper() == "BULLISH") or (dirn == "SHORT" and mb.upper() == "BEARISH") else 0.0
    return 0.0


def _smc_fib_confirmation(sig: dict) -> float:
    """Uses confluence breakdown (if present) to score SMC + Fib confirmation."""
    bd = None
    if sig.get("detected_structures") and sig["detected_structures"].get("score_breakdown"):
        bd = sig["detected_structures"]["score_breakdown"]
    elif sig.get("metadata_payload") and sig["metadata_payload"].get("confluence_breakdown"):
        bd = sig["metadata_payload"]["confluence_breakdown"]
    if not bd:
        return 0.0
    smc = bd.get("smc_confirmation") or {}
    fib = bd.get("fib_confirmation") or {}
    smc_p = (smc.get("points_awarded") or 0) / max(1, smc.get("max_points") or 1)
    fib_p = (fib.get("points_awarded") or 0) / max(1, fib.get("max_points") or 1)
    return round(_clamp(0.5 * smc_p + 0.5 * fib_p), 3)


def compute_quality_score(sig: dict) -> QualityScoreResult:
    """Deterministic 0-100 quality score from a signal dict.

    ``sig`` may be a `/signals` row or the ``signal`` object from
    `/analysis/live`.  Missing fields degrade gracefully to 0 (never invented).
    """
    components = {}
    notes = []

    # 1. Confluence (0-40)
    conf = sig.get("confidence_score")
    conf_pts = 40.0 * _clamp((conf or 0) / 100.0)
    components["confluence"] = round(conf_pts, 1)
    if conf is None:
        notes.append("No confluence score available.")

    # 2. MTF alignment (0-15)
    alignment = _mtf_alignment(sig)
    comp_mtf = 15.0 * alignment
    components["mtf_alignment"] = round(comp_mtf, 1)

    # 3. R:R (0-10)
    rr = sig.get("risk_reward")
    comp_rr = 10.0 * _clamp((rr or 0) / 3.0)
    components["risk_reward"] = round(comp_rr, 1)

    # 4. Signal quality tier (0-10)
    qmap = {"VERY_STRONG": 1.0, "STRONG": 0.8, "MODERATE": 0.5, "WEAK": 0.25, "NO_TRADE": 0.0}
    q = (sig.get("signal_quality") or "").upper()
    comp_q = 10.0 * qmap.get(q, 0.0)
    components["signal_tier"] = round(comp_q, 1)

    # 5. Regime (0-10) — RANGING/TRENDING preferred, UNCERTAIN penalized
    regime = (sig.get("regime") or "").upper()
    regime_score = {"RANGING": 1.0, "TRENDING": 1.0, "HIGH_VOL": 0.6, "LOW_VOL": 0.6, "UNCERTAIN": 0.2}.get(regime, 0.4)
    components["regime"] = round(10.0 * regime_score, 1)

    # 6. SMC + Fibonacci confirmation (0-15)
    comp_smc = 15.0 * _smc_fib_confirmation(sig)
    components["smc_fib"] = round(comp_smc, 1)

    score = conf_pts + comp_mtf + comp_rr + comp_q + 10.0 * regime_score + comp_smc
    score = round(_clamp(score, 0, 100), 1)

    return QualityScoreResult(score=score, classification=classify(score), components=components, notes=notes)
