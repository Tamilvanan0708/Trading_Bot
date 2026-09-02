"""
Candle-boundary analysis scheduler.

Runs on a background asyncio task and triggers the full analysis pipeline
shortly after every confirmed 15M candle close.  Handles restarts, missed
cycles, and provider disconnects safely.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config.settings import Settings, get_settings
from app.core.constants import AIValidationStatus, SignalDirection
from app.core.logging import logger
from app.data.live.service import LiveMarketDataAdaptor, LiveMarketDataService
from app.database.connection import async_session_factory
from app.database.repository import Repository
from app.market_regime.detector import MarketRegimeDetector
from app.news.filter import NewsFilter
from app.paper_trading.limits import TradingLimits
from app.risk.admission import TradeAdmissionGate
from app.services.pipeline import AnalysisPipeline
from app.services.status import SystemStatus, get_status


def _signal_payload_from_result(result: dict[str, Any]):
    """Rebuilds a SignalPayload from the pipeline's serialized result dict."""
    from app.core.constants import (
        MarketBias,
        SignalDirection,
        SignalQuality,
        StrategyType,
    )
    from app.signals.models import SignalPayload

    sig = result["signal"]
    try:
        strategy = StrategyType(sig["strategy"])
    except ValueError:
        strategy = StrategyType.CONFLUENCE
    try:
        direction = SignalDirection(sig["direction"])
    except ValueError:
        direction = SignalDirection.NO_TRADE
    try:
        quality = SignalQuality(sig["signal_quality"])
    except ValueError:
        quality = SignalQuality.NO_TRADE
    try:
        bias = MarketBias(sig["market_bias"])
    except ValueError:
        bias = MarketBias.NEUTRAL

    return SignalPayload(
        signal_id=sig["signal_id"],
        instrument=sig["instrument"],
        direction=direction,
        strategy=strategy,
        timeframe=sig.get("timeframe", "15m"),
        timestamp=result.get("timestamp"),
        entry=sig["entry"],
        stop_loss=sig["stop_loss"],
        take_profit_1=sig["take_profit_1"],
        take_profit_2=sig["take_profit_2"],
        take_profit_3=sig["take_profit_3"],
        risk_reward=sig["risk_reward"],
        confidence_score=sig["confidence_score"],
        signal_quality=quality,
        market_bias=bias,
        strategy_version=sig.get("strategy_version", ""),
        reasons=sig.get("reasons", []),
        invalidation_conditions=sig.get("invalidation_conditions", []),
        detected_structures=sig.get("detected_structures", {}),
        fibonacci_levels=sig.get("fibonacci_levels", {}),
        liquidity_levels=sig.get("liquidity_levels", []),
    )


