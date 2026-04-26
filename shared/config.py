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


def get_settings() -> Settings:
    return Settings()
