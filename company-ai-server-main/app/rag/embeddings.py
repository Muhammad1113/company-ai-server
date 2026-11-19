import asyncio
import math
import re
import zlib
from abc import ABC, abstractmethod

import httpx

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on",
    "for", "and", "or", "it", "this", "that", "with", "as", "by", "at",
    "be", "from", "what", "how", "do", "does", "can", "i", "you", "we",
}


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


class BaseEmbedder(ABC):
    id: str

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Query ke liye (kuch models ko query-side instruction prefix chahiye)."""
        return (await self.embed([text]))[0]

    async def close(self) -> None:
        pass


class HashingEmbedder(BaseEmbedder):
    """
    Model-free lexical embedder (feature hashing). Offline chalta hai,
    testing/dev ke liye. Semantic quality real embedding model jaisi nahi.
    """

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.id = f"hashing-{dim}"

    def _vector(self, text: str) -> list[float]:
        tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vec = [0.0] * self.dim
        for feature in features:
            h = zlib.crc32(feature.encode("utf-8"))
            vec[h % self.dim] += 1.0 if (h >> 31) & 1 else -1.0
        return _normalize(vec)

    async def embed(self, texts):
        return await asyncio.to_thread(lambda: [self._vector(t) for t in texts])


class LlamaCppEmbedder(BaseEmbedder):
    """
    llama-server --embedding (alag port, alag embedding GGUF model).
    OpenAI-style POST /v1/embeddings.

    Inputs chhote batches mein bheje jate hain (batch_size), taake
    llama-server ke -ub / slot-context limits se na takraye.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        batch_size: int = 16,
        query_prefix: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.id = f"llama_cpp-{model}"
        self.batch_size = max(1, batch_size)
        self.query_prefix = query_prefix
        self._client = httpx.AsyncClient(timeout=timeout)

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = await self._client.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda d: d["index"])

        if len(data) != len(texts):
            raise RuntimeError(
                f"embedding server returned {len(data)} vectors "
                f"for {len(texts)} inputs"
            )
        return [_normalize(d["embedding"]) for d in data]

    async def embed(self, texts):
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(
                await self._embed_batch(texts[start:start + self.batch_size])
            )
        return vectors

    async def embed_query(self, text):
        # Prefix sirf query par lagta hai, documents par nahi
        # (isliye prefix badalne par re-ingest nahi chahiye).
        return (await self.embed([self.query_prefix + text]))[0]

    async def close(self):
        await self._client.aclose()
