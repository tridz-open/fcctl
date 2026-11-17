from hypothesis import given, strategies as st

from fcctl.policy import (REGISTRY, Allow, Deny, NeedsApproval, NeedsConfirm, check, resolve_policy)
from fcctl.profiles import Profile, load_profiles

WRITES = [o for o, m in REGISTRY.items() if m["kind"] != "read"]
READS = [o for o, m in REGISTRY.items() if m["kind"] == "read"]
L, T = "a@x.com", "T1"


def eff(policy, allow=()):
    return resolve_policy(L, T, [Profile("p", L, T, policy, tuple(allow))])


def test_load_toml(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[profiles.s]\nemail="a@x.com"\nteam="T2"\npolicy="protected"\nallow=["backup.create"]\n')
    (p,) = load_profiles(f)
    assert (p.login, p.team, p.policy, p.allow) == ("a@x.com", "T2", "protected", ("backup.create",))


def test_unknown_pair_read_only():
    e = resolve_policy(L, "NOPE", [Profile("p", L, T, "full")])
    assert e.policy == "read_only"
    assert isinstance(check("site.drop", e), Deny)


def test_conflict_warning_and_intersection():
    ps = [Profile("a", L, T, "protected", ("backup.create", "backup.list")),
          Profile("b", L, T, "protected", ("backup.create",)), Profile("c", L, T, "full")]
    e = resolve_policy(L, T, ps)
    assert e.policy == "protected" and e.allow == {"backup.create"} and e.warnings


def test_read_only_denies_all_writes():
    e = eff("read_only", WRITES)
    assert all(isinstance(check(o, e), Deny) for o in WRITES)
    assert all(isinstance(check(o, e), Allow) for o in READS)


def test_protected():
    e = eff("protected", ["backup.create"])
    assert isinstance(check("backup.create", e), Allow)
    assert isinstance(check("site.migrate", e), Deny)
    assert isinstance(check("site.list", e), Allow)


def test_guarded_and_full():
    g, f = eff("guarded"), eff("full")
    assert isinstance(check("site.migrate", g), Allow)
    assert isinstance(check("site.drop", g), NeedsApproval)
    assert isinstance(check("site.drop", f), NeedsApproval)


def test_raw():
    g = eff("guarded")
    assert isinstance(check(("GET", "/api/x"), g), Allow)
    assert isinstance(check(("POST", "/api/x"), g), NeedsConfirm)
    assert isinstance(check(("DELETE", "/api/x"), g), NeedsApproval)
    assert isinstance(check(("POST", "/api/site/drop"), g), NeedsApproval)
    for pol in ("read_only", "protected"):
        assert isinstance(check(("GET", "/api/x"), eff(pol)), Deny)
        assert isinstance(check(("POST", "/api/x"), eff(pol)), Deny)


profile_st = st.builds(
    lambda i, pol, al: Profile(f"p{i}", L, T, pol, tuple(al)),
    st.integers(0, 99), st.sampled_from(["full", "guarded", "read_only", "protected"]),
    st.lists(st.sampled_from(list(REGISTRY)), unique=True))


@given(st.lists(profile_st, min_size=1, max_size=5))
def test_property_no_write_allowed(ps):
    e = resolve_policy(L, T, ps)
    pols = {p.policy for p in ps}
    if len(ps) > 1:
        assert e.warnings
    if "read_only" in pols:
        assert e.policy in ("read_only", "protected")
        assert all(isinstance(check(o, e), Deny) or e.policy == "protected" and o in e.allow and False
                   for o in WRITES) or e.policy == "protected"
    if "read_only" in pols and e.policy == "read_only":
        assert not any(isinstance(check(o, e), Allow) for o in WRITES)
    if e.policy == "protected":
        for o in WRITES:
            assert isinstance(check(o, e), Allow) == (o in e.allow)
        allowed = set.intersection(*[set(p.allow) for p in ps if p.policy == "protected"])
        assert e.allow == allowed
