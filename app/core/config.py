from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    APP_NAME: str = "Company AI Server"
    APP_VERSION: str = "1.6.0"
    API_V1_PREFIX: str = "/api/v1"

    INFERENCE_PROVIDER: str = "llama_cpp"

    LLAMA_BASE_URL: str = "http://127.0.0.1:8081"
    LLAMA_MODEL: str = "Qwen3-4B"
    LLAMA_TIMEOUT: float = 120.0
    LLAMA_MAX_CONNECTIONS: int = 20
    LLAMA_MAX_KEEPALIVE: int = 10

    # GTX 1070: keep at 1 until benchmark proves 2 is better.
    INFERENCE_MAX_CONCURRENT: int = 1
    INFERENCE_QUEUE_TIMEOUT: float = 60.0
    INFERENCE_MAX_QUEUE_SIZE: int = 20
    RETRY_AFTER_SECONDS: int = 5

    # --- Security -------------------------------------------------------
    # "name:key[:role],name2:key2"  (key >= 16 chars, role user|admin)
    # Empty => authentication DISABLED (development only).
    API_KEYS: str = ""
    RATE_LIMIT_PER_MINUTE: int = 120   # 0 = disabled
    RATE_LIMIT_BURST: int = 20

    # --- Cache ----------------------------------------------------------
    CACHE_BACKEND: str = "memory"      # memory | redis | none
    CACHE_TTL_SECONDS: int = 3600
    CACHE_MAX_ENTRIES: int = 1000
    # Sirf itne ya kam temperature wali requests cache hoti hain
    CACHE_MAX_TEMPERATURE: float = 0.3
    REDIS_URL: str = "redis://127.0.0.1:6379/0"

    # --- Database -------------------------------------------------------
    # DATABASE_URL set ho (postgresql://user:pass@host:5432/db) to usage log
    # PostgreSQL mein jata hai; khali => SQLite (DATABASE_PATH).
    DATABASE_URL: str = ""
    DATABASE_PATH: str = "data/company_ai.db"

    # --- RAG ------------------------------------------------------------
    RAG_STORE_BACKEND: str = "json"    # json | pgvector (needs DATABASE_URL)
    RAG_STORE_PATH: str = "data/rag_store.json"
    # pgvector column dimension (Qwen3-Embedding-0.6B = 1024, BGE-M3 = 1024)
    RAG_EMBED_DIM: int = 1024
    RAG_PGVECTOR_HNSW: bool = False
    RAG_EMBEDDER: str = "hashing"      # hashing | llama_cpp
    RAG_HASHING_DIM: int = 512
    RAG_EMBED_BASE_URL: str = "http://127.0.0.1:8082"
    RAG_EMBED_MODEL: str = "embedding-model"
    # Query-side prefix (sirf queries par). Qwen3-Embedding ke liye instruction
    # prefix try karo; BGE-M3 ke liye khali rakho.
    RAG_QUERY_PREFIX: str = ""
    RAG_CHUNK_SIZE: int = 800
    RAG_CHUNK_OVERLAP: int = 100
    RAG_TOP_K: int = 4
    RAG_MAX_CONTEXT_CHARS: int = 6000
    RAG_MAX_DOC_CHARS: int = 200000

    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()
