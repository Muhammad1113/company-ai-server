from app.core.ratelimit import TokenBucketLimiter
from app.core.security import (
    authenticate, extract_api_key, parse_api_keys,
)

KEY_A = "alice-secret-key-0001"
KEY_B = "bob-secret-key-00002"


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    return False


def test_parse_and_authenticate():
    entries = parse_api_keys(f"alice:{KEY_A}:admin, bob:{KEY_B}")
    assert authenticate(KEY_A, entries).name == "alice"
    assert authenticate(KEY_A, entries).is_admin
    assert authenticate(KEY_B, entries).role == "user"
    assert authenticate("wrong-key-wrong-key", entries) is None
    assert authenticate(None, entries) is None
    assert parse_api_keys("") == []


def test_parse_rejects_bad_config():
    assert _raises(lambda: parse_api_keys("alice:short"))
    assert _raises(lambda: parse_api_keys(f"alice:{KEY_A}:root"))
    assert _raises(lambda: parse_api_keys(f"alice:{KEY_A},alice:{KEY_B}"))
    assert _raises(lambda: parse_api_keys("justname"))


def test_extract_api_key():
    assert extract_api_key({"authorization": "Bearer abc123"}) == "abc123"
    assert extract_api_key({"authorization": "bearer   abc123 "}) == "abc123"
    assert extract_api_key({"x-api-key": "k1"}) == "k1"
    assert extract_api_key({}) is None


def test_token_bucket_burst_and_refill():
    now = [0.0]
    limiter = TokenBucketLimiter(per_minute=60, burst=3, clock=lambda: now[0])

    assert [limiter.allow("a")[0] for _ in range(3)] == [True, True, True]
    allowed, retry = limiter.allow("a")
    assert not allowed and 0.9 < retry <= 1.0

    assert limiter.allow("b")[0]          # alag client alag bucket

    now[0] += 1.0                          # 1 token refill
    assert limiter.allow("a")[0]
    assert not limiter.allow("a")[0]


def test_token_bucket_prunes_old_keys():
    limiter = TokenBucketLimiter(per_minute=60, burst=1, max_keys=3)
    for i in range(10):
        limiter.allow(f"k{i}")
    assert len(limiter._buckets) == 3
