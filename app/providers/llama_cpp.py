import httpx
from typing import AsyncIterator

from app.core.config import settings
from app.providers.base import BaseInferenceProvider


class LlamaCppProvider(BaseInferenceProvider):

    def __init__(self):
        self.client = None

    async def startup(self):
        timeout = httpx.Timeout(
            settings.LLAMA_TIMEOUT,
            connect=10.0
        )

        limits = httpx.Limits(
            max_connections=settings.LLAMA_MAX_CONNECTIONS,
            max_keepalive_connections=settings.LLAMA_MAX_KEEPALIVE
        )

        self.client = httpx.AsyncClient(
            timeout=timeout,
            limits=limits
        )

    async def shutdown(self):
        if self.client:
            await self.client.aclose()
            self.client = None

    def get_chat_url(self):
        return (
            f"{settings.LLAMA_BASE_URL.rstrip('/')}"
            "/v1/chat/completions"
        )

    async def chat(self, payload: dict) -> dict:
        if self.client is None:
            raise RuntimeError("Provider not initialized")

        response = await self.client.post(
            self.get_chat_url(),
            json=payload
        )

        response.raise_for_status()
        return response.json()

    async def stream_chat(
        self,
        payload: dict
    ) -> AsyncIterator[str]:

        if self.client is None:
            raise RuntimeError("Provider not initialized")

        async with self.client.stream(
            "POST",
            self.get_chat_url(),
            json=payload
        ) as response:

            response.raise_for_status()

            async for line in response.aiter_lines():

                if line.startswith("data:"):
                    yield line + "\n\n"

                    if line.strip() == "data: [DONE]":
                        break

    async def health(self) -> bool:
        if self.client is None:
            return False

        try:
            response = await self.client.get(
                f"{settings.LLAMA_BASE_URL.rstrip('/')}/health"
            )
            return response.status_code == 200

        except httpx.HTTPError:
            return False


llama_provider = LlamaCppProvider()