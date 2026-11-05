"""Consistent online backup of the telemetry database (safe while the collector is running).

    python scripts/backup_db.py                      # to ./backups, keep 14
    python scripts/backup_db.py D:\\Backups\\isp 30   # to another drive, keep 30
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ispmon.config import ROOT, load_settings  # noqa: E402


def main() -> int:
    settings = load_settings()
    if not settings.db_path.exists():
        print(f"Database not found: {settings.db_path}", file=sys.stderr)
        return 1
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "backups"
    keep = int(sys.argv[2]) if len(sys.argv) > 2 else 14
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"isp_telemetry_{datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(settings.db_path)
    dest = sqlite3.connect(target)
    with dest:
        source.backup(dest)
    source.close()
    dest.close()
    check = sqlite3.connect(target)
    ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    check.close()
    if not ok:
        print(f"Backup {target} FAILED the integrity check", file=sys.stderr)
        return 1
    for old in sorted(target_dir.glob("isp_telemetry_*.db"))[:-keep]:
        old.unlink()
    print(f"Backup written: {target} ({target.stat().st_size / 1e6:.1f} MB), keeping the newest {keep}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
