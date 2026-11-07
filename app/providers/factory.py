from app.core.config import settings
from app.providers.base import BaseInferenceProvider
from app.providers.llama_cpp import llama_provider


def get_inference_provider() -> BaseInferenceProvider:

    provider_name = settings.INFERENCE_PROVIDER.lower()

    if provider_name == "llama_cpp":
        return llama_provider

    raise ValueError(
        f"Unsupported inference provider: {provider_name}"
    )