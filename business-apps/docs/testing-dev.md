# Testing the Business Apps MCP server against DEV — step by step

This guide walks you from a clean checkout to a fully exercised server in the
**dev** environment: unit tests, a locally running server, AWS (Bedrock)
retrieval, the gateway API-key layer, and the new **Cognito user authentication**
(mirroring the backend).

There are three things that get "tested" and they are independent:

1. **Unit tests** — no AWS, no network. Pure logic.
2. **AWS auth (Bedrock retrieval)** — needs dev AWS creds; proves the app can
   reach the dev Knowledge Base.
3. **User auth (Cognito)** — needs a real dev Cognito access token; proves the
   server verifies end-user identity like the backend.

> Windows / PowerShell commands throughout. Run everything from
> `Amplify-MCP-Server\business-apps` unless noted.

---

## 0. Collect the DEV values first

You will plug these into `.env`. Sources are in the sibling repos.

| What | DEV value | Where it comes from |
|---|---|---|
| Region | `us-west-2` | frontend `terraform-dev.auto.tfvars` (`aws_region`) |
| Account | `<account-id>` | frontend `terraform-dev.auto.tfvars` (`account_num`) |
| Cognito **User Pool ID** | `<region>_<pool-id>` | set via `BAMCP_COGNITO_*` env var (frontend dev `main.tsx`) |
| Cognito **App Client ID** | _(set via env)_ | set via `BAMCP_COGNITO_*` env var (frontend dev `.env`) |
| userInfo URL | `https://<domain>.auth.<region>.amazoncognito.com/oauth2/userInfo` | set via `BAMCP_COGNITO_*` env var (frontend dev `.env`) |
| Business Apps **KB ID** (dev) | `XXXXXXXXXX` | Bedrock console (dev account) — the **only** value you must supply |

> The server now ships **dev** Cognito defaults, so you usually only need to set
> `BAMCP_KB_ID`. Override the Cognito values only if dev was redeployed with new ids.

### Getting the dev Cognito pool id + app client id

These are created by Terraform, so read them from the deployed dev stack:

```powershell
# Option A: from AWS (after `aws sso login` to the DEV account)
aws cognito-idp list-user-pools --max-results 20 --region us-west-2 `
  --query "UserPools[?contains(Name,'amplify-rag-dev')].{Name:Name,Id:Id}"

# then list the app client id for that pool:
aws cognito-idp list-user-pool-clients --user-pool-id us-west-2_XXXXXXXXX --region us-west-2 `
  --query "UserPoolClients[].{Name:ClientName,Id:ClientId}"
```

```powershell
# Option B: from the frontend repo's deployed outputs
cd ..\..\Amplify-RAG-frontend
terraform output | Select-String -Pattern "cognito|client|pool"
```

Or just copy them from the **frontend app's running dev `.env`** (the
`VITE_APP_CLIENT_ID` and the authority pool id in `main.tsx`), but make sure it
is the DEV build, not prod.

### Getting the dev Business Apps KB id

```powershell
aws bedrock-agent list-knowledge-bases --region us-west-2 `
  --query "knowledgeBaseSummaries[?contains(name,'business-apps')].{Name:name,Id:knowledgeBaseId}"
```

---

## 1. (No sibling repos needed)

The MCP server reads **no code** from the backend or frontend repos — it only
needs the dev **values** in the table above (set them as `BAMCP_*` env vars or in your
git-ignored `.env`). There is nothing to clone or branch-switch here.

---

## 2. Set up the Python environment

```powershell
cd ..\business-apps   # Amplify-MCP-Server\business-apps
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

> If `python` isn't found, use `py -m venv .venv`. The `.venv` is gitignored.

---

## 3. Run the unit tests (no AWS, no network)

This validates config parsing, retrieval parsing/clamping/reranking, the
API-key middleware, and the full Cognito user-auth flow (JWKS/JWT mocked).

```powershell
pytest
```

