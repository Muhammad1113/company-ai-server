import asyncio

from app.rag.embeddings import LlamaCppEmbedder


class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


class _FakeClient:
    def __init__(self, shuffle=False, drop=False):
        self.calls = []
        self.shuffle = shuffle
        self.drop = drop

    async def post(self, url, json):
        inputs = json["input"]
        self.calls.append(list(inputs))
        data = [
            {"index": i, "embedding": [float(len(t)), 0.0]}
            for i, t in enumerate(inputs)
        ]
        if self.shuffle:
            data.reverse()
        if self.drop:
            data = data[:-1]
        return _Resp({"data": data})

    async def aclose(self): pass


def _make(client, batch_size=3):
    e = LlamaCppEmbedder.__new__(LlamaCppEmbedder)   # real HTTP client nahi banana
    e.base_url, e.model, e.id = "http://x", "m", "llama_cpp-m"
    e.batch_size, e._client = batch_size, client
    return e


def test_batches_and_preserves_order():
    async def run():
        client = _FakeClient(shuffle=True)
        e = _make(client, batch_size=3)
        texts = ["a" * n for n in range(1, 9)]          # 8 inputs => 3+3+2
        vectors = await e.embed(texts)

        assert [len(c) for c in client.calls] == [3, 3, 2]
        assert [round(v[0], 3) for v in vectors] == [1.0] * 8   # normalized
        assert len(vectors) == 8
    asyncio.run(run())


def test_count_mismatch_raises():
    async def run():
        e = _make(_FakeClient(drop=True))
        try:
            await e.embed(["a", "b", "c"])
            assert False, "should raise"
        except RuntimeError as exc:
            assert "returned 2 vectors for 3 inputs" in str(exc)
    asyncio.run(run())


def test_query_prefix_only_on_queries():
    async def run():
        client = _FakeClient()
        e = _make(client)
        e.query_prefix = "Q: "

        await e.embed(["doc one"])                 # document: prefix nahi
        await e.embed_query("what is x")           # query: prefix

        assert client.calls[0] == ["doc one"]
        assert client.calls[1] == ["Q: what is x"]
    asyncio.run(run())
