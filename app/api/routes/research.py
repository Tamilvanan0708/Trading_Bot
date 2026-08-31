"""
Strategy Validation / Research API Routes.
"""

import json
import os
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/research", tags=["Strategy Research"])

_REPORT_PATH = "data/research/latest_report.json"


@router.get("/summary")
async def get_research_summary():
    """Returns the latest strategy validation report (if one exists)."""
    if not os.path.exists(_REPORT_PATH):
        return {"available": False, "detail": "No research report yet. Run scripts/run_strategy_research.py"}
    with open(_REPORT_PATH, encoding="utf-8") as f:
        report = json.load(f)
    report["available"] = True
    return report


@router.get("/classification")
async def get_research_classification():
    """Returns only the current strategy classification."""
    if not os.path.exists(_REPORT_PATH):
        return {"available": False, "grade": "INCONCLUSIVE"}
    with open(_REPORT_PATH, encoding="utf-8") as f:
        report = json.load(f)
    return {
        "available": True,
        "grade": report.get("classification", {}).get("grade", "INCONCLUSIVE"),
        "reasons": report.get("classification", {}).get("reasons", []),
        "out_of_sample": report.get("out_of_sample", {}),
    }


@router.get("/observation")
async def get_observation_summary():
    """Returns live observation-mode statistics (hypothetical signals only)."""
    from app.research.observation import get_observation_store
    return {"available": True, **get_observation_store().summary()}


@router.get("/mtf-report")
async def get_mtf_report():
    """Returns the latest MTF / candidate research report (if it exists)."""
    path = "data/research/mtf_report.json"
    if not os.path.exists(path):
        return {"available": False, "detail": "No MTF research yet. Run scripts/run_mtf_research.py"}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.get("/redesign")
async def get_redesign_report():
    """Returns the latest strategy-redesign research report (if one exists)."""
    path = "data/research/redesign_report.json"
    if not os.path.exists(path):
        return {"available": False, "detail": "No redesign research yet. Run scripts/run_strategy_redesign.py"}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@router.get("/signal-outcomes")
async def get_signal_outcomes():
    """Returns forward-outcome statistics for all tracked signals.

    Uses the real database signal-outcome columns populated by the
    SignalOutcomeTracker (Phase 8).  This is the authoritative forward
    validation dataset — distinct from the JSON observation store.
    """
    from app.database.connection import async_session_factory
    from app.database.repository import Repository

    async with async_session_factory() as session:
        repo = Repository(session)
        signals = await repo.list_open_signals_for_tracking(limit=2000)
        closed = [s for s in signals if s.outcome and s.outcome != "OPEN"]
        open_s = [s for s in signals if not s.outcome or s.outcome == "OPEN"]

        def _stats(rows):
            if not rows:
                return {"trades": 0, "win_rate_pct": 0.0, "expectancy_r": 0.0,
                        "avg_mfe_r": 0.0, "avg_mae_r": 0.0}
            rs = [s.final_r or 0.0 for s in rows if s.final_r is not None]
            mfe = [s.max_favorable_excursion_r or 0.0 for s in rows if s.max_favorable_excursion_r is not None]
            mae = [s.max_adverse_excursion_r or 0.0 for s in rows if s.max_adverse_excursion_r is not None]
            wins = [r for r in rs if r > 0]
            return {
                "trades": len(rows),
                "win_rate_pct": round(len(wins) / len(rs) * 100.0, 2) if rs else 0.0,
                "expectancy_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
                "avg_mfe_r": round(sum(mfe) / len(mfe), 3) if mfe else 0.0,
                "avg_mae_r": round(sum(mae) / len(mae), 3) if mae else 0.0,
            }

        by_outcome = {}
        for o in ("TP1", "TP2", "TP3", "SL", "EXPIRED"):
            rows = [s for s in closed if s.outcome == o]
            by_outcome[o] = {"trades": len(rows)}

        from datetime import datetime, timedelta, timezone

        def _aware(dt) -> datetime | None:
            """SQLAlchemy returns naive datetimes from SQLite; coerce to UTC."""
            if dt is None:
                return None
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

        now = datetime.now(timezone.utc)
        today = [s for s in signals if _aware(s.created_at) and (now - _aware(s.created_at)) <= timedelta(hours=24)]
        longs = [s for s in signals if s.direction == "LONG"]
        shorts = [s for s in signals if s.direction == "SHORT"]
        metadata = [s.metadata_payload or {} for s in signals]
        admitted = sum(1 for m in metadata if m.get("admission_allowed") is True)
        rejected = sum(1 for m in metadata if m.get("admission_allowed") is False)
        strategy_versions = sorted({
            m.get("strategy_version") or s.strategy_version
            for s, m in zip(signals, metadata)
            if (m.get("strategy_version") or s.strategy_version)
        })

        durations = [_aware(s.created_at) for s in signals if _aware(s.created_at)]
        obs_duration_hours = round((now - min(durations)).total_seconds() / 3600.0, 1) if durations else 0.0

        tp_hits = len(closed) or 1
        return {
            "tracked_total": len(signals),
            "open": len(open_s),
            "closed": len(closed),
            "closed_stats": _stats(closed),
            "open_stats": _stats(open_s),
            "by_outcome": by_outcome,
            "hit_rates_pct": {
                "tp1": round(sum(1 for s in closed if s.tp1_hit) / tp_hits * 100.0, 2),
                "tp2": round(sum(1 for s in closed if s.tp2_hit) / tp_hits * 100.0, 2),
                "tp3": round(sum(1 for s in closed if s.tp3_hit) / tp_hits * 100.0, 2),
                "sl": round(sum(1 for s in closed if s.sl_hit) / tp_hits * 100.0, 2),
            },
            "today": {
                "signals_24h": len(today),
                "long": len([s for s in today if s.direction == "LONG"]),
                "short": len([s for s in today if s.direction == "SHORT"]),
            },
            "direction_totals": {
                "long": len(longs),
                "short": len(shorts),
            },
            "admission": {
                "admitted": admitted,
                "rejected": rejected,
                "unknown": len(signals) - admitted - rejected,
            },
            "strategy_versions": strategy_versions,
            "current_strategy_version": strategy_versions[-1] if strategy_versions else None,
            "observation_duration_hours": obs_duration_hours,
            "sample_size": len(signals),
        }


