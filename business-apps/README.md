# Business Apps MCP Server

A production-grade [Model Context Protocol](https://modelcontextprotocol.io) (MCP)
server that exposes **semantic retrieval** over the Amplify **Business Apps**
Amazon Bedrock Knowledge Base.

It runs over **Streamable HTTP** so it can be registered behind the organization's
**Portkey MCP Gateway** (per the CCOE MCP governance doc), and it also runs
standalone for local development and testing.

- **Knowledge Base:** `amplify-rag-business-apps-kb` (dev; set `BAMCP_KB_ID`)
- **Index:** `amplify-rag-business-apps-index` on OpenSearch Serverless
- **Embeddings:** `amazon.titan-embed-text-v2:0` (managed by Bedrock)
- **Scope:** retrieve-only (the calling model writes the final answer)
- **Transport:** Streamable HTTP (`/mcp`)

---

## How it works

```
AI client (KIRO / Copilot)  ->  [Portkey MCP Gateway]*  ->  this server (/mcp)
                                                                 |
                                                                 v
                                             Bedrock KB retrieve (dev KB)
                                                                 |
                                                                 v
                                      OpenSearch Serverless vector search
```

`*` The Gateway is used once Portkey access is granted. Until then, clients can
connect to this server directly. **No code change is needed to move behind the
Gateway** — see `docs/flow-before-portkey.png` and `docs/flow-after-portkey.png`.

The single tool, `search_business_apps`, calls
`bedrock-agent-runtime:Retrieve`. Bedrock embeds the query with Titan and runs
the vector search; this server normalizes the hits into typed results. There is
**no LLM call** inside the server, so Portkey is not required for retrieval.

---

## Authentication — two independent kinds

Like the Amplify backend, this server separates **who the user is** from **what
the application is allowed to do in AWS**. They never mix.

### 1. User authentication (end-user identity)

Verifies the end user with a **Cognito access token**, exactly like the backend
(`Amplify-RAG-Backend/.../authentication.py`). Enable it with
`BAMCP_USER_AUTH_ENABLED=true`. On each non-public request the server:

1. Reads `Authorization: Bearer <jwt>`.
2. Finds the signing key in the pool's JWKS (refreshing once on key rotation).
3. Verifies signature, issuer, audience (app client id) and expiry.
4. Enforces `token_use == "access"` (id tokens are rejected).
5. Fetches profile + `custom:groups` from the Cognito `userInfo` endpoint.
6. Guarantees `custom:groups` exists and contains `"Default"`.

Verified claims are attached to `request.state.claims` and the resolved
username/email becomes the request identity. A dev-only `BAMCP_BYPASS_SECRET`
shortcut (matching the backend's `BYPASS_SECRET`) skips verification for a
synthetic `test` user — never set it in production.

This lives in `user_auth.py` and is the direct counterpart of the backend's
`verify_bearer_token`.

#### Access control (does this user have access?)

Authentication proves *who* the user is; **authorization** decides whether they
may use this server. Set `BAMCP_ALLOWED_GROUPS` to a comma-separated list of
Cognito groups — a user must belong to at least one of them:

- In an allowed group → request proceeds.
- Authenticated but **not** in any allowed group → **403 Forbidden**
  (distinct from 401, which means the token itself was missing/invalid).
- `BAMCP_ALLOWED_GROUPS` empty → any authenticated user may use the server.

This mirrors the backend's group-based checks (`enforce_agent_access`,
`verify_admin`), using the same `custom:groups` claim.

### 2. AWS authentication (application identity)

The server calls Bedrock with **its own AWS credentials** resolved by boto3
(SSO profile, env vars, or an IAM role) — never the user's token. The user's
identity is never forwarded to AWS. This is unchanged and handled in
`retriever.py`.

### Gateway API key (caller authentication)

Independent of the above, `BAMCP_API_KEYS` authenticates the *caller* (the
Portkey MCP Gateway) via `x-api-key`. You can run any combination: API key only,
user auth only, both, or (local dev) neither. Both are off by default.

---

## Project layout

```
business-apps/
├── pyproject.toml            # packaging + tooling config
├── requirements.txt          # pinned runtime deps
├── .env.example              # all settings (copy to .env)
├── Dockerfile                # container image (non-root, healthcheck)
├── src/business_apps_mcp/
│   ├── server.py             # fastmcp app, /health, entry point (thin, like ecm)
│   ├── tools/                # one module per tool + registry (like ecm's tools/)
│   │   ├── __init__.py       #   registry: register_tools() / registered_tools()
│   │   └── mcp_search_business_apps.py   # the search tool: register(mcp)
│   ├── retriever.py          # Bedrock KB retrieve wrapper (only AWS caller)
│   ├── config.py             # typed settings (BAMCP_ env prefix)
│   ├── logging_config.py     # structured JSON / console logging (stdout)
│   ├── tracing.py            # trace id per request, log filter, tool wrapper
│   ├── models.py             # RetrievalResult / SearchResponse
│   ├── auth.py               # API key + Cognito user + group authorization
│   ├── user_auth.py          # Cognito access-token verification
│   └── generation/           # FUTURE: Portkey-routed answer generation (empty)
└── tests/                    # config / retriever / auth / tracing / tools tests
```

**Structure compared with `ecm-mcp-server`.** The layout deliberately mirrors it:
a thin `server.py`, a `tools/` package where each tool is a `mcp_<name>.py`
module exposing `register(mcp)`, a registry that `/health` reads, trace ids from
request headers, and `ToolError` at the tool boundary. To add a tool, create
`tools/mcp_<name>.py` and call its `register` from
`tools/__init__.py:register_tools`.

Where this server intentionally differs: it uses the standalone `fastmcp`
package (ecm uses the official `mcp` SDK), a `src/` package layout (ecm uses a
flat layout), pip/hatchling (ecm uses uv), retrieves from Bedrock directly
(ecm calls the ECM search API with an Entra token), and verifies the caller
itself (ecm leaves that to Portkey and the Lambda Authorizer).

---

## Error handling, trace correlation and logging

This follows the same approach as `ecm-mcp-server`.

### Errors

The tool never returns raw exceptions to the agent. `retriever.py` handles each
downstream failure on its own (the same layering as ecm-mcp-server's
`ecm_search.py`), and the tool turns every failure into a FastMCP `ToolError` at
its boundary. Each message is summarized, names the stage that failed, and ends
with the request's trace id. The raw AWS error message, AWS request id and stack
trace go to the log only, stamped with the same trace id.

| Stage | Failure | What the agent sees (before `(trace_id=...)`) |
|---|---|---|
| input | blank/too long query, bad `max_results`, bad filter | `search_business_apps: invalid input: <field>: <reason>` |
| authentication | missing/expired AWS credentials, access denied (401/403) | `search_business_apps failed during authentication: ...` |
| call | timeout | `search_business_apps failed: search timed out after 30s` |
| call | connection error | `... failed: could not reach the Knowledge Base service` |
| call | throttled (429) | `... failed: the Knowledge Base is busy; retry shortly` |
| call | not found (404) | `... failed: the Knowledge Base was not found` |
| call | rejected (400) | `... failed: the request was rejected as invalid (ValidationException)` |
| call | service error (5xx) | `... failed: the Knowledge Base service returned an error (HTTP 503)` |
| result | non-JSON response, malformed record | `search_business_apps failed assembling results: search returned a malformed response` |
| — | anything else (a bug) | `search_business_apps failed unexpectedly` |

The call timeout is `API_REQUEST_TIMEOUT` (or `BAMCP_REQUEST_TIMEOUT`), default
30 seconds. The AWS SDK retries throttled and transient failures up to 3 attempts
in total, so the worst-case wait is longer than one timeout.

Failures from the auth middleware are plain HTTP 401/403 responses with a JSON
body that includes `trace_id`. `FastMCP(mask_error_details=True)` is set, so
anything that escapes without a `ToolError` is hidden from the client as well.

### Trace correlation

Every HTTP request gets one trace id, and it appears on every log line that
request writes.

1. The id is taken from the first of these inbound headers:
   `x-trace-id` (what the pge-aigw-apps agents forward), `traceparent`,
   `x-request-id`, `x-amzn-trace-id`.
2. If none is present, or the value is unsafe (control characters, over 128
   characters), the server generates `gen-<uuid>`.
3. The id is returned to the caller in an `x-trace-id` response header, and
   included in 401/403 bodies and tool errors.
4. It is **not** sent to Bedrock: the retriever does not add it to the AWS
   request, so correlation covers this server's own logs only.

The server runs in **stateless** HTTP mode, so each request is handled on its
own and a request can never pick up another call's id or headers.

### Logging

Logs are written to **stdout**, one JSON object per line (set
`BAMCP_LOG_FORMAT=console` for readable local output). Every line has
`timestamp`, `level`, `name`, `message`, `trace_id`, `service`, `version`, and
any extra fields (`tool`, `user`, `status`, `elapsed_ms`, `method`, `path`,
`status_code`).

- One `business_apps_mcp.access` line per request with status and duration
  (`/health` is logged at DEBUG only).
- Each tool call logs a `start` line and an `end status=ok|error elapsed_ms=...`
  line.
- The query text is **never** logged, only `query_length`. Tokens and API keys
  are never logged.
- Stack traces stay inside one JSON line (`exc_info`), so they are not split into
  many log events.
- uvicorn's own logs use the same format. Its access log is turned off (the app
  writes its own access line), both in `python -m business_apps_mcp.server` and
  with `--no-access-log` when uvicorn is started externally.

