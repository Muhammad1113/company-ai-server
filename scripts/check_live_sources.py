r"""
Asli internet par openFDA, eCFR, UK legislation.gov.uk aur EUR-Lex ka ek-ek chhota check (DB / RAG / admin ko haath nahi
lagata, kuch save nahi hota).

  python scripts/check_live_sources.py
  python scripts/check_live_sources.py --code GAD --part 820 --uk uksi/2002/618 --eu 32017R0745

Exit code 0 = saare sources ne sahi jawab diya. Yeh wohi OpenFDAClient / ECFRClient use karta hai jo server karta hai.
Internet / proxy / firewall ka masla ho to yahan pehle pata chalta hai, admin portal mein nahi.
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services import ecfr, eurlex, openfda, uklegislation  # noqa: E402


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


async def check_uk(code: str) -> bool:
    client = uklegislation.UKLegislationClient(settings.UK_LEGISLATION_BASE_URL, settings.UK_LEGISLATION_TIMEOUT)
    try:
        records, meta = await client.fetch_act(code)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] UK legislation: {type(exc).__name__}: {exc}")
        return False
    sample = ", ".join(r["data"]["section"] for r in records[:4])
    biggest = max(len(r["data"]["text"]) for r in records)
    print(f"[ OK ] UK legislation: {meta.get('title') or code} (modified {meta.get('modified')}): {len(records)} records "
          f"(e.g. {sample}), longest {biggest} chars, structure parsed={meta.get('parsed', True)}")
    return len(records) > 1 and meta.get("parsed", True)


def act_is_mdr(code: str) -> bool:
    return eurlex.act_id(eurlex.normalize_code(code)) == "32017R0745"


async def check_eu(code: str) -> bool:
    client = eurlex.EurLexClient(settings.EURLEX_BASE_URL, settings.EURLEX_SPARQL_URL, settings.EURLEX_TIMEOUT,
                                 language=settings.EURLEX_LANGUAGE)
    try:
        records, meta = await client.fetch_act(code)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] EUR-Lex: {type(exc).__name__}: {exc}")
        return False
    arts = [r for r in records if r["data"]["section"].startswith("Art ")]
    annexes = [r for r in records if r["data"]["section"].startswith("Annex")]
    rules = sorted({int(r["data"]["section"].rsplit(" ", 1)[1]) for r in records if r["data"]["section"].startswith("Annex VIII Rule ")})
    if act_is_mdr(code):
        missing = [n for n in range(1, eurlex.MAX_RULE + 1) if n not in rules]
        print(f"[{'FAIL' if missing else ' OK '}] EU MDR Annex VIII: {len(rules)}/{eurlex.MAX_RULE} classification rules split into "
              f"separate records" + (f"; missing Rule {missing}. Layout of Annex VIII changed? (check _RULE_HEAD in eurlex.py)" if missing else ""))
        if missing:
            return False
    print(f"[ OK ] EUR-Lex: CELEX {meta['celex']} (version {meta['as_of']}, consolidated={meta['consolidated']}): "
          f"{len(records)} records, {len(arts)} articles, {len(annexes)} annexes, structure parsed={meta.get('parsed', True)}")
    return len(arts) > 0 and meta.get("parsed", True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="GAD", help="FDA product code (3 letters/digits)")
    ap.add_argument("--part", default="820", help="CFR part number")
    ap.add_argument("--uk", default="uksi/2002/618", help="UK legislation code, jaise uksi/2002/618")
    ap.add_argument("--eu", default="32017R0745", help="EU CELEX number")
    args = ap.parse_args()
    results = [await check_openfda(args.code.upper()), await check_ecfr(args.part),
               await check_uk(args.uk), await check_eu(args.eu)]
    print("ALL OK" if all(results) else "SOME CHECKS FAILED")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
