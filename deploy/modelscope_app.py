"""Start the ModelScope synthetic-data Docker Studio demo."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from research_agent.hub import Hub
from research_agent.hub_api import create_app
from research_agent.hub_cli import seed_demo
from research_agent.hub_runtime import init_local, load_credentials


DATA_DIR = Path(os.environ.get("HUB_DEMO_DIR", "/tmp/chenxi-modelscope-demo"))
BASE_URL = os.environ.get("HUB_PUBLIC_URL", "http://127.0.0.1:7860")
STATIC_DIR = Path(os.environ.get("DASHBOARD_DIR", "/app/frontend/dist"))
CREDENTIALS_PATH = DATA_DIR / "access.local.json"


def csv_variable(name: str, defaults: str) -> list[str]:
    return [value.strip() for value in os.environ.get(name, defaults).split(",") if value.strip()]


base_host = urlsplit(BASE_URL).netloc
allowed_hosts = csv_variable(
    "HUB_MCP_ALLOWED_HOSTS",
    ",".join(
        value
        for value in (
            "127.0.0.1:*",
            "localhost:*",
            "[::1]:*",
            "modelscope.cn",
            "www.modelscope.cn",
            "modelscope.ai",
            "www.modelscope.ai",
            base_host,
        )
        if value
    ),
)
allowed_origins = csv_variable(
    "HUB_MCP_ALLOWED_ORIGINS",
    ",".join(
        value
        for value in (
            "http://localhost:*",
            "http://127.0.0.1:*",
            "https://modelscope.cn",
            "https://www.modelscope.cn",
            "https://modelscope.ai",
            "https://www.modelscope.ai",
            f"{urlsplit(BASE_URL).scheme}://{base_host}" if base_host else "",
        )
        if value
    ),
)

init_local(DATA_DIR, CREDENTIALS_PATH, BASE_URL)
credentials = load_credentials(CREDENTIALS_PATH)
hub = Hub(DATA_DIR, BASE_URL)
if hub.stats()["documents"] == 0:
    seed_demo(hub)

app = create_app(
    hub,
    credentials.read_token,
    credentials.admin_token,
    public_demo=True,
    dashboard_dir=STATIC_DIR,
    mcp_allowed_hosts=allowed_hosts,
    mcp_allowed_origins=allowed_origins,
    root_path=urlsplit(BASE_URL).path,
)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "7860")))