The application does not ship logs itself. When deployed on ECS/Fargate the
container's stdout is collected by the platform's log driver. **Creating the
CloudWatch log group, retention and an ERROR alarm is deployment configuration
and is not part of this repository.**

To follow one request in CloudWatch Logs Insights:

```
fields @timestamp, level, name, message
| filter trace_id = "abc-123"
| sort @timestamp asc
```

---

## Prerequisites

- Python 3.11+
- AWS credentials with permission to call the Knowledge Base:
  - `bedrock:Retrieve` on the dev KB ARN
    (`arn:aws:bedrock:us-west-2:<account-id>:knowledge-base/<dev KB id>`)
  - `bedrock:InvokeModel` on the rerank model **only if** `BAMCP_RERANKING_ENABLED=true`
- Credentials resolved the usual boto3 way (SSO profile, env vars, or role).

---

## Setup

```powershell
# from the business-apps/ folder
python -m venv .venv
.\.venv\Scripts\Activate.ps1

pip install -e ".[dev]"      # runtime + dev (tests, lint)
# or, runtime only:
pip install -r requirements.txt

copy .env.example .env       # then edit values
```

Minimum `.env` to run:

```ini
BAMCP_AWS_REGION=us-west-2
BAMCP_KB_ID=<dev Business Apps KB id>   # REQUIRED — no default shipped
# leave BAMCP_API_KEYS empty for local dev (auth disabled), or set keys:
# BAMCP_API_KEYS=<your-api-key>
```

