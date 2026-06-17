"""Tests for the admin login credential module."""
from rssrob import admin_credential as ac


def test_create_and_verify_roundtrip():
    cred = ac.create("admin", "s3cret", now=1000.0)
    assert cred.username == "admin"
    assert cred.updated_at == 1000.0
    assert cred.secret_key                      # generated
    assert ac.verify(cred, "admin", "s3cret") is True


def test_verify_rejects_wrong_password():
    cred = ac.create("admin", "s3cret", now=0.0)
    assert ac.verify(cred, "admin", "nope") is False


def test_verify_rejects_wrong_username():
    cred = ac.create("admin", "s3cret", now=0.0)
    assert ac.verify(cred, "root", "s3cret") is False


def test_verify_none_credential_is_false():
    assert ac.verify(None, "admin", "s3cret") is False


def test_create_rejects_empty():
    import pytest
    with pytest.raises(ValueError):
        ac.create("", "pw", now=0.0)
    with pytest.raises(ValueError):
        ac.create("admin", "", now=0.0)


def test_hash_string_shape():
    h = ac.hash_password("s3cret")
    parts = h.split("$")
    assert parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) > 0                    # iterations
    assert len(parts) == 4                       # algo$iters$salt$hash


def test_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "admin.json")
    cred = ac.create("admin", "s3cret", now=42.0)
    ac.save(path, cred)
    loaded = ac.load(path)
    assert loaded == cred
    assert ac.verify(loaded, "admin", "s3cret") is True


def test_load_missing_returns_none(tmp_path):
    assert ac.load(str(tmp_path / "nope.json")) is None


def test_create_preserves_passed_secret_key():
    cred = ac.create("admin", "pw", now=0.0, secret_key="KEEP")
    assert cred.secret_key == "KEEP"
