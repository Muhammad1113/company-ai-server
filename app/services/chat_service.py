import asyncio
import logging
from time import perf_counter
from typing import AsyncIterator

from app.core.config import settings
from app.core.concurrency import inference_controller
from app.core.metrics import (
    INFERENCE_CANCELLATIONS,
    INFERENCE_DURATION,
    INFERENCE_TOTAL,
    TIME_TO_FIRST_TOKEN,
)
from app.providers.factory import get_inference_provider

logger = logging.getLogger("company_ai.chat")


class ChatService:

    def __init__(self):
        self.provider = get_inference_provider()

    def build_payload(
        self,
        message: str,
        temperature: float,
        max_tokens: int,
        stream: bool = False
    ) -> dict:

        return {
            "model": settings.LLAMA_MODEL,
            "messages": [
                {"role": "user", "content": message}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            "chat_template_kwargs": {
                "enable_thinking": False
            }
        }

    async def generate(
        self,
        message: str,
        temperature: float,
        max_tokens: int
    ) -> dict:

        payload = self.build_payload(
            message, temperature, max_tokens
        )

        return await inference_controller.execute(
            lambda: self.provider.chat(payload)
        )

    async def stream_generate_reserved(
        self,
        message: str,
        temperature: float,
        max_tokens: int
    ) -> AsyncIterator[str]:
        """Slot pehle se reserve hona chahiye (caller zimmedar hai)."""

        payload = self.build_payload(
            message,
            temperature,
            max_tokens,
            stream=True
        )

        first_chunk = True
        stream_started = perf_counter()

        try:
            with INFERENCE_DURATION.time():

                async for chunk in self.provider.stream_chat(payload):

                    if first_chunk:
                        ttft = perf_counter() - stream_started
                        TIME_TO_FIRST_TOKEN.observe(ttft)
                        first_chunk = False
                        logger.info("first token ttft=%.3fs", ttft)

                    yield chunk

            INFERENCE_TOTAL.labels(status="success").inc()
            logger.info(
                "stream completed in %.2fs",
                perf_counter() - stream_started
            )

        # Client disconnect: Starlette task cancel karta hai
        # (CancelledError) ya generator close hota hai (GeneratorExit).
        except (asyncio.CancelledError, GeneratorExit):
            INFERENCE_CANCELLATIONS.inc()
            logger.info("stream cancelled by client")
            raise

        except Exception:
            INFERENCE_TOTAL.labels(status="error").inc()
            logger.exception("stream failed")
            raise

    async def stream_generate(
        self,
        message: str,
        temperature: float,
        max_tokens: int
    ) -> AsyncIterator[str]:

        async with inference_controller.slot():
            async for chunk in self.stream_generate_reserved(
                message,
                temperature,
                max_tokens
            ):
                yield chunk


chat_service = ChatService()
