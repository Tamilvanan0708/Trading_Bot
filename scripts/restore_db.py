"""
SQLite database restore with safety confirmation.

Restores a backup file over the live database.  The application should be
stopped before restoring to avoid data races.

Usage:
  python scripts/restore_db.py --backup data/backups/xauusd_20260101_120000.db
  python scripts/restore_db.py --backup data/backups/xauusd_20260101_120000.db --yes
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

DEFAULT_DB = "data/xauusd_agent.db"
DEFAULT_BACKUP_DIR = "data/backups"


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore the SQLite database from a backup.")
    parser.add_argument("--backup", required=True, help="Path to the backup .db file.")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    parser.add_argument("--yes", action="store_true", help="Skip interactive confirmation.")
    args = parser.parse_args()

    if not os.path.exists(args.backup):
        print(f"Backup file not found: {args.backup}")
        sys.exit(1)

    # Safety: never restore onto a live DB without a pre-restore snapshot.
    if os.path.exists(args.db):
        pre_restore = os.path.join(
            args.backup_dir,
            f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
        )
        os.makedirs(args.backup_dir, exist_ok=True)
        shutil.copy2(args.db, pre_restore)
        print(f"Pre-restore snapshot written: {pre_restore}")

    print(f"Restore target: {args.db}")
    print(f"Backup source : {args.backup}")
    if not args.yes:
        answer = input("This REPLACES the live database. Continue? [y/N]: ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            sys.exit(1)

    try:
        shutil.copy2(args.backup, args.db)
    except Exception as exc:  # noqa: BLE001
        print(f"Restore FAILED: {exc}")
        sys.exit(1)

    # Remove any stale WAL/SHM so the restored file opens cleanly.
    for suffix in ("-wal", "-shm"):
        stale = args.db + suffix
        if os.path.exists(stale):
            os.remove(stale)

    print(f"Restore complete: {args.db}")


if __name__ == "__main__":
    main()
