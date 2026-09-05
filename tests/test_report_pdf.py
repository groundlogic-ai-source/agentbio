import unittest
import subprocess
import tempfile
import io
from pathlib import Path

from api.report_pdf import render_case_pdf


class ReportPdfTests(unittest.TestCase):
    def test_renders_persisted_markdown_to_pdf(self):
        payload = render_case_pdf(
            "# Hypothesis\n\nA persisted **snapshot** with Δ and p.G406R.\n\n"
            "[Source](https://example.org/evidence)\n\n"
            "| Evidence | Value |\n| --- | --- |\n| ID | P12345 |",
            {
                "job_id": "abc123",
                "disease_name": "Example disease",
                "created_at": "2025-01-01T00:00:00",
                "updated_at": "2025-01-02T00:00:00",
                "report_snapshot_at": "2025-01-01T12:00:00+00:00",
                "report_sha256": "abc123",
            },
        )
        self.assertTrue(payload.startswith(b"%PDF-"))
        self.assertGreater(len(payload), 500)
        with tempfile.TemporaryDirectory() as directory:
            pdf_path = Path(directory) / "report.pdf"
            pdf_path.write_bytes(payload)
            text = subprocess.run(
                ["pdftotext", "-layout", str(pdf_path), "-"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
        self.assertIn("Evidence", text)
        self.assertIn("P12345", text)
        self.assertIn("example.org/evidence", text)
        self.assertIn("p.G406R", text)
        self.assertIn("abc123", text)
        self.assertNotIn("| --- |", text)
        self.assertIn(b"AgentBio Case Dossier", payload)
        # Visible URLs are insufficient for a usable dossier: fpdf2 must emit
        # real external URI annotations that PDF viewers can activate.
        self.assertIn(b"/Annots", payload)
        self.assertIn(b"/URI", payload)
        self.assertIn(b"https://example.org/evidence", payload)
        self.assertIn("Linked resources", text)
        # Resolve annotation objects structurally rather than accepting a raw
        # marker that could appear in unrelated PDF content.
        try:
            try:
                from pypdf import PdfReader
            except ImportError:
                from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(payload))
            uris = []
            for page in reader.pages:
                for annotation_ref in page.get("/Annots", []):
                    annotation = annotation_ref.get_object()
                    self.assertEqual(annotation.get("/Subtype"), "/Link")
                    action = annotation["/A"].get_object()
                    self.assertEqual(action.get("/S"), "/URI")
                    uris.append(str(action.get("/URI")))
        except ImportError:
            # fpdf2 emits annotation dictionaries directly in the page's
            # /Annots array. Parse that object boundary and validate the full
            # Link -> Action -> URI structure, not merely a global marker.
            import re
            arrays = re.findall(rb"/Annots \[(.*?)\]\s*/Contents", payload, re.S)
            self.assertTrue(arrays)
            uris = []
            for array in arrays:
                for match in re.finditer(
                        rb"/A\s*<<\s*/S\s*/URI\s*/URI\s*\(([^)]*)\)\s*>>"
                        rb".*?/Subtype\s*/Link", array, re.S):
                    uris.append(match.group(1).decode("latin-1"))
        self.assertIn("https://example.org/evidence", uris)


if __name__ == "__main__":
    unittest.main()