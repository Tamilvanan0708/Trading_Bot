"""
Daily SQLite database backup (WAL-safe).

Uses SQLite's online backup API so the copy is transactionally consistent
even while the application is writing (WAL mode).  Keeps the most recent N
backups and prunes older ones.  Safe to run from cron / Task Scheduler.

Usage:
  python scripts/backup_db.py [--db data/xauusd_agent.db] [--keep 7]
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = "data/xauusd_agent.db"
DEFAULT_BACKUP_DIR = "data/backups"


def backup_sqlite(src: str, dest: str) -> None:
    """Copies a SQLite database using the online backup API (WAL-safe)."""
    src_conn = sqlite3.connect(src)
    try:
        dest_conn = sqlite3.connect(dest)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backup the SQLite database (WAL-safe).")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"Database not found: {args.db}")
        sys.exit(1)

    os.makedirs(args.backup_dir, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(args.backup_dir, f"xauusd_{date}.db")

    # Also capture the WAL state if present (rarely needed with backup API,
    # but harmless and makes restores unambiguous).
    wal_path = args.db + "-wal"
    if os.path.exists(wal_path):
        try:
            dest_wal = dest + "-wal"
            with open(wal_path, "rb") as f_src, open(dest_wal, "wb") as f_dst:
                f_dst.write(f_src.read())
        except OSError as exc:
            print(f"WARNING: could not copy WAL file: {exc}")

    try:
        backup_sqlite(args.db, dest)
    except Exception as exc:  # noqa: BLE001
        print(f"Backup FAILED: {exc}")
        sys.exit(1)
    print(f"Backup written: {dest}")

    # Prune old backups (keep last N)
    backups = sorted(
        f for f in os.listdir(args.backup_dir)
        if f.startswith("xauusd_") and f.endswith(".db")
    )
    while len(backups) > args.keep:
        old = backups.pop(0)
        os.remove(os.path.join(args.backup_dir, old))
        print(f"Pruned old backup: {old}")


if __name__ == "__main__":
    main()
