"""
Cache company-isolation guard (fastapi ki zaroorat nahi).

ResponseCache ki key sirf prompt (messages + model + temperature) par hai, user/company par nahi. Ye tab hi safe hai
jab cache sirf plain /chat/completions mein ho, jahan prompt mein company documents aate hi nahi. Agar koi kal RAG /
conversation path mein cache laga de, ye test toot jayega aur pehle isolation sochna paray ga.
"""
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
ALLOWED = {"api/routes/chat.py", "services/container.py", "core/cache.py"}


def test_response_cache_only_used_in_plain_chat():
    users = {
        str(p.relative_to(APP)).replace("\\", "/")
        for p in APP.rglob("*.py")
        if re.search(r"\bresponse_cache\b|\bResponseCache\b", p.read_text(encoding="utf-8"))
    }
    assert users <= ALLOWED, f"response cache used outside plain chat: {sorted(users - ALLOWED)}"


def test_plain_chat_route_has_no_retrieval():
    src = (APP / "api/routes/chat.py").read_text(encoding="utf-8")
    assert "rag_service" not in src and "search_many" not in src
