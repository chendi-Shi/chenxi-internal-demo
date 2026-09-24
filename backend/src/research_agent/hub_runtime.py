"""Local credentials and client configuration, without modifying any user's MCP settings."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

from pydantic import Field, model_validator

from .hub import HubError
from .models import StrictModel


class HubCredentials(StrictModel):
    read_token: str = Field(pattern=r"^[A-Za-z0-9._~-]{32,256}$")
    admin_token: str = Field(pattern=r"^[A-Za-z0-9._~-]{32,256}$")

    @model_validator(mode="after")
    def distinct(self):
        if self.read_token == self.admin_token:
            raise ValueError("read_and_admin_tokens_must_differ")
        return self


def load_credentials(path: Path) -> HubCredentials:
    values = {}
    if path.exists():
        if path.stat().st_size > 4096:
            raise HubError("credentials_file_too_large")
        values = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(values, dict):
        raise HubError("invalid_credentials_file")
    for field, variable in (
        ("read_token", "RESEARCH_HUB_READ_TOKEN"),
        ("admin_token", "RESEARCH_HUB_ADMIN_TOKEN"),
    ):
        if os.environ.get(variable):
            values[field] = os.environ[variable]
    if not values:
        raise HubError("credentials_required_run_init")
    return HubCredentials.model_validate(values)


def init_local(directory: Path, credentials_path: Path, base_url: str):
    directory.mkdir(parents=True, exist_ok=True)
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        # Exclusive creation never rotates or overwrites an existing credential file.
        fd = os.open(credentials_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        load_credentials(credentials_path)
    else:
        credentials = HubCredentials(
            read_token=secrets.token_urlsafe(32), admin_token=secrets.token_urlsafe(32)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(credentials.model_dump_json(indent=2) + "\n")
        created = True
    config_path = directory / "mcp-client.local.json"
    config = {
        "mcpServers": {
            "internal-research": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "research_agent.hub_cli",
                    "--data-dir",
                    str(directory.resolve()),
                    "--base-url",
                    base_url,
                    "stdio",
                ],
                "env": {
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                    "PYTHONIOENCODING": "utf-8",
                },
            }
        }
    }
    if not config_path.exists():
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "credentials_created": created,
        "credentials_file": str(credentials_path.resolve()),
        "mcp_client_config": str(config_path.resolve()),
        "next": "research-hub serve",
    }
