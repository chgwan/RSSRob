"""Tests for the management UI's admin auth (gate, login, logout, setup)."""
import importlib.util
import sys
from pathlib import Path

from rssrob import admin_credential


def _load_webapp():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("webapp", root / "web" / "webapp.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["webapp"] = m
    spec.loader.exec_module(m)
    return m


def _wa(tmp_path):
    """Fresh webapp module pointed at a tmp config + a (not-yet-existing) admin file."""
    from rssrob.subscribers import Subscribers
    wa = _load_webapp()
    d = tmp_path / "configs"; d.mkdir()
    (d / "00.yaml").write_text("output_dir: ./var/feeds\nstate_db: ./var/x.db\n",
                               encoding="utf-8")
    (d / "a.yaml").write_text("name: a\ntype: rss\nurl: http://a/\n", encoding="utf-8")
    wa.CONFIG_OVERRIDE = str(d)
    wa.REPO_ROOT = tmp_path
    wa.SUBS = Subscribers(str(tmp_path / "subs.json"))
    wa.ADMIN_CRED_PATH = str(tmp_path / "admin.json")
    return wa


def _set_cred(wa, username="admin", password="s3cret"):
    cred = admin_credential.create(username, password, now=0.0)
    admin_credential.save(wa.ADMIN_CRED_PATH, cred)
    return cred


# --- open mode (no credential) ----------------------------------------------

def test_open_mode_serves_and_banner_links_setup_from_loopback(tmp_path):
    wa = _wa(tmp_path)
    r = wa.app.test_client().get("/about")            # default REMOTE_ADDR=127.0.0.1
    assert r.status_code == 200
    assert b"No admin password set" in r.data
    assert b"/setup" in r.data


def test_open_mode_banner_says_local_only_from_remote(tmp_path):
    wa = _wa(tmp_path)
    r = wa.app.test_client().get("/about",
                                 environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert r.status_code == 200
    assert b"rssrob set-admin-password" in r.data
    assert b"/setup" not in r.data


# --- gate + login + logout ---------------------------------------------------

def test_protected_route_redirects_to_login_when_credential_set(tmp_path):
    wa = _wa(tmp_path); _set_cred(wa)
    r = wa.app.test_client().get("/about")
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_login_page_reachable_unauthenticated(tmp_path):
    wa = _wa(tmp_path); _set_cred(wa)
    r = wa.app.test_client().get("/login")
    assert r.status_code == 200


def test_login_wrong_credentials_rejected(tmp_path):
    wa = _wa(tmp_path); _set_cred(wa)
    client = wa.app.test_client()
    r = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert r.status_code == 200                       # re-rendered, not redirected
    assert client.get("/about").status_code == 302    # still not logged in


def test_login_then_access_then_logout(tmp_path):
    wa = _wa(tmp_path); _set_cred(wa)
    client = wa.app.test_client()
    r = client.post("/login", data={"username": "admin", "password": "s3cret"})
    assert r.status_code == 302
    assert client.get("/about").status_code == 200
    client.post("/logout")
    after = client.get("/about")
    assert after.status_code == 302 and "/login" in after.headers["Location"]


# --- first-run /setup --------------------------------------------------------

def test_setup_from_loopback_creates_credential_and_logs_in(tmp_path):
    wa = _wa(tmp_path)
    client = wa.app.test_client()
    assert client.get("/setup").status_code == 200
    r = client.post("/setup", data={"username": "admin",
                                     "password": "pw", "confirm": "pw"})
    assert r.status_code == 302
    cred = admin_credential.load(wa.ADMIN_CRED_PATH)
    assert cred is not None and admin_credential.verify(cred, "admin", "pw")
    assert client.get("/about").status_code == 200    # logged in by setup


def test_setup_password_mismatch_rerenders(tmp_path):
    wa = _wa(tmp_path)
    r = wa.app.test_client().post("/setup", data={"username": "admin",
                                                  "password": "a", "confirm": "b"})
    assert r.status_code == 200
    assert admin_credential.load(wa.ADMIN_CRED_PATH) is None


def test_setup_from_remote_is_forbidden(tmp_path):
    wa = _wa(tmp_path)
    r = wa.app.test_client().get("/setup",
                                 environ_base={"REMOTE_ADDR": "10.0.0.5"})
    assert r.status_code == 403
    assert admin_credential.load(wa.ADMIN_CRED_PATH) is None


def test_setup_redirects_to_login_when_credential_exists(tmp_path):
    wa = _wa(tmp_path); _set_cred(wa)
    r = wa.app.test_client().get("/setup")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_ignores_open_redirect_next(tmp_path):
    wa = _wa(tmp_path); _set_cred(wa)
    r = wa.app.test_client().post("/login?next=//evil.com",
                                  data={"username": "admin", "password": "s3cret"})
    assert r.status_code == 302
    assert "evil" not in r.headers["Location"]
