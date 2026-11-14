"""Chat messages tayyar karna (system prompt + history truncation). Pure module."""


def build_messages(
    message: str | None,
    messages: list[dict] | None,
    system_prompt: str = "",
    max_history_chars: int = 8000,
) -> list[dict]:
    """
    Exactly one of message / messages. Naye turns pehle bachte hain: budget
    (max_history_chars) khatam ho to sab se purane turns drop hote hain.
    Aakhri (user) message hamesha rehta hai.
    """
    if (message is None) == (messages is None):
        raise ValueError("provide exactly one of 'message' or 'messages'")

    if messages is None:
        turns = [{"role": "user", "content": message}]
    else:
        turns = [{"role": m["role"], "content": m["content"]} for m in messages]

    if not turns or turns[-1]["role"] != "user":
        raise ValueError("the last message must have role 'user'")

    kept = [turns[-1]]
    used = len(turns[-1]["content"])

    for turn in reversed(turns[:-1]):
        if used + len(turn["content"]) > max_history_chars:
            break
        kept.append(turn)
        used += len(turn["content"])

    kept.reverse()

    while len(kept) > 1 and kept[0]["role"] == "assistant":
        kept.pop(0)

    out = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})
    return out + kept
