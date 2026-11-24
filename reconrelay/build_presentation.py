"""Generate an editable project-viva presentation for ReconRelay."""
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt

ROOT = Path(__file__).parent
OUT = ROOT / "ReconRelay_Project_Presentation.pptx"
FLOW = ROOT / "architecture_flow.png"

# Restrained dark navy + teal palette, with high-contrast readable text.
NAVY = RGBColor(16, 25, 43)
PANEL = RGBColor(27, 39, 61)
PANEL2 = RGBColor(35, 50, 74)
WHITE = RGBColor(247, 249, 252)
MUTED = RGBColor(177, 190, 208)
TEAL = RGBColor(64, 214, 188)
AMBER = RGBColor(255, 190, 92)
RED = RGBColor(255, 117, 117)
DARK = RGBColor(20, 29, 44)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
prs.core_properties.title = "ReconRelay Project Presentation"
prs.core_properties.subject = "Browser-extension reconnaissance workflow prototype"
prs.core_properties.author = "[Student names]"
prs.core_properties.keywords = "ReconRelay, browser extension, project presentation"
BLANK = prs.slide_layouts[6]


def add_box(slide, x, y, w, h, fill, radius=True, line=None, line_width=1):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE
    shp = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shp.fill.solid()
    shp.fill.fore_color.rgb = fill
    shp.line.color.rgb = line or fill
    shp.line.width = Pt(line_width)
    if radius:
        try:
            shp.adjustments[0] = 0.12
        except (IndexError, TypeError):
            pass
    return shp


