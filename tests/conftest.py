from pathlib import Path

import pandas as pd
import pytest

from paper_hunter import doi_to_filename
from paper_hunter.sources import PDFDownloader

FAKE_PDF = b"%PDF-1.4\n% test file\n"


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    """Skip the polite random pauses between DOIs."""
    monkeypatch.setattr("paper_hunter.sources.human_delay", lambda *a, **k: 0.0)


class FakeDownloader(PDFDownloader):
    """A source that 'finds' every DOI except those ending in 'missing'."""

    def try_download(self, doi, **kwargs):
        if doi.endswith("missing"):
            return None
        return self._save_pdf(doi, FAKE_PDF)


class FakeManager:
    """Stand-in for PDFDownloadManager that needs no network."""

    def __init__(self, download_dir):
        self.download_dir = Path(download_dir)
        self.results = []

    def download(self, dois, progress=True, should_stop=None):
        for doi in dois:
            if should_stop is not None and should_stop():
                break
            ok = not doi.endswith("missing")
            name = f"{doi_to_filename(doi)}.pdf" if ok else ""
            if ok:
                self.download_dir.mkdir(parents=True, exist_ok=True)
                (self.download_dir / name).write_bytes(FAKE_PDF)
            self.results.append({"doi": doi, "success": ok, "source": "Fake" if ok else "", "file": name})

    def save_results_to_csv(self):
        pd.DataFrame(self.results).to_csv(self.download_dir / "download_summary.csv", index=False)
