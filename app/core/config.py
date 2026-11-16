from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    APP_NAME: str = "Company AI Server"
    APP_VERSION: str = "1.10.0"
    # development: auth khali ho to server khula (sirf dev). production: auth hamesha lazmi (fail-closed).
    APP_ENV: str = "development"
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
    # Ek user ki max active (chal rahi + queue mein) chat requests; 0 = limit nahi
    INFERENCE_MAX_PER_CLIENT: int = 2

    # --- Security -------------------------------------------------------
    # "name:key[:role],name2:key2"  (key >= 16 chars, role user|admin)
    # Empty => authentication DISABLED (development only).
    API_KEYS: str = ""
    # Khali = /metrics khula (dev). Set ho to "Authorization: Bearer <token>" chahiye (Prometheus credentials).
    METRICS_TOKEN: str = ""
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
    # Is se kam score wale chunks ignore (0 = sirf positive). Eval se tune karo.
    RAG_MIN_SCORE: float = 0.0
    # Auto mode (collection="*"): is score se kam hit => documents mein jawab nahi, tools/internet try hota hai.
    # Qwen3-Embedding mein unrelated sawal bhi 0.2-0.4 de sakta hai: apne sawalon se tune karo (log mein top_score).
    RAG_AUTO_MIN_SCORE: float = 0.40
    RAG_MAX_CONTEXT_CHARS: int = 6000
    # "General reference" documents (company ke apne facts nahi). "our/we/hamara" wale sawal (our MOQ, do we have FDA...) mein
    # ye documents jawab ka sabot nahi maane jate. Naye general document ka doc_id yahan jodo (comma-separated).
    RAG_GENERAL_DOC_IDS: str = (
        "surgical_instruments_catalog,suture_materials_guide,gloves_and_ppe_guide,regulatory_and_standards_overview,"
        "packaging_labeling_and_symbols,export_trade_documents_guide,infection_control_basics,"
        "instrument_materials_and_care,qc_inspection_and_aql_basics,sterilization_basics,"
        "surgical_sizes_and_nomenclature,who_surgical_safety_checklist,export_guide_SAMPLE"
    )
    # Document se jawab dete waqt temperature is se zyada nahi (numbers/codes mein ghalti kam)
    RAG_ANSWER_MAX_TEMPERATURE: float = 0.1
    RAG_MAX_DOC_CHARS: int = 200000

    # --- Chat -----------------------------------------------------------
    CHAT_SYSTEM_PROMPT: str = ""
    CHAT_SYSTEM_PROMPT_FILE: str = "data/system_prompt.txt"
    # True: model ka output Hindi/Devanagari/Urdu script mein ja hi nahi sakta (llama.cpp grammar). Error aaye to False.
    LATIN_ONLY_OUTPUT: bool = True
    CHAT_MAX_MESSAGES: int = 40
    # History ka budget (chars). Context 4096 tokens hai: ~8000 chars (English).
    CHAT_HISTORY_MAX_CHARS: int = 8000

    # --- Agent / tools / internet search ----------------------------------
    AGENT_ENABLED: bool = True
    AGENT_TIMEZONE: str = "Asia/Karachi"      # Windows par: pip install tzdata
    AUDIT_RETENTION_DAYS: int = 365      # audit_log ki purani entries is se zyada purani hatayi jati hain; 0 = hamesha rakho
    WEB_SEARCH_ENABLED: bool = False     # secure default: company questions never leave the server unless explicitly enabled
    # auto: rules + chhota model faisla (general => model khud, verify wali cheez => internet). factual: har factual sawal par
    # internet. fresh: sirf current/latest/news wale par.
    WEB_SEARCH_POLICY: str = "auto"
    AGENT_CLASSIFIER_ENABLED: bool = True      # auto mode mein ambiguous sawal par ek chhota (<=8 token) model call
    AGENT_CLASSIFIER_TIMEOUT: float = 10.0
    # Comma-separated, fallback order. searxng sirf jab SEARXNG_URL set ho.
    WEB_SEARCH_PROVIDERS: str = "duckduckgo,wikipedia"
    SEARXNG_URL: str = ""
    # Wikipedia API User-Agent mein jata hai (unki policy). Personal ki jagah role email (it@company) behtar.
    WEB_SEARCH_CONTACT: str = ""
    WEB_SEARCH_PROXY: str = ""                # optional: http://proxy:3128 (company proxy / VPN)
    WEB_SEARCH_MAX_RESULTS: int = 4
    WEB_SEARCH_TIMEOUT: float = 8.0
    WEB_SEARCH_CACHE_TTL: float = 900.0
    WEB_SEARCH_PER_USER_PER_MINUTE: int = 5
    WEB_SEARCH_GLOBAL_PER_MINUTE: int = 20    # poore server ki outbound limit (IP block se bachao)
    WEB_SEARCH_MAX_CONCURRENT: int = 2
    WEB_SEARCH_COOLDOWN_SECONDS: float = 60.0
    # In lafzon wala sawal internet par kabhi nahi jata (comma-separated). Apni company/product names yahan jodo.
    WEB_SEARCH_PRIVACY_TERMS: str = (
        "patient,patients,mrn,cnic,hospital number,bed number,bed no,ward no,admitted,discharge summary,"
        "mareez,mareeza,مریض,مریضہ"
    )
    WEB_SEARCH_BLOCKED_DOMAINS: str = ""

    # --- File upload (RAG) ------------------------------------------------
    RAG_MAX_UPLOAD_MB: float = 20
    RAG_MAX_PDF_PAGES: int = 500
    RAG_MAX_FILE_CHARS: int = 500000

    # --- Regulatory (versioned documents + openFDA sync) ------------------
    REG_ARCHIVE_DIR: str = "data/reg_archive"      # purane versions ki asli file + text yahan mehfooz
    OPENFDA_BASE_URL: str = "https://api.fda.gov"
    OPENFDA_API_KEY: str = ""                      # optional (free); bina key: 240 req/min, 1000 req/din per IP
    OPENFDA_LIMIT: int = 25                        # har product code, har kism ke latest itne records
    OPENFDA_MAX_CODES: int = 30
    OPENFDA_TIMEOUT: float = 20.0
    OPENFDA_AUTO_SYNC_HOURS: float = 0             # 0 = sirf "Check now" button; >0 = itne ghante baad khud check (approval phir bhi chahiye)
    # eCFR (US Code of Federal Regulations, Title 21): free, koi key nahi. Isi approval flow se reg__fda mein jata hai.
    ECFR_BASE_URL: str = "https://www.ecfr.gov"
    ECFR_TITLE: int = 21
    ECFR_TIMEOUT: float = 60.0                     # Part bade hote hain (807, 878), is liye lamba timeout
    ECFR_MAX_PARTS: int = 12

    # --- Accounts / sessions ---------------------------------------------
    SESSION_TTL_HOURS: float = 168          # 7 din
    LOGIN_ATTEMPTS_PER_MINUTE: int = 10     # per IP aur per username
    LOGIN_BURST: int = 5

    # Frontend alag origin (jaise http://localhost:5173) par ho to comma-separated.
    # Nginx/Caddy same-origin se serve karo to khali rakho.
    CORS_ORIGINS: str = ""

    LOG_LEVEL: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()


import logging as _logging
from pathlib import Path

from app.core.prompt import DEFAULT_SYSTEM_PROMPT

_ROOT = Path(__file__).resolve().parents[2]      # project root (app/ ke upar)


def load_system_prompt() -> tuple[str, str]:
    """(text, source): file (cwd ya project root) > CHAT_SYSTEM_PROMPT (.env) > code ka default. Kabhi khali nahi."""
    rel = Path(settings.CHAT_SYSTEM_PROMPT_FILE)
    for p in ([rel] if rel.is_absolute() else [Path.cwd() / rel, _ROOT / rel]):
        if p.is_file():
            text = p.read_text(encoding="utf-8").strip()
            if text:
                return text, str(p)
    if settings.CHAT_SYSTEM_PROMPT.strip():
        return settings.CHAT_SYSTEM_PROMPT.strip(), ".env CHAT_SYSTEM_PROMPT"
    return DEFAULT_SYSTEM_PROMPT, "built-in default"


SYSTEM_PROMPT, SYSTEM_PROMPT_SOURCE = load_system_prompt()
_logging.getLogger("company_ai").info(
    "system prompt loaded: %d chars from %s", len(SYSTEM_PROMPT), SYSTEM_PROMPT_SOURCE)
