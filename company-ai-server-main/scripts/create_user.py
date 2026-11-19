r"""
Pehla admin / koi bhi user banao (password prompt se, history mein nahi jata).

  python scripts\create_user.py root --role admin                          (super admin, company nahi)
  python scripts\create_user.py boss_acme --role company_admin --company acme
  python scripts\create_user.py ali_acme --company acme                   (company ka normal user)
  python scripts\create_user.py ali_acme --reset-password

Company pehle banao: python scripts\create_company.py acme "Acme Surgical" --email info@acme.pk
Company users ki access company se nikalti hai (co_<slug>__*  +  shared reg__*); --collections sirf
purane company-less accounts ke liye hai.

--db wahi hona chahiye jo .env ka DATABASE_PATH hai (default data/company_ai.db).
Server band hona zaroori nahi (SQLite WAL). User banne ke baad auth automatically ON ho jata hai.
"""
import argparse
import asyncio
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.factory import open_accounts  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("--role", default="user", choices=["user", "company_admin", "admin"])
    ap.add_argument("--company", help="company slug (user / company_admin ke liye zaroori)")
    ap.add_argument("--collections", help="hr|finance  (sirf company-less legacy users; khali = kuch nahi)")
    ap.add_argument("--db", default=None, help="SQLite file (sirf jab DATABASE_URL khali ho); default DATABASE_PATH")
    ap.add_argument("--reset-password", action="store_true")
    args = ap.parse_args()

    password = getpass.getpass("Password (min 8 chars): ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match")

    opened = await open_accounts(args.db)
    repo = opened.repo

    try:
        exists = any(u["username"].lower() == args.username.lower() for u in await repo.list_users())

        if exists and args.reset_password:
            await repo.update_user(args.username, password=password)
            print(f"Password updated for '{args.username}' (purane sessions band).")
        elif exists:
            sys.exit(f"User '{args.username}' already exists (use --reset-password)")
        else:
            cols = args.collections.split("|") if args.collections else None
            user = await repo.create_user(args.username, password, args.role, cols, args.company)
            print(f"Created: {user['username']} role={user['role']} company={user['company']} collections={user['collections']}")
    except ValueError as exc:
        sys.exit(f"Error: {exc}")
    finally:
        await opened.close()


if __name__ == "__main__":
    asyncio.run(main())
