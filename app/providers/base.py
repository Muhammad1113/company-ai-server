from abc import ABC, abstractmethod
from typing import AsyncIterator


class BaseInferenceProvider(ABC):

    @abstractmethod
    async def startup(self) -> None:
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass

    @abstractmethod
    async def chat(self, payload: dict) -> dict:
        pass

    @abstractmethod
    async def stream_chat(
        self,
        payload: dict
    ) -> AsyncIterator[str]:
        pass

    @abstractmethod
    async def health(self) -> bool:
        pass