@router.get("/candidates")
async def get_candidate_comparison():
    """Side-by-side forward-observation comparison of Candidate A/B/C.

    Uses real database signals whose strategy_version starts with CANDIDATE_.
    Each candidate is evaluated independently; outcomes never mix.
    """

    from sqlalchemy import select

    from app.database.connection import async_session_factory
    from app.database.models import SignalModel

    async with async_session_factory() as session:
        stmt = (
            select(SignalModel)
            .where(SignalModel.strategy_version.like("CANDIDATE_%"))
            .order_by(SignalModel.created_at)
        )
        rows = list((await session.execute(stmt)).scalars().all())

    by_cand: dict[str, list] = {}
    for r in rows:
        by_cand.setdefault(r.strategy_version, []).append(r)

    out = {}
    for version, sigs in sorted(by_cand.items()):
        closed = [s for s in sigs if s.outcome and s.outcome != "OPEN"]
        open_s = [s for s in sigs if not s.outcome or s.outcome == "OPEN"]
        rs = [s.final_r or 0.0 for s in closed if s.final_r is not None]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        mfe = [s.max_favorable_excursion_r or 0.0 for s in closed if s.max_favorable_excursion_r is not None]
        mae = [s.max_adverse_excursion_r or 0.0 for s in closed if s.max_adverse_excursion_r is not None]
        by_outcome = {}
        for o in ("TP1", "TP2", "TP3", "SL", "EXPIRED"):
            by_outcome[o] = sum(1 for s in closed if s.outcome == o)
        sessions = {}
        regimes = {}
        for s in closed:
            sessions[s.session or "UNKNOWN"] = sessions.get(s.session or "UNKNOWN", 0) + 1
            regimes[s.regime or "UNKNOWN"] = regimes.get(s.regime or "UNKNOWN", 0) + 1

        out[version] = {
            "version": version,
            "signals": len(sigs),
            "open": len(open_s),
            "closed": len(closed),
            "win_rate_pct": round(len(wins) / len(rs) * 100.0, 2) if rs else 0.0,
            "expectancy_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
            "median_r": round(sorted(rs)[len(rs) // 2], 3) if rs else 0.0,
            "gross_win_r": round(sum(wins), 3),
            "gross_loss_r": round(abs(sum(losses)), 3),
            "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) else 0.0,
            "avg_mfe_r": round(sum(mfe) / len(mfe), 3) if mfe else 0.0,
            "avg_mae_r": round(sum(mae) / len(mae), 3) if mae else 0.0,
            "outcomes": by_outcome,
            "sessions": sessions,
            "regimes": regimes,
            "first_signal_at": min((s.created_at for s in sigs), default=None),
            "last_signal_at": max((s.created_at for s in sigs), default=None),
        }

    return {"candidates": out, "status": "FORWARD_OBSERVATION" if out else "NO_FORWARD_DATA"}


@router.get("/ranking")
async def get_research_ranking():
    """Returns the cost-aware, risk-adjusted candidate ranking with health.

    Reuses the MTF research report for the OOS side and the live forward
    signal-outcomes for the forward side.  Read-only; never auto-promotes.
    """
    from app.research.candidates import resolve_candidate_status
    from app.research.ranking import rank_candidates

    mtf = None
    path = "data/research/mtf_report.json"
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                mtf = json.load(f)
        except Exception:  # noqa: BLE001
            mtf = None

    cost_map = {}
    cost_path = "reports/cost_robustness.json"
    if os.path.exists(cost_path):
        try:
            with open(cost_path, encoding="utf-8") as f:
                cost = json.load(f)
            for ver, data in (cost.get("candidates") or {}).items():
                cost_map[ver] = data.get("cost_sweep", {})
        except Exception:  # noqa: BLE001
            pass

    candidates = []
    for c in (mtf.get("candidates") or []) if mtf else []:
        row = dict(c)
        row["cost_sweep"] = cost_map.get(row.get("config"), {})
        candidates.append(row)

    ranked = rank_candidates(candidates)
    config_to_version = {
        "PROD_4H_1H_30M_15M": "CANDIDATE_A_V1",
        "4H_1H_30M_5M": "CANDIDATE_B_V1",
        "4H_1H_15M_5M": "CANDIDATE_C_V1",
    }
    for row in ranked:
        version = config_to_version.get(row.get("config"), row.get("config", ""))
        row["status"] = resolve_candidate_status(version, forward_signals=0)

    return {
        "ranked_candidates": ranked,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Balanced risk-adjusted score; cost-adjusted expectancy preferred over raw.",
    }


@router.get("/health")
async def get_research_health():
    """Returns the candidate health monitor (GREEN/YELLOW/RED) for each
    forward-observation candidate, based on bootstrap CIs and cost-adjusted
    OOS references."""
    from sqlalchemy import select

    from app.database.connection import async_session_factory
    from app.database.models import SignalModel
    from app.research.candidate_health import assess_all_candidates

    CANDIDATE_VERSIONS = ["CANDIDATE_A_V1", "CANDIDATE_B_V1", "CANDIDATE_C_V1"]
    async with async_session_factory() as session:
        stmt = select(SignalModel).where(SignalModel.strategy_version.like("CANDIDATE_%"))
        rows = list((await session.execute(stmt)).scalars().all())
    health = assess_all_candidates(CANDIDATE_VERSIONS, rows)
    return {"candidates": {v: h.to_dict() for v, h in health.items()}}


@router.get("/daily-opportunity")
async def get_daily_opportunity():
    """Honest daily-opportunity statistics for the best cost-viable candidate.

    Reports distribution of best-day winning points and the probability of
    reaching 30/50 points, WITHOUT forcing any target.
    """
    from app.research.candidates import evaluate_candidate  # noqa: F401
    from app.research.replay_engine import daily_opportunity
    from app.research.data_fetch import load_real_history
    from app.core.constants import TimeFrame
    from app.core.mtf_config import PROD_4H_1H_30M_15M
    from app.data.timeframe_resampler import resample_candles

    CANDIDATE_A = {"config": "PROD_4H_1H_30M_15M", "regime": "RANGING", "conf": 75, "tp_r": 1.75, "sl_atr": 1.5}
    path = f"data/research/mtf_signals/signals_{CANDIDATE_A['config']}.json"
    if not os.path.exists(path):
        return {"available": False, "detail": "No cached signals. Run scripts/run_mtf_research.py first."}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    from app.core.constants import SignalDirection
    from app.research.replay_engine import SignalRecord
    sigs = []
    for x in raw:
        if x.get("regime") != "RANGING" or x.get("confidence", 0) < 75:
            continue
        sigs.append(SignalRecord(
            timestamp=datetime.fromisoformat(x["timestamp"]),
            direction=SignalDirection(x["direction"]),
            entry=x["entry"], base_sl=x["base_sl"], base_risk=x["base_risk"],
            atr=x["atr"], confidence=x["confidence"],
            market_bias=x["market_bias"], regime=x["regime"], session=x["session"],
            reasons=x.get("reasons", []),
        ))
    candles5m = load_real_history("data/research/xauusd_5m_2yr.json")
    base = resample_candles(candles5m, TimeFrame.M15)
    dop = daily_opportunity(sigs, base, tp_r=1.75, sl_atr=1.5, hold_bars=96)
    return {"available": True, "candidate": "CANDIDATE_A_V1", "daily_opportunity": dop,
            "conclusion": "STATISTICALLY SUPPORTED" if (dop.get("pct_days_ge_30pts") or 0) >= 30 else "NOT STATISTICALLY SUPPORTED"}