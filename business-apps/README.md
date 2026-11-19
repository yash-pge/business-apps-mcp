# business-apps MCP server

An MCP (Model Context Protocol) server that exposes document-retrieval tools over
Streamable HTTP. It searches the Amplify **Business Apps** knowledge base through a
retrieve API whose request/response shape mirrors Amazon Bedrock's
`bedrock-agent-runtime.retrieve()` operation, and returns the matching chunks to an
MCP client.

Same architecture, folder structure, naming and auth model as `ecm-mcp-server`.
Built on [FastMCP](https://github.com/modelcontextprotocol/python-sdk)
(`mcp.server.fastmcp`).

## Endpoints

| Purpose  | Path      | Method |
|----------|-----------|--------|
| MCP      | `/mcp`    | —      |
| Health   | `/health` | GET    |

`GET /health` returns the server name, port, the registered tools and a UTC timestamp.

## Tools

| Name | Description |
|------|-------------|
| `search_business_apps` | Searches the Business Apps knowledge base. Accepts `query` (1-2000 chars) and `max_results` (1-25, default 5). Returns chunks with `text`, `score`, `source_name`, `source_url` (web/SharePoint links only) and `metadata`. |

## Authentication and authorization

See [docs/authentication.md](docs/authentication.md).

- **Incoming clients:** enforced by the Portkey MCP Gateway and Lambda Authorizer,
  exactly like `ecm-mcp-server`. The server binds to `127.0.0.1` unless `MCP_HOST`
  is set (the container sets `0.0.0.0`).
- **Service identity:** Entra ID certificate credential → access token → sent as a
  Bearer token to `API_ENDPOINT`. No client secret and no AWS credentials.

## Configuration

Environment variables, loaded from `.env` via `python-dotenv`. **Do not commit
`.env`** — it holds the certificate. Copy `.env.example` to start.

| Variable | Default | Description |
|---|---|---|
| `MCP_HOST` | `127.0.0.1` | Bind address. The container sets `0.0.0.0`. |
| `MCP_PORT` | `5000` | Port. |
| `LOG_LEVEL` | `INFO` | Python logging level. |
| `ENTRA_TENANT_ID` | — | Entra tenant id. |
| `ENTRA_CLIENT_ID` | — | Entra application (client) id. |
| `ENTRA_SCOPE` | — | Token scope requested for the retrieve API. |
| `CERTIFICATE_DATA` | — | PEM certificate + encrypted private key (**secret**). |
| `CERTIFICATE_PASSWORD` | — | Private key password (**secret**). |
| `API_ENDPOINT` | — | Retrieve API URL. |
| `API_REQUEST_TIMEOUT` | `30` | HTTP timeout in seconds. |
| `KB_ID` | — | Knowledge base id. |
| `RERANK` | `false` | Enable reranking. |
| `RERANKING_MODEL_ARN` | — | Rerank model ARN, required when `RERANK=true`. |

## Running locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt pytest pytest-cov

copy .env.example .env      # then fill in the values
python -m tools.business_apps_token   # optional: check the Entra sign-in (token -> clipboard)
python server.py
curl http://127.0.0.1:5000/health
```

## Running with Docker

```powershell
docker build -t mcp-business-apps-search .
docker run --rm -p 5000:5000 --env-file .env mcp-business-apps-search
```

## Project layout

```
business-apps/
├── server.py                         # FastMCP app, /health route, entry point
├── tools/
│   ├── __init__.py                   # Tool registry: register_tools() / registered_tools()
│   ├── logging_utils.py              # Trace ids + logging setup (same as ecm)
│   ├── mcp_search_business_apps.py   # search_business_apps tool
│   ├── business_apps_search.py       # Calls the retrieve API over HTTPS (ecm_search.py)
│   └── business_apps_token.py        # Entra CertificateCredential / access token (ecm_token.py)
├── test/                             # pytest suite
├── docs/
├── Dockerfile
├── pyproject.toml
├── requirements.txt
├── setup.py
└── sonar-project.properties
```

To add a tool, create `tools/mcp_<name>.py` exposing `register(mcp)` and call it in
`tools/__init__.py:register_tools`.

## Development

```powershell
pytest test --cov --cov-report=term-missing
```

## Differences from ecm-mcp-server

- Dependencies install with pip from `requirements.txt` (no `uv.lock` yet).
- The Entra token command never prints the token, even if no clipboard tool exists.
- No `echo` or category tools; one search tool.
