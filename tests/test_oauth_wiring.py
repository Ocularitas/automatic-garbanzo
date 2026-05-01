"""Tests for the env-driven OAuth provider wiring on the MCP server.

The wiring is feature-flagged on the three core env vars (jwks_uri, issuer,
audience). When any is unset, the server runs without auth — the URL-token
Caddy gate is the sole boundary in the POC topology. When all are set,
FastMCP's JWTVerifier validates tokens and exposes the well-known
metadata endpoint per RFC 9728.

This test exercises the flag, not actual JWT validation (which would need
a real Entra-issued token to be meaningful).
"""
from __future__ import annotations

import pytest

from mcp_servers.query.server import _build_auth_provider
from shared.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "mcp_oauth_jwks_uri": "",
        "mcp_oauth_issuer": "",
        "mcp_oauth_audience": "",
        "mcp_oauth_required_scopes": "",
    }
    return Settings(**(base | overrides))


def test_auth_disabled_when_all_oauth_env_unset() -> None:
    """POC mode: no OAuth env vars → no auth provider → no JWT validation."""
    s = _settings()
    assert s.mcp_oauth_enabled is False
    assert _build_auth_provider(s) is None


@pytest.mark.parametrize("missing", ["jwks_uri", "issuer", "audience"])
def test_auth_disabled_when_any_required_var_missing(missing: str) -> None:
    """All three of jwks_uri/issuer/audience must be set together. Partial
    config is treated as 'still POC' rather than half-secured."""
    full = {
        "mcp_oauth_jwks_uri": "https://login.microsoftonline.com/T/discovery/v2.0/keys",
        "mcp_oauth_issuer": "https://login.microsoftonline.com/T/v2.0",
        "mcp_oauth_audience": "api://contract-intel-mcp",
    }
    full[f"mcp_oauth_{missing}"] = ""
    s = _settings(**full)
    assert s.mcp_oauth_enabled is False
    assert _build_auth_provider(s) is None


def test_auth_provider_built_when_all_three_set() -> None:
    s = _settings(
        mcp_oauth_jwks_uri="https://login.microsoftonline.com/T/discovery/v2.0/keys",
        mcp_oauth_issuer="https://login.microsoftonline.com/T/v2.0",
        mcp_oauth_audience="api://contract-intel-mcp",
    )
    assert s.mcp_oauth_enabled is True
    provider = _build_auth_provider(s)
    assert provider is not None
    # Smoke-check the provider is configured with what we passed.
    assert getattr(provider, "audience", None) == "api://contract-intel-mcp"


def test_required_scopes_parsed_from_csv() -> None:
    s = _settings(
        mcp_oauth_jwks_uri="https://x/keys",
        mcp_oauth_issuer="https://x/v2.0",
        mcp_oauth_audience="api://x",
        mcp_oauth_required_scopes="corpus.read,  contracts.list  ,",
    )
    assert s.mcp_oauth_required_scopes_list == ["corpus.read", "contracts.list"]


def test_empty_required_scopes_is_empty_list() -> None:
    s = _settings(
        mcp_oauth_jwks_uri="https://x/keys",
        mcp_oauth_issuer="https://x/v2.0",
        mcp_oauth_audience="api://x",
        mcp_oauth_required_scopes="",
    )
    assert s.mcp_oauth_required_scopes_list == []
