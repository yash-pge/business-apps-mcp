# Authentication and authorization model

Identical to `ecm-mcp-server`.

## 1. Incoming clients: gateway-enforced

The MCP server does **not** authenticate callers itself. Like `ecm-mcp-server`,
it relies on the platform in front of it:

| Concern | Enforced by |
|---|---|
| TLS, routing, rate limits | Portkey MCP Gateway |
| Authenticating the calling client / user | Portkey MCP Gateway + Lambda Authorizer |
| Authorizing who may use the server | Portkey MCP Gateway + Lambda Authorizer |
| Network reachability (only the gateway can reach the server) | Deployment network placement |
| Which Knowledge Base is searched | Server (`KB_ID` is server config; callers cannot change it) |
| Input bounds (query length, `max_results` 1-25) | Server |

The server binds to `127.0.0.1` by default. Binding to `0.0.0.0` is an explicit
opt-in (`MCP_HOST=0.0.0.0`), which only the container image sets. A local
`python server.py` is therefore never reachable from the network.

## 2. Service identity: Entra certificate -> retrieve API

```
certificate (env)  ->  Entra ID (CertificateCredential)  ->  access token
access token       ->  retrieve API (API_ENDPOINT, Authorization: Bearer)
retrieve API       ->  Bedrock Knowledge Base  ->  retrievalResults
```

- No client secret: the server authenticates with a certificate.
- The server holds **no AWS credentials**. The retrieve API does the Bedrock search.

| ecm-mcp-server | business-apps |
|---|---|
| `tools/ecm_token.py` | `tools/business_apps_token.py` |
| `tools/ecm_search.py` | `tools/business_apps_search.py` |
| `tools/mcp_search_gdl.py` | `tools/mcp_search_business_apps.py` |
| `tools/logging_utils.py` | `tools/logging_utils.py` |

### Environment variables

| Variable | Secret | Purpose |
|---|---|---|
| `ENTRA_TENANT_ID` | no | Entra tenant |
| `ENTRA_CLIENT_ID` | no | App registration (client) id |
| `ENTRA_SCOPE` | no | Token scope for the retrieve API |
| `CERTIFICATE_DATA` | **yes** | PEM certificate + encrypted private key |
| `CERTIFICATE_PASSWORD` | **yes** | Private key password |
| `API_ENDPOINT` | no | Retrieve API URL |
| `API_REQUEST_TIMEOUT` | no | Seconds, default 30 |
| `KB_ID` | no | Knowledge Base id |
| `RERANK` / `RERANKING_MODEL_ARN` | no | Optional reranking |
| `MCP_HOST` / `MCP_PORT` / `LOG_LEVEL` | no | Server settings |

Missing Entra variables raise `Missing required Entra ID fields: <names>`;
missing `KB_ID` / `API_ENDPOINT` raise a ToolError naming them.

### Logging and errors

- Tokens and certificate material are never logged; only the tenant id and client id.
- Auth failures are logged as `get_access_token: Entra token request failed ...` and
  returned as `search_business_apps failed during authentication: ...`.
- Retrieve API failures are logged by `search_business_apps_vectordb` and returned as
  `search_business_apps failed: ...`. The two never share a message.

### Fetching a token manually

```powershell
python -m tools.business_apps_token
```

The token is copied to the clipboard; it is never printed.
