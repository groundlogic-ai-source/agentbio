"""Build bioRxiv-ready PDFs (manuscript + supplement) from the markdown sources.

Pragmatic markdown-subset renderer on fpdf2 (core fonts): headings, paragraphs
with **bold** / *italic* inline markup, bullet and numbered lists, pipe tables,
and figure images (referenced as [Fig. N] on their own line, resolved from
publication/figures/figN_*.png). Supplement INCLUDE markers splice generated
tables.

Usage:  python3 publication/build_pdf.py
"""
import glob
import os
import re

from fpdf import FPDF

OUT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(OUT, "dist")
os.makedirs(DIST, exist_ok=True)

# cp1252-safe replacements for core-font rendering
SANITIZE = {
    "→": "->", "≥": ">=", "≤": "<=", "✓": "yes", "✗": "no", "—": "--",
    "–": "-", "×": "x", "↔": "<->", "’": "'", "‘": "'", "“": '"',
    "”": '"', "…": "...", "•": "-", "⚠": "(!)", "≠": "!=",
    "🔬": "", "📊": "", "\u00a0": " ",
}


def san(text):
    for k, v in SANITIZE.items():
        text = text.replace(k, v)
    # core fonts cannot hyphenate; give fpdf2 break points inside long tokens
    # (URLs, file paths) so multi_cell never hits an unbreakable word
    text = re.sub(r"\S{36,}",
                  lambda m: " ".join(m.group(0)[j:j + 34]
                                     for j in range(0, len(m.group(0)), 34)),
                  text)
    return text.encode("cp1252", "replace").decode("cp1252")


class Doc(FPDF):
    def __init__(self, footer):
        super().__init__()
        self.footer_text = footer
        self.set_auto_page_break(True, margin=18)
        self.set_margins(18, 16, 18)

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 6, san(f"{self.footer_text} -- page {self.page_no()}"),
                  align="C")


def render_table(pdf, rows):
    parsed = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    parsed = [r for r in parsed if not all(set(c) <= set("-: ") for c in r)]
    if not parsed:
        return
    ncol = max(len(r) for r in parsed)
    parsed = [r + [""] * (ncol - len(r)) for r in parsed]
    width = pdf.w - pdf.l_margin - pdf.r_margin
    colw = width / ncol
    fs = 7.5 if ncol >= 6 else 8.5
    for ri, row in enumerate(parsed):
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
        pdf.set_font("helvetica", "B" if ri == 0 else "", fs)
        max_lines = 1
        cells = []
        for cell in row:
            txt = san(cell.replace("**", ""))
            chars_per = max(int(colw / (fs * 0.5)), 4)
            est = max(1, -(-len(txt) // chars_per))
            max_lines = max(max_lines, min(est, 4))
            cells.append(txt)
        lh = fs * 0.5 + 1.2
        h = lh * max_lines
        y0 = pdf.get_y()
        for ci, txt in enumerate(cells):
            x = pdf.l_margin + ci * colw
            pdf.rect(x, y0, colw, h)
            pdf.set_xy(x + 0.8, y0 + 0.6)
            pdf.multi_cell(colw - 1.6, lh, txt, new_x="RIGHT")
        pdf.set_xy(pdf.l_margin, y0 + h)


def render(md_path, pdf_path, footer, include_dir=None):
    text = open(md_path).read()
    if include_dir:  # splice <!-- INCLUDE: file --> markers
        def _sub(m):
            p = os.path.join(include_dir, m.group(1).strip())
            return open(p).read() if os.path.exists(p) else f"[missing {p}]"
        text = re.sub(r"<!--\s*INCLUDE:\s*([^>]+?)\s*-->", _sub, text)

    pdf = Doc(footer)
    pdf.add_page()
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("|"):  # table block
            tbl = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                tbl.append(lines[i])
                i += 1
            render_table(pdf, tbl)
            pdf.ln(2)
            continue
        m = re.match(r"^\[Fig\.\s*(\d+)\]\s*$", line.strip())
        if m:  # figure embed
            hits = glob.glob(os.path.join(OUT, "figures",
                                          f"fig{m.group(1)}_*.png"))
            if hits:
                pdf.ln(1)
                w = pdf.w - pdf.l_margin - pdf.r_margin
                pdf.image(hits[0], x=pdf.l_margin + w * 0.07, w=w * 0.86)
                pdf.ln(2)
            i += 1
            continue
        if line.startswith("# "):
            pdf.set_font("helvetica", "B", 16)
            pdf.multi_cell(0, 7, san(line[2:]), markdown=True, new_x="LMARGIN")
            pdf.ln(2)
        elif line.startswith("## "):
            pdf.ln(2)
            pdf.set_font("helvetica", "B", 13)
            pdf.multi_cell(0, 6, san(line[3:]), markdown=True, new_x="LMARGIN")
            pdf.ln(1)
        elif line.startswith("### "):
            pdf.set_font("helvetica", "B", 11)
            pdf.multi_cell(0, 5.5, san(line[4:]), markdown=True, new_x="LMARGIN")
            pdf.ln(0.5)
        elif line.startswith("---"):
            pdf.ln(2)
        elif line.startswith(("* ", "- ")) or re.match(r"^\d+\.\s", line):
            txt = re.sub(r"^(\* |- |\d+\.\s)", "", line)
            pdf.set_font("helvetica", "", 10)
            pdf.set_x(pdf.l_margin + 4)
            pdf.multi_cell(0, 4.6, san("- " + txt), markdown=True, new_x="LMARGIN")
        elif line.strip():
            pdf.set_font("helvetica", "", 10)
            pdf.multi_cell(0, 4.6, san(line), markdown=True, new_x="LMARGIN")
        i += 1
    pdf.output(pdf_path)
    print("wrote", pdf_path, pdf.page_no(), "pages")


render(os.path.join(OUT, "manuscript.md"),
       os.path.join(DIST, "manuscript.pdf"),
       "AgentBio: frozen benchmark and external audit validation (preprint)")
render(os.path.join(OUT, "supplement.md"),
       os.path.join(DIST, "supplement.pdf"),
       "AgentBio supplement",
       include_dir=os.path.join(OUT, "generated"))
