"""Unit tests for tools.business_apps_token.get_access_token (Azure SDK is mocked)."""

from types import SimpleNamespace

import pytest

from tools import business_apps_token

ENV = {
    "ENTRA_TENANT_ID": "tenant",
    "ENTRA_CLIENT_ID": "client",
    "CERTIFICATE_DATA": "cert-data",
    "CERTIFICATE_PASSWORD": "cert-pass",
    "ENTRA_SCOPE": "api://scope/.default",
}


@pytest.fixture
def entra_env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)


def test_missing_fields_raise_and_are_named(monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant")

    with pytest.raises(RuntimeError) as exc:
        business_apps_token.get_access_token()

    message = str(exc.value)
    assert "ENTRA_CLIENT_ID" in message
    assert "ENTRA_SCOPE" in message
    assert "ENTRA_TENANT_ID" not in message


def test_success_returns_token_and_encodes_credentials(entra_env, monkeypatch):
    seen = {}

    class FakeCredential:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def get_token(self, scope):
            seen["scope"] = scope
            return SimpleNamespace(token="the-token")

    monkeypatch.setattr(business_apps_token, "CertificateCredential", FakeCredential)

    assert business_apps_token.get_access_token() == "the-token"
    assert seen["tenant_id"] == "tenant"
    assert seen["client_id"] == "client"
    assert seen["certificate_data"] == b"cert-data"
    assert seen["password"] == b"cert-pass"
    assert seen["scope"] == "api://scope/.default"


def test_token_failure_is_reraised_unchanged(entra_env, monkeypatch):
    class FailingCredential:
        def __init__(self, **kwargs):
            pass

        def get_token(self, scope):
            raise ValueError("entra rejected")

    monkeypatch.setattr(business_apps_token, "CertificateCredential", FailingCredential)

    with pytest.raises(ValueError, match="entra rejected"):
        business_apps_token.get_access_token()
