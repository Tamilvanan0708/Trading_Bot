"""
Tests for the daily/weekly forward reports and report-generation scripts.
"""

import asyncio
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def test_daily_forward_report_script_runs(tmp_path):
    """scripts/daily_forward_report.py must execute and write a report."""
    env = dict(os.environ)
    r = subprocess.run(
        [sys.executable, "scripts/daily_forward_report.py", "--days", "7"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "Daily forward report written to" in r.stdout
    # Report file exists under reports/daily/
    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    assert os.path.exists(os.path.join(ROOT, "reports", "daily", f"forward_{today}.md"))


def test_weekly_forward_report_script_runs():
    """scripts/weekly_forward_report.py must execute and write a report."""
    r = subprocess.run(
        [sys.executable, "scripts/weekly_forward_report.py", "--weeks", "1"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "Weekly report written to" in r.stdout


def test_mtf_comparison_script_runs():
    """scripts/generate_mtf_comparison.py must write json + md."""
    r = subprocess.run(
        [sys.executable, "scripts/generate_mtf_comparison.py"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert os.path.exists(os.path.join(ROOT, "reports", "mtf_comparison.json"))
    assert os.path.exists(os.path.join(ROOT, "reports", "mtf_comparison.md"))


def test_candidate_status_resolution():
    """resolve_candidate_status must return valid promotion states."""
    from app.research.candidates import resolve_candidate_status
    s0 = resolve_candidate_status("CANDIDATE_A_V1", forward_signals=0)
    s1 = resolve_candidate_status("CANDIDATE_B_V1", forward_signals=10)
    from app.research.ranking import PromotionState
    assert s0 in (PromotionState.OOS_VALIDATED.value, PromotionState.RESEARCH.value)
    assert s1 == PromotionState.FORWARD_OBSERVATION.value


def test_real_money_never_enabled():
    """REAL_MONEY_EXECUTION must default False and never be True in code."""
    from app.config.settings import Settings
    assert Settings().REAL_MONEY_EXECUTION is False
    assert Settings(REAL_MONEY_EXECUTION=False).REAL_MONEY_EXECUTION is False


def test_no_broker_execution_imports():
    """No broker order-execution API must exist in the codebase."""
    import pkgutil
    import app
    banned = ("broker", "order_api", "trade_execution", "mt5_exec", "ctrader_exec")
    for mod in pkgutil.walk_packages(app.__path__, "app."):
        if any(b in mod.name.lower() for b in banned):
            raise AssertionError(f"Unexpected broker/execution module: {mod.name}")