Expect **all tests passing** (38 at time of writing). This alone proves the
user-auth logic — bypass, missing/invalid header, access-token enforcement,
userInfo merge, and the always-present `Default` group — without any live
Cognito.

---

## 4. Create your DEV `.env`

```powershell
copy .env.example .env
```

Edit `.env` with the dev values from step 0:

```ini
# --- AWS / Bedrock (dev) ---
BAMCP_AWS_REGION=us-west-2
BAMCP_KB_ID=<DEV_BUSINESS_APPS_KB_ID>

# --- Gateway API key (optional for local) ---
BAMCP_API_KEYS=<your-api-key>

# --- User auth (Cognito dev) ---
BAMCP_USER_AUTH_ENABLED=true
BAMCP_COGNITO_USER_POOL_ID=<the part AFTER "us-west-2_">   # e.g. abc123DEF
BAMCP_COGNITO_APP_CLIENT_ID=<DEV_APP_CLIENT_ID>
BAMCP_COGNITO_USER_INFO_URL=https://amplify-rag-dev.auth.us-west-2.amazoncognito.com/oauth2/userInfo
BAMCP_VERIFY_SSL=true

# Dev bypass so you can test WITHOUT a real token (remove for a real-token test)
BAMCP_BYPASS_SECRET=dev-bypass-123

BAMCP_LOG_FORMAT=console
LOG_LEVEL=DEBUG
```

> `BAMCP_COGNITO_USER_POOL_ID` is only the suffix. If your full pool id is
> `us-west-2_abc123DEF`, set `BAMCP_COGNITO_USER_POOL_ID=abc123DEF`. The server
> rebuilds the full issuer URL from region + suffix (matching the backend).

---

## 5. Log in to DEV AWS (for Bedrock retrieval)

The server calls Bedrock with **its own AWS credentials** (never the user's
token). Give the shell dev creds:

```powershell
aws sso login --profile amplify-dev
$env:AWS_PROFILE = "amplify-dev"

# sanity check you're in the DEV account (<account-id>):
aws sts get-caller-identity
```

---

## 6. Start the server

```powershell
python -m business_apps_mcp.server
```

Leave it running. On startup the logs should show
`user_auth_enabled=true`, `auth_enabled=true`, the dev `kb_id`, and region.

Health check (new terminal):

```powershell
curl http://localhost:8080/health
# -> {"status":"ok","server":"business-apps","port":8080,"tools_registered":1,"tools":["search_business_apps"],...}
```

`/health` is public (no auth) by design.

---

## 7. Test the TWO auth layers at the HTTP edge

These hit `/mcp` directly to show each guard. `curl.exe` avoids the PowerShell
alias.

**7a. No API key → 401 (gateway layer):**
```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8080/mcp -X POST
# -> 401
```

**7b. API key but no/invalid user token → 401 (user-auth layer):**
```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://localhost:8080/mcp -X POST `
  -H "x-api-key: <your-api-key>" -H "Authorization: Bearer not-a-real-token"
# -> 401  (fails Cognito JWT verification)
```

**7c. API key + BYPASS token → passes user auth (dev shortcut):**
Use the MCP client in step 8 with the bypass token. The bypass proves the
pipeline works even before you have a real Cognito token.

---

## 8. Full tool call over MCP (Streamable HTTP)

Easiest is the included live smoke script. It already sends `x-api-key`; add
your token/bypass as the `Authorization` header.

Edit `tests/live_smoke.py` headers to:

```python
headers={
    "x-api-key": "<your-api-key>",
    "Authorization": "Bearer dev-bypass-123",   # bypass OR a real dev token
},
```

Then:

```powershell
python tests\live_smoke.py
```

Expected: `TOOLS: ['search_business_apps']`, a `RESULT_COUNT`, and a few
source-cited chunks from the **dev** KB. This exercises the whole chain:
gateway key → user auth → tool → Bedrock retrieve (dev) → normalized results.

Alternatively use the MCP Inspector:

```powershell
npx @modelcontextprotocol/inspector
#  Transport: Streamable HTTP
#  URL: http://localhost:8080/mcp
#  Headers: x-api-key = <your-api-key> ; Authorization = Bearer <token-or-bypass>
#  Call: search_business_apps { "query": "how do I request access to a business app?" }
```

---

## 9. Test with a REAL dev Cognito access token (the important one)

The bypass proves plumbing; this proves real user auth.

**Get an access token from dev Cognito.** The simplest path is to log into the
**dev frontend** (`https://eratest.nonprod.pge.com` or the local dev build),
open browser DevTools → Application/Storage, and copy the **access token**
(the `oidc.user:...` entry has `access_token`). It must be the *access* token,
not the id token — the server enforces `token_use == "access"`.

