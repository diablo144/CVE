"""Build the IEEE-style, editable ReconRelay DOCX from paper.md."""
from pathlib import Path
import re

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).parent
SOURCE = ROOT / "paper.md"
OUTPUT = ROOT / "IEEE_ReconRelay_Paper.docx"


def set_font(run, *, size=10, bold=False, italic=False, small_caps=False):
    run.font.name = "Times New Roman"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Times New Roman")
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    run.font.small_caps = small_caps


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instruction = OxmlElement("w:instrText")
    instruction.set(qn("xml:space"), "preserve")
    instruction.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instruction, separate, text, end])
    set_font(run, size=8)


def add_inline(paragraph, text, *, size=10):
    token_re = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")
    pos = 0
    for match in token_re.finditer(text):
        if match.start() > pos:
            run = paragraph.add_run(text[pos:match.start()])
            set_font(run, size=size)
        token = match.group(0)
        if token.startswith("**"):
            run = paragraph.add_run(token[2:-2])
            set_font(run, size=size, bold=True)
        elif token.startswith("*"):
            run = paragraph.add_run(token[1:-1])
            set_font(run, size=size, italic=True)
        else:
            run = paragraph.add_run(token[1:-1])
            set_font(run, size=size - 0.5)
        pos = match.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        set_font(run, size=size)


def set_columns(section, count=2, gap_twips=360):
    sect_pr = section._sectPr
    cols = sect_pr.xpath("./w:cols")
    if cols:
        cols = cols[0]
    else:
        cols = OxmlElement("w:cols")
        sect_pr.append(cols)
    cols.set(qn("w:num"), str(count))
    cols.set(qn("w:space"), str(gap_twips))
    cols.set(qn("w:equalWidth"), "1")


def format_body(p):
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p.paragraph_format.first_line_indent = Inches(0.14)
    p.paragraph_format.line_spacing = 1.0
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.widow_control = True


doc = Document()
first = doc.sections[0]
first.top_margin = Inches(0.68)
first.bottom_margin = Inches(0.68)
first.left_margin = Inches(0.72)
first.right_margin = Inches(0.72)
first.header_distance = Inches(0.3)
first.footer_distance = Inches(0.3)

normal = doc.styles["Normal"]
normal.font.name = "Times New Roman"
normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
normal.font.size = Pt(10)
normal.paragraph_format.line_spacing = 1.0
normal.paragraph_format.space_after = Pt(2)

header = first.header.paragraphs[0]
header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
r = header.add_run("RECONRELAY")
set_font(r, size=8, small_caps=True)
add_page_number(first.footer.paragraphs[0])

lines = [line.strip() for line in SOURCE.read_text(encoding="utf-8").splitlines()]
# Skip the first Markdown title; the conference title is typeset separately.
title = next(line[2:] for line in lines if line.startswith("# "))
content = lines[lines.index(next(line for line in lines if line.startswith("# "))) + 1:]

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(2)
p.paragraph_format.space_after = Pt(10)
p.paragraph_format.keep_with_next = True
r = p.add_run(title)
set_font(r, size=22)

for label in ("**Author:**", "**Affiliation:**", "**Date:**"):
    line = next((line for line in content if line.startswith(label)), None)
    if line:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.keep_with_next = True
        add_inline(p, line.replace("  ", " "), size=10)

# Abstract and index terms are in a full-width section, as in the IEEE
# conference Word template. The main paper continues in two columns below.
abstract_start = next(i for i, line in enumerate(content) if line == "## Abstract")
abstract_para = next(i for i in range(abstract_start + 1, len(content)) if content[i])
abstract_text = content[abstract_para]
keywords_line = next(line for line in content if line.startswith("**Keywords:**"))

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(4)
p.paragraph_format.space_after = Pt(2)
p.paragraph_format.keep_with_next = True
r = p.add_run("Abstract—")
set_font(r, size=9, bold=True, italic=True)
r = p.add_run(abstract_text)
set_font(r, size=9)
p.paragraph_format.line_spacing = 1.0
p.paragraph_format.space_after = Pt(4)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.LEFT
p.paragraph_format.space_after = Pt(7)
p.paragraph_format.keep_with_next = True
r = p.add_run("Index Terms—")
set_font(r, size=9, bold=True, italic=True)
keyword_text = keywords_line.split("**Keywords:**", 1)[1].strip()
r = p.add_run(keyword_text)
set_font(r, size=9, italic=True)

# Continuous section break: following text flows in IEEE-style two columns.
main = doc.add_section(WD_SECTION.CONTINUOUS)
main.top_margin = Inches(0.68)
main.bottom_margin = Inches(0.68)
main.left_margin = Inches(0.72)
main.right_margin = Inches(0.72)
main.header_distance = Inches(0.3)
main.footer_distance = Inches(0.3)
set_columns(main, 2, 360)

# The added section inherits header/footer definitions, but set these explicitly
# so Word displays page numbering consistently after the section break.
main.header.is_linked_to_previous = True
main.footer.is_linked_to_previous = True

skip = {"## Abstract", keywords_line}
for line in content:
    if not line or line in skip or line == abstract_text:
        continue
    if line.startswith("**Author:**") or line.startswith("**Affiliation:**") or line.startswith("**Date:**"):
        continue
    image_match = re.fullmatch(r"!\[(.*?)\]\((.*?)\)", line)
    if image_match:
        caption, image_name = image_match.groups()
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(1)
        p.paragraph_format.keep_with_next = True
        p.add_run().add_picture(str(ROOT / image_name), width=Inches(3.02))
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.0
        add_inline(p, caption, size=8.5)
    elif line.startswith("## "):
        heading = line[3:]
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(7)
        p.paragraph_format.space_after = Pt(4)
        p.paragraph_format.line_spacing = 1.0
        r = p.add_run(heading.upper())
        set_font(r, size=10, bold=True, small_caps=True)
    elif line.startswith("### "):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.keep_with_next = True
        p.paragraph_format.space_before = Pt(5)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.0
        r = p.add_run(line[4:])
        set_font(r, size=10, bold=True, italic=True)
    elif re.match(r"^\[\d+\]", line):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        p.paragraph_format.left_indent = Inches(0.18)
        p.paragraph_format.first_line_indent = Inches(-0.18)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.0
        p.paragraph_format.widow_control = True
        add_inline(p, line, size=8.5)
    else:
        p = doc.add_paragraph()
        format_body(p)
        add_inline(p, line, size=10)

# Make the references heading continuous with its first entry and keep headings
# from being stranded at the bottom of a column.
for p in doc.paragraphs:
    if p.text.strip().upper() == "REFERENCES":
        p.paragraph_format.keep_with_next = True

props = doc.core_properties
props.title = title
props.subject = "IEEE-style conference paper draft: architecture and prototype evaluation"
props.author = "[Your Name]"
props.keywords = "reconnaissance, browser extension, authorization, attack surface"
doc.save(OUTPUT)
print(OUTPUT)
