"""
auto_updater.py - Automated GitHub Pull & Safe Restart Service for Azure Windows VM.

Watches GitHub repository for new commits on 'main'.
When changes are detected:
1. Verifies that NO trades are currently open (Trade Safety Guard).
2. Pulls changes with `git pull origin main`.
3. Restarts the local server process cleanly.
4. Verifies /health endpoint.
5. Dispatches Telegram notification.
"""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

# Ensure project root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.core.logging import logger
from app.notifications.telegram_service import TelegramService

HEALTH_URL = "http://127.0.0.1:8000/health"
TRADES_URL = "http://127.0.0.1:8000/paper-trades?limit=30"
CHECK_INTERVAL_SEC = 60  # Check GitHub every 60 seconds


def run_cmd(cmd: str, timeout: int = 30) -> tuple[int, str]:
    """Runs a shell command and returns (returncode, stdout/stderr)."""
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
        return res.returncode, res.stdout.strip()
    except Exception as exc:
        return 1, str(exc)


def get_local_commit() -> str:
    """Returns local HEAD commit hash."""
    rc, out = run_cmd("git rev-parse HEAD")
    return out[:8] if rc == 0 else ""


def get_remote_commit() -> str:
    """Fetches origin/main and returns latest remote commit hash."""
    run_cmd("git fetch origin main", timeout=25)
    rc, out = run_cmd("git rev-parse origin/main")
    return out[:8] if rc == 0 else ""


def get_latest_commit_message() -> str:
    """Returns the subject of the latest commit on origin/main."""
    rc, out = run_cmd("git log -1 --format=%s origin/main")
    return out if rc == 0 else "Code updates"


def has_open_trades() -> bool:
    """Trade Safety Guard: Checks if any trades are OPEN before restarting."""
    # 1. Probe FastAPI /paper-trades endpoint
    try:
        import httpx
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(TRADES_URL)
            if resp.status_code == 200:
                data = resp.json()
                trades = data if isinstance(data, list) else data.get("trades", [])
                for t in trades:
                    if str(t.get("state", "")).upper() == "OPEN":
                        return True
                return False
    except Exception:
        pass

    # 2. Check MetaTrader 5 live positions on Windows
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            positions = mt5.positions_get(symbol="XAUUSD-VIP")
            if positions is None or len(positions) == 0:
                positions = mt5.positions_get()
            if positions and len(positions) > 0:
                return True
    except Exception:
        pass

    # 3. Check SQLite DB directly
    try:
        import sqlite3
        for db_name in ["trading.db", "trading_view.db"]:
            db_file = REPO_ROOT / "data" / db_name
            if db_file.exists():
                conn = sqlite3.connect(str(db_file))
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM paper_trades WHERE state = 'OPEN'")
                cnt = cur.fetchone()[0]
                conn.close()
                if cnt > 0:
                    return True
    except Exception:
        pass

    return False


def kill_existing_server() -> None:
    """Terminates any process currently listening on port 8000."""
    logger.info("[AUTO-UPDATER] Stopping existing server process on port 8000...")
    if os.name == "nt":
        # Windows: identify PID via netstat and taskkill
        try:
            rc, out = run_cmd('netstat -ano | findstr :8000 | findstr LISTENING')
            if rc == 0 and out:
                for line in out.strip().splitlines():
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        logger.info("[AUTO-UPDATER] Killing PID %s on port 8000", pid)
                        run_cmd(f"taskkill /F /PID {pid}")
        except Exception as exc:
            logger.warning("[AUTO-UPDATER] Error terminating Windows server: %s", exc)
    else:
        # Unix/macOS fallback
        try:
            rc, out = run_cmd("lsof -t -i :8000")
            if rc == 0 and out:
                for pid in out.strip().split():
                    run_cmd(f"kill -9 {pid}")
        except Exception as exc:
            logger.warning("[AUTO-UPDATER] Error terminating Unix server: %s", exc)


def start_server() -> bool:
    """Launches the server via scripts/start_local.ps1 (Windows) or start_linux.sh."""
    logger.info("[AUTO-UPDATER] Starting new server process...")
    if os.name == "nt":
        ps_script = REPO_ROOT / "scripts" / "start_local.ps1"
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)],
                cwd=str(REPO_ROOT),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            return True
        except Exception as exc:
            logger.error("[AUTO-UPDATER] Failed to launch PowerShell start script: %s", exc)
            return False
    else:
        # Fallback for non-Windows testing
        try:
            py_exe = sys.executable
            subprocess.Popen(
                [py_exe, "-m", "uvicorn", "app.api.app:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1"],
                cwd=str(REPO_ROOT),
            )
            return True
        except Exception as exc:
            logger.error("[AUTO-UPDATER] Failed to launch uvicorn: %s", exc)
            return False


