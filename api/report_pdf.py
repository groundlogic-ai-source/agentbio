"""Deterministic, server-side PDF rendering for persisted case dossiers.

This intentionally renders the frozen Markdown snapshot, rather than browser
DOM, so a download is reproducible and does not depend on a client's printer.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.fonts import FontFace

_MARKDOWN_URL = re.compile(r"\[[^\]]+\]\((https?://[^)\s]+|/[^)\s]+)\)")
_BARE_URL = re.compile(r"(?<!\]\()(?<!\w)(https?://[^\s<>()]+)")


def _plain_markdown(text: str) -> str:
    # HTML comments carry machine-readable provenance in the Markdown
    # snapshot, but are not reader-facing content and must never appear in the
    # PDF text layer.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(
        r"\[([^\]]+)\]\(((?:https?://|/)[^)]+)\)",
        lambda match: f"{match.group(1)} ({match.group(2)})",
        text,
    )
    text = re.sub(r"[*_`]", "", text)
    return str(text or "").strip()


def _split_table_row(line: str) -> list[str]:
    """Split a GFM table row while retaining escaped pipe characters."""
    content = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", content)
    return [_plain_markdown(cell.replace(r"\|", "|").strip()) for cell in cells]


def _is_table_separator(line: str) -> bool:
    cells = _split_table_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _resource_urls(markdown: str) -> list[str]:
    """Extract URLs in first-seen order for PDF link annotations."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in list(_MARKDOWN_URL.finditer(markdown)) + list(_BARE_URL.finditer(markdown)):
        url = match.group(1).rstrip(".,;:")
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


class DossierPDF(FPDF):
    def header(self) -> None:
        self.set_font("DejaVu", "B", 8)
        self.set_text_color(90, 90, 90)
        self.cell(0, 5, "AgentBio | Case dossier | Research hypothesis", align="C")
        self.ln(8)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("DejaVu", "", 7)
        self.set_text_color(100, 100, 100)
        self.cell(0, 5, f"AgentBio - persisted report snapshot - page {self.page_no()}", align="C")


def render_case_pdf(markdown: str, job: dict[str, Any]) -> bytes:
    """Render a report snapshot to a portable PDF with audit metadata."""
    disease = str(job.get("disease_name") or "Case dossier")
    job_id = str(job.get("job_id") or "unknown")
    created = str(job.get("created_at") or "")
    pdf = DossierPDF(format="A4", unit="mm")
    font_root = Path("/usr/share/fonts/truetype/dejavu")
    pdf.add_font("DejaVu", "", str(font_root / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(font_root / "DejaVuSans-Bold.ttf"))
    # Dockerfile.api installs fonts-dejavu-core, which provides the regular and
    # bold faces at this path but no oblique one. Registering
    # the regular face for italic requests retains Unicode coverage and avoids
    # a deployment-only dependency on an absent oblique font file.
    pdf.add_font("DejaVu", "I", str(font_root / "DejaVuSans.ttf"))
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(f"AgentBio Case Dossier - {disease}")
    pdf.set_author("AgentBio")
    pdf.set_creator("AgentBio controlled report export")
    pdf.set_subject(f"Persisted case-report snapshot; job {job_id}")
    pdf.set_keywords("AgentBio, case dossier, research hypothesis, persisted snapshot")
    pdf.set_lang("en-US")
    pdf.set_display_mode("fullwidth", "continuous")
    # fpdf2 otherwise inserts the render wall-clock time, making an interrupted
    # finalization produce different bytes on retry.
    try:
        created_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        created_at = datetime(1980, 1, 1, tzinfo=timezone.utc)
    pdf.set_creation_date(created_at)
    pdf.add_page()
    def block(height: float, text: str) -> None:
        # fpdf2 leaves the cursor at the right edge after multi_cell; reset it
        # before every block or the next line has zero available width.
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, height, text)

    pdf.set_text_color(26, 27, 29)
    pdf.set_font("DejaVu", "B", 16)
    block(8, disease)
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(100, 100, 100)
    block(
        4.5,
        f"Case ID: {job_id}\nOpened: {created}\n"
        f"Report snapshot: {job.get('report_snapshot_at') or created}\n"
        f"SHA-256: {job.get('report_sha256') or 'legacy snapshot; hash unavailable'}",
    )
    pdf.ln(3)
    resource_urls = _resource_urls(markdown)

    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        index += 1
        if not line:
            pdf.ln(1.5)
            continue
        if (line.startswith("|") and index < len(lines)
                and _is_table_separator(lines[index].strip())):
            table_rows = [_split_table_row(line)]
            index += 1  # consume separator
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_rows.append(_split_table_row(lines[index]))
                index += 1
            column_count = len(table_rows[0])
            table_rows = [
                row[:column_count] + [""] * max(0, column_count - len(row))
                for row in table_rows
            ]
            pdf.set_font("DejaVu", "", 6.6 if column_count > 5 else 7.5)
            pdf.set_text_color(47, 48, 51)
            heading_style = FontFace(
                emphasis="BOLD", color=(255, 255, 255),
                fill_color=(67, 72, 78), size_pt=7,
            )
            with pdf.table(
                rows=table_rows,
                width=pdf.epw,
                text_align="LEFT",
                line_height=3.8,
                padding=1.1,
                headings_style=heading_style,
                repeat_headings=1,
                wrapmode="WORD",
            ):
                pass
            pdf.ln(2)
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            # Keep a heading with at least two following body lines.
            if pdf.get_y() > pdf.h - pdf.b_margin - 22:
                pdf.add_page()
            pdf.ln(2)
            pdf.set_font("DejaVu", "B", max(10, 16 - len(heading.group(1))))
            pdf.set_text_color(26, 27, 29)
            block(6, _plain_markdown(heading.group(2)))
            continue
        if line.startswith(">"):
            line = line.lstrip("> ").strip()
            pdf.set_fill_color(245, 240, 228)
            pdf.set_font("DejaVu", "I", 8.5)
            pdf.set_text_color(80, 64, 35)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 4.8, _plain_markdown(line), fill=True, padding=2)
            continue
        pdf.set_font("DejaVu", "", 9)
        pdf.set_text_color(47, 48, 51)
        prefix = "- " if re.match(r"^[-*]\s+", line) else ""
        line = re.sub(r"^[-*]\s+", "", line)
        # FPDF wraps at whitespace; no character-level token splitting.
        block(4.8, prefix + _plain_markdown(line))

    # Body text remains deliberately plain for reliable table/layout rendering.
    # This section makes every resource both readable and an actual PDF URI
    # annotation, including the dossier, evidence bundle and durable structures
    # added during finalization.
    if resource_urls:
        if pdf.get_y() > pdf.h - pdf.b_margin - 30:
            pdf.add_page()
        pdf.ln(4)
        pdf.set_font("DejaVu", "B", 11)
        pdf.set_text_color(26, 27, 29)
        block(6, "Linked resources")
        pdf.set_font("DejaVu", "", 8)
        pdf.set_text_color(30, 84, 145)
        for url in resource_urls:
            # FPDF's external link argument emits /Annots with /URI; write()
            # wraps the visible text while preserving a clickable rectangle.
            pdf.set_x(pdf.l_margin)
            pdf.write(4.5, url, link=url)
            pdf.ln(5)

    return bytes(pdf.output())