"""
Load test: kitne log ek saath chala sakte hain? (asli chat endpoint par, SSE streaming ke saath)

Kya naapta hai: pehla token kitni der mein aaya (TTFT), poora jawab kitni der mein, tokens/s, aur
429 (queue full / per-user cap), 504 (queue timeout), 502 (model down) kitni dafa.

IMPORTANT: server har user ko max INFERENCE_MAX_PER_CLIENT=2 ek saath deta hai. Ek hi account se 10 "users" simulate
karoge to 429 aayega (design ke mutabiq). Isliye ye script har virtual user ke liye alag test account banata hai
(admin login chahiye), aur --cleanup par unhein disable kar deta hai.

Chalane ka tareeqa (PowerShell, backend folder se):
  python scripts/load_test.py --base http://127.0.0.1:8000/api/v1 --admin-user admin --admin-pass "XXXX" --users 1,2,4,8 --duration 90 --cleanup

Sirf pehle 2 minute ka quick check:
  python scripts/load_test.py --admin-user admin --admin-pass "XXXX" --users 1,3 --duration 40

Nateeje ko GPU wali machine par hi chalao, aur koi aur heavy kaam (games, training) saath na chalao.
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from dataclasses import dataclass

import httpx

QUESTIONS = [
    "What sample size for a lot of 500 pieces at level II?",
    "scalpel handle #4 par kaun si blade lagti hai",
    "What is the difference between Kelly and Rochester-Pean forceps?",
    "gloves ka HS code kya hai",
    "Can I cut sutures with Metzenbaum scissors?",
    "Which stainless steel is used for scissors and why?",
    "What does the STERILE EO symbol mean?",
    "15 Fr in mm",
    "What is EU MDR?",
    "What is the difference between FOB and CIF?",
    "Which suture lasts longer, plain catgut or chromic catgut?",
    "What documents does a buyer usually ask for with medical items?",
    "Do we have FDA approval?",
    "needle holder kya hota hai aur kahan use hota hai",
    "How do I calculate air freight volumetric weight?",
    "What is a critical defect in AQL inspection?",
]


@dataclass
class Result:
    status: int = 0
    ok: bool = False
    ttft: float | None = None
    total: float = 0.0
    chunks: int = 0
    tps: float | None = None
    error: str | None = None


def pctl(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return values[k]


async def login(c: httpx.AsyncClient, base: str, user: str, password: str) -> str:
    """Server login par 10/min per IP ki limit lagata hai: 429 par Retry-After ruk kar dobara koshish."""
    for _ in range(40):
        r = await c.post(f"{base}/auth/login", json={"username": user, "password": password})
        if r.status_code == 429:
            await asyncio.sleep(float(r.headers.get("Retry-After", "6")) + 0.5)
            continue
        r.raise_for_status()
        return r.json()["token"]
    raise RuntimeError(f"login {user}: bar bar 429")


async def make_users(c, base, admin_token, prefix, count, password):
    names = [f"{prefix}{i:02d}" for i in range(1, count + 1)]
    h = {"Authorization": f"Bearer {admin_token}"}
    for n in names:
        r = await c.post(f"{base}/admin/users", headers=h,
                         json={"username": n, "password": password, "role": "user", "collections": None})
        if r.status_code not in (200, 201):
            # pehle se maujood ho sakta hai: password reset + enable
            await c.patch(f"{base}/admin/users/{n}", headers=h, json={"password": password, "disabled": False})
    return names


async def disable_users(c, base, admin_token, names):
    h = {"Authorization": f"Bearer {admin_token}"}
    for n in names:
        await c.patch(f"{base}/admin/users/{n}", headers=h, json={"disabled": True})


async def new_conversation(c, base, token) -> str:
    r = await c.post(f"{base}/conversations", headers={"Authorization": f"Bearer {token}"}, json={})
    r.raise_for_status()
    return r.json()["id"]


async def one_chat(c, base, token, conv_id, question, max_tokens) -> Result:
    res = Result()
    t0 = time.perf_counter()
    first = None
    try:
        async with c.stream(
            "POST", f"{base}/conversations/{conv_id}/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": question, "collection": "*", "max_tokens": max_tokens},
        ) as r:
            res.status = r.status_code
            if r.status_code != 200:
                await r.aread()
                res.total = time.perf_counter() - t0
                return res
            event = None
            async for line in r.aiter_lines():
                if line == "":
                    event = None
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                if event == "error":
                    res.error = data[:120]
                    continue
                if event in ("sources", "status"):
                    continue
                try:
                    j = json.loads(data)
                    delta = ((j.get("choices") or [{}])[0].get("delta") or {}).get("content")
                except (ValueError, AttributeError):
                    continue
                if delta:
                    if first is None:
                        first = time.perf_counter()
                    res.chunks += 1
    except httpx.HTTPError as e:
        res.error = repr(e)[:120]
    end = time.perf_counter()
    res.total = end - t0
    if first is not None:
        res.ttft = first - t0
        if res.chunks > 1 and end > first:
            res.tps = res.chunks / (end - first)
    res.ok = res.status == 200 and res.error is None and res.chunks > 0
    return res


async def virtual_user(base, token, stage_end, think, max_tokens, results, limits):
    async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0), limits=limits) as c:
        conv, asked = None, 0
        await asyncio.sleep(random.uniform(0, think))          # sab ek hi lamhe par na takrayein
        while time.perf_counter() < stage_end:
            if conv is None or asked >= 4:                       # history badhne se prompt lamba na ho jaye
                conv, asked = await new_conversation(c, base, token), 0
            q = random.choice(QUESTIONS)
            res = await one_chat(c, base, token, conv, q, max_tokens)
            results.append(res)
            asked += 1
            if res.status == 429:
                await asyncio.sleep(5)                           # Retry-After ka ehtiram
            else:
                await asyncio.sleep(random.uniform(think * 0.5, think * 1.5))


def summarize(users, duration_s, results, failed_users=0):
    n = len(results)
    ok = [r for r in results if r.ok]
    by = lambda code: sum(1 for r in results if r.status == code)
    other = sum(1 for r in results if not r.ok and r.status not in (429, 502, 504))
    ttft = [r.ttft for r in ok if r.ttft is not None]
    total = [r.total for r in ok]
    tps = [r.tps for r in ok if r.tps]
    return {
        "users": users, "failed_users": failed_users, "requests": n, "ok%": 100 * len(ok) / n if n else 0,
        "429": by(429), "504": by(504), "502": by(502), "other": other,
        "ttft_p50": pctl(ttft, 50), "ttft_p95": pctl(ttft, 95),
        "total_p50": pctl(total, 50), "total_p95": pctl(total, 95),
        "tok/s": statistics.median(tps) if tps else float("nan"),
        "ok_req/min": len(ok) / (duration_s / 60) if duration_s else 0,
    }


def print_table(rows):
    cols = ["users", "failed_users", "requests", "ok%", "429", "504", "502", "other",
            "ttft_p50", "ttft_p95", "total_p50", "total_p95", "tok/s", "ok_req/min"]
    print("\n" + " ".join(f"{c:>10}" for c in cols))
    for r in rows:
        print(" ".join(f"{r[c]:>10.1f}" if isinstance(r[c], float) else f"{r[c]:>10}" for c in cols))
    print("\nPadhne ka tareeqa:")
    print("  ok% 95+ aur ttft_p95 < 20s  -> is user count par system theek hai")
    print("  504 aaye                     -> queue mein 60s se zyada intezar (INFERENCE_QUEUE_TIMEOUT)")
    print("  429 aaye                     -> queue full / ek user ke 2 se zyada requests")
    print("  502 aaye                     -> llama-server ne jawab nahi diya (crash/restart)")
    print("  ok_req/min ka maximum        -> aap ke system ki asli capacity (model + GPU ke saath)")
    print("  failed_users 0 se zyada      -> un virtual users ka test hi nahi chala (neeche error dekho), nateeja adhura")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--admin-user", required=True)
    ap.add_argument("--admin-pass", required=True)
    ap.add_argument("--users", default="1,2,4,8", help="comma separated stage sizes, jaise 1,2,4,8")
    ap.add_argument("--duration", type=int, default=90, help="har stage ke seconds")
    ap.add_argument("--think", type=float, default=6.0, help="sawalon ke beech average sochne ka waqt (sec)")
    ap.add_argument("--max-tokens", type=int, default=300)
    ap.add_argument("--prefix", default="loadtest")
    ap.add_argument("--password", default="LoadTest!2026pw")
    ap.add_argument("--cleanup", action="store_true", help="khatam par test accounts disable karo")
    a = ap.parse_args()

    stages = [int(x) for x in a.users.split(",") if x.strip()]
    biggest = max(stages)
    limits = httpx.Limits(max_connections=biggest * 2 + 4)

    async with httpx.AsyncClient(timeout=30.0) as c:
        base_root = a.base.rsplit("/api/", 1)[0]
        r = await c.get(f"{base_root}/ready")
        print(f"/ready -> {r.status_code} {r.text[:150]}")
        if r.status_code != 200:
            print("Server ready nahi hai (llama-server chal raha hai?). Pehle usay theek karo."); sys.exit(1)
        admin = await login(c, a.base, a.admin_user, a.admin_pass)
        names = await make_users(c, a.base, admin, a.prefix, biggest, a.password)
        print(f"{len(names)} test accounts tayyar ({names[0]} ... {names[-1]})")

        print("test users login ho rahe hain (server login limit ki wajah se thoda waqt lag sakta hai)...", flush=True)
        tokens = []
        for i, nm in enumerate(names, 1):
            tokens.append(await login(c, a.base, nm, a.password))
            print(f"  login {i}/{len(names)}", end="\r", flush=True)
        print()

        rows = []
        try:
            for n in stages:
                print(f"\n--- stage: {n} users, {a.duration}s ---", flush=True)
                results: list[Result] = []
                t_start = time.perf_counter()
                stage_end = t_start + a.duration
                tasks = [asyncio.create_task(
                    virtual_user(a.base, tokens[i], stage_end, a.think, a.max_tokens, results, limits))
                    for i in range(n)]
                outcome = await asyncio.gather(*tasks, return_exceptions=True)
                errs = [o for o in outcome if isinstance(o, Exception)]
                if errs:
                    print(f"  !! {len(errs)} virtual user(s) crash hue: {errs[0]!r}")
                rows.append(summarize(n, time.perf_counter() - t_start, results, len(errs)))
                print_table(rows[-1:])
                await asyncio.sleep(10)                       # stage ke darmiyan queue khali hone do
        finally:
            if a.cleanup:
                await disable_users(c, a.base, admin, names)
                print("\ntest accounts disable kar diye.")
    print("\n===== KHULASA =====")
    print_table(rows)


if __name__ == "__main__":
    asyncio.run(main())
