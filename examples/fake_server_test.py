"""Run fcctl against the in-process fake of Frappe Cloud. Fully offline: no account, no network."""
from fcctl import sites, tasks
from fcctl.testing import FakeFrappeCloud

fake = FakeFrappeCloud()
try:
    pilot = fake.pilot()
    done = tasks.run(pilot, lambda: sites.create_site(pilot, "demo.example.com", ["erpnext"], key="demo-1"), poll=0)
    assert done["status"] == "success", done
    assert sites.get_site(pilot, "demo.example.com")["status"] == "active"
    print("ok: provisioned demo.example.com on the fake")
finally:
    fake.close()
