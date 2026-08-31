import unittest
import subprocess
import tempfile
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


if __name__ == "__main__":
    unittest.main()