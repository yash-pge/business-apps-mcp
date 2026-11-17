"""Manual live smoke test: call search_business_apps over Streamable HTTP.

Requires a running server and active AWS creds. NOT part of the unit suite
(needs network + AWS). Run with:

    $env:AWS_PROFILE="amplify-dev"
    $env:BAMCP_CLIENT_API_KEY="<key accepted by the server>"
    python tests/live_smoke.py
"""

import asyncio
import json
import os

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def main() -> None:
    transport = StreamableHttpTransport(
        "http://127.0.0.1:8080/mcp",
        headers={"x-api-key": os.environ["BAMCP_CLIENT_API_KEY"]},
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        print("TOOLS:", [t.name for t in tools])

        result = await client.call_tool(
            "search_business_apps",
            {"query": "how do I request access to a business app", "max_results": 3},
        )
        data = result.structured_content or {}
        print("RESULT_COUNT:", data.get("result_count"))
        print("RERANKED:", data.get("reranked"))
        for i, r in enumerate(data.get("results", []), 1):
            print(f"[{i}] score={r.get('score')} source={ (r.get('source_uri') or '')[:80] }")
            print("    ", (r.get("text") or "")[:100].replace("\n", " "))
        print("\nRAW (truncated):", json.dumps(data)[:300])


if __name__ == "__main__":
    asyncio.run(main())
