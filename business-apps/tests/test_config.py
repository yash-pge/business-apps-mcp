"""Tests for configuration parsing and helpers."""

from __future__ import annotations

import pytest

from business_apps_mcp.config import Settings


def test_api_key_set_parsing() -> None:
    s = Settings(kb_id="TESTKB", api_keys=" a , b ,, b ,c ")
    assert s.api_key_set == frozenset({"a", "b", "c"})
    assert s.auth_enabled is True


def test_auth_disabled_when_no_keys() -> None:
    s = Settings(kb_id="TESTKB", api_keys="")
    assert s.api_key_set == frozenset()
    assert s.auth_enabled is False


def test_allowed_groups_parsing() -> None:
    s = Settings(kb_id="TESTKB", allowed_groups=" A , B ,, B , C ")
    assert s.allowed_group_set == frozenset({"A", "B", "C"})
    assert s.authorization_enabled is True


def test_authorization_disabled_when_no_groups() -> None:
    s = Settings(kb_id="TESTKB", allowed_groups="")
    assert s.allowed_group_set == frozenset()
    assert s.authorization_enabled is False


def test_kb_id_is_required() -> None:
    # No prod default is shipped; an empty kb_id must fail fast.
    with pytest.raises(ValueError):
        Settings(kb_id="")


def test_no_prod_kb_id_default() -> None:
    # Guard against a prod KB id ever being reintroduced as a default.
    import inspect

    from business_apps_mcp import config as config_module

    assert "QKQDEAJBG9" not in inspect.getsource(config_module)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(None, 5), (1, 1), (10, 10), (999, 25), (0, 1), (-3, 1)],
)
def test_effective_max_results_clamping(requested: int | None, expected: int) -> None:
    s = Settings(kb_id="TESTKB", default_max_results=5, max_results_limit=25)
    assert s.effective_max_results(requested) == expected


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(kb_id="TESTKB", log_level="verbose")


def test_invalid_log_format_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(kb_id="TESTKB", log_format="xml")
