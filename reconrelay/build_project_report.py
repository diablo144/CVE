"""Build the editable, college-style ReconRelay project report DOCX."""
from pathlib import Path
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.shared import Inches, Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).parent
SOURCE = ROOT / "project_report.md"
OUTPUT = ROOT / "ReconRelay_Project_Report.docx"


def set_font(run, size=12, bold=False, italic=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic


def add_inline(paragraph, text, size=12):
    token_re = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")
    pos = 0
    for match in token_re.finditer(text):
        if match.start() > pos:
            set_font(paragraph.add_run(text[pos:match.start()]), size)
        token = match.group(0)
        if token.startswith("**"):
            set_font(paragraph.add_run(token[2:-2]), size, bold=True)
        elif token.startswith("*"):
            set_font(paragraph.add_run(token[1:-1]), size, italic=True)
        else:
            set_font(paragraph.add_run(token[1:-1]), size - 0.5)
        pos = match.end()
    if pos < len(text):
        set_font(paragraph.add_run(text[pos:]), size)


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText"); instruction.set(qn("xml:space"), "preserve"); instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar"); separate.set(qn("w:fldCharType"), "separate")
    displayed = OxmlElement("w:t"); displayed.text = "1"
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, displayed, end])
    set_font(run, 10)


def add_toc(paragraph):
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText"); instruction.set(qn("xml:space"), "preserve")
    instruction.text = ' TOC \\o "1-3" \\h \\z \\u '
    separate = OxmlElement("w:fldChar"); separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t"); text.text = "Open in Word or LibreOffice and update the table to populate page numbers."
    end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, text, end])
    set_font(run, 11)


def add_table(doc, rows):
    parsed = [[cell.strip() for cell in row.strip().strip("|").split("|")] for row in rows]
    parsed = [row for row in parsed if not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in row)]
    if not parsed:
        return
    cols = max(map(len, parsed))
    table = doc.add_table(rows=len(parsed), cols=cols)
    table.style = "Table Grid"
    for i, row in enumerate(parsed):
        for j in range(cols):
            cell = table.cell(i, j)
            cell.text = ""
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            if j < len(row):
                add_inline(p, row[j], 10)
            for run in p.runs:
                if i == 0:
                    run.bold = True
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def configure_paragraph(p, *, indent=True):
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.widow_control = True
    if indent:
        p.paragraph_format.first_line_indent = Inches(0.3)


doc = Document()
section = doc.sections[0]
section.top_margin = Inches(0.85)
section.bottom_margin = Inches(0.8)
section.left_margin = Inches(1.15)
section.right_margin = Inches(0.85)
section.header_distance = Inches(0.35)
section.footer_distance = Inches(0.35)

normal = doc.styles["Normal"]
normal.font.name = "Times New Roman"
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
normal.font.size = Pt(12)
normal.paragraph_format.line_spacing = 1.5
normal.paragraph_format.space_after = Pt(6)
for style_name in ("Heading 1", "Heading 2", "Heading 3"):
    style = doc.styles[style_name]
    style.font.name = "Times New Roman"
    style._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
    style.font.color.rgb = __import__("docx").shared.RGBColor(0, 0, 0)

header = section.header.paragraphs[0]
header.alignment = WD_ALIGN_PARAGRAPH.CENTER
set_font(header.add_run("RECONRELAY — PROJECT REPORT"), 9, bold=True)
add_page_number(section.footer.paragraphs[0])

settings = doc.settings.element
update_fields = OxmlElement("w:updateFields")
update_fields.set(qn("w:val"), "true")
settings.append(update_fields)

lines = [line.rstrip() for line in SOURCE.read_text(encoding="utf-8").splitlines()]
cover = True
i = 0
while i < len(lines):
    raw = lines[i]
    line = raw.strip()
    if not line:
        i += 1
        continue
    if line == "<!-- INSTITUTION LOGOS -->":
        logos = doc.add_table(rows=1, cols=2)
        logos.alignment = WD_TABLE_ALIGNMENT.CENTER
        logos.autofit = False
        for cell in logos.rows[0].cells:
            cell.width = Inches(2.4)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        left_p = logos.cell(0, 0).paragraphs[0]
        right_p = logos.cell(0, 1).paragraphs[0]
        left_p.alignment = right_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        left_p.add_run().add_picture(str(ROOT / "report_assets" / "anna_university_emblem.jpeg"), width=Inches(1.15))
        right_p.add_run().add_picture(str(ROOT / "report_assets" / "mcet_logo.jpeg"), width=Inches(1.65))
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            node = OxmlElement(f"w:{edge}")
            node.set(qn("w:val"), "nil")
            borders.append(node)
        logos._tbl.tblPr.append(borders)
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_after = Pt(6)
        i += 1
        continue
    if line == "<!-- PAGEBREAK -->":
        doc.add_page_break()
        cover = False
        i += 1
        continue
    if line == "[TOC]":
        p = doc.add_paragraph()
        add_toc(p)
        i += 1
        continue
    if line.startswith("|"):
        table_lines = []
        while i < len(lines) and lines[i].strip().startswith("|"):
            table_lines.append(lines[i].strip())
            i += 1
        add_table(doc, table_lines)
        continue
    image = re.fullmatch(r"!\[(.*?)\]\((.*?)\)", line)
    if image:
        caption, image_name = image.groups()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True
        p.add_run().add_picture(str(ROOT / image_name), width=Inches(6.15))
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(8)
        add_inline(p, caption, 10)
        i += 1
        continue
    if line.startswith("# "):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(35)
        p.paragraph_format.space_after = Pt(34)
        p.paragraph_format.keep_with_next = True
        set_font(p.add_run(line[2:].upper()), 20, bold=True)
        i += 1
        continue
    if line.startswith("## "):
        heading = line[3:]
        if heading.upper().startswith(("CHAPTER ", "REFERENCES", "APPENDIX ")):
            doc.add_page_break()
        p = doc.add_paragraph(style="Heading 1")
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(12)
        set_font(p.add_run(heading.upper()), 14, bold=True)
        i += 1
        continue
    if line.startswith("### "):
        p = doc.add_paragraph(style="Heading 2")
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(8)
        p.paragraph_format.space_after = Pt(4)
        set_font(p.add_run(line[4:]), 12, bold=True)
        i += 1
        continue
    if line.startswith("- "):
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.line_spacing = 1.25
        p.paragraph_format.space_after = Pt(3)
        add_inline(p, line[2:], 12)
        i += 1
        continue
    if re.match(r"^\d+\. ", line):
        p = doc.add_paragraph(style="List Number")
        p.paragraph_format.line_spacing = 1.25
        p.paragraph_format.space_after = Pt(3)
        add_inline(p, re.sub(r"^\d+\. ", "", line), 12)
        i += 1
        continue

    p = doc.add_paragraph()
    if cover:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(5)
        p.paragraph_format.line_spacing = 1.25
        add_inline(p, line, 12)
    else:
        configure_paragraph(p, indent=not (line.startswith("[") or line.startswith("**")))
        add_inline(p, line, 12)
    i += 1

props = doc.core_properties
props.title = "ReconRelay: A Browser Extension for a Reconnaissance Service — Project Phase 1"
props.subject = "Project Phase 1 report: browser-extension reconnaissance service"
props.author = "Sanjay S; Mithrar Muralidharan R; Sudhakar K"
props.keywords = "browser extension, reconnaissance, project report"
doc.save(OUTPUT)
print(OUTPUT)
