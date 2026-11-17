"""Render the two Phase 2 flow diagrams as PNG images using Pillow (no SVG libs needed)."""
import os
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- fonts ---------------------------------------------------------------
def load_font(size, bold=False):
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()

F_TITLE = load_font(30, bold=True)
F_SUB = load_font(16)
F_BOX = load_font(17, bold=True)
F_SMALL = load_font(13)
F_EDGE = load_font(13)
F_NOTE = load_font(14)
F_PHASE = load_font(15, bold=True)

# ---- helpers -------------------------------------------------------------
def rounded(draw, box, radius, fill, outline, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def center_text(draw, cx, y, text, font, fill):
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)

def arrow(draw, start, end, color=(68, 68, 68), width=2, dash=False):
    x1, y1 = start
    x2, y2 = end
    if dash:
        # simple dashed line
        import math
        total = math.hypot(x2 - x1, y2 - y1)
        dx, dy = (x2 - x1) / total, (y2 - y1) / total
        seg, gap = 10, 6
        pos = 0
        while pos < total - 14:
            a = (x1 + dx * pos, y1 + dy * pos)
            b = (x1 + dx * min(pos + seg, total - 14), y1 + dy * min(pos + seg, total - 14))
            draw.line([a, b], fill=color, width=width)
            pos += seg + gap
    else:
        draw.line([start, (x2 - 10 * (x2 - x1 and 1), y2)], fill=color, width=width)
        draw.line([start, end], fill=color, width=width)
    # arrow head
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    L = 12
    for da in (math.radians(22), -math.radians(22)):
        hx = x2 - L * math.cos(ang - da)
        hy = y2 - L * math.sin(ang - da)
        draw.line([(x2, y2), (hx, hy)], fill=color, width=width)


# ===========================================================================
# Diagram 1: BEFORE Portkey
# ===========================================================================
def render_before():
    W, H = 1100, 620
    img = Image.new("RGB", (W, H), "#f7f9fc")
    d = ImageDraw.Draw(img)

    d.text((40, 28), "Flow - BEFORE Portkey Access (Phase 2 v1)", font=F_TITLE, fill="#1b2a4a")
    d.text((40, 70), "Direct connection: KIRO / Copilot talk straight to your MCP server over Streamable HTTP. Retrieve-only.",
           font=F_SUB, fill="#5b6b86")

    # client
    rounded(d, (40, 150, 250, 300), 14, "#ede7f6", "#7e57c2")
    center_text(d, 145, 172, "AI Applications", F_BOX, "#4527a0")
    center_text(d, 145, 205, "KIRO", F_SMALL, "#5e35b1")
    center_text(d, 145, 227, "GitHub Copilot", F_SMALL, "#5e35b1")
    center_text(d, 145, 262, "(desktop clients)", F_SMALL, "#6a4fb0")

    # server
    rounded(d, (360, 130, 610, 320), 14, "#e3f2fd", "#1976d2")
    center_text(d, 485, 150, "Internal MCP Server", F_BOX, "#0d47a1")
    center_text(d, 485, 180, "Streamable HTTP", F_SMALL, "#1565c0")
    center_text(d, 485, 205, "tool: search_business_apps", F_SMALL, "#1565c0")
    center_text(d, 485, 230, "API key auth", F_SMALL, "#1565c0")
    center_text(d, 485, 255, "retriever.py", F_SMALL, "#1565c0")
    center_text(d, 485, 288, "(runs locally / self-hosted)", F_SMALL, "#607d8b")

    # KB
    rounded(d, (720, 130, 970, 270), 14, "#e8f5e9", "#388e3c")
    center_text(d, 845, 152, "Bedrock Knowledge Base", F_BOX, "#1b5e20")
    center_text(d, 845, 185, "ID: QKQDEAJBG9", F_SMALL, "#2e7d32")
    center_text(d, 845, 208, "retrieve API", F_SMALL, "#2e7d32")
    center_text(d, 845, 231, "Titan v2 embeddings", F_SMALL, "#2e7d32")

    # OpenSearch
    rounded(d, (720, 330, 970, 450), 14, "#fff3e0", "#f57c00")
    center_text(d, 845, 352, "OpenSearch Serverless", F_BOX, "#e65100")
    center_text(d, 845, 385, "amplify-rag-business-apps", F_SMALL, "#ef6c00")
    center_text(d, 845, 408, "index (~29.7k vectors)", F_SMALL, "#ef6c00")

    # arrows
    arrow(d, (250, 225), (356, 225))
    center_text(d, 303, 198, "query", F_EDGE, "#444")
    center_text(d, 303, 232, "(MCP / HTTP)", F_EDGE, "#444")
    arrow(d, (610, 200), (716, 200))
    center_text(d, 663, 178, "retrieve()", F_EDGE, "#444")
    arrow(d, (845, 270), (845, 326))
    center_text(d, 905, 292, "vector search", F_EDGE, "#444")
    arrow(d, (716, 250), (490, 322), color=(46, 125, 50), dash=True)
    center_text(d, 610, 332, "chunks: text + score + source", F_EDGE, "#2e7d32")
    arrow(d, (360, 300), (160, 300), color=(46, 125, 50), dash=True)
    center_text(d, 258, 338, "results back to client", F_EDGE, "#2e7d32")

    # note
    rounded(d, (40, 420, 680, 575), 10, "#fffde7", "#fbc02d", width=1)
    d.text((60, 432), "What happens here", font=F_PHASE, fill="#2e7d32")
    lines = [
        "1. Developer asks a question in KIRO / Copilot.",
        "2. The tool call goes directly to your MCP server (API key checked).",
        "3. Server calls Bedrock KB retrieve -> KB embeds query + searches OpenSearch.",
        "4. Matching chunks return to the client; the client's AI writes the answer.",
        "No Portkey in the path. No answer-generation inside the server.",
    ]
    for i, t in enumerate(lines):
        d.text((60, 462 + i * 22), t, font=F_NOTE, fill="#7a4f00")

    path = os.path.join(OUT_DIR, "flow-before-portkey.png")
    img.save(path)
    return path


