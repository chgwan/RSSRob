"""Tests for resolve_ssl_context — picks the Werkzeug ssl_context from the
--https / --tls-cert / --tls-key CLI flags. Pure unit test (no server)."""
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_webapp():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("webapp", root / "web" / "webapp.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["webapp"] = m
    spec.loader.exec_module(m)
    return m


def test_plain_http_when_no_tls_flags():
    resolve = _load_webapp().resolve_ssl_context
    assert resolve(False) is None
    assert resolve(False, None, None) is None


def test_adhoc_when_https_only():
    resolve = _load_webapp().resolve_ssl_context
    assert resolve(True) == "adhoc"


def test_file_tuple_when_cert_and_key_given():
    resolve = _load_webapp().resolve_ssl_context
    assert resolve(False, "/p/cert.pem", "/p/key.pem") == ("/p/cert.pem", "/p/key.pem")


def test_cert_files_take_precedence_over_https():
    # --tls-cert/--tls-key win over --https when both are given.
    resolve = _load_webapp().resolve_ssl_context
    assert resolve(True, "/p/cert.pem", "/p/key.pem") == ("/p/cert.pem", "/p/key.pem")


@pytest.mark.parametrize("cert,key", [("/c.pem", None), (None, "/k.pem")])
def test_partial_cert_pair_raises(cert, key):
    resolve = _load_webapp().resolve_ssl_context
    with pytest.raises(ValueError):
        resolve(False, cert, key)
