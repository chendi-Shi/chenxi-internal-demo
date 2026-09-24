# Connect the local Retrieval Hub to ChatGPT

The backend already exposes a read-only MCP server with `search`, `search_documents`, and `fetch`. For a private document library, connect it through OpenAI Secure MCP Tunnel. The PDFs and SQLite index stay on the machine running the hub; query text, matching snippets, and any full text returned by `fetch` are sent through ChatGPT when requested.

## Requirements

- The local Retrieval Hub has imported the approved documents and its stdio MCP command works.
- The OpenAI Platform account can create/use tunnels and has a ChatGPT workspace association for the tunnel.
- ChatGPT developer mode is available for the account. Personal Pro accounts can connect read/fetch MCP tools in developer mode; workspace plans may require an administrator to enable it.
- The official `tunnel-client` binary is installed on the same machine as the private database.

## Connect

1. In [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels), create a tunnel and associate it with the ChatGPT workspace/account that will use it. Create a runtime key with tunnel-use access. Keep the tunnel ID and key in the local operator environment; never commit either value.
2. Download `tunnel-client` from the Platform page or the official [OpenAI tunnel-client releases](https://github.com/openai/tunnel-client). Follow `tunnel-client help quickstart` and the current OpenAI [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) to create a local stdio profile.
3. Configure that profile to launch this MCP command, replacing the paths with the local values from the generated `mcp-client.local.json`:

   ```text
   <python-executable> -m research_agent.hub_cli --data-dir <private-hub-data-directory> stdio
   ```

   Set `PYTHONPATH` to `backend/src` and `PYTHONIOENCODING=utf-8` in the local profile. Keep the runtime key out of the profile if the client supports an environment/secret-store reference.
4. Run `tunnel-client doctor` for the selected profile, then keep `tunnel-client run` active while using ChatGPT. The tunnel makes outbound connections; it does not expose the local MCP server as a public HTTP service.
5. In ChatGPT, enable developer mode if needed, open Plugins/Apps, create a custom app, choose **Tunnel**, and select or enter the tunnel ID. Review the discovered tools and keep them read-only.
6. To use it from an existing GPT, open that GPT's editor and enable the connected app if the editor offers Apps for that GPT. Personal Pro accounts can edit existing GPTs only when account and permission settings allow it; new GPT creation is not available on personal plans.

## Verify

- Confirm ChatGPT discovers exactly `search`, `search_documents`, and `fetch`.
- Ask a short question, inspect the returned snippet and page citation, then fetch the original document to check the citation against the source.
- Confirm the response treats document text as untrusted content, distinguishes source-document instructions from the user's request, and cites source plus page rather than inventing unsupported claims.
- Repeat a search after changing the Dashboard retrieval policy to confirm the new `policy_version` is used.

`fetch` returns the full source document. Use it only when the question requires original context, and review your organization’s data rules before connecting internal materials to ChatGPT. Disconnect the app or stop `tunnel-client` to end access.
