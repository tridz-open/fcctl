import pytest

from fcctl import apps


def test_dependency_first_and_stable():
    assert apps.install_order(["hrms", "erpnext"]) == ["erpnext", "hrms"]
    assert apps.install_order(["hrms"]) == ["erpnext", "hrms"]  # missing dependency is added
    assert apps.install_order(["a", "b"]) == ["a", "b"]  # unrelated apps keep their order


def test_custom_deps_and_cycle():
    assert apps.install_order(["x"], deps={"x": ["y"]}) == ["y", "x"]
    with pytest.raises(ValueError):
        apps.install_order(["x"], deps={"x": ["y"], "y": ["x"]})
