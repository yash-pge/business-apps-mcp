"""Render the architecture overview as a PNG using Pillow (no SVG libs needed)."""
import os
from PIL import Image, ImageDraw, ImageFont

OUT = os.path.dirname(os.path.abspath(__file__))


def font(size, bold=False):
    for p in (
        ("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
        ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


FT = font(28, True); FSUB = font(15); FLBL = font(16, True)
FS = font(13); FE = font(13, True); FSEC = font(14, True); FNOTE = font(14); FTAG = font(12)


def box(d, xy, r, fill, outline, w=2, dash=False):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)


def ctext(d, cx, y, t, f, fill):
    w = d.textlength(t, font=f)
    d.text((cx - w / 2, y), t, font=f, fill=fill)


def arrow(d, a, b, color, width=2, dash=False):
    import math
    x1, y1 = a; x2, y2 = b
    if dash:
        tot = math.hypot(x2 - x1, y2 - y1); dx, dy = (x2 - x1) / tot, (y2 - y1) / tot
        pos = 0
        while pos < tot - 14:
            s = (x1 + dx * pos, y1 + dy * pos)
            e = (x1 + dx * min(pos + 9, tot - 14), y1 + dy * min(pos + 9, tot - 14))
            d.line([s, e], fill=color, width=width); pos += 15
    else:
        d.line([a, b], fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1); L = 12
    for da in (math.radians(22), -math.radians(22)):
        d.line([(x2, y2), (x2 - L * math.cos(ang - da), y2 - L * math.sin(ang - da))], fill=color, width=width)


def render():
    W, H = 1360, 860
    img = Image.new("RGB", (W, H), "#f6f8fb"); d = ImageDraw.Draw(img)
    DK = "#37474f"; GR = "#2e7d32"

    d.text((40, 24), "Business Apps MCP Server - Architecture", font=FT, fill="#15233f")
    d.text((40, 66), "How AI tools securely search the Business Apps knowledge base in AWS. Retrieve-only. No secrets in code.",
           font=FSUB, fill="#56637a")

    ctext(d, 150, 108, "DEVELOPER TOOLS", FTAG, "#78909c")
    ctext(d, 490, 108, "OUR MCP SERVER (ECS Fargate)", FTAG, "#78909c")
    ctext(d, 1050, 108, "AWS (managed services)", FTAG, "#78909c")

    # clients
    box(d, (40, 150, 260, 350), 14, "#ede7f6", "#7e57c2")
    ctext(d, 150, 166, "AI Applications", FLBL, "#4527a0")
    for i, t in enumerate(["KIRO", "GitHub Copilot", "Custom Agents"]):
        ctext(d, 150, 200 + i * 24, t, FS, "#5e35b1")
    ctext(d, 150, 288, "Ask a question;", FS, "#6a4fb0")
    ctext(d, 150, 310, "model calls the tool", FS, "#6a4fb0")

    # server big
    box(d, (320, 150, 660, 620), 16, "#e3f2fd", "#1976d2", 3)
    ctext(d, 490, 166, "business-apps MCP server", FLBL, "#0d47a1")
    ctext(d, 490, 190, "Python - fastmcp - Streamable HTTP :8080", FTAG, "#1565c0")

    box(d, (345, 220, 635, 290), 10, "#f3e5f5", "#8e24aa")
    ctext(d, 490, 234, "1. Auth middleware", FSEC, "#6a1b9a")
    ctext(d, 490, 258, "checks API key + records user identity", FS, "#6a1b9a")

    box(d, (345, 306, 635, 384), 10, "#e8f5e9", "#388e3c")
    ctext(d, 490, 320, "2. Tool: search_business_apps", FLBL, "#1b5e20")
    ctext(d, 490, 344, "query, max_results, optional filter", FS, "#2e7d32")
    ctext(d, 490, 364, "returns text + score + source", FS, "#2e7d32")

    box(d, (345, 400, 635, 488), 10, "#fff3e0", "#f57c00")
    ctext(d, 490, 414, "3. Retriever (retriever.py)", FLBL, "#e65100")
    ctext(d, 490, 438, "boto3 client - only AWS caller", FS, "#ef6c00")
    ctext(d, 490, 458, "timeouts + retries, no creds in code", FS, "#ef6c00")

    box(d, (345, 504, 485, 596), 10, "#eceff1", "#607d8b")
    ctext(d, 415, 518, "config", FS, "#455a64")
    for i, t in enumerate(["region, KB id,", "keys - all via", "env vars"]):
        ctext(d, 415, 540 + i * 18, t, FTAG, "#78909c")

    box(d, (495, 504, 635, 596), 10, "#fafafa", "#bdbdbd", 2, dash=True)
    ctext(d, 565, 518, "generation", FS, "#9e9e9e")
    for i, t in enumerate(["FUTURE: answer", "writing via", "Portkey"]):
        ctext(d, 565, 540 + i * 18, t, FTAG, "#9e9e9e")

    # aws
    box(d, (900, 200, 1200, 350), 14, "#e8f5e9", "#2e7d32")
    ctext(d, 1050, 216, "Bedrock Knowledge Base", FLBL, "#1b5e20")
    for i, t in enumerate(["ID: QKQDEAJBG9", "retrieve API", "embeds query (Titan v2)", "+ runs vector search"]):
        ctext(d, 1050, 244 + i * 24, t, FS, "#2e7d32")

    box(d, (900, 400, 1200, 520), 14, "#fff3e0", "#ef6c00")
    ctext(d, 1050, 416, "OpenSearch Serverless", FLBL, "#e65100")
    ctext(d, 1050, 444, "amplify-rag-business-apps", FS, "#ef6c00")
    ctext(d, 1050, 468, "index - ~29,700 vectors", FS, "#ef6c00")
    ctext(d, 1050, 492, "(data loaded in Phase 1)", FTAG, "#78909c")

    # arrows
    arrow(d, (260, 250), (341, 250), DK, 2); ctext(d, 300, 228, "1 ask", FE, DK)
    arrow(d, (490, 290), (490, 304), DK, 2)
    arrow(d, (490, 384), (490, 398), DK, 2)
    arrow(d, (660, 430), (896, 275), DK, 2); ctext(d, 775, 338, "2 retrieve(query)", FE, DK)
    arrow(d, (1050, 350), (1050, 398), DK, 2); ctext(d, 1118, 372, "3 search", FE, DK)
    arrow(d, (896, 305), (662, 450), GR, 2, dash=True); ctext(d, 790, 420, "4 matching chunks", FE, GR)
    arrow(d, (341, 330), (262, 300), GR, 2, dash=True); ctext(d, 300, 352, "5 results -> AI answers", FE, GR)

    # auth panel
    box(d, (40, 400, 290, 620), 12, "#ffffff", "#8e24aa")
    d.text((58, 414), "How we authenticate to AWS", font=FSEC, fill="#6a1b9a")
    d.text((58, 442), "No keys or passwords in code.", font=FNOTE, fill="#2e7d32")
    d.text((58, 466), "boto3 auto-finds credentials:", font=FNOTE, fill="#5b6b86")
    d.text((68, 490), "- Local: AWS SSO login", font=FNOTE, fill="#5b6b86")
    d.text((68, 512), "  (temporary, expires)", font=FNOTE, fill="#5b6b86")
    d.text((68, 536), "- Prod: IAM Task Role", font=FNOTE, fill="#1b5e20")
    d.text((68, 558), "  (auto, rotating, no secret)", font=FNOTE, fill="#5b6b86")
    d.text((58, 586), "Least privilege: bedrock:Retrieve", font=FNOTE, fill="#c62828")

    # bottom notes
    box(d, (320, 650, 1200, 824), 12, "#fffde7", "#fbc02d", 1)
    d.text((340, 664), "Why this is solid (best practice)", font=FSEC, fill="#2e7d32")
    notes = [
        "- Retrieve-only: the server just searches; the AI writes the answer. Small, safe scope.",
        "- Streamable HTTP: meets org MCP standard; ready to register behind the Portkey Gateway with no code change.",
        "- Layered design: separate files for config, auth, retrieval - easy to test, review, and maintain.",
        "- Runs on ECS Fargate (matches other Amplify services) with an IAM role - AWS gold standard, no stored secrets.",
        "- Minimal blast radius: can only read/search one knowledge base - never modify or delete anything.",
    ]
    for i, t in enumerate(notes):
        d.text((340, 692 + i * 24), t, font=FNOTE, fill="#5b6b86")

    p = os.path.join(OUT, "architecture-overview.png")
    img.save(p)
    return p


if __name__ == "__main__":
    print(render())
