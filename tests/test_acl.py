from app.core.security import authenticate, parse_api_keys

A, B, C = "admin-secret-key-00001", "hr-user-secret-key-02", "all-user-secret-key-03"


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_collections_parsing_and_access():
    entries = parse_api_keys(f"root:{A}:admin, hr:{B}:user:hr|policies, anyone:{C}:user")

    root = authenticate(A, entries)
    hr = authenticate(B, entries)
    anyone = authenticate(C, entries)

    assert hr.collections == frozenset({"hr", "policies"})
    assert hr.can_access("hr") and hr.can_access("policies") and not hr.can_access("finance")
    assert anyone.collections is None and not anyone.can_access("finance")   # deny by default: khali list = kuch nahi
    assert anyone.can_access("reg__fda")                                     # shared regulatory sab padh sakte hain
    assert root.can_access("anything")                                       # admin hamesha


def test_star_means_no_extra_and_admin_ignores_restriction():
    entries = parse_api_keys(f"a:{A}:user:*, b:{B}:admin:hr")
    assert authenticate(A, entries).collections is None
    assert not authenticate(A, entries).can_access("finance")           # "*" ab "sab" nahi
    assert authenticate(B, entries).can_access("finance")


def test_invalid_collection_spec_rejected():
    assert _raises(lambda: parse_api_keys(f"a:{A}:user:hr|bad name"))
    assert _raises(lambda: parse_api_keys(f"a:{A}:user:hr:extra"))
    assert not _raises(lambda: parse_api_keys(f"a:{A}:user:"))      # khali = koi extra collection nahi
