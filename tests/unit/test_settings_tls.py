"""DB TLS: the switch, its refusals, and -- the part that carries the promise -- what the
driver actually ends up with."""

import shutil
import ssl
import subprocess

import pymysql
import pytest
from pydantic import ValidationError

from aim.boundaries.db.engine import create_db_engine
from aim.boundaries.settings import Settings

BASE = {"db_host": "db.example", "db_username": "aim", "db_password": "s3cret@:x"}


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    """A throwaway self-signed CA + key, enough for pymysql to build its SSL context."""
    openssl = shutil.which("openssl")
    assert openssl, "openssl is needed to generate test certificates"
    d = tmp_path_factory.mktemp("certs")
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=aim-test", "-keyout", d / "key.pem", "-out", d / "cert.pem"],
        check=True, capture_output=True,
    )
    path = str(d / "cert.pem")
    return {"db_ssl_ca": path, "db_ssl_cert": path, "db_ssl_key": str(d / "key.pem")}


def _settings(**kwargs) -> Settings:
    return Settings(_env_file=None, **BASE, **kwargs)


def test_db_tls_has_no_default():
    with pytest.raises(ValidationError, match="db_tls"):
        _settings()


def test_verified_needs_all_three_paths(certs):
    with pytest.raises(ValidationError, match="missing: DB_SSL_KEY"):
        _settings(db_tls="verified", db_ssl_ca=certs["db_ssl_ca"], db_ssl_cert=certs["db_ssl_cert"])


def test_blank_paths_count_as_missing():
    with pytest.raises(ValidationError, match="missing: DB_SSL_CA, DB_SSL_CERT, DB_SSL_KEY"):
        _settings(db_tls="verified", db_ssl_ca=" ", db_ssl_cert="", db_ssl_key="")


def test_verified_refuses_a_path_that_is_not_a_file(certs, tmp_path):
    with pytest.raises(ValidationError, match="DB_SSL_CA=.* is not a file"):
        _settings(db_tls="verified", **{**certs, "db_ssl_ca": str(tmp_path)})


def test_off_refuses_certificate_material(certs):
    with pytest.raises(ValidationError, match="DB_TLS=off while DB_SSL_CA"):
        _settings(db_tls="off", db_ssl_ca=certs["db_ssl_ca"])


def test_the_password_stays_out_of_repr_and_errors():
    assert "s3cret" not in repr(_settings(db_tls="off"))
    with pytest.raises(ValidationError) as exc:
        _settings(db_tls="nope")
    assert "s3cret" not in str(exc.value)


def _driver_kwargs(settings: Settings, monkeypatch) -> dict:
    """What SQLAlchemy hands to pymysql.connect when the engine opens a connection."""
    seen = {}

    def fake_connect(*args, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop before the network")

    monkeypatch.setattr(pymysql, "connect", fake_connect)
    engine = create_db_engine(settings.db_url, tls_args=settings.db_tls_args)
    with pytest.raises(Exception, match="stop before the network"):
        engine.connect()
    return seen


def test_verified_reaches_the_driver_as_a_certificate_checking_context(certs, monkeypatch):
    """The effect, not the declaration: pymysql builds its context from what arrives."""
    kwargs = _driver_kwargs(_settings(db_tls="verified", **certs), monkeypatch)
    assert kwargs["ssl"]["ca"] == certs["db_ssl_ca"]
    assert kwargs["init_command"] == "SET time_zone = '+00:00'"

    conn = pymysql.connections.Connection(defer_connect=True, **kwargs)
    assert conn.ctx.verify_mode is ssl.CERT_REQUIRED
    assert conn.ctx.check_hostname is True


def test_off_reaches_the_driver_as_ssl_disabled(monkeypatch):
    kwargs = _driver_kwargs(_settings(db_tls="off"), monkeypatch)
    assert kwargs["ssl_disabled"] is True
    assert "ssl" not in kwargs


def test_why_empty_is_refused_pymysql_without_ssl_args_verifies_nothing():
    """Pins the reason for the whole rule: no `ssl` argument => TLS when offered, but
    CERT_NONE and no hostname check. If a pymysql upgrade changes this, revisit the docs."""
    conn = pymysql.connections.Connection(defer_connect=True, host="db.example")
    assert conn.ctx.verify_mode is ssl.CERT_NONE
    assert conn.ctx.check_hostname is False


def test_the_engine_refuses_empty_tls_args():
    with pytest.raises(ValueError, match="unverified"):
        create_db_engine("mysql+pymysql://u:p@h/db", tls_args={})
