"""Manage admin accounts (separate from employee accounts).

    python -m app.admin_users add usman "A-strong-password" --name "Syed Usman Ali" --level super
    python -m app.admin_users add hrteam "Another-password" --name "HR Team" --level hr
    python -m app.admin_users list
    python -m app.admin_users passwd usman "New-password"
    python -m app.admin_users remove hrteam

Levels: super (CEO-level: can change access levels, edit the CEO record, delete employees) and hr.
"""
import argparse

from . import auth, db


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("username"); a.add_argument("password")
    a.add_argument("--name", default=None); a.add_argument("--level", choices=["super", "hr"], default="hr")
    sub.add_parser("list")
    p = sub.add_parser("passwd"); p.add_argument("username"); p.add_argument("password")
    r = sub.add_parser("remove"); r.add_argument("username")
    args = ap.parse_args()
    db.init_schema()
    if args.cmd == "add":
        if len(args.password) < 8:
            raise SystemExit("Password must be at least 8 characters.")
        db.create_admin(args.username, args.name or args.username, auth.hash_password(args.password), args.level)
        print(f"Admin '{args.username.lower()}' created ({args.level}).")
    elif args.cmd == "list":
        for x in db.list_admins():
            print(f"{x['username']:20} {x['level']:6} {x['full_name']}")
    elif args.cmd == "passwd":
        if len(args.password) < 8:
            raise SystemExit("Password must be at least 8 characters.")
        print("Password changed." if db.set_admin_password(args.username, auth.hash_password(args.password)) else "No such admin.")
    else:
        print("Removed." if db.delete_admin(args.username) else "No such admin.")
    db.close_pool()


if __name__ == "__main__":
    main()