def add_text(slide, text, x, y, w, h, *, size=18, color=WHITE, bold=False,
             font="Aptos", align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP,
             margin=0, italic=False, char_spacing=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(margin)
    tf.margin_top = tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    for idx, line in enumerate(str(text).split("\n")):
        p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
        p.text = line
        p.alignment = align
        p.space_after = Pt(3 if line else 0)
        for run in p.runs:
            run.font.name = font
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.italic = italic
            run.font.color.rgb = color
            if char_spacing is not None:
                run._r.get_or_add_rPr().set("spc", str(char_spacing))
    return box


def add_base(title, eyebrow, n, total=11, *, subtitle=None):
    slide = prs.slides.add_slide(BLANK)
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = NAVY
    add_text(slide, eyebrow.upper(), 0.68, 0.37, 7.6, 0.3,
             size=10, color=TEAL, bold=True, char_spacing=120)
    add_text(slide, title, 0.68, 0.78, 11.95, 0.66,
             size=28, color=WHITE, bold=True)
    if subtitle:
        add_text(slide, subtitle, 0.7, 1.42, 11.7, 0.38,
                 size=13, color=MUTED)
    # Teal rule and discreet footer.
    add_box(slide, 0.7, 1.84 if subtitle else 1.56, 0.72, 0.045, TEAL, radius=False)
    add_text(slide, "RECONRELAY  /  PROJECT PRESENTATION", 0.7, 7.13, 6.0, 0.2,
             size=8, color=MUTED, bold=True, char_spacing=50)
    add_text(slide, f"{n:02d}  /  {total:02d}", 11.72, 7.1, 0.92, 0.23,
             size=9, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)
    return slide


def add_card(slide, x, y, w, h, heading, body, *, accent=TEAL, icon=None, body_size=15):
    add_box(slide, x, y, w, h, PANEL, line=PANEL2)
    add_box(slide, x, y, 0.055, h, accent, radius=False)
    top = y + 0.22
    if icon:
        add_text(slide, icon, x + 0.22, top, 0.48, 0.35, size=18, color=accent, bold=True)
        hx = x + 0.78
        hw = w - 1.0
    else:
        hx = x + 0.25
        hw = w - 0.5
    add_text(slide, heading, hx, top, hw, 0.42, size=17, color=WHITE, bold=True)
    add_text(slide, body, x + 0.25, y + 0.83, w - 0.5, h - 1.0,
             size=body_size, color=MUTED)


def add_pill(slide, text, x, y, w, *, color=TEAL, fill=PANEL2):
    add_box(slide, x, y, w, 0.35, fill, line=fill)
    add_text(slide, text, x + 0.08, y + 0.035, w - 0.16, 0.26,
             size=10, color=color, bold=True, align=PP_ALIGN.CENTER)


def add_arrow(slide, x1, y1, x2, y2, color=TEAL, width=2):
    line = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                      Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    line.line.color.rgb = color
    line.line.width = Pt(width)
    line.line.end_arrowhead = True
    return line


# 1 — title
slide = prs.slides.add_slide(BLANK)
slide.background.fill.solid(); slide.background.fill.fore_color.rgb = NAVY
add_box(slide, 0, 0, 0.16, 7.5, TEAL, radius=False)
add_text(slide, "PROJECT PRESENTATION  /  CYBER SECURITY", 0.9, 0.72, 7.2, 0.35,
         size=11, color=TEAL, bold=True, char_spacing=100)
add_text(slide, "ReconRelay", 0.86, 1.45, 7.9, 1.0, size=44, color=WHITE, bold=True)
add_text(slide, "A browser-extension client for a\nserver-side reconnaissance workflow", 0.92, 2.52, 7.3, 1.2,
         size=25, color=MUTED)
add_box(slide, 0.94, 4.12, 0.9, 0.055, TEAL, radius=False)
add_text(slide, "[STUDENT NAMES]  ·  [DEPARTMENT / COLLEGE]", 0.94, 4.48, 7.8, 0.4,
         size=15, color=WHITE, bold=True)
add_text(slide, "[GUIDE NAME]  ·  [MONTH YEAR]", 0.94, 5.0, 7.8, 0.35,
         size=12, color=MUTED)
# Abstract, editorial motif on right side.
add_box(slide, 9.25, 1.25, 3.1, 4.95, PANEL, line=PANEL2)
for idx, (label, color) in enumerate([
    ("BROWSER", TEAL), ("GATEWAY", AMBER), ("WORKER", TEAL), ("LIVE EVENTS", AMBER)
]):
    yy = 1.72 + idx * 1.07
    add_box(slide, 9.7, yy, 2.2, 0.58, PANEL2, line=PANEL2)
    add_text(slide, label, 9.82, yy + 0.13, 1.95, 0.28,
             size=12, color=color, bold=True, align=PP_ALIGN.CENTER)
    if idx < 3:
        add_arrow(slide, 10.8, yy + 0.61, 10.8, yy + 0.99, color=TEAL, width=1.5)
add_text(slide, "COLLECT  →  PROCESS  →  STREAM", 9.5, 6.42, 2.6, 0.3,
         size=9, color=MUTED, bold=True, align=PP_ALIGN.CENTER)

# 2 — problem
slide = add_base("Why this project?", "01  /  CONTEXT", 2,
                 subtitle="Page-level clues are useful, but moving them into a trackable workflow can be manual.")
add_text(slide, "THE GAP", 0.78, 2.2, 1.55, 0.3, size=10, color=AMBER, bold=True, char_spacing=100)
add_text(slide, "Browser observations\n→ organized follow-up", 0.78, 2.66, 4.65, 1.45,
         size=29, color=WHITE, bold=True)
add_text(slide, "ReconRelay explores a direct hand-off from the page being inspected to a bounded server job, with results returned live.",
         0.82, 4.42, 4.55, 1.05, size=16, color=MUTED)
add_card(slide, 6.0, 2.18, 2.85, 1.65, "Scattered clues", "Links, scripts, forms and response metadata live in different places.", accent=TEAL, body_size=13)
add_card(slide, 9.15, 2.18, 3.25, 1.65, "Manual hand-off", "Copying observations into a separate workflow adds friction.", accent=AMBER, body_size=13)
add_card(slide, 6.0, 4.12, 2.85, 1.65, "Hard to follow", "As findings grow, relationships and scan progress can be harder to track.", accent=AMBER, body_size=13)
add_card(slide, 9.15, 4.12, 3.25, 1.65, "Prototype goal", "Connect browser context to bounded processing and a live event view.", accent=TEAL, body_size=13)

# 3 — objectives
slide = add_base("Project objectives", "02  /  SCOPE", 3,
                 subtitle="A compact prototype focused on the browser-to-server loop.")
objectives = [
    ("01", "Collect", "Gather selected page clues only when the operator invokes the extension.", TEAL),
    ("02", "Submit", "Preview a seed payload, then send it to the configured FastAPI service.", AMBER),
    ("03", "Process", "Filter targets and bound recursive jobs by depth, event count and time.", TEAL),
    ("04", "Observe", "Stream new events back into a popup list and browser graph.", AMBER),
]
for i, (num, title, body, col) in enumerate(objectives):
    x = 0.8 + (i % 2) * 6.08
    y = 2.22 + (i // 2) * 2.0
    add_card(slide, x, y, 5.72, 1.66, title, body, accent=col, icon=num, body_size=14)
add_pill(slide, "MANUAL BY DEFAULT", 0.85, 6.42, 2.35, color=TEAL)
add_pill(slide, "SERVER-BOUNDED", 3.38, 6.42, 2.05, color=AMBER)
add_pill(slide, "LIVE FEEDBACK", 5.62, 6.42, 1.95, color=TEAL)

# 4 — architecture overview
slide = add_base("System at a glance", "03  /  ARCHITECTURE", 4,
                 subtitle="The browser collects and displays; the service queues and processes jobs.")
add_box(slide, 0.78, 2.12, 4.18, 4.65, WHITE, line=WHITE)
slide.shapes.add_picture(str(FLOW), Inches(1.18), Inches(2.18), height=Inches(4.54))
add_text(slide, "THE REQUEST PATH", 5.5, 2.28, 3.0, 0.3, size=10, color=TEAL, bold=True, char_spacing=100)
steps = [
    ("1", "Browser extension", "Harvest + preview", TEAL),
    ("2", "FastAPI gateway", "Filter + queue", AMBER),
    ("3", "Python worker", "Process events", TEAL),
    ("4", "Redis + WebSocket", "Store + stream", AMBER),
]
for i, (n, title, sub, col) in enumerate(steps):
    yy = 2.8 + i * 0.86
    add_box(slide, 5.55, yy, 0.48, 0.48, PANEL2, line=PANEL2)
    add_text(slide, n, 5.55, yy + 0.08, 0.48, 0.28, size=14, color=col, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, title, 6.25, yy - 0.01, 3.1, 0.33, size=17, color=WHITE, bold=True)
    add_text(slide, sub, 9.32, yy + 0.015, 2.45, 0.3, size=13, color=MUTED)
add_box(slide, 5.55, 6.34, 6.42, 0.38, PANEL, line=PANEL)
add_text(slide, "Active TCP checks are optional and off by default.", 5.75, 6.405, 6.0, 0.22,
         size=11, color=AMBER, bold=True, align=PP_ALIGN.CENTER)

# 5 — extension
slide = add_base("The extension: the operator’s starting point", "04  /  BROWSER CLIENT", 5,
                 subtitle="Manifest V3 client for compatible Chromium browsers.")
add_card(slide, 0.8, 2.15, 3.72, 1.74, "Page harvester", "Links · forms · scripts\nResource entries · selected hints", accent=TEAL, body_size=14)
add_card(slide, 4.8, 2.15, 3.72, 1.74, "Popup controls", "Preview payload\nSubmit scan · follow event list", accent=AMBER, body_size=14)
add_card(slide, 8.8, 2.15, 3.72, 1.74, "DevTools graph", "Render event relationships\nfrom parent identifiers", accent=TEAL, body_size=14)
add_arrow(slide, 4.54, 3.0, 4.78, 3.0)
add_arrow(slide, 8.54, 3.0, 8.78, 3.0)
add_box(slide, 0.8, 4.35, 7.7, 1.62, PANEL, line=PANEL2)
add_text(slide, "DESIGNED TO LIMIT COLLECTION", 1.1, 4.62, 4.7, 0.28, size=11, color=TEAL, bold=True, char_spacing=50)
add_text(slide, "Manual submission is the default. Best-effort redaction covers common sensitive patterns, but it cannot guarantee that every secret is removed.",
         1.1, 5.02, 6.95, 0.74, size=15, color=WHITE)
add_box(slide, 8.85, 4.35, 3.67, 1.62, PANEL, line=PANEL2)
add_text(slide, "IMPORTANT LIMIT", 9.15, 4.62, 3.0, 0.28, size=11, color=AMBER, bold=True, char_spacing=50)
add_text(slide, "Metadata only: no request bodies, traffic blocking, or WebSocket-frame inspection.",
         9.15, 5.02, 3.0, 0.75, size=14, color=MUTED)

# 6 — processing pipeline
slide = add_base("From seed to live event", "05  /  SERVER PIPELINE", 6,
                 subtitle="The worker follows an event-driven loop with explicit resource ceilings.")
flow_steps = [
    ("SEED", "Submitted page clues", TEAL),
    ("FILTER", "Configured targets", AMBER),
    ("QUEUE", "Redis job list", TEAL),
    ("MODULES", "DNS · CT · HTTP", AMBER),
    ("EVENTS", "Stored + published", TEAL),
]
for i, (title, desc, col) in enumerate(flow_steps):
    x = 0.75 + i * 2.55
    add_box(slide, x, 2.48, 2.12, 1.3, PANEL, line=PANEL2)
    add_text(slide, title, x + 0.15, 2.73, 1.82, 0.3, size=14, color=col, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, desc, x + 0.15, 3.17, 1.82, 0.42, size=11, color=MUTED, align=PP_ALIGN.CENTER)
    if i < len(flow_steps) - 1:
        add_arrow(slide, x + 2.15, 3.13, x + 2.49, 3.13, color=TEAL, width=1.8)
# return loop
add_arrow(slide, 10.95, 4.18, 2.0, 4.18, color=AMBER, width=1.5)
add_text(slide, "New events can re-enter the queue until configured depth / count / time limits are reached",
         2.15, 4.27, 8.65, 0.38, size=12, color=AMBER, align=PP_ALIGN.CENTER)
add_card(slide, 0.9, 5.05, 3.65, 1.18, "Redis", "Jobs · event history · pub/sub", accent=TEAL, body_size=12)
add_card(slide, 4.85, 5.05, 3.65, 1.18, "WebSocket", "Backfill + live job events", accent=AMBER, body_size=12)
add_card(slide, 8.8, 5.05, 3.65, 1.18, "Browser view", "List + graph update", accent=TEAL, body_size=12)

# 7 — implementation stack
slide = add_base("Implementation stack", "06  /  BUILD", 7,
                 subtitle="A deliberately small stack keeps the prototype understandable and testable.")
stack = [
    ("CLIENT", "Chrome MV3\nJavaScript\nPopup + DevTools", TEAL),
    ("API", "Python\nFastAPI\nREST + WebSocket", AMBER),
    ("WORKER", "Python modules\nEvent queue\nDepth / time limits", TEAL),
    ("STATE", "Redis\nJob queue\nEvent history + pub/sub", AMBER),
]
for i, (head, body, col) in enumerate(stack):
    x = 0.82 + i * 3.12
    add_box(slide, x, 2.35, 2.75, 2.78, PANEL, line=PANEL2)
    add_box(slide, x, 2.35, 2.75, 0.08, col, radius=False)
    add_text(slide, head, x + 0.24, 2.7, 2.25, 0.34, size=13, color=col, bold=True, align=PP_ALIGN.CENTER)
    add_text(slide, body, x + 0.24, 3.28, 2.25, 1.42, size=18, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
add_box(slide, 0.82, 5.65, 11.99, 0.77, PANEL, line=PANEL2)
add_text(slide, "Deployment assets: Docker Compose + Nginx example  ·  Deployment was not run in the recorded environment.",
         1.1, 5.88, 11.4, 0.32, size=13, color=MUTED, align=PP_ALIGN.CENTER)

# 8 — testing
slide = add_base("What has been tested?", "07  /  EVALUATION", 8,
                 subtitle="Automated tests use controlled fixtures; they are not live-target performance measurements.")
# two large metric cards
add_box(slide, 0.9, 2.3, 5.35, 2.9, PANEL, line=PANEL2)
add_text(slide, "65", 1.25, 2.65, 1.85, 1.0, size=52, color=TEAL, bold=True)
add_text(slide, "SERVER TESTS", 3.0, 2.88, 2.65, 0.36, size=16, color=WHITE, bold=True)
add_text(slide, "Policy · API routes\nWorker behavior · event processing", 1.28, 4.0, 4.5, 0.72,
         size=15, color=MUTED)
add_box(slide, 7.03, 2.3, 5.35, 2.9, PANEL, line=PANEL2)
add_text(slide, "21", 7.38, 2.65, 1.85, 1.0, size=52, color=AMBER, bold=True)
add_text(slide, "EXTENSION TESTS", 9.1, 2.88, 2.9, 0.36, size=16, color=WHITE, bold=True)
add_text(slide, "Extraction · URL handling\nRedaction · headers · graph layout", 7.42, 4.0, 4.5, 0.72,
         size=15, color=MUTED)
add_box(slide, 0.9, 5.65, 11.48, 0.75, RGBColor(55, 44, 40), line=RGBColor(86, 63, 48))
add_text(slide, "NOT MEASURED: live discovery accuracy, false-positive rate, scan performance, or production readiness.",
         1.15, 5.87, 10.95, 0.3, size=13, color=AMBER, bold=True, align=PP_ALIGN.CENTER)

# 9 — limitations / next steps
slide = add_base("What remains to be validated?", "08  /  NEXT STEPS", 9,
                 subtitle="The next stage is controlled validation—not stronger claims from unit tests.")
roadmap = [
    ("01  BROWSER", "Load the extension in Chrome\nCheck permissions, popup and reconnects", TEAL),
    ("02  DEPLOYMENT", "Run containers behind HTTPS\nVerify proxy, restart and resource behavior", AMBER),
    ("03  LAB EVALUATION", "Use team-controlled targets\nMeasure coverage, false positives and timing", TEAL),
]
for i, (title, body, col) in enumerate(roadmap):
    x = 0.82 + i * 4.1
    add_card(slide, x, 2.55, 3.72, 2.15, title, body, accent=col, body_size=14)
    if i < 2:
        add_arrow(slide, x + 3.75, 3.62, x + 4.02, 3.62, color=col, width=1.8)
add_text(slide, "Current boundaries", 0.95, 5.32, 2.3, 0.32, size=15, color=WHITE, bold=True)
add_text(slide, "No multi-operator isolation  ·  No live-target evaluation  ·  Container deployment not yet executed",
         3.1, 5.32, 9.1, 0.42, size=13, color=MUTED)
add_pill(slide, "TEST ONLY SYSTEMS WITHIN YOUR PERMITTED SCOPE", 0.95, 6.08, 5.25, color=AMBER, fill=PANEL2)

# 10 — selected references
slide = add_base("Selected references", "09  /  SOURCES", 10,
                 subtitle="Background sources for the architecture and project context.")
references = [
    ("01", "BBOT", "Black Lantern Security — modular, event-driven reconnaissance tool.", "github.com/blacklanternsecurity/bbot", TEAL),
    ("02", "Chrome Manifest V3", "Chrome extension platform and service-worker lifecycle documentation.", "developer.chrome.com/docs/extensions/", AMBER),
    ("03", "Redis", "Redis Lists and Pub/Sub documentation for queue and event transport.", "redis.io/docs/latest/develop/", TEAL),
    ("04", "NIST SP 800-115", "Technical Guide to Information Security Testing and Assessment.", "doi:10.6028/NIST.SP.800-115", AMBER),
]
for i, (num, title, desc, url, col) in enumerate(references):
    yy = 2.05 + i * 1.06
    add_box(slide, 0.9, yy, 11.45, 0.82, PANEL, line=PANEL2)
    add_text(slide, num, 1.15, yy + 0.2, 0.55, 0.28, size=13, color=col, bold=True)
    add_text(slide, title, 1.9, yy + 0.13, 2.75, 0.28, size=15, color=WHITE, bold=True)
    add_text(slide, desc, 4.68, yy + 0.13, 5.1, 0.3, size=12, color=MUTED)
    add_text(slide, url, 4.68, yy + 0.49, 7.1, 0.2, size=9, color=col)

# 11 — conclusion / Q&A
slide = prs.slides.add_slide(BLANK)
slide.background.fill.solid(); slide.background.fill.fore_color.rgb = NAVY
add_box(slide, 0, 0, 0.16, 7.5, TEAL, radius=False)
add_text(slide, "TAKEAWAY", 0.94, 0.83, 3.0, 0.3, size=11, color=TEAL, bold=True, char_spacing=120)
add_text(slide, "A simple bridge from\nbrowser context to\nlive reconnaissance events.",
         0.9, 1.55, 7.7, 2.55, size=34, color=WHITE, bold=True)
add_text(slide, "ReconRelay demonstrates the workflow; controlled browser, deployment and lab validation are the next steps.",
         0.96, 4.52, 7.25, 0.85, size=17, color=MUTED)
add_box(slide, 9.0, 1.55, 3.2, 3.85, PANEL, line=PANEL2)
add_text(slide, "THANK YOU", 9.36, 2.2, 2.48, 0.52, size=24, color=TEAL, bold=True, align=PP_ALIGN.CENTER)
add_box(slide, 9.8, 3.0, 1.35, 0.05, AMBER, radius=False)
add_text(slide, "QUESTIONS?", 9.38, 3.58, 2.45, 0.4, size=18, color=WHITE, bold=True, align=PP_ALIGN.CENTER)
add_text(slide, "[STUDENT NAMES]", 9.35, 4.42, 2.5, 0.28, size=11, color=MUTED, align=PP_ALIGN.CENTER)
add_text(slide, "RECONRELAY  /  PROJECT PRESENTATION", 0.95, 7.08, 6.2, 0.2,
         size=8, color=MUTED, bold=True, char_spacing=50)
add_text(slide, "11  /  11", 11.72, 7.08, 0.92, 0.23,
         size=9, color=MUTED, bold=True, align=PP_ALIGN.RIGHT)

prs.save(OUT)
print(OUT)
