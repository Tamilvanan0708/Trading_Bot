"""
Tests for the live observation store (OBSERVATION MODE).
"""


from app.research.observation import LiveObservationStore


def test_observation_record_and_summary(tmp_path):
    store = LiveObservationStore(str(tmp_path / "obs.json"))
    store.record_signal({
        "instrument": "XAUUSD", "direction": "LONG", "entry": 4000.0,
        "stop_loss": 3990.0, "take_profit_2": 4010.0, "price": 4000.0,
        "score": 80.0, "regime": "TRENDING", "session": "LONDON",
    })
    store.record_signal({
        "instrument": "XAUUSD", "direction": "SHORT", "entry": 4000.0,
        "stop_loss": 4010.0, "take_profit_2": 3990.0, "price": 4000.0,
        "score": 75.0, "regime": "RANGING", "session": "NEW_YORK",
    })
    s = store.summary()
    assert s["total_signals"] == 2
    assert s["long"] == 1
    assert s["short"] == 1
    assert s["closed"] == 0


def test_observation_updates_mfe_mae_and_outcome(tmp_path):
    store = LiveObservationStore(str(tmp_path / "obs2.json"))
    store.record_signal({
        "instrument": "XAUUSD", "direction": "LONG", "entry": 4000.0,
        "stop_loss": 3990.0, "take_profit_2": 4012.0, "price": 4000.0,
        "score": 80.0,
    })
    # price rises to TP (4012): MFE = 12/10 = 1.2R, outcome TP
    store.update_price("XAUUSD", 4012.0)
    r = store._records[0]
    assert r["outcome"] == "TP"
    assert r["hypothetical_pnl_r"] == 1.2
    assert r["mfe_r"] == 1.2


def test_observation_sl_outcome(tmp_path):
    store = LiveObservationStore(str(tmp_path / "obs3.json"))
    store.record_signal({
        "instrument": "XAUUSD", "direction": "LONG", "entry": 4000.0,
        "stop_loss": 3990.0, "take_profit_2": 4012.0, "price": 4000.0,
        "score": 80.0,
    })
    store.update_price("XAUUSD", 3990.0)
    r = store._records[0]
    assert r["outcome"] == "SL"
    assert r["hypothetical_pnl_r"] == -1.0


def test_observation_persistence(tmp_path):
    path = tmp_path / "obs_persist.json"
    store = LiveObservationStore(str(path))
    store.record_signal({"instrument": "XAUUSD", "direction": "LONG", "entry": 4000.0, "stop_loss": 3990.0, "take_profit_2": 4012.0, "price": 4000.0, "score": 80.0})
    # New store instance loads from disk
    store2 = LiveObservationStore(str(path))
    assert store2.summary()["total_signals"] == 1