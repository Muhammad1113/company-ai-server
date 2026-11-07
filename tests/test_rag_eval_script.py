"""rag_eval.py ko ek fake HTTP server (real RagService + HashingEmbedder) se test karta hai."""
import asyncio
import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.rag.embeddings import HashingEmbedder
from app.rag.service import RagService
from app.rag.store import JsonVectorStore

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "rag_eval", ROOT / "scripts" / "rag_eval.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _server(throttle_first=False):
    embedder = HashingEmbedder(512)
    store = JsonVectorStore(None, embedder.id)

    async def gen(*a, **k):
        return {}

    svc = RagService(embedder, store, gen, top_k=5)
    state = {"throttled": False, "auth": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["auth"].append(self.headers.get("Authorization"))

            if throttle_first and not state["throttled"]:
                state["throttled"] = True
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"detail":"slow down"}')
                return

            if self.path.endswith("/ingest"):
                out = asyncio.run(svc.ingest(body["doc_id"], body["text"], body["collection"]))
            else:
                hits = asyncio.run(svc.search(body["question"], body["collection"], body["top_k"]))
                out = {"embedder": embedder.id, "hits": [
                    {"doc_id": h["doc_id"], "chunk_index": h["chunk_index"],
                     "score": h["score"], "preview": h["text"][:50]} for h in hits]}

            payload = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, state


def test_eval_script_end_to_end_on_sample_data():
    httpd, state = _server()
    try:
        script = _load_script()
        result = script.run_eval(
            f"http://127.0.0.1:{httpd.server_port}", "secret-key",
            ROOT / "eval/sample/docs", ROOT / "eval/sample/questions.jsonl",
            "eval", k=5, quiet=True,
        )
    finally:
        httpd.shutdown()

    assert result["questions"] == 8
    assert result["hit@3"] >= 0.75           # hashing embedder (lexical) bhi ye de sakta hai
    assert 0 < result["mrr"] <= 1
    assert set(state["auth"]) == {"Bearer secret-key"}


def test_eval_script_retries_on_429():
    httpd, state = _server(throttle_first=True)
    try:
        script = _load_script()
        result = script.run_eval(
            f"http://127.0.0.1:{httpd.server_port}", "",
            ROOT / "eval/sample/docs", ROOT / "eval/sample/questions.jsonl",
            "eval", k=5, quiet=True,
        )
    finally:
        httpd.shutdown()

    assert state["throttled"] is True
    assert result["questions"] == 8
