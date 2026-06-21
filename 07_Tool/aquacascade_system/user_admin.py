"""Trusted local user/tenant administration CLI.

Run from 07_Tool:
  python -m aquacascade_system.user_admin tenants
  python -m aquacascade_system.user_admin users
  python -m aquacascade_system.user_admin create-user <tenant_id> <role> <name>
  python -m aquacascade_system.user_admin rotate-token <user_id>
  python -m aquacascade_system.user_admin deactivate <user_id>
  python -m aquacascade_system.user_admin activate <user_id>

Create/rotate commands print the bearer token once. Store it in the
deployment's secret manager or hand it to the operator over a secure path.
"""
import json
import sys

from .db import init_db
from . import models as M


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    if not argv:
        print(__doc__)
        return 2
    init_db()
    cmd = argv[0]
    try:
        if cmd == "tenants":
            _print(M.list_tenants())
        elif cmd == "users":
            _print(M.list_users())
        elif cmd == "create-user" and len(argv) >= 4:
            tenant_id = int(argv[1])
            role = argv[2]
            name = " ".join(argv[3:])
            _print(M.create_user(tenant_id, name, role))
        elif cmd == "rotate-token" and len(argv) == 2:
            _print(M.rotate_user_token(int(argv[1])))
        elif cmd == "deactivate" and len(argv) == 2:
            _print(M.set_user_active(int(argv[1]), False))
        elif cmd == "activate" and len(argv) == 2:
            _print(M.set_user_active(int(argv[1]), True))
        else:
            print(__doc__)
            return 2
    except (PermissionError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
