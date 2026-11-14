from app.core.config import settings
from app.providers.base import BaseInferenceProvider
from app.providers.llama_cpp import llama_provider

# llama.cpp aur vLLM dono OpenAI-compatible /v1/chat/completions (+ /health) dete hain,
# isliye vLLM par jaane ke liye LLAMA_BASE_URL (aur LLAMA_MODEL = vLLM ka served model name)
# badalna kaafi hona chahiye. Asli vLLM server par abhi test nahi hua.
_OPENAI_COMPATIBLE = {"llama_cpp", "vllm", "openai_compatible"}


def get_inference_provider() -> BaseInferenceProvider:

    provider_name = settings.INFERENCE_PROVIDER.lower()

    if provider_name in _OPENAI_COMPATIBLE:
        return llama_provider

    raise ValueError(
        f"Unsupported inference provider: {provider_name}"
    )
