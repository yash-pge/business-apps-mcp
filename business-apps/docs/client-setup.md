# Connecting KIRO and VS Code Copilot to the Business Apps MCP server

The server speaks **Streamable HTTP**, so both clients use the same URL
(`http://localhost:8080/mcp`) and the same API key header.

## 0. Start the server (every session)

```powershell
aws sso login --profile amplify-dev
$env:AWS_PROFILE = "amplify-dev"

cd c:\Users\YXVF\repos\final\test\Amplify-MCP-Server\business-apps
$env:BAMCP_API_KEYS = "<your-api-key>"
python -m business_apps_mcp.server
```

Leave this terminal running. Verify: open `http://localhost:8080/health` → `{"status":"ok",...}`.

---

## 1. KIRO

KIRO reads `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user).
Create/merge this into **one** of them:

```json
{
  "mcpServers": {
    "business-apps": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "x-api-key": "<your-api-key>"
      },
      "disabled": false,
      "autoApprove": ["search_business_apps"]
    }
  }
}
```

Then: open the **MCP Servers** view in the Kiro feature panel and confirm
`business-apps` shows connected (or run the "reconnect" action). Ask Kiro
something like *"search business apps for how to request VDI access"* and it
will call the `search_business_apps` tool.

---

## 2. VS Code GitHub Copilot

VS Code reads `.vscode/mcp.json` (workspace-level) — already created in this repo:

```json
{
  "servers": {
    "business-apps": {
      "type": "http",
      "url": "http://localhost:8080/mcp",
      "headers": { "x-api-key": "<your-api-key>" }
    }
  }
}
```

Then in VS Code:

1. Open the Copilot **Chat** view and switch to **Agent** mode.
2. Click the **tools** icon — you should see `business-apps / search_business_apps`.
   - If not, run the command palette → **MCP: List Servers** → Start `business-apps`.
3. Ask: *"Use business-apps to find how to request access to a business app."*

> Requires a recent VS Code with MCP support (1.102+). If `.vscode/mcp.json`
> isn't picked up, run **MCP: Add Server → HTTP** and paste the URL + header.

---

## Notes

- Both clients point at the **same running server** — start it once, use it from
  either tool.
- The `x-api-key` value must match one of the keys in `BAMCP_API_KEYS`. For local
  testing we use a key of your choosing. For anything shared, use a real secret and
  (eventually) the Portkey Gateway.
- If a client can't connect: check the server terminal is still running and that
  `/health` responds.
