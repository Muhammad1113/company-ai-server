"""OpenAI-style SSE chunks se assistant ka text jama karna (conversation save ke liye). Pure module."""
import json


class SseTextCollector:

    def __init__(self):
        self._parts: list[str] = []

    def feed(self, chunk: str) -> None:
        for line in chunk.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            for choice in obj.get("choices") or []:
                content = (choice.get("delta") or {}).get("content")
                if isinstance(content, str):
                    self._parts.append(content)

    @property
    def text(self) -> str:
        return "".join(self._parts)
