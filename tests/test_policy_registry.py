import importlib

import pytest

from fcctl import policy
from fcctl.policy import Allow, EffectivePolicy, NeedsConfirm

MODS = ["activity", "apps", "backups", "billing", "domains", "infra", "logs", "monitor", "notifications",
        "objects", "servers", "sites", "snapshots", "taskops", "tasks", "workflows", "fleet"]


def test_every_ops_value_registered():
    seen = 0
    for m in MODS:
        try:
            mod = importlib.import_module(f"fcctl.{m}")
        except ImportError:
            continue
        for name, op in (getattr(mod, "OPS", None) or {}).items():
            seen += 1
            assert op in policy.REGISTRY, f"{m}.OPS[{name!r}] -> {op!r} missing from REGISTRY"
    assert seen > 20


@pytest.mark.parametrize("op,kind,plane", [
    ("backup.offsite_download", "destructive", "pilot"), ("backup.restore_upload", "destructive", "pilot"),
    ("server.stop", "disruptive", "central"), ("server.restart", "disruptive", "central"),
    ("server.start", "write", "central"), ("server.resize", "cost", "central"), ("billing.ledger", "read", "central"),
    ("site.config_set", "write", "pilot"), ("workflow.provision_site", "write", "pilot")])
def test_classification(op, kind, plane):
    assert policy.REGISTRY[op]["kind"] == kind and policy.REGISTRY[op]["plane"] == plane


def test_disruptive_semantics():
    g = EffectivePolicy("a", "t", "guarded")
    assert isinstance(policy.check("server.stop", g), NeedsConfirm)
    assert isinstance(policy.check("server.stop", EffectivePolicy("a", "t", "full")), Allow)
    assert isinstance(policy.check("server.stop", EffectivePolicy("a", "t", "read_only")), policy.Deny)


def test_resize_cost_write_confirm_not_approval():
    g = EffectivePolicy("a", "t", "guarded")
    assert isinstance(policy.check("server.resize", g), NeedsConfirm)
    assert isinstance(policy.check("server.resize", EffectivePolicy("a", "t", "full")), Allow)