Make sure your AWS creds are active, e.g.:

```powershell
aws sso login --profile <your-profile>
$env:AWS_PROFILE = "<your-profile>"
```

---

## Run

```powershell
# module entrypoint (uses .env) — this is also what the Docker image runs
python -m business_apps_mcp.server

# or an external uvicorn, using the app factory
uvicorn --factory business_apps_mcp.server:create_application --host 127.0.0.1 --port 8080 --no-access-log
```

Server is now at `http://127.0.0.1:8080/mcp` with health at
`http://127.0.0.1:8080/health`. On startup it logs the endpoint, the health URL
and the registered tools:

```
Starting business-apps: endpoint=http://127.0.0.1:8080/mcp health=http://127.0.0.1:8080/health tools=['search_business_apps']
```

`GET /health` (public, no auth) returns:

```json
{
  "status": "ok",
  "server": "business-apps",
  "port": 8080,
  "version": "0.1.0",
  "tools_registered": 1,
  "tools": ["search_business_apps"],
  "timestamp": "2026-11-12T14:11:26.574640+00:00"
}
```

It deliberately does not report the Knowledge Base id or AWS region.

### Docker

```powershell
docker build -t business-apps-mcp .
docker run --rm -p 8080:8080 `
  -e BAMCP_KB_ID=<dev KB id> `
  -e BAMCP_AWS_REGION=us-west-2 `
  -e AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID `
  -e AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY `
  -e AWS_SESSION_TOKEN=$env:AWS_SESSION_TOKEN `
  business-apps-mcp
```

