"""Answer-generation (FUTURE — intentionally not wired up in v1).

Scope today
-----------
This MCP server is **retrieve-only**. The ``search_business_apps`` tool returns
source chunks and the *calling* model (KIRO / Copilot / an agent) writes the
final answer. There is deliberately no LLM call inside this server yet.

When to implement
-----------------
Once the team has Portkey (MCP Gateway + LLM gateway) access, an optional
``answer_business_apps`` tool can be added that:

    1. Calls ``retriever.get_retriever().search(...)`` to fetch chunks.
    2. Builds a grounded prompt from those chunks.
    3. Invokes an LLM **through Portkey** (not boto3 bedrock-runtime directly),
       mirroring the backend's pattern (provider="bedrock-dev", a Portkey
       virtual key, fallbacks, cost tracking).

Why isolate it here
-------------------
Keeping generation in its own module means adding it later does NOT touch
``retriever.py`` or the existing search tool. The switch to Portkey is additive.

Reference sketch (do NOT uncomment until Portkey access + config exist):

    # from portkey_ai import Portkey
    #
    # def generate_answer(query: str, chunks: list[str], *, settings) -> str:
    #     client = Portkey(
    #         api_key=settings.portkey_api_key,
    #         virtual_key=settings.portkey_virtual_key,
    #     )
    #     context = "\n\n".join(chunks)
    #     resp = client.chat.completions.create(
    #         model=settings.generation_model,
    #         messages=[
    #             {"role": "system", "content": "Answer only from the context. Cite sources."},
    #             {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
    #         ],
    #     )
    #     return resp.choices[0].message.content

This file currently exports nothing runtime-relevant on purpose.
"""

__all__: list[str] = []
