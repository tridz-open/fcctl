"""Every op id the CLI uses must exist in policy.REGISTRY; destructive ones also in guard.REGISTRY."""
import ast
import pathlib

from fcctl import guard, policy

CLI = pathlib.Path(__file__).parent.parent / "fcctl" / "cli"
RUNNERS = {"run_central": 1, "run_op": 0}  # name -> index of the op-id positional arg


def _literal_op_ids():
    ids = {}
    for f in sorted(CLI.glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Call):
                n = getattr(node.func, "attr", getattr(node.func, "id", None))
                i = RUNNERS.get(n)
                if i is not None and len(node.args) > i:
                    a = node.args[i]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        ids.setdefault(a.value, f.name)
    return ids


def test_scan_finds_ops():
    assert len(_literal_op_ids()) > 25


def test_cli_op_ids_in_policy_registry():
    missing = {o: f for o, f in _literal_op_ids().items() if o not in policy.REGISTRY}
    assert not missing, missing


def test_cli_modules_do_not_mutate_registries():
    for f in CLI.glob("*.py"):
        assert "REGISTRY.setdefault" not in f.read_text(), f.name


def test_destructive_cli_ops_in_guard_registry():
    guard.load_all()
    destructive = [o for o in _literal_op_ids() if policy.REGISTRY.get(o, {}).get("kind") == "destructive"
                   and policy.REGISTRY[o]["plane"] == "central"]  # pilot ones are guarded via @guarded fn-name ids
    assert "snapshot.delete" in destructive
    missing = [o for o in destructive if o not in guard.REGISTRY]
    assert not missing, missing


def test_guard_registry_ops_known_to_policy():
    for op in ("server.terminate", "server.stop", "server.resize", "server.set_termination_protection",
               "snapshot.delete", "bucket.delete"):
        assert op in guard.REGISTRY and op in policy.REGISTRY
