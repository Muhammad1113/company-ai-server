r"""
Nayi company (tenant) banao ya dekho.

  python scripts\create_company.py acme "Acme Surgical" --email info@acme.pk --max-users 20
  python scripts\create_company.py --list
  python scripts\create_company.py acme --disable      (company ke sab users foran logout + login band)
  python scripts\create_company.py acme --enable

Slug: chhote english harf/digits, beech mein single underscore (jaise sialkot_med). Baad mein badalna mushkil
hai kyunke company ki collection ka naam (co_<slug>__docs) isi se banta hai.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.factory import open_accounts  # noqa: E402


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", nargs="?")
    ap.add_argument("name", nargs="?")
    ap.add_argument("--email")
    ap.add_argument("--max-users", type=int, default=0, help="0 = unlimited")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--disable", action="store_true")
    ap.add_argument("--enable", action="store_true")
    ap.add_argument("--db", default=None, help="SQLite file (sirf jab DATABASE_URL khali ho); default DATABASE_PATH")
    args = ap.parse_args()

    opened = await open_accounts(args.db)
    repo = opened.repo
    try:
        if args.list or not args.slug:
            for c in await repo.list_companies():
                state = "DISABLED" if c["disabled"] else "active"
                print(f"{c['slug']:<20} {c['name']:<30} users={c['users']:<4} online={c['online']:<3} {state}  {c['collection']}")
        elif args.disable or args.enable:
            if not await repo.update_company(args.slug, disabled=args.disable):
                sys.exit("Company not found")
            print(f"{args.slug}: {'disabled' if args.disable else 'enabled'}")
        else:
            if not args.name:
                sys.exit("Company name required")
            c = await repo.create_company(args.slug, args.name, args.email, args.max_users)
            print(f"Created: {c['slug']} ({c['name']})  collection: co_{c['slug']}__docs")
    except ValueError as exc:
        sys.exit(f"Error: {exc}")
    finally:
        await opened.close()


if __name__ == "__main__":
    asyncio.run(main())