# ===========================================================================
# Diagram 2: AFTER Portkey
# ===========================================================================
def render_after():
    W, H = 1200, 700
    img = Image.new("RGB", (W, H), "#f7f9fc")
    d = ImageDraw.Draw(img)

    d.text((40, 28), "Flow - AFTER Portkey Access (Phase 2 target)", font=F_TITLE, fill="#1b2a4a")
    d.text((40, 70), "All traffic routes through the Portkey MCP Gateway. Same server registered behind it. retriever.py unchanged.",
           font=F_SUB, fill="#5b6b86")

    # client
    rounded(d, (40, 200, 240, 360), 14, "#ede7f6", "#7e57c2")
    center_text(d, 140, 222, "AI Applications", F_BOX, "#4527a0")
    center_text(d, 140, 255, "KIRO", F_SMALL, "#5e35b1")
    center_text(d, 140, 277, "GitHub Copilot", F_SMALL, "#5e35b1")
    center_text(d, 140, 299, "Custom Agents", F_SMALL, "#5e35b1")
    center_text(d, 140, 330, "(connect to Gateway)", F_SMALL, "#6a4fb0")

    # gateway
    rounded(d, (330, 150, 600, 420), 14, "#f3e5f5", "#8e24aa", width=3)
    center_text(d, 465, 168, "Portkey MCP Gateway", F_BOX, "#6a1b9a")
    center_text(d, 465, 194, "(CCOE managed)", F_SMALL, "#7b1fa2")
    gw = [
        "Authentication (API key / OAuth)",
        "Access control (teams / users)",
        "Tool provisioning (on/off per user)",
        "Observability (metrics, audit logs)",
        "Guardrails (rate limits, filters)",
    ]
    for i, t in enumerate(gw):
        d.text((350, 224 + i * 24), t, font=F_SMALL, fill="#6a1b9a")
    d.text((350, 352), "+ Identity Forwarding", font=F_SMALL, fill="#6a1b9a")
    d.text((350, 374), "(passes real user to the server)", font=F_SMALL, fill="#6a1b9a")

    # server
    rounded(d, (690, 150, 930, 350), 14, "#e3f2fd", "#1976d2")
    center_text(d, 810, 170, "Internal MCP Server", F_BOX, "#0d47a1")
    center_text(d, 810, 198, "Streamable HTTP", F_SMALL, "#1565c0")
    center_text(d, 810, 222, "registered with Gateway", F_SMALL, "#1565c0")
    center_text(d, 810, 246, "search_business_apps", F_SMALL, "#1565c0")
    center_text(d, 810, 270, "reads forwarded identity", F_SMALL, "#1565c0")
    center_text(d, 810, 296, "generation/ (future, via Portkey)", F_SMALL, "#90a4ae")
    center_text(d, 810, 320, "retriever.py (unchanged)", F_SMALL, "#607d8b")

    # KB
    rounded(d, (690, 430, 930, 550), 14, "#e8f5e9", "#388e3c")
    center_text(d, 810, 452, "Bedrock Knowledge Base", F_BOX, "#1b5e20")
    center_text(d, 810, 485, "ID: QKQDEAJBG9", F_SMALL, "#2e7d32")
    center_text(d, 810, 508, "retrieve API", F_SMALL, "#2e7d32")

    # OpenSearch
    rounded(d, (990, 430, 1170, 550), 14, "#fff3e0", "#f57c00")
    center_text(d, 1080, 458, "OpenSearch", F_BOX, "#e65100")
    center_text(d, 1080, 488, "business-apps", F_SMALL, "#ef6c00")
    center_text(d, 1080, 510, "index (~29.7k)", F_SMALL, "#ef6c00")

    # arrows
    arrow(d, (240, 280), (326, 280))
    center_text(d, 283, 258, "MCP / HTTP", F_EDGE, "#444")
    arrow(d, (600, 250), (686, 250))
    center_text(d, 643, 224, "forward", F_EDGE, "#444")
    center_text(d, 643, 286, "+ identity", F_EDGE, "#444")
    arrow(d, (810, 350), (810, 426))
    center_text(d, 872, 388, "retrieve()", F_EDGE, "#444")
    arrow(d, (930, 490), (986, 490))
    center_text(d, 958, 466, "search", F_EDGE, "#444")
    arrow(d, (686, 320), (604, 360), color=(46, 125, 50), dash=True)
    center_text(d, 650, 332, "chunks back", F_EDGE, "#2e7d32")

    # note
    rounded(d, (40, 470, 650, 665), 10, "#fffde7", "#fbc02d", width=1)
    d.text((60, 482), "What changes vs. before", font=F_PHASE, fill="#2e7d32")
    lines = [
        "1. Clients connect to the Portkey Gateway, not directly to your server.",
        "2. Gateway does auth, access control, guardrails, logging in one place.",
        "3. Gateway forwards the request + real user identity to your server.",
        "4. Server still calls Bedrock KB retrieve -> OpenSearch (same as before).",
        "5. Optional future: answer-generation tool whose LLM call goes via Portkey.",
        "retriever.py and the search tool are IDENTICAL to the 'before' version.",
    ]
    for i, t in enumerate(lines):
        d.text((60, 512 + i * 24), t, font=F_NOTE, fill="#7a4f00")

    path = os.path.join(OUT_DIR, "flow-after-portkey.png")
    img.save(path)
    return path


if __name__ == "__main__":
    print(render_before())
    print(render_after())