---

## Test

```powershell
# health check
curl http://localhost:8080/health

# full tool call via the MCP Inspector
npx @modelcontextprotocol/inspector
#  -> Transport: Streamable HTTP
#  -> URL: http://localhost:8080/mcp
#  -> (if auth on) add header x-api-key: <your key>
#  -> call search_business_apps { "query": "how do I request access to a business app?" }

# unit tests
pytest
```

---

## The tool: `search_business_apps`

| Parameter | Type | Default | Constraint | Description |
|---|---|---|---|---|
| `query` | string | — | 1–2000 characters, not blank | Natural-language search query (required). |
| `max_results` | int | `BAMCP_DEFAULT_MAX_RESULTS` (5) | 1–25 | Chunks to return; also clamped to `BAMCP_MAX_RESULTS_LIMIT`. |
| `metadata_filter` | object or null | null | validated Bedrock filter | Optional filter that narrows the search. |

The parameters are flat (not one nested object), so the bounds appear directly on
each property of the tool schema (`minLength`, `maxLength`, `minimum`,
`maximum`) with no `$ref`/`$defs`. Every value is re-validated through the
`SearchBusinessAppsInput` model before use. Invalid input is rejected before
any AWS call, with a message naming the field.

Returns a `SearchResponse`, built through typed models:

```json
{
  "query": "how do I request access to a business app?",
  "result_count": 1,
  "reranked": false,
  "results": [
    {
      "text": "To request access, open the Access Request form ...",
      "score": 0.82,
      "source_name": "access.md",
      "source_url": null,
      "metadata": { "category": "BusinessApps", "subcategory": "HR", "document_name": "Access guide" }
    }
  ]
}
```

Only declared fields are returned. Bedrock bookkeeping keys
(`x-amz-bedrock-kb-*`), data source ids and the raw storage location (S3 bucket
and key) are dropped. `source_url` is set only when the source is a user-facing
page (SharePoint, web, Confluence). A malformed upstream record fails
validation and is reported as `failed assembling results`, never passed through.
To expose another metadata attribute, declare it on `ResultMetadata` in
`models.py`.

### Using `metadata_filter` (optional, advanced)

Filtering only works on fields present in the index metadata. Example:

```json
{ "equals": { "key": "subcategory", "value": "HR" } }
```

Valid operators: `equals`, `notEquals`, `greaterThan`, `greaterThanOrEquals`,
`lessThan`, `lessThanOrEquals`, `in`, `notIn`, `startsWith`, `listContains`,
`stringContains`, and `andAll` / `orAll` (2–5 conditions each, nested at most 3
levels, 20 conditions in total). An unknown operator is rejected with an error
listing the valid ones.

**Server-side scope.** If `BAMCP_SCOPE_FILTER` is set, it is always ANDed with the
caller's filter, so a caller can narrow results but never broaden them past the
scope. An invalid scope filter stops the server at startup. Leave the caller's
filter out for a normal search of everything in scope.

---

## Register with a client

### KIRO / Claude Desktop (`mcp.json`)

Streamable HTTP server entry:

```json
{
  "mcpServers": {
    "business-apps": {
      "url": "http://localhost:8080/mcp",
      "headers": { "x-api-key": "<your-api-key>" }
    }
  }
}
```

(Drop `headers` if auth is disabled locally.)

---

## Configuration reference

