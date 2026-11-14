from prometheus_client import Counter, Gauge, Histogram

ACTIVE_INFERENCES = Gauge(
    "company_ai_active_inferences",
    "Currently running inference requests"
)

QUEUED_REQUESTS = Gauge(
    "company_ai_queued_requests",
    "Requests waiting for inference capacity"
)

INFERENCE_TOTAL = Counter(
    "company_ai_inference_total",
    "Total inference requests",
    ["status"]
)

INFERENCE_DURATION = Histogram(
    "company_ai_inference_duration_seconds",
    "Total inference generation duration",
    buckets=(1, 2, 5, 10, 20, 30, 60, 120)
)

QUEUE_WAIT_DURATION = Histogram(
    "company_ai_queue_wait_seconds",
    "Time spent waiting for inference capacity",
    buckets=(0.01, 0.1, 0.5, 1, 2, 5, 10, 30, 60)
)

TIME_TO_FIRST_TOKEN = Histogram(
    "company_ai_time_to_first_token_seconds",
    "Time from inference start until first streamed chunk",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30)
)

INFERENCE_CANCELLATIONS = Counter(
    "company_ai_inference_cancellations_total",
    "Inference requests cancelled by clients"
)

# result: hit | miss | shared | bypass
CACHE_REQUESTS = Counter(
    "company_ai_cache_requests_total",
    "Completion requests by cache result",
    ["result"]
)

RATE_LIMITED = Counter(
    "company_ai_rate_limited_total",
    "Requests rejected by the rate limiter"
)

AUTH_FAILURES = Counter(
    "company_ai_auth_failures_total",
    "Requests rejected for missing/invalid API key"
)

RAG_QUERIES = Counter(
    "company_ai_rag_queries_total",
    "RAG queries",
    ["llm_called"]
)


LOGIN_FAILURES = Counter(
    "company_ai_login_failures_total",
    "Failed login attempts"
)
