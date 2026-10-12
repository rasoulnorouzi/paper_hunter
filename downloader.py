# %%
from pathlib import Path

import logging
import pandas as pd
from plugins_class import (
    UnpaywallDownloader,
    OpenAlexDownloader,
    CrossrefDownloader,
    SciHubDownloader,
    PDFDownloadManager,
)
from utility import headers as _global_headers, scihub_mirrors as _global_mirrors

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# %%

def download_pdf_from_doi(doi: str, mirrors: list, headers: dict) -> tuple[str, str]:
    """
    Downloads a PDF for a given DOI using the Sci-Hub strategy.

    Delegates to :class:`SciHubDownloader`, which tries the fast browserless
    path (requests + Altcha proof-of-work solving) across the mirrors and then
    falls back to a headless nodriver browser if needed.

    Returns a tuple of (doi, status) where status is 'success' or 'fail'.
    """
    downloader = SciHubDownloader(
        headers=headers, download_dir=Path("scihub_downloads"), mirrors=mirrors
    )
    path = downloader.try_download(doi)
    if path:
        logging.info(f"Successfully saved PDF to {path}")
        return doi, "success"
    logging.error(f"All mirrors failed for DOI: {doi}")
    return doi, "fail"


def run_bulk_download(
    dois: list[str] | str,
    download_dir: str | Path = "fulldownloads",
    headers: dict | None = None,
    mirrors: list[str] | None = None,
    use_browser: bool = True,
) -> pd.DataFrame:
    """High-level helper wrapping the plugin-based PDFDownloadManager.

    Keeps notebook usage minimal by:
    - Accepting a list or single DOI
    - Constructing strategy instances (Unpaywall, Crossref, Sci-Hub)
    - Running manager.download and returning a DataFrame of results
    - Persisting CSV summary in the target directory

    Parameters
    ----------
    dois : list[str] | str
        DOIs to attempt downloading.
    download_dir : str | Path
        Output directory for PDFs and summary CSV.
    headers : dict | None
        Optional override for HTTP headers (defaults to utility.headers).
    mirrors : list[str] | None
        Optional override for Sci-Hub mirrors (defaults to utility.scihub_mirrors).

    Returns
    -------
    pd.DataFrame
        DataFrame with columns: doi, success (bool).
    """
    if isinstance(dois, str):
        dois_list = [dois]
    else:
        dois_list = list(dois)

    headers = headers or _global_headers
    mirrors = mirrors or _global_mirrors
    download_path = Path(download_dir)

    strategies = [
        UnpaywallDownloader(headers=headers, download_dir=download_path),
        OpenAlexDownloader(headers=headers, download_dir=download_path),
        CrossrefDownloader(headers=headers, download_dir=download_path),
        SciHubDownloader(headers=headers, download_dir=download_path, mirrors=mirrors, use_browser=use_browser),
    ]
    manager = PDFDownloadManager(strategies=strategies, download_dir=download_path)
    manager.download(dois_list)
    manager.save_results_to_csv()
    return pd.DataFrame(manager.results)


def zip_downloads(download_dir: str | Path = "fulldownloads", zip_name: str = "papers_zip") -> Path:
    """Create a zip archive of all PDFs in the download directory.

    Parameters
    ----------
    download_dir : str | Path
        Directory containing downloaded PDF files.
    zip_name : str
        Base name (without .zip) for the archive.

    Returns
    -------
    Path
        Path to the created zip file.
    """
    from zipfile import ZipFile

    download_path = Path(download_dir)
    if not download_path.exists():
        raise FileNotFoundError(f"Download directory not found: {download_path}")

    zip_path = download_path.parent / f"{zip_name}.zip"
    with ZipFile(zip_path, "w") as zf:
        for pdf in download_path.glob("*.pdf"):
            zf.write(pdf, pdf.name)

        # include summary CSV if present
        summary_csv = download_path / "download_summary.csv"
        if summary_csv.exists():
            zf.write(summary_csv, summary_csv.name)

    logging.info(f"Created zip archive at {zip_path}")
    return zip_path

if __name__ == '__main__':
    test_dois = pd.read_csv("sample_doi.csv")["doi"].tolist()

    # Uses the same priority order as the app: Unpaywall -> Crossref -> Sci-Hub.
    results = run_bulk_download(test_dois, download_dir="fulldownloads")

    total = len(results)
    success = int(results["success"].sum()) if total else 0
    print(f"Success rate: {success}/{total}")
    print(f"Failed rate: {total - success}/{total}")
