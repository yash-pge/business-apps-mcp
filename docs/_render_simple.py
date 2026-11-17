"""Render a very simple, manager-friendly flow diagram as PNG (Pillow only)."""
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


FT = font(32, True); FSUB = font(17); FLBL = font(20, True)
FEMO = font(34); FS = font(15); FSTEP = font(16); FNOTE = font(15, True)


def box(d, xy, r, fill, outline, w=3):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)


def ctext(d, cx, y, t, f, fill):
    w = d.textlength(t, font=f)
    d.text((cx - w / 2, y), t, font=f, fill=fill)


def fat_arrow(d, x1, x2, y, color):
    # horizontal chunky arrow between boxes
    d.line([(x1, y), (x2 - 14, y)], fill=color, width=5)
    d.polygon([(x2, y), (x2 - 16, y - 10), (x2 - 16, y + 10)], fill=color)


def render():
    W, H = 1280, 680
    img = Image.new("RGB", (W, H), "#f6f8fb"); d = ImageDraw.Draw(img)

    ctext(d, W / 2, 34, "How it works - in plain words", FT, "#15233f")
    ctext(d, W / 2, 82, "You ask a question. It finds the answer in our documents. Simple.", FSUB, "#56637a")

    # 4 boxes across
    y0, y1 = 150, 320
    boxes = [
        (40, 240, "#ede7f6", "#7e57c2", "You", "Ask a question in\nKIRO or Copilot", "#4527a0"),
        (330, 530, "#e3f2fd", "#1976d2", "Our MCP Server", "Receives the question\n(checks the key first)", "#0d47a1"),
        (620, 820, "#e8f5e9", "#2e7d32", "AWS Knowledge Base", "Searches all our\nBusiness Apps documents", "#1b5e20"),
        (910, 1240, "#fff3e0", "#f57c00", "Answer", "Best matching info\ncomes back to you", "#e65100"),
    ]
    emojis = ["?", ">>", "[search]", "[OK]"]
    for i, (x1, x2, fill, outline, title, sub, tcol) in enumerate(boxes):
        box(d, (x1, y0, x2, y1), 16, fill, outline)
        cx = (x1 + x2) / 2
        ctext(d, cx, y0 + 30, title, FLBL, tcol)
        for j, line in enumerate(sub.split("\n")):
            ctext(d, cx, y0 + 80 + j * 24, line, FS, tcol)
        d.text((x1 + 14, y0 + 10), f"{i+1}", font=FNOTE, fill=tcol)

    # arrows between
    midy = (y0 + y1) / 2
    fat_arrow(d, 243, 328, midy, "#90a4ae")
    fat_arrow(d, 533, 618, midy, "#90a4ae")
    fat_arrow(d, 823, 908, midy, "#2e7d32")

    # the 4 plain steps
    box(d, (40, 380, 1240, 540), 14, "#ffffff", "#cfd8dc", 2)
    d.text((64, 398), "Step by step:", font=FNOTE, fill="#15233f")
    steps = [
        "1.  You type a question in your AI tool (KIRO / Copilot).",
        "2.  Our server takes the question and asks AWS to find the most relevant document pieces.",
        "3.  AWS searches the Business Apps documents and returns the best matches (with their source).",
        "4.  Your AI tool uses those pieces to give you a clear answer.",
    ]
    for i, t in enumerate(steps):
        d.text((64, 432 + i * 26), t, font=FSTEP, fill="#37474f")

    # safety line
    box(d, (40, 560, 1240, 650), 12, "#fffde7", "#fbc02d", 1)
    d.text((64, 576), "Safe by design:", font=FNOTE, fill="#2e7d32")
    d.text((64, 604), "No passwords stored in the code. The server can only SEARCH - it can never change or delete anything.",
           font=FS, fill="#5b6b86")
    d.text((64, 626), "Login uses our secure company sign-in (AWS SSO now; an auto IAM role in production).",
           font=FS, fill="#5b6b86")

    p = os.path.join(OUT, "simple-flow.png")
    img.save(p)
    return p


if __name__ == "__main__":
    print(render())