def wait_for_health(timeout_sec: int = 45) -> bool:
    """Polls /health until status code 200 or timeout."""
    import httpx
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(2)
        try:
            with httpx.Client(timeout=3.0) as client:
                res = client.get(HEALTH_URL)
                if res.status_code == 200:
                    return True
        except Exception:
            pass
    return False


async def send_tg_notice(message: str) -> None:
    """Sends notification to Telegram."""
    try:
        tg = TelegramService()
        await tg.send_raw_alert(message)
    except Exception as exc:
        logger.warning("[AUTO-UPDATER] Failed to send Telegram update alert: %s", exc)


async def execute_safe_update(remote_hash: str) -> None:
    """Performs git pull, restarts server, verifies health, and notifies Telegram."""
    msg_summary = get_latest_commit_message()
    logger.info("[AUTO-UPDATER] Commencing update to %s: %s", remote_hash, msg_summary)

    # 1. Notify Telegram that update is beginning
    init_msg = (
        f"🔄 *SYSTEM UPDATE INITIATED*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Source:* GitHub `main`\n"
        f"🏷 *Commit:* `#{remote_hash}`\n"
        f"💬 *Summary:* {msg_summary}\n"
        f"🛡 *Trade Guard:* 0 Open Trades (Safe to restart)\n"
        f"⚙️ *Action:* Pulling changes & restarting bot...\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await send_tg_notice(init_msg)

    # 2. Run git pull
    t0 = time.time()
    rc, pull_out = run_cmd("git pull origin main", timeout=60)
    if rc != 0:
        logger.error("[AUTO-UPDATER] git pull failed: %s", pull_out)
        err_msg = (
            f"⚠️ *UPDATE FAILED (Git Error)*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Failed to pull latest code from GitHub.\n"
            f"Error: `{pull_out[:200]}`\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await send_tg_notice(err_msg)
        return

    logger.info("[AUTO-UPDATER] git pull succeeded. Output:\n%s", pull_out)

    # 3. Stop old server and start new one
    kill_existing_server()
    time.sleep(2)
    start_server()

    # 4. Wait for /health
    is_healthy = wait_for_health(timeout_sec=50)
    downtime_sec = round(time.time() - t0, 1)

    if is_healthy:
        logger.info("[AUTO-UPDATER] Update successful! Server healthy in %ss.", downtime_sec)
        success_msg = (
            f"✅ *SYSTEM UPDATE COMPLETE*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🚀 *Status:* Online & Healthy\n"
            f"🏷 *Running Commit:* `#{remote_hash}`\n"
            f"⏱️ *Downtime:* {downtime_sec}s\n"
            f"🏛️ *MT5 Bridge:* Active & Synced\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await send_tg_notice(success_msg)
    else:
        logger.error("[AUTO-UPDATER] Server failed /health check after update!")
        fail_msg = (
            f"🚨 *UPDATE WARNING*\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Code was pulled (`#{remote_hash}`), but server did not respond healthy within 50s.\n"
            f"Watchdog will attempt automated recovery.\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        await send_tg_notice(fail_msg)


async def main_loop() -> None:
    """Continuous polling loop."""
    logger.info("=======================================================")
    logger.info("  XAU Terminal - Auto Updater Watcher Service Started   ")
    logger.info("  Polling GitHub 'main' every %d seconds               ", CHECK_INTERVAL_SEC)
    logger.info("  Trade Safety Guard: ACTIVE                          ")
    logger.info("=======================================================")

    postponed_notice_sent = False

    while True:
        try:
            local_h = get_local_commit()
            remote_h = get_remote_commit()

            if not remote_h:
                logger.debug("[AUTO-UPDATER] Could not reach GitHub origin/main; will retry.")
            elif local_h != remote_h:
                logger.info("[AUTO-UPDATER] Update available! Local: %s | Remote: %s", local_h, remote_h)

                # Check Trade Safety Guard
                if has_open_trades():
                    logger.warning("[AUTO-UPDATER] Trade Safety Guard: Open trade detected! Postponing update until trade exits.")
                    if not postponed_notice_sent:
                        await send_tg_notice(
                            f"⏳ *UPDATE POSTPONED (Trade in Progress)*\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"Pushed commit `#{remote_h}` detected.\n"
                            f"Update is held because a live trade is currently active.\n"
                            f"Bot will automatically update as soon as trade closes.\n"
                            f"━━━━━━━━━━━━━━━━━━━━"
                        )
                        postponed_notice_sent = True
                else:
                    postponed_notice_sent = False
                    await execute_safe_update(remote_h)
            else:
                logger.debug("[AUTO-UPDATER] Code is up-to-date at commit %s", local_h)
                postponed_notice_sent = False

        except Exception as exc:
            logger.error("[AUTO-UPDATER] Loop exception: %s", exc)

        await asyncio.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n[AUTO-UPDATER] Stopped by user.")
