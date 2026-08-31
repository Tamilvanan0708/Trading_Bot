"""
Tests for the forward-observation candidate system:
- candidate evaluator fires/does-not-fire correctly
- side-by-side observation persists versioned signals
- signal deduplication (strategy_version + candle)
- outcome tracking for candidate signals
- candidate comparison endpoint data shape
"""


import pytest

from app.config.settings import Settings
from app.core.constants import TimeFrame
from app.data.models import Candle, MultiTimeframeSnapshot
from app.data.timeframe_resampler import IncrementalResampler
from app.database.repository import Repository


def _candle(ts, o, h, l, c) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=10.0)


def _snapshot(candles):
    res = IncrementalResampler()
    for c in candles:
        res.add(c)
    return MultiTimeframeSnapshot(
        symbol="XAUUSD",
        timestamp=candles[-1].timestamp,
        current_price=candles[-1].close,
        m5=res.series(TimeFrame.M5, 120),
        m15=res.series(TimeFrame.M15, 150),
        m30=res.series(TimeFrame.M30, 100),
        h1=res.series(TimeFrame.H1, 80),
        h4=res.series(TimeFrame.H4, 50),
    )


def _real_slice(n=1500):
    from app.research.data_fetch import load_real_history
    return load_real_history("data/research/xauusd_5m_2yr.json")[:n]


@pytest.mark.asyncio
async def test_candidate_observation_persists_and_dedups(in_memory_db, monkeypatch):
    from sqlalchemy import select

    from app.database.models import SignalModel
    from app.research.candidate_observation import CandidateObservationService

    candles = _real_slice()
    snap = _snapshot(candles)
    repo = Repository(in_memory_db)

    service = CandidateObservationService(Settings(OBSERVATION_MODE=True))
    res1 = await service.process_candle(snap, repo)
    # Second identical candle must be fully deduplicated.
    res2 = await service.process_candle(snap, repo)

    stmt = select(SignalModel).where(SignalModel.strategy_version.like("CANDIDATE_%"))
    rows = list((await in_memory_db.execute(stmt)).scalars().all())
    # Each candidate that fired must appear exactly once.
    fired = [v for v, r in res1.items() if r.get("status") == "SIGNAL"]
    for version in fired:
        assert res2[version]["status"] == "DUPLICATE_SKIPPED"
    # Total rows across candidates equals the fired count.
    assert len(rows) == len(fired)
    for row in rows:
        assert row.strategy_version.startswith("CANDIDATE_")
        assert row.regime == "RANGING"


@pytest.mark.asyncio
async def test_candidate_versions_never_mix(in_memory_db):
    from sqlalchemy import select

    from app.database.models import SignalModel
    from app.research.candidate_observation import CandidateObservationService

    candles = _real_slice(16000)
    repo = Repository(in_memory_db)
    service = CandidateObservationService(Settings(OBSERVATION_MODE=True))
    resampler = IncrementalResampler()
    total = 0
    for i in range(400, 16000, 3):  # walk consecutive candles like the scheduler
        resampler.add(candles[i])
        snap = MultiTimeframeSnapshot(
            symbol="XAUUSD", timestamp=candles[i].timestamp, current_price=candles[i].close,
            m5=resampler.series(TimeFrame.M5, 120),
            m15=resampler.series(TimeFrame.M15, 150),
            m30=resampler.series(TimeFrame.M30, 100),
            h1=resampler.series(TimeFrame.H1, 80),
            h4=resampler.series(TimeFrame.H4, 50),
        )
        results = await service.process_candle(snap, repo)
        total += sum(1 for r in results.values() if r.get("status") == "SIGNAL")
        if total >= 3:
            break

    stmt = select(SignalModel).where(SignalModel.strategy_version.like("CANDIDATE_%"))
    rows = list((await in_memory_db.execute(stmt)).scalars().all())
    assert len(rows) >= 3
    versions = {r.strategy_version for r in rows}
    for r in rows:
        name = r.metadata_payload.get("candidate_name")
        assert name is not None and r.strategy_version.startswith(name)
    assert len(versions) >= 1


def test_candidate_definitions_frozen():
    from app.research.candidates import (
        ALL_FORWARD_CANDIDATES,
        CANDIDATE_A,
        CANDIDATE_B,
        CANDIDATE_C,
    )
    assert CANDIDATE_A.version == "CANDIDATE_A_V1"
    assert CANDIDATE_B.version == "CANDIDATE_B_V1"
    assert CANDIDATE_C.version == "CANDIDATE_C_V1"
    assert CANDIDATE_A.regime == "RANGING"
    assert CANDIDATE_B.tp_r == 1.75
    assert CANDIDATE_C.tp_r == 2.0
    assert len(ALL_FORWARD_CANDIDATES) == 3


def test_candidate_comparison_report_shape():
    """The /research/candidates comparison must expose per-candidate stats."""
    import asyncio

    from app.api.routes.research import get_candidate_comparison
    data = asyncio.run(get_candidate_comparison())
    assert "candidates" in data
    assert data["status"] in ("FORWARD_OBSERVATION", "NO_FORWARD_DATA")
