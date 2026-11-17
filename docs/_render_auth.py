"""Render an AWS-authentication-focused diagram as PNG (Pillow only)."""
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


FT = font(31, True); FSUB = font(16); FLBL = font(19, True)
FS = font(14); FSTEP = font(15); FNOTE = font(15, True); FTAG = font(13); FCODE = font(14, True)


def box(d, xy, r, fill, outline, w=3, dash=False):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)


def ctext(d, cx, y, t, f, fill):
    w = d.textlength(t, font=f)
    d.text((cx - w / 2, y), t, font=f, fill=fill)


def arrow(d, a, b, color, width=4, dash=False):
    import math
    x1, y1 = a; x2, y2 = b
    if dash:
        tot = math.hypot(x2 - x1, y2 - y1); dx, dy = (x2 - x1) / tot, (y2 - y1) / tot
        pos = 0
        while pos < tot - 16:
            s = (x1 + dx * pos, y1 + dy * pos)
            e = (x1 + dx * min(pos + 11, tot - 16), y1 + dy * min(pos + 11, tot - 16))
            d.line([s, e], fill=color, width=width); pos += 18
    else:
        d.line([a, b], fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1); L = 15
    d.polygon([
        (x2, y2),
        (x2 - L * math.cos(ang - math.radians(24)), y2 - L * math.sin(ang - math.radians(24))),
        (x2 - L * math.cos(ang + math.radians(24)), y2 - L * math.sin(ang + math.radians(24))),
    ], fill=color)


def render():
    W, H = 1300, 820
    img = Image.new("RGB", (W, H), "#f6f8fb"); d = ImageDraw.Draw(img)

    ctext(d, W / 2, 30, "How our server logs in to AWS", FT, "#15233f")
    ctext(d, W / 2, 76, "No passwords in the code. AWS gives temporary credentials automatically.", FSUB, "#56637a")

    # center: our code box
    box(d, (470, 130, 830, 250), 16, "#e3f2fd", "#1976d2")
    ctext(d, 650, 150, "Our MCP Server (retriever.py)", FLBL, "#0d47a1")
    ctext(d, 650, 184, "boto3.client(\"bedrock-agent-runtime\")", FCODE, "#1565c0")
    ctext(d, 650, 212, "No keys. No secrets. Just asks AWS.", FS, "#607d8b")

    # Two paths split
    ctext(d, 320, 300, "PATH A - On your laptop (now)", FNOTE, "#6a1b9a")
    ctext(d, 980, 300, "PATH B - In production (deployed)", FNOTE, "#1b5e20")

    # Path A - SSO
    box(d, (60, 340, 600, 560), 14, "#f3e5f5", "#8e24aa")
    ctext(d, 330, 362, "AWS SSO Login", FLBL, "#6a1b9a")
    stepsA = [
        "1. You run:  aws sso login --profile amplify-dev",
        "2. You sign in with your PG&E identity (browser).",
        "3. AWS hands your machine TEMPORARY keys",
        "    that expire automatically.",
        "4. boto3 finds them and uses them.",
    ]
    for i, t in enumerate(stepsA):
        d.text((82, 396 + i * 28), t, font=FS, fill="#4a148c")
    ctext(d, 330, 540, "Good for: local development & testing", FTAG, "#8e24aa")

    # Path B - IAM Role
    box(d, (700, 340, 1240, 560), 14, "#e8f5e9", "#2e7d32")
    ctext(d, 970, 362, "IAM Task Role", FLBL, "#1b5e20")
    stepsB = [
        "1. The container runs on AWS (ECS Fargate).",
        "2. An IAM Role is attached to it.",
        "3. AWS injects TEMPORARY keys into the",
        "    container automatically - and rotates them.",
        "4. boto3 finds them and uses them.",
    ]
    for i, t in enumerate(stepsB):
        d.text((722, 396 + i * 28), t, font=FS, fill="#1b5e20")
    ctext(d, 970, 540, "Best practice for: production (no human login)", FTAG, "#2e7d32")

    # arrows from paths up to code box
    arrow(d, (330, 338), (520, 252), "#8e24aa")
    arrow(d, (970, 338), (780, 252), "#2e7d32")

    # arrow from code box down to KB (vertical). Label sits just below the arrow
    # start, in the clear gap between the two path boxes (nothing to overlap there).
    arrow(d, (650, 250), (650, 608), "#37474f")
    ctext(d, 650, 280, "uses the temporary", FTAG, "#546e7a")
    ctext(d, 650, 580, "credentials to call AWS", FTAG, "#546e7a")

    box(d, (470, 610, 830, 712), 14, "#fff3e0", "#f57c00")
    ctext(d, 650, 630, "AWS Bedrock Knowledge Base", FLBL, "#e65100")
    ctext(d, 650, 662, "QKQDEAJBG9  -  allowed action: bedrock:Retrieve", FS, "#ef6c00")
    ctext(d, 650, 686, "search only - cannot change or delete anything", FTAG, "#ef6c00")

    # key message footer
    box(d, (60, 735, 1240, 805), 12, "#fffde7", "#fbc02d", 1)
    d.text((82, 750), "Key point for your manager:", font=FNOTE, fill="#2e7d32")
    d.text((82, 776), "Same code, both places. Credentials are never stored - AWS provides short-lived ones. "
                      "Permission is the minimum: search one knowledge base only.",
           font=FS, fill="#5b6b86")

    p = os.path.join(OUT, "aws-auth-flow-clean.png")
    img.save(p)
    return p


if __name__ == "__main__":
    print(render())
