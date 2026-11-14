from app.core.fairness import ClientBusy, ClientConcurrencyLimiter


def test_cap_per_client_and_release():
    lim = ClientConcurrencyLimiter(2)
    lim.acquire("alice"); lim.acquire("alice")
    try:
        lim.acquire("alice")
        assert False, "third must be rejected"
    except ClientBusy:
        pass

    lim.acquire("bob")                       # doosre user par asar nahi
    lim.release("alice")
    lim.acquire("alice")                     # ek jagah khuli

    for _ in range(2): lim.release("alice")
    lim.release("bob")
    assert lim._active == {}                 # leak nahi


def test_disabled_when_zero_and_release_never_negative():
    lim = ClientConcurrencyLimiter(0)
    for _ in range(100): lim.acquire("x")    # kabhi reject nahi
    lim.release("x"); lim.release("x")
    assert lim._active == {}

    capped = ClientConcurrencyLimiter(1)
    capped.release("never-acquired")         # galat release se counter kharab nahi
    capped.acquire("a")
    try:
        capped.acquire("a"); assert False
    except ClientBusy:
        pass
