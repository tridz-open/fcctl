import time

from fcctl import journal as J, reconcile as rc, servers, sites
from fcctl.testing import FakeFrappeCloud, fake_fc  # noqa: F401


def mk(tmp_path, fake, op, args):
    j = J.Journal(tmp_path / "j", now=time.time)
    e = j.create(op, args)
    j.transition(e["id"], J.DISPATCHING)
    j.transition(e["id"], J.UNKNOWN, error="x")
    p = fake.pilot()
    return j, e, rc.default_resolvers(lambda: fake.central(), lambda ent: p)


def state(j, e):
    return j.get(e["id"])["state"]


def test_create_server_found_and_missing(tmp_path, fake_fc):
    j, e, res = mk(tmp_path, fake_fc, "create_server", {"title": "t1", "request_key": "k"})
    assert rc.reconcile(j, res)["unresolved"] == [e["id"]]  # nothing remote, resend unsafe w/o resubmit
    servers.create_server(fake_fc.central(), "t1", "p", "img", request_key="k")
    assert rc.reconcile(j, res)["succeeded"] == [e["id"]] and state(j, e) == J.SUCCEEDED


def test_create_site_task_states(tmp_path, fake_fc):
    p = fake_fc.pilot()
    j, e, res = mk(tmp_path, fake_fc, "create_site", {"site": "a.x"})
    assert res["create_site"](e) == "not_happened"
    sites.create_site(p, "a.x", [])
    assert res["create_site"](e) is None  # pending
    fake_fc.tasks["task-0001"]["status"] = "success"
    assert res["create_site"](e)[0] == "succeeded"
    fake_fc.tasks["task-0001"]["status"] = "failed"
    assert res["create_site"](e)[0] == "failed"


def test_drop_site_absent_is_success(tmp_path, fake_fc):
    p = fake_fc.pilot()
    j, e, res = mk(tmp_path, fake_fc, "drop_site", {"site": "gone.x"})
    assert res["drop_site"](e) == "succeeded"
    sites.create_site(p, "here.x", [])
    e2 = dict(e, args={"site": "here.x"})
    assert res["drop_site"](e2) == "not_happened"


def test_bucket_create_lookup(tmp_path, fake_fc):
    from fcctl import objects
    j, e, res = mk(tmp_path, fake_fc, "create_bucket", {"bucket_name": "bk1"})
    assert res["create_bucket"](e) == "not_happened"
    objects.create_bucket(fake_fc.central(), "bk1")
    assert res["create_bucket"](e)[0] == "succeeded"


def test_cli_reconcile_uses_defaults(tmp_path, fake_fc):
    from fcctl.cli import g_misc
    assert hasattr(g_misc, "reconcile")