Then repeat step 7b / step 8 with:

```
Authorization: Bearer <paste the real dev access_token>
```

Expected results:
- A valid, unexpired dev access token → request passes, and the server logs the
  resolved `identity` (username/email) plus `custom:groups` (always includes
  `Default`).
- An **id** token instead of access → 401 "Expected an access token."
- An expired token → 401 "Token verification failed".
- A token from the **prod** pool while `.env` points at **dev** → 401
  (issuer/audience mismatch). This is the check working correctly.

Turn the bypass OFF for this test so nothing masks a real failure:
set `BAMCP_BYPASS_SECRET=` (empty) in `.env` and restart the server.

---

## 10. (Optional) Docker smoke in dev

```powershell
docker build -t business-apps-mcp .
docker run --rm -p 8080:8080 `
  -e BAMCP_KB_ID=<DEV_KB_ID> `
  -e BAMCP_AWS_REGION=us-west-2 `
  -e BAMCP_USER_AUTH_ENABLED=true `
  -e BAMCP_COGNITO_USER_POOL_ID=<suffix> `
  -e BAMCP_COGNITO_APP_CLIENT_ID=<dev client> `
  -e BAMCP_COGNITO_USER_INFO_URL=https://amplify-rag-dev.auth.us-west-2.amazoncognito.com/oauth2/userInfo `
  -e BAMCP_API_KEYS=<your-api-key> `
  -e AWS_ACCESS_KEY_ID=$env:AWS_ACCESS_KEY_ID `
  -e AWS_SECRET_ACCESS_KEY=$env:AWS_SECRET_ACCESS_KEY `
  -e AWS_SESSION_TOKEN=$env:AWS_SESSION_TOKEN `
  business-apps-mcp
```

Then repeat the `/health` and `/mcp` checks against the container.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/mcp` returns 401 with a real token | `.env` pool/client id or userInfo URL is prod, not dev | Set the dev pool suffix, client id, and `amplify-rag-dev` userInfo URL; restart |
| 401 "Expected an access token" | You pasted the **id** token | Copy the `access_token` instead |
| 401 "Public key not found in JWKS" | Wrong pool id (JWKS doesn't contain the token's `kid`) | Fix `BAMCP_COGNITO_USER_POOL_ID` |
| `RetrievalError (AccessDeniedException)` | AWS creds lack `bedrock:Retrieve` on the dev KB, or wrong account | Re-`aws sso login` to dev; confirm `sts get-caller-identity` = <account-id> |
| `RetrievalError` ResourceNotFound | `BAMCP_KB_ID` is the prod id against the dev account | Use the dev KB id from step 0 |
| userInfo call fails/timeout | `BAMCP_VERIFY_SSL` or network to the Cognito domain | Confirm the dev domain; only disable SSL verify as a last local resort |
| `python` not found | Launcher naming on Windows | Use `py` instead of `python` |

---

## What "everything tested" means here

- [ ] `pytest` green (logic, incl. user-auth flow) — step 3
- [ ] `/health` ok — step 6
- [ ] gateway key rejects missing key (401) — step 7a
- [ ] user auth rejects bad token (401) — step 7b
- [ ] bypass path returns KB results — step 8
- [ ] **real dev Cognito access token** accepted; id/expired/wrong-pool rejected — step 9
- [ ] Bedrock dev retrieval returns cited chunks — step 8/9
