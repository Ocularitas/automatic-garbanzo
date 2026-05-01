"""Centralised settings. Reads from environment / .env."""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://contract:contract@localhost:5432/contract_intel"
    )

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-7"

    voyage_api_key: str = ""
    voyage_embedding_model: str = "voyage-3-large"
    voyage_embedding_dimensions: int = 1024

    watch_folder: Path = Path("./data/watch")
    rules_dir: Path = Path("./rules")

    demo_user_id: str = "demo-user"
    demo_group_id: str = "demo-group"

    query_mcp_host: str = "0.0.0.0"
    query_mcp_port: int = 8765
    query_mcp_bearer_token: str = "demo-token-change-me"

    # Public base URL the deployment is reachable on, e.g.
    #   https://cipoc-abc123.uksouth.cloudapp.azure.com/<bearer-token>
    # Used by the MCP server to construct clickable `document_url` values
    # in tool responses. Empty in local dev — URLs are simply omitted then.
    public_base_url: str = ""

    # OAuth / Entra production hooks. When all three (jwks_uri, issuer,
    # audience) are set, the MCP server enables JWT validation and exposes
    # /.well-known/oauth-protected-resource for client discovery (RFC 9728).
    # When any is unset, OAuth is disabled and the URL-token Caddy gate is
    # the sole auth layer (POC mode).
    #
    # Typical Entra values:
    #   issuer:    https://login.microsoftonline.com/<tenant-id>/v2.0
    #   jwks_uri:  https://login.microsoftonline.com/<tenant-id>/discovery/v2.0/keys
    #   audience:  the App ID URI of your registered MCP API
    #              (e.g. api://contract-intel-mcp), or the application's client_id
    mcp_oauth_jwks_uri: str = ""
    mcp_oauth_issuer: str = ""
    mcp_oauth_audience: str = ""
    # CSV. Empty means "any token with the right audience and issuer is ok."
    # Scope strings should match what's in the Entra app registration.
    mcp_oauth_required_scopes: str = ""

    @property
    def mcp_oauth_enabled(self) -> bool:
        return bool(
            self.mcp_oauth_jwks_uri
            and self.mcp_oauth_issuer
            and self.mcp_oauth_audience
        )

    @property
    def mcp_oauth_required_scopes_list(self) -> list[str]:
        return [s.strip() for s in self.mcp_oauth_required_scopes.split(",") if s.strip()]


def get_settings() -> Settings:
    return Settings()
