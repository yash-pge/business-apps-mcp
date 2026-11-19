# Connecting Kiro and VS Code Copilot to the Business Apps MCP server

The server speaks **Streamable HTTP** at `http://localhost:5000/mcp`. Like
`ecm-mcp-server`, it does not check caller credentials itself (the Portkey
gateway does that in deployed environments), so local clients need no headers.

## 0. Start the server

```powershell
cd business-apps
python server.py
```

Settings come from `business-apps/.env` (see `.env.example`). Verify:
`http://localhost:5000/health` returns `{"status":"ok",...}`.

## 1. Kiro

Add to `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (user):

```json
{
  "mcpServers": {
    "business-apps": {
      "url": "http://localhost:5000/mcp",
      "disabled": false,
      "autoApprove": ["search_business_apps"]
    }
  }
}
```

## 2. VS Code Copilot

`.vscode/mcp.json`:

```json
{
  "servers": {
    "business-apps": {
      "type": "http",
      "url": "http://localhost:5000/mcp"
    }
  }
}
```

## Notes

- Start the server once and use it from either client.
- If a client can't connect, check the server terminal and `/health`.
