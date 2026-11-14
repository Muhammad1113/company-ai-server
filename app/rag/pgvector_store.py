"""
pgvector-backed vector store (async). JsonVectorStore jaisa interface.

- Exact (brute-force) cosine search by default: filtering (collection)
  hamesha sahi rehti hai. ~100k chunks tak theek.
- HNSW index opt-in (hnsw=True): approximate hai aur WHERE filter index ke
  BAAD lagta hai, to chhoti collections ke results kam aa sakte hain.
"""
import json
import logging

from app.rag.store import summarize_metadata

logger = logging.getLogger("company_ai.pgvector")


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"


class PgVectorStore:

    def __init__(self, db, embedder_id: str, dim: int, hnsw: bool = False):
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.db = db
        self.embedder_id = embedder_id
        self.dim = dim
        self.hnsw = hnsw

    async def init(self) -> None:
        async with self.db.ddl() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS rag_chunks ("
                " id BIGSERIAL PRIMARY KEY,"
                " collection TEXT NOT NULL,"
                " doc_id TEXT NOT NULL,"
                " chunk_index INTEGER NOT NULL,"
                " text TEXT NOT NULL,"
                " metadata JSONB NOT NULL DEFAULT '{}'::jsonb,"
                " embedder TEXT NOT NULL,"
                f" embedding vector({self.dim}) NOT NULL,"
                " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " UNIQUE (collection, doc_id, chunk_index))"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS rag_chunks_doc_idx "
                "ON rag_chunks (collection, doc_id)"
            )

            table_dim = await conn.fetchval(
                "SELECT atttypmod FROM pg_attribute "
                "WHERE attrelid = to_regclass('rag_chunks') AND attname = 'embedding'"
            )
            if table_dim != self.dim:
                raise ValueError(
                    f"rag_chunks.embedding is vector({table_dim}) but "
                    f"RAG_EMBED_DIM={self.dim}. Use the same dimension or "
                    "drop the table and re-ingest."
                )

            existing = await conn.fetchval("SELECT embedder FROM rag_chunks LIMIT 1")
            if existing is not None and existing != self.embedder_id:
                raise ValueError(
                    f"rag_chunks was built with '{existing}' but the current "
                    f"embedder is '{self.embedder_id}'. TRUNCATE rag_chunks "
                    "and re-ingest."
                )

            if self.hnsw:
                await conn.execute(
                    "CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw "
                    "ON rag_chunks USING hnsw (embedding vector_cosine_ops)"
                )

    def _check_dim(self, vector):
        if len(vector) != self.dim:
            raise ValueError(
                f"embedding has {len(vector)} dimensions but RAG_EMBED_DIM="
                f"{self.dim}"
            )

    async def replace_document(self, doc_id, collection, items: list[dict]) -> int:
        rows = []
        for it in items:
            self._check_dim(it["vector"])
            rows.append((
                collection, doc_id, it["chunk_index"], it["text"],
                json.dumps(it.get("metadata", {})), self.embedder_id,
                _vec_literal(it["vector"]),
            ))

        async with self.db.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM rag_chunks WHERE doc_id = $1 AND collection = $2",
                    doc_id, collection,
                )
                await conn.executemany(
                    "INSERT INTO rag_chunks (collection, doc_id, chunk_index, "
                    "text, metadata, embedder, embedding) "
                    "VALUES ($1,$2,$3,$4,$5::text::jsonb,$6,$7::text::vector)",
                    rows,
                )
        return len(rows)

    async def search(self, vector, k, collection) -> list[dict]:
        self._check_dim(vector)
        rows = await self.db.pool.fetch(
            "SELECT doc_id, chunk_index, text, metadata::text AS metadata, "
            "1 - (embedding <=> $1::text::vector) AS score "
            "FROM rag_chunks WHERE collection = $2 "
            "ORDER BY embedding <=> $1::text::vector LIMIT $3",
            _vec_literal(vector), collection, k,
        )
        return [
            {
                "score": round(float(r["score"]), 4),
                "doc_id": r["doc_id"],
                "collection": collection,
                "chunk_index": r["chunk_index"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
            }
            for r in rows
        ]

    async def delete_document(self, doc_id, collection) -> int:
        status = await self.db.pool.execute(
            "DELETE FROM rag_chunks WHERE doc_id = $1 AND collection = $2",
            doc_id, collection,
        )
        return int(status.split()[-1])          # "DELETE 3" -> 3

    async def list_documents(self, collection) -> list[dict]:
        rows = await self.db.pool.fetch(
            "SELECT doc_id, COUNT(*) AS chunks, "
            "(array_agg(metadata::text ORDER BY chunk_index))[1] AS metadata "
            "FROM rag_chunks WHERE collection = $1 GROUP BY doc_id ORDER BY lower(doc_id)",
            collection,
        )
        out = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
            except ValueError:
                meta = {}
            out.append({"doc_id": r["doc_id"], "chunks": int(r["chunks"]), **summarize_metadata(meta)})
        return out

    async def get_document_chunks(self, doc_id, collection) -> list[str]:
        rows = await self.db.pool.fetch(
            "SELECT text FROM rag_chunks WHERE collection = $1 AND doc_id = $2 ORDER BY chunk_index",
            collection, doc_id,
        )
        return [r["text"] for r in rows]

    async def stats(self) -> dict:
        rows = await self.db.pool.fetch(
            "SELECT collection, COUNT(DISTINCT doc_id) AS docs, COUNT(*) AS chunks "
            "FROM rag_chunks GROUP BY collection"
        )
        return {
            "embedder": self.embedder_id,
            "chunks": sum(int(r["chunks"]) for r in rows),
            "collections": {r["collection"]: int(r["docs"]) for r in rows},
        }
