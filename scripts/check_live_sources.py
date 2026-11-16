r"""
Asli internet par openFDA aur eCFR ka ek-ek chhota check (DB / RAG / admin ko haath nahi lagata, kuch save nahi hota).

  python scripts/check_live_sources.py
  python scripts/check_live_sources.py --code GAD --part 820

Exit code 0 = dono sources ne sahi jawab diya. Yeh wohi OpenFDAClient / ECFRClient use karta hai jo server karta hai.
Internet / proxy / firewall ka masla ho to yahan pehle pata chalta hai, admin portal mein nahi.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services import ecfr, openfda  # noqa: E402


async def check_openfda(code: str) -> bool:
    client = openfda.OpenFDAClient(settings.OPENFDA_BASE_URL, settings.OPENFDA_API_KEY, 3, settings.OPENFDA_TIMEOUT)
    try:
        records = await client.fetch("classification", code)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] openFDA: {type(exc).__name__}: {exc}")
        return False
    if not records:
        print(f"[WARN] openFDA reachable, but no classification found for product code {code}")
        return False
    d = records[0]["data"]
    print(f"[ OK ] openFDA: {code} -> {d.get('device_name', '?')} (class {d.get('device_class', '?')})")
    return True


async def check_ecfr(part: str) -> bool:
    client = ecfr.ECFRClient(settings.ECFR_BASE_URL, settings.ECFR_TITLE, settings.ECFR_TIMEOUT)
    try:
        as_of = await client.as_of_date()
        records = await client.fetch_part(part, as_of)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] eCFR: {type(exc).__name__}: {exc}")
        return False
    sections = [r for r in records if r["data"].get("section", "")[:1].isdigit()]
    sample = ", ".join(r["data"]["section"] for r in sections[:3])
    print(f"[ OK ] eCFR: Title {client.title} Part {part} as of {as_of}: {len(records)} records, "
          f"{len(sections)} numbered sections (e.g. {sample})")
    return len(sections) > 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="GAD", help="FDA product code (3 letters/digits)")
    ap.add_argument("--part", default="820", help="CFR part number")
    args = ap.parse_args()
    results = [await check_openfda(args.code.upper()), await check_ecfr(args.part)]
    print("ALL OK" if all(results) else "SOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
