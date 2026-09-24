# Hugging Face Docker Space

This deployment serves the Dashboard, Retrieval Hub HTTP API, and read-only MCP endpoint from one Docker Space on port `7860`. It seeds only the repository's fictional demo records. It does not read a workstation folder, OneDrive, real PDFs, or an existing Hub database. The Space's demo API allows anonymous searches and policy edits; source management, imports, and deletes still require the hidden admin token. Runtime policy is stored under `/tmp` and may reset when the Space restarts.

## Create and push the Space

1. Create a new Space in the Hugging Face account or organization that will own it. Select **Docker**, keep visibility **Private** unless the owner explicitly wants a public demo, and use CPU Basic hardware. The Space README already declares `sdk: docker` and `app_port: 7860`.
2. Docker Spaces require an eligible paid account or organization plan to create, even though CPU Basic has no hourly compute charge. Confirm the account is eligible before creating it.
3. Add the Hugging Face Space Git URL as a remote, then push this repository's deployment branch to the Space's `main` branch. Authenticate locally with a Hugging Face write token; never add it to this repository or paste it into chat.
4. The launcher reads Hugging Face's `SPACE_HOST` at runtime to generate public document URLs and validate MCP requests. `HUB_PUBLIC_URL` can override it if the Space uses a custom domain. The MCP endpoint is the Space host plus `/mcp`.

The Docker context excludes local databases, PDFs, secrets, development artifacts, and dependency folders. Review `git status` and `git ls-files` before pushing; only tracked project files should be uploaded.

## Local verification

Build from the repository root and run the container on port `7860`. Then verify `/`, `/healthz`, `/api/status`, `/api/search`, and `/mcp`. No actual Hugging Face Space is created by the local build.
