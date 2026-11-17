import pytest
from cryptography.fernet import Fernet

from fcctl.request import CentralClient, PilotClient
from fcctl.session_store import SessionStore

BASE = "https://dashboard.frappe.io"
GW = "https://admin-x.gw.example.com"
PROFILE = BASE + "/api/v2/method/central.api.identity.my_profile"
LINK = BASE + "/api/v2/method/central.api.sso.get_bench_link"


@pytest.fixture
def store(tmp_path):
    s = SessionStore(tmp_path / "s.enc", Fernet.generate_key())
    s.set_central(sid="sid0", csrf="csrf0")
    return s


@pytest.fixture
def central(store):
    return CentralClient(store, team="TEAM-1")


@pytest.fixture
def pilot(central):
    return PilotClient(central, "server-abc")
