"""Render a monochrome, print-friendly flow diagram for the IEEE paper."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "architecture_flow.png"
W, H = 1100, 1480
WHITE = "#ffffff"
BLACK = "#111111"
DARK = "#222222"
GRAY = "#eeeeee"

img = Image.new("RGB", (W, H), WHITE)
draw = ImageDraw.Draw(img)
font_dir = Path("/usr/share/fonts/truetype/dejavu")
FONT = ImageFont.truetype(str(font_dir / "DejaVuSans.ttf"), 23)
FONT_BOLD = ImageFont.truetype(str(font_dir / "DejaVuSans-Bold.ttf"), 25)
FONT_SMALL = ImageFont.truetype(str(font_dir / "DejaVuSans.ttf"), 19)
FONT_SMALL_BOLD = ImageFont.truetype(str(font_dir / "DejaVuSans-Bold.ttf"), 19)


def centered(text, y, font=FONT, fill=BLACK):
    draw.text((W // 2, y), text, font=font, fill=fill, anchor="mm")


def box(top, height, heading, details, width=820):
    left = (W - width) // 2
    right = left + width
    bottom = top + height
    draw.rounded_rectangle((left, top, right, bottom), radius=10, fill=WHITE, outline=BLACK, width=3)
    band_h = 48
    draw.rounded_rectangle((left + 1, top + 1, right - 1, top + band_h), radius=9, fill=GRAY)
    draw.rectangle((left + 1, top + band_h - 10, right - 1, top + band_h), fill=GRAY)
    draw.line((left, top + band_h, right, top + band_h), fill=BLACK, width=2)
    draw.text((W // 2, top + band_h // 2), heading, font=FONT_BOLD, fill=BLACK, anchor="mm")
    line_y = top + band_h + 27
    for line in details:
        draw.text((W // 2, line_y), line, font=FONT, fill=BLACK, anchor="mm")
        line_y += 34
    return left, right, bottom


def arrow_down(y1, y2, label=None):
    x = W // 2
    draw.line((x, y1, x, y2 - 14), fill=BLACK, width=3)
    draw.polygon(((x - 8, y2 - 15), (x + 8, y2 - 15), (x, y2)), fill=BLACK)
    if label:
        draw.text((x + 18, (y1 + y2) // 2), label, font=FONT_SMALL, fill=DARK, anchor="lm")


# A restrained IEEE-style figure: white background, black outlines, gray headers.
centered("RECONRELAY DATA FLOW", 36, FONT_BOLD)
centered("Browser observations flow to the server; scan events return to the browser", 72, FONT_SMALL, DARK)

_, _, bottom1 = box(110, 145, "1. BROWSER EXTENSION", [
    "Current page → DOM / script / response-metadata harvest",
    "Popup scan controls and DevTools graph display",
])
arrow_down(bottom1, 300, "HTTPS REST · JSON seed")

left2, right2, bottom2 = box(300, 150, "2. FASTAPI GATEWAY", [
    "Receives seed and applies configured target filters",
    "Jobs outside the configured list are rejected",
], width=700)

# Rejection path to the right of the gateway.
reject_left, reject_top, reject_w, reject_h = 920, 335, 150, 76
draw.line((right2, 372, reject_left, 372), fill=BLACK, width=2)
draw.polygon(((reject_left + 10, 366), (reject_left + 10, 378), (reject_left, 372)), fill=BLACK)
draw.rounded_rectangle((reject_left, reject_top, reject_left + reject_w, reject_top + reject_h), radius=7, fill=WHITE, outline=BLACK, width=2)
draw.text((reject_left + reject_w // 2, reject_top + 23), "REJECT", font=FONT_SMALL_BOLD, fill=BLACK, anchor="mm")
draw.text((reject_left + reject_w // 2, reject_top + 51), "outside target list", font=FONT_SMALL, fill=BLACK, anchor="mm")

arrow_down(bottom2, 510)
_, _, bottom3 = box(510, 115, "3. REDIS JOB QUEUE", [
    "Accepted scan jobs wait for the worker",
])
arrow_down(bottom3, 690)
_, _, bottom4 = box(690, 225, "4. PYTHON WORKER / RECURSIVE EVENT LOOP", [
    "Seed → event queue → modules → new events",
    "New events return to the queue until scan limits are reached",
    "DNS · certificate transparency · HTTP · extraction",
    "Optional TCP checks require server-side permission",
], width=900)
arrow_down(bottom4, 950)
_, _, bottom5 = box(950, 115, "5. REDIS EVENT HISTORY + PUB/SUB", [
    "Stores bounded events and publishes scan updates",
])
arrow_down(bottom5, 1130, "WSS · live events")
_, _, bottom6 = box(1130, 145, "6. BROWSER RESULTS", [
    "Popup event list and DevTools graph update as events arrive",
])

# Small figure footer clarifies the trust boundary without adding an auth UI.
draw.line((125, 1320, W - 125, 1320), fill=BLACK, width=1)
centered("The gateway filters targets and applies scan limits.", 1352, FONT_SMALL, DARK)

img.save(OUT, optimize=True)
print(OUT)
