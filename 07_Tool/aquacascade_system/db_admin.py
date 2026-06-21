"""Database lifecycle CLI.

Run from 07_Tool:
  python -m aquacascade_system.db_admin migrate
  python -m aquacascade_system.db_admin seed
  python -m aquacascade_system.db_admin info
  python -m aquacascade_system.db_admin backup <backup.db>
  python -m aquacascade_system.db_admin restore <backup.db>
"""
import json
import sys

from .db import db_info, init_db, migrate_db, seed_db
from .ops import backup_sqlite, restore_sqlite


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    cmd = argv[0] if argv else "info"
    if cmd == "migrate":
        migrate_db()
        print(json.dumps(db_info(), indent=2, default=str))
    elif cmd == "seed":
        migrate_db()
        seed_db()
        print(json.dumps(db_info(), indent=2, default=str))
    elif cmd == "init":
        init_db()
        print(json.dumps(db_info(), indent=2, default=str))
    elif cmd == "info":
        init_db()
        print(json.dumps(db_info(), indent=2, default=str))
    elif cmd == "backup" and len(argv) == 2:
        print(json.dumps(backup_sqlite(argv[1]), indent=2, default=str))
    elif cmd == "restore" and len(argv) == 2:
        print(json.dumps(restore_sqlite(argv[1]), indent=2, default=str))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
