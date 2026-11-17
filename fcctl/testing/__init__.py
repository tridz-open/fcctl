"""Public test doubles: FakeFrappeCloud (in-memory Central + Pilot) and the `fake_fc` pytest fixture.

Enable in a consumer's conftest:  pytest_plugins = ["fcctl.testing"]
"""
from .fake import FakeFrappeCloud, FakeClock, FakeSiteAdmin

try:  # pragma: no cover - pytest is optional
    import pytest

    @pytest.fixture
    def fake_fc(tmp_path):
        f = FakeFrappeCloud(tmp_dir=tmp_path)
        yield f
        f.close()
except ImportError:  # pragma: no cover
    pass

__all__ = ["FakeFrappeCloud", "FakeClock", "FakeSiteAdmin", "fake_fc"]
