"""
RAG retrieval eval (LLM use nahi hota, sirf /rag/search).

  $env:COMPANY_AI_KEY = "<admin key>"       # auth on ho to
  python scripts\\rag_eval.py --docs eval\\sample\\docs --questions eval\\sample\\questions.jsonl --collection eval

questions.jsonl: har line {"question": "...", "doc_id": "<file name bina extension>"}
Metrics: hit@1, hit@3 (document level), MRR, misses ki list.
Model compare karne ke liye: har model ka alag RAG_STORE_PATH rakho, server
restart karo, wahi command dobara chalao.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _post(base, path, payload, key, retries=6):
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    for attempt in range(retries):
        req = urllib.request.Request(
            base.rstrip("/") + path, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(float(exc.headers.get("Retry-After", "1")))
                continue
            raise SystemExit(f"HTTP {exc.code} on {path}: {body}")
        except urllib.error.URLError as exc:
            raise SystemExit(f"Cannot reach {base}: {exc.reason}")


def run_eval(base, key, docs_dir, questions_file, collection, k=5, quiet=False):
    log = (lambda *a: None) if quiet else print

    if docs_dir:
        for path in sorted(Path(docs_dir).glob("*")):
            if path.suffix.lower() in {".txt", ".md"}:
                out = _post(base, "/api/v1/rag/ingest", {
                    "doc_id": path.stem,
                    "text": path.read_text(encoding="utf-8"),
                    "collection": collection,
                }, key)
                log(f"ingested {path.name}: {out['chunks']} chunks")

    rows = [
        json.loads(line)
        for line in Path(questions_file).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    hit1 = hit3 = 0
    mrr = 0.0
    misses = []
    embedder = None
    latencies = []

    for row in rows:
        started = time.perf_counter()
        out = _post(base, "/api/v1/rag/search", {
            "question": row["question"], "collection": collection, "top_k": k,
        }, key)
        latencies.append((time.perf_counter() - started) * 1000)
        embedder = out.get("embedder")

        ranked = []
        for hit in out["hits"]:
            if hit["doc_id"] not in ranked:
                ranked.append(hit["doc_id"])

        expected = row["doc_id"]
        if expected in ranked:
            rank = ranked.index(expected) + 1
            mrr += 1.0 / rank
            hit1 += rank == 1
            hit3 += rank <= 3
        else:
            rank = None

        if rank != 1:
            misses.append({"question": row["question"], "expected": expected,
                           "got": ranked[:3], "rank": rank})

    n = len(rows) or 1
    result = {
        "embedder": embedder,
        "questions": len(rows),
        "hit@1": round(hit1 / n, 3),
        "hit@3": round(hit3 / n, 3),
        "mrr": round(mrr / n, 3),
        "avg_search_ms": round(sum(latencies) / n, 1),
        "misses": misses,
    }
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--docs", help="folder of .txt/.md to ingest first")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--collection", default="eval")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    result = run_eval(
        args.base, os.environ.get("COMPANY_AI_KEY", ""),
        args.docs, args.questions, args.collection, args.k,
    )

    print("\n=== RESULT ===")
    for name in ("embedder", "questions", "hit@1", "hit@3", "mrr", "avg_search_ms"):
        print(f"{name:14} {result[name]}")
    if result["misses"]:
        print("\nNot at rank 1:")
        for m in result["misses"]:
            print(f"  - {m['question']!r}\n      expected={m['expected']} got={m['got']} rank={m['rank']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