class AnalysisScheduler:
    """Background scheduler that runs analysis after every closed 15M candle."""

    def __init__(
        self,
        live_service: LiveMarketDataService,
        settings: Settings | None = None,
    ) -> None:
        self.live_service = live_service
        self.settings = settings or get_settings()
        self.status: SystemStatus = get_status()
        self._task: asyncio.Task | None = None
        self._running = False
        self._last_processed_ts: datetime | None = None

        adaptor = LiveMarketDataAdaptor(live_service)
        self.pipeline = AnalysisPipeline(adaptor, self.settings)
        self.admission_gate = TradeAdmissionGate(self.settings)
        self.limits = TradingLimits(self.settings)
        self.regime_detector = MarketRegimeDetector(atr_period=self.settings.ATR_PERIOD)
        self.news_filter = NewsFilter(self.settings)
        self.outcome_tracker = None  # lazy: needs a DB session

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self.status.mark_started()
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="analysis-scheduler")
        logger.info("AnalysisScheduler started.")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("AnalysisScheduler stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — never crash the loop
                await self.status.mark_error(str(exc))
                logger.error("Scheduler tick error: %s", exc, exc_info=True)
            await asyncio.sleep(self.settings.SCHEDULER_POLL_INTERVAL)

    async def _tick(self) -> None:
        symbol = self.settings.DEFAULT_SYMBOL

        # Market-hours awareness: skip entirely during the weekend (XAUUSDT 24/5).
        if self.settings.MARKET_HOURS_UTC_ENABLED:
            from app.core.market_hours import is_market_open, next_market_open
            now = datetime.now(timezone.utc)
            if not is_market_open(now):
                await self.status.set_next_analysis(next_market_open(now))
                return

        # Observation mode: update hypothetical outcomes with the current live price.
        if self.settings.OBSERVATION_MODE:
            try:
                live_price = await self.live_service.get_latest_price(symbol)
                if live_price and live_price > 0:
                    from app.research.observation import get_observation_store
                    get_observation_store().update_price(symbol, live_price)
            except Exception as obs_exc:  # noqa: BLE001
                logger.debug("Observation update skipped: %s", obs_exc)

        # If degraded (e.g. stale history / disconnect), attempt an immediate
        # REST refresh (cooldown-gated) so the system can self-heal.
        try:
            dq = await self.live_service.data_quality()
            if dq.degraded:
                await self._alert_if_degraded(dq)
                self._was_degraded = True
            else:
                # Recovery alert: only fire after a real degraded episode.
                if getattr(self, "_was_degraded", False):
                    self._was_degraded = False
                    await self._alert_recovered(dq)
            if dq.degraded and self.settings.LIVE_HISTORY_REFRESH_ON_DEGRADED:
                refresh = await self.live_service.request_emergency_refresh()
                if refresh.get("status") == "SUCCESS":
                    logger.info(
                        "[SAFETY] Emergency history refresh succeeded; re-evaluating data quality."
                    )
                elif refresh.get("status") == "SKIPPED":
                    pass  # cooldown — no log spam
        except Exception as exc:  # noqa: BLE001
            await self.status.mark_error(f"Emergency refresh failed: {exc}")
            logger.warning("Scheduler: emergency refresh error %s", exc)

        try:
            snap = await self.live_service.get_multi_timeframe_snapshot(
                symbol, include_forming=False
            )
        except ValueError as exc:
            logger.debug("Scheduler: no closed candle yet (%s).", exc)
            return
        except Exception as exc:
            await self.status.mark_error(f"Snapshot fetch failed: {exc}")
            logger.warning("Scheduler: snapshot error %s", exc)
            return

        latest_candle = snap.m15[-1]
        now = datetime.now(timezone.utc)

        # Candle is confirmed closed only when its window has fully elapsed
        candle_close = latest_candle.timestamp + timedelta(minutes=15)
        ready_at = candle_close + timedelta(seconds=self.settings.ANALYSIS_OFFSET_SECONDS)
        if now < ready_at:
            await self.status.set_next_analysis(ready_at)
            return

        # Dedup: skip candles already processed this run
        if self._last_processed_ts is not None and latest_candle.timestamp <= self._last_processed_ts:
            return

        # Restart safety: skip the current candle on first tick by default
        if self._last_processed_ts is None and not self.settings.PROCESS_LAST_CLOSED_ON_START:
            self._last_processed_ts = latest_candle.timestamp
            await self.status.mark_candle(latest_candle.timestamp)
            logger.info("Scheduler: initial skip of candle %s (restart).", latest_candle.timestamp)
            return

        await self._run_analysis(snap, latest_candle)

    async def _run_analysis(self, snap, latest_candle) -> None:
        logger.info("Scheduler: processing candle %s ...", latest_candle.timestamp)
        await self.status.mark_candle(latest_candle.timestamp)
        # Mark the candle as processed BEFORE the pipeline runs.  A failure
        # mid-pipeline then cannot cause the same candle to be re-processed
        # (and re-alerted to Telegram) on every subsequent tick.
        self._last_processed_ts = latest_candle.timestamp
        symbol = self.settings.DEFAULT_SYMBOL

        async with async_session_factory() as session:
            repo = Repository(session)

            # 0. Forward-outcome tracker: restore once, then update open signals
            #    with the latest closed candles (before this candle's analysis).
            if self.outcome_tracker is None:
                from app.research.outcome_tracker import SignalOutcomeTracker
                self.outcome_tracker = SignalOutcomeTracker(self.settings)
                await self.outcome_tracker.restore(repo)
            if self.outcome_tracker.open_count:
                await self.outcome_tracker.update(snap.m15, repo=repo)

            # 1. Full pipeline (signal + AI + persistence)
            result = await self.pipeline.run_full_analysis(symbol=symbol, db_session=session)
            signal_payload = _signal_payload_from_result(result)
            ai_validation = result["ai_validation"]
            await self.status.mark_analysis(
                signal_payload.confidence_score, signal_payload.direction.value
            )
            if signal_payload.direction != SignalDirection.NO_TRADE:
                await self.status.mark_signal(signal_payload.signal_id)

            # 2. Market regime
            regime = self.regime_detector.analyze(snap.m15)
            result["market_regime"] = regime.model_dump()

            # 2a. Register the new signal for forward-outcome tracking and stamp
            #     regime / session / strategy version + full intelligence
            #     metadata on the persisted row.
            if signal_payload.direction != SignalDirection.NO_TRADE:
                try:
                    session_name = self._session_name(signal_payload)
                    metadata = self._build_signal_metadata(
                        result, signal_payload, regime, ai_validation, session_name
                    )
                    await repo.update_signal_outcome(signal_payload.signal_id, {
                        "regime": regime.regime.value,
                        "session": session_name,
                        "strategy_version": signal_payload.strategy_version,
                        "outcome": "OPEN",
                        "metadata_payload": metadata,
                    })
                    self.outcome_tracker.register(signal_payload)
                    if self.outcome_tracker.open_count:
                        await self.outcome_tracker.update(snap.m15, repo=repo)
                except Exception as tracker_exc:  # noqa: BLE001
                    logger.warning("Signal outcome registration failed: %s", tracker_exc)

            # 2b. FORWARD-OBSERVATION CANDIDATES (independent of production):
            #     evaluate Candidate A/B/C on the same closed candle, persist
            #     versioned signals, track outcomes.  Never affects production.
            try:
                if getattr(self, "_candidate_service", None) is None:
                    from app.research.candidate_observation import (
                        CandidateObservationService,
                    )
                    self._candidate_service = CandidateObservationService(self.settings)
                    self._candidate_service.outcome_tracker = self.outcome_tracker  # share tracker
                    await self._candidate_service.restore(repo)
                cand_results = await self._candidate_service.process_candle(snap, repo)
                # Optional Telegram alerts for candidate signals
                if self.settings.CANDIDATE_TELEGRAM_ALERTS_ENABLED and self.settings.TELEGRAM_ENABLED:
                    for version, res in cand_results.items():
                        if res.get("status") == "SIGNAL":
                            await self._send_candidate_alert(res, session)
            except Exception as cand_exc:  # noqa: BLE001
                logger.warning("Candidate observation failed: %s", cand_exc)

            # 3. AI admission status
            ai_ok = ai_validation["status"] == AIValidationStatus.APPROVE.value
            if not ai_ok and self.settings.AUTO_TRADE_ON_CAUTION:
                ai_ok = ai_validation["status"] == AIValidationStatus.CAUTION.value

            # 4. Admission gate (authoritative, explainable)
            has_conflicting = self._has_conflicting_position(signal_payload)
            is_duplicate = await self._is_duplicate_signal(repo, latest_candle.timestamp)
            data_quality = await self.live_service.data_quality()
            result["data_quality"] = data_quality.model_dump(mode="json")
            decision = await self.admission_gate.evaluate(
                signal=signal_payload,
                repo=repo,
                snapshot_timestamp=latest_candle.timestamp,
                market_data_fresh=data_quality.historical_fresh,
                candle_closed=True,
                regime=regime,
                ai_status_ok=ai_ok,
                has_conflicting_position=has_conflicting,
                is_duplicate=is_duplicate,
                data_quality=data_quality,
                account_balance=self.pipeline.paper_service.balance,
                initial_balance=self.pipeline.paper_service.initial_balance,
            )
            result["admission"] = {
                "allowed": decision.allowed,
                "reasons": decision.reasons,
            }

            # 4a. Persist the admission decision on the signal (audit trail).
            if signal_payload.direction != SignalDirection.NO_TRADE:
                try:
                    await repo.stamp_signal_metadata(signal_payload.signal_id, {
                        "admission_allowed": decision.allowed,
                        "admission_reasons": list(decision.reasons),
                    })
                except Exception as stamp_exc:  # noqa: BLE001
                    logger.warning("Signal admission stamp failed: %s", stamp_exc)

            # 5. Open paper trade only when admission passes AND the strategy is
            #    not classified FAILED (safety) AND not in observation mode.
            trade_blocked_reason = None
            if self.settings.OBSERVATION_MODE:
                trade_blocked_reason = "OBSERVATION MODE enabled — hypothetical only, no paper trades."
            elif self.settings.BLOCK_PAPER_TRADING_ON_FAILED_STRATEGY and self._strategy_failed():
                trade_blocked_reason = "SAFETY: strategy classified FAILED — automatic paper trading blocked."

            # Alert: paper trading blocked
            if trade_blocked_reason and trade_blocked_reason != "OBSERVATION MODE enabled — hypothetical only, no paper trades.":
                await self._alert_paper_blocked(trade_blocked_reason)

            # Alert: strategy status (grade change detection)
            await self._alert_strategy_status(self._read_strategy_grade())

            # Alert: signal rejection (throttled)
            if not decision.allowed and signal_payload.direction != SignalDirection.NO_TRADE:
                await self._alert_signal_rejected(signal_payload, decision)

            # 5a. Observation mode: record the hypothetical signal (no trade).
            if self.settings.OBSERVATION_MODE and decision.allowed and signal_payload.direction != SignalDirection.NO_TRADE:
                try:
                    from app.research.observation import get_observation_store
                    get_observation_store().record_signal({
                        "instrument": signal_payload.instrument,
                        "timestamp": signal_payload.timestamp.isoformat() if signal_payload.timestamp else None,
                        "price": signal_payload.entry,
                        "direction": signal_payload.direction.value,
                        "score": signal_payload.confidence_score,
                        "entry": signal_payload.entry,
                        "stop_loss": signal_payload.stop_loss,
                        "take_profit_1": signal_payload.take_profit_1,
                        "take_profit_2": signal_payload.take_profit_2,
                        "take_profit_3": signal_payload.take_profit_3,
                        "regime": regime.regime.value if regime else "unknown",
                        "session": self._session_name(signal_payload),
                        "ai_status": ai_validation.get("status"),
                        "strategy_grade": self._read_strategy_grade(),
                    })
                    logger.info("[OBSERVATION] Recorded hypothetical signal %s @ %.2f",
                                signal_payload.direction.value, signal_payload.entry)
                except Exception as obs_exc:  # noqa: BLE001
                    logger.error("[OBSERVATION] Failed to record signal: %s", obs_exc)

            if self.settings.PAPER_TRADING_ENABLED and decision.allowed and trade_blocked_reason is None:
                opened = await self.pipeline.paper_service.open_position_from_signal(
                    signal_payload, repo=repo
                )
                if opened is not None:
                    await self.status.mark_paper_trade(opened.position_id)
                    await self.limits.register_trade_opened(repo)
                    logger.info(
                        "Paper trade opened: %s %s @ $%.2f (id=%s)",
                        opened.direction.value, opened.symbol, opened.target_entry, opened.position_id,
                    )
                else:
                    logger.info("Paper trade not opened (sizing/rejection).")
            elif trade_blocked_reason is not None:
                logger.info("[SAFETY] %s", trade_blocked_reason)
                result["admission"]["trade_blocked_reason"] = trade_blocked_reason
            elif not decision.allowed:
                logger.info("Admission rejected: %s", decision.rejected_reason)

            # 6. Telegram dispatch for tradable signals (with full metadata),
            #    subject to the daily signal cap (research risk metric).
            if signal_payload.direction != SignalDirection.NO_TRADE:
                if self._daily_signal_cap_reached():
                    logger.info("MAX_SIGNALS_PER_DAY reached — signal %s stored but alert suppressed.",
                                signal_payload.signal_id)
                else:
                    await self._send_signal_alert_with_metadata(result, signal_payload, ai_validation, regime, session=session)

            # ------------------------------------------------------------------
            # 5a. CUSTOM USER STRATEGIES INTEGRATION (Fib With Retracement & SMC With Fib)
            #     Cascades 5m -> 15m -> 30m -> 1h (and 4h for SMC).
            #     Each filled tranche layer is opened as its own paper trade at a
            #     strict 0.01 lots (3-Tranche Fib / 2-Tranche SMC).
            # ------------------------------------------------------------------
            try:
                # 1. FIB WITH RETRACEMENT (Dual-Direction Bullish & Bearish 5m -> 15m -> 30m -> 1h)
                from app.retracement.multi_tf import get_retracement_multi_tf_service
                fib_svc = get_retracement_multi_tf_service(symbol)
                fib_states = await fib_svc.advance(session)

                for tf_name in ["5m"]:
                    setup_obj = fib_states.get(tf_name)
                    if not setup_obj:
                        continue
                    layers = getattr(setup_obj, "layers", {}) or {}
                    if not layers:
                        continue
                    dir_str = str(getattr(setup_obj, "direction", "LONG")).upper()

                    # Open one paper trade per FILLED tranche layer (0.01 lots each).
                    for layer_key, layer in sorted(layers.items()):
                        if layer.get("state") not in ("FILLED", "TP_HIT", "ESCAPE_CLOSED"):
                            continue
                        entry_px = layer.get("entry_price")
                        sl_px = layer.get("sl")
                        tp_px = layer.get("tp")
                        if not entry_px or not sl_px or not tp_px:
                            logger.warning("[FIB-RETRACEMENT] Layer %s on %s missing levels; skipping.", layer_key, tf_name)
                            continue

                        sig_id = f"FIB_RETR_{tf_name.upper()}_{layer_key}_{int(latest_candle.timestamp.timestamp())}"
                        existing_sig = await repo.get_signal_by_id(sig_id)
                        if existing_sig is not None:
                            continue

                        from app.core.constants import MarketBias, SignalQuality, StrategyType
                        from app.signals.models import SignalPayload

                        custom_sig = SignalPayload(
                            signal_id=sig_id,
                            instrument=symbol,
                            direction=SignalDirection.LONG if dir_str == "LONG" else SignalDirection.SHORT,
                            strategy=StrategyType.RETRACEMENT,
                            timeframe=tf_name,
                            timestamp=latest_candle.timestamp,
                            entry=entry_px,
                            stop_loss=sl_px,
                            take_profit_1=tp_px,
                            take_profit_2=tp_px,
                            take_profit_3=tp_px,
                            risk_reward=round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                            confidence_score=0.95,
                            signal_quality=SignalQuality.VERY_STRONG,
                            market_bias=MarketBias.BULLISH if dir_str == "LONG" else MarketBias.BEARISH,
                            strategy_version="RETRACEMENT_BOS_V2",
                            reasons=[
                                f"Fib BOS {layer_key} @ {layer.get('entry_ratio')} retracement "
                                f"on {tf_name.upper()} ({dir_str}), 0.01 lots"
                            ],
                        )
                        # AI VALIDATION GATE
                        try:
                            ai_val = await self.pipeline.ai_validator.validate_signal(custom_sig)
                            custom_sig.reasons.append(
                                f"AI Verdict: {ai_val.status.value} (conf={ai_val.confidence:.0f}%) — {ai_val.explanation}"
                            )
                            if ai_val.status.value == "REJECT":
                                logger.warning("[AI-GATE] Fib Retracement %s REJECTED by AI: %s", sig_id, ai_val.explanation)
                                await repo.save_signal(custom_sig.model_dump(mode="json"))
                                continue
                        except Exception as ai_exc:  # noqa: BLE001
                            logger.warning("[AI-GATE] Validation call error: %s", ai_exc)

                        await repo.save_signal(custom_sig.model_dump(mode="json"))

                        if self.settings.PAPER_TRADING_ENABLED:
                            opened_pos = await self.pipeline.paper_service.open_position_from_signal(
                                custom_sig, repo=repo, fixed_lot_size=0.01
                            )
                            if opened_pos:
                                logger.info(
                                    "[FIB-RETRACEMENT] Opened paper trade %s %s %s on %s @ %.2f 0.01 lots (id=%s)",
                                    dir_str, symbol, layer_key, tf_name, entry_px, opened_pos.position_id,
                                )
                    break

                # 2. SMC WITH FIB (Golden Pocket 0.680 Single Entry, 0.920 SL, 0.000 TP) — Single Position (0.01 lots)
                from app.retracement.smc_fib_multi_tf import get_smc_fib_multi_tf_service
                smc_svc = get_smc_fib_multi_tf_service(symbol)
                smc_states = await smc_svc.advance(session)

                for tf_name in ["5m"]:
                    st_card = smc_states.get(tf_name) or {}
                    if not st_card.get("is_entry_touched"):
                        continue
                    entry_px = st_card.get("entry", {}).get("price")
                    sl_px = st_card.get("sl", {}).get("price")
                    tp_px = st_card.get("tp", {}).get("locked") or st_card.get("tp", {}).get("dynamic")
                    p2 = st_card.get("point_2", {}).get("price")
                    if not entry_px or not sl_px or not tp_px:
                        continue
                    dir_str = str(st_card.get("direction", "SHORT")).upper()

                    sig_id = f"SMC_FIB_{tf_name.upper()}_{int(p2 or 0)}"
                    existing_sig = await repo.get_signal_by_id(sig_id)
                    if existing_sig is not None:
                        continue

                    from app.core.constants import MarketBias, SignalQuality, StrategyType
                    from app.signals.models import SignalPayload

                    custom_sig = SignalPayload(
                        signal_id=sig_id,
                        instrument=symbol,
                        direction=SignalDirection.LONG if dir_str == "LONG" else SignalDirection.SHORT,
                        strategy=StrategyType.SMC,
                        timeframe=tf_name,
                        timestamp=latest_candle.timestamp,
                        entry=entry_px,
                        stop_loss=sl_px,
                        take_profit_1=tp_px,
                        take_profit_2=tp_px,
                        take_profit_3=tp_px,
                        risk_reward=round(abs(tp_px - entry_px) / max(0.1, abs(entry_px - sl_px)), 2),
                        confidence_score=0.95,
                        signal_quality=SignalQuality.VERY_STRONG,
                        market_bias=MarketBias.BULLISH if dir_str == "LONG" else MarketBias.BEARISH,
                        strategy_version="SMC_WITH_FIB_V1",
                        reasons=[
                            f"SMC 0.680 Golden Pocket Single Entry on {tf_name.upper()} ({dir_str}), 0.01 lots"
                        ],
                    )

                    # AI VALIDATION GATE
                    try:
                        ai_val = await self.pipeline.ai_validator.validate_signal(custom_sig)
                        custom_sig.reasons.append(
                            f"AI Verdict: {ai_val.status.value} (conf={ai_val.confidence:.0f}%) — {ai_val.explanation}"
                        )
                        if ai_val.status.value == "REJECT":
                            logger.warning("[AI-GATE] SMC With Fib %s REJECTED by AI: %s", sig_id, ai_val.explanation)
                            await repo.save_signal(custom_sig.model_dump(mode="json"))
                            break
                    except Exception as ai_exc:  # noqa: BLE001
                        logger.warning("[AI-GATE] Validation call error: %s", ai_exc)

                    await repo.save_signal(custom_sig.model_dump(mode="json"))

                    if self.settings.PAPER_TRADING_ENABLED:
                        opened_pos = await self.pipeline.paper_service.open_position_from_signal(
                            custom_sig, repo=repo, fixed_lot_size=0.01
                        )
                        if opened_pos:
                            logger.info(
                                "[SMC-FIB] Opened single paper trade %s %s on %s @ %.2f 0.01 lots (id=%s)",
                                dir_str, symbol, tf_name, entry_px, opened_pos.position_id,
                            )
                    break
            except Exception as strat_sched_exc:  # noqa: BLE001
                logger.warning("[STRATEGY-SCHED] auto paper trade integration exception: %s", strat_sched_exc)

            await session.commit()
            self._last_processed_ts = latest_candle.timestamp
            logger.info("Scheduler: finished candle %s.", latest_candle.timestamp)

    async def _send_candidate_alert(self, res: dict, session) -> None:
        """Sends a Telegram alert for a candidate forward-observation signal."""
        try:
            from app.core.constants import (
                MarketBias,
                SignalDirection,
                SignalQuality,
                StrategyType,
            )
            from app.research.candidates import candidate_by_version
            from app.signals.models import SignalPayload

            version = res["candidate"]
            cand = candidate_by_version(version)
            signal = SignalPayload(
                signal_id=res["signal_id"],
                instrument="XAUUSD",
                direction=SignalDirection(res["direction"]),
                strategy=StrategyType.CONFLUENCE,
                timeframe=cand.mtf.trigger.value if cand else "5m",
                timestamp=datetime.now(timezone.utc),
                entry=res["entry"],
                stop_loss=res["stop_loss"],
                take_profit_1=res["take_profit_1"],
                take_profit_2=res.get("take_profit_2", res["take_profit_1"]),
                take_profit_3=res.get("take_profit_3", res["take_profit_1"]),
                risk_reward=cand.tp_r if cand else 1.75,
                confidence_score=res["confidence_score"],
                signal_quality=SignalQuality.STRONG,
                market_bias=MarketBias.BULLISH if res["direction"] == "LONG" else MarketBias.BEARISH,
                strategy_version=version,
                reasons=[f"{version} setup, {res.get('regime', '')} regime"],
            )
            await self.pipeline.telegram_service.send_signal_alert(
                signal, None, repo=Repository(session),
                metadata={
                    "market_regime": res.get("regime"),
                    "session": res.get("session"),
                    "htf_bias_4h": "",
                    "structure_1h": "",
                    "setup_tf": "MTF",
                    "setup_state": "CONFIRMED",
                    "trigger_tf": cand.mtf.trigger.value if cand else "5m",
                    "trigger_state": "CONFIRMED",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Candidate Telegram alert failed: %s", exc)

    def _has_conflicting_position(self, signal) -> bool:
        for pos in self.pipeline.paper_service.get_active_positions():
            if pos.direction == signal.direction:
                return True
        return False

    def _daily_signal_cap_reached(self) -> bool:
        """True when MAX_SIGNALS_PER_DAY (>0) signals have already been alerted today."""
        cap = self.settings.MAX_SIGNALS_PER_DAY
        if cap <= 0:
            return False
        today = datetime.now(timezone.utc).date()
        if getattr(self, "_signal_cap_date", None) != today:
            self._signal_cap_date = today
            self._signal_cap_count = 0
        return self._signal_cap_count >= cap

    async def _send_signal_alert_with_metadata(self, result, signal_payload, ai_validation, regime, session=None) -> None:
        """Sends a tradable-signal Telegram alert with full metadata and counts the cap."""
        from app.ai.models import AIValidationResult
        ai_res = AIValidationResult(
            status=ai_validation["status"],
            confidence=ai_validation["confidence"],
            explanation=ai_validation["explanation"],
            identified_risks=ai_validation.get("identified_risks", []),
            missing_confirmations=ai_validation.get("missing_confirmations", []),
        )
        meta = {
            "market_regime": regime.regime.value if regime else "UNKNOWN",
            "session": self._session_name(signal_payload),
            "htf_bias_4h": (result.get("market_bias", {}).get("4h", {}) or {}).get("trend"),
            "structure_1h": (result.get("market_bias", {}).get("1h", {}) or {}).get("trend"),
            "setup_tf": signal_payload.timeframe,
            "setup_state": "CONFIRMED" if signal_payload.confidence_score >= self.settings.THRESHOLD_STRONG else "WEAK",
            "trigger_tf": signal_payload.timeframe,
            "trigger_state": "CONFIRMED" if signal_payload.confidence_score >= self.settings.THRESHOLD_STRONG else "WEAK",
        }
        try:
            if session is not None:
                # Reuse the caller's session to avoid a second SQLite writer
                # (nested sessions can trigger 'database is locked').
                from app.database.repository import Repository
                await self.pipeline.telegram_service.send_signal_alert(
                    signal_payload, ai_res, repo=Repository(session), metadata=meta
                )
            else:
                from app.database.connection import async_session_factory
                from app.database.repository import Repository
                async with async_session_factory() as session:
                    await self.pipeline.telegram_service.send_signal_alert(
                        signal_payload, ai_res, repo=Repository(session), metadata=meta
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram signal alert failed: %s", exc)
        # Count the cap regardless of send outcome
        if getattr(self, "_signal_cap_date", None) == datetime.now(timezone.utc).date():
            self._signal_cap_count = getattr(self, "_signal_cap_count", 0) + 1

    @staticmethod
    def _session_name(signal) -> str:
        try:
            from app.research.session import _session_for
            ts = signal.timestamp
            if ts is None:
                return "UNKNOWN"
            return _session_for(ts.hour)
        except Exception:
            return "UNKNOWN"

    @staticmethod
    def _build_signal_metadata(result, signal, regime, ai_validation, session_name: str) -> dict:
        """Builds the auditable signal-intelligence metadata payload.

        Captures the full decision trail: HTF bias, structure, SMC, Fibonacci,
        confluence breakdown, market regime, session, AI status and the
        admission decision so every signal is traceable from candle to outcome.
        """
        mb = result.get("market_bias", {})
        smc = result.get("smc_analysis", {})
        fib = result.get("fibonacci_setup")
        conf = result.get("confluence", {})
        admission = result.get("admission", {})
        try:
            fib_compact = {
                "direction": fib.get("direction"),
                "in_golden_pocket": fib.get("in_golden_pocket"),
                "active_level_ratio": fib.get("active_level_ratio"),
                "entry_zone_min": fib.get("entry_zone_min"),
                "entry_zone_max": fib.get("entry_zone_max"),
            } if isinstance(fib, dict) else None
        except Exception:  # noqa: BLE001
            fib_compact = None
        try:
            smc_compact = {
                "current_zone": smc.get("current_zone"),
                "equilibrium_price": smc.get("equilibrium_price"),
                "active_fvg_count": len(smc.get("active_fvgs", []) or []),
                "active_ob_count": len(smc.get("active_order_blocks", []) or []),
                "recent_sweep_count": len(smc.get("recent_sweeps", []) or []),
            } if isinstance(smc, dict) else None
        except Exception:  # noqa: BLE001
            smc_compact = None
        return {
            "strategy_version": signal.strategy_version,
            "market_regime": regime.regime.value if regime else "UNKNOWN",
            "session": session_name,
            "htf_bias_4h": (mb.get("4h", {}) or {}).get("trend"),
            "structure_1h": (mb.get("1h", {}) or {}).get("trend"),
            "trigger_15m": (mb.get("15m", {}) or {}).get("trend"),
            "fibonacci": fib_compact,
            "smc": smc_compact,
            "confluence_score": signal.confidence_score,
            "confluence_quality": signal.signal_quality.value if signal.signal_quality else None,
            "confluence_breakdown": (conf.get("breakdown") or {}) if isinstance(conf, dict) else {},
            "ai_status": ai_validation.get("status") if isinstance(ai_validation, dict) else None,
            "admission_allowed": admission.get("allowed"),
            "admission_reasons": admission.get("reasons", []),
        }

    def _strategy_failed(self) -> bool:
        """Reads the latest research classification; True if the strategy is FAILED."""
        try:
            grade = self._read_strategy_grade()
            return grade == "FAILED"
        except Exception:
            return False

    @staticmethod
    def _read_strategy_grade(path: str = "data/research/latest_report.json") -> str:
        """Reads the strategy grade from a research report file."""
        import json
        import os
        if not os.path.exists(path):
            return "INCONCLUSIVE"  # no research yet — do not assume failed
        with open(path, encoding="utf-8") as f:
            report = json.load(f)
        return report.get("classification", {}).get("grade", "INCONCLUSIVE")

    async def _alert_if_degraded(self, dq) -> None:
        """Send a Telegram degradation alert once per event (1h cooldown)."""
        if not self.settings.TELEGRAM_ENABLED:
            return
        key = "alert:data_quality_degraded"
        cooldown = 3600  # 1 hour
        try:
            from app.database.connection import async_session_factory
            from app.database.repository import Repository
            async with async_session_factory() as session:
                repo = Repository(session)
                last = await repo.get_system_state(key, "")
                now = datetime.now(timezone.utc)
                if last:
                    last_dt = datetime.fromisoformat(last)
                    if (now - last_dt).total_seconds() < cooldown:
                        return  # dedup — not a new event
                await repo.set_system_state(key, now.isoformat())
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Degradation alert dedup check failed: %s", exc)
            return

        msg = (
            "⚠️ DATA QUALITY DEGRADED\n"
            f"Reason: {dq.degradation_reason}\n"
            f"Live price: {dq.live_price}\n"
            "Automatic paper trading is BLOCKED."
        )
        await self.pipeline.telegram_service.send_raw_alert(msg)

    async def _alert_recovered(self, dq) -> None:
        """Send a Telegram recovery alert after a degraded episode ends."""
        if not self.settings.TELEGRAM_ENABLED:
            return
        msg = (
            "✅ DATA QUALITY RECOVERED\n"
            f"Candles: {dq.candle_count} | Gaps: {dq.gap_count} | Duplicates: {dq.duplicate_count}\n"
            "The system has self-healed and normal signal processing resumes."
        )
        await self.pipeline.telegram_service.send_typed_alert(
            "data_quality_recovered", msg, cooldown_seconds=3600
        )

    async def _alert_paper_blocked(self, reason: str) -> None:
        """Send a paper-trading-blocked alert (once daily to avoid spam)."""
        if not self.settings.TELEGRAM_ENABLED:
            return
        msg = f"🚫 PAPER TRADING BLOCKED\n{reason}\nNo automatic paper trades will open."
        await self.pipeline.telegram_service.send_typed_alert(
            "paper_trading_blocked", msg, cooldown_seconds=86400
        )

    async def _alert_strategy_status(self, grade: str) -> None:
        """Send a strategy-status alert when the classification grade changes."""
        if not self.settings.TELEGRAM_ENABLED:
            return
        prev = getattr(self, "_last_notified_grade", None)
        if prev == grade:
            return
        self._last_notified_grade = grade
        msg = (
            f"🧬 STRATEGY STATUS: {grade}\n"
            "The research classification changed. See /research/classification "
            "and data/research/latest_report.json for details."
        )
        await self.pipeline.telegram_service.send_typed_alert(
            f"strategy_status_{grade}", msg, cooldown_seconds=3600
        )

    async def _alert_signal_rejected(self, signal, decision) -> None:
        """Send a throttled rejection alert (max one per 30 minutes)."""
        if not self.settings.TELEGRAM_ENABLED:
            return
        reasons = decision.reasons[:3] if decision.reasons else [decision.rejected_reason or "unknown"]
        msg = (
            f"⛔ SIGNAL REJECTED — {signal.direction.value} {signal.confidence_score:.0f}/100\n"
            + "\n".join(f"• {r}" for r in reasons)
        )
        await self.pipeline.telegram_service.send_typed_alert(
            "signal_rejected", msg, cooldown_seconds=1800
        )

    async def _is_duplicate_signal(self, repo: Repository, candle_ts: datetime) -> bool:
        existing = await repo.list_recent_signals_by_candle(candle_ts)
        return len(existing) > 0

    async def next_analysis_time(self) -> datetime | None:
        next_str = await self.status._get("next_analysis_at")
        if next_str:
            try:
                return datetime.fromisoformat(next_str)
            except ValueError:
                pass
        return None