All variables use the `BAMCP_` prefix (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `BAMCP_AWS_REGION` | `us-west-2` | Region of the KB. |
| `BAMCP_KB_ID` | _(required)_ | Business Apps Knowledge Base ID (dev). No default. |
| `BAMCP_DEFAULT_MAX_RESULTS` | `5` | Default chunks per query. |
| `BAMCP_MAX_RESULTS_LIMIT` | `25` | Hard cap on requested results. |
| `BAMCP_RERANKING_ENABLED` | `false` | Enable second-pass rerank (needs extra IAM). |
| `BAMCP_RERANKING_MODEL_ID` | `cohere.rerank-v3-5:0` | Rerank model when enabled. |
| `BAMCP_HOST` / `BAMCP_PORT` | `127.0.0.1` / `8080` | Bind address. Loopback by default; the Docker image sets `BAMCP_HOST=0.0.0.0`. |
| `BAMCP_MCP_PATH` | `/mcp` | MCP endpoint path. |
| `BAMCP_API_KEYS` | _(empty)_ | Comma-separated accepted keys; empty = gateway auth off (dev only). |
| `BAMCP_API_KEY_HEADER` | `x-api-key` | Header carrying the API key. |
| `BAMCP_IDENTITY_HEADER` | `x-forwarded-user` | Header carrying the forwarded user identity. |
| `BAMCP_USER_AUTH_ENABLED` | `false` | Require a valid Cognito access token per request. |
| `BAMCP_COGNITO_USER_POOL_ID` | _(set via env)_ | Dev user pool id suffix (after `<region>_`). |
| `BAMCP_COGNITO_APP_CLIENT_ID` | _(set via env)_ | Dev app client id; verified as token audience. |
| `BAMCP_COGNITO_USER_INFO_URL` | _(dev URL)_ | Cognito userInfo endpoint (groups/profile). |
| `BAMCP_JWKS_TTL_SECONDS` | `3600` | JWKS cache lifetime. |
| `BAMCP_VERIFY_SSL` | `true` | Verify TLS on JWKS/userInfo calls. |
| `BAMCP_BYPASS_SECRET` | _(empty)_ | Dev-only token that skips JWT verification. |
| `BAMCP_ALLOWED_GROUPS` | _(empty)_ | Groups allowed to use the server; empty = any authenticated user. |
| `LOG_LEVEL` (or `BAMCP_LOG_LEVEL`) | `INFO` | Log verbosity. |
| `API_REQUEST_TIMEOUT` (or `BAMCP_REQUEST_TIMEOUT`) | `30` | Seconds to wait on the Knowledge Base call (1–300). |
| `BAMCP_SCOPE_FILTER` | _(empty)_ | JSON Bedrock filter always ANDed with the caller's filter; validated at startup. |
| `BAMCP_LOG_FORMAT` | `json` | `json` (prod) or `console` (dev). |

---

## Portkey MCP Gateway registration (when access is granted)

This server already satisfies the internal-server contract from the CCOE doc:

- **Transport:** Streamable HTTP (not stdio/SSE) ✔
- **Auth:** API Key + Identity Forwarding ✔ (`auth.py` reads
  `x-api-key` and records `x-forwarded-user`)
- **Health:** `/health` for Gateway health checks ✔

Registration steps (high level):

1. Deploy this server to an internal, Gateway-reachable host (ECS/Fargate, etc.).
2. Set `BAMCP_API_KEYS` to the key(s) the Gateway will present.
3. Register the server in the Portkey MCP Gateway with its URL + `/mcp` path and
   the API key auth method; enable identity forwarding.
4. Grant teams access via Gateway workspace permissions and enable the
   `search_business_apps` tool.

No application code changes are required to go from "direct" to "behind the
Gateway".

---

## Future: answer generation via Portkey

`src/business_apps_mcp/generation/` is an intentionally empty placeholder. When
Portkey access exists, add an `answer_business_apps` tool there that retrieves
chunks (reusing `retriever.py`), builds a grounded prompt, and calls an LLM
**through Portkey**. This is additive — `retriever.py` and `search_business_apps`
stay unchanged.

---

## Security notes

- Never commit `.env` (ignored by `.gitignore`).
- Keep `BAMCP_API_KEYS` set in any shared/non-local deployment.
- The server logs the forwarded identity for audit but never logs secrets.
- IAM stays minimal (`bedrock:Retrieve`) unless reranking is enabled.
```
