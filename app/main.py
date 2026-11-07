import logging

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import get_client
from app.api.routes.chat import router as chat_router
from app.api.routes.rag import router as rag_router
from app.api.routes.usage import router as usage_router
from app.core.config import settings
from app.core.request_context import (
    RequestContextMiddleware,
    setup_logging,
)
from app.providers.factory import get_inference_provider
from app.services import container

setup_logging(settings.LOG_LEVEL)

logger = logging.getLogger("company_ai")

provider = get_inference_provider()


@asynccontextmanager
async def lifespan(app: FastAPI):

    logger.info("Company AI Server starting")

    await provider.startup()
    await container.startup()

    app.state.inference_provider = provider

    yield

    await container.shutdown()
    await provider.shutdown()

    logger.info("Company AI Server shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    description="Private Local AI Inference Platform",
    version=settings.APP_VERSION,
    lifespan=lifespan
)

app.add_middleware(RequestContextMiddleware)


# NOTE: /metrics abhi open hai. Production mein sirf internal network /
# Nginx allow-list se expose karo.
@app.get("/metrics", include_in_schema=False)
async def metrics():

    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/")
async def home():

    return {
        "status": "running",
        "message": "Company AI Server is ready",
        "version": settings.APP_VERSION
    }


@app.get("/health")
async def health():

    # Liveness: application process is running.
    return {"status": "healthy"}


@app.get("/ready")
async def readiness():

    provider_ready = await provider.health()
    database = "disabled"

    if container.db is not None:
        database = "ok" if await container.db.ping() else "down"

    if not provider_ready or database == "down":
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "inference_provider": "ok" if provider_ready else "down",
                "database": database,
            }
        )

    return {
        "status": "ready",
        "inference_provider": settings.INFERENCE_PROVIDER,
        "model": settings.LLAMA_MODEL,
        "database": database,
    }


# Router-level auth: koi naya endpoint bina auth ke nahi rahega.
for router in (chat_router, rag_router, usage_router):
    app.include_router(
        router,
        prefix=settings.API_V1_PREFIX,
        dependencies=[Depends(get_client)],
    )
